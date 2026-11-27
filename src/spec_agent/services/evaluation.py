from decimal import Decimal
from operator import ge, gt, le, lt

from spec_agent.schemas.inspection import ItemResult, Observation
from spec_agent.schemas.standard import Rule, Standard

OPERATORS = {"lt": lt, "lte": le, "gt": gt, "gte": ge}
SYMBOLS = {"lt": "<", "lte": "≤", "gt": ">", "gte": "≥"}


def number(value: Decimal) -> str:
    return format(value, "f")


def evaluate_rule(rule: Rule, observation: Observation) -> ItemResult:
    base = {"rule_id": rule.id, "area": rule.area, "item": rule.item, "source": rule.source}
    if rule.kind == "NOT_APPLICABLE":
        return ItemResult(
            **base,
            auto_result="N_A",
            criteria="Not applicable",
            measured="-",
            explanation="This item does not apply and is excluded from pass counts.",
        )
    if rule.kind == "UNRESOLVED":
        return ItemResult(
            **base,
            auto_result="REVIEW",
            criteria="Unresolved interpretation",
            measured="-",
            explanation="Resolve the interpretation of the criteria first. This revision cannot be approved.",
        )
    if rule.kind == "PROHIBITED":
        present = observation.defect_present
        return ItemResult(
            **base,
            auto_result="REVIEW" if present is None else "FAIL" if present else "PASS",
            criteria="Defects are prohibited",
            measured="Not entered" if present is None else "Present" if present else "Absent",
            explanation="Specify whether the defect is present."
            if present is None
            else "A defect was observed and is not permitted."
            if present
            else "No defect was observed.",
        )
    criteria, measured, missing, violations = [], [], [], []
    warning = False
    for condition in rule.conditions:
        criteria.append(
            f"{condition.label} {SYMBOLS[condition.operator]} {condition.limit} {condition.unit}"
        )
        value = observation.measurements.get(condition.field)
        unit = observation.units.get(condition.field)
        if value is None or unit != condition.unit:
            missing.append(f"{condition.label}: Missing value or unit mismatch")
            measured.append(f"{condition.label}: Not verified")
            continue
        measured.append(f"{condition.label} {number(value)} {unit}")
        if condition.warning_margin is not None:
            warning |= abs(value - condition.limit) <= condition.warning_margin
        if not OPERATORS[condition.operator](value, condition.limit):
            difference = abs(value - condition.limit)
            if difference == 0:
                violations.append(
                    f"{condition.label}: The value equals the boundary, but equality is not permitted."
                )
            else:
                direction = "above" if condition.operator in {"lt", "lte"} else "below"
                violations.append(
                    f"{condition.label}: The value is {number(difference)} {unit} {direction} the limit."
                )
    result = "REVIEW" if missing else "FAIL" if violations else "PASS"
    explanation = " ".join(missing + violations) or "The input satisfies all conditions."
    return ItemResult(
        **base,
        auto_result=result,
        warning=warning,
        criteria=" AND ".join(criteria),
        measured=", ".join(measured),
        explanation=explanation,
    )


def evaluate(standard: Standard, observations: dict[str, Observation]) -> list[ItemResult]:
    allowed = {rule.id for rule in standard.rules}
    if set(observations) - allowed:
        raise ValueError("An inspection item is not defined in the standard.")
    for rule in standard.rules:
        observation = observations.get(rule.id, Observation())
        fields = {condition.field for condition in rule.conditions}
        if (set(observation.measurements) | set(observation.units)) - fields:
            raise ValueError(f"{rule.item}: A measurement field is not defined in the standard.")
    return [
        evaluate_rule(rule, observations.get(rule.id, Observation())) for rule in standard.rules
    ]


def overall(results: list[ItemResult], final: bool = False) -> str:
    applicable = [result for result in results if result.auto_result != "N_A"]
    values = [result.final_result if final else result.auto_result for result in applicable]
    if not values or any(value in {None, "REVIEW"} for value in values):
        return "REVIEW"
    return "FAIL" if "FAIL" in values else "PASS"
