"""Read customer inspection forms and convert them into the standard structure.

Customers send their inspection criteria in their own layouts. This module recognises
two families found in the supplied samples and turns them into data the rest of the
application understands:

* ``INSPECTION_MATRIX`` - an "inspection item x surface area" table whose cells hold
  the acceptance criteria (models B and C). Each cell becomes one standard rule.
* ``LIMIT_APPROVAL`` - a limit-sample approval pack (model A): a summary table of defect
  samples, the customer's OK / NG / HOLD decision, and one detail sheet per sample.

Every rule keeps the original wording and its location so a person can check it, and
the layout is captured as a *form profile* so a standard can later be written back in
the customer's own layout (see ``customer_form.py``).

All customer vocabulary lives in ``form_lexicon.json``; nothing here is language specific.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import ValidationError

from spec_agent.schemas.standard import Rule

LEXICON = json.loads((Path(__file__).with_name("form_lexicon.json")).read_text("utf-8"))

NUMBER = r"(\d+(?:\.\d+)?)"
UNIT = r"(mm|µm)"
OPERATORS = {"<": "lt", "<=": "lte", ">": "gt", ">=": "gte"}


def _words(key: str) -> list[str]:
    return LEXICON[key]


def _alternation(words: list[str]) -> str:
    return "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))


def clean(text: str | None) -> str:
    """Collapse whitespace inside lines and normalise look-alike symbols."""
    if not text:
        return ""
    text = (
        text.replace("＞", ">")
        .replace("＜", "<")
        .replace("≤", "<=")
        .replace("≥", ">=")
        .replace("μm", "µm")
    )
    text = re.sub(r"(?<=\d)\s*um\b", " µm", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def one_line(text: str | None) -> str:
    return re.sub(r"\s+", " ", clean(text)).strip()


def _matches(text: str, words: list[str]) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    return any(re.sub(r"\s+", "", word).lower() in compact for word in words)


def _starts(text: str, words: list[str]) -> str | None:
    for word in sorted(words, key=len, reverse=True):
        if text.startswith(word):
            return word
    return None


# --- Criterion text -> rule ------------------------------------------------------------


@dataclass
class Criterion:
    kind: str
    conditions: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    confidence: str = "high"
    variant: str | None = None


class CriterionParser:
    def __init__(self):
        dimensions = LEXICON["dimensions"]
        self.dimension_of = {
            word.lower(): key for key, words in dimensions.items() for word in words
        }
        self.labels = {key: words[0] for key, words in dimensions.items()}
        names = _alternation(list(self.dimension_of))
        self.condition = re.compile(
            rf"(?:({names})\s*)?(<=|>=|<|>)\s*{NUMBER}\s*{UNIT}", re.IGNORECASE
        )
        self.within = re.compile(
            rf"(?:({names})\s*)?{NUMBER}\s*{UNIT}\s*(?:{_alternation(_words('within'))})"
        )
        relative = _alternation(_words("relative_limit"))
        # "(살두께의 8 %)" and "(8 % of wall thickness)" word orders
        self.relative = re.compile(
            rf"\(\s*(?:(?:{relative})\s*{NUMBER}\s*%|{NUMBER}\s*%\s*(?:{relative}))\s*\)",
            re.IGNORECASE,
        )
        self.allow = _words("allow")
        self.prohibit = _words("prohibit")

    def _dimension_from_item(self, item: str) -> str | None:
        for word, key in sorted(self.dimension_of.items(), key=lambda pair: -len(pair[0])):
            if word in item.lower():
                return key
        return None

    def parse(self, text: str, item: str) -> list[Criterion]:
        text = clean(text)
        if not text or text in _words("not_applicable"):
            return [Criterion("NOT_APPLICABLE")]
        lines = text.splitlines()
        variants = [line[1:].strip() for line in lines if line.startswith(">")]
        if variants:
            result = []
            for variant in variants:
                label, _, rest = variant.partition(":")
                if not rest:
                    label, rest = "", variant
                for criterion in self.parse(rest.strip(), item):
                    criterion.variant = label.strip() or None
                    result.append(criterion)
            return result
        return [self._single(" ".join(lines), item)]

    def _single(self, text: str, item: str) -> Criterion:
        lead = _starts(text, self.prohibit)
        if lead:
            remainder = text[len(lead) :].strip(" :,.")
            notes = [{"code": "CONDITION_TEXT", "params": {"text": remainder}}] if remainder else []
            return Criterion("PROHIBITED", notes=notes)
        conditions, notes, confidence = [], [], "high"
        rest = text
        for match in self.relative.finditer(text):
            percent = next(group for group in match.groups() if group)
            notes.append({"code": "RELATIVE_LIMIT", "params": {"percent": percent}})
            confidence = "medium"
        rest = self.relative.sub(" ", rest)
        found = list(self.condition.finditer(rest))
        within = list(self.within.finditer(rest))
        for match in found:
            name, operator, value, unit = match.groups()
            if name is None:
                # word order differs by language: "Phần dư cửa rót < 0.42 mm"
                name = self._dimension_word(rest[: match.start()])
            conditions.append(self._condition(name, OPERATORS[operator], value, unit, item))
        for match in within:
            name, value, unit = match.groups()
            condition = self._condition(name, "lte", value, unit, item)
            if name is None and _matches(rest, _words("below_surface")):
                condition.update(field="recess_depth", label=LEXICON["recess_label"])
            conditions.append(condition)
            if _matches(rest, _words("no_protrusion")):
                notes.append({"code": "NO_PROTRUSION", "params": {}})
            confidence = "low"
        for match in [*found, *within]:
            rest = rest.replace(match.group(0), " ")
        for word in [*self.allow, *_words("below_surface"), *_words("no_protrusion")]:
            rest = re.sub(re.escape(word), " ", rest, flags=re.IGNORECASE)
        subject = re.sub(r"\s+", "", item).lower()
        words = re.sub(r"[\s,.:;()%]+", " ", rest).split()
        leftover = " ".join(
            dict.fromkeys(word for word in words if word.lower() not in subject and len(word) > 1)
        )
        if leftover:
            notes.append({"code": "UNPARSED", "params": {"text": leftover}})
            confidence = "low"
        if not conditions:
            return Criterion("UNRESOLVED", notes=notes, confidence="low")
        seen: dict[str, int] = {}
        for condition in conditions:
            count = seen.get(condition["field"], 0) + 1
            seen[condition["field"]] = count
            if count > 1:
                condition["field"] = f"{condition['field']}_{count}"
        return Criterion("NUMERIC", conditions, notes, confidence)

    def _dimension_word(self, text: str) -> str | None:
        """The last dimension word in the text before a comparison, if any."""
        lowered, best = text.lower(), None
        for word in self.dimension_of:
            position = lowered.rfind(word)
            if position >= 0 and (best is None or position > best[0]):
                best = (position, word)
        return best[1] if best else None

    def _condition(self, name, operator, value, unit, item) -> dict:
        key = self.dimension_of.get(name.lower()) if name else None
        if key is None:
            key = self._dimension_from_item(item) or "value"
        label = self.labels.get(key, LEXICON["value_label"])
        unit = "µm" if unit.lower().endswith("m") and unit != "mm" else "mm"
        return {"field": key, "label": label, "operator": operator, "limit": value, "unit": unit}


PARSER = CriterionParser()


def criterion_text(rule: dict, original_prefix: str = "") -> str:
    """Write a rule back in the customer's wording (used when exporting)."""
    words = LEXICON["export"]
    if rule["kind"] == "PROHIBITED":
        return words["prohibited"]
    if rule["kind"] == "NOT_APPLICABLE":
        return words["not_applicable"]
    if rule["kind"] != "NUMERIC":
        return ""
    symbols = {"lt": "<", "lte": "≤", "gt": ">", "gte": "≥"}
    parts = []
    for condition in rule["conditions"]:
        limit = condition["limit"]
        try:
            limit = format(Decimal(str(limit)).normalize(), "f")
        except InvalidOperation:
            limit = str(limit)
        parts.append(
            f"{condition['label']} {symbols[condition['operator']]} {limit} {condition['unit']}"
        )
    return original_prefix + ", ".join(parts)


# --- Reading results -------------------------------------------------------------------


def _slug(item: str, row: int) -> str:
    english = re.search(r"\(([A-Za-z][A-Za-z \-]*)\)", item)
    if english:
        slug = re.sub(r"[^a-z0-9]+", "-", english.group(1).lower()).strip("-")
        if slug:
            return slug
    return f"item{row:02d}"


@dataclass
class Reading:
    document_type: str
    filename: str
    pages: int
    header: dict = field(default_factory=dict)
    title: str = ""
    rules: list[dict] = field(default_factory=list)
    conditions: dict = field(default_factory=dict)
    layout: dict | None = None
    samples: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    text: str = ""
    interpreter: str = "rules"

    def to_dict(self) -> dict:
        return {
            "document_type": self.document_type,
            "filename": self.filename,
            "pages": self.pages,
            "header": self.header,
            "title": self.title,
            "rules": self.rules,
            "conditions": self.conditions,
            "layout": self.layout,
            "samples": self.samples,
            "warnings": self.warnings,
            "interpreter": self.interpreter,
            "text_preview": self.text[:4000],
        }


def _warn(reading: Reading, code: str, level: str = "warning", **params):
    reading.warnings.append({"level": level, "code": code, "params": params})


def _open_pdf(content: bytes):
    import pdfplumber

    return pdfplumber.open(io.BytesIO(content))


def _plain_texts(content: bytes) -> list[str]:
    from pypdf import PdfReader

    try:
        return [page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages]
    except Exception:  # noqa: BLE001 - layout text is only a fallback
        return []


def read_form(filename: str, content: bytes) -> Reading:
    suffix = Path(filename).suffix.lower()
    if suffix != ".pdf":
        raise ValueError("Customer forms must be PDF files.")
    with _open_pdf(content) as pdf:
        texts = [page.extract_text() or "" for page in pdf.pages]
        all_text = "\n".join(texts)
        tables = [page.find_tables() for page in pdf.pages]
        if _matches(all_text, _words("limit_titles")) and any(
            _matches(" ".join(one_line(cell) for cell in row if cell), ["No.", "Type"])
            for page_tables in tables
            for table in page_tables
            for row in table.extract()[:3]
        ):
            reading = _read_limit_approval(pdf, filename, texts)
        else:
            plain = _plain_texts(content)
            reading = _read_matrix(pdf, filename, plain if len(plain) == len(texts) else texts)
        reading.text = all_text
        return reading


# --- Inspection matrix (models B and C) ------------------------------------------------


def _find_header_value(rows: list[list], words: list[str]) -> str | None:
    for row in rows:
        for index, cell in enumerate(row):
            if cell and one_line(cell) in words:
                for value in row[index + 1 :]:
                    if value is not None and one_line(value):
                        return one_line(value)
                return None
    return None


def _read_matrix(pdf, filename: str, texts: list[str]) -> Reading:
    reading = Reading("UNKNOWN", filename, len(pdf.pages))
    markers = LEXICON["matrix_markers"]
    for page_number, page in enumerate(pdf.pages, start=1):
        for table in page.find_tables():
            rows = table.extract()
            start = next(
                (
                    index
                    for index, row in enumerate(rows)
                    if row
                    and row[0]
                    and one_line(row[0]) in markers["item"]
                    and any(cell and one_line(cell) in markers["criteria"] for cell in row)
                ),
                None,
            )
            if start is None:
                continue
            reading.document_type = "INSPECTION_MATRIX"
            _parse_matrix(reading, page, page_number, table, rows, start, texts[page_number - 1])
            return reading
    _warn(reading, "UNKNOWN_FORMAT")
    return reading


def _parse_matrix(reading: Reading, page, page_number, table, rows, start, page_text):
    markers = LEXICON["matrix_markers"]
    header_rows = rows[:start]
    for key, words in LEXICON["header_fields"].items():
        value = _find_header_value(header_rows, words)
        if value:
            reading.header[key] = value
    for row in header_rows:
        for cell in row:
            first_line = clean(cell).splitlines()[0] if cell and clean(cell) else ""
            titles = {title.casefold() for title in LEXICON["matrix_titles"]}
            if first_line and (
                one_line(cell).casefold() in titles or first_line.casefold() in titles
            ):
                reading.title = (
                    first_line if one_line(cell).casefold() not in titles else one_line(cell)
                )
    title_row = rows[start]
    criteria_col = next(
        i for i, cell in enumerate(title_row) if cell and one_line(cell) in markers["criteria"]
    )
    method_col = next(
        (i for i, c in enumerate(title_row) if c and one_line(c) in markers["method"]), None
    )
    frequency_col = next(
        (i for i, c in enumerate(title_row) if c and one_line(c) in markers["frequency"]), None
    )
    end_col = min(i for i in (method_col, frequency_col, len(title_row)) if i is not None)
    data_start = start + 1
    while data_start < len(rows) and not (rows[data_start][0] and one_line(rows[data_start][0])):
        data_start += 1
    span_rows = rows[start + 1 : data_start]
    filled = [
        {i: one_line(row[i]) for i in range(criteria_col, end_col) if row[i] and one_line(row[i])}
        for row in span_rows
    ]
    filled = [row for row in filled if row]
    columns_row = filled[-1] if filled else {criteria_col: "Criteria"}
    group_row = filled[0] if len(filled) > 1 else {}
    column_indexes = sorted(columns_row)
    groups = []
    for index in column_indexes:
        owner = max((g for g in group_row if g <= index), default=None)
        name = group_row.get(owner, "") if owner is not None else ""
        if not groups or groups[-1]["name"] != name:
            groups.append({"name": name, "columns": []})
        groups[-1]["columns"].append(columns_row[index])
    areas = [columns_row[i] for i in column_indexes]

    layout_rows, used_ids, method, frequency = [], set(), "", ""
    review = 0
    history_words = LEXICON["history_markers"]
    for row_offset, row in enumerate(rows[data_start:], start=1):
        item = one_line(row[0]) if row[0] else ""
        if not item or item in history_words:
            break
        if method_col is not None and row[method_col] and one_line(row[method_col]):
            method = one_line(row[method_col])
        if frequency_col is not None and row[frequency_col] and one_line(row[frequency_col]):
            frequency = one_line(row[frequency_col])
        base = _slug(item, row_offset)
        cells = []
        for area_index, column in enumerate(column_indexes, start=1):
            original = clean(row[column])
            criteria = PARSER.parse(original, item)
            ids = []
            for variant_index, criterion in enumerate(criteria, start=1):
                rule_id = f"{base}-a{area_index}" + (
                    f"-v{variant_index}" if len(criteria) > 1 else ""
                )
                while rule_id in used_ids:
                    rule_id += "x"
                used_ids.add(rule_id)
                rule = _rule_dict(
                    reading, rule_id, item, areas[area_index - 1], criterion, original, page_number
                )
                if criterion.confidence != "high":
                    review += 1
                reading.rules.append(rule)
                ids.append(rule_id)
            prefix = LEXICON["export"]["allow_prefix"] if _starts(original, _words("allow")) else ""
            cells.append(
                {
                    "rule_ids": ids,
                    "variants": [criterion.variant for criterion in criteria],
                    "original": original,
                    "prefix": prefix,
                }
            )
        layout_rows.append({"item": item, "cells": cells})

    reading.conditions = _inspection_conditions(page_text)
    if method:
        reading.conditions["method"] = method
    if frequency:
        reading.conditions["frequency"] = frequency
    figure_text = " ".join(
        one_line(cell) for row in header_rows for cell in row if cell and len(one_line(cell)) > 40
    )
    _check_colors(reading, areas, figure_text)
    for key in ("customer", "part_name", "document_number", "revision"):
        if key not in reading.header:
            _warn(reading, "MISSING_HEADER", field=key)
    if review:
        _warn(reading, "NEEDS_REVIEW", level="info", count=review)

    figure_bbox = None
    for index, row in enumerate(table.rows[:start]):
        cells = [cell for cell in row.cells if cell]
        if cells and len(one_line(rows[index][0] or "")) > 40:
            xs = [c[0] for c in cells] + [c[2] for c in cells]
            ys = [c[1] for c in cells] + [c[3] for c in cells]
            figure_bbox = [min(xs), min(ys), max(xs), max(ys)]
    reading.layout = {
        "kind": "INSPECTION_MATRIX",
        "language": "ko",
        "title": reading.title,
        "header": [
            [words[0], key]
            for key, words in LEXICON["header_fields"].items()
            if key in reading.header
        ],
        "item_label": one_line(title_row[0]),
        "criteria_label": one_line(title_row[criteria_col]),
        "method_label": one_line(title_row[method_col]) if method_col is not None else None,
        "frequency_label": one_line(title_row[frequency_col])
        if frequency_col is not None
        else None,
        "groups": groups,
        "areas": areas,
        "rows": layout_rows,
        "conditions": reading.conditions,
        "page": page_number,
        "page_size": [float(page.width), float(page.height)],
        "figure_bbox": figure_bbox,
    }


def _rule_dict(reading, rule_id, item, area, criterion: Criterion, original, page_number) -> dict:
    name = f"{item} ({criterion.variant})" if criterion.variant else item
    document = reading.header.get("document_number", reading.filename)
    source = f"{document} p.{page_number} / {item} / {area}: {one_line(original) or '-'}"
    rule = {
        "id": rule_id,
        "area": area[:80],
        "item": name[:100],
        "kind": criterion.kind,
        "conditions": criterion.conditions,
        "source": source[:500],
    }
    try:
        Rule.model_validate(rule)
    except ValidationError:
        rule.update(kind="UNRESOLVED", conditions=[])
        criterion.confidence = "low"
    return {
        **rule,
        "original": original,
        "page": page_number,
        "confidence": criterion.confidence,
        "notes": criterion.notes,
    }


def _inspection_conditions(text: str) -> dict:
    flat = re.sub(r"\s+", " ", text)
    result: dict = {}
    distance = re.search(
        rf"{NUMBER}\s*~\s*{NUMBER}\s*inch\s*\(\s*{NUMBER}\s*~\s*{NUMBER}\s*cm\s*\)", flat
    )
    if distance:
        result["distance_cm"] = [distance.group(3), distance.group(4)]
        result["distance_inch"] = [distance.group(1), distance.group(2)]
    lux = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*Lux", flat, re.IGNORECASE)
    if lux:
        result["illuminance_lux"] = lux.group(1).replace(",", "")
    standard = re.search(r"(?<![A-Za-z0-9])([A-Z]{2,}-\d{3,})", flat)
    if standard:
        result["customer_standard"] = standard.group(1)
    grade = re.search(r"Class\s*(\d+)", flat)
    if grade:
        result["class"] = grade.group(1)
    return result


def _check_colors(reading: Reading, areas: list[str], drawing_text: str):
    colors = LEXICON["colors"]
    color_of = {word: key for key, words in colors.items() for word in words}
    pattern = re.compile(rf"(\S+?)\s*\(\s*({_alternation(list(color_of))})\S*\)")
    drawing = {match.group(1): match.group(2) for match in pattern.finditer(drawing_text)}
    for area in areas:
        match = pattern.search(area)
        if not match:
            continue
        base, word = match.groups()
        other = drawing.get(base)
        if other and color_of[other] != color_of[word]:
            _warn(reading, "COLOR_MISMATCH", area=area, table=word, drawing=other)


# --- Limit approval pack (model A) -----------------------------------------------------


def _read_limit_approval(pdf, filename: str, texts: list[str]) -> Reading:
    reading = Reading("LIMIT_APPROVAL", filename, len(pdf.pages))
    reading.title = next((w for w in _words("limit_titles") if _matches("\n".join(texts), [w])), "")
    samples: dict[int, dict] = {}
    model = part = ""
    decision_rows: list[list] = []
    details: dict[int, dict] = {}
    fields = LEXICON["limit_fields"]
    for page_number, page in enumerate(pdf.pages, start=1):
        for table in page.extract_tables():
            if not table:
                continue
            header = [one_line(cell) for cell in table[1]] if len(table) > 1 else []
            if "No." in header and "Type" in header:
                columns = {name: header.index(name) for name in header if name}
                for row in table[2:]:
                    number = one_line(row[0])
                    if not number.isdigit():
                        continue
                    model = one_line(row[columns.get("Model", 1)]) or model
                    part = one_line(row[columns.get("items", 2)]) or part
                    kind = one_line(row[columns["Type"]])
                    match = re.match(r"(.+?)\s*\((.+?)\)\s*-\s*\d+$", kind)
                    local, english = (match.group(1), match.group(2)) if match else (kind, "")
                    level = [one_line(row[columns[k]]) for k in ("1", "2", "3") if k in columns]
                    samples[int(number)] = {
                        "no": int(number),
                        "model": model,
                        "part": part,
                        "defect": local,
                        "defect_en": english,
                        "levels": level,
                        "sets": one_line(row[columns["Set"]]) if "Set" in columns else "",
                        "detect_process": one_line(row[columns["Detect"]])
                        if "Detect" in columns
                        else "",
                        "cause_process": one_line(row[columns["Cause"]])
                        if "Cause" in columns
                        else "",
                        "page": page_number,
                    }
            elif table[0] and [one_line(c) for c in table[0][:2]] == LEXICON["decision_header"]:
                decision_rows.extend(table[1:])
            elif table[0] and one_line(table[0][0]) in fields["code"]:
                page_text = texts[page_number - 1].strip()
                number = re.search(r"(\d+)\s+OK\s+NG$", page_text) or re.match(
                    r"(\d+)\s", page_text
                )
                if not number:
                    continue
                values = {}
                for row in table:
                    label = one_line(row[0])
                    for key, words in fields.items():
                        if label in words:
                            values[key] = clean(row[1])
                details[int(number.group(1))] = {**values, "page": page_number}
    group = None
    groups = LEXICON["decision_groups"]
    for row in decision_rows:
        if row[0]:
            label = one_line(row[0])
            group = next(
                (key for key, words in groups.items() if any(w in label for w in words)), group
            )
        numbers = re.search(r"No\.\s*([\d,\s]+)", row[1] or "")
        if not numbers or group is None:
            continue
        for value in re.findall(r"\d+", numbers.group(1)):
            sample = samples.setdefault(int(value), {"no": int(value)})
            sample["decision"] = group
            sample["decision_reason"] = clean(row[2]) if len(row) > 2 else ""
    for number, values in details.items():
        sample = samples.setdefault(number, {"no": number})
        sample["cause"] = values.get("cause", "")
        sample["countermeasures"] = [
            line for line in values.get("countermeasure", "").splitlines() if line
        ]
        sample["defect_rate"] = values.get("rate", "")
        sample["quantity"] = values.get("quantity", "")
        sample["detail_page"] = values["page"]
    reading.samples = [samples[key] for key in sorted(samples)]
    reading.header = {"model": model, "part_name": part}
    undecided = [s["no"] for s in reading.samples if not s.get("decision")]
    holds = [s["no"] for s in reading.samples if s.get("decision") == "HOLD"]
    if holds:
        _warn(reading, "LIMIT_HOLD", numbers=holds)
    if undecided:
        _warn(reading, "LIMIT_UNDECIDED", numbers=undecided)
    if not reading.samples:
        _warn(reading, "UNKNOWN_FORMAT")
    reading.layout = {"kind": "LIMIT_APPROVAL"}
    return reading


def render_figure(content: bytes, page_number: int, bbox: list[float] | None) -> bytes | None:
    """Crop the drawing area of the customer's page so exports can reuse it."""
    if not bbox:
        return None
    try:
        import pypdfium2 as pdfium
    except ImportError:  # pragma: no cover - optional
        return None
    document = pdfium.PdfDocument(content)
    page = document[page_number - 1]
    scale = 2
    image = page.render(scale=scale).to_pil()
    left, top, right, bottom = (value * scale for value in bbox)
    crop = image.crop((int(left), int(top), int(right), int(bottom)))
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    return buffer.getvalue()
