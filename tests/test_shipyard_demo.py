"""Dongnam demo: Korean, English and Chinese shipbuilding forms become standards and lots."""

from io import BytesIO

from pypdf import PdfReader


def test_shipyard_demo_reads_three_languages_and_reports_in_chinese(client):
    created = client.post("/api/demo/shipyard").json()
    assert created == {"standards": 3, "inspections": 4, "programs": 3}
    assert client.post("/api/demo/shipyard").json()["standards"] == 0  # loaded once

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
