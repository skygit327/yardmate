import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


def load_env_file(path: Path | None = None) -> Path | None:
    """Load KEY=VALUE lines from .env without overriding variables already set.

    The file stays on the local machine (.env is git-ignored); secrets such as API keys
    belong there, never in source files.
    """
    path = path or Path(os.environ.get("SPEC_AGENT_ENV_FILE", ".env"))
    if not path.is_file():
        return None
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value
    return path


@dataclass(frozen=True)
class LLMSettings:
    """Optional model used by the standard revision agent.

    provider: "none" (offline rule interpreter only), "anthropic", or "openai" (any
    OpenAI-compatible endpoint, e.g. Ollama at http://localhost:11434/v1).
    """

    provider: str = "none"
    model: str = ""
    base_url: str | None = None
    api_key: str | None = None
    allow_internal: bool = False
    timeout: float = 60.0
    temperature: float | None = None

    def __post_init__(self):
        if self.provider not in {"none", "anthropic", "openai"}:
            raise ValueError("SPEC_AGENT_LLM_PROVIDER must be none, anthropic, or openai.")
        if self.provider != "none" and not self.model:
            raise ValueError("Set SPEC_AGENT_LLM_MODEL when an LLM provider is configured.")

    @property
    def external(self) -> bool:
        """True when requests leave this machine/LAN-local model server."""
        if self.provider == "none":
            return False
        if not self.base_url:
            return True
        return (urlparse(self.base_url).hostname or "").lower() not in LOCAL_HOSTS

    @classmethod
    def from_env(cls):
        provider = os.environ.get("SPEC_AGENT_LLM_PROVIDER", "none").strip().lower() or "none"
        return cls(
            provider=provider,
            model=os.environ.get("SPEC_AGENT_LLM_MODEL", "").strip(),
            base_url=os.environ.get("SPEC_AGENT_LLM_BASE_URL") or None,
            api_key=os.environ.get("SPEC_AGENT_LLM_API_KEY")
            or (os.environ.get("ANTHROPIC_API_KEY") if provider == "anthropic" else None)
            or (os.environ.get("OPENAI_API_KEY") if provider == "openai" else None),
            allow_internal=os.environ.get("SPEC_AGENT_LLM_ALLOW_INTERNAL", "false").lower()
            == "true",
            timeout=float(os.environ.get("SPEC_AGENT_LLM_TIMEOUT", "60")),
            temperature=float(os.environ["SPEC_AGENT_LLM_TEMPERATURE"])
            if os.environ.get("SPEC_AGENT_LLM_TEMPERATURE")
            else None,
        )


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    font_path: Path | None = None
    session_seconds: int = 8 * 60 * 60
    cookie_secure: bool = False
    llm: LLMSettings = field(default_factory=LLMSettings)
    report_languages: tuple[str, ...] = ("en", "ko", "zh")
    # Watched work folders (automation). None disables the folder watcher.
    workspace_dir: Path | None = None
    automation_interval: float = 5.0
    automation_language: str = "ko"

    def __post_init__(self):
        if self.session_seconds <= 0:
            raise ValueError("The session lifetime must be positive.")
        unsupported = set(self.report_languages) - {"en", "ko", "zh"}
        if unsupported:
            raise ValueError("SPEC_AGENT_REPORT_LANGUAGES supports en, ko, and zh.")

    @classmethod
    def from_env(cls):
        load_env_file()
        font = os.environ.get("SPEC_AGENT_FONT")
        return cls(
            data_dir=Path(os.environ.get("SPEC_AGENT_DATA_DIR", ".runtime")).resolve(),
            font_path=Path(font).expanduser() if font else None,
            session_seconds=int(os.environ.get("SPEC_AGENT_SESSION_SECONDS", "28800")),
            cookie_secure=os.environ.get("SPEC_AGENT_COOKIE_SECURE", "false").lower() == "true",
            llm=LLMSettings.from_env(),
            report_languages=tuple(
                language.strip().lower()
                for language in os.environ.get("SPEC_AGENT_REPORT_LANGUAGES", "en,ko,zh").split(",")
                if language.strip()
            ),
            workspace_dir=None
            if os.environ.get("SPEC_AGENT_AUTOMATION", "true").lower() in {"0", "false", "off"}
            else Path(os.environ.get("SPEC_AGENT_WORKSPACE_DIR", "workspace"))
            .expanduser()
            .resolve(),
            automation_interval=float(os.environ.get("SPEC_AGENT_AUTOMATION_INTERVAL", "5")),
            automation_language=os.environ.get("SPEC_AGENT_AUTOMATION_LANGUAGE", "ko"),
        )
