import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import sign_in
from fastapi.testclient import TestClient

from spec_agent.config import LLMSettings, Settings, load_env_file
from spec_agent.main import create_app
from spec_agent.services.revision_agent import LLMClient, LLMError


class StubOpenAI:
    """A local stand-in for the OpenAI chat completions endpoint."""

    def __init__(self):
        self.requests: list[dict] = []
        self.responses: list[tuple[int, dict]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append(
                    {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
                )
                status, payload = stub.responses.pop(0) if stub.responses else (500, {})
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def answer(content: dict | str) -> tuple[int, dict]:
        text = content if isinstance(content, str) else json.dumps(content)
        return 200, {"choices": [{"message": {"content": text}}]}

    @staticmethod
    def error(status: int, message: str) -> tuple[int, dict]:
        return status, {"error": {"message": message}}

    def settings(self, **changes) -> LLMSettings:
        return LLMSettings(
            provider="openai", model="gpt-test", base_url=self.url, api_key="sk-test", **changes
        )

    def close(self):
        self.server.shutdown()


@pytest.fixture
def stub():
    server = StubOpenAI()
    yield server
    server.close()


def test_openai_request_format_and_ping(stub):
    stub.responses.append(stub.answer({"ok": True}))
    result = LLMClient(stub.settings()).ping()
    assert result["ok"] is True
    request = stub.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["auth"] == "Bearer sk-test"
    assert request["body"]["model"] == "gpt-test"
    assert request["body"]["response_format"] == {"type": "json_object"}
    assert "temperature" not in request["body"]
    stub.responses.append(stub.answer({"ok": True}))
    LLMClient(stub.settings(temperature=0.0)).ping()
    assert stub.requests[1]["body"]["temperature"] == 0.0


def test_openai_errors_are_readable_and_retried(stub):
    stub.responses.append(stub.error(401, "Incorrect API key provided."))
    with pytest.raises(LLMError, match="HTTP 401: Incorrect API key provided."):
        LLMClient(stub.settings()).ping()
    stub.responses += [stub.error(429, "Rate limit"), stub.answer({"ok": True})]
    assert LLMClient(stub.settings()).ping()["ok"] is True
    stub.responses += [
        stub.error(400, "Invalid parameter: 'response_format' is not supported."),
        stub.answer('```json\n{"ok": true}\n```'),
    ]
    assert LLMClient(stub.settings()).ping()["ok"] is True
    assert "response_format" not in stub.requests[-1]["body"]
    stub.responses.append(stub.answer(""))
    with pytest.raises(LLMError, match="empty"):
        LLMClient(stub.settings()).ping()


def test_unreachable_endpoint_is_reported():
    settings = LLMSettings(
        provider="openai", model="m", base_url="http://127.0.0.1:9/v1", timeout=2
    )
    with pytest.raises(LLMError):
        LLMClient(settings).ping()


def test_revision_and_request_analysis_use_openai(stub, tmp_path):
    settings = Settings(tmp_path, llm=stub.settings())
    with TestClient(create_app(settings)) as client:
        client.app.state.auth.create_user("tester", "Tester", "testing-password-1234")
        sign_in(client)
        stub.responses.append(
            stub.answer(
                {
                    "summary": "Tighten burr height to 0.18 mm",
                    "revision": None,
                    "operations": [
                        {
                            "op": "update_condition",
                            "rule_id": "burr",
                            "field": "height",
                            "values": {"limit": "0.18"},
                            "reason": "customer request",
                        }
                    ],
                    "questions": [],
                }
            )
        )
        proposal = client.post(
            "/api/standards/demo-cover-en-v1/revision-proposals",
            data={"instruction": "Please make the burr limit a bit stricter, 0.18"},
        ).json()
        assert proposal["interpreter"] == "llm:openai/gpt-test"
        assert proposal["operations"][0]["values"] == {"limit": "0.18"}
        sent = stub.requests[-1]["body"]["messages"]
        assert "Change request" in sent[1]["content"] and "burr" in sent[1]["content"]

        stub.responses.append(
            stub.answer(
                {
                    "summary": "Customer asks for the burr limit",
                    "category": "INQUIRY",
                    "priority": "NORMAL",
                    "key_points": ["Confirm burr height limit"],
                    "response_draft": "Dear team, the current limit is Height < 0.20 mm.",
                }
            )
        )
        response = client.post(
            "/api/customer-requests",
            data={"customer": "DEMO CUSTOMER", "body": "What is the burr limit for DEMO-COVER?"},
        ).json()
        assert response["analysis"]["interpreter"] == "llm:openai/gpt-test"
        assert response["response_draft"].startswith("Dear team")
        health = client.get("/api/health").json()
        assert health["llm"] == {"provider": "openai", "external": False, "allow_internal": False}


def test_env_file_loading(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "SPEC_AGENT_LLM_PROVIDER=openai\n"
        'SPEC_AGENT_LLM_MODEL="gpt-test"\n'
        "export SPEC_AGENT_LLM_API_KEY=sk-from-file  # inline comment\n"
        "SPEC_AGENT_LLM_TIMEOUT=30\n"
    )
    for key in ("SPEC_AGENT_LLM_PROVIDER", "SPEC_AGENT_LLM_MODEL", "SPEC_AGENT_LLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SPEC_AGENT_LLM_TIMEOUT", "5")
    monkeypatch.setenv("SPEC_AGENT_ENV_FILE", str(env))
    monkeypatch.chdir(tmp_path)
    try:
        assert load_env_file() == env
        settings = Settings.from_env()
        assert settings.llm.provider == "openai" and settings.llm.model == "gpt-test"
        assert settings.llm.api_key == "sk-from-file"
        assert settings.llm.timeout == 5  # variables already set are not overridden
        assert settings.llm.external is True
    finally:
        for key in ("SPEC_AGENT_LLM_PROVIDER", "SPEC_AGENT_LLM_MODEL", "SPEC_AGENT_LLM_API_KEY"):
            os.environ.pop(key, None)
    assert load_env_file(tmp_path / "missing.env") is None


def test_ai_settings_screen_api(stub, tmp_path):
    def connect(create_user: bool):
        connection = TestClient(create_app(Settings(tmp_path)))
        connection.__enter__()
        if create_user:
            connection.app.state.auth.create_user("tester", "Tester", "testing-password-1234")
        sign_in(connection)
        return connection

    client = connect(create_user=True)
    try:
        assert client.get("/api/ai-settings").json()["source"] == "none"
        body = {
            "provider": "openai",
            "model": "gpt-test",
            "base_url": stub.url,
            "api_key": "sk-secret-1234",
        }

        stub.responses.append(stub.error(401, "Incorrect API key provided."))
        failed = client.put("/api/ai-settings", json=body).json()
        assert failed["saved"] is False and "HTTP 401" in failed["test"]["error"]
        assert failed["settings"]["provider"] == "none"

        stub.responses.append(stub.answer({"ok": True}))
        saved = client.put("/api/ai-settings", json=body).json()
        assert saved["saved"] is True and saved["test"]["ok"] is True
        public = client.get("/api/ai-settings").json()
        assert public["provider"] == "openai" and public["key_set"] is True
        assert public["key_hint"] == "sk-…1234" and public["source"] == "app"
        assert "sk-secret-1234" not in json.dumps(public)
        stored = tmp_path / "ai_settings.json"
        assert stored.stat().st_mode & 0o777 == 0o600
        assert json.loads(stored.read_text())["api_key"] == "sk-secret-1234"
        assert client.get("/api/health").json()["llm"]["provider"] == "openai"

        stub.responses.append(stub.answer({"ok": True}))
        kept = client.put(
            "/api/ai-settings", json={**body, "api_key": "", "model": "gpt-other"}
        ).json()
        assert kept["saved"] is True and stub.requests[-1]["auth"] == "Bearer sk-secret-1234"
        assert stub.requests[-1]["body"]["model"] == "gpt-other"

        stub.responses.append(
            stub.answer({"summary": "", "operations": [], "questions": ["Which area?"]})
        )
        proposal = client.post(
            "/api/standards/demo-cover-en-v1/revision-proposals", data={"instruction": "burr"}
        ).json()
        assert proposal["interpreter"] == "llm:openai/gpt-other"

        bad = client.put("/api/ai-settings", json={**body, "base_url": "ftp://x"})
        assert bad.status_code == 422
        no_model = client.put("/api/ai-settings", json={**body, "model": "", "test": False})
        assert no_model.status_code == 422
    finally:
        client.__exit__(None, None, None)

    client = connect(create_user=False)  # saved settings survive a restart
    try:
        assert client.get("/api/ai-settings").json()["model"] == "gpt-other"
        reset = client.delete("/api/ai-settings").json()
        assert reset["provider"] == "none" and not (tmp_path / "ai_settings.json").exists()
        off = client.put("/api/ai-settings", json={"provider": "none"}).json()
        assert off["saved"] is True and off["settings"]["provider"] == "none"
    finally:
        client.__exit__(None, None, None)
