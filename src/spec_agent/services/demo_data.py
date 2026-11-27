"""Load synthetic demo customers, inspections and requests (for local demonstrations only)."""

import json
from pathlib import Path

from spec_agent.schemas.customer_request import CustomerInput
from spec_agent.schemas.inspection import InspectionInput
from spec_agent.services.standards import demo_observations

DATA = Path(__file__).with_name("demo_requests.json")


def seed_demo(workflow, desk, actor: str = "Demo") -> dict:
    data = json.loads(DATA.read_text("utf-8"))
    created = {"customers": 0, "inspections": 0, "requests": 0}
    if not desk._customer_record(data["customer"]["name"]):
        desk.create_customer(CustomerInput.model_validate(data["customer"]))
        created["customers"] += 1
    existing_lots = {item["inputs"]["lot"] for item in workflow.list()}
    for item in data["inspections"]:
        if item["lot"] in existing_lots:
            continue
        observations = demo_observations()
        observations["burr"] = {"measurements": {"height": item["burr"]}, "units": {"height": "mm"}}
        workflow.create(
            InspectionInput(
                standard_id="demo-cover-en-v1",
                lot=item["lot"],
                inspector=item["inspector"],
                observations=observations,
            )
        )
        created["inspections"] += 1
    if desk.requests():
        return created
    for item in data["requests"]:
        files = [("request.eml", item["eml"].encode())] if "eml" in item else []
        desk.create_request(
            customer=item.get("customer", ""),
            subject=item.get("subject", ""),
            body=item.get("body", ""),
            channel=item["channel"],
            response_language=item.get("response_language"),
            files=files,
            actor=actor,
        )
        created["requests"] += 1
    return created
