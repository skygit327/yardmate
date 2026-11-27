from io import BytesIO

import pytest
from conftest import TEST_PASSWORD, sign_in
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pypdf import PdfReader
from test_workflow import action_payload, create, submit

from spec_agent.auth import COOKIE_NAME, AuthStore, LoginInput, token_hash
from spec_agent.config import Settings
from spec_agent.main import create_app


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/standards"),
        ("GET", "/api/inspections"),
        ("GET", "/api/inspections/missing"),
        ("GET", "/api/standards/demo-cover-en-v1/source"),
        ("GET", "/api/inspections/missing/reports/pdf"),
        ("GET", "/api/inspections/missing/reports/xlsx"),
        ("GET", "/api/auth/me"),
        ("GET", "/openapi.json"),
        ("GET", "/docs"),
        ("POST", "/api/standards"),
        ("POST", "/api/standards/demo-cover-en-v1/source"),
        ("POST", "/api/standards/demo-cover-en-v1/activate"),
        ("POST", "/api/inspections"),
        ("POST", "/api/inspections/missing/actions"),
        ("POST", "/api/inspections/missing/retry"),
        ("POST", "/api/auth/logout"),
    ],
)
def test_all_business_routes_require_login(anonymous_client, method, path):
    response = anonymous_client.request(method, path)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert anonymous_client.app.state.workflow.list() == []


def test_public_health_and_no_default_accounts(anonymous_client):
    assert anonymous_client.get("/api/health").status_code == 200
    assert (
        anonymous_client.app.state.auth.connection.execute("SELECT count(*) FROM users").fetchone()[
            0
        ]
        == 0
    )


def test_login_cookie_and_secret_storage(anonymous_client):
    auth = anonymous_client.app.state.auth
    user = auth.create_user("tester", "가상 승인자", TEST_PASSWORD)
    response = anonymous_client.post(
        "/api/auth/login",
        headers={"X-Spec-Agent": "1"},
        json={"username": "TESTER", "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    assert response.json()["user"] == user
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=28800" in cookie
    token = anonymous_client.cookies.get(COOKIE_NAME)
    row = auth.connection.execute("SELECT * FROM sessions").fetchone()
    assert row["token_hash"] == token_hash(token)
    assert token not in dict(row).values()
    account = auth.connection.execute("SELECT * FROM users").fetchone()
    assert account["password_hash"] != TEST_PASSWORD.encode()
    assert TEST_PASSWORD not in response.text and "password_hash" not in response.text
    assert anonymous_client.get("/api/auth/me").json() == response.json()


def test_login_rejects_bad_credentials_and_redacts_validation(anonymous_client):
    anonymous_client.app.state.auth.create_user("tester", "확인자", TEST_PASSWORD)
    for username in ["tester", "missing"]:
        response = anonymous_client.post(
            "/api/auth/login",
            headers={"X-Spec-Agent": "1"},
            json={"username": username, "password": "wrong-password"},
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid username or password."}
        assert COOKIE_NAME not in anonymous_client.cookies
    response = anonymous_client.post(
        "/api/auth/login",
        headers={"X-Spec-Agent": "1"},
        json={"username": "tester", "password": "sensitive" * 100},
    )
    assert response.status_code == 422
    assert "sensitive" not in response.text


def test_csrf_origin_and_upload_guards(client, inspection_payload):
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/inspections", json=inspection_payload).status_code == 403
    client.headers["X-CSRF-Token"] = "forged"
    assert client.post("/api/auth/logout", json={}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    for headers in [{"Origin": "https://attacker.invalid"}, {"Sec-Fetch-Site": "cross-site"}]:
        assert (
            client.post("/api/inspections", json=inspection_payload, headers=headers).status_code
            == 403
        )
        assert (
            client.post(
                "/api/standards/demo-cover-en-v1/source",
                headers=headers,
                files={"file": ("source.pdf", b"%PDF-1.4")},
            ).status_code
            == 403
        )
    assert client.app.state.workflow.list() == []
    client.headers.pop("X-Spec-Agent")
    assert (
        client.post(
            "/api/auth/login", json={"username": "tester", "password": TEST_PASSWORD}
        ).status_code
        == 403
    )


def test_logout_revokes_cookie_and_preserves_pending(client, inspection_payload):
    state = create(client, inspection_payload)
    token = client.cookies.get(COOKIE_NAME)
    assert client.post("/api/auth/logout", json={}).status_code == 200
    assert COOKIE_NAME not in client.cookies
    assert client.get("/api/inspections").status_code == 401
    assert (
        client.get("/api/inspections", headers={"Cookie": f"{COOKIE_NAME}={token}"}).status_code
        == 401
    )
    sign_in(client)
    restored = client.get(f"/api/inspections/{state['inspection_id']}").json()
    assert restored["pending"] == state["pending"]
    assert restored["events"] == []


def test_expired_and_disabled_sessions_cannot_mutate(client, inspection_payload):
    state = create(client, inspection_payload)
    auth = client.app.state.auth
    with auth.connection:
        auth.connection.execute("UPDATE sessions SET expires_at=0")
    endpoint = f"/api/inspections/{state['inspection_id']}/actions"
    assert client.post(endpoint, json=action_payload(state, "confirm")).status_code == 401
    sign_in(client)
    with auth.connection:
        auth.connection.execute("UPDATE users SET active=0")
    assert client.get("/api/auth/me").status_code == 401
    assert client.post(endpoint, json=action_payload(state, "confirm")).status_code == 401
    assert client.app.state.workflow.get(state["inspection_id"])["events"] == []


def test_actor_cannot_be_forged_and_exports_keep_approval_identity(client, inspection_payload):
    state = create(client, inspection_payload)
    endpoint = f"/api/inspections/{state['inspection_id']}/actions"
    for changes in [{"actor": "다른 승인자"}, {"actor_user_id": "forged-id"}]:
        assert (
            client.post(endpoint, json=action_payload(state, "confirm", **changes)).status_code
            == 422
        )
    state = submit(client, state, "confirm")
    assert state["report"]["approval"] is None
    assert state["approver"] == ""
    auth = client.app.state.auth
    approver = auth.create_user("approver", "가상 승인자", TEST_PASSWORD)
    sign_in(client, "approver")
    state = submit(client, state, "approve", decisions={})
    assert state["approver_user_id"] == approver["id"]
    assert state["events"][-1]["actor_user_id"] == approver["id"]
    assert state["report"]["approval"]["name"] == "가상 승인자"
    assert state["report"]["approval"]["at"] == state["events"][-1]["at"]
    with auth.connection:
        auth.connection.execute(
            "UPDATE users SET display_name='변경 이름' WHERE id=?", (approver["id"],)
        )
    sign_in(client)
    restored = client.get(f"/api/inspections/{state['inspection_id']}").json()
    assert restored["approver"] == "가상 승인자"
    assert restored["report"] == state["report"]
    pdf = client.get(f"/api/inspections/{state['inspection_id']}/reports/pdf")
    assert pdf.headers["cache-control"] == "no-store"
    assert "가상 승인자" in "".join(
        page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages
    )
    excel = client.get(f"/api/inspections/{state['inspection_id']}/reports/xlsx")
    sheet = load_workbook(BytesIO(excel.content)).active
    assert any("가상 승인자" in str(cell.value) for row in sheet for cell in row)
    assert client.post("/api/auth/logout", json={}).status_code == 200
    for extension in ("pdf", "xlsx"):
        assert (
            client.get(f"/api/inspections/{state['inspection_id']}/reports/{extension}").status_code
            == 401
        )


def test_idempotency_does_not_impersonate_same_named_account(client, inspection_payload):
    state = create(client, inspection_payload)
    endpoint = f"/api/inspections/{state['inspection_id']}/actions"
    payload = action_payload(state, "confirm")
    assert client.post(endpoint, json=payload).status_code == 200
    client.app.state.auth.create_user("another", "확인자", TEST_PASSWORD)
    sign_in(client, "another")
    assert client.post(endpoint, json=payload).status_code == 409


def test_sessions_survive_restart_and_secure_cookie(tmp_path):
    settings = Settings(tmp_path, cookie_secure=True)
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        client.app.state.auth.create_user("tester", "확인자", TEST_PASSWORD)
        session = sign_in(client)
        token = client.cookies.get(COOKIE_NAME)
        response = client.post(
            "/api/auth/login", json={"username": "tester", "password": TEST_PASSWORD}
        )
        assert "Secure" in response.headers["set-cookie"]
        assert client.app.state.auth.session(token) is None
        cookies = client.cookies
        session = response.json()
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        client.cookies.update(cookies)
        assert client.get("/api/auth/me").json() == session


def test_failed_login_throttle(anonymous_client):
    for attempt in range(6):
        response = anonymous_client.post(
            "/api/auth/login",
            headers={"X-Spec-Agent": "1"},
            json={"username": "missing", "password": "wrong"},
        )
        assert response.status_code == (401 if attempt < 5 else 429)


def test_account_validation_and_password_whitespace(tmp_path):
    auth = AuthStore(Settings(tmp_path))
    try:
        with pytest.raises(ValueError):
            auth.create_user("tester", " ", TEST_PASSWORD)
        with pytest.raises(ValueError):
            auth.create_user("tester", "이름", "short")
        password = "  keep spaces in password  "
        auth.create_user("tester", "이름", password)
        with pytest.raises(ValueError, match="already registered"):
            auth.create_user("TESTER", "이름", TEST_PASSWORD)
        token, _ = auth.login(LoginInput(username="tester", password=password), "local", None)
        assert auth.session(token)
        assert (tmp_path / "auth.sqlite").stat().st_mode & 0o777 == 0o600
    finally:
        auth.close()


def test_create_user_command(tmp_path, monkeypatch, capsys):
    from spec_agent.cli import main

    monkeypatch.setenv("SPEC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "sys.argv", ["spec-agent", "create-user", "--username", "tester", "--name", "가상 사용자"]
    )
    monkeypatch.setattr("getpass.getpass", lambda _: TEST_PASSWORD)
    main()
    output = capsys.readouterr().out
    assert "Account created" in output
    assert TEST_PASSWORD not in output
    auth = AuthStore(Settings(tmp_path))
    try:
        _, session = auth.login(
            LoginInput(username="tester", password=TEST_PASSWORD), "local", None
        )
        assert session["user"]["display_name"] == "가상 사용자"
    finally:
        auth.close()
