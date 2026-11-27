"""Synthetic shipbuilding inspection forms for the Dongnam demo (shipyard KO, coating EN, owner ZH).

Every form is invented for demonstration and is marked as synthetic. No real shipyard, owner
or classification society issued them.

Run: uv run python scripts/generate_dongnam_forms.py
"""

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "spec_agent" / "services" / "shipyard_samples"
DOCS = ROOT / "docs" / "Dongnam Samples"
FORMS = json.loads((Path(__file__).with_name("dongnam_forms.json")).read_text("utf-8"))
FONTS = {"ko": "HYSMyeongJo-Medium", "zh": "STSong-Light", "en": "Helvetica"}


def build(form: dict) -> Path:
    font = FONTS[form["language"]]
    if font not in pdfmetrics.getRegisteredFontNames() and font != "Helvetica":
        pdfmetrics.registerFont(UnicodeCIDFont(font))
    labels = form["labels"]
    header = form["header"]
    areas = form["areas"]
    width = 3 + len(areas)
    rows = [[form["title"]] + [""] * (width - 1)]
    pairs = list(header.items())
    for left, right in zip(pairs[0::2], pairs[1::2], strict=False):
        rows.append([left[0], left[1], right[0], right[1]] + [""] * (width - 4))
    rows.append(
        [labels["item"], labels["criteria"]]
        + [""] * (len(areas) - 1)
        + [labels["method"], labels["frequency"]]
    )
    rows.append([""] + areas + ["", ""])
    for item in form["items"]:
        rows.append([item["item"], *item["criteria"], form["method"], form["frequency"]])
    first_data = 2 + len(pairs) // 2
    style = TableStyle(
        [
            ("FONT", (0, 0), (-1, -1), font, 8.5),
            ("FONT", (0, 0), (-1, 0), font, 15),
            ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#46515c")),
            ("SPAN", (0, 0), (-1, 0)),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("BACKGROUND", (0, first_data - 1), (-1, first_data), colors.HexColor("#dfe6ee")),
            ("SPAN", (0, first_data - 1), (0, first_data)),
            ("SPAN", (1, first_data - 1), (len(areas), first_data - 1)),
            ("SPAN", (len(areas) + 1, first_data - 1), (len(areas) + 1, first_data)),
            ("SPAN", (len(areas) + 2, first_data - 1), (len(areas) + 2, first_data)),
            ("BACKGROUND", (0, first_data + 1), (0, -1), colors.HexColor("#eef2f6")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
    )
    for index in range(1, first_data - 1):
        style.add("SPAN", (3, index), (-1, index))
        style.add("BACKGROUND", (0, index), (0, index), colors.HexColor("#eef2f6"))
        style.add("BACKGROUND", (2, index), (2, index), colors.HexColor("#eef2f6"))
    column = (780 - 130 - 60 - 60) / len(areas)
    table = Table(rows, colWidths=[130, *[column] * len(areas), 60, 60], style=style)
    note = ParagraphStyle(
        "note", fontName=font, fontSize=8, leading=11, textColor=colors.HexColor("#8a1c1c")
    )
    path = OUT / form["file"]
    SimpleDocTemplate(
        str(path),
        pagesize=landscape(A4),
        leftMargin=30,
        rightMargin=30,
        topMargin=30,
        bottomMargin=30,
        title=form["title"],
    ).build([table, Spacer(1, 10), Paragraph(form["notice"], note)])
    return path


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    for form in FORMS:
        path = build(form)
        (DOCS / path.name).write_bytes(path.read_bytes())
        print(path)
