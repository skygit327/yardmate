"""Deterministic standard revision helpers: apply operations, diff versions, label revisions."""

import re
from decimal import Decimal

from spec_agent.schemas.revision import Operation
from spec_agent.schemas.standard import Condition, Rule, Standard, StandardInput

SYMBOLS = {"lt": "<", "lte": "≤", "gt": ">", "gte": "≥"}
HEADER_DIFF_FIELDS = ["customer", "part_number", "product_name", "document_number", "revision"]


def condition_text(condition: Condition | dict | None) -> str:
    if condition is None:
        return "-"
    if isinstance(condition, dict):
        condition = Condition.model_validate(condition)
    text = (
        f"{condition.label} {SYMBOLS[condition.operator]} "
        f"{format(condition.limit, 'f')} {condition.unit}"
    )
    if condition.warning_margin is not None:
        text += f" (warning ±{format(condition.warning_margin, 'f')} {condition.unit})"
    return text


def rule_criteria(rule: Rule) -> str:
    if rule.kind == "NUMERIC":
        return " AND ".join(condition_text(condition) for condition in rule.conditions)
    return {
        "PROHIBITED": "Prohibited",
        "NOT_APPLICABLE": "Not applicable",
        "UNRESOLVED": "Unresolved interpretation",
    }[rule.kind]


def _find_rule(rules: list[dict], rule_id: str) -> dict:
    for rule in rules:
        if rule["id"] == rule_id:
            return rule
    raise ValueError(f"The item '{rule_id}' does not exist in the standard.")


def _find_condition(rule: dict, field: str) -> dict:
    for condition in rule["conditions"]:
        if condition["field"] == field:
            return condition
    raise ValueError(f"The item '{rule['id']}' has no condition '{field}'.")


def apply_operations(base: Standard | StandardInput, operations: list[Operation]) -> dict:
    """Return the header and rules of a new revision. Raises ValueError on invalid changes."""
    data = base.model_dump(mode="json")
    rules: list[dict] = data["rules"]
    for index, operation in enumerate(operations, start=1):
        try:
            if operation.op == "set_header":
                data[operation.field] = operation.values["value"]
            elif operation.op == "add_rule":
                if any(rule["id"] == operation.rule.id for rule in rules):
                    raise ValueError(f"The item '{operation.rule.id}' already exists.")
                rules.append(operation.rule.model_dump(mode="json"))
            elif operation.op == "remove_rule":
                rules.remove(_find_rule(rules, operation.rule_id))
            elif operation.op == "update_rule":
                rule = _find_rule(rules, operation.rule_id)
                for key, value in operation.values.items():
                    if value is None:
                        raise ValueError(f"{key} cannot be empty.")
                    rule[key] = value
                if rule["kind"] != "NUMERIC":
                    rule["conditions"] = []
            elif operation.op == "add_condition":
                rule = _find_rule(rules, operation.rule_id)
                if any(c["field"] == operation.condition.field for c in rule["conditions"]):
                    raise ValueError(
                        f"The item '{rule['id']}' already has '{operation.condition.field}'."
                    )
                rule["kind"] = "NUMERIC"
                rule["conditions"].append(operation.condition.model_dump(mode="json"))
            elif operation.op == "remove_condition":
                rule = _find_rule(rules, operation.rule_id)
                rule["conditions"].remove(_find_condition(rule, operation.field))
            elif operation.op == "update_condition":
                condition = _find_condition(_find_rule(rules, operation.rule_id), operation.field)
                for key, value in operation.values.items():
                    if value is None and key != "warning_margin":
                        raise ValueError(f"{key} cannot be empty.")
                    condition[key] = value
        except ValueError as error:
            raise ValueError(f"Change {index}: {error}") from None
    try:
        validated = StandardInput.model_validate(
            {key: data[key] for key in StandardInput.model_fields}
        )
    except ValueError as error:
        raise ValueError(f"The revised standard is invalid: {error}") from None
    return validated.model_dump(mode="json")


def _rule_map(standard: dict | None) -> dict[str, dict]:
    return {rule["id"]: rule for rule in (standard or {}).get("rules", [])}


def diff_standards(before: Standard | None, after: Standard) -> dict:
    """Field-level comparison between two versions of a standard."""
    old = before.model_dump(mode="json") if before else None
    new = after.model_dump(mode="json")
    header = [
        {"field": key, "before": old[key], "after": new[key]}
        for key in HEADER_DIFF_FIELDS
        if old is not None and old[key] != new[key]
    ]
    old_rules, new_rules = _rule_map(old), _rule_map(new)
    rules = []
    for rule_id in [*old_rules, *[key for key in new_rules if key not in old_rules]]:
        previous, current = old_rules.get(rule_id), new_rules.get(rule_id)
        reference = current or previous
        entry = {
            "rule_id": rule_id,
            "area": reference["area"],
            "item": reference["item"],
            "before": rule_criteria(Rule.model_validate(previous)) if previous else "-",
            "after": rule_criteria(Rule.model_validate(current)) if current else "-",
            "fields": [],
            "conditions": [],
        }
        if previous is None:
            entry["change"] = "added"
        elif current is None:
            entry["change"] = "removed"
        else:
            entry["fields"] = [
                {"field": key, "before": previous[key], "after": current[key]}
                for key in ("area", "item", "kind", "source")
                if previous[key] != current[key]
            ]
            old_conditions = {c["field"]: c for c in previous["conditions"]}
            new_conditions = {c["field"]: c for c in current["conditions"]}
            for field in [
                *old_conditions,
                *[key for key in new_conditions if key not in old_conditions],
            ]:
                a, b = old_conditions.get(field), new_conditions.get(field)
                if a == b:
                    continue
                entry["conditions"].append(
                    {
                        "field": field,
                        "change": "added" if a is None else "removed" if b is None else "changed",
                        "before": condition_text(a),
                        "after": condition_text(b),
                    }
                )
            if not entry["fields"] and not entry["conditions"]:
                continue
            entry["change"] = "changed"
        rules.append(entry)
    return {
        "before_id": before.id if before else None,
        "after_id": after.id,
        "before_revision": before.revision if before else None,
        "after_revision": after.revision,
        "header": header,
        "rules": rules,
        "changed": bool(header or rules),
    }


def summarize_diff(diff: dict) -> str:
    lines = []
    for entry in diff["header"]:
        lines.append(f"{entry['field']}: {entry['before']} → {entry['after']}")
    for entry in diff["rules"]:
        name = f"{entry['area']} / {entry['item']}"
        if entry["change"] == "added":
            lines.append(f"Added {name}: {entry['after']}")
        elif entry["change"] == "removed":
            lines.append(f"Removed {name}")
        else:
            lines.append(f"{name}: {entry['before']} → {entry['after']}")
    return "\n".join(lines)


def next_revision_label(current: str, taken: set[str]) -> str:
    def bump(label: str) -> str:
        if re.fullmatch(r"[A-Y]", label) or re.fullmatch(r"[a-y]", label):
            return chr(ord(label) + 1)
        numbers = list(re.finditer(r"\d+", label))
        if numbers:
            match = numbers[-1]
            return label[: match.start()] + str(int(match.group()) + 1) + label[match.end() :]
        letter = re.search(r"(?<![A-Za-z])([A-Ya-y])$", label)
        if letter:
            return label[:-1] + chr(ord(letter.group(1)) + 1)
        return f"{label}-2"

    candidate = bump(current)
    while candidate in taken:
        candidate = bump(candidate)
    return candidate


def percent_of(limit: Decimal, percent: Decimal) -> Decimal:
    return (abs(limit) * percent / Decimal(100)).normalize()
