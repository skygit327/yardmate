import ast
import json
import re
from io import BytesIO
from pathlib import Path

from conftest import TEST_PASSWORD, sign_in
from openpyxl import load_workbook
from pypdf import PdfReader
from test_workflow import create, submit

from spec_agent.config import Settings
from spec_agent.schemas.inspection import InspectionInput
from spec_agent.services.standards import demo_observations, demo_standard
from spec_agent.services.workflow import Workflow

ROOT = Path(__file__).resolve().parents[1]
HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")


def test_application_owned_text_is_english():
    for path in (ROOT / "src/spec_agent").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not HANGUL.search(node.value), (path, node.lineno, node.value)
    for path in [*(ROOT / "frontend/src").glob("*.tsx"), ROOT / "frontend/src/api.ts"]:
        assert not HANGUL.search(path.read_text()), path
    html = (ROOT / "frontend/index.html").read_text()
    assert 'lang="en"' in html
    assert not HANGUL.search(html)
    for path in (ROOT / "data/fixtures").glob("*.json"):
        assert not HANGUL.search(path.read_text()), path


def test_english_workflow_and_both_report_formats(client, inspection_payload):
    client.app.state.auth.create_user("english", "Demo Reviewer", TEST_PASSWORD)
    sign_in(client, "english")
    state = create(client, {**inspection_payload, "inspector": "Demo Inspector"})
    assert not HANGUL.search(json.dumps(state, ensure_ascii=False))
    state = submit(
        client,
        state,
        "confirm",
        decisions={"burr": {"result": "PASS", "reason": "Near-limit measurement reviewed"}},
    )
    state = submit(client, state, "approve", decisions={})
    assert not HANGUL.search(json.dumps(state, ensure_ascii=False))
    endpoint = f"/api/inspections/{state['inspection_id']}/reports"
    pdf_text = "".join(
        page.extract_text()
        for page in PdfReader(BytesIO(client.get(endpoint + "/pdf").content)).pages
    )
    workbook = load_workbook(BytesIO(client.get(endpoint + "/xlsx").content))
    excel_text = " ".join(str(cell.value or "") for row in workbook.active for cell in row)
    assert workbook.active.title == "Inspection Report"
    for text in (pdf_text, excel_text):
        assert not HANGUL.search(text)
        for expected in ("Inspection Report", "Approved", "Demo Reviewer", "Burr height"):
            assert expected in text


def test_english_seed_preserves_legacy_standard_and_inspection(tmp_path):
    settings = Settings(tmp_path)
    workflow = Workflow(settings)
    try:
        legacy = demo_standard().model_copy(
            update={"id": "demo-cover-v1", "revision": "1", "product_name": "가상 커버"}
        )
        workflow.store.save_standard(legacy)
        state = workflow.create(
            InspectionInput(
                standard_id=legacy.id,
                lot="LEGACY",
                inspector="기존 검사원",
                observations=demo_observations(),
            )
        )
        original = workflow.store.initial_state(state["inspection_id"])
        with workflow.store.connection:
            workflow.store.connection.execute(
                "DELETE FROM standards WHERE id=?", (demo_standard().id,)
            )
    finally:
        workflow.close()
    restored = Workflow(settings)
    try:
        assert restored.store.standard(legacy.id) == legacy
        assert restored.store.standard("demo-cover-en-v1").product_name == "Synthetic cover"
        assert restored.store.initial_state(state["inspection_id"]) == original
        assert restored.get(state["inspection_id"])["standard"]["product_name"] == "가상 커버"
    finally:
        restored.close()
