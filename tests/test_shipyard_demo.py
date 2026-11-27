"""Dongnam demo: Korean, English and Chinese shipbuilding forms become standards and lots."""

from io import BytesIO

from pypdf import PdfReader


def test_shipyard_demo_reads_three_languages_and_reports_in_chinese(client):
    created = client.post("/api/demo/shipyard").json()
    assert created == {"standards": 3, "inspections": 4, "programs": 3, "tasks": 15}
    again = client.post("/api/demo/shipyard").json()
    assert again["standards"] == 0 and again["tasks"] == 0  # loaded once

    standards = {s["document_number"]: s for s in client.get("/api/standards").json()}
    assert {"HB-WLD-201", "DH-PNT-110", "HC-QC-07"} <= set(standards)
    owner = standards["HC-QC-07"]
    assert owner["status"] == "ACTIVE" and owner["customer"].endswith("海川船务 上海")
    undercut = next(r for r in owner["rules"] if r["item"] == "咬边" and r["area"] == "管对接焊缝")
    assert undercut["conditions"][0]["limit"] == "0.3"

    lots = {i["inputs"]["lot"]: i for i in client.get("/api/inspections").json()}
    assert lots["E21-B08"]["overall_result"] == "FAIL"
    assert lots["HC-S03"]["status"] == "APPROVED"
    report = client.get(f"/api/inspections/{lots['HC-S03']['inspection_id']}/reports/pdf?lang=zh")
    text = "".join(page.extract_text() for page in PdfReader(BytesIO(report.content)).pages)
    assert "检验报告" in text and "咬边" in text
    english = client.get(f"/api/inspections/{lots['HC-S03']['inspection_id']}/reports/pdf").content
    assert "Undercut" in "".join(p.extract_text() for p in PdfReader(BytesIO(english)).pages)


def test_demo_loader_and_glossary_need_a_session(client):
    client.cookies.clear()
    assert client.post("/api/demo/shipyard").status_code in {401, 403}
    assert client.get("/api/glossary").status_code == 401


def test_hosted_demo_bootstrap_creates_account_and_loads_data(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from spec_agent.config import Settings
    from spec_agent.main import create_app

    monkeypatch.setenv("SPEC_AGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SPEC_AGENT_AUTOMATION", "false")
    monkeypatch.setenv("SPEC_AGENT_DEMO_USERNAME", "demo")
    monkeypatch.setenv("SPEC_AGENT_DEMO_PASSWORD", "hosted-demo-password-1")
    monkeypatch.setenv("SPEC_AGENT_LOAD_SHIPYARD_DEMO", "true")
    for _ in range(2):  # a restart must not fail on the existing account or data
        with TestClient(create_app(Settings.from_env())) as client:
            assert (
                client.post(
                    "/api/auth/login",
                    json={"username": "demo", "password": "hosted-demo-password-1"},
                    headers={"X-Spec-Agent": "1"},
                ).status_code
                == 200
            )
            lots = client.get("/api/inspections", headers={"X-Spec-Agent": "1"}).json()
            assert len([lot for lot in lots if lot["inputs"]["lot"] == "E21-B08"]) == 1


def test_demo_tasks_fill_the_board_and_link_to_records(client):
    from datetime import date

    client.post("/api/demo/shipyard")
    tasks = client.get("/api/tasks").json()
    today = date.today().isoformat()
    open_tasks = [task for task in tasks if task["status"] == "TODO"]
    assert any(task["due"] < today for task in open_tasks)  # overdue column
    assert any(task["due"] == today for task in open_tasks)  # today column
    assert any(task["due"] > today for task in open_tasks)  # this week / later
    assert sum(task["status"] == "DONE" for task in tasks) == 2
    reweld = next(task for task in tasks if task["title"]["en"].startswith("E21-B08"))
    lots = {i["inspection_id"]: i["inputs"]["lot"] for i in client.get("/api/inspections").json()}
    assert reweld["link"]["type"] == "inspection" and lots[reweld["link"]["id"]] == "E21-B08"
    assert any(task["link"] and task["link"]["type"] == "program" for task in tasks)
