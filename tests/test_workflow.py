from io import BytesIO
from uuid import uuid4

from conftest import TEST_PASSWORD, sign_in
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pypdf import PdfReader

from spec_agent.config import Settings
from spec_agent.main import create_app


def action_payload(state, action, **changes):
    return {
        "request_id": str(uuid4()),
        "expected_revision": state["revision"],
        "interrupt_id": state["pending"]["id"],
        "action": action,
        "decisions": {"burr": {"result": "PASS", "reason": "개발용 경고 확인"}},
        **changes,
    }


def submit(client, state, action, **changes):
    response = client.post(
        f"/api/inspections/{state['inspection_id']}/actions",
        json=action_payload(state, action, **changes),
    )
    assert response.status_code == 200, response.text
    return response.json()


def create(client, payload):
    response = client.post("/api/inspections", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_full_flow_and_exports(client, inspection_payload):
    state = create(client, inspection_payload)
    assert state["status"] == "AWAITING_REVIEW"
    assert state["report"] is None
    assert client.get(f"/api/inspections/{state['inspection_id']}/reports/pdf").status_code == 409
    state = submit(client, state, "confirm")
    assert state["status"] == "AWAITING_REPORT_APPROVAL"
    assert not state["report"]["approved"]
    pdf = client.get(f"/api/inspections/{state['inspection_id']}/reports/pdf")
    assert pdf.status_code == 200
    assert "Unapproved draft" in "".join(
        page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages
    )
    state = submit(client, state, "approve", decisions={})
    assert state["status"] == "APPROVED"
    assert state["overall_result"] == "PASS"
    assert len(state["events"]) == 2
    excel = client.get(f"/api/inspections/{state['inspection_id']}/reports/xlsx")
    book = load_workbook(BytesIO(excel.content))
    sheet = book.active
    assert sheet["B3"].value == "Approved"
    assert sheet["B9"].value == "PASS"
    header = next(cell for row in sheet for cell in row if cell.value == "Area / Item")
    assert sheet.auto_filter.ref.startswith(f"A{header.row}:")
    assert sheet.freeze_panes == f"C{header.row + 1}"
    pdf = client.get(f"/api/inspections/{state['inspection_id']}/reports/pdf")
    text = "".join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)
    for value in [state["inspection_id"], "TEST-001", "Approved", "PASS"]:
        assert value in text


def test_missing_correction_and_rejection(client, inspection_payload):
    original = inspection_payload["observations"]
    state = create(client, {**inspection_payload, "observations": {}})
    assert state["status"] == "NEEDS_INPUT"
    state = submit(client, state, "correct", observations=original, decisions={})
    assert state["input_revision"] == 2
    state = submit(client, state, "reject", reason="측정값 재확인", decisions={})
    assert state["status"] == "NEEDS_INPUT"
    assert state["report"] is None
    state = submit(client, state, "correct", observations=original, decisions={})
    state = submit(client, state, "confirm")
    old_report = state["report"]["revision"]
    state = submit(client, state, "reject", reason="로트 확인", decisions={})
    assert client.get(f"/api/inspections/{state['inspection_id']}/reports/pdf").status_code == 409
    state = submit(client, state, "correct", observations=original, decisions={})
    state = submit(client, state, "confirm")
    assert state["report"]["revision"] != old_report
    assert len(state["report_history"]) == 2


def test_warning_requires_explicit_review(client, inspection_payload):
    state = create(client, inspection_payload)
    response = client.post(
        f"/api/inspections/{state['inspection_id']}/actions",
        json=action_payload(state, "confirm", decisions={}),
    )
    assert response.status_code == 422
    assert "warning" in response.text


def test_fail_can_be_approved_but_not_overridden(client, inspection_payload):
    inspection_payload["observations"]["burr"]["measurements"]["height"] = "0.20"
    state = create(client, inspection_payload)
    response = client.post(
        f"/api/inspections/{state['inspection_id']}/actions", json=action_payload(state, "confirm")
    )
    assert response.status_code == 422
    state = submit(
        client, state, "confirm", decisions={"burr": {"result": "FAIL", "reason": "경계 위반 확인"}}
    )
    state = submit(client, state, "approve", decisions={})
    assert state["overall_result"] == "FAIL"
    assert state["status"] == "APPROVED"


def test_duplicate_and_stale_requests(client, inspection_payload):
    state = create(client, inspection_payload)
    payload = action_payload(state, "confirm")
    endpoint = f"/api/inspections/{state['inspection_id']}/actions"
    first = client.post(endpoint, json=payload)
    second = client.post(endpoint, json=payload)
    assert first.status_code == second.status_code == 200
    assert len(second.json()["events"]) == 1
    assert client.post(endpoint, json={**payload, "reason": "다른 내용"}).status_code == 409
    assert client.post(endpoint, json={**payload, "request_id": str(uuid4())}).status_code == 409


def test_restart_and_inspection_isolation(tmp_path, inspection_payload):
    settings = Settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.app.state.auth.create_user("tester", "확인자", TEST_PASSWORD)
        sign_in(client)
        first = create(client, inspection_payload)
        second = create(client, {**inspection_payload, "lot": "SECOND"})
        identifier = first["inspection_id"]
    with TestClient(create_app(settings)) as client:
        sign_in(client)
        restored = client.get(f"/api/inspections/{identifier}").json()
        assert restored["pending"]["id"] == first["pending"]["id"]
        first = submit(client, restored, "confirm")
        assert (
            client.get(f"/api/inspections/{second['inspection_id']}").json()["status"]
            == "AWAITING_REVIEW"
        )
    with TestClient(create_app(settings)) as client:
        sign_in(client)
        restored = client.get(f"/api/inspections/{identifier}").json()
        assert submit(client, restored, "approve", decisions={})["status"] == "APPROVED"


def test_formula_injection_written_as_text(client, inspection_payload):
    state = create(client, {**inspection_payload, "lot": '=HYPERLINK("https://example.invalid")'})
    state = submit(client, state, "confirm")
    workbook = load_workbook(
        BytesIO(client.get(f"/api/inspections/{state['inspection_id']}/reports/xlsx").content)
    )
    assert all(cell.data_type != "f" for row in workbook.active for cell in row)


def test_cancel_and_no_applicable_items(client, inspection_payload):
    state = create(client, {**inspection_payload, "observations": {}})
    state = submit(client, state, "cancel", decisions={}, reason="검사 대상 변경")
    assert state["status"] == "CANCELLED"
    assert state["pending"] is None


def test_render_failure_is_recoverable(client, inspection_payload, monkeypatch):
    from spec_agent.reports import rendering

    original = rendering.render_excel

    def fail(*args):
        raise RuntimeError("simulated disk failure")

    state = create(client, inspection_payload)
    monkeypatch.setattr(rendering, "render_excel", fail)
    try:
        client.post(
            f"/api/inspections/{state['inspection_id']}/actions",
            json=action_payload(state, "confirm"),
        )
    except RuntimeError:
        pass
    state = client.get(f"/api/inspections/{state['inspection_id']}").json()
    assert state["status"] == "ERROR"
    assert state["report"] is None
    monkeypatch.setattr(rendering, "render_excel", original)
    response = client.post(f"/api/inspections/{state['inspection_id']}/retry")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "AWAITING_REPORT_APPROVAL"
    assert len(response.json()["events"]) == 1


def test_file_integrity(client, inspection_payload):
    state = submit(client, create(client, inspection_payload), "confirm")
    path = client.app.state.workflow.report_path(state["inspection_id"], "pdf")
    path.write_bytes(b"modified report")
    response = client.get(f"/api/inspections/{state['inspection_id']}/reports/pdf")
    assert response.status_code == 409
    response = client.post(
        f"/api/inspections/{state['inspection_id']}/actions",
        json=action_payload(state, "approve", decisions={}),
    )
    assert response.status_code == 409


def test_long_review_reason_survives_both_formats(client, inspection_payload):
    state = create(client, inspection_payload)
    reason = "확인 근거입니다. " * 90
    state = submit(
        client, state, "confirm", decisions={"burr": {"result": "PASS", "reason": reason}}
    )
    pdf = client.get(f"/api/inspections/{state['inspection_id']}/reports/pdf")
    assert pdf.status_code == 200
    assert len(PdfReader(BytesIO(pdf.content)).pages) >= 2
    excel = client.get(f"/api/inspections/{state['inspection_id']}/reports/xlsx")
    sheet = load_workbook(BytesIO(excel.content)).active
    text = "".join(str(cell.value or "") for row in sheet for cell in row)
    assert text.count("확인 근거입니다.") == 90


def test_initial_execution_failure_can_resume(client, inspection_payload, monkeypatch):
    workflow = client.app.state.workflow
    original = workflow.invoke

    def fail(*args):
        raise RuntimeError("stopped before checkpoint")

    monkeypatch.setattr(workflow, "invoke", fail)
    try:
        client.post("/api/inspections", json=inspection_payload)
    except RuntimeError:
        pass
    identifier = client.get("/api/inspections").json()[0]["inspection_id"]
    assert client.get(f"/api/inspections/{identifier}").json()["status"] == "ERROR"
    monkeypatch.setattr(workflow, "invoke", original)
    response = client.post(f"/api/inspections/{identifier}/retry")
    assert response.status_code == 200
    assert response.json()["status"] == "AWAITING_REVIEW"


def test_external_tracing_disabled(client, monkeypatch):
    from langsmith.run_helpers import get_tracing_context

    workflow = client.app.state.workflow

    def capture(*args):
        assert get_tracing_context()["enabled"] is False
        return "local"

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(workflow.graph, "invoke", capture)
    assert workflow.invoke({}, "test-thread") == "local"


def test_same_request_concurrent_submission(client, inspection_payload):
    from concurrent.futures import ThreadPoolExecutor

    state = create(client, inspection_payload)
    payload = action_payload(state, "confirm")
    endpoint = f"/api/inspections/{state['inspection_id']}/actions"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(endpoint, json=payload), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert len(client.get(f"/api/inspections/{state['inspection_id']}").json()["events"]) == 1
