from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Condition(StrictModel):
    field: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=60)
    operator: Literal["lt", "lte", "gt", "gte"]
    limit: Decimal = Field(allow_inf_nan=False, max_digits=18, decimal_places=9)
    unit: Literal["mm", "µm"] = "mm"
    warning_margin: Decimal | None = Field(
        default=None, ge=0, allow_inf_nan=False, max_digits=18, decimal_places=9
    )


class Rule(StrictModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    area: str = Field(min_length=1, max_length=80)
    item: str = Field(min_length=1, max_length=100)
    kind: Literal["NUMERIC", "PROHIBITED", "NOT_APPLICABLE", "UNRESOLVED"]
    conditions: list[Condition] = Field(default_factory=list, max_length=12)
    source: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_conditions(self):
        if self.kind == "NUMERIC" and not self.conditions:
            raise ValueError("Numeric criteria require at least one condition.")
        if self.kind != "NUMERIC" and self.conditions:
            raise ValueError("Only numeric criteria may define conditions.")
        if len({condition.field for condition in self.conditions}) != len(self.conditions):
            raise ValueError("Measurement field names must be unique within a rule.")
        return self


class StandardInput(StrictModel):
    customer: str = Field(min_length=1, max_length=100)
    part_number: str = Field(min_length=1, max_length=100)
    product_name: str = Field(min_length=1, max_length=100)
    document_number: str = Field(min_length=1, max_length=100)
    revision: str = Field(min_length=1, max_length=40)
    provenance: Literal["SYNTHETIC", "INTERNAL"] = "INTERNAL"
    rules: list[Rule] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_rules(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("Rule IDs must be unique.")
        return self


class ChangeEntry(StrictModel):
    """One recorded change to a standard version (who, when, why, and what)."""

    at: str
    actor: str
    actor_user_id: str | None = None
    kind: Literal["CREATED", "REVISED", "ACTIVATED", "SUPERSEDED"]
    summary: str = Field(default="", max_length=2000)
    instruction: str = Field(default="", max_length=20000)
    request_file: str | None = None
    request_filename: str | None = None
    operations: list[dict] = Field(default_factory=list)
    interpreter: str | None = None


class Standard(StandardInput):
    id: str
    status: Literal["DRAFT", "ACTIVE", "SUPERSEDED"] = "DRAFT"
    source_file: str | None = None
    source_hash: str | None = None
    activated_by: str | None = None
    # Version lineage. Standards registered before versioning have no family and are
    # treated as version 1 of their own family.
    family_id: str | None = None
    version: int = Field(default=1, ge=1)
    previous_id: str | None = None
    created_at: str | None = None
    activated_at: str | None = None
    superseded_by: str | None = None
    history: list[ChangeEntry] = Field(default_factory=list)

    @property
    def lineage(self) -> str:
        return self.family_id or self.id
