from typing import Literal

from pydantic import Field

from spec_agent.schemas.standard import StrictModel

Channel = Literal["EMAIL", "DOCUMENT", "PHONE", "MEETING", "OTHER"]
Status = Literal["OPEN", "IN_PROGRESS", "RESPONDED", "CLOSED"]
Category = Literal["COMPLAINT", "SPEC_CHANGE", "INQUIRY", "DELIVERY", "REPORT_REQUEST", "OTHER"]
Priority = Literal["LOW", "NORMAL", "HIGH"]
ReplyLanguage = Literal["en", "ko", "zh"]


class Attachment(StrictModel):
    stored_name: str
    filename: str
    sha256: str
    size: int


class RequestEvent(StrictModel):
    at: str
    actor: str
    actor_user_id: str | None = None
    kind: Literal["RECEIVED", "ANALYZED", "UPDATED", "NOTE", "RESPONDED", "CLOSED", "REOPENED"]
    note: str = Field(default="", max_length=5000)
    changes: dict = Field(default_factory=dict)


class CustomerRequest(StrictModel):
    id: str
    created_at: str
    updated_at: str
    customer: str = Field(default="", max_length=100)
    part_numbers: list[str] = Field(default_factory=list, max_length=20)
    subject: str = Field(default="", max_length=300)
    channel: Channel = "EMAIL"
    sender: str = Field(default="", max_length=300)
    received_at: str = ""
    body: str = Field(default="", max_length=60000)
    attachments: list[Attachment] = Field(default_factory=list)
    status: Status = "OPEN"
    category: Category = "OTHER"
    priority: Priority = "NORMAL"
    due_date: str | None = None
    analysis: dict | None = None
    response_language: ReplyLanguage = "en"
    response_draft: str = Field(default="", max_length=20000)
    final_response: str = Field(default="", max_length=20000)
    events: list[RequestEvent] = Field(default_factory=list)


class RequestUpdate(StrictModel):
    customer: str | None = Field(default=None, max_length=100)
    part_numbers: list[str] | None = Field(default=None, max_length=20)
    subject: str | None = Field(default=None, max_length=300)
    status: Status | None = None
    category: Category | None = None
    priority: Priority | None = None
    due_date: str | None = Field(default=None, max_length=40)
    response_draft: str | None = Field(default=None, max_length=20000)
    response_language: ReplyLanguage | None = None
    note: str = Field(default="", max_length=5000)


class AnalyzeInput(StrictModel):
    response_language: ReplyLanguage | None = None


class RespondInput(StrictModel):
    final_response: str | None = Field(default=None, max_length=20000)
    note: str = Field(default="", max_length=5000)


class NoteInput(StrictModel):
    note: str = Field(min_length=1, max_length=5000)


class Contact(StrictModel):
    name: str = Field(default="", max_length=100)
    role: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=60)


class CustomerInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(default="", max_length=40)
    country: str = Field(default="", max_length=60)
    reply_language: ReplyLanguage = "en"
    contacts: list[Contact] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=5000)


class Customer(CustomerInput):
    id: str
    created_at: str
    updated_at: str
