"""Watched work folders: files dropped in produce drafts, reports and tasks."""

import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import sign_in
from fastapi.testclient import TestClient

from spec_agent.config import Settings
from spec_agent.main import create_app
from spec_agent.services.automation import FOLDERS

DOCS = Path(__file__).resolve().parents[1] / "docs"

MAIL = b"""From: Quality Team <quality@amx.example>
To: supplier@example.com
Subject: Urgent - burr height on Top Cover Assy
Date: Wed, 21 Oct 2026 09:00:00 +0900
Content-Type: text/plain; charset=utf-8

Please check the parting line burr limit for Top Cover Assy urgently and send the report.
"""


def sample(pattern: str) -> Path:
    matches = sorted(DOCS.glob(pattern))
    if not matches:
        pytest.skip(f"{pattern} is not available")
    return matches[0]


@pytest.fixture
def auto(tmp_path):
    settings = Settings(
        tmp_path / "data", workspace_dir=tmp_path / "workspace", automation_interval=0
    )
    with TestClient(create_app(settings)) as client:
        client.app.state.auth.create_user("tester", "Tester", "testing-password-1234")
        sign_in(client)
        client.app.state.automation.prepare()
        yield client, tmp_path / "workspace"


def drop(
    workspace: Path, folder: str, source: Path | None = None, name: str = "", content: bytes = b""
):
    target = workspace / FOLDERS[folder] / (name or source.name)
    if source is not None:
        shutil.copy(source, target)
    else:
        target.write_bytes(content)
    return target


def titles(tasks, language="en"):
    return [task["title"][language] for task in tasks]


def test_folders_are_created_with_a_readme(auto):
    _, workspace = auto
    for key in ("customer_forms", "ncr", "requests", "reports", "done", "failed"):
        assert (workspace / FOLDERS[key]).is_dir()
    assert (workspace / "README.txt").read_text("utf-8").startswith("이 폴더에")


def test_files_wait_until_they_stop_changing(auto):
    client, workspace = auto
    drop(workspace, "ncr", sample("*NCR*.pdf"))
    automation = client.app.state.automation
    assert automation.scan() == []  # first sighting only
    jobs = automation.scan()
    assert len(jobs) == 1 and jobs[0]["kind"] == "ncr"


def test_dropped_files_become_drafts_reports_and_tasks(auto):
    client, workspace = auto
    drop(workspace, "customer_forms", sample("*모델B*.pdf"))
    drop(workspace, "customer_forms", sample("*모델A*.pdf"))
    drop(workspace, "ncr", sample("*NCR*.pdf"))
    drop(workspace, "requests", name="burr.eml", content=MAIL)
    drop(workspace, "requests", name="notes.zip", content=b"PK")

    jobs = client.post("/api/automation/scan").json()
    by_file = {job["filename"]: job for job in jobs}
    assert len(jobs) == 5

    # Customer form (criteria table): a draft standard, never activated automatically.
    form = next(
        job
        for name, job in by_file.items()
        if "B" in name and name.endswith(".pdf") and job["kind"] == "customer_forms"
    )
    assert form["status"] == "NEEDS_REVIEW"
    standards = [
        s for s in client.get("/api/standards").json() if s["document_number"] == "QA-STD 05.10.312"
    ]
    assert len(standards) == 1 and standards[0]["status"] == "DRAFT"
    assert any("QA-STD 05.10.312" in title for title in titles(form["tasks"]))

    # Limit approval: waits for a person to pick the customer.
    limits = next(
        job for name, job in by_file.items() if "A" in name and job["kind"] == "customer_forms"
    )
    assert limits["status"] == "NEEDS_REVIEW"
    assert any(
        title.startswith("Decide samples on hold: No. 1, 6") for title in titles(limits["tasks"])
    )

    # NCR: root-cause analysis with requested actions scheduled by urgency.
    ncr = next(job for job in jobs if job["kind"] == "ncr")
    assert ncr["status"] == "DONE" and ncr["link"]["type"] == "trace"
    assert "1. CNC 가공" in ncr["read"][2]["ko"]
    due = {task["title"]["en"]: task["due"] for task in ncr["tasks"]}
    today = date.today()
    assert due["NCR action: Containment Action"] == (today + timedelta(days=1)).isoformat()
    assert due["NCR action: Please make 8D's Report"] == (today + timedelta(days=14)).isoformat()
    assert "Verify root-cause candidate 1: CNC machining" in due

    # Mail: analysed request with a reply task due soon because it is urgent.
    mail = by_file["burr.eml"]
    request = client.get(f"/api/customer-requests/{mail['link']['id']}").json()
    assert request["priority"] == "HIGH"
    reply = next(task for task in mail["tasks"] if task["title"]["en"].startswith("Reply to"))
    assert reply["due"] == (today + timedelta(days=1)).isoformat()

    # Unsupported file: moved aside with a task to check it.
    failed = by_file["notes.zip"]
    assert failed["status"] == "ERROR" and (workspace / FOLDERS["failed"] / "notes.zip").exists()

    # Originals moved, one PDF report per file in the workspace and through the API.
    assert not any(
        list((workspace / FOLDERS[key]).iterdir()) for key in ("customer_forms", "ncr", "requests")
    )
    assert len(list((workspace / FOLDERS["reports"]).glob("*.pdf"))) == 5
    report = client.get(f"/api/automation/jobs/{ncr['id']}/report")
    assert report.status_code == 200 and report.content.startswith(b"%PDF-")

    status = client.get("/api/automation").json()
    assert status["enabled"] and len(status["jobs"]) == 5

    # Tasks: calendar data, manual tasks, completion.
    tasks = client.get("/api/tasks").json()
    assert len(tasks) >= 12
    assert [task["due"] for task in tasks] == sorted(task["due"] for task in tasks)
    added = client.post("/api/tasks", json={"title": "Customer audit", "due": "2026-11-03"}).json()
    assert added["title"]["ko"] == "Customer audit" and added["source"] == "manual"
    done = client.patch(
        f"/api/tasks/{added['id']}", json={"status": "DONE", "due": "2026-11-04"}
    ).json()
    assert done["status"] == "DONE" and done["due"] == "2026-11-04" and done["done_at"]
    assert client.patch(f"/api/tasks/{added['id']}", json={"status": "LATER"}).status_code == 422
    assert client.delete(f"/api/tasks/{added['id']}").status_code == 200
    assert client.delete(f"/api/tasks/{added['id']}").status_code == 404


def test_automation_can_be_turned_off(tmp_path):
    with TestClient(create_app(Settings(tmp_path))) as client:
        client.app.state.auth.create_user("tester", "Tester", "testing-password-1234")
        sign_in(client)
        assert client.get("/api/automation").json()["enabled"] is False
        assert client.post("/api/automation/scan").json() == []


def test_support_notice_in_folder_is_judged_and_scheduled(auto):
    from spec_agent.services.support_programs import SAMPLES

    client, root = auto
    for path in sorted(SAMPLES.glob("*.txt")):
        shutil.copy(path, root / FOLDERS["programs"] / path.name)
    jobs = {job["filename"]: job for job in client.post("/api/automation/scan").json()}
    assert jobs["01_shipyard_crisis_area.txt"]["status"] == "DONE"
    assert len(jobs["01_shipyard_crisis_area.txt"]["tasks"]) == 6  # five documents + submission
    assert jobs["02_busan_port_logistics.txt"]["tasks"] == []  # not eligible: nothing scheduled
    assert jobs["02_busan_port_logistics.txt"]["warnings"]
    assert jobs["03_class_approval.txt"]["status"] == "NEEDS_REVIEW"
    assert (root / FOLDERS["done"] / "01_shipyard_crisis_area.txt").exists()
    assert len(client.get("/api/programs").json()) == 3
