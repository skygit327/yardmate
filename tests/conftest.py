import pytest
from fastapi.testclient import TestClient

from spec_agent.config import Settings
from spec_agent.main import create_app
from spec_agent.services.standards import demo_observations

TEST_PASSWORD = "testing-password-1234"


def sign_in(client, username="tester", password=TEST_PASSWORD):
    client.headers["X-Spec-Agent"] = "1"
    client.headers["Origin"] = str(client.base_url).rstrip("/")
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()


@pytest.fixture
def anonymous_client(tmp_path):
    with TestClient(create_app(Settings(tmp_path))) as connection:
        yield connection


@pytest.fixture
def client(anonymous_client):
    anonymous_client.app.state.auth.create_user("tester", "확인자", TEST_PASSWORD)
    sign_in(anonymous_client)
    return anonymous_client


@pytest.fixture
def inspection_payload():
    return {
        "standard_id": "demo-cover-en-v1",
        "lot": "TEST-001",
        "inspector": "검사원",
        "observations": demo_observations(),
    }
