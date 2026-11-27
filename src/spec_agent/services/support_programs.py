"""Support-programme matching for local manufacturers (Busan, Ulsan, Gyeongnam).

The same shape as reading a customer form, applied to public support notices:

    notice (each agency writes it differently)
      -> one standard list of conditions (region, size, revenue, age, industry, certificates...)
      -> judged against the company profile (pass / fail / check), with the source sentence
      -> deadline and documents become tasks with due dates

Everything is rule-based, so a notice never leaves the company. Unrecognised conditions are
never guessed: they are shown as "check" with the original sentence. The vocabulary lives in
``support_lexicon.json``.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from spec_agent.schemas.standard import StrictModel
from spec_agent.storage.database import timestamp

LEX = json.loads(Path(__file__).with_name("support_lexicon.json").read_text("utf-8"))
LANGUAGES = ("en", "ko", "zh")
MAX_NOTICE_BYTES = 10 * 1024 * 1024
SAMPLES = Path(__file__).with_name("support_samples")
SECTIONS = LEX["sections"]
PLACES = {**LEX["provinces"], **LEX["cities"]}
CERTIFICATES = LEX["certificates"]
TYPES = LEX["company_types"]
TRIGGERS = LEX["triggers"]
PATTERNS = LEX["patterns"]
OPS = LEX["ops"]
FIELDS = LEX["fields"]
UNITS = LEX["units"]
WORDS = LEX["words"]
RESULTS = LEX["results"]
SYMBOL = {">=": "≥", ">": ">", "<=": "≤", "<": "<"}
DATE = re.compile(PATTERNS["date"])
AMOUNT = re.compile(PATTERNS["amount"], re.IGNORECASE)
BULLET = re.compile(PATTERNS["bullet"])


def _same(value: str) -> dict:
    return {language: value for language in LANGUAGES}


def _num(value: float) -> str:
    return f"{value:g}" if value != int(value) else f"{int(value):,}"


def _measure(field: str, rule: list) -> dict:
    """'Employees ≥ 10 people · < 300 people' in each language."""
    return {
        language: f"{FIELDS[field][language]} "
        + " · ".join(f"{SYMBOL[op]} {_num(value)} {UNITS[field][language]}" for op, value in rule)
        for language in LANGUAGES
    }


def _listed(field: str, items: list[str], joiner: str = ", ") -> dict:
    return {language: f"{FIELDS[field][language]}: {joiner.join(items)}" for language in LANGUAGES}


def _compare(actual: float, op: str, limit: float) -> bool:
    return {">=": actual >= limit, ">": actual > limit, "<=": actual <= limit, "<": actual < limit}[
        op
    ]


def _has(text: str, words: list[str]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


# --- company profile ------------------------------------------------------------------------


class CompanyProfile(StrictModel):
    name: str = Field(default="", max_length=120)
    region: str = Field(default="", max_length=80)
    company_type: str = Field(default=TYPES["sme"], max_length=40)
    industry: str = Field(default="", max_length=80)
    employees: int | None = Field(default=None, ge=0, le=1_000_000)
    revenue_eok: float | None = Field(default=None, ge=0, le=1_000_000)
    founded: int | None = Field(default=None, ge=1900, le=2100)
    export_usd: float | None = Field(default=None, ge=0)
    certifications: list[str] = Field(default_factory=list, max_length=30)
    main_customers: list[str] = Field(default_factory=list, max_length=30)
    received: list[str] = Field(default_factory=list, max_length=50)
    tax_arrears: bool = False
    closed: bool = False


DEMO_PROFILE = CompanyProfile(**LEX["demo_profile"])


class NoticeInput(StrictModel):
    text: str = Field(min_length=20, max_length=200_000)
    filename: str = Field(default="", max_length=200)


# --- reading a notice -----------------------------------------------------------------------


def _clean(line: str) -> str:
    return BULLET.sub("", line).strip()


def _header(line: str) -> tuple[str, str] | None:
    """('eligibility', rest of line) when the line opens a section."""
    cleaned = _clean(line)
    lowered = cleaned.lower()
    for section, words in SECTIONS.items():
        for word in sorted(words, key=len, reverse=True):
            if lowered.startswith(word.lower()):
                rest = cleaned[len(word) :].strip()
                if rest and not rest.startswith((":", "：", "-", "(")) and len(cleaned) > 30:
                    continue  # a sentence that merely starts with the word
                return section, rest.lstrip(":：- ").strip()
    return None


def _dates(text: str, year_hint: int) -> list[date]:
    found, year = [], year_hint
    for match in DATE.finditer(text):
        y, m, d = match.groups()
        if y:
            year = int(y)
        try:
            found.append(date(year, int(m), int(d)))
        except ValueError:
            continue
    return found


def _amount(text: str) -> dict | None:
    match = AMOUNT.search(text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    return {"text": match.group(0).strip(), "manwon": value * LEX["money_units"][match.group(2)]}


def read_notice(text: str, filename: str = "", today: date | None = None) -> dict:
    """Turn a notice into one standard structure: meta, conditions, documents."""
    today = today or date.today()
    note = LEX["note_prefix"]
    content = [line.rstrip() for line in text.replace("\r", "").split("\n") if line.strip()]
    title = next((line.strip() for line in content if not line.strip().startswith(note)), "")
    sections: dict[str, list[str]] = {key: [] for key in SECTIONS}
    current = None
    for line in content[1:]:
        if line.strip().startswith(note):
            continue
        found = _header(line)
        if found:
            current, rest = found
            if rest:
                sections[current].append(rest)
        elif current and _clean(line):
            sections[current].append(_clean(line))

    period = " ".join(sections["period"]) or next(
        (line for line in content if _has(line, LEX["deadline_hint"])), ""
    )
    dates = _dates(period, today.year)
    conditions = []
    for group in ("eligibility", "exclusion", "preferred"):
        for line in sections[group]:
            conditions.extend(_conditions(line, group))
    return {
        "title": title or filename or "Support programme",
        "agency": sections["agency"][0] if sections["agency"] else "",
        "opens": min(dates).isoformat() if len(dates) > 1 else None,
        "deadline": max(dates).isoformat() if dates else None,
        "amount": _amount(" ".join(sections["support"])) or _amount(text),
        "support": sections["support"],
        "purpose": " ".join(sections["purpose"]),
        "conditions": conditions,
        "documents": sections["documents"],
        "synthetic": _has(text[:400], LEX["synthetic_markers"]),
    }


def _ops(text: str, unit: str) -> list[tuple[str, float]]:
    pattern = rf"(\d+(?:[.,]\d+)?)\s*{unit}\s*{PATTERNS['ops']}"
    return [(OPS[op], float(value.replace(",", ""))) for value, op in re.findall(pattern, text)]


def _conditions(line: str, group: str) -> list[dict]:
    """All standard conditions one sentence holds; an unknown sentence becomes 'other'."""
    found = []

    def add(field, rule=None, label=None):
        found.append(
            {
                "id": uuid4().hex[:8],
                "group": group,
                "field": field,
                "rule": rule or {},
                "label": label or dict(FIELDS[field]),
                "source": line,
            }
        )

    if group == "exclusion":
        for field in ("tax", "closed", "duplicate"):
            if _has(line, TRIGGERS[field]):
                add(field)
        if not found:
            add("other")
        return found

    areas = next(
        (members for word, members in LEX["area_groups"].items() if word.lower() in line.lower()),
        None,
    ) or [name for name in LEX["districts"] if name in line]
    if len(areas) >= 2 or (areas and _has(line, list(LEX["area_groups"]))):
        add("area", {"any": areas}, _listed("area", areas))

    regions = set()
    for word, members in LEX["region_groups"].items():
        if word in line:
            regions.update(members)
    for word, province in PLACES.items():
        if re.search(word, line, re.IGNORECASE):
            regions.add(province)
    if regions and not areas and _has(line, LEX["region_context"]):
        ordered = [p for p in LEX["province_order"] if p in regions]
        add("region", {"any": ordered}, _listed("region", ordered))

    kinds = [kind for kind in TYPES.values() if kind in line]
    if kinds and group == "eligibility":
        add("company_type", {"any": kinds}, _listed("company_type", kinds))

    if _has(line, TRIGGERS["employees"]):
        rule = _ops(line, PATTERNS["count_unit"])
        if rule:
            add("employees", {"all": rule}, _measure("employees", rule))

    if _has(line, TRIGGERS["revenue"]):
        rule = [
            (OPS[op], float(value.replace(",", "")) * LEX["revenue_units"][unit])
            for value, unit, op in re.findall(PATTERNS["revenue"], line)
        ]
        if rule:
            add("revenue", {"all": rule}, _measure("revenue", rule))

    if _has(line, TRIGGERS["age"]):
        rule = _ops(line, PATTERNS["year_unit"])
        if rule:
            add("age", {"all": rule}, _measure("age", rule))

    if _has(line, TRIGGERS["export"]):
        rule = [
            (OPS[op], float(value.replace(",", "")) * (10000 if man == LEX["ten_thousand"] else 1))
            for value, man, op in re.findall(PATTERNS["export"], line, re.IGNORECASE)
        ]
        if rule:
            add("export", {"all": rule}, _measure("export", rule))

    industries = [name for name in LEX["industries"] if name in line]
    if industries:
        add("industry", {"any": industries}, _listed("industry", industries))

    certificates = [
        name for name, pattern in CERTIFICATES.items() if re.search(pattern, line, re.IGNORECASE)
    ]
    if certificates:
        add("certification", {"any": certificates}, _listed("certification", certificates, " / "))

    if _has(line, TRIGGERS["partner"]):
        add("partner")

    if not found:
        add("other")
    return found


# --- judging against the profile ------------------------------------------------------------


def _province(region: str) -> str | None:
    return next(
        (province for word, province in PLACES.items() if word.lower() in region.lower()), None
    )


def facets(program: dict) -> dict:
    """Support fields and regions of a notice, for search and filters.

    Fields: the API's own field names when the notice came from Bizinfo, otherwise words in the
    title, purpose and support text. Regions: the notice's location conditions and the
    [region] prefix of its title; no region at all means "*" (anywhere).
    """
    notice = program["notice"]
    categories = LEX["categories"]
    given = set(program.get("categories") or [])
    text = " ".join([notice["title"], notice.get("purpose", ""), *notice.get("support", [])])
    found = [
        key
        for key, item in categories.items()
        if not key.startswith("_")
        and key != "other"
        and (item["ko"] in given or any(word in text for word in item["words"]))
    ]
    regions = set()
    for condition in notice["conditions"]:
        if condition["field"] == "region":
            regions.update(condition["rule"].get("any", []))
        elif condition["field"] == "area":
            regions.update(
                LEX["area_provinces"].get(name, "") for name in condition["rule"].get("any", [])
            )
    for tag in re.findall(r"\[([^\]]+)\]", notice["title"]):
        regions.update(p for word, p in LEX["feed"]["region_tags"].items() if word in tag)
    regions.discard("")
    return {"categories": found or ["other"], "regions": sorted(regions) or ["*"]}


def judge(notice: dict, profile: CompanyProfile, today: date | None = None) -> dict:
    """Each condition gets pass / fail / unknown with what the profile says."""
    today = today or date.today()
    results = []
    for condition in notice["conditions"]:
        status, actual = _judge_one(condition, profile, today)
        results.append({**condition, "status": status, "actual": actual})
    required = [c for c in results if c["group"] != "preferred"]
    preferred = [c for c in results if c["group"] == "preferred"]
    if any(c["status"] == "fail" for c in required):
        overall = "INELIGIBLE"
    elif any(c["status"] == "unknown" for c in required):
        overall = "CHECK"
    else:
        overall = "ELIGIBLE"
    deadline = notice.get("deadline")
    days_left = (date.fromisoformat(deadline) - today).days if deadline else None
    if days_left is not None and days_left < 0:
        overall = "CLOSED"
    return {
        "conditions": results,
        "overall": overall,
        "met": sum(c["status"] == "pass" for c in required),
        "required": len(required),
        "bonus": sum(c["status"] == "pass" for c in preferred),
        "preferred": len(preferred),
        "days_left": days_left,
    }


def _judge_one(condition: dict, profile: CompanyProfile, today: date) -> tuple[str, dict]:
    field, rule = condition["field"], condition["rule"]
    unknown = WORDS["unknown_profile"]
    if field == "region":
        if not profile.region:
            return "unknown", unknown
        return ("pass" if _province(profile.region) in rule["any"] else "fail"), _same(
            profile.region
        )
    if field == "area":
        if not profile.region:
            return "unknown", unknown
        place = profile.region.replace(" ", "")
        inside = any(
            all(part.rstrip(LEX["district_suffixes"]) in place for part in name.split())
            for name in rule["any"]
        )
        return ("pass" if inside else "fail"), _same(profile.region)
    if field == "company_type":
        kinds = set(rule["any"])
        actual = _same(profile.company_type or "-")
        small, sme = TYPES["small_business"], TYPES["sme"]
        if small in kinds and sme not in kinds:
            if profile.company_type == small:
                return "pass", actual
            if profile.employees is None:
                return "unknown", unknown
            detail = {
                k: f"{profile.company_type} · {profile.employees} {UNITS['employees'][k]}"
                for k in LANGUAGES
            }
            return ("pass" if profile.employees < 10 else "fail"), detail
        if sme in kinds:
            return (
                "pass" if profile.company_type in {sme, small, TYPES["startup"]} else "fail"
            ), actual
        return ("pass" if profile.company_type in kinds else "unknown"), actual
    if field in {"employees", "revenue", "age", "export"}:
        value = {
            "employees": profile.employees,
            "revenue": profile.revenue_eok,
            "age": (today.year - profile.founded) if profile.founded else None,
            "export": profile.export_usd,
        }[field]
        if value is None:
            return "unknown", unknown
        ok = all(_compare(value, op, limit) for op, limit in rule["all"])
        return ("pass" if ok else "fail"), {
            k: f"{_num(value)} {UNITS[field][k]}" for k in LANGUAGES
        }
    if field == "industry":
        if not profile.industry:
            return "unknown", unknown
        return ("pass" if any(name in profile.industry for name in rule["any"]) else "fail"), _same(
            profile.industry
        )
    if field == "certification":
        owned = [
            c
            for c in profile.certifications
            if any(
                re.search(CERTIFICATES.get(name, re.escape(name)), c, re.IGNORECASE)
                for name in rule["any"]
            )
        ]
        actual = (
            _same(", ".join(profile.certifications)) if profile.certifications else WORDS["none"]
        )
        return ("pass" if owned else "fail"), actual
    if field in {"tax", "closed"}:
        flagged = profile.tax_arrears if field == "tax" else profile.closed
        return ("fail" if flagged else "pass"), (WORDS["yes"] if flagged else WORDS["none"])
    if field == "duplicate":
        return (
            ("pass", WORDS["none"])
            if not profile.received
            else ("unknown", _same(", ".join(profile.received)))
        )
    if field == "partner":
        return (
            ("pass", _same(", ".join(profile.main_customers)))
            if profile.main_customers
            else ("fail", WORDS["none"])
        )
    return "unknown", WORDS["unknown_rule"]


# --- the desk the API talks to --------------------------------------------------------------


class SupportPrograms:
    def __init__(self, workflow):
        self.workflow = workflow

    @property
    def store(self):
        return self.workflow.store

    def profile(self) -> dict:
        with self.workflow.lock:
            saved = self.store.company_profile()
        choices = {
            "company_types": list(TYPES.values()),
            "certificates": list(CERTIFICATES),
            "categories": {
                key: {lang: item[lang] for lang in LANGUAGES}
                for key, item in LEX["categories"].items()
                if not key.startswith("_")
            },
            "regions": LEX["province_order"],
        }
        if saved is None:
            return {**DEMO_PROFILE.model_dump(), "demo": True, "choices": choices}
        return {**saved, "demo": False, "choices": choices}

    def _profile_model(self) -> CompanyProfile:
        data = self.profile()
        return CompanyProfile(**{k: v for k, v in data.items() if k not in {"demo", "choices"}})

    def save_profile(self, data: CompanyProfile) -> dict:
        with self.workflow.lock:
            self.store.save_company_profile(data.model_dump())
        return self.profile()  # matching runs on read, so the new profile applies everywhere

    def list(self) -> list[dict]:
        with self.workflow.lock:
            programs = self.store.support_programs()
        profile = self._profile_model()
        result = [
            {**p, "match": judge(p["notice"], profile), "facets": facets(p)} for p in programs
        ]
        order = {"ELIGIBLE": 0, "CHECK": 1, "INELIGIBLE": 2, "CLOSED": 3}
        return sorted(
            result, key=lambda p: (order[p["match"]["overall"]], p["notice"]["deadline"] or "9999")
        )

    def get(self, program_id: str) -> dict:
        with self.workflow.lock:
            program = self.store.support_program(program_id)
        return {
            **program,
            "match": judge(program["notice"], self._profile_model()),
            "facets": facets(program),
        }

    def add(
        self, text: str, filename: str = "", source: str = "manual", extra: dict | None = None
    ) -> dict:
        program = {
            **(extra or {}),
            "id": str(uuid4()),
            "created_at": timestamp(),
            "filename": filename,
            "source": source,
            "text": text[:50_000],
            "notice": read_notice(text, filename),
            "tasks": [],
        }
        with self.workflow.lock:
            self.store.save_support_program(program)
        return self.get(program["id"])

    def add_file(self, filename: str, content: bytes, source: str = "manual") -> dict:
        from spec_agent.services.documents import extract_text

        if len(content) > MAX_NOTICE_BYTES:
            raise ValueError("The file is larger than 10 MB.")
        suffix = Path(filename).suffix.lower()
        if suffix not in {".pdf", ".txt", ".md"}:
            raise ValueError("Upload a PDF or text file of the notice.")
        text = (
            content.decode("utf-8", "replace")
            if suffix in {".txt", ".md"}
            else extract_text(suffix, content)
        )
        if len(text.strip()) < 20:
            raise ValueError("No text could be read from the file.")
        return self.add(text, filename, source)

    def load_samples(self) -> list[dict]:
        with self.workflow.lock:
            existing = {p["filename"] for p in self.store.support_programs()}
        return [
            self.add(path.read_text("utf-8"), path.name, "sample")
            for path in sorted(SAMPLES.glob("*.txt"))
            if path.name not in existing
        ]

    def delete(self, program_id: str) -> dict:
        with self.workflow.lock:
            self.store.delete_support_program(program_id)
        return {"deleted": program_id}

    def short_title(self, notice: dict) -> str:
        return notice["title"].replace(LEX["sample_prefix"], "").strip()[:80]

    def register_tasks(self, program_id: str) -> dict:
        """Documents five days before the deadline, submission the day before."""
        program = self.get(program_id)
        if program["tasks"]:
            return program
        notice = program["notice"]
        today = date.today()
        deadline = (
            date.fromisoformat(notice["deadline"])
            if notice["deadline"]
            else today + timedelta(days=14)
        )
        prepare = max(today, deadline - timedelta(days=5))
        submit = max(today, deadline - timedelta(days=1))
        link = {"type": "program", "id": program_id}
        templates = LEX["tasks"]
        tasks = [
            self._task(templates["document"], item, prepare, link, notice["title"])
            for item in notice["documents"]
        ]
        tasks.append(
            self._task(templates["submit"], self.short_title(notice), submit, link, notice["title"])
        )
        stored = {k: v for k, v in program.items() if k != "match"}
        stored["tasks"] = [{"id": t["id"], "title": t["title"], "due": t["due"]} for t in tasks]
        with self.workflow.lock:
            self.store.save_support_program(stored)
        return self.get(program_id)

    def _task(self, template: dict, item: str, due: date, link: dict, detail: str) -> dict:
        task = {
            "id": str(uuid4()),
            "title": {language: template[language].format(item=item) for language in LANGUAGES},
            "detail": detail,
            "due": due.isoformat(),
            "status": "TODO",
            "source": "automation",
            "kind": "programs",
            "customer": "",
            "link": link,
            "created_at": timestamp(),
        }
        with self.workflow.lock:
            self.store.save_task(task)
        return task

    def summary(self) -> dict:
        programs = [p for p in self.list() if p["match"]["overall"] != "CLOSED"]
        hopeful = [p for p in programs if p["match"]["overall"] in {"ELIGIBLE", "CHECK"}]
        return {
            "eligible": sum(p["match"]["overall"] == "ELIGIBLE" for p in programs),
            "check": sum(p["match"]["overall"] == "CHECK" for p in programs),
            "closing": sum(
                p["match"]["days_left"] is not None and p["match"]["days_left"] <= 7
                for p in hopeful
            ),
            "total": len(programs),
        }
