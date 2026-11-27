"""Generate 5 deterministic variants of each task-2 PDF. Requires PyMuPDF.

Run with .venv/bin/python scripts/generate_pdf_samples.py from the repository root.
"""

import hashlib
import json
import re
import unicodedata
from pathlib import Path

import pymupdf as fitz
from pypdf.generic import ContentStream, DecodedStreamObject, FloatObject

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/pdf/synthetic-v1"
QA = ROOT / "output/evaluation/synthetic-v1"


def spans(page):
    return [
        s
        for b in page.get_text("rawdict")["blocks"]
        if "lines" in b
        for line in b["lines"]
        for s in line["spans"]
    ]


def text(span):
    return "".join(c["c"] for c in span["chars"])


def edits_for(page, replacements):
    edits = []
    for span in spans(page):
        original = text(span)
        for old, new in replacements.items():
            for match in re.finditer(re.escape(old), original):
                # Match full numeric tokens, not e.g. 0.1 inside 0.18.
                if (
                    old[0].isdigit()
                    and match.start()
                    and original[match.start() - 1] in "0123456789."
                ):
                    continue
                if (
                    old[-1].isdigit()
                    and match.end() < len(original)
                    and original[match.end()] in "0123456789."
                ):
                    continue
                chars = span["chars"][match.start() : match.end()]
                rect = fitz.Rect(chars[0]["bbox"])
                for char in chars[1:]:
                    rect |= fitz.Rect(char["bbox"])
                edits.append(
                    {
                        "rect": rect,
                        "origin": chars[0]["origin"],
                        "size": span["size"],
                        "font": span["font"],
                        "color": span["color"],
                        "old": old,
                        "new": new,
                    }
                )
    return edits


def apply_edits(page, edits):
    original_widths = stroke_widths(content_stream(page))
    for edit in edits:
        # Leave vector table borders and images untouched; remove actual old text.
        rect = fitz.Rect(edit["rect"])
        rect.y0 += rect.height * 0.15
        rect.y1 -= rect.height * 0.15
        page.add_redact_annot(rect, fill=False, cross_out=False)
    if edits:
        page.apply_redactions(images=0, graphics=0, text=0)
        if original_widths:
            # MuPDF redaction drops thin text stroke widths in the B/C source PDFs.
            stream = content_stream(page)
            restored = stroke_widths(stream)
            assert len(restored) == len(original_widths)
            widths = iter(original_widths.values())
            stream.operations = [
                entry
                for i, op in enumerate(stream.operations)
                for entry in ([([FloatObject(next(widths))], b"w"), op] if i in restored else [op])
            ]
            assert len(page.get_contents()) == 1
            page.parent.update_stream(page.get_contents()[0], stream.get_data())
    for edit in edits:
        if "cell" in edit:
            cell = fitz.Rect(edit["cell"])
            cell += (0.4, 0.4, -0.4, -0.4)
            page.draw_rect(cell, color=None, fill=edit["fill"], overlay=True)
        fontname = "hebo" if "Bold" in edit["font"] else "helv"
        size = min(
            edit["size"],
            edit["rect"].width / fitz.get_text_length(edit["new"], fontname=fontname, fontsize=1),
        )
        assert size >= 4, edit
        color = fitz.sRGB_to_pdf(edit["color"])
        page.insert_text(edit["origin"], edit["new"], fontname=fontname, fontsize=size, color=color)


def content_stream(page):
    stream = DecodedStreamObject()
    stream.set_data(page.read_contents())
    return ContentStream(stream, None)


def stroke_widths(stream):
    widths, stack = {}, []
    width, mode = 1, 0
    for index, (args, operator) in enumerate(stream.operations):
        if operator == b"q":
            stack.append((width, mode))
        elif operator == b"Q":
            width, mode = stack.pop()
        elif operator == b"w":
            width = float(args[0])
        elif operator == b"Tr":
            mode = int(args[0])
        elif operator in (b"Tj", b"TJ", b"'", b'"') and mode in (1, 2):
            widths[index] = width
    return widths


def source(family):
    return next(
        p
        for p in (ROOT / "docs").glob("*.pdf")
        if ("NCR" if family == "NCR" else f"모델{family}") in unicodedata.normalize("NFC", p.name)
    )


def transform(doc, family, variant):
    changes, facts, queries = [], {}, []
    if family in ("B", "C"):
        old_model = "MX 5.2" if family == "B" else "KX 5.2"
        model = ("MX" if family == "B" else "KX") + f" 6.{variant}"
        old_number = "05.10.312" if family == "B" else "05.10.316"
        number = f"26.10.{(310 if family == 'B' else 410) + variant}"
        gate = ["0.40", "0.42", "0.48", "0.50", "0.55"][variant - 1]
        burr = ["0.15", "0.16", "0.20", "0.22", "0.24"][variant - 1]
        replacements = {
            old_model: model,
            old_number: number,
            "2020-10-03": f"2026-10-{10 + variant}",
            "0.45": gate,
            "0.18": burr,
        }
        page = doc[0]
        edits = edits_for(page, replacements)
        for old in replacements:
            assert any(e["old"] == old for e in edits), (family, old)
        facts = {
            "model": model,
            "document_number": f"QA-STD {number}",
            "gate_limit_mm_exclusive": gate,
            "burr_limit_mm_exclusive": burr,
        }
        queries = [
            {
                "query": f"{model}의 외관 검사 기준 문서번호는?",
                "page": 1,
                "answer": number,
                "evidence": "상단 문서번호",
            },
            {
                "query": f"{model}의 게이트 잔류물 허용 기준은?",
                "page": 1,
                "answer": gate,
                "evidence": f"게이트(Gate) / 잔류물 < {gate} mm",
            },
            {
                "query": f"{model}의 파팅면 버 높이 허용 기준은?",
                "page": 1,
                "answer": burr,
                "evidence": f"파팅면 버(Burr) / 높이 < {burr} mm",
            },
        ]
        apply_edits(page, edits)
        changes.extend(record(1, e) for e in edits)
    elif family == "A":
        model = f"Model-{chr(ord('C') + variant)}"
        ratios = []
        for page_number, page in enumerate(doc):
            edits = edits_for(
                page,
                {
                    "Model-A": model,
                    "Part-A": f"Part-{chr(ord('C') + variant)}",
                    "2 SET": f"{variant + 2} SET",
                    "2 set": f"{variant + 2} set",
                },
            )
            percent_edits = []
            for span in spans(page):
                value = text(span).strip()
                if re.fullmatch(r"\d+\.\d%", value):
                    percent_edits.extend(edits_for_span(span, value))
            if page_number < 2:
                percent_edits.sort(key=lambda e: (round(e["rect"].y0, 0), e["rect"].x0))
                assert len(percent_edits) == (48 if page_number == 0 else 27)
                for offset in range(0, len(percent_edits), 3):
                    group = percent_edits[offset : offset + 3]
                    values = [round(float(e["old"][:-1]) * 10) for e in group]
                    updated = [
                        values[0] + variant * 3,
                        values[1] - variant,
                        values[2] - variant * 2,
                    ]
                    assert sum(updated) == 1000
                    ratios.append([f"{v / 10:.1f}%" for v in updated])
                    for e, new in zip(group, ratios[-1]):
                        e["new"] = new
            elif page_number >= 3:
                assert len(percent_edits) == 4, (page_number, percent_edits)
                ordered = sorted(percent_edits, key=lambda e: e["rect"].x0)
                defect = ordered[0]
                defect["new"] = f"{float(defect['old'][:-1]) + variant * 0.2:.1f}%"
                for e, new in zip(
                    sorted(ordered[1:], key=lambda e: e["rect"].y0), ratios[page_number - 3]
                ):
                    e["new"] = new
                if page_number == 3:
                    facts["dirt_defect_rate"] = defect["new"]
            edits.extend(percent_edits)
            apply_edits(page, edits)
            changes.extend(record(page_number + 1, e) for e in edits)
        facts.update(model=model, severity_ratios=ratios, sample_sets=variant + 2)
        queries = [
            {
                "query": f"{model}의 오염(Dirt) 불량률은?",
                "page": 4,
                "answer": facts["dirt_defect_rate"],
                "evidence": "오염 (Dirt) 상세 기준서 / 불량률",
            },
            {
                "query": f"{model}의 오염 불량 중 1단계 비율은?",
                "page": 1,
                "answer": ratios[0][0],
                "evidence": "요약표 No.1 / Type 오염 (Dirt) / 1열",
                "relevant_pages": [1, 4],
            },
            {
                "query": f"{model}의 한도 기준 수량은 몇 set인가?",
                "page": 4,
                "answer": f"{variant + 2} set",
                "evidence": "한도 기준 수량",
                "relevant_pages": list(range(1, 3)) + list(range(4, 29)),
            },
        ]
    else:
        changes, facts, queries = transform_ncr(doc, variant)
    return changes, facts, queries


def edits_for_span(span, value):
    return [
        {
            "rect": fitz.Rect(span["bbox"]),
            "origin": span["origin"],
            "size": span["size"],
            "font": span["font"],
            "color": span["color"],
            "old": value,
            "new": value,
        }
    ]


def record(page, edit):
    return {"page": page, "bbox": list(edit["rect"]), "old": edit["old"], "new": edit["new"]}


def transform_ncr(doc, variant):
    page = doc[0]
    ncr = f"NCR-SYN-{100 + variant}"
    replacements = {
        "NCR-CASE-001": ncr,
        "October 9, 2026": f"October {10 + variant}, 2026",
        "LOT-A": f"LOT-{2 * variant + 1:02d}",
        "LOT-B": f"LOT-{2 * variant + 2:02d}",
        "M Company": f"{chr(ord('M') + variant)} Company",
        "P-A001": f"P-A{100 + variant}",
        "P-B001": f"P-B{100 + variant}",
    }
    edits = edits_for(page, replacements)
    for old in replacements:
        assert any(e["old"] == old for e in edits), old
    measurements = []
    for span in spans(page):
        value = text(span).strip()
        if re.fullmatch(r"85\.\d{4}", value):
            measurements.extend(edits_for_span(span, value))
    measurements.sort(key=lambda e: (round(e["rect"].y0), e["rect"].x0))
    assert len(measurements) == 20, measurements
    colored_cells = [
        p
        for p in page.get_drawings()
        if p["fill"] is not None
        and (p["fill"][0] > 0.8 and p["fill"][1] < 0.1 or p["fill"][1] > 0.5 and p["fill"][0] < 0.8)
    ]
    values = []
    for index, edit in enumerate(measurements):
        units = 858200 + ((index * 13 + variant * 7) % 50) * 10 + variant
        value = f"{units / 10000:.4f}"
        ok = 858300 <= units <= 858600
        cells = [
            p["rect"]
            for p in colored_cells
            if p["rect"].contains(edit["rect"].tl) and p["rect"].contains(edit["rect"].br)
        ]
        assert len(cells) == 1, (edit, cells)
        edit.update(new=value, cell=list(cells[0]), fill=(0.573, 0.816, 0.314) if ok else (1, 0, 0))
        values.append(
            {
                "product": "B" if index < 10 else "A",
                "column": index % 10 + 1,
                "measurement_mm": value,
                "judgement": "OK" if ok else "NG",
            }
        )
    edits.extend(measurements)
    assert any(v["judgement"] == "NG" for v in values)
    apply_edits(page, edits)
    facts = {
        "ncr_number": ncr,
        "nominal_mm": "85.85",
        "lower_mm": "85.83",
        "upper_mm": "85.86",
        "measurements": values,
        "result": "NG",
    }
    queries = [
        {
            "query": f"{ncr}의 LOT 번호는?",
            "page": 1,
            "answer": f"LOT-{2 * variant + 1:02d}",
            "evidence": "상단 Lot No 및 측정표 LOT 행",
            "additional_answer": f"LOT-{2 * variant + 2:02d}",
        },
        {
            "query": f"{ncr}에서 제품 B의 첫 번째 CMM 측정값은?",
            "page": 1,
            "answer": values[0]["measurement_mm"],
            "evidence": "HOUSING PRODUCT B / CMM / X1 (첫 LOT)",
        },
        {
            "query": f"부품 P-A{100 + variant}의 직경 부적합 보고서 번호는?",
            "page": 1,
            "answer": ncr,
            "evidence": "상단 Part Number 및 NCR No",
        },
    ]
    return [record(1, e) for e in edits], facts, queries


def generate():
    from spec_agent.services.documents import extract_text

    OUT.mkdir(parents=True, exist_ok=True)
    QA.mkdir(parents=True, exist_ok=True)
    manifest, questions = [], []
    for family in ("NCR", "A", "B", "C"):
        src = source(family)
        source_hash = hashlib.sha256(src.read_bytes()).hexdigest()
        for variant in range(1, 6):
            doc = fitz.open(src)
            doc_id = f"SYN-{family}-{variant:03d}"
            changes, facts, queries = transform(doc, family, variant)
            doc.set_metadata(
                {
                    "title": f"SYNTHETIC TEST DATA / {doc_id}",
                    "subject": "Synthetic retrieval benchmark; not an approved standard",
                    "author": "Synthetic test generator",
                }
            )
            for page in doc:
                page.insert_text(
                    (8, page.rect.height - 5),
                    f"SYNTHETIC TEST DATA | {doc_id} | NOT FOR PRODUCTION",
                    fontsize=5,
                    color=(0.4, 0.4, 0.4),
                )
            target = OUT / f"{doc_id}.pdf"
            doc.save(target, garbage=4, deflate=True)
            doc.close()
            check = fitz.open(target)
            from pypdf import PdfReader

            reader = PdfReader(target)
            assert len(check) == (28 if family == "A" else 1)
            extracted = extract_text(".pdf", target.read_bytes())
            assert doc_id in extracted
            for change in changes:
                page = check[change["page"] - 1]
                region = fitz.Rect(change["bbox"])
                region.x1 += 1
                region.y0 -= 2
                region.y1 += 2
                local_text = page.get_textbox(region)
                assert change["new"] in local_text, (target, change, local_text)
                # The old value must be absent at its former location, not globally.
                assert change["old"] not in local_text, (target, change, local_text)
            for query in queries:
                page_text = reader.pages[query["page"] - 1].extract_text()
                assert query["answer"] in page_text, (target, query)
                questions.append(
                    {
                        "id": f"{doc_id}-Q{len(questions) + 1:03d}",
                        "document_id": doc_id,
                        "file": target.name,
                        **query,
                        "visible_to_app_parser": query["answer"] in extracted,
                    }
                )
            manifest.append(
                {
                    "document_id": doc_id,
                    "file": target.name,
                    "source": str(src.relative_to(ROOT)),
                    "source_sha256": source_hash,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "pages": len(check),
                    "facts": facts,
                    "changes": changes,
                }
            )
            check.close()
            assert hashlib.sha256(src.read_bytes()).hexdigest() == source_hash
    assert len(manifest) == 20 and sum(x["pages"] for x in manifest) == 155
    (QA / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    (QA / "questions.jsonl").write_text(
        "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions)
    )
    print(f"Validated {len(manifest)} PDFs, 155 pages, {len(questions)} queries")


if __name__ == "__main__":
    generate()
