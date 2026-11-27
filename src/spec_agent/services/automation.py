"""Watched work folders: drop a file in, get drafts, a processing report and tasks.

Folder layout (names in ``automation_lexicon.json``)::

    workspace/
      1_customer-forms  -> customer inspection forms (criteria tables, limit approvals)
      2_NCR             -> non-conformance reports (root-cause analysis, requested actions)
      3_requests        -> customer mails / documents (analysis, reply draft)
      reports/          -> one PDF processing report per file
      done/, failed/    -> originals after processing

Everything stops at drafts: activating standards, approving reports and sending replies
remain human decisions in the app.
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from spec_agent.schemas.standard import StandardInput, StrictModel
from spec_agent.storage.database import timestamp

LEXICON = json.loads(Path(__file__).with_name("automation_lexicon.json").read_text("utf-8"))
LANGUAGES = ("en", "ko", "zh")
FOLDERS = LEXICON["folders"]
INBOXES = ("customer_forms", "ncr", "requests", "programs")
SUFFIXES = {
    "customer_forms": {".pdf"},
    "ncr": {".pdf"},
    "requests": {".eml", ".pdf", ".docx", ".xlsx", ".txt", ".md", ".png", ".jpg", ".jpeg"},
    "programs": {".pdf", ".txt", ".md"},
}
log = logging.getLogger("spec_agent.automation")
FINDINGS = {
    "COMMON_CAUSE": "Every factor level fails alike ({ng}/{n}): look for a cause shared by all pieces",
    "SYSTEMATIC_OFFSET": "Systematic offset of {shift} mm with small spread ({std} mm)",
    "SPECIFIC_FACTOR": "Failures concentrate on {factor} {level}",
    "UNKNOWN_DEFECT": "Defect type not recognised",
}


def text(template: str, params: dict | None = None) -> dict:
    """The same message in every supported language."""
    params = params or {}
    result = {}
    for language in LANGUAGES:
        pattern = LEXICON["texts"].get(language, {}).get(template, template)
        values = {
            key: (value.get(language) or value.get("en")) if isinstance(value, dict) else value
            for key, value in params.items()
        }
        values = {
            key: ", ".join(map(str, v)) if isinstance(v, list) else v for key, v in values.items()
        }
        try:
            result[language] = pattern.format(**values)
        except (KeyError, IndexError):
            result[language] = pattern
    return result


class TaskInput(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    due: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    detail: str = Field(default="", max_length=2000)
    customer: str = Field(default="", max_length=100)


class TaskUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    due: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    status: str | None = Field(default=None, pattern=r"^(TODO|DONE)$")
    detail: str | None = Field(default=None, max_length=2000)


class Automation:
    def __init__(self, workflow, desk, intake, traceback_desk, programs=None):
        self.workflow = workflow
        self.programs = programs
        self.desk = desk
        self.intake = intake
        self.traceback = traceback_desk
        settings = workflow.settings
        self.root: Path | None = settings.workspace_dir
        self.interval = settings.automation_interval
        self.language = (
            settings.automation_language if settings.automation_language in LANGUAGES else "en"
        )
        self.reports_dir = settings.data_dir / "automation"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self._seen: dict[Path, tuple[int, float]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._scan_lock = threading.Lock()
        self.last_scan: str | None = None

    # --- folders -----------------------------------------------------------------------------

    def folder(self, key: str) -> Path:
        assert self.root is not None
        return self.root / FOLDERS[key]

    def prepare(self):
        if self.root is None:
            return
        for key in FOLDERS:
            self.folder(key).mkdir(parents=True, exist_ok=True)
        readme = self.root / "README.txt"
        if not readme.exists():
            readme.write_text(
                LEXICON["readme"]["ko"] + "\n" + LEXICON["readme"]["en"] + "\n", "utf-8"
            )

    def start(self):
        if self.root is None or self.interval <= 0:
            return
        self.prepare()
        self._thread = threading.Thread(target=self._run, name="spec-agent-automation", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.scan()
            except Exception:
                log.exception("Automation scan failed")

    def status(self) -> dict:
        return {
            "enabled": self.root is not None,
            "watching": bool(self._thread and self._thread.is_alive()),
            "root": str(self.root) if self.root else None,
            "folders": {key: str(self.folder(key)) for key in FOLDERS} if self.root else {},
            "interval": self.interval,
            "last_scan": self.last_scan,
        }

    # --- scanning ----------------------------------------------------------------------------

    def scan(self, settle: bool = True) -> list[dict]:
        """Process every new file. With ``settle`` a file must keep its size for one scan."""
        if self.root is None:
            return []
        self.prepare()
        jobs = []
        with self._scan_lock:
            for kind in INBOXES:
                for path in sorted(self.folder(kind).iterdir()):
                    if (
                        not path.is_file()
                        or path.name.startswith((".", "~$"))
                        or path.name == "README.txt"
                    ):
                        continue
                    stat = path.stat()
                    signature = (stat.st_size, stat.st_mtime)
                    if settle and self._seen.get(path) != signature:
                        self._seen[path] = signature
                        continue
                    self._seen.pop(path, None)
                    jobs.append(self.process(kind, path))
            self.last_scan = timestamp()
        return jobs

    def process(self, kind: str, path: Path) -> dict:
        content = path.read_bytes()
        job = {
            "id": str(uuid4()),
            "kind": kind,
            "filename": path.name,
            "created_at": timestamp(),
            "status": "DONE",
            "read": [],
            "created": [],
            "warnings": [],
            "tasks": [],
            "error": None,
        }
        try:
            if path.suffix.lower() not in SUFFIXES[kind]:
                raise ValueError(f"Unsupported file type: {path.suffix or '-'}")
            getattr(self, f"_{kind}")(job, path.name, content)
        except Exception as error:  # noqa: BLE001 - recorded on the job and in the report
            log.warning("Could not process %s: %s", path, error)
            job["status"] = "ERROR"
            job["error"] = str(error)[:500]
            job["warnings"].append(text("Error: {error}", {"error": str(error)[:300]}))
            self._task(
                job,
                "Check the file that could not be processed: {file}",
                {"file": path.name},
                LEXICON["due_days"]["failed_file"],
            )
        target = self.folder("failed" if job["status"] == "ERROR" else "done") / path.name
        if target.exists():
            target = target.with_name(f"{target.stem}-{datetime.now():%Y%m%d%H%M%S}{target.suffix}")
        shutil.move(str(path), target)
        job["stored"] = str(target)
        job["report"] = self._report(job)
        with self.workflow.lock:
            self.workflow.store.save_automation_job(job)
        return job

    # --- per folder --------------------------------------------------------------------------

    def _customer_forms(self, job, filename, content):
        reading = self.intake.read(filename, content, "INTERNAL")
        job["link"] = {"type": "form", "id": reading["id"]}
        warnings = {warning["code"]: warning for warning in reading["warnings"]}
        if (
            reading["document_type"] == "INSPECTION_MATRIX"
            or reading["document_type"] == "AI_EXTRACTED"
        ):
            review = sum(rule["confidence"] != "high" for rule in reading["rules"])
            job["read"].append(
                text(
                    "Inspection criteria table: {rules} criteria, {review} to check",
                    {"rules": len(reading["rules"]), "review": review},
                )
            )
            draft = self.intake.draft(reading)
            missing = [
                key for key in ("customer", "part_number", "document_number") if not draft[key]
            ]
            if missing:
                raise ValueError("The form header is incomplete: " + ", ".join(missing))
            standard = self.intake.save_standard(
                reading["id"],
                StandardInput.model_validate(draft),
                reading.get("previous_id"),
                "Automation",
                None,
            )
            job["created"].append(
                {
                    "type": "standard",
                    "id": standard.lineage,
                    "label": text(
                        "Draft standard {document} revision {revision} (version {version})",
                        {
                            "document": standard.document_number,
                            "revision": standard.revision,
                            "version": standard.version,
                        },
                    ),
                }
            )
            job["customer"] = standard.customer
            self._task(
                job,
                "Check and activate draft standard {document}",
                {"document": standard.document_number},
                LEXICON["due_days"]["standard_review"],
                link={"type": "standard", "id": standard.lineage},
            )
            if review:
                job["status"] = "NEEDS_REVIEW"
            if "COLOR_MISMATCH" in warnings:
                params = warnings["COLOR_MISMATCH"]["params"]
                job["warnings"].append(
                    text("Confirm the colour mismatch with the customer ({area})", params)
                )
                self._task(
                    job,
                    "Confirm the colour mismatch with the customer ({area})",
                    params,
                    LEXICON["due_days"]["standard_review"],
                    link={"type": "standard", "id": standard.lineage},
                )
        elif reading["document_type"] == "LIMIT_APPROVAL":
            samples = reading["samples"]
            counts = {d: sum(s.get("decision") == d for s in samples) for d in ("OK", "NG", "HOLD")}
            job["read"].append(
                text(
                    "Limit-sample approval: {samples} samples (OK {ok}, NG {ng}, on hold {hold})",
                    {
                        "samples": len(samples),
                        "ok": counts["OK"],
                        "ng": counts["NG"],
                        "hold": counts["HOLD"],
                    },
                )
            )
            job["created"].append(
                {
                    "type": "form",
                    "id": reading["id"],
                    "label": text("Form reading waiting for a customer"),
                }
            )
            job["status"] = "NEEDS_REVIEW"
            self._task(
                job,
                "Assign a customer and save limit samples ({file})",
                {"file": filename},
                LEXICON["due_days"]["limit_samples"],
                link={"type": "form", "id": reading["id"]},
            )
            holds = [s["no"] for s in samples if s.get("decision") == "HOLD"]
            if holds:
                self._task(
                    job,
                    "Decide samples on hold: No. {numbers}",
                    {"numbers": holds},
                    LEXICON["due_days"]["limit_samples"],
                    link={"type": "form", "id": reading["id"]},
                )
        else:
            job["read"].append(text("Unknown layout"))
            raise ValueError("The layout was not recognised.")

    def _ncr(self, job, filename, content):
        case = self.traceback.from_ncr(filename, content)
        header, summary = case["ncr"]["header"], case["summary"]
        job["link"] = {"type": "trace", "id": case["id"]}
        job["read"].append(
            text(
                "{ncr}: {problem}",
                {"ncr": header.get("ncr_no", filename), "problem": header.get("problem", "")},
            )
        )
        job["read"].append(
            text(
                "{ng} of {n} pieces out of tolerance, offset {shift} mm",
                {"ng": summary["ng"], "n": summary["n"], "shift": summary["shift"]},
            )
        )
        candidates = case["analysis"]["candidates"]

        def step_name(candidate, language):
            names = candidate.get("names") or {}
            return candidate["label"] or names.get(language) or names.get("en") or candidate["step"]

        steps = {
            language: ", ".join(f"{c['rank']}. {step_name(c, language)}" for c in candidates[:3])
            for language in LANGUAGES
        }
        job["read"].append(text("Root-cause candidates: {steps}", {"steps": steps}))
        job["created"].append(
            {"type": "trace", "id": case["id"], "label": text("Root-cause analysis")}
        )
        for finding in case["analysis"]["findings"]:
            template = FINDINGS.get(finding["code"])
            if template:
                job["warnings"].append(text(template, finding["params"]))
        for candidate in candidates[:2]:
            step = {language: step_name(candidate, language) for language in LANGUAGES}
            self._task(
                job,
                "Verify root-cause candidate {rank}: {step}",
                {"rank": candidate["rank"], "step": step},
                LEXICON["due_days"]["ncr_candidate"],
                link={"type": "trace", "id": case["id"]},
            )
        for action in case["ncr"]["actions"]:
            days = next(
                (
                    rule["days"]
                    for rule in LEXICON["due_days"]["ncr_actions"]
                    if rule["match"] in action.lower()
                ),
                LEXICON["due_days"]["ncr_default"],
            )
            self._task(
                job,
                "NCR action: {action}",
                {"action": action},
                days,
                link={"type": "trace", "id": case["id"]},
            )

    def _requests(self, job, filename, content):
        request = self.desk.create_request(
            channel="EMAIL" if filename.lower().endswith(".eml") else "DOCUMENT",
            files=[(filename, content)],
            actor="Automation",
        )
        job["link"] = {"type": "request", "id": request.id}
        job["customer"] = request.customer
        customer = request.customer or text("unknown customer")
        job["read"].append(
            text(
                "Request: {subject} ({customer}, {category}, {priority})",
                {
                    "subject": request.subject,
                    "customer": customer,
                    "category": request.category,
                    "priority": request.priority,
                },
            )
        )
        job["created"].append(
            {
                "type": "request",
                "id": request.id,
                "label": text("Customer request with analysis and reply draft"),
            }
        )
        days = LEXICON["due_days"]["request"].get(request.priority, 3)
        due = request.due_date or None
        self._task(
            job,
            "Reply to {customer}: {subject}",
            {"customer": customer, "subject": request.subject[:80]},
            days,
            link={"type": "request", "id": request.id},
            due=due,
        )
        for action in (request.analysis or {}).get("proposed_actions", []):
            self._task(
                job,
                "Customer request action: {action}",
                {"action": action.get("label", action.get("kind", ""))},
                days,
                link={"type": "request", "id": request.id},
                due=due,
            )
        if not request.customer or (request.analysis or {}).get("missing"):
            job["status"] = "NEEDS_REVIEW"

    def _programs(self, job, filename, content):
        if self.programs is None:
            raise ValueError("Support-programme matching is not available.")
        program = self.programs.add_file(filename, content, "automation")
        notice, match = program["notice"], program["match"]
        job["link"] = {"type": "program", "id": program["id"]}
        from spec_agent.services.support_programs import RESULTS

        title = self.programs.short_title(notice)
        job["read"].append(text("Support programme: {title}", {"title": title}))
        job["read"].append(
            text(
                "Result: {result} · {met}/{required} conditions met · deadline {deadline}",
                {
                    "result": RESULTS[match["overall"]],
                    "met": match["met"],
                    "required": match["required"],
                    "deadline": notice["deadline"] or "-",
                },
            )
        )
        job["created"].append(
            {
                "type": "program",
                "id": program["id"],
                "label": text("Support programme: {title}", {"title": title}),
            }
        )
        if match["overall"] == "INELIGIBLE":
            failed = [
                c["label"]
                for c in match["conditions"]
                if c["status"] == "fail" and c["group"] != "preferred"
            ]
            reasons = {lang: ", ".join(item[lang] for item in failed[:3]) for lang in LANGUAGES}
            job["warnings"].append(text("Not eligible: {reasons}", {"reasons": reasons}))
            return
        if match["overall"] == "CHECK":
            job["status"] = "NEEDS_REVIEW"
            self._task(
                job,
                "Check conditions the rules could not decide: {title}",
                {"title": title},
                LEXICON["due_days"]["programs_unknown"],
            )
        if match["overall"] in {"ELIGIBLE", "CHECK"}:
            registered = self.programs.register_tasks(program["id"])
            job["tasks"].extend(
                {"id": t["id"], "title": t["title"], "due": t["due"]} for t in registered["tasks"]
            )

    # --- tasks and reports -------------------------------------------------------------------

    def _task(self, job, template, params, days, link=None, due=None):
        task = {
            "id": str(uuid4()),
            "title": text(template, params),
            "detail": "",
            "due": due or (date.today() + timedelta(days=days)).isoformat(),
            "status": "TODO",
            "source": "automation",
            "kind": job["kind"],
            "job_id": job["id"],
            "file": job["filename"],
            "customer": job.get("customer", ""),
            "link": link or job.get("link"),
            "created_at": timestamp(),
        }
        with self.workflow.lock:
            self.workflow.store.save_task(task)
        job["tasks"].append({"id": task["id"], "title": task["title"], "due": task["due"]})

    def _report(self, job) -> str:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        from spec_agent.reports.i18n import rich_text
        from spec_agent.reports.rendering import font_name, latin_font

        language = self.language
        words = LEXICON["report"][language]
        cjk = font_name(self.workflow.settings.font_path)
        body = ParagraphStyle("auto", fontName=latin_font(), fontSize=9.5, leading=14)
        head = ParagraphStyle(
            "auto-head", parent=body, fontSize=12, leading=18, spaceBefore=10, spaceAfter=4
        )
        title = ParagraphStyle("auto-title", parent=body, fontSize=17, leading=24, spaceAfter=8)

        def p(value, style=body):
            return Paragraph(rich_text(str(value), cjk), style)

        def pick(message):
            return (
                message.get(language) or message.get("en")
                if isinstance(message, dict)
                else str(message)
            )

        story = [p(words["title"], title)]
        meta = [
            [p(words["file"]), p(job["filename"])],
            [p(words["folder"]), p(words[f"kind_{job['kind']}"])],
            [
                p(words["processed"]),
                p(
                    datetime.fromisoformat(job["created_at"])
                    .astimezone()
                    .strftime("%Y-%m-%d %H:%M")
                ),
            ],
            [p(words["status"]), p(words[f"status_{job['status']}"])],
        ]
        table = Table(meta, colWidths=[110, 380])
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E7E9E7")),
                ]
            )
        )
        story.append(table)
        for key, items in (
            ("read", job["read"]),
            ("created", [c["label"] for c in job["created"]]),
            ("warnings", job["warnings"]),
        ):
            if items:
                story.append(p(words[key], head))
                story += [p("• " + pick(item)) for item in items]
        if job["tasks"]:
            story.append(p(words["tasks"], head))
            rows = [[p(words["due"]), p(words["tasks"])]] + [
                [p(task["due"]), p(pick(task["title"]))]
                for task in sorted(job["tasks"], key=lambda item: item["due"])
            ]
            tasks = Table(rows, colWidths=[90, 400])
            tasks.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E7E9E7")),
                    ]
                )
            )
            story.append(tasks)
        story += [Spacer(1, 12), p(words["note"])]
        name = re.sub(r"[^\w.-]+", "_", Path(job["filename"]).stem)[:60]
        filename = f"{datetime.now():%Y-%m-%d}_{name}_{job['id'][:8]}.pdf"
        buffer = io.BytesIO()
        SimpleDocTemplate(
            buffer, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=36, bottomMargin=36
        ).build(story)
        (self.reports_dir / filename).write_bytes(buffer.getvalue())
        if self.root is not None:
            (self.folder("reports") / filename).write_bytes(buffer.getvalue())
        return filename

    def report_path(self, job_id: str) -> Path:
        with self.workflow.lock:
            job = self.workflow.store.automation_job(job_id)
        path = (self.reports_dir / job["report"]).resolve()
        if not path.is_relative_to(self.reports_dir.resolve()) or not path.is_file():
            raise KeyError("The report could not be found.")
        return path

    # --- tasks API ---------------------------------------------------------------------------

    def tasks(self) -> list[dict]:
        with self.workflow.lock:
            return self.workflow.store.tasks()

    def add_task(self, data: TaskInput, actor: str) -> dict:
        task = {
            "id": str(uuid4()),
            "title": {language: data.title for language in LANGUAGES},
            "detail": data.detail,
            "due": data.due,
            "status": "TODO",
            "source": "manual",
            "kind": "manual",
            "customer": data.customer,
            "link": None,
            "created_by": actor,
            "created_at": timestamp(),
        }
        with self.workflow.lock:
            self.workflow.store.save_task(task)
        return task

    def update_task(self, task_id: str, data: TaskUpdate) -> dict:
        with self.workflow.lock:
            task = self.workflow.store.task(task_id)
            changes = data.model_dump(exclude_none=True)
            if "title" in changes:
                task["title"] = {language: changes.pop("title") for language in LANGUAGES}
            task.update(changes)
            if data.status == "DONE":
                task["done_at"] = timestamp()
            self.workflow.store.save_task(task)
            return task

    def delete_task(self, task_id: str) -> dict:
        with self.workflow.lock:
            self.workflow.store.delete_task(task_id)
        return {"ok": True}

    def jobs(self) -> list[dict]:
        with self.workflow.lock:
            return self.workflow.store.automation_jobs()
