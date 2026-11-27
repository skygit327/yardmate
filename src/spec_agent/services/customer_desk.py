"""Customer management: customer registry, customer requests, and customer/part history."""

import hashlib
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from uuid import uuid4

from spec_agent.schemas.customer_request import (
    Attachment,
    Customer,
    CustomerInput,
    CustomerRequest,
    RequestEvent,
    RequestUpdate,
    RespondInput,
)
from spec_agent.services.documents import MAX_REQUEST_BYTES, extract_text
from spec_agent.services.request_agent import RequestAgent
from spec_agent.storage.database import timestamp

TEXT_SUFFIXES = {".pdf", ".xlsx", ".docx", ".txt", ".md", ".eml"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
SIGNATURES = {
    ".pdf": b"%PDF-",
    ".xlsx": b"PK",
    ".docx": b"PK",
    ".png": b"\x89PNG",
    ".jpg": b"\xff\xd8",
    ".jpeg": b"\xff\xd8",
}
MAX_FILES = 10


def key(name: str) -> str:
    return " ".join(name.split()).casefold()


def suffix_of(filename: str) -> str:
    return ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""


class CustomerDesk:
    def __init__(self, workflow, llm_client=None):
        self.workflow = workflow
        self.store = workflow.store
        self.lock = workflow.lock
        self.settings = workflow.settings
        self.agent = RequestAgent(self.settings.llm, llm_client)

    def set_llm(self, llm, client=None):
        self.settings = self.workflow.settings
        self.agent = RequestAgent(llm, client)

    # --- files ------------------------------------------------------------------------------

    def _store_file(self, filename: str, content: bytes) -> Attachment:
        suffix = suffix_of(filename)
        if suffix not in TEXT_SUFFIXES | IMAGE_SUFFIXES:
            raise ValueError("Attach PDF, XLSX, DOCX, EML, TXT, MD, PNG, or JPG files.")
        if len(content) > MAX_REQUEST_BYTES:
            raise ValueError("Files must not exceed 20 MB.")
        if suffix in SIGNATURES and not content.startswith(SIGNATURES[suffix]):
            raise ValueError("Check the file format.")
        digest = hashlib.sha256(content).hexdigest()
        root = self.settings.data_dir / "requests"
        root.mkdir(exist_ok=True)
        stored = digest + suffix
        (root / stored).write_bytes(content)
        return Attachment(
            stored_name=stored, filename=filename[:255], sha256=digest, size=len(content)
        )

    def _read_mail(self, content: bytes) -> dict:
        message = BytesParser(policy=policy.default).parsebytes(content)
        body_part = message.get_body(preferencelist=("plain", "html"))
        body = ""
        if body_part is not None:
            body = body_part.get_content()
            if body_part.get_content_subtype() == "html":
                body = re.sub(r"<[^>]+>", " ", body)
        received = ""
        if message.get("date"):
            try:
                received = parsedate_to_datetime(message["date"]).isoformat()
            except (TypeError, ValueError):
                received = ""
        attachments = []
        for part in message.iter_attachments():
            filename = part.get_filename() or ""
            if suffix_of(filename) in TEXT_SUFFIXES | IMAGE_SUFFIXES:
                payload = part.get_payload(decode=True) or b""
                attachments.append((filename, payload))
        return {
            "subject": str(message.get("subject", "")),
            "sender": str(message.get("from", "")),
            "received_at": received,
            "body": body.strip(),
            "attachments": attachments[:MAX_FILES],
        }

    def attachment_path(self, request_id: str, stored_name: str):
        with self.lock:
            request = self.store.customer_request(request_id)
            if not any(item.stored_name == stored_name for item in request.attachments):
                raise KeyError("The attachment could not be found.")
        return self.workflow.request_file_path(stored_name)

    # --- requests ---------------------------------------------------------------------------

    def create_request(
        self,
        *,
        customer: str = "",
        part_numbers: list[str] | None = None,
        subject: str = "",
        channel: str = "EMAIL",
        sender: str = "",
        received_at: str = "",
        body: str = "",
        response_language: str | None = None,
        files: list[tuple[str, bytes]] | None = None,
        actor: str = "System",
        actor_user_id: str | None = None,
    ) -> CustomerRequest:
        files = list(files or [])
        if len(files) > MAX_FILES:
            raise ValueError(f"Attach at most {MAX_FILES} files.")
        attachments, extracted = [], []
        for filename, content in files:
            attachment = self._store_file(filename, content)
            attachments.append(attachment)
            suffix = suffix_of(filename)
            if suffix == ".eml":
                mail = self._read_mail(content)
                subject = subject or mail["subject"]
                sender = sender or mail["sender"]
                received_at = received_at or mail["received_at"]
                body = (body + "\n\n" + mail["body"]).strip() if body else mail["body"]
                for inner_name, inner_content in mail["attachments"]:
                    try:
                        attachments.append(self._store_file(inner_name, inner_content))
                    except ValueError:
                        continue
                    if suffix_of(inner_name) in TEXT_SUFFIXES - {".eml"}:
                        text = extract_text(suffix_of(inner_name), inner_content)
                        if text:
                            extracted.append(f"[{inner_name}]\n{text}")
            elif suffix in TEXT_SUFFIXES:
                text = extract_text(suffix, content)
                if text:
                    extracted.append(f"[{filename}]\n{text}")
        if extracted:
            body = (body + "\n\n" + "\n\n".join(extracted)).strip()
        if not body.strip() and not subject.strip():
            raise ValueError("Enter the request text or attach a file.")
        now = timestamp()
        record = self._customer_record(customer)
        request = CustomerRequest(
            id=str(uuid4()),
            created_at=now,
            updated_at=now,
            customer=self.canonical(customer)[:100],
            part_numbers=[part.strip() for part in part_numbers or [] if part.strip()][:20],
            subject=(subject.strip() or body.strip().split("\n")[0])[:300],
            channel=channel,
            sender=sender[:300],
            received_at=received_at or now,
            body=body[:60000],
            attachments=attachments,
            response_language=response_language or (record.reply_language if record else "en"),
            events=[
                RequestEvent(at=now, actor=actor, actor_user_id=actor_user_id, kind="RECEIVED")
            ],
        )
        self._analyze(
            request,
            request.response_language,
            actor,
            actor_user_id,
            adopt=True,
            auto_language=response_language is None,
        )
        with self.lock:
            self.store.save_customer_request(request)
        return request

    def _context(self):
        with self.lock:
            standards = self.store.standards()
        inspections = self.workflow.list()
        return standards, inspections

    def _analyze(self, request, language, actor, actor_user_id, adopt=False, auto_language=False):
        standards, inspections = self._context()
        analysis = self.agent.analyze(request, standards, inspections, language)
        analysis = self.agent.refine(request, analysis, language)
        request.analysis = analysis
        request.response_language = language
        request.response_draft = analysis.pop("response_draft")
        if adopt:
            if not request.customer and len(analysis["mentions"]["customers"]) == 1:
                request.customer = analysis["mentions"]["customers"][0]
                record = self._customer_record(request.customer)
                if auto_language and record and record.reply_language != language:
                    return self._analyze(
                        request, record.reply_language, actor, actor_user_id, adopt=True
                    )
            if not request.part_numbers:
                request.part_numbers = analysis["mentions"]["parts"][:20]
            request.category = analysis["category"]
            request.priority = analysis["priority"]
        request.events.append(
            RequestEvent(
                at=timestamp(),
                actor=actor,
                actor_user_id=actor_user_id,
                kind="ANALYZED",
                note=analysis["interpreter"],
            )
        )
        request.updated_at = timestamp()
        return request

    def analyze(self, request_id, language, actor, actor_user_id=None) -> CustomerRequest:
        with self.lock:
            request = self.store.customer_request(request_id)
        self._analyze(request, language or request.response_language, actor, actor_user_id)
        with self.lock:
            self.store.save_customer_request(request)
        return request

    def update(self, request_id, data: RequestUpdate, actor, actor_user_id=None):
        with self.lock:
            request = self.store.customer_request(request_id)
            changes = {}
            for field, value in data.model_dump(exclude_unset=True, exclude={"note"}).items():
                if value is None and field != "due_date":
                    continue
                if field == "customer":
                    value = self.canonical(value)
                if field == "part_numbers":
                    value = [part.strip() for part in value if part.strip()]
                if getattr(request, field) != value:
                    if field != "response_draft":
                        changes[field] = {"before": getattr(request, field), "after": value}
                    setattr(request, field, value)
            kind = "UPDATED"
            if "status" in changes:
                after = changes["status"]["after"]
                kind = "CLOSED" if after == "CLOSED" else "REOPENED" if after == "OPEN" else kind
            if changes or data.note:
                request.events.append(
                    RequestEvent(
                        at=timestamp(),
                        actor=actor,
                        actor_user_id=actor_user_id,
                        kind=kind,
                        note=data.note,
                        changes=changes,
                    )
                )
            request.updated_at = timestamp()
            self.store.save_customer_request(request)
            return request

    def add_note(self, request_id, note, actor, actor_user_id=None):
        with self.lock:
            request = self.store.customer_request(request_id)
            request.events.append(
                RequestEvent(
                    at=timestamp(), actor=actor, actor_user_id=actor_user_id, kind="NOTE", note=note
                )
            )
            request.updated_at = timestamp()
            self.store.save_customer_request(request)
            return request

    def respond(self, request_id, data: RespondInput, actor, actor_user_id=None):
        with self.lock:
            request = self.store.customer_request(request_id)
            if request.status == "CLOSED":
                raise ValueError("Reopen the request before recording a response.")
            final = (data.final_response or request.response_draft).strip()
            if not final:
                raise ValueError("Write the response before marking it as sent.")
            request.final_response = final
            request.response_draft = final
            request.status = "RESPONDED"
            request.events.append(
                RequestEvent(
                    at=timestamp(),
                    actor=actor,
                    actor_user_id=actor_user_id,
                    kind="RESPONDED",
                    note=data.note,
                )
            )
            request.updated_at = timestamp()
            self.store.save_customer_request(request)
            return request

    def requests(self, customer: str | None = None, status: str | None = None) -> list[dict]:
        with self.lock:
            items = self.store.customer_requests()
        return [
            self.request_summary(item)
            for item in items
            if (customer is None or key(item.customer) == key(customer))
            and (status is None or item.status == status)
        ]

    @staticmethod
    def request_summary(item: CustomerRequest) -> dict:
        return {
            "id": item.id,
            "subject": item.subject,
            "customer": item.customer,
            "part_numbers": item.part_numbers,
            "channel": item.channel,
            "status": item.status,
            "category": item.category,
            "priority": item.priority,
            "received_at": item.received_at,
            "updated_at": item.updated_at,
            "due_date": item.due_date,
            "attachments": len(item.attachments),
        }

    def response_eml(self, request_id: str) -> tuple[CustomerRequest, bytes]:
        with self.lock:
            request = self.store.customer_request(request_id)
        message = EmailMessage()
        if request.sender:
            message["To"] = request.sender
        subject = request.subject or "Customer request"
        message["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        message["X-Unsent"] = "1"
        message.set_content(request.final_response or request.response_draft or "")
        return request, bytes(message)

    # --- customers --------------------------------------------------------------------------

    def _customer_record(self, name: str) -> Customer | None:
        if not name or not name.strip():
            return None
        return next((item for item in self.store.customers() if key(item.name) == key(name)), None)

    def canonical(self, name: str) -> str:
        """Use the spelling already known for a customer (registry, standards, requests)."""
        name = " ".join((name or "").split())
        if not name:
            return ""
        with self.lock:
            known = [
                *(item.name for item in self.store.customers()),
                *(item.customer for item in self.store.standards()),
                *(item.customer for item in self.store.customer_requests()),
            ]
        return next((item for item in known if key(item) == key(name)), name)

    def create_customer(self, data: CustomerInput) -> Customer:
        with self.lock:
            if self._customer_record(data.name):
                raise ValueError("This customer is already registered.")
            now = timestamp()
            customer = Customer(
                **data.model_dump(), id=str(uuid4()), created_at=now, updated_at=now
            )
            self.store.save_customer(customer)
            return customer

    def update_customer(self, customer_id: str, data: CustomerInput) -> Customer:
        with self.lock:
            customer = next(
                (item for item in self.store.customers() if item.id == customer_id), None
            )
            if customer is None:
                raise KeyError("The customer could not be found.")
            if key(data.name) != key(customer.name):
                raise ValueError("The customer name cannot be changed after registration.")
            updated = Customer(
                **{**data.model_dump(), "name": customer.name},
                id=customer.id,
                created_at=customer.created_at,
                updated_at=timestamp(),
            )
            self.store.save_customer(updated)
            return updated

    def _collect(self):
        with self.lock:
            records = self.store.customers()
            standards = self.store.standards()
            requests = self.store.customer_requests()
        inspections = self.workflow.list()
        return records, standards, requests, inspections

    def customers(self) -> list[dict]:
        records, standards, requests, inspections = self._collect()
        names: dict[str, str] = {}
        for name in [
            *(item.name for item in records),
            *(item.customer for item in standards),
            *(item.customer for item in requests),
        ]:
            if name and name.strip():
                names.setdefault(key(name), name.strip())
        overview = []
        for customer_key, name in names.items():
            record = next((item for item in records if key(item.name) == customer_key), None)
            own_standards = [item for item in standards if key(item.customer) == customer_key]
            own_requests = [item for item in requests if key(item.customer) == customer_key]
            own_inspections = [
                item for item in inspections if key(item["standard"]["customer"]) == customer_key
            ]
            parts = {item.part_number: item.product_name for item in own_standards}
            for item in own_requests:
                for part in item.part_numbers:
                    parts.setdefault(part, "")
            activity = [
                *(item.updated_at for item in own_requests),
                *(item["created_at"] for item in own_inspections),
                *(entry.at for item in own_standards for entry in item.history),
            ]
            overview.append(
                {
                    "name": name,
                    "registered": record is not None,
                    "record": record.model_dump(mode="json") if record else None,
                    "parts": [
                        {"part_number": part, "product_name": product}
                        for part, product in parts.items()
                    ],
                    "active_standards": sum(item.status == "ACTIVE" for item in own_standards),
                    "requests": len(own_requests),
                    "open_requests": sum(
                        item.status in {"OPEN", "IN_PROGRESS"} for item in own_requests
                    ),
                    "inspections": len(own_inspections),
                    "failed_inspections": sum(
                        item["overall_result"] == "FAIL" for item in own_inspections
                    ),
                    "last_activity": max(activity) if activity else None,
                }
            )
        return sorted(
            overview, key=lambda item: (item["last_activity"] or "", item["name"]), reverse=True
        )

    def customer(self, name: str, part: str | None = None) -> dict:
        overview = next((item for item in self.customers() if key(item["name"]) == key(name)), None)
        if overview is None:
            raise KeyError("The customer could not be found.")
        _, standards, requests, inspections = self._collect()
        customer_key = key(name)
        own_standards = [item for item in standards if key(item.customer) == customer_key]
        own_requests = [item for item in requests if key(item.customer) == customer_key]
        own_inspections = [
            item for item in inspections if key(item["standard"]["customer"]) == customer_key
        ]
        parts = []
        for entry in overview["parts"]:
            number = entry["part_number"]
            versions = [item for item in own_standards if item.part_number == number]
            active = next((item for item in versions if item.status == "ACTIVE"), None)
            draft = next((item for item in versions if item.status == "DRAFT"), None)
            part_inspections = [
                item for item in own_inspections if item["standard"]["part_number"] == number
            ]
            parts.append(
                {
                    **entry,
                    "standard": {
                        "id": active.id,
                        "lineage": active.lineage,
                        "document_number": active.document_number,
                        "revision": active.revision,
                    }
                    if active
                    else None,
                    "draft_revision": draft.revision if draft else None,
                    "versions": len(versions),
                    "inspections": len(part_inspections),
                    "last_result": part_inspections[0]["overall_result"]
                    if part_inspections
                    else None,
                    "open_requests": sum(
                        number in item.part_numbers and item.status in {"OPEN", "IN_PROGRESS"}
                        for item in own_requests
                    ),
                }
            )

        def matches(part_numbers):
            return part is None or part in part_numbers

        timeline = []
        for item in own_requests:
            if not matches(item.part_numbers):
                continue
            for event in item.events:
                if event.kind in {"RECEIVED", "RESPONDED", "CLOSED", "REOPENED", "NOTE"}:
                    timeline.append(
                        {
                            "at": item.received_at if event.kind == "RECEIVED" else event.at,
                            "kind": f"REQUEST_{event.kind}",
                            "title": item.subject,
                            "detail": event.note or item.category,
                            "actor": event.actor,
                            "part_numbers": item.part_numbers,
                            "ref": {"type": "request", "id": item.id},
                        }
                    )
        for item in own_standards:
            if not matches([item.part_number]):
                continue
            for entry in item.history:
                timeline.append(
                    {
                        "at": entry.at,
                        "kind": f"STANDARD_{entry.kind}",
                        "title": f"{item.part_number} Rev {item.revision}",
                        "detail": entry.summary.split("\n")[0]
                        if entry.summary
                        else item.document_number,
                        "actor": entry.actor,
                        "part_numbers": [item.part_number],
                        "ref": {"type": "standard", "id": item.lineage},
                    }
                )
        for item in own_inspections:
            if not matches([item["standard"]["part_number"]]):
                continue
            timeline.append(
                {
                    "at": item["created_at"],
                    "kind": "INSPECTION",
                    "title": f"{item['standard']['part_number']} · {item['inputs']['lot']}",
                    "detail": f"{item['overall_result']} · {item['status']}",
                    "actor": item["inputs"]["inspector"],
                    "part_numbers": [item["standard"]["part_number"]],
                    "ref": {"type": "inspection", "id": item["inspection_id"]},
                }
            )
        timeline.sort(key=lambda entry: entry["at"], reverse=True)
        return {
            **overview,
            "part_filter": part,
            "parts": parts,
            "request_list": [
                self.request_summary(item) for item in own_requests if matches(item.part_numbers)
            ],
            "inspection_list": [
                {
                    "id": item["inspection_id"],
                    "lot": item["inputs"]["lot"],
                    "part_number": item["standard"]["part_number"],
                    "revision": item["standard"]["revision"],
                    "overall_result": item["overall_result"],
                    "status": item["status"],
                    "created_at": item["created_at"],
                }
                for item in own_inspections
                if matches([item["standard"]["part_number"]])
            ],
            "timeline": timeline[:300],
        }
