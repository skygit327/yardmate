from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from spec_agent.agent.state import InspectionState
from spec_agent.schemas.inspection import ItemResult, Observation
from spec_agent.schemas.standard import Standard
from spec_agent.services.evaluation import evaluate, overall


# 2026.10.18
def event_updates(state: InspectionState, reply: dict) -> dict:
    return {
        "events": [
            *state["events"],
            {
                "request_id": reply["request_id"],
                "action": reply["action"],
                "actor": reply["actor"],
                "actor_user_id": reply.get("actor_user_id"),
                "reason": reply["reason"],
                "at": reply["at"],
                "input_revision": state["input_revision"],
                "decisions": reply.get("decisions", {}),
            },
        ]
    }


def evaluate_items(state: InspectionState):
    standard = Standard.model_validate(state["standard"])
    observations = {
        key: Observation.model_validate(value)
        for key, value in state["inputs"]["observations"].items()
    }
    results = evaluate(standard, observations)
    return {
        "item_results": [result.model_dump(mode="json") for result in results],
        "overall_result": overall(results),
        "status": "EVALUATED",
    }


def evaluation_route(state: InspectionState):
    return "correct" if state["overall_result"] == "REVIEW" else "review"


def await_correction(state: InspectionState):
    reply = interrupt(
        {"kind": "correction", "message": "Correct the inputs or cancel the inspection."}
    )
    updates = event_updates(state, reply)
    if reply["action"] == "cancel":
        return {**updates, "status": "CANCELLED"}
    inputs = {**state["inputs"], "observations": reply["observations"]}
    return {
        **updates,
        "inputs": inputs,
        "input_revision": state["input_revision"] + 1,
        "report": None,
        "reviewer": "",
        "approver": "",
        "reviewer_user_id": None,
        "approver_user_id": None,
        "status": "CORRECTED",
    }


def await_review(state: InspectionState):
    reply = interrupt(
        {
            "kind": "inspection_review",
            "message": "Review the automated decisions and warning items.",
        }
    )
    updates = event_updates(state, reply)
    if reply["action"] in {"reject", "cancel"}:
        return {**updates, "status": "CANCELLED" if reply["action"] == "cancel" else "REWORK"}
    results = [ItemResult.model_validate(value) for value in state["item_results"]]
    for result in results:
        if result.auto_result == "N_A":
            continue
        decision = reply.get("decisions", {}).get(result.rule_id)
        result.final_result = decision["result"] if decision else result.auto_result
        result.review_reason = decision["reason"] if decision else "Automated decision reviewed"
    return {
        **updates,
        "item_results": [result.model_dump(mode="json") for result in results],
        "overall_result": overall(results, final=True),
        "reviewer": reply["actor"],
        "reviewer_user_id": reply.get("actor_user_id"),
        "status": "CONFIRMED",
    }


def await_report_approval(state: InspectionState):
    reply = interrupt(
        {
            "kind": "report_approval",
            "message": "Review the PDF and Excel drafts before approving.",
            "report_revision": state["report"]["revision"],
            "files": state["report"]["files"],
        }
    )
    updates = event_updates(state, reply)
    if reply["action"] in {"reject", "cancel"}:
        return {**updates, "status": "CANCELLED" if reply["action"] == "cancel" else "REWORK"}
    return {
        **updates,
        "approver": reply["actor"],
        "approver_user_id": reply.get("actor_user_id"),
        "status": "APPROVING",
    }


def review_route(state: InspectionState):
    if state["status"] == "CANCELLED":
        return END
    return "correct" if state["status"] == "REWORK" else "draft"


def report_route(state: InspectionState):
    if state["status"] == "CANCELLED":
        return END
    return "correct" if state["status"] == "REWORK" else "publish"


def build_graph(checkpointer, renderer):
    def draft(state: InspectionState):
        report = renderer(state, approved=False)
        return {
            "report": report,
            "report_history": [*state["report_history"], report],
            "status": "DRAFT",
        }

    def publish(state: InspectionState):
        report = renderer(state, approved=True)
        return {
            "report": report,
            "report_history": [*state["report_history"], report],
            "status": "APPROVED",
        }

    builder = StateGraph(InspectionState)
    for name, node in [
        ("evaluate", evaluate_items),
        ("correct", await_correction),
        ("review", await_review),
        ("draft", draft),
        ("report_review", await_report_approval),
        ("publish", publish),
    ]:
        builder.add_node(name, node)
    builder.add_edge(START, "evaluate")
    builder.add_conditional_edges("evaluate", evaluation_route, ["correct", "review"])
    builder.add_conditional_edges(
        "correct",
        lambda state: END if state["status"] == "CANCELLED" else "evaluate",
        [END, "evaluate"],
    )
    builder.add_conditional_edges("review", review_route, [END, "correct", "draft"])
    builder.add_edge("draft", "report_review")
    builder.add_conditional_edges("report_review", report_route, [END, "correct", "publish"])
    builder.add_edge("publish", END)
    return builder.compile(checkpointer=checkpointer)
