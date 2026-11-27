"""Process flows per part and root-cause (process) suggestions for defects."""

from __future__ import annotations

import json
from uuid import uuid4

from pydantic import Field, field_validator

from spec_agent.schemas.standard import StrictModel
from spec_agent.services import traceback
from spec_agent.storage.database import timestamp

MAX_NCR_BYTES = 20 * 1024 * 1024


class FlowStep(StrictModel):
    key: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    label: str | None = Field(default=None, max_length=60)


class FlowInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    template: str | None = Field(default=None, max_length=60)
    customer: str = Field(default="", max_length=100)
    part_numbers: list[str] = Field(default_factory=list, max_length=50)
    steps: list[FlowStep] = Field(min_length=1, max_length=30)

    @field_validator("steps")
    @classmethod
    def unique_steps(cls, steps):
        if len({step.key for step in steps}) != len(steps):
            raise ValueError("Each process step can appear only once in a flow.")
        return steps

    @field_validator("part_numbers")
    @classmethod
    def clean_parts(cls, parts):
        return [part.strip()[:100] for part in parts if part.strip()]


class TracebackInput(StrictModel):
    defect: str = Field(min_length=1, max_length=300)
    part_number: str | None = Field(default=None, max_length=100)
    detected_at: str | None = Field(default=None, max_length=40)
    flow_id: str | None = None
    customer: str | None = Field(default=None, max_length=100)
    source: str | None = Field(default=None, max_length=300)


class CaseSave(StrictModel):
    customer: str = Field(min_length=1, max_length=100)


class TracebackDesk:
    def __init__(self, workflow):
        self.workflow = workflow
        self.root = workflow.settings.data_dir / "traceback"
        self.root.mkdir(parents=True, exist_ok=True)

    # --- flows -----------------------------------------------------------------------------

    def flows(self) -> list[dict]:
        with self.workflow.lock:
            return self.workflow.store.process_flows()

    def save_flow(self, data: FlowInput, flow_id: str | None = None) -> dict:
        with self.workflow.lock:
            existing = {flow["id"]: flow for flow in self.workflow.store.process_flows()}
            if flow_id is not None and flow_id not in existing:
                raise KeyError("The process flow could not be found.")
            flow = {
                **data.model_dump(),
                "id": flow_id or str(uuid4()),
                "created_at": existing.get(flow_id, {}).get("created_at", timestamp()),
                "updated_at": timestamp(),
            }
            self.workflow.store.save_process_flow(flow)
            return flow

    def delete_flow(self, flow_id: str) -> dict:
        with self.workflow.lock:
            self.workflow.store.delete_process_flow(flow_id)
        return {"ok": True}

    def flow_for(self, flow_id: str | None, part_numbers: list[str]) -> dict:
        flows = self.flows()
        if flow_id:
            for flow in flows:
                if flow["id"] == flow_id:
                    return flow
            raise KeyError("The process flow could not be found.")
        wanted = {part.casefold() for part in part_numbers if part}
        for flow in flows:
            if wanted & {part.casefold() for part in flow.get("part_numbers", [])}:
                return flow
        return traceback.default_flow()

    # --- analysis --------------------------------------------------------------------------

    def _approvals(self) -> list[dict]:
        with self.workflow.lock:
            return self.workflow.store.limit_approvals()

    def _store(self, case: dict) -> dict:
        case["id"] = str(uuid4())
        case["created_at"] = timestamp()
        (self.root / f"{case['id']}.json").write_text(json.dumps(case, ensure_ascii=False), "utf-8")
        return case

    def from_defect(self, data: TracebackInput) -> dict:
        flow = self.flow_for(data.flow_id, [data.part_number or ""])
        defect = traceback.match_defect(data.defect)
        detected = data.detected_at or next(
            (
                step["key"]
                for step in flow["steps"]
                if traceback.STEPS.get(step["key"], {}).get("role") == "inspection"
            ),
            None,
        )
        analysis = traceback.analyze(flow, defect, detected, None, self._approvals())
        return self._store(
            {
                "kind": "DEFECT",
                "input": data.model_dump(),
                "customer": data.customer or "",
                "part_numbers": [data.part_number] if data.part_number else [],
                "analysis": analysis,
            }
        )

    def from_ncr(self, filename: str, content: bytes, flow_id: str | None = None) -> dict:
        if len(content) > MAX_NCR_BYTES:
            raise ValueError("Files must not exceed 20 MB.")
        ncr = traceback.read_ncr(filename, content)
        parts = [part.strip() for part in ncr["header"].get("part_number", "").split("/")]
        flow = self.flow_for(flow_id, parts)
        stratified = traceback.stratify(ncr, flow["steps"])
        defect = traceback.match_defect(ncr["header"].get("problem", "")) or (
            "dimension" if ncr["spec"] else None
        )
        # The customer's incoming inspection found it, after every internal step.
        analysis = traceback.analyze(flow, defect, None, stratified, self._approvals())
        return self._store(
            {
                "kind": "NCR",
                "ncr": ncr,
                "customer": "",
                "part_numbers": [part for part in parts if part],
                "summary": stratified["summary"],
                "factors": stratified["factors"],
                "pieces": stratified["pieces"],
                "analysis": analysis,
            }
        )

    def case(self, case_id: str) -> dict:
        path = (self.root / f"{case_id}.json").resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            raise KeyError("The analysis could not be found.")
        return json.loads(path.read_text("utf-8"))

    def save_case(self, case_id: str, data: CaseSave, actor: str) -> dict:
        case = self.case(case_id)
        case.update(customer=" ".join(data.customer.split()), saved_by=actor, saved_at=timestamp())
        (self.root / f"{case_id}.json").write_text(json.dumps(case, ensure_ascii=False), "utf-8")
        with self.workflow.lock:
            self.workflow.store.save_traceback_case(case)
        return case

    def cases(self, customer: str | None = None) -> list[dict]:
        with self.workflow.lock:
            cases = self.workflow.store.traceback_cases()
        if customer:
            cases = [case for case in cases if case["customer"].casefold() == customer.casefold()]
        return cases
