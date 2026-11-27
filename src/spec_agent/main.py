import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field, SecretStr

from spec_agent.auth import (
    COOKIE_NAME,
    AuthenticationMiddleware,
    AuthStore,
    LoginInput,
    LoginRejected,
    LoginThrottled,
)
from spec_agent.config import Settings
from spec_agent.schemas.customer_request import (
    AnalyzeInput,
    Channel,
    CustomerInput,
    NoteInput,
    ReplyLanguage,
    RequestUpdate,
    RespondInput,
    Status,
)
from spec_agent.schemas.inspection import Action, ActionInput, ActivateInput, InspectionInput
from spec_agent.schemas.revision import RevisionApply
from spec_agent.schemas.standard import StandardInput, StrictModel
from spec_agent.services.ai_settings import AISettingsInput, AISettingsStore
from spec_agent.services.assistant import Assistant, AssistantInput
from spec_agent.services.automation import Automation, TaskInput, TaskUpdate
from spec_agent.services.customer_desk import CustomerDesk
from spec_agent.services.documents import MAX_REQUEST_BYTES
from spec_agent.services.form_intake import MAX_FORM_BYTES, FormIntake
from spec_agent.services.notice_feed import NoticeFeed
from spec_agent.services.notice_original import OriginalError, fetch_original, read_file
from spec_agent.services.revision_agent import LLMClient, LLMError
from spec_agent.services.support_programs import (
    MAX_NOTICE_BYTES,
    CompanyProfile,
    NoticeInput,
    SupportPrograms,
)
from spec_agent.services.traceback import library as process_library
from spec_agent.services.traceback_desk import (
    MAX_NCR_BYTES,
    CaseSave,
    FlowInput,
    TracebackDesk,
    TracebackInput,
)
from spec_agent.services.workflow import Conflict, Workflow

FORM_SAMPLES = Path(__file__).parent / "services" / "form_samples" / "samples.json"


class ProfileName(StrictModel):
    display_name: str = Field(min_length=1, max_length=80)


class PasswordChange(StrictModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=1, max_length=128)


class OriginalFetch(StrictModel):
    url: str | None = Field(default=None, max_length=2000)


Language = Literal["en", "ko", "zh"]


class FormStandardSave(StrictModel):
    standard: StandardInput
    previous_id: str | None = None


class LimitApprovalSave(StrictModel):
    customer: str


def bootstrap_hosted_demo(app) -> None:
    """For a hosted demo whose disk resets: create the sign-in account and load the shipyard demo.

    Both run only when the operator sets the environment variables; the password is never in
    the repository.
    """
    username = os.environ.get("SPEC_AGENT_DEMO_USERNAME", "").strip()
    password = os.environ.get("SPEC_AGENT_DEMO_PASSWORD", "")
    if not username or not password:
        return
    name = os.environ.get("SPEC_AGENT_DEMO_NAME", "").strip() or username
    with suppress(ValueError):
        app.state.auth.create_user(username, name, password)
    team_password = os.environ.get("SPEC_AGENT_DEMO_TEAM_PASSWORD", "")
    if team_password:
        # Extra demo accounts for the team; names live in a JSON file, the password in the env.
        team = json.loads(Path(__file__).with_name("demo_team.json").read_text("utf-8"))
        for member in team["members"]:
            with suppress(ValueError):
                app.state.auth.create_user(
                    member["username"], member["name"], team_password, demo=True
                )
    if os.environ.get("SPEC_AGENT_LOAD_SHIPYARD_DEMO", "").lower() == "true":
        from spec_agent.services.shipyard_demo import seed_shipyard

        seed_shipyard(app.state.intake, app.state.workflow, app.state.programs, name, None)


def create_app(settings: Settings | None = None, llm_client=None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.workflow = Workflow(settings, llm_client)
        app.state.desk = CustomerDesk(app.state.workflow, llm_client)
        app.state.intake = FormIntake(app.state.workflow, llm_client)
        app.state.traceback = TracebackDesk(app.state.workflow)
        app.state.programs = SupportPrograms(app.state.workflow)
        app.state.fetch_original = fetch_original
        app.state.feed = NoticeFeed(app.state.programs)
        app.state.automation = Automation(
            app.state.workflow,
            app.state.desk,
            app.state.intake,
            app.state.traceback,
            app.state.programs,
        )
        app.state.assistant = Assistant(
            app.state.workflow,
            app.state.desk,
            app.state.traceback,
            app.state.automation,
            llm_client,
        )
        app.state.assistant.feed = app.state.feed
        app.state.ai_settings = AISettingsStore(settings.data_dir, settings.llm)
        if llm_client is None:
            effective, _ = app.state.ai_settings.effective()
            if effective != settings.llm:
                apply_llm(effective)
        try:
            app.state.auth = AuthStore(settings)
            bootstrap_hosted_demo(app)
            app.state.automation.start()
            app.state.feed.start()
            try:
                yield
            finally:
                app.state.automation.stop()
                app.state.feed.stop()
                app.state.auth.close()
        finally:
            app.state.workflow.close()

    def apply_llm(llm):
        app.state.workflow.set_llm(llm)
        app.state.desk.set_llm(llm)
        app.state.intake.set_llm(llm)
        app.state.assistant.set_llm(llm)

    app = FastAPI(title="Spec Agent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(AuthenticationMiddleware)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, error):
        from fastapi.exception_handlers import request_validation_exception_handler

        if request.url.path == "/api/auth/login":
            return JSONResponse(
                status_code=422, content={"detail": "Check the username and password format."}
            )
        return await request_validation_exception_handler(request, error)

    @app.post("/api/auth/login")
    def login(request: Request, data: LoginInput):
        try:
            token, session = app.state.auth.login(
                data,
                request.client.host if request.client else "unknown",
                request.cookies.get(COOKIE_NAME),
            )
        except LoginRejected:
            raise HTTPException(401, "Invalid username or password.") from None
        except LoginThrottled:
            raise HTTPException(429, "Too many sign-in attempts. Try again in 5 minutes.") from None
        response = JSONResponse(session)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=settings.session_seconds,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
        )
        return response

    @app.get("/api/auth/me")
    def me(request: Request):
        return request.state.session

    @app.patch("/api/auth/me")
    def rename_me(request: Request, data: ProfileName):
        user = request.state.session["user"]
        try:
            renamed = app.state.auth.rename(user["id"], data.display_name)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return {**request.state.session, "user": renamed}

    @app.post("/api/auth/password")
    def change_password(request: Request, data: PasswordChange):
        try:
            app.state.auth.change_password(
                request.state.session["user"]["id"],
                data.current_password.get_secret_value(),
                data.new_password.get_secret_value(),
            )
        except LoginRejected:
            raise HTTPException(400, "The current password is not correct.") from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return {"ok": True}

    @app.post("/api/auth/logout")
    def logout(request: Request):
        app.state.auth.logout(request.cookies[COOKIE_NAME])
        response = JSONResponse({"ok": True})
        response.delete_cookie(
            COOKIE_NAME, httponly=True, secure=settings.cookie_secure, samesite="lax"
        )
        return response

    @app.exception_handler(Conflict)
    async def conflict_handler(request, error):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(ValueError)
    async def value_handler(request, error):
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(KeyError)
    async def missing_handler(request, error):
        return JSONResponse(status_code=404, content={"detail": str(error.args[0])})

    @app.exception_handler(Exception)
    async def unexpected_handler(request, error):
        logging.getLogger("spec_agent").exception(
            "Operation failed: %s", request.url.path, exc_info=error
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "The operation failed. Retry from the inspection list."},
        )

    @app.get("/api/health")
    def health():
        llm = app.state.workflow.settings.llm
        return {
            "status": "ok",
            "mode": "local-demo",
            "external_ai": llm.external,
            "llm": {
                "provider": llm.provider,
                "external": llm.external,
                "allow_internal": llm.allow_internal,
            },
            "report_languages": list(app.state.workflow.report_languages),
        }

    @app.get("/api/standards")
    def standards(customer: str | None = None, part_number: str | None = None):
        workflow = app.state.workflow
        with workflow.lock:
            return [
                standard
                for standard in workflow.store.standards()
                if (customer is None or standard.customer == customer)
                and (part_number is None or standard.part_number == part_number)
            ]

    @app.post("/api/standards", status_code=201)
    def register(data: StandardInput, request: Request):
        user = request.state.session["user"]
        return app.state.workflow.register_standard(data, user["display_name"], user["id"])

    @app.post("/api/standards/{standard_id}/activate")
    def activate(standard_id: str, data: ActivateInput, request: Request):
        user = request.state.session["user"]
        return app.state.workflow.activate_standard(standard_id, user["display_name"], user["id"])

    @app.get("/api/standards/{standard_id}/versions")
    def versions(standard_id: str):
        return app.state.workflow.versions(standard_id)

    @app.get("/api/standards/{standard_id}/diff")
    def diff(standard_id: str, against: str | None = None):
        return app.state.workflow.diff(standard_id, against)

    @app.post("/api/standards/{standard_id}/revision-proposals")
    def propose_revision(
        standard_id: str,
        instruction: Annotated[str, Form(max_length=20000)] = "",
        file: Annotated[UploadFile | None, File()] = None,
    ):
        content = None
        if file is not None and file.filename:
            content = file.file.read(MAX_REQUEST_BYTES + 1)
            if len(content) > MAX_REQUEST_BYTES:
                raise HTTPException(413, "Files must not exceed 20 MB.")
        return app.state.workflow.propose_revision(
            standard_id, instruction, file.filename if file else None, content
        )

    @app.post("/api/standards/{standard_id}/revisions", status_code=201)
    def apply_revision(standard_id: str, data: RevisionApply, request: Request):
        user = request.state.session["user"]
        return app.state.workflow.apply_revision(
            standard_id, data, user["display_name"], user["id"]
        )

    @app.delete("/api/standards/{standard_id}")
    def discard(standard_id: str):
        return app.state.workflow.discard_standard(standard_id)

    @app.get("/api/standards/{standard_id}/document/{extension}")
    def standard_document(
        standard_id: str, extension: Literal["pdf", "xlsx"], lang: Language = "en"
    ):
        standard, path = app.state.workflow.standard_document(standard_id, extension, lang)
        name = f"{standard.document_number}-rev-{standard.revision}".replace(" ", "_")
        suffix = "" if lang == "en" else f"-{lang}"
        return FileResponse(path, filename=f"{name}{suffix}.{extension}")

    @app.get("/api/requests/{stored_name}")
    def request_file(stored_name: str):
        path = app.state.workflow.request_file_path(stored_name)
        return FileResponse(path, filename="request" + path.suffix)

    @app.post("/api/standards/{standard_id}/source")
    def upload_source(standard_id: str, file: Annotated[UploadFile, File()]):
        workflow = app.state.workflow
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".pdf", ".xlsx"}:
            raise HTTPException(422, "Only PDF or XLSX files can be uploaded.")
        content = file.file.read(20 * 1024 * 1024 + 1)
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(413, "Files must not exceed 20 MB.")
        if not content.startswith(b"%PDF-" if suffix == ".pdf" else b"PK"):
            raise HTTPException(422, "Check the file format.")
        with workflow.lock:
            standard = workflow.store.standard(standard_id)
            if standard.status != "DRAFT":
                raise Conflict("The source of an active standard cannot be changed.")
            digest = hashlib.sha256(content).hexdigest()
            root = settings.data_dir / "sources"
            root.mkdir(exist_ok=True)
            filename = digest + suffix
            (root / filename).write_bytes(content)
            standard.source_file, standard.source_hash = filename, digest
            workflow.store.save_standard(standard)
            return standard

    @app.get("/api/standards/{standard_id}/source")
    def source(standard_id: str):
        workflow = app.state.workflow
        with workflow.lock:
            standard = workflow.store.standard(standard_id)
            if not standard.source_file:
                raise KeyError("No source file has been uploaded.")
            return FileResponse(
                settings.data_dir / "sources" / standard.source_file,
                filename="standard" + Path(standard.source_file).suffix,
            )

    # --- customer forms ------------------------------------------------------------------

    @app.post("/api/forms", status_code=201)
    def read_customer_form(
        file: Annotated[UploadFile, File()],
        provenance: Annotated[Literal["INTERNAL", "SYNTHETIC"], Form()] = "INTERNAL",
    ):
        content = file.file.read(MAX_FORM_BYTES + 1)
        intake = app.state.intake
        reading = intake.read(file.filename or "form.pdf", content, provenance)
        return {**reading, "draft": intake.draft(reading)}

    @app.get("/api/forms/samples")
    def form_samples():
        manifest = json.loads(FORM_SAMPLES.read_text("utf-8"))["samples"]
        return [
            {"id": index, "language": item["language"], "label": item["label"]}
            for index, item in enumerate(manifest)
        ]

    @app.post("/api/forms/samples/{sample_id}", status_code=201)
    def read_form_sample(sample_id: int):
        manifest = json.loads(FORM_SAMPLES.read_text("utf-8"))["samples"]
        if not 0 <= sample_id < len(manifest):
            raise HTTPException(404, "That sample form does not exist.")
        path = FORM_SAMPLES.parents[1] / manifest[sample_id]["file"]
        intake = app.state.intake
        reading = intake.read(path.name, path.read_bytes(), "SYNTHETIC")
        return {**reading, "draft": intake.draft(reading)}

    @app.get("/api/forms/{reading_id}")
    def customer_form_reading(reading_id: str):
        intake = app.state.intake
        reading = intake.get(reading_id)
        return {**reading, "draft": intake.draft(reading)}

    @app.get("/api/forms/{reading_id}/file")
    def customer_form_file(reading_id: str):
        reading = app.state.intake.get(reading_id)
        return FileResponse(
            app.state.intake.source(reading["stored"]),
            media_type="application/pdf",
            filename=Path(reading["filename"]).name,
            content_disposition_type="inline",
        )

    @app.post("/api/forms/{reading_id}/standard", status_code=201)
    def save_form_standard(reading_id: str, data: FormStandardSave, request: Request):
        user = request.state.session["user"]
        return app.state.intake.save_standard(
            reading_id, data.standard, data.previous_id, user["display_name"], user["id"]
        )

    @app.get("/api/glossary")
    def glossary():
        from spec_agent.reports.i18n import resource

        return resource()["glossary"]

    def save_fetched_original(program_id: str, url: str) -> dict:
        found = app.state.fetch_original(url)
        return app.state.programs.save_original(
            program_id, found["text"], found["source"], found["files"], found["skipped"]
        )

    @app.post("/api/programs/{program_id}/ai-check")
    def program_ai_check(program_id: str):
        settings = app.state.workflow.settings.llm
        if settings.provider == "none":
            raise HTTPException(409, "No AI model is connected. Add one in AI settings.")
        if settings.external and not settings.allow_internal:
            raise HTTPException(
                409, "Company data is kept from external AI. Allow it in AI settings first."
            )
        program = app.state.programs.get(program_id)
        original_error = None
        if not program.get("original") and program.get("url"):
            # Read the full notice first: the feed summary rarely holds every condition.
            try:
                save_fetched_original(program_id, program["url"])
            except OriginalError as error:
                original_error = str(error)
        client = app.state.assistant.client or LLMClient(settings)
        try:
            return app.state.programs.ai_check(
                program_id, client, client.label, original_error=original_error
            )
        except LLMError as error:
            raise HTTPException(502, f"The AI check failed: {error}") from None

    @app.get("/api/programs/{program_id}/original")
    def program_original(program_id: str):
        try:
            return app.state.programs.original(program_id)
        except KeyError:
            raise HTTPException(404, "No original notice is saved yet.") from None

    @app.post("/api/programs/{program_id}/original")
    def program_original_fetch(program_id: str, data: OriginalFetch):
        url = (data.url or app.state.programs.get(program_id).get("url") or "").strip()
        if not url:
            raise HTTPException(400, "This notice has no link. Upload the notice file instead.")
        try:
            return save_fetched_original(program_id, url)
        except OriginalError as error:
            raise HTTPException(502, str(error)) from None

    @app.post("/api/programs/{program_id}/original/file")
    def program_original_file(program_id: str, file: Annotated[UploadFile, File()]):
        app.state.programs.get(program_id)
        content = file.file.read(MAX_NOTICE_BYTES + 1)
        if len(content) > MAX_NOTICE_BYTES:
            raise HTTPException(413, "The file is larger than 10 MB.")
        name = file.filename or "notice"
        try:
            text = read_file(name, content)
        except OriginalError as error:
            raise HTTPException(400, str(error)) from None
        return app.state.programs.save_original(
            program_id, text, name, [{"name": name, "chars": len(text)}]
        )

    @app.get("/api/programs/feed")
    def notice_feed_status():
        return app.state.feed.status()

    @app.post("/api/programs/feed")
    def notice_feed_refresh():
        return app.state.feed.refresh()

    @app.post("/api/demo/shipyard")
    def load_shipyard_demo(request: Request):
        from spec_agent.services.shipyard_demo import seed_shipyard

        user = request.state.session["user"]
        return seed_shipyard(
            app.state.intake,
            app.state.workflow,
            app.state.programs,
            user["display_name"],
            user["id"],
        )

    @app.post("/api/forms/{reading_id}/limit-approval", status_code=201)
    def save_form_limits(reading_id: str, data: LimitApprovalSave, request: Request):
        user = request.state.session["user"]
        return app.state.intake.save_limits(reading_id, data.customer, user["display_name"])

    @app.get("/api/limit-approvals")
    def limit_approvals(customer: str | None = None):
        return app.state.intake.limit_approvals(customer)

    @app.get("/api/form-profiles")
    def form_profiles():
        workflow = app.state.workflow
        with workflow.lock:
            return [
                {key: profile[key] for key in ("family_id", "customer", "filename", "created_at")}
                for profile in workflow.store.form_profiles()
            ]

    @app.get("/api/standards/{standard_id}/customer-form")
    def customer_form_preview(standard_id: str):
        return app.state.intake.customer_form(standard_id)

    @app.get("/api/standards/{standard_id}/customer-form/{extension}")
    def customer_form_export(standard_id: str, extension: Literal["pdf", "xlsx"]):
        standard, path = app.state.intake.export(standard_id, extension)
        name = f"{standard.document_number}-rev-{standard.revision}-customer".replace(" ", "_")
        return FileResponse(path, filename=f"{name}.{extension}")

    # --- process flows and root-cause suggestions ----------------------------------------

    @app.get("/api/process-library")
    def process_library_view():
        return process_library()

    @app.get("/api/process-flows")
    def process_flows():
        return app.state.traceback.flows()

    @app.post("/api/process-flows", status_code=201)
    def create_process_flow(data: FlowInput):
        return app.state.traceback.save_flow(data)

    @app.put("/api/process-flows/{flow_id}")
    def update_process_flow(flow_id: str, data: FlowInput):
        return app.state.traceback.save_flow(data, flow_id)

    @app.delete("/api/process-flows/{flow_id}")
    def delete_process_flow(flow_id: str):
        return app.state.traceback.delete_flow(flow_id)

    @app.post("/api/traceback", status_code=201)
    def traceback_defect(data: TracebackInput):
        return app.state.traceback.from_defect(data)

    @app.post("/api/traceback/ncr", status_code=201)
    def traceback_ncr(
        file: Annotated[UploadFile, File()],
        flow_id: Annotated[str | None, Form()] = None,
    ):
        content = file.file.read(MAX_NCR_BYTES + 1)
        return app.state.traceback.from_ncr(file.filename or "ncr.pdf", content, flow_id or None)

    @app.get("/api/traceback/cases")
    def traceback_cases(customer: str | None = None):
        return app.state.traceback.cases(customer)

    @app.get("/api/traceback/{case_id}")
    def traceback_case(case_id: str):
        return app.state.traceback.case(case_id)

    @app.post("/api/traceback/{case_id}/save")
    def save_traceback_case(case_id: str, data: CaseSave, request: Request):
        user = request.state.session["user"]
        return app.state.traceback.save_case(case_id, data, user["display_name"])

    # --- automation: watched folders, tasks and calendar ----------------------------------

    @app.post("/api/assistant")
    def assistant(data: AssistantInput, request: Request):
        return app.state.assistant.handle(data, request.state.session["user"]["display_name"])

    @app.get("/api/home")
    def home():
        return app.state.assistant.home()

    @app.get("/api/automation")
    def automation_status():
        automation = app.state.automation
        return {**automation.status(), "jobs": automation.jobs()[:50]}

    @app.post("/api/automation/scan")
    def automation_scan():
        return app.state.automation.scan(settle=False)

    @app.get("/api/automation/jobs/{job_id}/report")
    def automation_report(job_id: str):
        path = app.state.automation.report_path(job_id)
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=path.name,
            content_disposition_type="inline",
        )

    # --- support programmes (local business growth) ----------------------------------------

    @app.get("/api/company-profile")
    def company_profile():
        return app.state.programs.profile()

    @app.put("/api/company-profile")
    def save_company_profile(data: CompanyProfile):
        return app.state.programs.save_profile(data)

    @app.get("/api/programs")
    def programs():
        return app.state.programs.list()

    @app.get("/api/programs/summary")
    def programs_summary():
        return app.state.programs.summary()

    @app.post("/api/programs", status_code=201)
    def add_program(data: NoticeInput):
        return app.state.programs.add(data.text, data.filename)

    @app.post("/api/programs/file", status_code=201)
    def add_program_file(file: Annotated[UploadFile, File()]):
        content = file.file.read(MAX_NOTICE_BYTES + 1)
        return app.state.programs.add_file(file.filename or "notice.pdf", content)

    @app.post("/api/programs/samples", status_code=201)
    def add_program_samples():
        return app.state.programs.load_samples()

    @app.get("/api/programs/{program_id}")
    def program(program_id: str):
        return app.state.programs.get(program_id)

    @app.post("/api/programs/{program_id}/tasks")
    def program_tasks(program_id: str):
        return app.state.programs.register_tasks(program_id)

    @app.delete("/api/programs/{program_id}")
    def delete_program(program_id: str):
        return app.state.programs.delete(program_id)

    @app.get("/api/tasks")
    def tasks():
        return app.state.automation.tasks()

    @app.post("/api/tasks", status_code=201)
    def add_task(data: TaskInput, request: Request):
        return app.state.automation.add_task(data, request.state.session["user"]["display_name"])

    @app.patch("/api/tasks/{task_id}")
    def update_task(task_id: str, data: TaskUpdate):
        return app.state.automation.update_task(task_id, data)

    @app.delete("/api/tasks/{task_id}")
    def delete_task(task_id: str):
        return app.state.automation.delete_task(task_id)

    @app.get("/api/inspections")
    def inspections():
        return app.state.workflow.list()

    @app.post("/api/inspections", status_code=201)
    def create_inspection(data: InspectionInput):
        return app.state.workflow.create(data)

    @app.get("/api/inspections/{inspection_id}")
    def inspection(inspection_id: str):
        return app.state.workflow.get(inspection_id)

    @app.post("/api/inspections/{inspection_id}/actions")
    def act(inspection_id: str, data: ActionInput, request: Request):
        user = request.state.session["user"]
        action = Action(**data.model_dump(), actor=user["display_name"], actor_user_id=user["id"])
        return app.state.workflow.act(inspection_id, action)

    @app.post("/api/inspections/{inspection_id}/retry")
    def retry(inspection_id: str):
        return app.state.workflow.retry(inspection_id)

    @app.get("/api/inspections/{inspection_id}/reports/{extension}")
    def download(inspection_id: str, extension: Literal["pdf", "xlsx"], lang: Language = "en"):
        path = app.state.workflow.report_path(inspection_id, extension, lang)
        suffix = "" if lang == "en" else f"-{lang}"
        return FileResponse(
            path, filename=f"inspection-{inspection_id}-{path.parent.name}{suffix}.{extension}"
        )

    # --- AI connection settings ------------------------------------------------------------

    @app.get("/api/ai-settings")
    def ai_settings():
        store = app.state.ai_settings
        _, source = store.effective()
        return store.public(app.state.workflow.settings.llm, source)

    @app.put("/api/ai-settings")
    def save_ai_settings(data: AISettingsInput):
        store = app.state.ai_settings
        current = app.state.workflow.settings.llm
        candidate = store.build(data, current)
        test = None
        if candidate.provider != "none":
            if (
                candidate.provider in {"openai", "anthropic"}
                and candidate.external
                and not (candidate.api_key)
            ):
                raise ValueError("Enter the API key.")
            if data.test:
                try:
                    test = {"ok": LLMClient(candidate).ping()["ok"], "error": None}
                except LLMError as error:
                    test = {"ok": False, "error": str(error)}
                if not test["ok"]:
                    _, source = store.effective()
                    return {
                        "saved": False,
                        "test": test,
                        "settings": store.public(current, source),
                    }
        store.save(candidate)
        apply_llm(candidate)
        return {"saved": True, "test": test, "settings": store.public(candidate, "app")}

    @app.delete("/api/ai-settings")
    def reset_ai_settings():
        store = app.state.ai_settings
        store.clear()
        effective, source = store.effective()
        apply_llm(effective)
        return store.public(effective, source)

    # --- customers and customer requests ---------------------------------------------------

    def actor(request: Request) -> tuple[str, str]:
        user = request.state.session["user"]
        return user["display_name"], user["id"]

    @app.get("/api/customers")
    def customers():
        return app.state.desk.customers()

    @app.post("/api/customers", status_code=201)
    def create_customer(data: CustomerInput):
        return app.state.desk.create_customer(data)

    @app.put("/api/customers/{customer_id}")
    def update_customer(customer_id: str, data: CustomerInput):
        return app.state.desk.update_customer(customer_id, data)

    @app.get("/api/customers/detail")
    def customer_detail(name: str, part: str | None = None):
        return app.state.desk.customer(name, part)

    @app.get("/api/customer-requests")
    def customer_requests(customer: str | None = None, status: Status | None = None):
        return app.state.desk.requests(customer, status)

    @app.post("/api/customer-requests", status_code=201)
    def create_customer_request(
        request: Request,
        customer: Annotated[str, Form(max_length=100)] = "",
        part_numbers: Annotated[str, Form(max_length=2000)] = "",
        subject: Annotated[str, Form(max_length=300)] = "",
        channel: Annotated[Channel, Form()] = "EMAIL",
        sender: Annotated[str, Form(max_length=300)] = "",
        received_at: Annotated[str, Form(max_length=40)] = "",
        body: Annotated[str, Form(max_length=60000)] = "",
        response_language: Annotated[ReplyLanguage | None, Form()] = None,
        files: Annotated[list[UploadFile] | None, File()] = None,
    ):
        contents = []
        for upload in files or []:
            if not upload.filename:
                continue
            content = upload.file.read(MAX_REQUEST_BYTES + 1)
            if len(content) > MAX_REQUEST_BYTES:
                raise HTTPException(413, "Files must not exceed 20 MB.")
            contents.append((upload.filename, content))
        name, user_id = actor(request)
        return app.state.desk.create_request(
            customer=customer,
            part_numbers=[part for part in part_numbers.split(",") if part.strip()],
            subject=subject,
            channel=channel,
            sender=sender,
            received_at=received_at,
            body=body,
            response_language=response_language,
            files=contents,
            actor=name,
            actor_user_id=user_id,
        )

    @app.get("/api/customer-requests/{request_id}")
    def customer_request(request_id: str):
        with app.state.workflow.lock:
            return app.state.workflow.store.customer_request(request_id)

    @app.patch("/api/customer-requests/{request_id}")
    def update_customer_request(request_id: str, data: RequestUpdate, request: Request):
        return app.state.desk.update(request_id, data, *actor(request))

    @app.post("/api/customer-requests/{request_id}/analyze")
    def analyze_customer_request(request_id: str, data: AnalyzeInput, request: Request):
        return app.state.desk.analyze(request_id, data.response_language, *actor(request))

    @app.post("/api/customer-requests/{request_id}/notes")
    def note_customer_request(request_id: str, data: NoteInput, request: Request):
        return app.state.desk.add_note(request_id, data.note, *actor(request))

    @app.post("/api/customer-requests/{request_id}/respond")
    def respond_customer_request(request_id: str, data: RespondInput, request: Request):
        return app.state.desk.respond(request_id, data, *actor(request))

    @app.get("/api/customer-requests/{request_id}/response.eml")
    def response_eml(request_id: str):
        item, content = app.state.desk.response_eml(request_id)
        return Response(
            content,
            media_type="message/rfc822",
            headers={"Content-Disposition": f'attachment; filename="reply-{item.id[:8]}.eml"'},
        )

    @app.get("/api/customer-requests/{request_id}/attachments/{stored_name}")
    def request_attachment(request_id: str, stored_name: str):
        path = app.state.desk.attachment_path(request_id, stored_name)
        item = app.state.workflow.store.customer_request(request_id)
        filename = next(a.filename for a in item.attachments if a.stored_name == stored_name)
        return FileResponse(path, filename=filename)

    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app
