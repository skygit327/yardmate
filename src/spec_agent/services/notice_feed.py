"""Collect support notices from the Bizinfo (Korean SME support portal) open API and keep those that fit the company.

Bizinfo item -> notice text in the shape ``read_notice`` already understands -> the usual
judgement. Only the official API is used (no page scraping). Without an API key the bundled,
clearly synthetic sample response is used so the flow can be shown offline.

All words live in support_lexicon.json ("feed"); nothing here is language specific.
"""

import json
import os
import re
import threading
import urllib.parse
import urllib.request
from datetime import date
from html import unescape
from pathlib import Path

from spec_agent.services.support_programs import LANGUAGES, LEX

ENDPOINT = "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do"
SAMPLE = Path(__file__).with_name("bizinfo_sample.json")
FEED = LEX["feed"]
MAX_BYTES = 5 * 1024 * 1024
DAY = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})")


def api_key() -> str:
    return os.environ.get("SPEC_AGENT_BIZINFO_API_KEY", "").strip()


def fetch_live(key: str, count: int = 500, opener=urllib.request.urlopen) -> list[dict]:
    query = urllib.parse.urlencode({"crtfcKey": key, "dataType": "json", "searchCnt": count})
    with opener(f"{ENDPOINT}?{query}", timeout=20) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("The Bizinfo response was too large.")
    data = json.loads(body.decode("utf-8"))
    items = data.get("jsonArray") if isinstance(data, dict) else None
    if isinstance(items, dict):  # a single item comes back as an object
        items = [items]
    if not isinstance(items, list):
        raise ValueError("The Bizinfo response had no notice list. Check the API key.")  # noqa: TRY004
    return [item for item in items if isinstance(item, dict)]


def fetch_sample() -> list[dict]:
    return json.loads(SAMPLE.read_text("utf-8"))["jsonArray"]


def _plain(value) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def _tags(item: dict) -> list[str]:
    raw = item.get("hashTags") or item.get("hashtags") or ""
    return [tag.strip() for tag in str(raw).split(",") if tag.strip()]


def _regions(item: dict) -> set[str]:
    """Regions the notice is for: its title prefix in brackets and its hashtags."""
    title = str(item.get("pblancNm", ""))
    words = [*re.findall(r"\[([^\]]+)\]", title), *_tags(item)]
    found = set()
    for word in words:
        for tag, province in FEED["region_tags"].items():
            if tag in word:
                found.add(province)
        if word in FEED["nationwide_tags"]:
            found.add("*")
        if word in FEED["other_region_tags"]:
            found.add("other")
    return found


def _period(item: dict) -> tuple[date | None, date | None]:
    days = [
        date(int(y), int(m), int(d))
        for y, m, d in DAY.findall(str(item.get("reqstBeginEndDe", "")))
        if 1 <= int(m) <= 12 and 1 <= int(d) <= 31
    ]
    return (days[0], days[-1]) if days else (None, None)


def relevance(item: dict, province: str | None, today: date) -> dict | None:
    """Why this notice fits the company, or None when it does not.

    Kept: notices for the company's province, and nationwide or untagged notices that mention
    the company's industry. Other provinces and closed notices are dropped.
    """
    _, end = _period(item)
    if end and end < today:
        return None
    regions = _regions(item)
    text = " ".join(
        _plain(item.get(key)) for key in ("pblancNm", "bsnsSumryCn", "trgetNm", "hashTags")
    )
    industry = [word for word in FEED["industry_words"] if word in text]
    reasons = []
    if province and province in regions:
        reasons.append(_reason("region", region=province))
        score = 2
    elif regions - {"*"} and not (regions & {"*"}):
        return None  # only for other regions
    elif industry:
        reasons.append(_reason("nationwide"))
        score = 1
    else:
        return None
    if industry:
        reasons.append(_reason("industry", words=", ".join(industry[:3])))
        score += 1
    return {"score": score, "reasons": reasons}


def _reason(kind: str, **values) -> dict:
    return {lang: FEED["reasons"][kind][lang].format(**values) for lang in LANGUAGES}


def notice_text(item: dict, province: str | None) -> str:
    """Bizinfo fields written out as a notice, so the normal reader can judge it."""
    heads = FEED["headings"]
    start, end = _period(item)
    agency = " / ".join(
        dict.fromkeys(_plain(item.get(k)) for k in ("jrsdInsttNm", "excInsttNm") if item.get(k))
    )
    regions = [r for r in _regions(item) if r not in {"*", "other"}]
    eligibility = []
    if regions:
        eligibility.append(FEED["region_line"].format(region="·".join(sorted(regions))))
    if item.get("trgetNm"):
        eligibility.append(FEED["target_line"].format(target=_plain(item["trgetNm"])))
    lines = [_plain(item.get("pblancNm")), "", f"{heads['agency']}: {agency}", ""]
    lines += [heads["purpose"], f" {_plain(item.get('bsnsSumryCn'))}", ""]
    # The API carries only a summary: the detailed conditions stay "to check", never guessed.
    eligibility.append(FEED["detail_line"])
    lines += [heads["eligibility"], *[f" - {line}" for line in eligibility], ""]
    if end:
        begin = start or end
        lines += [
            heads["period"],
            f" - {begin.year}. {begin.month}. {begin.day}. ~ {end.year}. {end.month}. {end.day}.",
            "",
        ]
    if item.get("pblancUrl"):
        lines.append(f"{heads['link']}: {item['pblancUrl']}")
    return "\n".join(lines)


def _province(region: str) -> str | None:
    return next((p for tag, p in FEED["region_tags"].items() if tag in (region or "")), None)


class NoticeFeed:
    """Fetches, filters and stores notices; runs on demand and every few hours with a key."""

    def __init__(self, programs, fetch=None, interval_hours: float | None = None):
        self.programs = programs
        self._fetch = fetch
        self.interval = interval_hours or float(
            os.environ.get("SPEC_AGENT_FEED_INTERVAL_HOURS", "12")
        )
        self.last: dict | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def status(self) -> dict:
        return {"mode": "live" if api_key() else "sample", "last": self.last}

    def refresh(self, today: date | None = None) -> dict:
        today = today or date.today()
        key = api_key()
        mode = "live" if key else "sample"
        with self._lock:
            try:
                if self._fetch:
                    items = self._fetch()
                elif key:
                    items = fetch_live(key)
                else:
                    items = fetch_sample()
            except Exception as error:  # noqa: BLE001 - shown to the user, never the key
                self.last = {"mode": mode, "error": _safe(str(error), key), "at": _now()}
                return self.last
            profile = self.programs._profile_model()
            province = _province(profile.region)
            existing = {p["filename"] for p in self.programs.store.support_programs()}
            matched, added = [], []
            for item in items:
                fit = relevance(item, province, today)
                if fit is None:
                    continue
                matched.append(item)
                filename = f"bizinfo-{item.get('pblancId') or _digest(item.get('pblancNm'))}"
                if filename in existing:
                    continue
                program = self.programs.add(
                    notice_text(item, province),
                    filename,
                    "bizinfo",
                    extra={"url": item.get("pblancUrl"), "fit": fit},
                )
                existing.add(filename)
                added.append(program["id"])
            self.last = {
                "mode": mode,
                "fetched": len(items),
                "matched": len(matched),
                "added": len(added),
                "added_ids": added,
                "at": _now(),
            }
            return self.last

    def start(self):
        if not api_key() or self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="notice-feed", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(self.interval * 3600)


def _digest(value) -> str:
    import hashlib

    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def _safe(message: str, key: str) -> str:
    return message.replace(key, "***") if key else message


def _now() -> str:
    from spec_agent.storage.database import timestamp

    return timestamp()
