from typing import TypedDict


class InspectionState(TypedDict):
    inspection_id: str
    created_at: str
    standard: dict
    inputs: dict
    input_revision: int
    item_results: list[dict]
    overall_result: str
    events: list[dict]
    status: str
    report: dict | None
    report_history: list[dict]
    reviewer: str
    approver: str
    reviewer_user_id: str | None
    approver_user_id: str | None
