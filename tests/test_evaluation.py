from decimal import Decimal

import pytest
from pydantic import ValidationError

from spec_agent.schemas.inspection import Observation
from spec_agent.schemas.standard import Rule
from spec_agent.services.evaluation import evaluate, evaluate_rule, overall
from spec_agent.services.standards import demo_standard


@pytest.mark.parametrize(
    "height,expected,warning",
    [
        ("0", "PASS", False),
        ("0.10", "PASS", False),
        ("0.190", "PASS", True),
        ("0.195", "PASS", True),
        ("0.20", "FAIL", True),
        ("0.21", "FAIL", True),
        ("0.211", "FAIL", False),
        (None, "REVIEW", False),
    ],
)
def test_exact_boundaries(height, expected, warning):
    result = evaluate_rule(
        demo_standard().rules[0],
        Observation(measurements={"height": height}, units={"height": "mm"}),
    )
    assert result.auto_result == expected
    assert result.warning is warning
    if height == "0.20":
        assert "equality is not permitted" in result.explanation


def test_compound_missing_and_units():
    rule = demo_standard().rules[1]
    result = evaluate_rule(
        rule,
        Observation(
            measurements={"depth": "0.3", "diameter": "0.79"},
            units={"depth": "mm", "diameter": "mm"},
        ),
    )
    assert result.auto_result == "PASS"
    result = evaluate_rule(rule, Observation(measurements={"depth": "0.9"}, units={"depth": "mm"}))
    assert result.auto_result == "REVIEW"
    assert "above" in result.explanation
    result = evaluate_rule(
        rule,
        Observation(
            measurements={"depth": "0.1", "diameter": "0.2"},
            units={"depth": "µm", "diameter": "mm"},
        ),
    )
    assert result.auto_result == "REVIEW"


@pytest.mark.parametrize("present,expected", [(True, "FAIL"), (False, "PASS"), (None, "REVIEW")])
def test_prohibited(present, expected):
    assert (
        evaluate_rule(demo_standard().rules[2], Observation(defect_present=present)).auto_result
        == expected
    )


def test_na_never_means_pass():
    result = evaluate_rule(demo_standard().rules[3], Observation())
    assert result.auto_result == "N_A"
    assert overall([result]) == "REVIEW"
    assert overall([]) == "REVIEW"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-0.1", True, "not-a-number", "0.0000000001"])
def test_invalid_measurements(value):
    with pytest.raises(ValidationError):
        Observation(measurements={"height": value})


def test_unknown_fields_rejected():
    with pytest.raises(ValueError, match="measurement field"):
        evaluate(demo_standard(), {"burr": Observation(measurements={"wrong": Decimal(1)})})
    with pytest.raises(ValueError, match="inspection item"):
        evaluate(demo_standard(), {"unknown": Observation()})


def test_unresolved_standard():
    rule = Rule(
        id="unknown", area="외측면", item="모호한 기준", kind="UNRESOLVED", source="검토 중"
    )
    assert evaluate_rule(rule, Observation()).auto_result == "REVIEW"
