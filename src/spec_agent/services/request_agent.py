"""Customer request agent: read a request, link it to our records, and draft a response.

Deterministic steps (always offline):
- clean the mail body (quotes, signatures), classify the request, find priority words
- detect customers, part numbers, lots and inspection items mentioned
- link related active standards and inspection records, and compute findings
- propose actions and assemble a reply draft from templates (en / ko / vi)

Optional LLM step: when a model is configured (and allowed for customer data), it rewrites
the summary, key points and reply draft. Links and findings always come from our records.
A person reviews, edits and sends every reply; nothing is sent automatically.
"""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from spec_agent.config import LLMSettings
from spec_agent.schemas.customer_request import CustomerRequest
from spec_agent.schemas.standard import Standard
from spec_agent.services.revision_agent import (
    NUMBER,
    LLMClient,
    LLMError,
    _keyword_present,
    expand,
    normalize_unit,
    parse_json_object,
    tokens,
)
from spec_agent.services.versioning import rule_criteria

LEXICON = json.loads(Path(__file__).with_name("request_lexicon.json").read_text("utf-8"))
CATEGORY_PATTERNS = {
    name: re.compile(pattern, re.IGNORECASE) for name, pattern in LEXICON["categories"].items()
}
CATEGORY_ORDER = ["COMPLAINT", "SPEC_CHANGE", "REPORT_REQUEST", "DELIVERY", "INQUIRY"]
URGENT = re.compile(LEXICON["urgent"], re.IGNORECASE)
REQUEST_VERBS = re.compile(LEXICON["request_verbs"], re.IGNORECASE)
QUOTE = re.compile(LEXICON["quote_markers"], re.IGNORECASE)
SIGNATURE = re.compile(LEXICON["signature_markers"], re.IGNORECASE)
GREETING = re.compile(LEXICON["greetings"], re.IGNORECASE)
GENERIC_TOKENS = set(LEXICON["generic_tokens"])
LOT_PATTERN = re.compile(LEXICON["lot_pattern"], re.IGNORECASE)


def now() -> str:
    return datetime.now(UTC).isoformat()


def clean_body(text: str) -> str:
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if QUOTE.match(stripped):
            break
        if SIGNATURE.match(stripped) and lines:
            break
        lines.append(line.rstrip())
    return "\n".join(lines).strip() or text.strip()


def sentences(text: str) -> list[str]:
    parts = re.split(r"\n+|(?<=[.!?。])\s+", text)
    return [part.strip(" -*•\t") for part in parts if len(part.strip()) > 3]


def classify(text: str) -> str:
    scores = {name: len(pattern.findall(text)) for name, pattern in CATEGORY_PATTERNS.items()}
    best = max(scores.values())
    if not best:
        return "OTHER"
    return next(name for name in CATEGORY_ORDER if scores[name] == best)


def key_points(text: str, limit: int = 3) -> list[str]:
    candidates = sentences(text)
    scored = []
    for index, sentence in enumerate(candidates):
        if GREETING.match(sentence) or (len(sentence) < 40 and sentence.endswith(",")):
            continue
        score = 2 * bool(REQUEST_VERBS.search(sentence))
        score += sum(1 for pattern in CATEGORY_PATTERNS.values() if pattern.search(sentence))
        score += len(NUMBER.findall(sentence)) > 0
        scored.append((score, index, sentence))
    chosen = sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]
    return [sentence[:300] for _, _, sentence in sorted(chosen, key=lambda item: item[1])]


def word_in(needle: str, haystack: str) -> bool:
    needle = needle.strip().lower()
    if not needle:
        return False
    return re.search(rf"(?<![\w-]){re.escape(needle)}(?![\w-])", haystack.lower()) is not None


class RequestAgent:
    def __init__(self, settings: LLMSettings, client=None):
        self.settings = settings
        if client is None and settings.provider != "none":
            client = LLMClient(settings)
        self.client = client

    # --- deterministic analysis ----------------------------------------------------------

    def analyze(
        self,
        request: CustomerRequest,
        standards: list[Standard],
        inspections: list[dict],
        language: str,
    ) -> dict:
        body = clean_body(request.body)
        text = f"{request.subject}\n{body}"
        known_customers = sorted(
            {standard.customer for standard in standards}, key=len, reverse=True
        )
        customers = [request.customer] if request.customer else []
        if not customers:
            sender = request.sender.lower()
            customers = [
                name
                for name in known_customers
                if word_in(name, text) or any(token in sender for token in tokens(name))
            ][:3]
        parts = list(request.part_numbers)
        if not parts:
            for standard in standards:
                if (not customers or standard.customer in customers) and (
                    word_in(standard.part_number, text) or word_in(standard.product_name, text)
                ):
                    parts.append(standard.part_number)
            parts = list(dict.fromkeys(parts))
        related = self._related_standards(standards, customers, parts)
        lots, unknown_lots = self._lots(text, inspections)
        related_inspections = [
            self._inspection_summary(item, matched=True)
            for item in inspections
            if item["inputs"]["lot"] in lots
        ]
        if not related_inspections and parts:
            recent = [
                item
                for item in inspections
                if item["standard"]["part_number"] in parts
                and (not customers or item["standard"]["customer"] in customers)
            ][:3]
            related_inspections = [self._inspection_summary(item, matched=False) for item in recent]
        category = classify(text)
        items = self._items(text, related)
        findings = self._findings(
            text, category, items, related_inspections, unknown_lots, language
        )
        missing = []
        if not parts:
            missing.append("missing_part")
        if category == "COMPLAINT" and not lots:
            missing.append("missing_lot")
        if category == "COMPLAINT" and not any(
            NUMBER.search(sentence) for sentence in sentences(body)
        ):
            missing.append("missing_values")
        points = key_points(body or request.subject)
        analysis = {
            "summary": " / ".join(points[:2]) if points else request.subject,
            "key_points": points,
            "category": category,
            "priority": "HIGH" if URGENT.search(text) else "NORMAL",
            "mentions": {
                "customers": customers,
                "parts": parts,
                "lots": lots + unknown_lots,
                "items": [
                    {key: value for key, value in item.items() if key != "rule"} for item in items
                ],
            },
            "related_standards": [self._standard_summary(standard) for standard in related],
            "related_inspections": related_inspections,
            "findings": findings,
            "missing": missing,
            "interpreter": "rules",
            "notes": [],
            "language": language,
            "generated_at": now(),
        }
        analysis["proposed_actions"] = self._actions(analysis, items, body, language)
        analysis["response_draft"] = self.reply(request, analysis, items, language)
        return analysis

    def _related_standards(self, standards, customers, parts) -> list[Standard]:
        selected = []
        for standard in standards:
            if standard.status != "ACTIVE":
                continue
            if customers and standard.customer not in customers:
                continue
            if parts and standard.part_number not in parts:
                continue
            if not customers and not parts:
                continue
            selected.append(standard)
        return selected

    def _lots(self, text: str, inspections: list[dict]) -> tuple[list[str], list[str]]:
        known = {item["inputs"]["lot"] for item in inspections}
        matched = [lot for lot in sorted(known, key=len, reverse=True) if word_in(lot, text)]
        unknown = []
        for match in LOT_PATTERN.finditer(text):
            candidate = match.group(1).rstrip(".,;)")
            if (
                not any(candidate.lower() == lot.lower() for lot in matched)
                and not any(candidate.lower() == lot.lower() for lot in unknown)
                and any(character.isdigit() for character in candidate)
            ):
                unknown.append(candidate)
        return matched, unknown

    @staticmethod
    def _inspection_summary(item: dict, matched: bool) -> dict:
        return {
            "id": item["inspection_id"],
            "lot": item["inputs"]["lot"],
            "date": item["inputs"].get("inspection_date") or item["created_at"][:10],
            "overall_result": item["overall_result"],
            "status": item["status"],
            "part_number": item["standard"]["part_number"],
            "revision": item["standard"]["revision"],
            "matched": matched,
        }

    @staticmethod
    def _standard_summary(standard: Standard) -> dict:
        return {
            "id": standard.id,
            "lineage": standard.lineage,
            "customer": standard.customer,
            "part_number": standard.part_number,
            "product_name": standard.product_name,
            "document_number": standard.document_number,
            "revision": standard.revision,
            "status": standard.status,
        }

    def _items(self, text: str, related: list[Standard]) -> list[dict]:
        expanded = expand(text)
        found = []
        for standard in related:
            for rule in standard.rules:
                distinctive = set(tokens(rule.item)) - GENERIC_TOKENS
                if not any(_keyword_present(token, expanded) for token in distinctive):
                    continue
                area_hit = any(_keyword_present(token, expanded) for token in tokens(rule.area))
                found.append(
                    {
                        "standard_id": standard.id,
                        "part_number": standard.part_number,
                        "rule_id": rule.id,
                        "area": rule.area,
                        "item": rule.item,
                        "criteria": rule_criteria(rule),
                        "area_mentioned": area_hit,
                        "rule": rule,
                    }
                )
        # Prefer items whose area is named when the same item exists in several areas.
        if any(item["area_mentioned"] for item in found):
            names = {
                (item["standard_id"], item["item"]) for item in found if item["area_mentioned"]
            }
            found = [
                item
                for item in found
                if item["area_mentioned"] or (item["standard_id"], item["item"]) not in names
            ]
        return found[:12]

    def _findings(self, text, category, items, inspections, unknown_lots, language) -> list[str]:
        templates = LEXICON["findings"][language]
        english = LEXICON["findings"]["en"]
        findings = []

        def add(code, **params):
            entry = {
                "code": code,
                "template": english[code],
                "params": params,
                "text": templates[code].format(**params),
            }
            if all(entry["text"] != other["text"] for other in findings):
                findings.append(entry)

        if category in {"SPEC_CHANGE", "INQUIRY"}:
            for item in items:
                rule = item["rule"]
                if rule.kind != "NUMERIC":
                    continue
                for sentence in sentences(text):
                    distinctive = set(tokens(rule.item)) - GENERIC_TOKENS
                    if not any(_keyword_present(token, expand(sentence)) for token in distinctive):
                        continue
                    for match in NUMBER.finditer(sentence):
                        unit = normalize_unit(match.group(2))
                        if unit not in {"mm", "µm"}:
                            continue
                        value = Decimal(match.group(1))
                        if all(value != condition.limit for condition in rule.conditions):
                            add(
                                "value_differs",
                                item=f"{item['area']} / {item['item']}",
                                value=match.group(1),
                                unit=unit,
                                criteria=item["criteria"],
                            )
                            break
                    else:
                        continue
                    break
        for inspection in inspections:
            if not inspection["matched"]:
                continue
            if inspection["overall_result"] == "FAIL":
                add(
                    "inspection_fail", lot=inspection["lot"], result="FAIL", date=inspection["date"]
                )
            elif category == "COMPLAINT" and inspection["overall_result"] == "PASS":
                add("inspection_pass_but_complaint", lot=inspection["lot"], date=inspection["date"])
        for lot in unknown_lots:
            add("unknown_lot", lot=lot)
        return findings

    def _actions(self, analysis: dict, items: list[dict], body: str, language: str) -> list[dict]:
        text, english = LEXICON["actions"][language], LEXICON["actions"]["en"]
        category = analysis["category"]
        item_names = ", ".join(f"{item['area']} / {item['item']}" for item in items) or "-"
        actions = []

        def add(kind, template, params=None, **extra):
            params = params or {}
            values = {
                key: ", ".join(text[code] for code in value) if isinstance(value, list) else value
                for key, value in params.items()
            }
            actions.append(
                {
                    "kind": kind,
                    "label": text[template].format(**values),
                    "template": english[template],
                    "params": params,
                    **extra,
                }
            )

        if category == "SPEC_CHANGE":
            for standard in analysis["related_standards"]:
                add(
                    "open_revision",
                    "open_revision",
                    {"part": standard["part_number"]},
                    target_id=standard["id"],
                    instruction="\n".join(analysis["key_points"]) or body[:2000],
                )
        for inspection in analysis["related_inspections"]:
            if inspection["matched"] or category == "COMPLAINT":
                add(
                    "review_inspection",
                    "review_inspection",
                    {"lot": inspection["lot"], "result": inspection["overall_result"]},
                    target_id=inspection["id"],
                )
        if category == "COMPLAINT":
            matched = [item for item in analysis["related_inspections"] if item["matched"]]
            for inspection in matched:
                add(
                    "reinspect",
                    "reinspect",
                    {"lot": inspection["lot"], "items": item_names},
                    target_id=inspection["id"],
                )
            if not matched:
                add("reinspect", "reinspect_generic", {"items": item_names})
        if category == "INQUIRY" and items:
            add("check_standard", "check_standard", {"items": item_names})
        if category == "REPORT_REQUEST":
            for inspection in analysis["related_inspections"]:
                if inspection["status"] == "APPROVED":
                    add(
                        "send_report",
                        "send_report",
                        {"lot": inspection["lot"]},
                        target_id=inspection["id"],
                    )
        if category == "DELIVERY":
            add("check_delivery", "check_delivery")
        if analysis["missing"]:
            add("ask_customer", "ask_customer", {"missing": list(analysis["missing"])})
        return actions

    def reply(
        self, request: CustomerRequest, analysis: dict, items: list[dict], language: str
    ) -> str:
        template = LEXICON["replies"][language]
        customer = request.customer or ", ".join(analysis["mentions"]["customers"]) or "-"
        lines = [
            template["greeting"].format(customer=customer),
            "",
            template["thanks"].format(subject=request.subject or "-", case=request.id[:8].upper()),
        ]
        if analysis["key_points"]:
            lines += ["", template["understanding"]]
            lines += [f"- {point}" for point in analysis["key_points"]]
        facts = []
        standards = {standard["id"]: standard for standard in analysis["related_standards"]}
        for item in items:
            standard = standards.get(item["standard_id"])
            if standard:
                facts.append(
                    template["standard"].format(
                        part=standard["part_number"],
                        document=standard["document_number"],
                        revision=standard["revision"],
                        item=f"{item['area']} / {item['item']}",
                        criteria=item["criteria"],
                    )
                )
        for inspection in analysis["related_inspections"]:
            if inspection["matched"]:
                facts.append(
                    template["inspection"].format(
                        lot=inspection["lot"],
                        date=inspection["date"],
                        result=inspection["overall_result"],
                        status=LEXICON["statuses"][language].get(
                            inspection["status"], inspection["status"]
                        ),
                    )
                )
        lines += ["", template["facts"]]
        lines += [f"- {fact}" for fact in facts] or [f"- {template['no_facts']}"]
        customer_actions = [
            action["label"]
            for action in analysis["proposed_actions"]
            if action["kind"] != "review_inspection"
        ]
        if customer_actions:
            lines += ["", template["actions"]]
            lines += [f"- {label}" for label in customer_actions]
        lines += ["", template["closing"], "", template["sign"]]
        return "\n".join(lines)

    # --- optional LLM refinement ---------------------------------------------------------

    def refine(self, request: CustomerRequest, analysis: dict, language: str) -> dict:
        if self.client is None:
            return analysis
        if self.settings.external and not self.settings.allow_internal:
            analysis["notes"].append(
                "Customer requests are not sent to an external AI service. "
                "The rule-based analysis was used."
            )
            return analysis
        context = {
            "request": {
                "subject": request.subject,
                "sender": request.sender,
                "customer": request.customer,
                "body": clean_body(request.body)[:12000],
            },
            "category_guess": analysis["category"],
            "related_standards": analysis["related_standards"],
            "mentioned_items": analysis["mentions"]["items"],
            "related_inspections": analysis["related_inspections"],
            "findings": analysis["findings"],
            "proposed_actions": [action["label"] for action in analysis["proposed_actions"]],
        }
        system = (
            "You support a quality assurance team that answers customer requests about product "
            "inspection standards. Use only the facts in the context; never invent inspection "
            'results, limits or dates. Return ONLY a JSON object: {"summary": str, '
            '"category": one of COMPLAINT|SPEC_CHANGE|INQUIRY|DELIVERY|REPORT_REQUEST|OTHER, '
            '"priority": LOW|NORMAL|HIGH, "key_points": [str], "response_draft": str}. '
            f"Write summary, key_points and response_draft in this language: {language}. "
            "The response draft is a polite business e-mail for a person to review before sending; "
            "it restates the request, the facts from our records and the planned actions."
        )
        try:
            reply = parse_json_object(
                self.client.complete(system, json.dumps(context, ensure_ascii=False))
            )
        except LLMError as error:
            analysis["notes"].append(
                f"The LLM analysis failed ({str(error)[:200]}). The rule-based analysis was used."
            )
            return analysis
        if isinstance(reply.get("summary"), str) and reply["summary"].strip():
            analysis["summary"] = reply["summary"][:1000]
        if isinstance(reply.get("key_points"), list):
            analysis["key_points"] = [str(point)[:300] for point in reply["key_points"][:6]]
        if reply.get("category") in {*CATEGORY_ORDER, "OTHER"}:
            analysis["category"] = reply["category"]
        if reply.get("priority") in {"LOW", "NORMAL", "HIGH"}:
            analysis["priority"] = reply["priority"]
        if isinstance(reply.get("response_draft"), str) and reply["response_draft"].strip():
            analysis["response_draft"] = reply["response_draft"][:20000]
        analysis["interpreter"] = getattr(self.client, "label", "llm")
        return analysis
