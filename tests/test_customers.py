import json
from pathlib import Path

from conftest import sign_in
from fastapi.testclient import TestClient
from test_workflow import create

from spec_agent.config import LLMSettings, Settings
from spec_agent.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
COMPLAINT = b"""From: Mr. Tran <tran@demo.example>
To: qa@example.com
Subject: [URGENT] Burr complaint on DEMO-COVER lot LOT-2026-077
Date: Tue, 20 Oct 2026 09:00:00 +0700
Content-Type: text/plain; charset=utf-8

Dear QA team,
We found burr on the outer surface of DEMO-COVER parts from lot LOT-2026-077.
The burr height was about 0.25 mm.
Please investigate and send us your countermeasure by Friday.

Best regards
Tran
> previous quoted mail that must be ignored: crack crack crack
"""
FAILING = {
    "burr": {"measurements": {"height": "0.25"}, "units": {"height": "mm"}},
    "pore": {
        "measurements": {"depth": "0.20", "diameter": "0.50"},
        "units": {"depth": "mm", "diameter": "mm"},
    },
    "crack": {"defect_present": False},
}


def post_request(client, files=None, **fields):
    response = client.post(
        "/api/customer-requests",
        data=fields,
        files=[("files", file) for file in files or []],
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_complaint_mail_is_linked_and_drafted(client):
    customer = client.post(
        "/api/customers",
        json={"name": "DEMO CUSTOMER", "code": "DC", "country": "CN", "reply_language": "zh"},
    )
    assert customer.status_code == 201
    assert client.post("/api/customers", json={"name": "demo customer"}).status_code == 422
    inspection = create(
        client,
        {
            "standard_id": "demo-cover-en-v1",
            "lot": "LOT-2026-077",
            "inspector": "An",
            "observations": FAILING,
        },
    )
    request = post_request(client, [("complaint.eml", COMPLAINT, "message/rfc822")])
    assert request["subject"].startswith("[URGENT] Burr complaint")
    assert "tran@demo.example" in request["sender"]
    assert request["received_at"].startswith("2026-10-20")
    assert request["customer"] == "DEMO CUSTOMER"
    assert request["part_numbers"] == ["DEMO-COVER"]
    assert (request["category"], request["priority"]) == ("COMPLAINT", "HIGH")
    assert request["response_language"] == "zh"  # the customer's reply language
    analysis = request["analysis"]
    assert analysis["related_inspections"][0]["id"] == inspection["inspection_id"]
    assert analysis["mentions"]["items"][0]["rule_id"] == "burr"
    assert "crack" not in json.dumps(analysis["mentions"])  # quoted mail is ignored
    finding = analysis["findings"][0]
    assert finding["code"] == "inspection_fail" and finding["params"]["lot"] == "LOT-2026-077"
    assert "LOT-2026-077" in finding["text"]
    assert analysis["key_points"][0].startswith("We found burr")  # greeting skipped
    kinds = [action["kind"] for action in analysis["proposed_actions"]]
    assert kinds == ["review_inspection", "reinspect"]
    reinspect = analysis["proposed_actions"][1]
    assert reinspect["template"] == "Re-inspect retained samples of lot {lot} for: {items}"
    assert reinspect["params"] == {"lot": "LOT-2026-077", "items": "Outer surface / Burr height"}
    draft = request["response_draft"]
    assert draft.startswith("DEMO CUSTOMER 团队您好")
    assert "Height < 0.20 mm" in draft and "LOT-2026-077" in draft

    english = client.post(
        f"/api/customer-requests/{request['id']}/analyze", json={"response_language": "en"}
    ).json()
    assert english["response_draft"].startswith("Dear DEMO CUSTOMER team,")
    assert [event["kind"] for event in english["events"]] == ["RECEIVED", "ANALYZED", "ANALYZED"]


def test_spec_change_request_proposes_a_revision(client):
    standard = json.loads((FIXTURES / "korean_standard.json").read_text("utf-8"))
    registered = client.post("/api/standards", json=standard).json()
    assert client.post(f"/api/standards/{registered['id']}/activate", json={}).status_code == 200
    request = post_request(
        client,
        customer="masked customer",
        body="MASKED-PART-B 외측면 파팅면 버 높이 기준을 0.15 mm로 변경 요청드립니다.",
        channel="DOCUMENT",
        response_language="ko",
    )
    assert request["customer"] == "MASKED CUSTOMER"
    assert request["category"] == "SPEC_CHANGE"
    analysis = request["analysis"]
    assert [item["rule_id"] for item in analysis["mentions"]["items"]] == ["burr-outer"]
    assert any(finding["params"].get("value") == "0.15" for finding in analysis["findings"])
    action = analysis["proposed_actions"][0]
    assert action["kind"] == "open_revision" and action["target_id"] == registered["id"]
    proposal = client.post(
        f"/api/standards/{action['target_id']}/revision-proposals",
        data={"instruction": action["instruction"]},
    ).json()
    assert proposal["operations"][0]["values"]["limit"] == "0.15"
    assert request["response_draft"].startswith("MASKED CUSTOMER 담당자님께")


def test_status_notes_response_and_export(client):
    request = post_request(
        client,
        subject="Inquiry",
        body="Could you please confirm the burr height limit for DEMO-COVER?",
        sender="buyer@demo.example",
    )
    identifier = request["id"]
    assert request["category"] == "INQUIRY"
    assert request["customer"] == "DEMO CUSTOMER"
    updated = client.patch(
        f"/api/customer-requests/{identifier}",
        json={"status": "IN_PROGRESS", "priority": "HIGH", "note": "Checking with QA lead"},
    ).json()
    assert updated["events"][-1]["changes"]["status"] == {"before": "OPEN", "after": "IN_PROGRESS"}
    client.post(f"/api/customer-requests/{identifier}/notes", json={"note": "Called customer"})
    responded = client.post(
        f"/api/customer-requests/{identifier}/respond",
        json={"final_response": "The current limit is Height < 0.20 mm."},
    ).json()
    assert responded["status"] == "RESPONDED"
    assert [event["kind"] for event in responded["events"]][-3:] == ["UPDATED", "NOTE", "RESPONDED"]
    mail = client.get(f"/api/customer-requests/{identifier}/response.eml")
    assert mail.status_code == 200
    assert "To: buyer@demo.example" in mail.text and "Subject: Re: Inquiry" in mail.text
    closed = client.patch(f"/api/customer-requests/{identifier}", json={"status": "CLOSED"}).json()
    assert closed["events"][-1]["kind"] == "CLOSED"
    rejected = client.post(f"/api/customer-requests/{identifier}/respond", json={})
    assert rejected.status_code == 422
    listed = client.get("/api/customer-requests", params={"status": "CLOSED"}).json()
    assert [item["id"] for item in listed] == [identifier]


def test_customer_overview_and_history(client):
    overview = client.get("/api/customers").json()
    assert overview[0]["name"] == "DEMO CUSTOMER" and overview[0]["registered"] is False
    create(
        client,
        {
            "standard_id": "demo-cover-en-v1",
            "lot": "LOT-A",
            "inspector": "An",
            "observations": FAILING,
        },
    )
    post_request(client, customer="DEMO CUSTOMER", subject="Delivery", body="Delivery date?")
    post_request(
        client,
        customer="DEMO CUSTOMER",
        part_numbers="OTHER-PART",
        subject="Other part question",
        body="Question about another part",
    )
    detail = client.get("/api/customers/detail", params={"name": "demo customer"}).json()
    assert {part["part_number"] for part in detail["parts"]} == {"DEMO-COVER", "OTHER-PART"}
    cover = next(part for part in detail["parts"] if part["part_number"] == "DEMO-COVER")
    assert cover["standard"]["revision"] == "1-en" and cover["last_result"] == "FAIL"
    assert detail["open_requests"] == 2 and detail["failed_inspections"] == 1
    kinds = {entry["kind"] for entry in detail["timeline"]}
    assert {"INSPECTION", "REQUEST_RECEIVED"} <= kinds
    filtered = client.get(
        "/api/customers/detail", params={"name": "DEMO CUSTOMER", "part": "OTHER-PART"}
    ).json()
    assert [item["subject"] for item in filtered["request_list"]] == ["Other part question"]
    assert filtered["inspection_list"] == []
    assert client.get("/api/customers/detail", params={"name": "nobody"}).status_code == 404

    record = client.post("/api/customers", json={"name": "DEMO CUSTOMER"}).json()
    renamed = client.put(f"/api/customers/{record['id']}", json={"name": "Other"})
    assert renamed.status_code == 422
    saved = client.put(
        f"/api/customers/{record['id']}",
        json={"name": "DEMO CUSTOMER", "contacts": [{"name": "Lan", "email": "lan@demo.example"}]},
    ).json()
    assert saved["contacts"][0]["name"] == "Lan"


def test_attachments_are_validated_and_scoped(client):
    request = post_request(
        client,
        [
            ("photo.png", b"\x89PNG\r\n fake", "image/png"),
            ("note.txt", b"burr found", "text/plain"),
        ],
        customer="DEMO CUSTOMER",
    )
    assert [item["filename"] for item in request["attachments"]] == ["photo.png", "note.txt"]
    assert "[note.txt]" in request["body"]
    stored = request["attachments"][0]["stored_name"]
    response = client.get(f"/api/customer-requests/{request['id']}/attachments/{stored}")
    assert response.status_code == 200 and response.content.startswith(b"\x89PNG")
    other = post_request(client, subject="Other", body="Other")
    assert (
        client.get(f"/api/customer-requests/{other['id']}/attachments/{stored}").status_code == 404
    )
    bad = client.post(
        "/api/customer-requests", files=[("files", ("run.exe", b"MZ", "application/octet-stream"))]
    )
    assert bad.status_code == 422
    assert client.post("/api/customer-requests", data={"body": " "}).status_code == 422


class FakeClient:
    label = "llm:fake/test"

    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        return json.dumps(
            {
                "summary": "Customer reports burr on lot LOT-2026-077",
                "category": "COMPLAINT",
                "priority": "HIGH",
                "key_points": ["Burr 0.25 mm on outer surface"],
                "response_draft": "Dear team, we are investigating.",
            }
        )


def test_llm_refinement_and_data_guard(tmp_path):
    fake = FakeClient()
    local = Settings(
        tmp_path / "local",
        llm=LLMSettings(provider="openai", model="m", base_url="http://localhost:1/v1"),
    )
    with TestClient(create_app(local, fake)) as connection:
        connection.app.state.auth.create_user("tester", "Tester", "testing-password-1234")
        sign_in(connection)
        request = post_request(connection, [("complaint.eml", COMPLAINT, "message/rfc822")])
        assert request["analysis"]["interpreter"] == "llm:fake/test"
        assert request["response_draft"] == "Dear team, we are investigating."
        assert request["analysis"]["key_points"] == ["Burr 0.25 mm on outer surface"]
        assert request["part_numbers"] == ["DEMO-COVER"]  # links still come from our records
    external = Settings(tmp_path / "external", llm=LLMSettings(provider="anthropic", model="m"))
    guarded = FakeClient()
    with TestClient(create_app(external, guarded)) as connection:
        connection.app.state.auth.create_user("tester", "Tester", "testing-password-1234")
        sign_in(connection)
        request = post_request(connection, [("complaint.eml", COMPLAINT, "message/rfc822")])
        assert guarded.calls == 0
        assert request["analysis"]["interpreter"] == "rules"
        assert request["analysis"]["notes"][0].startswith("Customer requests are not sent")


def test_demo_seed_is_idempotent_and_multilingual(client):
    from spec_agent.services.demo_data import seed_demo

    workflow, desk = client.app.state.workflow, client.app.state.desk
    assert seed_demo(workflow, desk) == {"customers": 1, "inspections": 2, "requests": 3}
    assert seed_demo(workflow, desk) == {"customers": 0, "inspections": 0, "requests": 0}
    requests = {item["channel"]: item for item in client.get("/api/customer-requests").json()}
    vietnamese = client.get(f"/api/customer-requests/{requests['DOCUMENT']['id']}").json()
    assert vietnamese["category"] == "SPEC_CHANGE"
    assert vietnamese["analysis"]["proposed_actions"][0]["kind"] == "open_revision"
    assert vietnamese["analysis"]["findings"][0]["params"]["value"] == "0.18"
    korean = client.get(f"/api/customer-requests/{requests['PHONE']['id']}").json()
    assert korean["category"] == "REPORT_REQUEST" and korean["response_language"] == "ko"
    assert korean["analysis"]["related_inspections"][0]["lot"] == "LOT-2026-076"
