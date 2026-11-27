"""Customer form intake: read a customer's inspection form, let a person review it,
save it as a standard (or limit-sample approval) and write it back in the customer's layout.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from spec_agent.reports.customer_form import build_form, render_pdf, render_xlsx
from spec_agent.reports.rendering import font_name
from spec_agent.schemas.standard import Rule, Standard, StandardInput
from spec_agent.services.form_reader import read_form, render_figure
from spec_agent.services.revision_agent import LLMClient, LLMError, parse_json_object
from spec_agent.services.versioning import rule_criteria
from spec_agent.storage.database import timestamp

MAX_FORM_BYTES = 20 * 1024 * 1024
META_KEYS = ("original", "page", "confidence", "notes")

CELL_PROMPT = """You convert acceptance criteria from a customer's cosmetic inspection form
into structured rules. For every cell return its id, a kind
(NUMERIC, PROHIBITED, NOT_APPLICABLE or UNRESOLVED) and, for NUMERIC, the conditions:
field (snake_case English), label (the customer's own word for the dimension), operator
(lt, lte, gt, gte), limit (number) and unit (mm or µm). Use UNRESOLVED when the text
cannot be expressed as limits. Never invent limits that are not in the text.
Answer with JSON: {"rules": [{"id": "...", "kind": "...", "conditions": [...], "note": "..."}]}"""

FORM_PROMPT = """You read a customer's inspection standard and extract its criteria.
Return JSON: {"header": {"customer", "part_name", "product_name", "document_number",
"revision"}, "rules": [{"area", "item", "kind", "conditions": [{"field", "label",
"operator", "limit", "unit"}], "original"}]}. kind is NUMERIC, PROHIBITED,
NOT_APPLICABLE or UNRESOLVED; operator is lt, lte, gt or gte; unit is mm or µm.
field is snake_case English. Copy the original wording of each criterion into "original".
Never invent limits that are not in the text."""


def _criteria_key(rules) -> set:
    return {
        (rule.area, rule.item, rule_criteria(rule))
        for rule in (Rule.model_validate(r) if isinstance(r, dict) else r for r in rules)
    }


class FormIntake:
    def __init__(self, workflow, llm_client=None):
        self.workflow = workflow
        self.client = llm_client
        self.root = workflow.settings.data_dir / "intake"
        self.root.mkdir(parents=True, exist_ok=True)

    def set_llm(self, llm, client=None):
        self.client = client

    @property
    def settings(self):
        return self.workflow.settings.llm

    def _client(self):
        if self.client is not None:
            return self.client
        if self.settings.provider == "none":
            return None
        return LLMClient(self.settings)

    def _path(self, reading_id: str) -> Path:
        path = (self.root / f"{reading_id}.json").resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise KeyError("The reading could not be found.")
        return path

    # --- reading ---------------------------------------------------------------------------

    def read(self, filename: str, content: bytes, provenance: str = "INTERNAL") -> dict:
        if len(content) > MAX_FORM_BYTES:
            raise ValueError("Files must not exceed 20 MB.")
        if not content.startswith(b"%PDF-"):
            raise ValueError("Customer forms must be PDF files.")
        reading = read_form(filename, content).to_dict()
        digest = hashlib.sha256(content).hexdigest()
        (self.root / f"{digest}.pdf").write_bytes(content)
        reading_id = str(uuid4())
        reading.update(
            id=reading_id,
            provenance=provenance,
            stored=f"{digest}.pdf",
            sha256=digest,
            created_at=timestamp(),
            previous_id=None,
        )
        self._use_llm(reading)
        if reading["document_type"] == "INSPECTION_MATRIX" or reading["rules"]:
            self._compare_with_standards(reading)
        self._path(reading_id).write_text(json.dumps(reading, ensure_ascii=False), "utf-8")
        return reading

    def get(self, reading_id: str) -> dict:
        path = self._path(reading_id)
        if not path.is_file():
            raise KeyError("The reading could not be found.")
        return json.loads(path.read_text("utf-8"))

    def _allowed(self, reading: dict) -> bool:
        internal = reading["provenance"] == "INTERNAL"
        return not (internal and self.settings.external and not self.settings.allow_internal)

    def _use_llm(self, reading: dict):
        """Ask the AI only where the rules were unsure, and record why it was or wasn't used."""
        provider = self.settings.provider
        model = f"{provider}/{self.settings.model}" if provider != "none" else ""
        unresolved = [rule for rule in reading["rules"] if rule["confidence"] == "low"]
        unknown = reading["document_type"] == "UNKNOWN" and reading.get("text_preview")
        if provider == "none":
            reading["ai"] = {"status": "off", "model": "", "count": 0}
            return
        if not (unresolved or unknown):
            reading["ai"] = {"status": "not_needed", "model": model, "count": 0}
            return
        client = self._client() if self._allowed(reading) else None
        if client is None:
            reading["ai"] = {"status": "blocked", "model": model, "count": len(unresolved)}
            reading["warnings"].append({"level": "info", "code": "LLM_BLOCKED", "params": {}})
            return
        try:
            if unknown:
                self._llm_form(client, reading)
                count = len(reading["rules"]) if reading["document_type"] == "AI_EXTRACTED" else 0
            else:
                count = self._llm_cells(client, reading, unresolved)
            reading["ai"] = {"status": "used", "model": model, "count": count}
        except LLMError as error:
            reading["ai"] = {
                "status": "failed",
                "model": model,
                "count": 0,
                "error": str(error)[:200],
            }
            reading["warnings"].append(
                {"level": "warning", "code": "LLM_FAILED", "params": {"error": str(error)[:200]}}
            )

    def _llm_cells(self, client, reading, unresolved):
        payload = [
            {"id": r["id"], "item": r["item"], "area": r["area"], "text": r["original"]}
            for r in unresolved
        ]
        answer = parse_json_object(
            client.complete(CELL_PROMPT, json.dumps(payload, ensure_ascii=False))
        )
        by_id = {rule["id"]: rule for rule in reading["rules"]}
        changed = 0
        for proposal in answer.get("rules", []):
            rule = by_id.get(proposal.get("id"))
            if rule is None:
                continue
            candidate = {
                "id": rule["id"],
                "area": rule["area"],
                "item": rule["item"],
                "kind": proposal.get("kind"),
                "conditions": proposal.get("conditions") or [],
                "source": rule["source"],
            }
            try:
                Rule.model_validate(candidate)
            except ValidationError:
                continue
            rule.update(kind=candidate["kind"], conditions=candidate["conditions"])
            rule["notes"] = [
                *rule["notes"],
                {
                    "code": "AI_INTERPRETED",
                    "params": {"model": client.label, "note": str(proposal.get("note", ""))[:300]},
                },
            ]
            changed += 1
        reading["interpreter"] = f"rules+llm:{client.label}" if changed else reading["interpreter"]
        return changed

    def _llm_form(self, client, reading):
        answer = parse_json_object(client.complete(FORM_PROMPT, reading["text_preview"]))
        header = {k: str(v)[:100] for k, v in (answer.get("header") or {}).items() if v}
        reading["header"] = {**reading["header"], **header}
        rules = []
        for index, proposal in enumerate(answer.get("rules") or [], start=1):
            candidate = {
                "id": f"ai{index:02d}",
                "area": str(proposal.get("area") or "-")[:80],
                "item": str(proposal.get("item") or "-")[:100],
                "kind": proposal.get("kind"),
                "conditions": proposal.get("conditions") or [],
                "source": f"{reading['filename']}: {proposal.get('original', '')}"[:500],
            }
            try:
                Rule.model_validate(candidate)
            except ValidationError:
                continue
            rules.append(
                {
                    **candidate,
                    "original": str(proposal.get("original", "")),
                    "page": None,
                    "confidence": "medium",
                    "notes": [
                        {"code": "AI_INTERPRETED", "params": {"model": client.label, "note": ""}}
                    ],
                }
            )
        if rules:
            reading["rules"] = rules
            reading["document_type"] = "AI_EXTRACTED"
            reading["interpreter"] = f"llm:{client.label}"
            reading["warnings"] = [w for w in reading["warnings"] if w["code"] != "UNKNOWN_FORMAT"]
            reading["warnings"].append(
                {"level": "warning", "code": "AI_EXTRACTED", "params": {"count": len(rules)}}
            )

    def _compare_with_standards(self, reading: dict):
        header = reading["header"]
        customer = (header.get("customer") or "").casefold()
        document = (header.get("document_number") or "").casefold()
        mine = _criteria_key(_strip(rule) for rule in reading["rules"])
        with self.workflow.lock:
            standards = self.workflow.store.standards()
        families: dict[str, list[Standard]] = {}
        for standard in standards:
            families.setdefault(standard.lineage, []).append(standard)
        for versions in families.values():
            latest = max(versions, key=lambda item: item.version)
            if (
                customer
                and document
                and latest.customer.casefold() == customer
                and latest.document_number.casefold() == document
            ):
                reading["previous_id"] = latest.id
                reading["warnings"].append(
                    {
                        "level": "info",
                        "code": "EXISTING_STANDARD",
                        "params": {
                            "id": latest.id,
                            "revision": latest.revision,
                            "status": latest.status,
                            "version": latest.version,
                        },
                    }
                )
            elif mine:
                same = next(
                    (
                        v
                        for v in sorted(versions, key=lambda item: -item.version)
                        if _criteria_key(v.rules) == mine
                    ),
                    None,
                )
                if same is not None:
                    reading["warnings"].append(
                        {
                            "level": "info",
                            "code": "SAME_CRITERIA",
                            "params": {
                                "id": same.id,
                                "document_number": same.document_number,
                                "part_number": same.part_number,
                                "revision": same.revision,
                            },
                        }
                    )

    # --- saving ----------------------------------------------------------------------------

    def draft(self, reading: dict) -> dict:
        header = reading["header"]
        return {
            "customer": header.get("customer", ""),
            "part_number": header.get("part_name") or header.get("drawing_number") or "",
            "product_name": header.get("product_name") or header.get("part_name") or "",
            "document_number": header.get("document_number")
            or Path(reading["filename"]).stem[:100],
            "revision": header.get("revision") or "1",
            "provenance": reading["provenance"],
            "rules": [_strip(rule) for rule in reading["rules"]],
        }

    def save_standard(
        self,
        reading_id: str,
        data: StandardInput,
        previous_id: str | None,
        actor: str,
        actor_user_id: str | None,
    ) -> Standard:
        reading = self.get(reading_id)
        if reading.get("saved_standard_id"):
            raise ValueError("This reading has already been saved.")
        summary = f"Imported from customer form {reading['filename']}"
        workflow = self.workflow
        if previous_id:
            standard = workflow.import_version(
                previous_id, data, actor, actor_user_id, summary, reading["filename"]
            )
        else:
            standard = workflow.register_standard(data, actor, actor_user_id)
            with workflow.lock:
                standard.history[0].summary = summary
                standard.history[0].request_filename = reading["filename"]
                workflow.store.save_standard(standard)
        content = (self.root / reading["stored"]).read_bytes()
        standard = workflow.attach_source(standard.id, reading["filename"], content)
        if reading.get("layout") and reading["layout"].get("kind") == "INSPECTION_MATRIX":
            notes = {rule["id"]: rule.get("notes", []) for rule in reading["rules"]}
            figure_name = None
            layout = reading["layout"]
            try:
                figure = render_figure(content, layout["page"], layout.get("figure_bbox"))
            except Exception:  # noqa: BLE001 - the drawing is optional in exports
                figure = None
            if figure:
                figure_name = f"{reading_id}-figure.png"
                (self.root / figure_name).write_bytes(figure)
            profile = {
                "id": str(uuid4()),
                "family_id": standard.lineage,
                "standard_id": standard.id,
                "version": standard.version,
                "customer": standard.customer,
                "created_at": timestamp(),
                "reading_id": reading_id,
                "filename": reading["filename"],
                "header": reading["header"],
                "layout": layout,
                "figure": figure_name,
                # The criteria exactly as written in the customer's document: cells whose
                # rule still matches keep the customer's original wording on export.
                "baseline": {
                    rule["id"]: {
                        "kind": rule["kind"],
                        "conditions": rule["conditions"],
                        "notes": notes.get(rule["id"], []),
                    }
                    for rule in reading["rules"]
                },
            }
            with workflow.lock:
                workflow.store.save_form_profile(profile)
        reading["saved_standard_id"] = standard.id
        self._path(reading_id).write_text(json.dumps(reading, ensure_ascii=False), "utf-8")
        return standard

    def save_limits(self, reading_id: str, customer: str, actor: str) -> dict:
        reading = self.get(reading_id)
        if reading["document_type"] != "LIMIT_APPROVAL":
            raise ValueError("Only limit approval packs can be saved as limit samples.")
        if reading.get("saved_approval_id"):
            raise ValueError("This reading has already been saved.")
        customer = " ".join(customer.split())[:100]
        if not customer:
            raise ValueError("Enter the customer.")
        approval = {
            "id": str(uuid4()),
            "customer": customer,
            "model": reading["header"].get("model", ""),
            "part": reading["header"].get("part_name", ""),
            "filename": reading["filename"],
            "stored": reading["stored"],
            "samples": reading["samples"],
            "created_at": timestamp(),
            "created_by": actor,
            "reading_id": reading_id,
        }
        with self.workflow.lock:
            self.workflow.store.save_limit_approval(approval)
        reading["saved_approval_id"] = approval["id"]
        self._path(reading_id).write_text(json.dumps(reading, ensure_ascii=False), "utf-8")
        return approval

    def limit_approvals(self, customer: str | None = None) -> list[dict]:
        with self.workflow.lock:
            approvals = self.workflow.store.limit_approvals()
        if customer:
            approvals = [a for a in approvals if a["customer"].casefold() == customer.casefold()]
        return approvals

    def source(self, stored: str) -> Path:
        path = (self.root / stored).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            raise KeyError("The file could not be found.")
        return path

    # --- writing back in the customer's layout --------------------------------------------

    def profile_for(self, standard_id: str) -> tuple[Standard, list[Standard], dict | None]:
        with self.workflow.lock:
            standard = self.workflow.store.standard(standard_id)
            family = self.workflow.store.family(standard.lineage)
            return standard, family, self.workflow.store.form_profile(standard.lineage)

    def customer_form(self, standard_id: str) -> dict:
        standard, family, profile = self.profile_for(standard_id)
        if profile is None:
            raise KeyError("This standard was not read from a customer form.")
        form = build_form(standard, family, profile)
        form.pop("layout")
        return form

    def export(self, standard_id: str, extension: str) -> tuple[Standard, Path]:
        standard, family, profile = self.profile_for(standard_id)
        if profile is None:
            raise KeyError("This standard was not read from a customer form.")
        form = build_form(standard, family, profile)
        figure = None
        if profile.get("figure"):
            figure_path = self.root / profile["figure"]
            figure = figure_path.read_bytes() if figure_path.is_file() else None
        folder = self.workflow.settings.data_dir / "customer_forms"
        folder.mkdir(exist_ok=True)
        path = folder / f"{standard.id}.{extension}"
        temporary = path.with_suffix(f".tmp.{extension}")
        if extension == "xlsx":
            render_xlsx(temporary, form, figure)
        else:
            render_pdf(temporary, form, figure, font_name(self.workflow.settings.font_path))
        temporary.replace(path)
        return standard, path


def _strip(rule: dict) -> dict:
    return {key: value for key, value in rule.items() if key not in META_KEYS}
