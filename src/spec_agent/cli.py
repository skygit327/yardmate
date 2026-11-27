import argparse
import getpass
import json
import os
from uuid import uuid4

from pydantic import ValidationError

from spec_agent.auth import AuthStore
from spec_agent.config import Settings
from spec_agent.schemas.inspection import Action, InspectionInput
from spec_agent.services.standards import demo_observations
from spec_agent.services.workflow import Workflow


def main():
    parser = argparse.ArgumentParser(description="Spec Agent local demo")
    parser.add_argument(
        "command", choices=["serve", "demo", "create-user", "seed-demo", "llm-check"]
    )
    parser.add_argument("--username")
    parser.add_argument("--name")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    if args.command == "llm-check":
        from spec_agent.services.revision_agent import LLMClient, LLMError

        llm = Settings.from_env().llm
        if llm.provider == "none":
            print(
                "No LLM configured. Set SPEC_AGENT_LLM_PROVIDER and SPEC_AGENT_LLM_MODEL in .env."
            )
            raise SystemExit(1)
        print(f"Provider: {llm.provider} / Model: {llm.model}")
        print(f"Endpoint: {llm.base_url or 'default'} ({'external' if llm.external else 'local'})")
        print(f"API key: {'set' if llm.api_key else 'missing'}")
        print(
            "Internal data to external AI: "
            + ("allowed" if llm.allow_internal or not llm.external else "blocked (rule-based used)")
        )
        try:
            result = LLMClient(llm).ping()
        except LLMError as error:
            print(f"Connection failed: {error}")
            raise SystemExit(1) from None
        print(f"Connection OK ({result['seconds']} s)" if result["ok"] else "Unexpected reply.")
        return
    if args.command == "seed-demo":
        from spec_agent.services.customer_desk import CustomerDesk
        from spec_agent.services.demo_data import seed_demo

        workflow = Workflow(Settings.from_env())
        try:
            created = seed_demo(workflow, CustomerDesk(workflow))
        finally:
            workflow.close()
        print("Demo data added: " + ", ".join(f"{count} {name}" for name, count in created.items()))
        return
    if args.command == "create-user":
        if not args.username or not args.name:
            parser.error("create-user requires --username and --name.")
        password = getpass.getpass("Password (12-128 characters): ")
        if password != getpass.getpass("Confirm password: "):
            parser.error("Passwords do not match.")
        auth = AuthStore(Settings.from_env())
        try:
            user = auth.create_user(args.username, args.name, password)
        except ValidationError:
            parser.error(
                "Check the username (letters, digits, _.-), name (1-80 characters), and password (12-128 characters)."
            )
        except ValueError as error:
            parser.error(str(error))
        finally:
            auth.close()
        print(f"Account created: {user['username']} / {user['display_name']}")
        return
    if args.command == "serve":
        import uvicorn

        behind_proxy = os.environ.get("SPEC_AGENT_BEHIND_PROXY", "").lower() == "true"
        uvicorn.run(
            "spec_agent.main:create_app",
            factory=True,
            host=args.host,
            port=int(os.environ.get("PORT", args.port)),
            workers=1,
            proxy_headers=behind_proxy,
            forwarded_allow_ips="*" if behind_proxy else None,
        )
        return
    workflow = Workflow(Settings.from_env())
    try:
        state = workflow.create(
            InspectionInput(
                standard_id="demo-cover-en-v1",
                lot="CLI-DEMO",
                inspector="Demo Inspector",
                observations=demo_observations(),
            )
        )
        while state["pending"]:
            print(
                json.dumps(
                    {"pending": state["pending"], "results": state["item_results"]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            if input("Review the content and enter yes to approve: ").strip() != "yes":
                print(f"Inspection {state['inspection_id']} has been saved as pending.")
                return
            review = state["pending"]["kind"] == "inspection_review"
            decisions = (
                {
                    result["rule_id"]: {
                        "result": result["auto_result"],
                        "reason": "Synthetic example reviewed",
                    }
                    for result in state["item_results"]
                    if result["warning"]
                }
                if review
                else {}
            )
            state = workflow.act(
                state["inspection_id"],
                Action(
                    request_id=uuid4(),
                    expected_revision=state["revision"],
                    interrupt_id=state["pending"]["id"],
                    action="confirm" if review else "approve",
                    actor="Demo Reviewer",
                    decisions=decisions,
                ),
            )
        print(json.dumps(state["report"], ensure_ascii=False, indent=2))
    finally:
        workflow.close()
