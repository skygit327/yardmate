"""YardMate chat: plain-language requests open the right page or do the work."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import sign_in
from fastapi.testclient import TestClient

from spec_agent.config import LLMSettings, Settings
from spec_agent.main import create_app

DOCS = Path(__file__).resolve().parents[1] / "docs"


def ask(client, message, language="ko"):
    response = client.post("/api/assistant", json={"message": message, "language": language})
    assert response.status_code == 200, response.text
    return response.json()


def test_tasks_are_added_and_listed_from_chat(client):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    added = ask(client, "내일까지 8D 보고서 작성 할 일 추가해줘")
    assert added["interpreter"] == "rules"
    assert (
        added["cards"][0]["title"] == "8D 보고서 작성" and added["cards"][0]["detail"] == tomorrow
    )
    tasks = client.get("/api/tasks").json()
    assert tasks[0]["due"] == tomorrow and tasks[0]["source"] == "manual"
    listed = ask(client, "오늘 할 일 뭐 있어?")
    assert listed["navigate"]["page"] == "tasks"
    assert "1" in listed["reply"]
    assert ask(client, "add a task review gauge R&R on 11/3", "en")["cards"][0]["detail"].endswith(
        "-11-03"
    )


def test_navigation_requests(client):
    assert ask(client, "기준서 양식 읽고 싶어")["navigate"]["page"] == "forms"
    assert ask(client, "캘린더 보여줘")["navigate"]["page"] == "calendar"
    assert ask(client, "AI 설정 바꾸고 싶어")["navigate"]["page"] == "ai"
    inspection = ask(client, "DEMO-COVER 새 검사 시작")
    assert inspection["navigate"] == {
        "page": "inspection_new",
        "params": {"standard_id": "demo-cover-en-v1"},
    }
    unknown = ask(client, "음...")
    assert unknown["cards"] == [{"kind": "help"}]


def test_standards_are_found_and_revised_from_chat(client):
    found = ask(client, "DEMO-COVER 기준서 찾아줘")
    assert (
        found["cards"][0]["kind"] == "standard"
        and found["cards"][0]["link"]["id"] == "demo-cover-en-v1"
    )
    revise = ask(client, "DEMO-COVER 버 높이를 0.15로 강화해줘")
    assert revise["navigate"]["page"] == "standard"
    assert revise["navigate"]["params"]["instruction"] == "DEMO-COVER 버 높이를 0.15로 강화해줘"


def test_lots_and_failed_lots(client):
    client.post("/api/demo/shipyard")
    lot = ask(client, "E21-B08 결과 알려줘")
    assert lot["cards"][0]["kind"] == "lot" and lot["cards"][0]["tone"] == "FAIL"
    assert lot["cards"][0]["link"]["type"] == "inspection"
    failed = ask(client, "不合格批次有哪些？", "zh")
    assert [card["title"].split(" · ")[0] for card in failed["cards"]] == ["E21-B08"]
    assert "1" in failed["reply"]
    assert ask(client, "X9-Z99 로트 결과")["navigate"]["page"] == "inspections"


def test_support_notices_from_chat(client, monkeypatch):
    monkeypatch.delenv("SPEC_AGENT_BIZINFO_API_KEY", raising=False)
    client.post("/api/programs/samples")
    found = ask(client, "거제에서 신청 가능한 지원사업 있어?")
    assert found["navigate"] == {"page": "programs", "params": {"region": "경남"}}
    assert [card["tone"] for card in found["cards"]] == ["ELIGIBLE"]
    exports = ask(client, "export support notices", "en")
    assert all(card["kind"] == "program" for card in exports["cards"]) and exports["cards"]
    assert "(for Export)" in exports["reply"]

    collected = ask(client, "기업마당 새 공고 가져와줘")
    assert "3" in collected["reply"] and collected["navigate"]["page"] == "programs"

    notice = (
        Path(__file__).resolve().parents[1]
        / "src/spec_agent/services/support_samples"
        / "02_busan_port_logistics.txt"
    ).read_text("utf-8")
    judged = ask(client, notice.replace("[샘플 · 가상 공고]", "[가상]"), "zh")
    assert judged["cards"][0]["kind"] == "program" and judged["cards"][0]["tone"] == "INELIGIBLE"
    assert "不可申请" in judged["reply"]


class Planner:
    label = "fake/planner"

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def complete(self, system, user, json_mode=True):
        self.prompts.append(user)
        return json.dumps(self.answer)


@pytest.mark.parametrize("external", [False, True])
def test_ai_planner_and_data_protection(tmp_path, external):
    planner = Planner(
        {
            "reply": "Opening forms and adding the task.",
            "actions": [
                {"type": "navigate", "page": "forms"},
                {"type": "add_task", "title": "Check DEMO-OEM form", "due": "2026-11-02"},
                {"type": "navigate", "page": "trace"},
                {"type": "delete_everything"},
            ],
        }
    )
    base_url = "https://api.example.com/v1" if external else "http://127.0.0.1:9/v1"
    settings = Settings(
        tmp_path, llm=LLMSettings(provider="openai", model="m", base_url=base_url, api_key="k")
    )
    with TestClient(create_app(settings, planner)) as client:
        client.app.state.auth.create_user("tester", "Tester", "testing-password-1234")
        sign_in(client)
        result = ask(
            client, "please open the forms and remind me to check the DEMO-OEM form on 11/2", "en"
        )
        if external:  # customer data may not leave the company: rules only
            assert planner.prompts == [] and result["interpreter"] == "rules" and result["notes"]
        else:
            assert result["interpreter"] == "llm:fake/planner"
            assert result["reply"] == "Opening forms and adding the task."
            assert result["navigate"]["page"] == "forms"
            assert result["cards"][0]["title"] == "Check DEMO-OEM form"
            assert "DEMO CUSTOMER" in planner.prompts[0]
