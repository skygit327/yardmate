"""Revision agent: turn a chat message or request document into proposed standard changes.

The agent never edits a standard directly. It returns structured operations that are
validated, previewed as a diff, and only saved as a DRAFT version after a person applies
them. A DRAFT becomes usable only after a person activates it.

Two interpreters are available:
- ``RuleInterpreter``: offline, deterministic Korean/English pattern matching for common
  requests (limit changes, operators, warning margins, prohibited/not-applicable, removal).
- ``LLMInterpreter``: an optional model adapter (Anthropic or any OpenAI-compatible
  endpoint such as Ollama). It falls back to the rule interpreter when unavailable.
"""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import ValidationError

from spec_agent.config import LLMSettings
from spec_agent.schemas.revision import Operation
from spec_agent.schemas.standard import Condition, Rule, Standard
from spec_agent.services.versioning import (
    apply_operations,
    diff_standards,
    next_revision_label,
    percent_of,
    summarize_diff,
)

# Korean/English vocabulary lives in a data file so new customer terms can be added without
# code changes (and so application-owned Python text stays English).
LEXICON = json.loads((Path(__file__).with_name("revision_lexicon.json")).read_text("utf-8"))
KEYWORD_PAIRS: list[tuple[str, str]] = [tuple(pair) for pair in LEXICON["keyword_pairs"]]
FIELD_SYNONYMS: dict[str, list[str]] = LEXICON["field_synonyms"]
OPERATOR_WORDS: list[tuple[str, str]] = [tuple(pair) for pair in LEXICON["operators"]]
PATTERNS = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in LEXICON["patterns"].items()
    if not name.startswith("_")
}
HANGUL_SYLLABLE = r"[\uac00-\ud7a3]"

NUMBER = re.compile(r"(?<![\w\-./])(\d+(?:\.\d+)?)(?![\d\-/])\s*(mm|µm|μm|um|%)?", re.IGNORECASE)
WARNING_WORDS = PATTERNS["warning"]
REMOVE_WORDS = PATTERNS["remove"]
ADD_WORDS = PATTERNS["add"]
PROHIBIT_WORDS = PATTERNS["prohibit"]
NA_WORDS = PATTERNS["not_applicable"]
ALL_WORDS = PATTERNS["all_areas"]
CLEAR_WARNING = PATTERNS["clear_warning"]
REVISION_LABEL = PATTERNS["revision_label"]
REVISION_ONLY = PATTERNS["revision_only"]
DOCUMENT_NUMBER = PATTERNS["document_number"]
ARROW = PATTERNS["arrow"]
ARROW_FROM_TO = PATTERNS["arrow_from_to"]
SEGMENT_SPLIT = PATTERNS["segment_split"]
CLAUSE_SPLIT = PATTERNS["clause_split"]
LIST_MARKER = re.compile(LEXICON["patterns"]["list_marker"])


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    unit = unit.lower()
    return "µm" if unit in {"µm", "μm", "um"} else "mm" if unit == "mm" else unit


def _keyword_present(keyword: str, text: str) -> bool:
    if re.fullmatch(HANGUL_SYLLABLE, keyword):
        # A single Hangul syllable keyword (e.g. the Korean word for burr) must not match
        # the tail of a longer word (e.g. the Korean word for overflow).
        return re.search(rf"(?<!{HANGUL_SYLLABLE}){keyword}", text) is not None
    if re.fullmatch(r"[a-z0-9\- ]+", keyword):
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
    return keyword in text


def expand(text: str) -> str:
    lowered = text.lower()
    extra = []
    for korean, english in KEYWORD_PAIRS:
        if _keyword_present(korean, lowered):
            extra.append(english)
        if _keyword_present(english, lowered):
            extra.append(korean)
    return lowered + " " + " ".join(extra)


def tokens(value: str) -> list[str]:
    return [
        token
        for token in re.split(r"[\W_]+", value.lower())
        if len(token) >= 2 or re.fullmatch(HANGUL_SYLLABLE, token)
    ]


def split_requests(text: str) -> list[str]:
    parts = re.split(r"\n|;|(?<=[^0-9])[.。]\s+", text)
    lines = []
    for part in parts:
        part = LIST_MARKER.sub("", part).strip(" .\t")
        if part:
            lines.append(part)
    return lines


def split_segments(line: str) -> list[str]:
    return [segment.strip() for segment in SEGMENT_SPLIT.split(line) if segment.strip()]


def condition_for(segment: str, rule: Rule) -> Condition | None:
    lowered = segment.lower()
    for condition in rule.conditions:
        names = {condition.field.lower(), condition.label.lower()}
        names.update(FIELD_SYNONYMS.get(condition.field, []))
        if any(_keyword_present(name, lowered) for name in names):
            return condition
    return None


def new_field_in(segment: str) -> tuple[str, str] | None:
    lowered = segment.lower()
    for field_name, names in FIELD_SYNONYMS.items():
        for name in names:
            if _keyword_present(name, lowered):
                return field_name, name
    return None


def operator_near(segment: str, number_end: int) -> str | None:
    tail = segment[number_end : number_end + 32].lower()
    head = segment[max(0, number_end - 24) : number_end].lower()
    for pattern, operator in OPERATOR_WORDS:
        if re.match(rf"\s*(?:mm|µm|μm|um)?\s*(?:{pattern})", tail):
            return operator
    for pattern, operator in OPERATOR_WORDS:
        if re.search(rf"(?:{pattern})\s*$", head.split(" ")[-1] if head else ""):
            return operator
        if re.search(rf"(?:{pattern})\s*\d+(?:\.\d+)?\s*$", head):
            return operator
    return None


def target_number(segment: str):
    """Pick the new value: the number after an arrow or 'from ... to', else the last number."""
    numbers = list(NUMBER.finditer(segment))
    if not numbers:
        return None
    arrow = ARROW.search(segment)
    if arrow:
        after = [match for match in numbers if match.start() > arrow.end()]
        if after:
            return after[-1] if ARROW_FROM_TO.fullmatch(arrow.group()) else after[0]
    return numbers[-1]


@dataclass
class Interpretation:
    operations: list[Operation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    revision: str | None = None
    summary: str = ""
    interpreter: str = "rules"
    unchanged: bool = False


class RuleInterpreter:
    name = "rules"

    def interpret(self, standard: Standard, text: str) -> Interpretation:
        result = Interpretation(interpreter=self.name)
        revision = REVISION_LABEL.search(text)
        if revision:
            result.revision = revision.group(1)
        for line in split_requests(text):
            for clause in self._clause_groups(standard, line):
                self._line(standard, clause, result)
        return result

    def _mentions_item(self, standard: Standard, text: str) -> bool:
        expanded = expand(text)
        return any(
            _keyword_present(token, expanded)
            for rule in standard.rules
            for token in set(tokens(rule.item) + tokens(rule.id))
        )

    def _clause_groups(self, standard: Standard, line: str) -> list[str]:
        """Split 'change A and B' into one request per inspection item.

        Clauses without their own item (e.g. a second dimension of the same defect)
        stay attached to the preceding item.
        """
        groups: list[list[str]] = []
        for clause in (part.strip() for part in CLAUSE_SPLIT.split(line)):
            if not clause:
                continue
            if not groups or self._mentions_item(standard, clause):
                groups.append([clause])
            else:
                groups[-1].append(clause)
        return [", ".join(group) for group in groups] or [line]

    def _targets(self, standard: Standard, line: str, result: Interpretation) -> list[Rule]:
        expanded = expand(line)
        scored = []
        for rule in standard.rules:
            item_tokens = tokens(rule.item) + tokens(rule.id)
            area_tokens = tokens(rule.area)
            item_score = sum(1 for token in set(item_tokens) if _keyword_present(token, expanded))
            area_score = sum(1 for token in set(area_tokens) if _keyword_present(token, expanded))
            if item_score:
                scored.append((item_score, area_score, rule))
        if not scored:
            return []
        best_item = max(score for score, _, _ in scored)
        candidates = [(area, rule) for score, area, rule in scored if score == best_item]
        best_area = max(area for area, _ in candidates)
        narrowed = [rule for area, rule in candidates if area == best_area]
        if len(narrowed) > 1 and not ALL_WORDS.search(line):
            names = ", ".join(f"{rule.area} / {rule.item}" for rule in narrowed)
            result.notes.append(
                f'Ambiguous item: "{line}" (candidates: {names}). '
                "Name the area or say 'all areas'."
            )
            return []
        return narrowed

    def _line(self, standard: Standard, line: str, result: Interpretation):
        document = DOCUMENT_NUMBER.search(line)
        if document:
            result.operations.append(
                Operation(
                    op="set_header",
                    field="document_number",
                    values={"value": document.group(1)},
                    reason=line,
                )
            )
            return
        rules = self._targets(standard, line, result)
        if not rules:
            if any(note.startswith(f'Ambiguous item: "{line}"') for note in result.notes):
                return
            if REVISION_ONLY.fullmatch(line.strip()):
                return
            if ADD_WORDS.search(line):
                result.notes.append(
                    "Adding a new inspection item requires the LLM interpreter "
                    f'or a structured JSON import: "{line}"'
                )
            else:
                result.notes.append(f'No matching item: "{line}"')
            return
        before = len(result.operations)
        result.unchanged = False
        broad = len(rules) > 1
        for rule in rules:
            if broad and rule.kind != "NUMERIC":
                # "All areas" never relaxes a prohibited / not-applicable area implicitly.
                result.notes.append(
                    f"Skipped {rule.area} / {rule.item} ({rule.kind}). "
                    "Name the area explicitly to change it."
                )
                continue
            self._rule_changes(rule, line, result)
        if len(result.operations) == before:
            if result.unchanged:
                result.notes.append(f'Already matches the current standard: "{line}"')
            else:
                result.notes.append(f'Could not interpret: "{line}"')

    def _rule_changes(self, rule: Rule, line: str, result: Interpretation):
        add = result.operations.append
        if NA_WORDS.search(line):
            if rule.kind != "NOT_APPLICABLE":
                add(
                    Operation(
                        op="update_rule",
                        rule_id=rule.id,
                        values={"kind": "NOT_APPLICABLE"},
                        reason=line,
                    )
                )
            else:
                result.unchanged = True
            return
        has_numbers = NUMBER.search(line) is not None
        if PROHIBIT_WORDS.search(line) and not has_numbers:
            if rule.kind != "PROHIBITED":
                add(
                    Operation(
                        op="update_rule",
                        rule_id=rule.id,
                        values={"kind": "PROHIBITED"},
                        reason=line,
                    )
                )
            else:
                result.unchanged = True
            return
        if REMOVE_WORDS.search(line) and not WARNING_WORDS.search(line):
            condition = condition_for(line, rule) if rule.kind == "NUMERIC" else None
            if condition and len(rule.conditions) > 1:
                add(
                    Operation(
                        op="remove_condition", rule_id=rule.id, field=condition.field, reason=line
                    )
                )
            else:
                add(Operation(op="remove_rule", rule_id=rule.id, reason=line))
            return
        if rule.kind != "NUMERIC" and has_numbers:
            # e.g. a prohibited defect becomes allowed up to a size limit.
            pending = []
            for segment in split_segments(line):
                found = new_field_in(segment)
                number = target_number(segment)
                if not found or not number or number.group(2) == "%":
                    continue
                field_name, label = found
                pending.append(
                    Condition(
                        field=field_name,
                        label=label.capitalize() if label.isascii() else label,
                        operator=operator_near(segment, number.end()) or "lt",
                        limit=Decimal(number.group(1)),
                        unit=normalize_unit(number.group(2)) or "mm",
                    )
                )
            if pending:
                for condition in pending:
                    add(
                        Operation(
                            op="add_condition", rule_id=rule.id, condition=condition, reason=line
                        )
                    )
            return
        last_condition = None
        for segment in split_segments(line):
            if (
                WARNING_WORDS.search(segment)
                and condition_for(segment, rule) is None
                and len(rule.conditions) > 1
            ):
                for condition in rule.conditions:
                    self._warning(rule, condition, segment, line, result)
                continue
            condition = condition_for(segment, rule)
            if condition is None and len(rule.conditions) == 1:
                condition = rule.conditions[0]
            if condition is None:
                condition = last_condition
            number = target_number(segment)
            if condition is None:
                found = new_field_in(segment)
                if found and number and ADD_WORDS.search(line) and number.group(2) != "%":
                    field_name, label = found
                    add(
                        Operation(
                            op="add_condition",
                            rule_id=rule.id,
                            reason=line,
                            condition=Condition(
                                field=field_name,
                                label=label.capitalize() if label.isascii() else label,
                                operator=operator_near(segment, number.end()) or "lt",
                                limit=Decimal(number.group(1)),
                                unit=normalize_unit(number.group(2)) or "mm",
                            ),
                        )
                    )
                continue
            last_condition = condition
            if WARNING_WORDS.search(segment):
                self._warning(rule, condition, segment, line, result)
                continue
            if number is None or number.group(2) == "%":
                continue
            values: dict[str, str | None] = {}
            limit = Decimal(number.group(1))
            if limit != condition.limit:
                values["limit"] = format(limit, "f")
            unit = normalize_unit(number.group(2))
            if unit and unit != condition.unit:
                values["unit"] = unit
            operator = operator_near(segment, number.end())
            if operator and operator != condition.operator:
                values["operator"] = operator
            if values:
                add(
                    Operation(
                        op="update_condition",
                        rule_id=rule.id,
                        field=condition.field,
                        values=values,
                        reason=line,
                    )
                )
            else:
                result.unchanged = True

    def _warning(self, rule, condition, segment, line, result):
        if CLEAR_WARNING.search(segment):
            if condition.warning_margin is None:
                result.unchanged = True
            else:
                result.operations.append(
                    Operation(
                        op="update_condition",
                        rule_id=rule.id,
                        field=condition.field,
                        values={"warning_margin": None},
                        reason=line,
                    )
                )
            return
        number = target_number(segment)
        if number is None:
            return
        value = Decimal(number.group(1))
        if number.group(2) == "%":
            value = percent_of(condition.limit, value)
        if value == condition.warning_margin:
            result.unchanged = True
            return
        result.operations.append(
            Operation(
                op="update_condition",
                rule_id=rule.id,
                field=condition.field,
                values={"warning_margin": format(value, "f")},
                reason=line,
            )
        )


SYSTEM_PROMPT = """You revise structured product inspection standards for a quality team.
Read the current standard (JSON) and the change request, then return ONLY a JSON object:
{"summary": str, "revision": str|null, "operations": [Operation, ...], "questions": [str, ...]}

Operation shapes (use existing rule ids and condition field names exactly):
- {"op":"update_condition","rule_id":..,"field":..,"values":{"limit"|"operator"|"unit"|"warning_margin"|"label": str|null},"reason":..}
- {"op":"add_condition","rule_id":..,"condition":{"field":"snake_case","label":..,"operator":"lt|lte|gt|gte","limit":"0.15","unit":"mm|µm","warning_margin":null},"reason":..}
- {"op":"remove_condition","rule_id":..,"field":..,"reason":..}
- {"op":"update_rule","rule_id":..,"values":{"area"|"item"|"kind"|"source": str},"reason":..}
  kind is NUMERIC, PROHIBITED, NOT_APPLICABLE or UNRESOLVED.
- {"op":"add_rule","rule":{"id":"lower-kebab","area":..,"item":..,"kind":..,"conditions":[..],"source":..},"reason":..}
- {"op":"remove_rule","rule_id":..,"reason":..}
- {"op":"set_header","field":"product_name|document_number","values":{"value":..},"reason":..}

Rules:
- Map comparison words exactly: "less than" = lt, "or less" = lte, "greater than" = gt,
  "or more" = gte (the same applies to their Korean equivalents). Keep the existing
  operator unless the request changes it.
- Numbers are decimal strings. Never convert units unless asked.
- "reason" quotes the request sentence that justifies the change.
- Do not invent changes. Put anything ambiguous (unknown area, unclear item, conflicting
  values) in "questions" instead of guessing.
- Write "summary" and "questions" in the language of the request."""


class LLMError(RuntimeError):
    pass


class LLMClient:
    """Minimal JSON-in/JSON-out chat client for Anthropic or OpenAI-compatible endpoints."""

    RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(self, settings: LLMSettings):
        self.settings = settings

    @property
    def label(self) -> str:
        return f"llm:{self.settings.provider}/{self.settings.model}"

    @property
    def external(self) -> bool:
        return self.settings.external

    @staticmethod
    def _error_detail(error: urllib.error.HTTPError) -> str:
        try:
            payload = json.loads(error.read() or b"{}")
            detail = payload.get("error", {})
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
        except (json.JSONDecodeError, AttributeError, ValueError):
            message = ""
        return f"HTTP {error.code}: {(message or error.reason or 'request failed')[:300]}"

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        data = json.dumps(body).encode()
        for attempt in range(2):
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json", **headers},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as error:
                detail = self._error_detail(error)
                if error.code in self.RETRY_STATUSES and attempt == 0:
                    time.sleep(1.5)
                    continue
                raise LLMError(detail) from None
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
                raise LLMError(str(getattr(error, "reason", error))[:300]) from None
        raise LLMError("The model did not respond.")

    def complete(self, system: str, user: str, json_mode: bool = True) -> str:
        settings = self.settings
        if settings.provider == "anthropic":
            base = (settings.base_url or "https://api.anthropic.com").rstrip("/")
            payload = self._post(
                f"{base}/v1/messages",
                {
                    "model": settings.model,
                    "max_tokens": 4000,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
                {"x-api-key": settings.api_key or "", "anthropic-version": "2023-06-01"},
            )
            try:
                return "".join(
                    block.get("text", "") for block in payload["content"] if block["type"] == "text"
                )
            except (KeyError, TypeError):
                raise LLMError("Unexpected response from the model.") from None
        base = (settings.base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
        body = {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if settings.temperature is not None:
            body["temperature"] = settings.temperature
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            payload = self._post(f"{base}/chat/completions", body, headers)
        except LLMError as error:
            # Some OpenAI-compatible servers reject JSON mode; retry once without it.
            if json_mode and "response_format" in str(error):
                return self.complete(system, user, json_mode=False)
            raise
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("Unexpected response from the model.") from None
        if not content:
            raise LLMError("The model returned an empty answer.")
        return content

    def ping(self) -> dict:
        started = time.monotonic()
        reply = parse_json_object(
            self.complete(
                'You are a connectivity check. Reply with the JSON object {"ok": true}.', "ping"
            )
        )
        return {"ok": reply.get("ok") is True, "seconds": round(time.monotonic() - started, 2)}


def parse_json_object(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LLMError("The model did not return JSON.")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise LLMError(f"The model returned invalid JSON: {error}") from None


class LLMInterpreter:
    def __init__(self, client):
        self.client = client

    @property
    def name(self) -> str:
        return self.client.label

    def interpret(self, standard: Standard, text: str) -> Interpretation:
        current = standard.model_dump(
            mode="json",
            include={
                "customer",
                "part_number",
                "product_name",
                "document_number",
                "revision",
                "rules",
            },
        )
        reply = parse_json_object(
            self.client.complete(
                SYSTEM_PROMPT,
                "Current standard:\n"
                + json.dumps(current, ensure_ascii=False)
                + "\n\nChange request:\n"
                + text,
            )
        )
        result = Interpretation(interpreter=self.name)
        result.summary = str(reply.get("summary") or "")[:2000]
        revision = reply.get("revision")
        result.revision = str(revision)[:40] if revision else None
        result.notes.extend(str(question)[:500] for question in reply.get("questions") or [])
        for index, raw in enumerate(reply.get("operations") or [], start=1):
            try:
                result.operations.append(Operation.model_validate(raw))
            except (ValidationError, InvalidOperation, TypeError) as error:
                result.notes.append(f"Operation {index} was skipped: {_brief(error)}")
        return result


def _brief(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(item["msg"] for item in error.errors()[:3])
    return str(error)


class RevisionAgent:
    def __init__(self, settings: LLMSettings, client=None):
        self.settings = settings
        self.rules = RuleInterpreter()
        if client is None and settings.provider != "none":
            client = LLMClient(settings)
        self.llm = LLMInterpreter(client) if client is not None else None

    def interpret(self, standard: Standard, text: str) -> Interpretation:
        if self.llm is not None:
            if (
                standard.provenance == "INTERNAL"
                and self.settings.external
                and not (self.settings.allow_internal)
            ):
                result = self.rules.interpret(standard, text)
                result.notes.insert(
                    0,
                    "Internal standards are not sent to an external AI service. "
                    "The rule-based interpreter was used.",
                )
                return result
            try:
                return self.llm.interpret(standard, text)
            except LLMError as error:
                result = self.rules.interpret(standard, text)
                result.notes.insert(
                    0,
                    f"The LLM interpreter failed ({str(error)[:200]}). "
                    "The rule-based interpreter was used.",
                )
                return result
        return self.rules.interpret(standard, text)

    def propose(self, standard: Standard, text: str, taken_revisions: set[str]) -> dict:
        interpretation = self.interpret(standard, text)
        operations, notes = [], list(interpretation.notes)
        # Validate each operation cumulatively so one bad change does not hide the rest.
        for operation in interpretation.operations:
            try:
                apply_operations(standard, [*operations, operation])
                operations.append(operation)
            except ValueError as error:
                notes.append(f"Operation {len(operations) + 1} was skipped: {error}")
        suggested = interpretation.revision
        if not suggested or suggested in taken_revisions:
            suggested = next_revision_label(standard.revision, taken_revisions)
        preview, diff = None, None
        if operations:
            preview = Standard.model_validate(
                {
                    **standard.model_dump(mode="json"),
                    **apply_operations(standard, operations),
                    "revision": suggested,
                }
            )
            diff = diff_standards(standard, preview)
        summary = interpretation.summary or (summarize_diff(diff) if diff else "")
        return {
            "base_id": standard.id,
            "base_revision": standard.revision,
            "base_status": standard.status,
            "suggested_revision": suggested,
            "summary": summary,
            "operations": [operation.model_dump(mode="json") for operation in operations],
            "notes": notes,
            "interpreter": interpretation.interpreter,
            "diff": diff,
            "instruction": text,
        }


__all__ = ["LLMClient", "LLMInterpreter", "RevisionAgent", "RuleInterpreter"]
