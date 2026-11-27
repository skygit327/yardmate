import hashlib
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from langsmith import tracing_context

from spec_agent.agent.graph import build_graph
from spec_agent.config import Settings
from spec_agent.reports.rendering import ReportRenderer
from spec_agent.reports.standard_document import StandardDocumentRenderer
from spec_agent.schemas.inspection import Action, InspectionInput, ItemResult
from spec_agent.schemas.revision import RevisionApply
from spec_agent.schemas.standard import ChangeEntry, Standard, StandardInput
from spec_agent.services.documents import extract_text, validate_request_file
from spec_agent.services.evaluation import evaluate
from spec_agent.services.revision_agent import RevisionAgent
from spec_agent.services.versioning import (
    apply_operations,
    diff_standards,
    next_revision_label,
    summarize_diff,
)
from spec_agent.storage.database import Store, timestamp


class Conflict(ValueError):
    pass


class Workflow:
    def __init__(self, settings: Settings, llm_client=None):
        self.settings = settings
        self.revision_agent = RevisionAgent(settings.llm, llm_client)
        self.lock = threading.RLock()
        renderer = ReportRenderer(
            settings.data_dir / "reports", settings.font_path, settings.report_languages
        )
        self.store = Store(settings.data_dir)
        self.document_renderer = StandardDocumentRenderer(
            settings.data_dir / "standard_documents", settings.font_path
        )
        self.report_languages = ("en", *[x for x in settings.report_languages if x != "en"])
        self.checkpoint_connection = sqlite3.connect(
            settings.data_dir / "checkpoints.sqlite", check_same_thread=False
        )
        self.checkpointer = SqliteSaver(self.checkpoint_connection)
        self.graph = build_graph(self.checkpointer, renderer)

    def set_llm(self, llm, client=None):
        """Switch the model used by the agents without restarting the server."""
        with self.lock:
            self.settings = replace(self.settings, llm=llm)
            self.revision_agent = RevisionAgent(llm, client)

    def close(self):
        self.checkpoint_connection.close()
        self.store.close()

    def config(self, inspection_id):
        return {"configurable": {"thread_id": inspection_id}, "recursion_limit": 100}

    def invoke(self, value, inspection_id):
        with tracing_context(enabled=False):
            return self.graph.invoke(value, self.config(inspection_id))

    def register_standard(
        self, data: StandardInput, actor: str = "System", actor_user_id: str | None = None
    ) -> Standard:
        with self.lock:
            identifier = str(uuid4())
            now = timestamp()
            standard = Standard(
                **data.model_dump(),
                id=identifier,
                family_id=identifier,
                created_at=now,
                history=[
                    ChangeEntry(
                        at=now,
                        actor=actor,
                        actor_user_id=actor_user_id,
                        kind="CREATED",
                        summary="Registered from structured criteria",
                    )
                ],
            )
            self.store.save_standard(standard)
            return standard

    def activate_standard(
        self, standard_id: str, actor: str, actor_user_id: str | None = None
    ) -> Standard:
        with self.lock:
            standard = self.store.standard(standard_id)
            if standard.status != "DRAFT":
                raise Conflict(
                    "This standard is already active. Register a new revision to make changes."
                )
            now = timestamp()
            standard.status = "ACTIVE"
            standard.activated_by = actor
            standard.activated_at = now
            standard.history.append(
                ChangeEntry(at=now, actor=actor, actor_user_id=actor_user_id, kind="ACTIVATED")
            )
            for other in self.store.family(standard.lineage):
                if other.id != standard.id and other.status == "ACTIVE":
                    other.status = "SUPERSEDED"
                    other.superseded_by = standard.id
                    other.history.append(
                        ChangeEntry(
                            at=now,
                            actor=actor,
                            actor_user_id=actor_user_id,
                            kind="SUPERSEDED",
                            summary=f"Superseded by revision {standard.revision}",
                        )
                    )
                    self.store.save_standard(other)
            self.store.save_standard(standard)
            return standard

    # --- Standard versions and revisions -------------------------------------------------

    def versions(self, standard_id: str) -> list[dict]:
        with self.lock:
            standard = self.store.standard(standard_id)
            counts = self.store.inspection_counts()
            return [
                {**version.model_dump(mode="json"), "inspection_count": counts.get(version.id, 0)}
                for version in reversed(self.store.family(standard.lineage))
            ]

    def diff(self, standard_id: str, against: str | None = None) -> dict:
        with self.lock:
            standard = self.store.standard(standard_id)
            before_id = against or standard.previous_id
            before = self.store.standard(before_id) if before_id else None
            if before and before.lineage != standard.lineage:
                raise ValueError("Only versions of the same standard can be compared.")
            return diff_standards(before, standard)

    def store_request_file(self, filename: str, content: bytes) -> tuple[dict, str]:
        suffix = validate_request_file(filename, content)
        text = extract_text(suffix, content)
        digest = hashlib.sha256(content).hexdigest()
        root = self.settings.data_dir / "requests"
        root.mkdir(exist_ok=True)
        stored = digest + suffix
        (root / stored).write_bytes(content)
        return {"stored_name": stored, "filename": filename[:255], "sha256": digest}, text

    def request_file_path(self, stored_name: str):
        root = (self.settings.data_dir / "requests").resolve()
        path = (root / stored_name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise KeyError("The request file could not be found.")
        return path

    def propose_revision(
        self,
        standard_id: str,
        instruction: str,
        filename: str | None = None,
        content: bytes | None = None,
    ) -> dict:
        with self.lock:
            standard = self.store.standard(standard_id)
            if standard.status == "SUPERSEDED":
                raise Conflict("This version has been superseded. Revise the latest version.")
        attachment, text = None, instruction.strip()
        if content is not None and filename:
            attachment, extracted = self.store_request_file(filename, content)
            text = (text + "\n\n" + extracted).strip() if extracted else text
        if not text:
            raise ValueError("Describe the change or attach a request file.")
        with self.lock:
            taken = {version.revision for version in self.store.family(standard.lineage)}
            if standard.status == "DRAFT":
                taken.discard(standard.revision)
        # Model calls run outside the workflow lock so inspections are not blocked.
        proposal = self.revision_agent.propose(standard, text, taken)
        if standard.status == "DRAFT":
            proposal["suggested_revision"] = standard.revision
        proposal["instruction"] = instruction.strip()
        proposal["attachment"] = attachment
        proposal["extracted_text"] = text if attachment else None
        proposal["llm_external"] = self.settings.llm.external
        return proposal

    def apply_revision(
        self,
        standard_id: str,
        data: RevisionApply,
        actor: str = "System",
        actor_user_id: str | None = None,
    ) -> Standard:
        with self.lock:
            base = self.store.standard(standard_id)
            family = self.store.family(base.lineage)
            if data.request_file:
                self.request_file_path(data.request_file)
            changed = apply_operations(base, data.operations)
            now = timestamp()
            if base.status == "SUPERSEDED":
                raise Conflict("This version has been superseded. Revise the latest version.")
            if base.status == "ACTIVE":
                drafts = [version for version in family if version.status == "DRAFT"]
                if drafts:
                    raise Conflict(
                        f"Draft revision {drafts[0].revision} is already open. "
                        "Continue revising that draft or discard it first."
                    )
                label = data.revision or next_revision_label(
                    base.revision, {version.revision for version in family}
                )
                if label in {version.revision for version in family}:
                    raise ValueError(f"Revision {label} already exists for this standard.")
                target = Standard(
                    **{**changed, "revision": label},
                    id=str(uuid4()),
                    family_id=base.lineage,
                    version=max(version.version for version in family) + 1,
                    previous_id=base.id,
                    created_at=now,
                )
                if base.family_id is None:
                    base.family_id = base.lineage
                    self.store.save_standard(base)
            else:
                label = data.revision or base.revision
                if label in {v.revision for v in family if v.id != base.id}:
                    raise ValueError(f"Revision {label} already exists for this standard.")
                target = Standard.model_validate(
                    {**base.model_dump(mode="json"), **changed, "revision": label}
                )
            summary = data.summary.strip() or summarize_diff(diff_standards(base, target))
            target.history.append(
                ChangeEntry(
                    at=now,
                    actor=actor,
                    actor_user_id=actor_user_id,
                    kind="REVISED",
                    summary=summary[:2000],
                    instruction=data.instruction,
                    request_file=data.request_file,
                    request_filename=data.request_filename,
                    operations=[operation.model_dump(mode="json") for operation in data.operations],
                    interpreter=data.interpreter,
                )
            )
            self.store.save_standard(target)
            return target

    def import_version(
        self,
        base_id: str,
        data: StandardInput,
        actor: str,
        actor_user_id: str | None,
        summary: str,
        request_filename: str | None = None,
    ) -> Standard:
        """Register criteria read from a customer form as the next version of a standard."""
        with self.lock:
            base = self.store.standard(base_id)
            family = self.store.family(base.lineage)
            drafts = [version for version in family if version.status == "DRAFT"]
            if drafts:
                raise Conflict(
                    f"Draft revision {drafts[0].revision} is already open. "
                    "Activate or discard it before importing a new customer form."
                )
            latest = max(family, key=lambda version: version.version)
            taken = {version.revision for version in family}
            label = (
                data.revision
                if data.revision not in taken
                else next_revision_label(latest.revision, taken)
            )
            now = timestamp()
            target = Standard(
                **{**data.model_dump(), "revision": label},
                id=str(uuid4()),
                family_id=base.lineage,
                version=latest.version + 1,
                previous_id=latest.id,
                created_at=now,
                history=[
                    ChangeEntry(
                        at=now,
                        actor=actor,
                        actor_user_id=actor_user_id,
                        kind="REVISED",
                        summary=summary[:2000],
                        request_filename=request_filename,
                        interpreter="form-reader",
                    )
                ],
            )
            if base.family_id is None:
                base.family_id = base.lineage
                self.store.save_standard(base)
            self.store.save_standard(target)
            return target

    def attach_source(self, standard_id: str, filename: str, content: bytes) -> Standard:
        suffix = Path(filename).suffix.lower()
        with self.lock:
            standard = self.store.standard(standard_id)
            digest = hashlib.sha256(content).hexdigest()
            root = self.settings.data_dir / "sources"
            root.mkdir(exist_ok=True)
            stored = digest + suffix
            (root / stored).write_bytes(content)
            standard.source_file, standard.source_hash = stored, digest
            self.store.save_standard(standard)
            return standard

    def standard_document(self, standard_id: str, extension: str, language: str = "en"):
        with self.lock:
            standard = self.store.standard(standard_id)
            return standard, self.document_renderer(
                standard, self.store.family(standard.lineage), extension, language
            )

    def discard_standard(self, standard_id: str) -> dict:
        with self.lock:
            standard = self.store.standard(standard_id)
            if standard.status != "DRAFT":
                raise Conflict("Only draft versions can be discarded.")
            if self.store.inspection_counts().get(standard.id):
                raise Conflict("This version is referenced by inspections.")
            self.store.delete_standard(standard.id)
            return {"ok": True, "previous_id": standard.previous_id}

    def create(self, data: InspectionInput) -> dict:
        with self.lock:
            standard = self.store.standard(data.standard_id)
            if standard.status != "ACTIVE":
                raise Conflict("Only reviewed and activated standards can be used.")
            evaluate(standard, data.observations)
            inspection_id = str(uuid4())
            state = {
                "inspection_id": inspection_id,
                "created_at": timestamp(),
                "standard": standard.model_dump(mode="json"),
                "inputs": data.model_dump(mode="json"),
                "input_revision": 1,
                "item_results": [],
                "overall_result": "REVIEW",
                "events": [],
                "status": "CREATED",
                "report": None,
                "report_history": [],
                "reviewer": "",
                "approver": "",
                "reviewer_user_id": None,
                "approver_user_id": None,
            }
            self.store.create_inspection(inspection_id, state)
            self.invoke(state, inspection_id)
            return self.get(inspection_id)

    def get(self, inspection_id: str) -> dict:
        with self.lock:
            initial = self.store.initial_state(inspection_id)
            snapshot = self.graph.get_state(self.config(inspection_id))
            state = dict(snapshot.values) if snapshot.values else initial
            pending = [entry for task in snapshot.tasks for entry in task.interrupts]
            errors = [task.error for task in snapshot.tasks if task.error]
            incomplete = not snapshot.values or (snapshot.next and not pending and not errors)
            state["revision"] = len(state["events"]) + 1
            state["pending"] = {"id": pending[0].id, **pending[0].value} if pending else None
            if state["pending"]:
                state["pending"]["message"] = {
                    "correction": "Correct the inputs or cancel the inspection.",
                    "inspection_review": "Review the automated decisions and warning items.",
                    "report_approval": "Review the PDF and Excel drafts before approving.",
                }[state["pending"]["kind"]]
            state["error"] = (
                "Processing was interrupted. Check the server logs and retry."
                if errors or incomplete
                else None
            )
            if errors or incomplete:
                state["status"] = "ERROR"
            elif pending:
                state["status"] = {
                    "correction": "NEEDS_INPUT",
                    "inspection_review": "AWAITING_REVIEW",
                    "report_approval": "AWAITING_REPORT_APPROVAL",
                }[pending[0].value["kind"]]
            return state

    def list(self) -> list[dict]:
        with self.lock:
            return [
                {
                    key: state[key]
                    for key in (
                        "inspection_id",
                        "created_at",
                        "inputs",
                        "standard",
                        "overall_result",
                        "status",
                        "revision",
                    )
                }
                for state in [self.get(identifier) for identifier in self.store.inspection_ids()]
            ]

    def validate_action(self, state: dict, action: Action):
        pending = state["pending"]
        if state["error"]:
            raise Conflict("Retry the failed operation first.")
        if (
            not pending
            or pending["id"] != action.interrupt_id
            or state["revision"] != action.expected_revision
        ):
            raise Conflict(
                "The pending action or revision has changed. Reload the latest inspection."
            )
        allowed = {
            "correction": {"correct", "cancel"},
            "inspection_review": {"confirm", "reject", "cancel"},
            "report_approval": {"approve", "reject", "cancel"},
        }
        if action.action not in allowed[pending["kind"]]:
            raise Conflict("This action is not available at the current stage.")
        if action.action in {"reject", "cancel"} and not action.reason:
            raise ValueError("Provide a reason for rejection or cancellation.")
        if action.action == "approve":
            for key in state["report"]["files"]:
                extension, _, language = key.partition("-")
                self.report_path(state["inspection_id"], extension, language or "en")
        if action.action == "correct":
            if action.observations is None:
                raise ValueError("Provide the corrected measurements.")
            evaluate(Standard.model_validate(state["standard"]), action.observations)
        if action.action == "confirm":
            results = [ItemResult.model_validate(result) for result in state["item_results"]]
            applicable = [result for result in results if result.auto_result != "N_A"]
            if not applicable or any(result.auto_result == "REVIEW" for result in applicable):
                raise ValueError("Unresolved items prevent confirmation.")
            if set(action.decisions) - {result.rule_id for result in applicable}:
                raise ValueError("A final decision was provided for an inapplicable item.")
            for result in applicable:
                decision = action.decisions.get(result.rule_id)
                if result.warning and (decision is None or not decision.reason):
                    raise ValueError(
                        f"{result.item}: A final decision and reason are required for each warning."
                    )
                if decision and result.auto_result == "FAIL" and decision.result == "PASS":
                    raise ValueError(
                        "An automated FAIL cannot be overridden as PASS. Correct the inputs and reevaluate."
                    )
                if decision and decision.result != result.auto_result and not decision.reason:
                    raise ValueError(
                        "A reason is required when the final decision differs from the automated decision."
                    )

    def act(self, inspection_id: str, action: Action) -> dict:
        with self.lock:
            state = self.get(inspection_id)
            payload = action.model_dump(mode="json")
            request_id = str(action.request_id)
            recorded = self.store.request(inspection_id, request_id)
            if recorded:
                prior = {key: value for key, value in recorded.items() if key != "at"}
                prior.setdefault("actor_user_id", None)
                if prior != payload:
                    raise Conflict("The same request ID cannot be reused with different content.")
                if any(event["request_id"] == request_id for event in state["events"]):
                    return state
            self.validate_action(state, action)
            if not recorded:
                recorded = {**payload, "at": timestamp()}
                self.store.save_request(inspection_id, request_id, recorded)
            self.invoke(Command(resume=recorded), inspection_id)
            return self.get(inspection_id)

    def retry(self, inspection_id: str) -> dict:
        with self.lock:
            state = self.get(inspection_id)
            snapshot = self.graph.get_state(self.config(inspection_id))
            if not snapshot.values:
                self.invoke(self.store.initial_state(inspection_id), inspection_id)
            elif snapshot.next and (state["error"] or not state["pending"]):
                self.invoke(None, inspection_id)
            else:
                pending_id = state["pending"]["id"] if state["pending"] else None
                unfinished = [
                    payload
                    for payload in self.store.requests(inspection_id)
                    if payload["interrupt_id"] == pending_id
                    and not any(
                        event["request_id"] == payload["request_id"] for event in state["events"]
                    )
                ]
                if unfinished:
                    self.act(
                        inspection_id,
                        Action.model_validate(
                            {key: value for key, value in unfinished[-1].items() if key != "at"}
                        ),
                    )
                else:
                    raise Conflict("There is no failed operation to retry.")
            return self.get(inspection_id)

    def report_path(self, inspection_id: str, extension: str, language: str = "en"):
        with self.lock:
            state = self.get(inspection_id)
            if (
                state["status"] not in {"AWAITING_REPORT_APPROVAL", "APPROVED"}
                or not state["report"]
            ):
                raise Conflict("No valid report is available for the current state.")
            key = extension if language == "en" else f"{extension}-{language}"
            if key not in state["report"]["files"]:
                raise KeyError("This report is not available in the requested language.")
            artifact = state["report"]["files"][key]
            root = (self.settings.data_dir / "reports").resolve()
            path = (root / artifact["path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise KeyError("The report file could not be found.")
            if hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
                raise Conflict(
                    "The report file differs from the approval target. Check file integrity."
                )
            return path
