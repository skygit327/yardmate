"""Customer form intake: the three supplied samples (models A, B and C)."""

import io
from pathlib import Path

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from spec_agent.services.form_reader import PARSER, read_form

DOCS = Path(__file__).resolve().parents[1] / "docs"


def sample(model: str) -> Path:
    matches = sorted(DOCS.glob(f"*모델{model}*.pdf"))
    if not matches:
        pytest.skip(f"Sample form for model {model} is not available")
    return matches[0]


def upload(client, model: str, provenance: str = "SYNTHETIC"):
    path = sample(model)
    response = client.post(
        "/api/forms",
        files={"file": (path.name, path.read_bytes(), "application/pdf")},
        data={"provenance": provenance},
    )
    assert response.status_code == 201, response.text
    return response.json()


def codes(reading):
    return [warning["code"] for warning in reading["warnings"]]


def test_matrix_form_is_read_into_rules():
    reading = read_form("b.pdf", sample("B").read_bytes()).to_dict()
    assert reading["document_type"] == "INSPECTION_MATRIX"
    assert reading["header"]["customer"] == "DEMO-OEM"
    assert reading["header"]["document_number"] == "QA-STD 05.10.312"
    assert reading["header"]["part_name"] == "Top Cover Assy"
    layout = reading["layout"]
    assert [group["name"] for group in layout["groups"]] == ["외측면", "내측면"]
    assert layout["areas"] == ["외측면", "스크류 체결부 상면", "내측면", "체결면(적색면)"]
    assert len(layout["rows"]) == 21
    rules = {rule["id"]: rule for rule in reading["rules"]}
    pore = next(r for r in reading["rules"] if r["item"] == "표면 기공" and r["area"] == "외측면")
    assert pore["kind"] == "NUMERIC" and pore["confidence"] == "high"
    assert [(c["field"], c["operator"], c["limit"]) for c in pore["conditions"]] == [
        ("depth", "lt", "0.25"),
        ("diameter", "lt", "0.6"),
    ]
    assert rules["burr-a4"]["kind"] == "PROHIBITED"
    assert rules["gate-a2"]["kind"] == "NOT_APPLICABLE"
    roughness = next(
        r for r in reading["rules"] if r["item"] == "제품 표면 거칠기" and r["area"] == "외측면"
    )
    assert roughness["conditions"][0] == {
        "field": "roughness",
        "label": "거칠기",
        "operator": "lt",
        "limit": "6.3",
        "unit": "µm",
    }
    trims = [r for r in reading["rules"] if r["id"].startswith("trim-hole-a1")]
    assert [r["item"] for r in trims] == [
        "트림부 보이드(Trim hole) (장공형)",
        "트림부 보이드(Trim hole) (원형)",
    ]
    sink = next(
        r for r in reading["rules"] if r["item"].startswith("표면 수축") and r["area"] == "외측면"
    )
    assert sink["confidence"] == "medium"
    assert {"code": "RELATIVE_LIMIT", "params": {"percent": "8"}} in sink["notes"]
    assert sum(r["confidence"] == "low" for r in reading["rules"]) == 1
    assert reading["conditions"] == {
        "distance_cm": ["35.56", "66.04"],
        "distance_inch": ["14", "26"],
        "illuminance_lux": "2368.06",
        "customer_standard": "QS-3172",
        "class": "3",
        "method": "육안",
        "frequency": "100%",
    }
    assert "COLOR_MISMATCH" not in codes(reading)


def test_model_c_drawing_colour_conflict_is_flagged():
    reading = read_form("c.pdf", sample("C").read_bytes()).to_dict()
    mismatch = next(w for w in reading["warnings"] if w["code"] == "COLOR_MISMATCH")
    assert mismatch["params"] == {"area": "체결면(적색면)", "table": "적색", "drawing": "황색"}


def test_limit_approval_pack_is_read_per_sample():
    reading = read_form("a.pdf", sample("A").read_bytes()).to_dict()
    assert reading["document_type"] == "LIMIT_APPROVAL"
    samples = {s["no"]: s for s in reading["samples"]}
    assert len(samples) == 25
    decisions = [s["decision"] for s in samples.values()]
    assert (decisions.count("OK"), decisions.count("NG"), decisions.count("HOLD")) == (10, 13, 2)
    first = samples[1]
    assert (first["defect"], first["defect_en"], first["detect_process"]) == (
        "오염",
        "Dirt",
        "Chromate",
    )
    assert first["decision"] == "HOLD" and first["defect_rate"] == "6.9%"
    assert first["cause"].startswith("크로메이트 지그 불량")
    assert samples[16]["defect_en"] == "Polished surface scratch"
    assert samples[11]["decision"] == "NG" and samples[11]["decision_reason"] == "조립 시 간섭"
    assert {"level": "warning", "code": "LIMIT_HOLD", "params": {"numbers": [1, 6]}} in reading[
        "warnings"
    ]


@pytest.mark.parametrize(
    ("text", "kind", "conditions"),
    [
        ("-", "NOT_APPLICABLE", []),
        ("불허: 발생 시 불합격", "PROHIBITED", []),
        ("버 높이 < 0.18 mm", "NUMERIC", [("height", "lt", "0.18", "mm")]),
        ("허용: 깊이 ≤ 0.1 mm", "NUMERIC", [("depth", "lte", "0.1", "mm")]),
        (
            "Height < 0.2 mm, Width < 1 mm",
            "NUMERIC",
            [("height", "lt", "0.2", "mm"), ("width", "lt", "1", "mm")],
        ),
        ("< 3.0 um", "NUMERIC", [("value", "lt", "3.0", "µm")]),
    ],
)
def test_criterion_wording(text, kind, conditions):
    (criterion,) = PARSER.parse(text, "item")
    assert criterion.kind == kind
    assert [
        (c["field"], c["operator"], c["limit"], c["unit"]) for c in criterion.conditions
    ] == conditions


def test_form_round_trip_through_the_api(client):
    reading = upload(client, "B")
    draft = reading["draft"]
    assert draft["customer"] == "DEMO-OEM" and draft["part_number"] == "Top Cover Assy"
    assert len(draft["rules"]) == 91 and "original" not in draft["rules"][0]
    pdf = client.get(f"/api/forms/{reading['id']}/file")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")

    saved = client.post(f"/api/forms/{reading['id']}/standard", json={"standard": draft})
    assert saved.status_code == 201, saved.text
    standard = saved.json()
    assert standard["status"] == "DRAFT" and standard["source_file"].endswith(".pdf")
    assert standard["history"][0]["request_filename"] == reading["filename"]
    again = client.post(f"/api/forms/{reading['id']}/standard", json={"standard": draft})
    assert again.status_code == 422

    preview = client.get(f"/api/standards/{standard['id']}/customer-form").json()
    assert preview["changed"] == 0 and len(preview["rows"]) == 21
    burr = next(row for row in preview["rows"] if row["item"] == "파팅면 버(Burr)")
    assert burr["cells"][0]["text"] == "버 높이 < 0.18 mm"

    # Re-reading the exported form gives the same criteria (nothing lost on the way out).
    exported = client.get(f"/api/standards/{standard['id']}/customer-form/xlsx")
    assert exported.status_code == 200
    sheet = load_workbook(io.BytesIO(exported.content)).active
    values = [cell for row in sheet.iter_rows(values_only=True) for cell in row if cell]
    assert "QA-STD 05.10.312" in values and "버 높이 < 0.18 mm" in values
    exported_pdf = client.get(f"/api/standards/{standard['id']}/customer-form/pdf")
    assert exported_pdf.status_code == 200
    text = "".join(
        page.extract_text() for page in PdfReader(io.BytesIO(exported_pdf.content)).pages
    )
    assert "QA-STD 05.10.312" in text

    activated = client.post(f"/api/standards/{standard['id']}/activate", json={})
    assert activated.status_code == 200, activated.text

    # The customer sends the same form again: it is offered as the next version.
    second = upload(client, "B")
    assert second["previous_id"] == standard["id"]
    assert "EXISTING_STANDARD" in codes(second)
    changed = second["draft"]
    for rule in changed["rules"]:
        if rule["id"] == "burr-a1":
            rule["conditions"][0]["limit"] = "0.15"
    version = client.post(
        f"/api/forms/{second['id']}/standard",
        json={"standard": changed, "previous_id": second["previous_id"]},
    )
    assert version.status_code == 201, version.text
    new = version.json()
    assert new["version"] == 2 and new["previous_id"] == standard["id"]
    assert new["revision"] != standard["revision"]

    preview = client.get(f"/api/standards/{new['id']}/customer-form").json()
    burr = next(row for row in preview["rows"] if row["item"] == "파팅면 버(Burr)")
    assert burr["cells"][0] == {
        "text": "버 높이 < 0.15 mm",
        "changed": True,
        "before": "버 높이 < 0.18 mm",
    }
    assert preview["changed"] == 1

    # Model C has the same criteria as model B under another part.
    model_c = upload(client, "C")
    assert "SAME_CRITERIA" in codes(model_c) and "COLOR_MISMATCH" in codes(model_c)


def test_relative_limit_note_is_kept_when_a_cell_changes(client):
    reading = upload(client, "B")
    draft = reading["draft"]
    sink = next(
        r for r in draft["rules"] if r["item"].startswith("표면 수축") and r["area"] == "외측면"
    )
    saved = client.post(f"/api/forms/{reading['id']}/standard", json={"standard": draft}).json()
    client.post(f"/api/standards/{saved['id']}/activate", json={})
    second = upload(client, "B")
    for rule in second["draft"]["rules"]:
        if rule["id"] == sink["id"]:
            rule["conditions"][2]["limit"] = "0.08"
    new = client.post(
        f"/api/forms/{second['id']}/standard",
        json={"standard": second["draft"], "previous_id": saved["id"]},
    ).json()
    preview = client.get(f"/api/standards/{new['id']}/customer-form").json()
    row = next(r for r in preview["rows"] if r["item"].startswith("표면 수축"))
    assert (
        row["cells"][0]["text"]
        == "허용: 길이 < 2.5 mm, 폭 < 1.2 mm,\n깊이 < 0.08 mm (살두께의 8 %)"
    )
    assert preview["history"][0][1] == new["revision"]


def test_limit_approval_is_saved_for_the_customer(client):
    reading = upload(client, "A")
    assert reading["document_type"] == "LIMIT_APPROVAL"
    assert (
        client.post(
            f"/api/forms/{reading['id']}/limit-approval", json={"customer": " "}
        ).status_code
        == 422
    )
    saved = client.post(f"/api/forms/{reading['id']}/limit-approval", json={"customer": "DEMO-OEM"})
    assert saved.status_code == 201
    approvals = client.get("/api/limit-approvals", params={"customer": "amx"}).json()
    assert len(approvals) == 1 and len(approvals[0]["samples"]) == 25
    assert (
        client.post(
            f"/api/forms/{reading['id']}/standard", json={"standard": reading["draft"]}
        ).status_code
        == 422
    )


def test_non_pdf_and_missing_profile_are_rejected(client):
    response = client.post("/api/forms", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert response.status_code == 422
    assert client.get("/api/standards/demo-cover-en-v1/customer-form").status_code == 404


class RecordingClient:
    label = "fake/model"

    def __init__(self, answer: str):
        self.answer = answer
        self.calls = []

    def complete(self, system, user, json_mode=True):
        self.calls.append(user)
        return self.answer


def ai_client(tmp_path, client_answer: str, base_url: str):
    from conftest import sign_in
    from fastapi.testclient import TestClient

    from spec_agent.config import LLMSettings, Settings
    from spec_agent.main import create_app

    fake = RecordingClient(client_answer)
    settings = Settings(
        tmp_path, llm=LLMSettings(provider="openai", model="m", base_url=base_url, api_key="k")
    )
    connection = TestClient(create_app(settings, fake))
    connection.__enter__()
    connection.app.state.auth.create_user("tester", "Tester", "testing-password-1234")
    sign_in(connection)
    return connection, fake


def test_ai_interprets_only_the_cells_rules_could_not(tmp_path):
    answer = (
        '{"rules": [{"id": "item03-a3", "kind": "NUMERIC", "conditions": [{"field": '
        '"recess_depth", "label": "depth", "operator": "lte", "limit": 0.18, "unit": "mm"}], '
        '"note": "protrusion not allowed"}, {"id": "burr-a1", "kind": "BOGUS"}]}'
    )
    client, fake = ai_client(tmp_path, answer, "http://127.0.0.1:9/v1")
    try:
        reading = upload(client, "B", provenance="INTERNAL")  # local model: allowed
        assert (
            len(fake.calls) == 1 and "item03-a3" in fake.calls[0] and "burr-a1" not in fake.calls[0]
        )
        rule = next(r for r in reading["rules"] if r["id"] == "item03-a3")
        assert rule["notes"][-1]["code"] == "AI_INTERPRETED"
        assert reading["interpreter"] == "rules+llm:fake/model"
        burr = next(r for r in reading["rules"] if r["id"] == "burr-a1")
        assert burr["kind"] == "NUMERIC"  # an invalid AI answer is ignored
        assert reading["ai"] == {"status": "used", "model": "openai/m", "count": 1}
    finally:
        client.__exit__(None, None, None)


def test_customer_forms_are_not_sent_to_an_external_ai(tmp_path):
    client, fake = ai_client(tmp_path, "{}", "https://api.example.com/v1")
    try:
        reading = upload(client, "B", provenance="INTERNAL")
        assert fake.calls == [] and "LLM_BLOCKED" in codes(reading)
        assert reading["ai"]["status"] == "blocked"
        reading = upload(client, "B", provenance="SYNTHETIC")
        assert len(fake.calls) == 1 and reading["ai"]["status"] == "used"
    finally:
        client.__exit__(None, None, None)


def test_the_reading_says_when_ai_is_off(client):
    reading = upload(client, "B")
    assert reading["ai"] == {"status": "off", "model": "", "count": 0}


# --- Synthetic customer forms in English and Vietnamese (docs/Data Augmentation) ---------

AUGMENTED = DOCS / "Data Augmentation"


def synthetic(model: str, case: str, language: str) -> Path:
    path = AUGMENTED / f"Model_{model}" / language / f"SYNTHETIC_{model}_{case}_{language}.pdf"
    if not path.is_file():
        pytest.skip("Synthetic forms are not available")
    return path


@pytest.mark.parametrize("language", ["KO", "EN", "VI"])
def test_same_form_in_three_languages_reads_the_same_way(language):
    reading = read_form("x.pdf", synthetic("B", "003", language).read_bytes()).to_dict()
    korean = read_form("x.pdf", synthetic("B", "003", "KO").read_bytes()).to_dict()
    assert reading["document_type"] == "INSPECTION_MATRIX"
    assert reading["header"]["document_number"] == "DEMO-B-003"
    assert reading["header"]["revision"] == "D03"
    assert len(reading["rules"]) == len(korean["rules"])
    assert [r["kind"] for r in reading["rules"]] == [r["kind"] for r in korean["rules"]]
    fields = lambda rules: [[c["field"] for c in r["conditions"]] for r in rules]
    assert fields(reading["rules"]) == fields(korean["rules"])


def test_change_history_rows_are_not_read_as_criteria():
    reading = read_form("x.pdf", synthetic("B", "001", "KO").read_bytes()).to_dict()
    items = {rule["item"] for rule in reading["rules"]}
    assert not {"NO", "1", "2"} & items


@pytest.mark.parametrize(
    "text, field, limit",
    [
        ("Phần dư cửa rót < 0.42 mm", "residue", "0.42"),
        ("Gate residue < 0.34 mm", "residue", "0.34"),
        ("Burr height < 0.14 mm", "height", "0.14"),
        ("Cho phép: sâu < 0.1 mm (8 % chiều dày thành)", "depth", "0.1"),
        ("Allowed: depth < 0.1 mm (8 % of wall thickness)", "depth", "0.1"),
    ],
)
def test_english_and_vietnamese_criteria(text, field, limit):
    (criterion,) = PARSER.parse(text, "-")
    assert criterion.kind == "NUMERIC"
    assert criterion.conditions[0]["field"] == field
    assert criterion.conditions[0]["limit"] == limit
