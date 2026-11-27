"""Write a standard back in the customer's own layout (Excel and PDF).

The layout comes from the form profile captured when the customer's document was read
(``form_reader.py``). Cells whose rule is unchanged since that reading keep the customer's
original wording; changed cells are rewritten in the same style and highlighted, and the
form's change-history table is filled from the standard's version history.
"""

from __future__ import annotations

import io
import re
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from spec_agent.reports.i18n import rich_text
from spec_agent.reports.rendering import latin_font
from spec_agent.schemas.standard import Standard
from spec_agent.services.form_reader import LEXICON, criterion_text

WORDS = LEXICON["export"]
NUMBER_WITH_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mm|µm|um)")
CHANGED = "FFF2A8"


def _same(rule, baseline: dict | None) -> bool:
    if baseline is None:
        return False
    current = rule.model_dump(mode="json")
    if current["kind"] != baseline["kind"]:
        return False

    def key(conditions):
        return [(c["field"], c["operator"], str(float(c["limit"])), c["unit"]) for c in conditions]

    return key(current["conditions"]) == key(baseline.get("conditions", []))


def _rule_text(rule, baseline: dict | None, prefix: str) -> str:
    text = criterion_text(rule.model_dump(mode="json"), prefix if rule.kind == "NUMERIC" else "")
    for note in (baseline or {}).get("notes", []):
        if note["code"] == "RELATIVE_LIMIT" and rule.kind == "NUMERIC":
            text += f" ({LEXICON['relative_limit'][0]} {note['params']['percent']} %)"
    return text


def _cell_text(cell: dict, rules: dict, baseline: dict) -> str:
    present = [rules[i] for i in cell["rule_ids"] if i in rules]
    if len(present) == len(cell["rule_ids"]) and all(
        _same(rule, baseline.get(rule.id)) for rule in present
    ):
        return cell["original"]
    if not present:
        return WORDS["not_applicable"]
    patched = _patch_numbers(cell, present, baseline)
    if patched is not None:
        return patched
    if len(present) == 1 and not any(cell.get("variants") or []):
        return _rule_text(present[0], baseline.get(present[0].id), cell["prefix"])
    lines = [cell["prefix"].strip(" :")] if cell["prefix"] else []
    for rule, variant in zip(present, cell.get("variants") or [], strict=False):
        body = _rule_text(rule, baseline.get(rule.id), "")
        lines.append(f"\N{FULLWIDTH GREATER-THAN SIGN}{variant}: {body}" if variant else body)
    return "\n".join(line for line in lines if line)


def _patch_numbers(cell: dict, present: list, baseline: dict) -> str | None:
    """Keep the customer's sentence and swap only the limits when the structure is unchanged."""
    if len(present) != len(cell["rule_ids"]):
        return None
    old, new = [], []
    for rule in present:
        before = baseline.get(rule.id)
        if before is None or rule.kind != "NUMERIC" or before["kind"] != "NUMERIC":
            return None
        shape = [(c["field"], c["operator"], c["unit"]) for c in before["conditions"]]
        if shape != [(c.field, c.operator, c.unit) for c in rule.conditions]:
            return None
        old += before["conditions"]
        new += rule.conditions
    matches = list(NUMBER_WITH_UNIT.finditer(cell["original"]))
    if len(matches) != len(new):
        return None
    text, shift = cell["original"], 0
    for match, before, after in zip(matches, old, new, strict=True):
        if Decimal(match.group(1)) != Decimal(str(before["limit"])):
            return None
        value = format(after.limit.normalize(), "f")
        start, end = match.start(1) + shift, match.end(1) + shift
        text = text[:start] + value + text[end:]
        shift += len(value) - (match.end(1) - match.start(1))
    return text


def build_form(standard: Standard, family: list[Standard], profile: dict) -> dict:
    """Return the customer-layout grid for one standard version.

    Cells keep the customer's wording where the rule matches the form that was read and
    are rewritten in the same style otherwise. A cell is marked as changed when it
    differs from the previous version of the standard (what the customer saw last).
    """
    layout, baseline = profile["layout"], profile["baseline"]
    rules = {rule.id: rule for rule in standard.rules}
    previous = next((v for v in family if v.id == standard.previous_id), None)
    before_rules = {rule.id: rule for rule in previous.rules} if previous else None
    used = set()
    rows = []
    for row in layout["rows"]:
        cells = []
        for cell in row["cells"]:
            used.update(cell["rule_ids"])
            text = _cell_text(cell, rules, baseline)
            before = (
                cell["original"]
                if before_rules is None
                else _cell_text(cell, before_rules, baseline)
            )
            if text == before:
                cells.append({"text": text, "changed": False})
            else:
                cells.append({"text": text, "changed": True, "before": before})
        rows.append({"item": row["item"], "cells": cells})
    areas = layout["areas"]
    extra = [rule for rule in standard.rules if rule.id not in used]
    for rule in extra:
        cells = [
            {"text": _rule_text(rule, None, WORDS["allow_prefix"]), "changed": True, "before": ""}
            if rule.area == area
            else {"text": "", "changed": False}
            for area in areas
        ]
        if rule.area not in areas:
            cells[0] = {
                "text": f"{rule.area}: {_rule_text(rule, None, '')}",
                "changed": True,
                "before": "",
            }
        rows.append({"item": f"{rule.item} ({WORDS['added_rows']})", "cells": cells})
    history = []
    for version in sorted(family, key=lambda item: item.version):
        if version.version <= 1 or version.version > standard.version:
            continue
        entry = next((h for h in reversed(version.history) if h.kind == "REVISED"), None)
        summary = entry.summary if entry else ""
        if entry and entry.interpreter == "form-reader":
            summary = WORDS["imported_summary"].format(filename=entry.request_filename or "")
        date = (version.activated_at or version.created_at or "")[:10]
        history.append(
            [
                str(len(history) + 1),
                version.revision,
                summary[:300],
                date,
                entry.actor if entry else "",
                "",
                version.activated_by or "",
            ]
        )
    header_values = {
        "customer": standard.customer,
        "product_name": standard.product_name,
        "part_name": standard.part_number,
        "revision": standard.revision,
        "document_number": standard.document_number,
    }
    header = [
        [label, header_values.get(key, profile["header"].get(key, ""))]
        for label, key in layout["header"]
    ]
    return {
        "title": layout.get("title") or "",
        "header": header,
        "layout": layout,
        "rows": rows,
        "history": history,
        "changed": sum(cell["changed"] for row in rows for cell in row["cells"]),
    }


def _conditions_lines(conditions: dict) -> list[str]:
    lines = []
    if conditions.get("distance_cm"):
        low, high = conditions["distance_cm"]
        inch = conditions.get("distance_inch") or ["", ""]
        lines.append(f"{inch[0]}~{inch[1]} inch ({low}~{high} cm)")
    if conditions.get("illuminance_lux"):
        lines.append(f"≥ {conditions['illuminance_lux']} Lux")
    if conditions.get("customer_standard"):
        grade = f" Class {conditions['class']}" if conditions.get("class") else ""
        lines.append(f"{conditions['customer_standard']}{grade}")
    return lines


def render_xlsx(path: Path, form: dict, figure: bytes | None):
    layout = form["layout"]
    areas = layout["areas"]
    extra_columns = [
        label for label in (layout.get("method_label"), layout.get("frequency_label")) if label
    ]
    width = 1 + len(areas) + len(extra_columns)
    book = Workbook()
    sheet = book.active
    sheet.title = "Form"
    thin = Side(style="thin", color="808080")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="center")
    centre = Alignment(wrap_text=True, vertical="center", horizontal="center")
    bold = Font(bold=True)
    grey = PatternFill("solid", fgColor="E7E9E7")
    yellow = PatternFill("solid", fgColor=CHANGED)
    sheet.column_dimensions["A"].width = 26
    for index in range(2, width + 1):
        sheet.column_dimensions[get_column_letter(index)].width = (
            34 if index <= len(areas) + 1 else 12
        )

    row = 1
    sheet.cell(row, 1, form["title"]).font = Font(bold=True, size=16)
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)
    row += 2
    for label, value in form["header"]:
        sheet.cell(row, 1, label).font = bold
        sheet.cell(row, 2, value)
        row += 1
    if figure:
        from openpyxl.drawing.image import Image as XLImage

        image = XLImage(io.BytesIO(figure))
        ratio = 900 / image.width
        image.width, image.height = int(image.width * ratio), int(image.height * ratio)
        sheet.add_image(image, f"A{row + 1}")
        row += max(2, image.height // 20 + 2)
    conditions = _conditions_lines(layout.get("conditions") or {})
    if conditions:
        sheet.cell(row, 1, WORDS["conditions_title"]).font = bold
        sheet.cell(row, 2, " / ".join(conditions))
        row += 2

    top = row
    sheet.cell(row, 1, layout.get("item_label") or "")
    sheet.cell(row, 2, layout.get("criteria_label") or "")
    sheet.merge_cells(start_row=row, start_column=2, end_row=row, end_column=1 + len(areas))
    for offset, label in enumerate(extra_columns):
        sheet.cell(row, 2 + len(areas) + offset, label)
    row += 1
    column = 2
    for group in layout["groups"]:
        sheet.cell(row, column, group["name"])
        if len(group["columns"]) > 1:
            sheet.merge_cells(
                start_row=row,
                start_column=column,
                end_row=row,
                end_column=column + len(group["columns"]) - 1,
            )
        column += len(group["columns"])
    row += 1
    for index, area in enumerate(areas):
        sheet.cell(row, 2 + index, area)
    sheet.merge_cells(start_row=top, start_column=1, end_row=row, end_column=1)
    for offset in range(len(extra_columns)):
        sheet.merge_cells(
            start_row=top,
            start_column=2 + len(areas) + offset,
            end_row=row,
            end_column=2 + len(areas) + offset,
        )
    for r in range(top, row + 1):
        for c in range(1, width + 1):
            cell = sheet.cell(r, c)
            cell.font, cell.fill, cell.alignment, cell.border = bold, grey, centre, border
    first_data = row + 1
    for data in form["rows"]:
        row += 1
        sheet.cell(row, 1, data["item"]).font = bold
        for index, cell in enumerate(data["cells"]):
            target = sheet.cell(row, 2 + index, cell["text"])
            if cell["changed"]:
                target.fill = yellow
                if cell.get("before"):
                    target.comment = Comment(cell["before"], "Spec Agent")
        lines = max(1, *(c["text"].count("\n") + 1 + len(c["text"]) // 40 for c in data["cells"]))
        sheet.row_dimensions[row].height = 15 * lines
        for c in range(1, width + 1):
            sheet.cell(row, c).alignment = wrap if c > 1 else centre
            sheet.cell(row, c).border = border
    conditions = layout.get("conditions") or {}
    for offset, key in enumerate(("method", "frequency")[: len(extra_columns)]):
        column = 2 + len(areas) + offset
        sheet.cell(first_data, column, conditions.get(key, "")).alignment = centre
        if row > first_data:
            sheet.merge_cells(
                start_row=first_data, start_column=column, end_row=row, end_column=column
            )
    row += 2
    sheet.cell(row, 1, WORDS["changed_legend"]).fill = yellow
    row += 2
    sheet.cell(row, 1, WORDS["history_title"]).font = bold
    row += 1
    for index, label in enumerate(WORDS["history_columns"], start=1):
        cell = sheet.cell(row, index, label)
        cell.font, cell.fill, cell.border = bold, grey, border
    for entry in form["history"] or [["1", "", "", "", "", "", ""]]:
        row += 1
        for index, value in enumerate(entry, start=1):
            cell = sheet.cell(row, index, value)
            cell.border, cell.alignment = border, wrap
    book.save(path)


def render_pdf(path: Path, form: dict, figure: bytes | None, cjk_font: str):
    layout = form["layout"]
    areas = layout["areas"]
    extra_columns = [
        label for label in (layout.get("method_label"), layout.get("frequency_label")) if label
    ]
    base = ParagraphStyle("form", fontName=latin_font(), fontSize=8, leading=10.5)
    head = ParagraphStyle("form-head", parent=base, fontSize=8.5, alignment=1)
    title = ParagraphStyle("form-title", parent=base, fontSize=18, leading=24, alignment=1)

    def p(value, style=base):
        return Paragraph(rich_text(value, cjk_font), style)

    page_width = landscape(A3)[0] - 60
    story = [p(form["title"], title), Spacer(1, 8)]
    pairs = form["header"]
    per_row = 4
    cells = []
    for start in range(0, len(pairs), per_row):
        line = []
        for label, value in pairs[start : start + per_row]:
            line += [p(label, head), p(value)]
        line += [""] * (per_row * 2 - len(line))
        cells.append(line)
    header = Table(cells, colWidths=[70, page_width / per_row - 70] * per_row)
    style_rows = [("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]
    for column in range(0, per_row * 2, 2):
        style_rows.append(("BACKGROUND", (column, 0), (column, -1), colors.HexColor("#E7E9E7")))
    header.setStyle(TableStyle(style_rows))
    story += [header, Spacer(1, 8)]
    if figure:
        image = Image(io.BytesIO(figure))
        scale = min(page_width / image.imageWidth, 150 / image.imageHeight)
        image.drawWidth, image.drawHeight = image.imageWidth * scale, image.imageHeight * scale
        story += [image, Spacer(1, 6)]
    conditions = _conditions_lines(layout.get("conditions") or {})
    if conditions:
        story += [p(f"{WORDS['conditions_title']}: " + " / ".join(conditions)), Spacer(1, 6)]

    extra_width = 50
    item_width = 120
    area_width = (page_width - item_width - extra_width * len(extra_columns)) / max(1, len(areas))
    widths = [item_width, *[area_width] * len(areas), *[extra_width] * len(extra_columns)]
    group_row = [""]
    spans = []
    column = 1
    for group in layout["groups"]:
        group_row += [p(group["name"], head)] + [""] * (len(group["columns"]) - 1)
        if len(group["columns"]) > 1:
            spans.append(("SPAN", (column, 1), (column + len(group["columns"]) - 1, 1)))
        column += len(group["columns"])
    group_row += [""] * len(extra_columns)
    data = [
        [p(layout.get("item_label") or "", head), p(layout.get("criteria_label") or "", head)]
        + [""] * (len(areas) - 1)
        + [p(label, head) for label in extra_columns],
        group_row,
        [""] + [p(area, head) for area in areas] + [""] * len(extra_columns),
    ]
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 2), colors.HexColor("#E7E9E7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("SPAN", (0, 0), (0, 2)),
        ("SPAN", (1, 0), (len(areas), 0)),
        *spans,
    ]
    for offset in range(len(extra_columns)):
        column = 1 + len(areas) + offset
        style.append(("SPAN", (column, 0), (column, 2)))
    conditions_map = layout.get("conditions") or {}
    for index, row in enumerate(form["rows"]):
        line = [p(row["item"], head)] + [p(cell["text"]) for cell in row["cells"]]
        line += [
            p(conditions_map.get(key, ""), head) if index == 0 else ""
            for key in ("method", "frequency")[: len(extra_columns)]
        ]
        data.append(line)
        for column, cell in enumerate(row["cells"], start=1):
            if cell["changed"]:
                style.append(
                    (
                        "BACKGROUND",
                        (column, 3 + index),
                        (column, 3 + index),
                        colors.HexColor("#" + CHANGED),
                    )
                )
    if form["rows"]:
        for offset in range(len(extra_columns)):
            column = 1 + len(areas) + offset
            style.append(("SPAN", (column, 3), (column, 2 + len(form["rows"]))))
    table = Table(data, colWidths=widths, repeatRows=3)
    table.setStyle(TableStyle(style))
    story += [table, Spacer(1, 6), p(WORDS["changed_legend"]), Spacer(1, 10)]
    history = [[p(label, head) for label in WORDS["history_columns"]]]
    history += [
        [p(value) for value in entry]
        for entry in form["history"] or [["1", "", "", "", "", "", ""]]
    ]
    history_table = Table(
        history, colWidths=[30, 70, page_width - 30 - 70 - 80 * 4, 80, 80, 80, 80]
    )
    history_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E7E9E7")),
            ]
        )
    )
    story += [p(WORDS["history_title"], head), history_table]
    SimpleDocTemplate(
        str(path),
        pagesize=landscape(A3),
        leftMargin=30,
        rightMargin=30,
        topMargin=24,
        bottomMargin=24,
    ).build(story)
