from io import BytesIO

from conftest import sign_in
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pypdf import PdfReader
from test_workflow import create, submit

from spec_agent.config import Settings
from spec_agent.main import create_app
from spec_agent.reports.i18n import ReportTranslator


def pdf_text(content: bytes) -> str:
    text = " ".join(page.extract_text() for page in PdfReader(BytesIO(content)).pages)
    return " ".join(text.split())


def excel_text(content: bytes):
    book = load_workbook(BytesIO(content))
    return book, " ".join(str(cell.value or "") for row in book.active for cell in row)


def test_reports_are_rendered_in_every_language(client, inspection_payload):
    state = submit(client, create(client, inspection_payload), "confirm")
    assert set(state["report"]["files"]) == {
        "pdf",
        "xlsx",
        "pdf-ko",
        "xlsx-ko",
        "pdf-zh",
        "xlsx-zh",
    }
    endpoint = f"/api/inspections/{state['inspection_id']}/reports"

    english = pdf_text(client.get(endpoint + "/pdf").content)
    assert "Inspection Report" in english and "Translated" not in english

    response = client.get(endpoint + "/pdf?lang=zh")
    assert response.status_code == 200
    assert "-zh.pdf" in response.headers["content-disposition"]
    chinese = pdf_text(response.content)
    for expected in ("检验报告", "毛刺高度 (Burr height)", "合格 (PASS)", "警告"):
        assert expected in chinese

    book, korean = excel_text(client.get(endpoint + "/xlsx?lang=ko").content)
    assert book.active.title == "검사성적서"
    for expected in ("버 높이 (Burr height)", "합격 (PASS)", "높이 < 0.20 mm", "[경고]"):
        assert expected in korean
    assert "개발용 경고 확인" in korean  # user-entered reasons are kept as written

    assert client.get(endpoint + "/pdf?lang=jp").status_code == 422

    state = submit(client, state, "approve", decisions={})
    _, approved = excel_text(client.get(endpoint + "/xlsx?lang=zh").content)
    assert "已批准" in approved


def test_every_language_file_is_checked_before_approval(client, inspection_payload):
    state = submit(client, create(client, inspection_payload), "confirm")
    path = client.app.state.workflow.report_path(state["inspection_id"], "pdf", "zh")
    path.write_bytes(b"tampered")
    response = client.post(
        f"/api/inspections/{state['inspection_id']}/actions",
        json={
            "request_id": "00000000-0000-4000-8000-000000000001",
            "expected_revision": state["revision"],
            "interrupt_id": state["pending"]["id"],
            "action": "approve",
        },
    )
    assert response.status_code == 409


def test_report_languages_can_be_limited(tmp_path, inspection_payload):
    settings = Settings(tmp_path, report_languages=("en",))
    with TestClient(create_app(settings)) as connection:
        connection.app.state.auth.create_user("tester", "Tester", "testing-password-1234")
        sign_in(connection)
        state = submit(connection, create(connection, inspection_payload), "confirm")
        assert set(state["report"]["files"]) == {"pdf", "xlsx"}
        endpoint = f"/api/inspections/{state['inspection_id']}/reports/pdf?lang=zh"
        assert connection.get(endpoint).status_code == 404
        assert connection.get("/api/health").json()["report_languages"] == ["en"]


def test_standard_documents_in_other_languages(client):
    response = client.get("/api/standards/demo-cover-en-v1/document/xlsx?lang=zh")
    assert response.status_code == 200
    book, text = excel_text(response.content)
    assert book.active.title == "标准书"
    assert "毛刺高度 (Burr height)" in text and "变更记录" in book.sheetnames
    korean = pdf_text(client.get("/api/standards/demo-cover-en-v1/document/pdf?lang=ko").content)
    assert "Synthetic criterion 1" in korean  # sources are cited as written


def test_translator_keeps_customer_wording():
    english = ReportTranslator("en")
    assert english.term("외측면") == "Outer surface (외측면)"
    assert english.term("Unknown area") == "Unknown area"
    assert english.sentence("높이: The value is 0.05 mm above the limit.") == (
        "Height: The value is 0.05 mm above the limit."
    )
    chinese = ReportTranslator("zh")
    assert chinese.phrase("깊이 < 0.25 mm AND 직경 < 0.6 mm") == ("深度 < 0.25 mm 且 直径 < 0.6 mm")
    assert chinese.sentence("Depth: Missing value or unit mismatch") == (
        "深度：缺少数值或单位不一致"
    )
    assert ReportTranslator("ko").decision("REVIEW") == "판단 보류 (REVIEW)"
