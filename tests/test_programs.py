"""Support-programme matching for Dongnam shipbuilding suppliers: notice -> conditions -> verdict."""

import json
from datetime import date

import pytest

from spec_agent.services.support_programs import (
    DEMO_PROFILE,
    SAMPLES,
    CompanyProfile,
    judge,
    read_notice,
)

TODAY = date(2026, 11, 27)


def sample(name: str) -> dict:
    path = next(SAMPLES.glob(f"{name}*.txt"))
    return read_notice(path.read_text("utf-8"), path.name, TODAY)


def statuses(match: dict) -> dict:
    return {c["field"]: c["status"] for c in match["conditions"] if c["group"] != "preferred"}


def test_notice_becomes_one_standard_structure():
    notice = sample("01")
    assert notice["agency"] == "(가상) 동남권 조선해양혁신센터"
    assert notice["opens"] == "2026-12-01" and notice["deadline"] == "2026-12-18"
    assert notice["amount"]["manwon"] == 7000
    assert len(notice["documents"]) == 5
    assert notice["synthetic"] is True
    fields = {c["field"] for c in notice["conditions"]}
    assert {
        "area",
        "company_type",
        "industry",
        "employees",
        "partner",
        "tax",
        "closed",
        "duplicate",
    } <= fields
    employees = next(c for c in notice["conditions"] if c["field"] == "employees")
    assert employees["rule"]["all"] == [[">=", 10.0], ["<", 300.0]] or employees["rule"]["all"] == [
        (">=", 10.0),
        ("<", 300.0),
    ]
    assert employees["source"] == "상시근로자 10인 이상 300인 미만"


def test_demo_company_is_judged_per_condition_with_reasons():
    eligible = judge(sample("01"), DEMO_PROFILE, TODAY)
    assert eligible["overall"] == "ELIGIBLE" and eligible["met"] == eligible["required"]
    assert eligible["bonus"] == 1  # ISO 3834 / KR class approval
    area = next(c for c in eligible["conditions"] if c["field"] == "area")
    assert area["rule"]["any"][:2] == ["거제", "통영"] and area["status"] == "pass"
    assert "region" not in statuses(eligible)  # the crisis-area line is not read as a province

    blocked = judge(sample("02"), DEMO_PROFILE, TODAY)
    assert blocked["overall"] == "INELIGIBLE"
    assert statuses(blocked)["region"] == "fail" and statuses(blocked)["industry"] == "fail"

    check = judge(sample("03"), DEMO_PROFILE, TODAY)
    assert check["overall"] == "CHECK"
    unknown = [c for c in check["conditions"] if c["status"] == "unknown"]
    assert {c["field"] for c in unknown} == {"other"}
    assert any("선급 승인" in c["source"] for c in unknown)  # never guessed: the sentence is kept


def test_profile_changes_the_verdict():
    small_exporter = DEMO_PROFILE.model_copy(update={"export_usd": 300_000})
    match = judge(sample("03"), small_exporter, TODAY)
    assert statuses(match)["export"] == "fail"
    outside = DEMO_PROFILE.model_copy(update={"region": "울산 남구"})
    assert statuses(judge(sample("01"), outside, TODAY))["area"] == "fail"
    dong_gu = DEMO_PROFILE.model_copy(update={"region": "울산광역시 동구"})
    assert statuses(judge(sample("01"), dong_gu, TODAY))["area"] == "pass"
    arrears = DEMO_PROFILE.model_copy(update={"tax_arrears": True})
    assert judge(sample("01"), arrears, TODAY)["overall"] == "INELIGIBLE"
    busan = CompanyProfile(
        region="부산 중구",
        company_type="창업기업",
        industry="항만물류 (컨테이너 운송)",
        employees=6,
        founded=2023,
    )
    assert judge(sample("02"), busan, TODAY)["overall"] == "ELIGIBLE"


def test_closed_notice():
    assert judge(sample("01"), DEMO_PROFILE, date(2026, 12, 19))["overall"] == "CLOSED"


def test_api_samples_profile_and_tasks(client):
    profile = client.get("/api/company-profile").json()
    assert profile["demo"] is True and profile["region"] == "경남 거제시"
    added = client.post("/api/programs/samples").json()
    assert len(added) == 3
    assert client.post("/api/programs/samples").json() == []  # loaded once
    programs = client.get("/api/programs").json()
    assert [p["match"]["overall"] for p in programs][:1] == ["ELIGIBLE"]
    first = programs[0]
    result = client.post(f"/api/programs/{first['id']}/tasks").json()
    assert len(result["tasks"]) == len(first["notice"]["documents"]) + 1
    tasks = [t for t in client.get("/api/tasks").json() if t["kind"] == "programs"]
    assert len(tasks) == len(result["tasks"])
    assert all(t["link"] == {"type": "program", "id": first["id"]} for t in tasks)
    assert client.post(f"/api/programs/{first['id']}/tasks").json()["tasks"] == result["tasks"]

    saved = client.put(
        "/api/company-profile",
        json={
            **{k: v for k, v in profile.items() if k not in {"demo", "choices"}},
            "region": "서울 강남구",
        },
    ).json()
    assert saved["demo"] is False
    assert all(p["match"]["overall"] != "ELIGIBLE" for p in client.get("/api/programs").json())
    summary = client.get("/api/home").json()["programs"]
    assert summary["total"] == 3


def test_api_paste_and_reject(client):
    text = "지원사업 공고\n신청자격: 울산 소재 중소기업\n접수기간: 2027. 1. 5. ~ 2027. 1. 20.\n제출서류\n- 신청서"
    program = client.post("/api/programs", json={"text": text}).json()
    assert program["notice"]["deadline"] == "2027-01-20"
    assert program["match"]["overall"] == "INELIGIBLE"  # demo company is in Gyeongnam
    response = client.post(
        "/api/programs/file", files={"file": ("x.exe", b"MZ" * 20, "application/octet-stream")}
    )
    assert response.status_code == 422
    assert client.delete(f"/api/programs/{program['id']}").status_code == 200
    assert client.get(f"/api/programs/{program['id']}").status_code == 404


class FakeModel:
    label = "llm:fake/model"

    def __init__(self):
        self.prompts = []

    def complete(self, system, user, json_mode=True):
        self.prompts.append(user)
        ids = [c["id"] for c in json.loads(user)["conditions"]]
        return json.dumps(
            {
                "conditions": [
                    {
                        "id": ids[0],
                        "status": "pass",
                        "reason": {
                            "en": "Has KR approval.",
                            "ko": "KR 승인 보유.",
                            "zh": "持有 KR 认可。",
                        },
                    },
                    {"id": "made-up", "status": "pass", "reason": {}},
                ]
            }
        )


@pytest.mark.parametrize(
    ("base_url", "allow", "expected"),
    [
        ("http://127.0.0.1:9/v1", False, 200),
        ("https://api.example.com/v1", False, 409),
        ("https://api.example.com/v1", True, 200),
    ],
)
def test_ai_check_adds_an_opinion_without_changing_the_rule_verdict(
    tmp_path, base_url, allow, expected
):
    from conftest import sign_in
    from fastapi.testclient import TestClient

    from spec_agent.config import LLMSettings, Settings
    from spec_agent.main import create_app

    model = FakeModel()
    llm = LLMSettings(
        provider="openai", model="m", base_url=base_url, api_key="k", allow_internal=allow
    )
    with TestClient(create_app(Settings(tmp_path, llm=llm), model)) as client:
        client.app.state.auth.create_user("tester", "Tester", "testing-password-1234")
        sign_in(client)
        client.post("/api/programs/samples")
        target = next(
            p for p in client.get("/api/programs").json() if p["filename"].startswith("03")
        )
        response = client.post(f"/api/programs/{target['id']}/ai-check")
        assert response.status_code == expected
        if expected != 200:
            assert model.prompts == []  # nothing left the company
            return
        checked = response.json()
        assert checked["match"]["overall"] == target["match"]["overall"]  # rules stay official
        opinion = list(checked["ai"]["conditions"].values())
        assert len(opinion) == 1 and opinion[0]["status"] == "pass"
        assert opinion[0]["reason"]["zh"] == "持有 KR 认可。"
        assert checked["ai"]["model"] == "llm:fake/model"
        assert "거제" in model.prompts[0]  # the company profile was the context


def test_ai_check_needs_a_model(client):
    client.post("/api/programs/samples")
    program = client.get("/api/programs").json()[0]
    response = client.post(f"/api/programs/{program['id']}/ai-check")
    assert response.status_code == 409
