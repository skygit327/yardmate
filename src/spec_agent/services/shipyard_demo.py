"""Load the synthetic Dongnam shipbuilding demo: three customer forms -> standards -> lots.

A shipyard form (Korean), a coating spec (English) and a ship owner's form (Chinese) are read by
the same form reader, saved and activated as standards, and a few inspection lots are created at
different stages. Programme notices are loaded as well. Everything is synthetic.
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from spec_agent.schemas.inspection import Action, InspectionInput
from spec_agent.schemas.standard import StandardInput
from spec_agent.storage.database import timestamp

HERE = Path(__file__).parent
PLAN = json.loads((HERE / "shipyard_demo.json").read_text("utf-8"))
SAMPLES = HERE / "shipyard_samples"


def _passing(operator: str, limit: str) -> str:
    value = Decimal(limit)
    factor = Decimal("0.6") if operator in {"lt", "lte"} else Decimal("1.2")
    return str((value * factor).quantize(Decimal("0.01")))


def _observations(standard, overrides: dict) -> dict:
    observations = {}
    for rule in standard.rules:
        if rule.kind == "PROHIBITED":
            observations[rule.id] = {"defect_present": False}
        elif rule.kind == "NUMERIC":
            measurements, units = {}, {}
            for condition in rule.conditions:
                value = _passing(condition.operator, str(condition.limit))
                for key, override in overrides.items():
                    item, _, area = key.partition("|")
                    if (
                        item == rule.item
                        and area in rule.area
                        and condition.field == rule.conditions[0].field
                    ):
                        value = override
                measurements[condition.field] = value
                units[condition.field] = condition.unit
            observations[rule.id] = {"measurements": measurements, "units": units}
    return observations


def _act(workflow, state: dict, action: str, actor: str) -> dict:
    return workflow.act(
        state["inspection_id"],
        Action(
            request_id=uuid4(),
            expected_revision=state["revision"],
            interrupt_id=state["pending"]["id"],
            action=action,
            actor=actor,
        ),
    )


def seed_shipyard(intake, workflow, programs, actor: str, actor_user_id: str | None) -> dict:
    created = {"standards": 0, "inspections": 0, "programs": 0}
    existing = {standard.document_number for standard in workflow.store.standards()}
    lots = {item["inputs"]["lot"] for item in workflow.list()}
    for form in PLAN["forms"]:
        path = SAMPLES / form["file"]
        reading = intake.read(path.name, path.read_bytes(), "SYNTHETIC")
        draft = intake.draft(reading)
        if draft["document_number"] in existing:
            continue
        standard = intake.save_standard(
            reading["id"], StandardInput.model_validate(draft), None, actor, actor_user_id
        )
        standard = workflow.activate_standard(standard.id, actor, actor_user_id)
        created["standards"] += 1
        for lot in form["lots"]:
            if lot["lot"] in lots:
                continue
            state = workflow.create(
                InspectionInput(
                    standard_id=standard.id,
                    lot=lot["lot"],
                    inspector=lot["inspector"],
                    observations=_observations(standard, lot.get("values", {})),
                )
            )
            if lot["stage"] in {"report", "approved"}:
                state = _act(workflow, state, "confirm", actor)
            if lot["stage"] == "approved":
                state = _act(workflow, state, "approve", actor)
            created["inspections"] += 1
    created["programs"] = len(programs.load_samples())
    created["tasks"] = _seed_tasks(workflow, programs, actor)
    return created


def _seed_tasks(workflow, programs, actor: str) -> int:
    """Demo work across overdue, today, this week, later and done, linked to the demo records."""
    store = workflow.store
    if any(task.get("source") == "demo" for task in store.tasks()):
        return 0
    lots = {item["inputs"]["lot"]: item["inspection_id"] for item in workflow.list()}
    standards = {s.document_number: s.id for s in store.standards() if s.status == "ACTIVE"}
    today = date.today()
    count = 0
    for item in PLAN["tasks"]:
        link = item.get("link") or {}
        target = (
            {"type": "inspection", "id": lots[link["lot"]]}
            if link.get("lot") in lots
            else {"type": "standard", "id": standards[link["document"]]}
            if link.get("document") in standards
            else None
        )
        due = today + timedelta(days=item["days"])
        task = {
            "id": str(uuid4()),
            "title": item["title"],
            "detail": "",
            "due": due.isoformat(),
            "status": "DONE" if item.get("done") else "TODO",
            "source": "demo",
            "kind": "manual",
            "customer": item.get("customer", ""),
            "link": target,
            "created_by": actor,
            "created_at": timestamp(),
        }
        if item.get("done"):
            task["done_at"] = timestamp()
        with workflow.lock:
            store.save_task(task)
        count += 1
    program = next(
        (p for p in programs.list() if p["filename"] == PLAN["register_program_tasks"]), None
    )
    if program and not program["tasks"]:
        count += len(programs.register_tasks(program["id"])["tasks"])
    return count
