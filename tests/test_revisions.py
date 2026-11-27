import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from conftest import sign_in
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pypdf import PdfReader

from spec_agent.config import LLMSettings, Settings
from spec_agent.main import create_app
from spec_agent.schemas.standard import Standard
from spec_agent.services.revision_agent import LLMError, RevisionAgent
from spec_agent.services.standards import demo_standard
from spec_agent.services.versioning import next_revision_label

FIXTURES = Path(__file__).parent / "fixtures"
DEMO = "demo-cover-en-v1"


def korean_standard() -> Standard:
    return Standard.model_validate(
        {**json.loads((FIXTURES / "korean_standard.json").read_text("utf-8")), "id": "kr"}
    )


def requests_text() -> dict:
    return json.loads((FIXTURES / "revision_requests.json").read_text("utf-8"))


def propose(client, standard_id, instruction="", file=None):
    response = client.post(
        f"/api/standards/{standard_id}/revision-proposals",
        data={"instruction": instruction},
        files={"file": file} if file else None,
    )
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, standard_id, proposal, **changes):
    body = {
        "operations": proposal["operations"],
        "summary": proposal["summary"],
        "instruction": proposal["instruction"],
        "revision": proposal["suggested_revision"],
        "interpreter": proposal["interpreter"],
        **(
            {
                "request_file": proposal["attachment"]["stored_name"],
                "request_filename": proposal["attachment"]["filename"],
            }
            if proposal.get("attachment")
            else {}
        ),
        **changes,
    }
    return client.post(f"/api/standards/{standard_id}/revisions", json=body)


# --- Offline interpreter ---------------------------------------------------------------


def test_rule_interpreter_handles_korean_requests():
    agent, cases = RevisionAgent(LLMSettings()), requests_text()
    standard = korean_standard()

    proposal = agent.propose(standard, cases["outer_burr"], {"C"})
    assert proposal["suggested_revision"] == "D"
    assert proposal["operations"] == [
        {
            "op": "update_condition",
            "rule_id": "burr-outer",
            "field": "height",
            "values": {"limit": "0.16", "operator": "lte"},
            "condition": None,
            "rule": None,
            "reason": cases["outer_burr"],
        }
    ]

    ambiguous = agent.propose(standard, cases["ambiguous_burr"], {"C"})
    assert ambiguous["operations"] == []
    assert ambiguous["notes"][0].startswith("Ambiguous item")

    broad = agent.propose(standard, cases["all_areas_burr"], {"C"})
    assert {op["rule_id"] for op in broad["operations"]} == {"burr-outer", "burr-inner"}
    assert any("burr-fastening" not in op["rule_id"] for op in broad["operations"])
    assert broad["notes"] and broad["notes"][0].startswith("Skipped")

    allowed = agent.propose(standard, cases["allow_fastening_pore"], {"C"})
    fields = [op["condition"]["field"] for op in allowed["operations"]]
    assert fields == ["depth", "diameter"]
    assert allowed["diff"]["rules"][0]["before"] == "Prohibited"

    warning = agent.propose(standard, cases["pore_warning_percent"], {"C"})
    margins = {op["field"]: op["values"]["warning_margin"] for op in warning["operations"]}
    assert margins == {"depth": "0.0125", "diameter": "0.03"}

    both = agent.propose(standard, cases["two_items"], {"C"})
    assert [(op["rule_id"], op["op"]) for op in both["operations"]] == [
        ("burr-outer", "update_condition"),
        ("crack-outer", "update_rule"),
    ]

    multi = agent.propose(standard, cases["numbered_request"], {"C"})
    assert multi["suggested_revision"] == "E"
    assert len(multi["operations"]) == 2
    assert multi["notes"] == []


def test_rule_interpreter_english_and_notes():
    agent = RevisionAgent(LLMSettings())
    proposal = agent.propose(
        demo_standard(),
        "Burr height from 0.20 to 0.15 mm or less; crack not applicable\nAdd a new gate check",
        {"1-en"},
    )
    assert proposal["suggested_revision"] == "2-en"
    assert [op["op"] for op in proposal["operations"]] == ["update_condition", "update_rule"]
    assert proposal["operations"][0]["values"] == {"limit": "0.15", "operator": "lte"}
    assert proposal["notes"] == [
        (
            "Adding a new inspection item requires the LLM interpreter or a structured JSON "
            'import: "Add a new gate check"'
        )
    ]
    unchanged = agent.propose(demo_standard(), "burr warning margin 5%", {"1-en"})
    assert unchanged["operations"] == []
    assert unchanged["notes"][0].startswith("Already matches")


def test_revision_labels():
    assert next_revision_label("C", {"C"}) == "D"
    assert next_revision_label("1-en", {"1-en", "2-en"}) == "3-en"
    assert next_revision_label("Rev.C", set()) == "Rev.D"
    assert next_revision_label("draft", set()) == "draft-2"


# --- LLM adapter ----------------------------------------------------------------------


class FakeClient:
    label = "llm:fake/test"

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, system, user):
        self.calls.append(user)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_llm_interpreter_validates_operations_and_falls_back():
    reply = json.dumps(
        {
            "summary": "Tighten burr height",
            "revision": "2-en",
            "operations": [
                {
                    "op": "update_condition",
                    "rule_id": "burr",
                    "field": "height",
                    "values": {"limit": "0.18"},
                    "reason": "request",
                },
                {
                    "op": "update_condition",
                    "rule_id": "missing",
                    "field": "x",
                    "values": {"limit": "1"},
                },
                {"op": "explode"},
            ],
            "questions": ["Should the warning margin change too?"],
        }
    )
    client = FakeClient("```json\n" + reply + "\n```")
    agent = RevisionAgent(
        LLMSettings(provider="openai", model="m", base_url="http://localhost:1/v1"), client
    )
    proposal = agent.propose(demo_standard(), "burr 0.18", {"1-en"})
    assert proposal["interpreter"] == "llm:fake/test"
    assert proposal["summary"] == "Tighten burr height"
    assert len(proposal["operations"]) == 1
    assert proposal["notes"][0] == "Should the warning margin change too?"
    assert any(note.startswith("Operation 2 was skipped") for note in proposal["notes"])
    assert any(note.startswith("Operation") for note in proposal["notes"][1:])

    failing = RevisionAgent(
        LLMSettings(provider="openai", model="m", base_url="http://localhost:1/v1"),
        FakeClient(LLMError("offline")),
    )
    fallback = failing.propose(demo_standard(), "burr height 0.18 mm", {"1-en"})
    assert fallback["interpreter"] == "rules"
    assert fallback["notes"][0].startswith("The LLM interpreter failed (offline)")
    assert fallback["operations"][0]["values"] == {"limit": "0.18"}


def test_internal_standards_are_not_sent_to_external_models():
    client = FakeClient("{}")
    agent = RevisionAgent(LLMSettings(provider="anthropic", model="m"), client)
    proposal = agent.propose(korean_standard(), requests_text()["outer_burr"], {"C"})
    assert client.calls == []
    assert proposal["notes"][0].startswith("Internal standards are not sent")
    local = RevisionAgent(
        LLMSettings(provider="openai", model="m", base_url="http://127.0.0.1:11434/v1"), client
    )
    local.propose(korean_standard(), requests_text()["outer_burr"], {"C"})
    assert len(client.calls) == 1
    with pytest.raises(ValueError):
        LLMSettings(provider="anthropic")


# --- API: versioning lifecycle -------------------------------------------------------


def test_revision_lifecycle(client, inspection_payload):
    # An inspection made on the original version keeps its snapshot.
    old = client.post("/api/inspections", json=inspection_payload).json()

    proposal = propose(client, DEMO, "Burr height 0.20 -> 0.18 mm")
    assert proposal["base_revision"] == "1-en"
    assert proposal["suggested_revision"] == "2-en"
    assert proposal["diff"]["rules"][0]["after"].startswith("Height < 0.18 mm")
    response = apply(client, DEMO, proposal)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["status"] == "DRAFT"
    assert draft["version"] == 2
    assert draft["previous_id"] == DEMO
    assert draft["family_id"] == DEMO
    assert draft["history"][-1]["kind"] == "REVISED"
    assert draft["history"][-1]["actor"] == "확인자"

    # Only one open draft per standard.
    assert apply(client, DEMO, proposal).status_code == 409

    # Chat refinement updates the open draft in place.
    refine = propose(client, draft["id"], "crack not applicable")
    assert refine["base_status"] == "DRAFT"
    assert refine["suggested_revision"] == "2-en"
    updated = apply(client, draft["id"], refine).json()
    assert updated["id"] == draft["id"]
    assert len(updated["history"]) == 2

    diff = client.get(f"/api/standards/{draft['id']}/diff").json()
    assert {entry["rule_id"] for entry in diff["rules"]} == {"burr", "crack"}

    # Drafts cannot be used until activated.
    payload = {**inspection_payload, "standard_id": draft["id"]}
    assert client.post("/api/inspections", json=payload).status_code == 409
    assert client.post(f"/api/standards/{draft['id']}/activate", json={}).status_code == 200

    versions = client.get(f"/api/standards/{DEMO}/versions").json()
    assert [(v["version"], v["status"]) for v in versions] == [(2, "ACTIVE"), (1, "SUPERSEDED")]
    assert versions[1]["inspection_count"] == 1
    assert versions[1]["superseded_by"] == draft["id"]
    assert client.post("/api/inspections", json=inspection_payload).status_code == 409
    assert propose_status(client, DEMO) == 409

    created = client.post("/api/inspections", json=payload).json()
    burr = next(item for item in created["item_results"] if item["rule_id"] == "burr")
    assert burr["auto_result"] == "FAIL"  # 0.195 mm now exceeds the revised 0.18 mm limit
    crack = next(item for item in created["item_results"] if item["rule_id"] == "crack")
    assert crack["auto_result"] == "N_A"
    kept = client.get(f"/api/inspections/{old['inspection_id']}").json()
    assert kept["standard"]["revision"] == "1-en"

    pdf = client.get(f"/api/standards/{draft['id']}/document/pdf")
    assert pdf.status_code == 200
    text = "".join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)
    assert "Change history" in text and "2-en" in text and "Height < 0.18 mm" in text
    book = load_workbook(BytesIO(client.get(f"/api/standards/{draft['id']}/document/xlsx").content))
    log = [[cell.value for cell in row] for row in book["Change history"].iter_rows()]
    assert [row[1] for row in log[1:]] == ["1-en", "2-en"]


def propose_status(client, standard_id):
    return client.post(
        f"/api/standards/{standard_id}/revision-proposals", data={"instruction": "burr 0.1 mm"}
    ).status_code


def test_request_attachments_and_discard(client):
    docx = BytesIO()
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Customer change notice</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Burr height limit 0.20 -&gt; 0.17 mm</w:t></w:r></w:p></w:body>"
            "</w:document>",
        )
    proposal = propose(
        client, DEMO, "", ("notice.docx", docx.getvalue(), "application/octet-stream")
    )
    assert proposal["attachment"]["filename"] == "notice.docx"
    assert "Burr height limit" in proposal["extracted_text"]
    assert proposal["operations"][0]["values"] == {"limit": "0.17"}
    assert any(note.startswith("No matching item") for note in proposal["notes"])
    draft = apply(client, DEMO, proposal).json()
    entry = draft["history"][-1]
    assert entry["request_filename"] == "notice.docx"
    stored = client.get(f"/api/requests/{entry['request_file']}")
    assert stored.status_code == 200 and stored.content == docx.getvalue()

    text = propose(client, draft["id"], "", ("mail.txt", b"crack: remove", "text/plain"))
    assert text["operations"][0]["op"] == "remove_rule"

    bad = client.post(
        f"/api/standards/{DEMO}/revision-proposals",
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
    )
    assert bad.status_code == 422
    empty = client.post(f"/api/standards/{DEMO}/revision-proposals", data={"instruction": " "})
    assert empty.status_code == 422

    assert client.delete(f"/api/standards/{DEMO}").status_code == 409
    assert client.delete(f"/api/standards/{draft['id']}").status_code == 200
    assert client.get(f"/api/standards/{DEMO}/versions").json()[0]["id"] == DEMO
    invalid = apply(
        client,
        DEMO,
        {
            "operations": [
                {
                    "op": "update_condition",
                    "rule_id": "burr",
                    "field": "height",
                    "values": {"limit": "-x"},
                }
            ],
            "summary": "",
            "instruction": "",
            "suggested_revision": "2-en",
            "interpreter": "rules",
        },
    )
    assert invalid.status_code == 422


def test_health_reports_llm_configuration(tmp_path):
    settings = Settings(
        tmp_path, llm=LLMSettings(provider="openai", model="m", base_url="http://localhost:1/v1")
    )
    with TestClient(create_app(settings)) as connection:
        health = connection.get("/api/health").json()
        assert health["external_ai"] is False
        assert health["llm"] == {"provider": "openai", "external": False, "allow_internal": False}
        connection.app.state.auth.create_user("tester", "Tester", "testing-password-1234")
        sign_in(connection)
        assert connection.get(f"/api/standards/{DEMO}/versions").status_code == 200


def test_rule_interpreter_understands_vietnamese():
    agent = RevisionAgent(LLMSettings())
    proposal = agent.propose(
        demo_standard(),
        "Chiều cao bavia từ 0.20 mm thành 0.18 mm và vết nứt không áp dụng",
        {"1-en"},
    )
    assert [(op["rule_id"], op["op"]) for op in proposal["operations"]] == [
        ("burr", "update_condition"),
        ("crack", "update_rule"),
    ]
    assert proposal["operations"][0]["values"] == {"limit": "0.18"}
    pore = agent.propose(demo_standard(), "rỗ khí độ sâu 0.25 mm nhỏ hơn hoặc bằng", {"1-en"})
    assert pore["operations"][0]["values"] == {"limit": "0.25"}
