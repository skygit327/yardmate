from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, field_validator

from spec_agent.schemas.standard import StrictModel

Measurement = Annotated[Decimal, Field(allow_inf_nan=False, ge=0, max_digits=18, decimal_places=9)]


class Observation(StrictModel):
    measurements: dict[str, Measurement | None] = Field(default_factory=dict, max_length=12)
    units: dict[str, Literal["mm", "µm"]] = Field(default_factory=dict, max_length=12)
    defect_present: StrictBool | None = None

    @field_validator("measurements", mode="before")
    @classmethod
    def reject_booleans(cls, values):
        if isinstance(values, dict) and any(isinstance(value, bool) for value in values.values()):
            raise ValueError("Measurements cannot be true or false.")
        return values


class InspectionInput(StrictModel):
    standard_id: str = Field(min_length=1, max_length=100)
    lot: str = Field(min_length=1, max_length=100)
    inspector: str = Field(min_length=1, max_length=80)
    inspection_date: date = Field(default_factory=date.today)
    observations: dict[str, Observation] = Field(default_factory=dict, max_length=100)


class ItemDecision(StrictModel):
    result: Literal["PASS", "FAIL"]
    reason: str = Field(default="", max_length=1000)


class ActionInput(StrictModel):
    request_id: UUID
    expected_revision: int = Field(ge=1)
    interrupt_id: str = Field(min_length=1, max_length=200)
    action: Literal["correct", "confirm", "reject", "approve", "cancel"]
    reason: str = Field(default="", max_length=1000)
    observations: dict[str, Observation] | None = Field(default=None, max_length=100)
    decisions: dict[str, ItemDecision] = Field(default_factory=dict, max_length=100)


class Action(ActionInput):
    actor: str = Field(min_length=1, max_length=80)
    actor_user_id: str | None = None


class ActivateInput(StrictModel):
    pass


class ItemResult(StrictModel):
    rule_id: str
    area: str
    item: str
    auto_result: Literal["PASS", "FAIL", "REVIEW", "N_A"]
    final_result: Literal["PASS", "FAIL"] | None = None
    warning: bool = False
    criteria: str
    measured: str
    explanation: str
    source: str
    review_reason: str = ""
