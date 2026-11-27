"""My page (name and password) and the extra hosted-demo team accounts."""

from conftest import TEST_PASSWORD, sign_in
from fastapi.testclient import TestClient

from spec_agent.config import Settings
from spec_agent.main import create_app


def test_rename_and_change_password(client):
    renamed = client.patch("/api/auth/me", json={"display_name": "품질팀 김재익"}).json()
    assert renamed["user"]["display_name"] == "품질팀 김재익"
    assert client.get("/api/auth/me").json()["user"]["display_name"] == "품질팀 김재익"
    wrong = client.post(
        "/api/auth/password",
        json={"current_password": "nope", "new_password": "another-password-99"},
    )
    assert wrong.status_code == 400
    short = client.post(
        "/api/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "short"}
    )
    assert short.status_code == 400
    ok = client.post(
        "/api/auth/password",
        json={"current_password": TEST_PASSWORD, "new_password": "another-password-99"},
    )
    assert ok.status_code == 200
    client.post("/api/auth/logout")
    sign_in(client, password="another-password-99")


def test_demo_team_accounts_use_the_env_password(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEC_AGENT_DEMO_USERNAME", "demo")
    monkeypatch.setenv("SPEC_AGENT_DEMO_PASSWORD", "demo-password-1234")
    monkeypatch.setenv("SPEC_AGENT_DEMO_TEAM_PASSWORD", "0000")
    with TestClient(create_app(Settings(tmp_path))) as client:
        session = sign_in(client, "jeb", "0000")
        assert session["user"]["display_name"] == "정은비"
        for username in ("kji", "lsm", "kmk"):
            client.post("/api/auth/logout")
            sign_in(client, username, "0000")


def test_demo_team_is_off_without_a_password(tmp_path, monkeypatch):
    monkeypatch.delenv("SPEC_AGENT_DEMO_TEAM_PASSWORD", raising=False)
    with TestClient(create_app(Settings(tmp_path))) as client:
        client.headers["X-Spec-Agent"] = "1"
        client.headers["Origin"] = str(client.base_url).rstrip("/")
        response = client.post("/api/auth/login", json={"username": "kji", "password": "0000"})
        assert response.status_code == 401


def test_sample_forms_can_be_read_from_the_page(client):
    samples = client.get("/api/forms/samples").json()
    assert len(samples) == 7 and samples[3]["label"]["ko"].startswith("한바다조선")
    reading = client.post("/api/forms/samples/3").json()
    assert reading["draft"]["revision"] == "D"
    assert client.post("/api/forms/samples/99").status_code == 404
