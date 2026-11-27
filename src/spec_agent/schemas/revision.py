from typing import Literal

from pydantic import Field, model_validator

from spec_agent.schemas.standard import Condition, Rule, StrictModel

OperationKind = Literal[
    "update_condition",
    "add_condition",
    "remove_condition",
    "update_rule",
    "add_rule",
    "remove_rule",
    "set_header",
]

CONDITION_FIELDS = {"label", "operator", "limit", "unit", "warning_margin"}
RULE_FIELDS = {"area", "item", "kind", "source"}
HEADER_FIELDS = {"product_name", "document_number"}


class Operation(StrictModel):
    """A single structured change to a standard.

    The interpreter (rule-based or LLM) only proposes operations. Applying them is
    deterministic and validated, so a person always reviews the exact change set.
    """

    op: OperationKind
    rule_id: str | None = Field(default=None, max_length=80)
    field: str | None = Field(default=None, max_length=60)
    values: dict[str, str | None] = Field(default_factory=dict, max_length=10)
    condition: Condition | None = None
    rule: Rule | None = None
    reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def check_shape(self):
        needs_rule = self.op not in {"add_rule", "set_header"}
        if needs_rule and not self.rule_id:
            raise ValueError(f"{self.op} requires rule_id.")
        if self.op in {"update_condition", "remove_condition"} and not self.field:
            raise ValueError(f"{self.op} requires the condition field.")
        if self.op == "update_condition" and (
            not self.values or set(self.values) - CONDITION_FIELDS
        ):
            raise ValueError(
                "update_condition values may only contain " + ", ".join(sorted(CONDITION_FIELDS))
            )
        if self.op == "update_rule" and (not self.values or set(self.values) - RULE_FIELDS):
            raise ValueError(
                "update_rule values may only contain " + ", ".join(sorted(RULE_FIELDS))
            )
        if self.op == "set_header" and (
            self.field not in HEADER_FIELDS or "value" not in self.values
        ):
            raise ValueError("set_header supports product_name or document_number with a value.")
        if self.op == "add_condition" and self.condition is None:
            raise ValueError("add_condition requires a condition.")
        if self.op == "add_rule" and self.rule is None:
            raise ValueError("add_rule requires a rule.")
        return self


class RevisionApply(StrictModel):
    operations: list[Operation] = Field(min_length=1, max_length=100)
    summary: str = Field(default="", max_length=2000)
    instruction: str = Field(default="", max_length=20000)
    revision: str | None = Field(default=None, min_length=1, max_length=40)
    request_file: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}\.(pdf|xlsx|docx|txt|md|eml)$"
    )
    request_filename: str | None = Field(default=None, max_length=255)
    interpreter: str | None = Field(default=None, max_length=120)
