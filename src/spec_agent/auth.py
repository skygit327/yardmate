import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from uuid import uuid4

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from spec_agent.config import Settings

COOKIE_NAME = "spec_agent_session"


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: SecretStr = Field(min_length=1, max_length=128)


class AccountInput(LoginInput):
    password: SecretStr = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)


class LoginRejected(Exception):
    pass


class LoginThrottled(Exception):
    pass


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**17, r=8, p=1, maxmem=256 * 1024 * 1024)


class AuthStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = threading.RLock()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            settings.data_dir / "auth.sqlite", check_same_thread=False
        )
        (settings.data_dir / "auth.sqlite").chmod(0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL, salt BLOB NOT NULL,
                password_hash BLOB NOT NULL, active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                csrf_token TEXT NOT NULL, expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
                bucket TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires_at REAL NOT NULL
            );
        """)

    def create_user(
        self, username: str, display_name: str, password: str, demo: bool = False
    ) -> dict:
        """Create an account. `demo` allows the short shared password of hosted demo accounts."""
        if demo:
            if len(password) < 4:
                raise ValueError("Demo passwords need at least 4 characters.")
            account = AccountInput.model_construct(
                username=LoginInput(username=username.lower(), password=password).username,
                display_name=display_name.strip()[:80],
                password=SecretStr(password),
            )
        else:
            account = AccountInput(
                username=username.lower(), display_name=display_name.strip(), password=password
            )
        salt = secrets.token_bytes(16)
        digest = password_hash(account.password.get_secret_value(), salt)
        user = {
            "id": str(uuid4()),
            "username": account.username,
            "display_name": account.display_name,
        }
        with self.lock, self.connection:
            try:
                self.connection.execute(
                    "INSERT INTO users (id, username, display_name, salt, password_hash) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (*user.values(), salt, digest),
                )
            except sqlite3.IntegrityError:
                raise ValueError("This username is already registered.") from None
        return user

    def login(self, data: LoginInput, remote: str, old_token: str | None) -> tuple[str, dict]:
        with self.lock:
            now = time.time()
            buckets = [("user:" + data.username.lower(), 5), ("ip:" + remote, 20)]
            with self.connection:
                self.connection.execute("DELETE FROM login_attempts WHERE expires_at<=?", (now,))
                self.connection.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
                for bucket, limit in buckets:
                    row = self.connection.execute(
                        "SELECT attempts FROM login_attempts WHERE bucket=?", (token_hash(bucket),)
                    ).fetchone()
                    if row and row["attempts"] >= limit:
                        raise LoginThrottled()
                for bucket, _ in buckets:
                    self.connection.execute(
                        "INSERT INTO login_attempts VALUES (?, 1, ?) "
                        "ON CONFLICT(bucket) DO UPDATE SET attempts=attempts+1",
                        (token_hash(bucket), now + 300),
                    )
            user = self.connection.execute(
                "SELECT * FROM users WHERE username=?", (data.username.lower(),)
            ).fetchone()
            salt = user["salt"] if user else bytes(16)
            digest = password_hash(data.password.get_secret_value(), salt)
            expected = user["password_hash"] if user else bytes(len(digest))
            if not hmac.compare_digest(digest, expected) or not user or not user["active"]:
                raise LoginRejected()
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            expires_at = time.time() + self.settings.session_seconds
            with self.connection:
                self.connection.execute(
                    "DELETE FROM login_attempts WHERE bucket=?", (token_hash(buckets[0][0]),)
                )
                if old_token:
                    self.connection.execute(
                        "DELETE FROM sessions WHERE token_hash=?", (token_hash(old_token),)
                    )
                self.connection.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                    (token_hash(token), user["id"], csrf, expires_at),
                )
            return token, {
                "user": {key: user[key] for key in ("id", "username", "display_name")},
                "csrf_token": csrf,
                "expires_at": expires_at,
            }

    def session(self, token: str | None) -> dict | None:
        if not token or len(token) > 200:
            return None
        with self.lock:
            row = self.connection.execute(
                "SELECT users.id, username, display_name, csrf_token, expires_at "
                "FROM sessions JOIN users ON users.id=sessions.user_id "
                "WHERE token_hash=? AND expires_at>? AND active=1",
                (token_hash(token), time.time()),
            ).fetchone()
            return (
                {
                    "user": {key: row[key] for key in ("id", "username", "display_name")},
                    "csrf_token": row["csrf_token"],
                    "expires_at": row["expires_at"],
                }
                if row
                else None
            )

    def rename(self, user_id: str, display_name: str) -> dict:
        name = display_name.strip()
        if not 1 <= len(name) <= 80:
            raise ValueError("Use a name of 1 to 80 characters.")
        with self.lock, self.connection:
            self.connection.execute("UPDATE users SET display_name=? WHERE id=?", (name, user_id))
            row = self.connection.execute(
                "SELECT id, username, display_name FROM users WHERE id=?", (user_id,)
            ).fetchone()
        return {key: row[key] for key in ("id", "username", "display_name")}

    def change_password(self, user_id: str, current: str, new: str) -> None:
        if not 12 <= len(new) <= 128:
            raise ValueError("Use a new password of 12 to 128 characters.")
        with self.lock:
            row = self.connection.execute(
                "SELECT salt, password_hash FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if not row or not hmac.compare_digest(
                password_hash(current, row["salt"]), row["password_hash"]
            ):
                raise LoginRejected()
            salt = secrets.token_bytes(16)
            with self.connection:
                self.connection.execute(
                    "UPDATE users SET salt=?, password_hash=? WHERE id=?",
                    (salt, password_hash(new, salt), user_id),
                )

    def logout(self, token: str):
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))

    def close(self):
        self.connection.close()


class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        protected = (
            path == "/api"
            or path.startswith("/api/")
            or path in {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
        )
        if not protected:
            return await call_next(request)
        public = (request.method, path) in {("POST", "/api/auth/login"), ("GET", "/api/health")}
        session = None
        if not public:
            session = await run_in_threadpool(
                request.app.state.auth.session, request.cookies.get(COOKIE_NAME)
            )
            if session is None:
                return self.reject(401, "Sign-in is required or your session has expired.")
            request.state.session = session
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected_origin = f"{request.url.scheme}://{request.url.netloc}"
            if (
                request.headers.get("x-spec-agent") != "1"
                or (origin is not None and origin != expected_origin)
                or request.headers.get("sec-fetch-site") == "cross-site"
            ):
                return self.reject(403, "The request origin could not be verified.")
            if session and not hmac.compare_digest(
                request.headers.get("x-csrf-token", "").encode(), session["csrf_token"].encode()
            ):
                return self.reject(403, "The security token has changed. Refresh the page.")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @staticmethod
    def reject(status: int, detail: str):
        return JSONResponse(
            {"detail": detail}, status_code=status, headers={"Cache-Control": "no-store"}
        )
