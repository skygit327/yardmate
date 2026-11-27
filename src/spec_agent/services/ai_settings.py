"""AI connection settings entered from the app (stored in the data folder, not in Git).

Settings saved here override the values from .env / the environment. The API key is
written only to `<data_dir>/ai_settings.json` (owner-only permissions) and is never
returned to the browser; the UI only sees whether a key is set and its last characters.
"""

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator

from spec_agent.config import LLMSettings
from spec_agent.schemas.standard import StrictModel


class AISettingsInput(StrictModel):
    provider: Literal["none", "openai", "anthropic"]
    model: str = Field(default="", max_length=120)
    base_url: str = Field(default="", max_length=300)
    api_key: str | None = Field(default=None, max_length=500)
    allow_internal: bool = False
    test: bool = True

    @field_validator("base_url")
    @classmethod
    def check_url(cls, value: str) -> str:
        value = value.strip()
        if value and urlparse(value).scheme not in {"http", "https"}:
            raise ValueError("The endpoint must start with http:// or https://.")
        return value.rstrip("/")


def key_hint(key: str | None) -> str | None:
    if not key:
        return None
    return f"{key[:3]}…{key[-4:]}" if len(key) > 10 else "set"


class AISettingsStore:
    def __init__(self, data_dir: Path, env_settings: LLMSettings):
        self.path = data_dir / "ai_settings.json"
        self.env_settings = env_settings

    def saved(self) -> dict | None:
        if not self.path.is_file():
            return None
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def effective(self) -> tuple[LLMSettings, str]:
        saved = self.saved()
        if saved:
            try:
                return replace(self.env_settings, **saved), "app"
            except (TypeError, ValueError):
                pass
        source = "env" if self.env_settings.provider != "none" else "none"
        return self.env_settings, source

    def build(self, data: AISettingsInput, current: LLMSettings) -> LLMSettings:
        if data.provider == "none":
            return replace(current, provider="none", model="", api_key=None, base_url=None)
        api_key = data.api_key.strip() if data.api_key and data.api_key.strip() else None
        if api_key is None and current.provider == data.provider:
            api_key = current.api_key
        return LLMSettings(
            provider=data.provider,
            model=data.model.strip(),
            base_url=data.base_url or None,
            api_key=api_key,
            allow_internal=data.allow_internal,
            timeout=current.timeout,
            temperature=current.temperature,
        )

    def save(self, settings: LLMSettings):
        payload = {
            "provider": settings.provider,
            "model": settings.model,
            "base_url": settings.base_url,
            "api_key": settings.api_key,
            "allow_internal": settings.allow_internal,
        }
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temporary, self.path)

    def clear(self):
        self.path.unlink(missing_ok=True)

    @staticmethod
    def public(settings: LLMSettings, source: str) -> dict:
        return {
            "provider": settings.provider,
            "model": settings.model,
            "base_url": settings.base_url or "",
            "key_set": bool(settings.api_key),
            "key_hint": key_hint(settings.api_key),
            "allow_internal": settings.allow_internal,
            "external": settings.external,
            "source": source,
        }
