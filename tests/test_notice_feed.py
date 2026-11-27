"""Bizinfo notices: fetched, kept only when they fit the company, judged like any notice."""

import io
import json
from datetime import date

import pytest

from spec_agent.services import notice_feed
from spec_agent.services.notice_feed import fetch_live, fetch_sample, relevance

TODAY = date(2026, 11, 27)


def test_only_notices_for_the_company_region_or_industry_are_kept():
    kept = {
        item["pblancId"] for item in fetch_sample() if relevance(item, "경남", TODAY) is not None
    }
    # Gyeongnam notices and a nationwide welding notice; not Busan, Seoul, an unrelated
    # nationwide notice or a closed one.
    assert kept == {"SAMPLE_0001", "SAMPLE_0002", "SAMPLE_0003"}


def test_sample_feed_is_added_once_and_left_to_check(client, monkeypatch):
    monkeypatch.delenv("SPEC_AGENT_BIZINFO_API_KEY", raising=False)
    result = client.post("/api/programs/feed").json()
    assert result["mode"] == "sample"
    assert (result["fetched"], result["matched"], result["added"]) == (7, 3, 3)
    assert client.post("/api/programs/feed").json()["added"] == 0
    programs = [p for p in client.get("/api/programs").json() if p["source"] == "bizinfo"]
    assert len(programs) == 3
    # The API has only a summary, so detailed eligibility is never assumed.
    assert {p["match"]["overall"] for p in programs} == {"CHECK"}
    assert all(p["url"] and p["fit"]["reasons"] for p in programs)
    assert client.get("/api/programs/feed").json()["last"]["matched"] == 3


def test_live_fetch_reads_the_api_shape_and_hides_the_key(monkeypatch):
    calls = []

    def opener(url, timeout):
        calls.append(url)
        return io.BytesIO(json.dumps({"jsonArray": fetch_sample()[:2]}).encode())

    assert len(fetch_live("secret-key", opener=opener)) == 2
    assert "crtfcKey=secret-key" in calls[0] and "dataType=json" in calls[0]

    def broken(url, timeout):
        raise OSError(f"failed {url}")

    monkeypatch.setenv("SPEC_AGENT_BIZINFO_API_KEY", "secret-key")
    feed = notice_feed.NoticeFeed(programs=None, fetch=lambda: broken("x?crtfcKey=secret-key", 1))
    result = feed.refresh(TODAY)
    assert "secret-key" not in result["error"] and result["mode"] == "live"


def test_bad_response_is_reported():
    with pytest.raises(ValueError):
        fetch_live("k", opener=lambda url, timeout: io.BytesIO(b'{"reqErr": "invalid key"}'))


def test_feed_needs_a_session(client):
    client.cookies.clear()
    assert client.post("/api/programs/feed").status_code in {401, 403}


def test_notices_carry_fields_and_regions_for_search(client, monkeypatch):
    monkeypatch.delenv("SPEC_AGENT_BIZINFO_API_KEY", raising=False)
    client.post("/api/programs/samples")
    client.post("/api/programs/feed")
    programs = {p["notice"]["title"]: p["facets"] for p in client.get("/api/programs").json()}
    crisis = next(f for title, f in programs.items() if "스마트야드" in title)
    assert "technology" in crisis["categories"] and crisis["regions"] == ["경남", "울산"]
    busan = next(f for title, f in programs.items() if "부산항" in title)
    assert busan["regions"] == ["부산"]
    nationwide = next(f for title, f in programs.items() if "뿌리산업" in title)
    assert nationwide["regions"] == ["*"]
    choices = client.get("/api/company-profile").json()["choices"]
    assert choices["categories"]["export"]["ko"] == "수출" and choices["regions"][0] == "부산"
