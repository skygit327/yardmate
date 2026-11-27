"""Chat assistant: understand a request in plain words, then open the right page or do it.

The assistant plans a small list of actions and runs them with the same services the
screens use. Without an AI model (or when customer data may not leave the company) a
rule-based planner reads the request with ``assistant_lexicon.json``; with a model the
model plans and the same actions run. Everything it creates is a draft or a task:
standards are never activated and replies never sent from the chat.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from pydantic import Field

from spec_agent.schemas.standard import StrictModel
from spec_agent.services import traceback
from spec_agent.services.automation import TaskInput
from spec_agent.services.revision_agent import LLMClient, LLMError, parse_json_object

LEXICON = json.loads(Path(__file__).with_name("assistant_lexicon.json").read_text("utf-8"))
INTENTS = LEXICON["intents"]
PAGES = {
    "home",
    "tasks",
    "calendar",
    "automation",
    "inspections",
    "inspection_new",
    "standards",
    "standard",
    "forms",
    "trace",
    "customers",
    "customer",
    "requests",
    "request_new",
    "ai",
}
ACTIONS = {
    "navigate",
    "list_tasks",
    "add_task",
    "find_standards",
    "revise_standard",
    "trace_defect",
    "create_request",
    "start_inspection",
    "open_customer",
    "help",
}

PLANNER_PROMPT = """You are the assistant inside Spec Agent, a quality-inspection app. Read the user's
request and plan actions. Answer ONLY with JSON:
{"reply": "<one or two sentences in the user's language>", "actions": [ ... ]}
Actions (use only these, at most 3):
- {"type":"navigate","page":"home|tasks|calendar|automation|inspections|standards|forms|trace|customers|requests|request_new|ai"}
- {"type":"list_tasks"}
- {"type":"add_task","title":"...","due":"YYYY-MM-DD"}
- {"type":"find_standards","query":"customer, part number, product or document number"}
- {"type":"revise_standard","query":"which standard","instruction":"the requested change in the user's words"}
- {"type":"trace_defect","defect":"defect type","part_number":"optional"}
- {"type":"create_request","text":"the customer's request text","customer":"optional"}
- {"type":"start_inspection","query":"optional standard"}
- {"type":"open_customer","name":"customer name"}
- {"type":"help"}
Never invent customers, parts or numbers; use the context lists. Today is {today}."""


class AssistantInput(StrictModel):
    message: str = Field(min_length=1, max_length=8000)
    language: str = Field(default="ko", pattern=r"^(en|ko|vi)$")
    history: list[dict] = Field(default_factory=list, max_length=20)


def _has(text: str, words: list[str]) -> str | None:
    lowered = text.lower()
    for word in sorted(words, key=len, reverse=True):
        if word.lower() in lowered:
            return word
    return None


class Assistant:
    def __init__(self, workflow, desk, traceback_desk, automation, llm_client=None):
        self.workflow = workflow
        self.desk = desk
        self.traceback = traceback_desk
        self.automation = automation
        self.client = llm_client

    def set_llm(self, llm, client=None):
        self.client = client

    # --- context ----------------------------------------------------------------------------

    def _standards(self):
        with self.workflow.lock:
            standards = self.workflow.store.standards()
        latest = {}
        for standard in standards:
            current = latest.get(standard.lineage)
            if current is None or standard.version > current.version:
                latest[standard.lineage] = standard
        return list(latest.values())

    def _customers(self) -> list[str]:
        names = {item["name"] for item in self.desk.customers()}
        return sorted(name for name in names if name)

    def _match_standards(self, text: str) -> list:
        lowered = text.lower()
        found = []
        for standard in self._standards():
            keys = [standard.document_number, standard.part_number, standard.product_name]
            if any(key and key.lower() in lowered for key in keys):
                found.append(standard)
        return found

    def _match_customer(self, text: str) -> str | None:
        lowered = text.lower()
        return next(
            (
                name
                for name in sorted(self._customers(), key=len, reverse=True)
                if name.lower() in lowered
            ),
            None,
        )

    @staticmethod
    def _due(text: str) -> tuple[str, str]:
        """Find a deadline in the text; return (iso date, text without it)."""
        dates, today = LEXICON["dates"], date.today()
        rest = text
        due = None
        numbered = re.search(
            rf"(\d{{1,2}})\s*(?:{re.escape(dates['month_marker'])}|[/.-])\s*(\d{{1,2}})\s*{re.escape(dates['day_marker'])}?",
            rest,
        )
        if numbered:
            month, day = int(numbered.group(1)), int(numbered.group(2))
            try:
                due = date(today.year, month, day)
                if due < today - timedelta(days=30):
                    due = date(today.year + 1, month, day)
            except ValueError:
                due = None
            rest = rest.replace(numbered.group(0), " ")
        later = re.search(
            r"(\d{1,3})\s*(?:"
            + "|".join(
                re.escape(w.replace("{n}", "").strip())
                for w in dates["in_days"]
                if w.replace("{n}", "").strip()
            )
            + ")",
            rest,
        )
        if due is None and later:
            due = today + timedelta(days=int(later.group(1)))
            rest = rest.replace(later.group(0), " ")
        for key, offset in (("day_after", 2), ("tomorrow", 1), ("next_week", 7), ("today", 0)):
            word = _has(rest, dates[key])
            if word:
                if due is None:
                    due = today + timedelta(days=offset)
                rest = re.sub(re.escape(word), " ", rest, flags=re.IGNORECASE)
        return (due or today).isoformat(), rest

    @staticmethod
    def _clean(text: str, remove: list[str]) -> str:
        for word in sorted(remove, key=len, reverse=True):
            text = re.sub(re.escape(word), " ", text, flags=re.IGNORECASE)
        strip = set(LEXICON["strip"])
        tokens = [token for token in text.split() if token.lower() not in strip]
        return " ".join(tokens).strip(" .,!?:;")

    def home(self) -> dict:
        """Numbers for the home page: what needs attention today."""
        today = date.today().isoformat()
        week = (date.today() + timedelta(days=6)).isoformat()
        tasks = [task for task in self.automation.tasks() if task["status"] == "TODO"]
        with self.workflow.lock:
            standards = self.workflow.store.standards()
            requests = self.workflow.store.customer_requests()
        jobs = self.automation.jobs()
        inspections = self.workflow.list()
        return {
            "tasks": {
                "overdue": sum(t["due"] < today for t in tasks),
                "today": sum(t["due"] == today for t in tasks),
                "week": sum(today < t["due"] <= week for t in tasks),
                "open": len(tasks),
            },
            "drafts": sum(s.status == "DRAFT" for s in standards),
            "active_standards": sum(s.status == "ACTIVE" for s in standards),
            "open_requests": sum(r.status in {"OPEN", "IN_PROGRESS"} for r in requests),
            "needs_review": sum(j["status"] != "DONE" for j in jobs),
            "pending_inspections": sum(
                i["status"] in {"AWAITING_REVIEW", "AWAITING_REPORT_APPROVAL", "NEEDS_INPUT"}
                for i in inspections
            ),
            "customers": len(self._customers()),
            "automation": self.automation.status()["watching"],
            "programs": self.automation.programs.summary() if self.automation.programs else None,
        }

    # --- planning ---------------------------------------------------------------------------

    def _llm_allowed(self) -> bool:
        settings = self.workflow.settings.llm
        if settings.provider == "none":
            return False
        return not settings.external or settings.allow_internal

    def plan_rules(self, message: str) -> list[dict]:
        text = message.strip()
        long_text = len(text) > 120 or text.count("\n") >= 2
        intent = lambda key: _has(text, INTENTS[key])
        if intent("add_task"):
            due, rest = self._due(text)
            title = self._clean(rest, INTENTS["add_task"])
            return [{"type": "add_task", "title": title, "due": due}]
        if long_text or (intent("request") and not intent("trace") and not intent("revise")):
            if long_text or len(self._clean(text, INTENTS["request"])) > 30:
                return [
                    {"type": "create_request", "text": text, "customer": self._match_customer(text)}
                ]
            return [
                {"type": "navigate", "page": "request_new", "customer": self._match_customer(text)}
            ]
        if intent("revise") and (self._match_standards(text) or re.search(r"\d", text)):
            return [{"type": "revise_standard", "query": text, "instruction": text}]
        if intent("trace") or intent("ncr"):
            defect = traceback.match_defect(text)
            if defect:
                return [{"type": "trace_defect", "defect": text, "part_number": None}]
            return [{"type": "navigate", "page": "trace"}]
        if intent("form"):
            return [{"type": "navigate", "page": "forms"}]
        if intent("inspection"):
            return [{"type": "start_inspection", "query": text}]
        if intent("calendar"):
            return [{"type": "navigate", "page": "calendar"}]
        if intent("automation"):
            return [{"type": "navigate", "page": "automation"}]
        if intent("settings"):
            return [{"type": "navigate", "page": "ai"}]
        customer = self._match_customer(text)
        if intent("customer") or (customer and not intent("standard")):
            return [{"type": "open_customer", "name": customer or ""}]
        if intent("standard") or self._match_standards(text):
            return [{"type": "find_standards", "query": text}]
        if intent("tasks"):
            return [{"type": "list_tasks"}]
        if intent("help"):
            return [{"type": "help"}]
        if traceback.match_defect(text):
            return [{"type": "trace_defect", "defect": text, "part_number": None}]
        return [{"type": "help", "unknown": True}]

    def plan_llm(self, data: AssistantInput) -> tuple[str, list[dict]]:
        client = self.client or LLMClient(self.workflow.settings.llm)
        context = {
            "customers": self._customers()[:50],
            "standards": [
                {
                    "document": s.document_number,
                    "part": s.part_number,
                    "product": s.product_name,
                    "customer": s.customer,
                    "status": s.status,
                }
                for s in self._standards()[:50]
            ],
            "language": data.language,
        }
        history = "\n".join(
            f"{turn.get('role', 'user')}: {str(turn.get('text', ''))[:500]}"
            for turn in data.history[-6:]
        )
        prompt = f"Context: {json.dumps(context, ensure_ascii=False)}\nConversation so far:\n{history}\n\nUser: {data.message}"
        answer = parse_json_object(
            client.complete(PLANNER_PROMPT.replace("{today}", date.today().isoformat()), prompt)
        )
        actions = [
            a for a in answer.get("actions", []) if isinstance(a, dict) and a.get("type") in ACTIONS
        ][:3]
        return str(answer.get("reply") or "")[:1000], actions

    # --- running ----------------------------------------------------------------------------

    def handle(self, data: AssistantInput, actor: str) -> dict:
        language = data.language
        replies = LEXICON["replies"][language]
        notes, reply, interpreter = [], "", "rules"
        actions = None
        if self._llm_allowed():
            try:
                reply, actions = self.plan_llm(data)
                interpreter = f"llm:{(self.client or LLMClient(self.workflow.settings.llm)).label}"
            except (LLMError, ValueError, KeyError, TypeError):
                actions = None
        elif self.workflow.settings.llm.provider != "none":
            notes.append(replies["llm_blocked"])
        if not actions:
            actions = self.plan_rules(data.message)
            interpreter = "rules"
            reply = ""
        result = {
            "reply": reply,
            "messages": [],
            "cards": [],
            "navigate": None,
            "interpreter": interpreter,
            "notes": notes,
        }
        for action in actions:
            try:
                self._run(action, data, actor, result)
            except (ValueError, KeyError) as error:
                result["messages"].append(str(error)[:300])
        if not result["reply"]:
            result["reply"] = " ".join(result["messages"]) or replies["help"]
        result.pop("messages")
        return result

    def _say(self, result, data, key, **params):
        template = LEXICON["replies"][data.language][key]
        result["messages"].append(template.format(**params))

    def _run(self, action: dict, data: AssistantInput, actor: str, result: dict):
        kind = action.get("type")
        language = data.language
        if kind == "help":
            self._say(result, data, "unknown" if action.get("unknown") else "help")
            if action.get("unknown"):
                self._say(result, data, "help")
            result["cards"].append({"kind": "help"})
        elif kind == "navigate":
            page = action.get("page")
            if page not in PAGES:
                raise ValueError("Unknown page.")
            params = {
                key: value for key, value in action.items() if key not in {"type", "page"} and value
            }
            result["navigate"] = {"page": page, "params": params}
            key = {
                "forms": "form",
                "trace": "trace_page",
                "request_new": "request_page",
                "requests": "request_page",
                "standards": "standards_page",
                "customers": "customers_page",
                "calendar": "calendar",
                "automation": "automation",
                "ai": "settings",
            }.get(page)
            if key:
                self._say(result, data, key)
        elif kind == "list_tasks":
            tasks = [task for task in self.automation.tasks() if task["status"] == "TODO"]
            today = date.today().isoformat()
            week = (date.today() + timedelta(days=6)).isoformat()
            overdue = [t for t in tasks if t["due"] < today]
            due_today = [t for t in tasks if t["due"] == today]
            this_week = [t for t in tasks if today < t["due"] <= week]
            self._say(
                result,
                data,
                "tasks",
                open=len(tasks),
                overdue=len(overdue),
                today=len(due_today),
                week=len(this_week),
            )
            for task in (overdue + due_today + this_week)[:6]:
                result["cards"].append(
                    {
                        "kind": "task",
                        "title": task["title"].get(language) or task["title"]["en"],
                        "detail": task["due"],
                        "late": task["due"] < today,
                        "link": task.get("link"),
                    }
                )
            result["navigate"] = result["navigate"] or {"page": "tasks", "params": {}}
        elif kind == "add_task":
            title = str(action.get("title") or "").strip()
            if not title:
                self._say(result, data, "task_missing")
                return
            due = str(action.get("due") or date.today().isoformat())
            task = self.automation.add_task(TaskInput(title=title[:300], due=due), actor)
            self._say(result, data, "task_added", title=title, due=due)
            result["cards"].append(
                {
                    "kind": "task",
                    "title": title,
                    "detail": due,
                    "late": False,
                    "link": None,
                    "id": task["id"],
                }
            )
        elif kind == "find_standards":
            query = str(action.get("query") or data.message)
            found = self._match_standards(query)
            customer = self._match_customer(query)
            if not found and customer:
                found = [
                    s for s in self._standards() if s.customer.casefold() == customer.casefold()
                ]
            if not found:
                self._say(result, data, "standards_page")
                result["navigate"] = {"page": "standards", "params": {}}
                return
            self._say(result, data, "standards_found", count=len(found))
            for standard in found[:6]:
                result["cards"].append(
                    {
                        "kind": "standard",
                        "title": f"{standard.product_name} · {standard.part_number}",
                        "detail": f"{standard.customer} · {standard.document_number} · Rev {standard.revision} · {standard.status}",
                        "link": {"type": "standard", "id": standard.lineage},
                    }
                )
        elif kind == "revise_standard":
            query = str(action.get("query") or data.message)
            found = self._match_standards(query) or self._match_standards(data.message)
            if len(found) != 1:
                self._say(result, data, "revise_which")
                for standard in (found or self._standards())[:6]:
                    result["cards"].append(
                        {
                            "kind": "standard",
                            "title": f"{standard.product_name} · {standard.part_number}",
                            "detail": f"{standard.customer} · {standard.document_number} · Rev {standard.revision}",
                            "link": {"type": "standard", "id": standard.lineage},
                        }
                    )
                return
            standard = found[0]
            instruction = str(action.get("instruction") or data.message)[:2000]
            self._say(
                result,
                data,
                "revise",
                standard=f"{standard.document_number} ({standard.part_number})",
            )
            result["navigate"] = {
                "page": "standard",
                "params": {"id": standard.lineage, "instruction": instruction},
            }
        elif kind == "trace_defect":
            defect_text = str(action.get("defect") or data.message)
            if not traceback.match_defect(defect_text):
                self._say(result, data, "trace_unknown")
                result["navigate"] = {"page": "trace", "params": {}}
                return
            from spec_agent.services.traceback_desk import TracebackInput

            case = self.traceback.from_defect(
                TracebackInput(
                    defect=defect_text[:300], part_number=(action.get("part_number") or None)
                )
            )
            analysis = case["analysis"]
            names = []
            for candidate in analysis["candidates"][:3]:
                step_names = candidate.get("names") or {}
                names.append(
                    f"{candidate['rank']}. {candidate.get('label') or step_names.get(language) or candidate['step']}"
                )
            defect_names = traceback.DEFECTS.get(analysis["defect"] or "", {}).get("names", {})
            self._say(
                result,
                data,
                "trace",
                defect=defect_names.get(language, defect_text),
                steps=", ".join(names) or "-",
            )
            result["cards"].append(
                {
                    "kind": "trace",
                    "title": defect_names.get(language, defect_text),
                    "detail": ", ".join(names),
                    "link": {"type": "trace", "id": case["id"]},
                }
            )
        elif kind == "create_request":
            text = str(action.get("text") or data.message)
            customer = action.get("customer") or self._match_customer(text) or ""
            request = self.desk.create_request(
                customer=customer, channel="OTHER", body=text, actor=actor
            )
            self._say(
                result,
                data,
                "request_created",
                summary=(request.analysis or {}).get("summary", request.subject)[:200],
                category=request.category,
                priority=request.priority,
            )
            result["cards"].append(
                {
                    "kind": "request",
                    "title": request.subject[:120],
                    "detail": f"{request.customer or '-'} · {request.category} · {request.priority}",
                    "link": {"type": "request", "id": request.id},
                }
            )
        elif kind == "start_inspection":
            found = [
                s
                for s in self._match_standards(str(action.get("query") or data.message))
                if s.status == "ACTIVE"
            ]
            params = {"standard_id": found[0].id} if len(found) == 1 else {}
            label = f" ({found[0].document_number})" if len(found) == 1 else ""
            self._say(result, data, "inspection", standard=label)
            result["navigate"] = {"page": "inspection_new", "params": params}
        elif kind == "open_customer":
            name = str(action.get("name") or "").strip()
            known = self._match_customer(name) if name else None
            if known:
                self._say(result, data, "customer", customer=known)
                result["navigate"] = {"page": "customer", "params": {"name": known}}
            else:
                self._say(result, data, "customers_page")
                result["navigate"] = {"page": "customers", "params": {}}
