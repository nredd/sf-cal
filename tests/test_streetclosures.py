"""Tests for the DataSF street closures adapter."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
import pytest
import respx

from sfcal import TZ
from sfcal.cfg import SourceCfg
from sfcal.sources.base import SourceError, Window
from sfcal.sources.streetclosures import (
    DETAILS_URL,
    NOTES,
    Shape,
    StreetClosures,
    midpoint,
    quote,
    street_case,
)
from tests.conftest import fixture_text

URL = "https://data.sf.gov/resource/8x25-yybr.json"
WINDOW = Window(
    start=datetime(2026, 10, 5, tzinfo=TZ), end=datetime(2026, 12, 5, tzinfo=TZ), full=True
)


def rows() -> list[dict[str, Any]]:
    """Load the captured rows.

    Returns:
        list[dict[str, Any]]: SODA rows.
    """
    return json.loads(fixture_text("streetclosures.json"))


def adapter(client: httpx.Client, **options: Any) -> StreetClosures:
    """Build the adapter.

    Parameters:
        client (httpx.Client): HTTP client.
        **options (Any): Option overrides.

    Returns:
        StreetClosures: Adapter.
    """
    opts = {"url": URL, "bucket": "street-events", "skip_pattern": r"\bcorporate\b"} | options
    cfg = SourceCfg(adapter="streetclosures", label="Test", priority=20, options=opts)
    return StreetClosures("streets", cfg, client)


def test_helpers() -> None:
    assert quote("Castro Farmers' Market") == "'Castro Farmers'' Market'"
    assert street_case("MISSION ST between THERESA ST and GENEVA AVE") == (
        "Mission St between Theresa St and Geneva Ave"
    )
    assert street_case("JEFFERSON ST between HYDE ST and END: 500-599 BLOCK") == (
        "Jefferson St between Hyde St and End: 500-599 Block"
    )
    assert midpoint(Shape(coordinates=[[-122.44, 37.71], [-122.43, 37.72], [-122.42, 37.73]])) == (
        37.72,
        -122.43,
    )
    assert midpoint(Shape(coordinates=[])) is None
    assert midpoint(None) is None


@respx.mock
def test_fetch_fixture(client: httpx.Client) -> None:
    route = respx.get(URL).respond(json=rows())
    events = adapter(client).fetch(WINDOW, [], {}).events
    by_title: dict[str, list[Any]] = {}
    for event in events:
        by_title.setdefault(event.title, []).append(event)

    # Midway (corporate, multi-day), Fleet Week (multi-day umbrella) and the
    # in-review block party are all gone.
    assert set(by_title) == {
        "Castro Farmers' Market 2026",
        "Chinatown Night Market 2026",
        "Fleet Week Block Party",
        "Sunday Streets Excelsior",
    }
    assert [e.start.day for e in by_title["Castro Farmers' Market 2026"]] == [7, 14]

    (sunday,) = by_title["Sunday Streets Excelsior"]
    assert sunday.start == datetime(2026, 10, 18, 9, tzinfo=TZ)
    assert sunday.end == datetime(2026, 10, 18, 18, tzinfo=TZ)
    assert sunday.source_id == "1516551-2026-10-18T09:00:00-07:00"
    assert sunday.venue == "Mission St between Theresa St and Geneva Ave"
    assert sunday.locality == "San Francisco"
    assert sunday.geo == (37.718888, -122.439182)
    assert sunday.url == DETAILS_URL
    assert sunday.bucket == "street-events"
    assert sunday.notes == ["Permit status: Permitted", *NOTES]
    assert sunday.description is not None
    assert sunday.description.startswith("Closed: Mission St from Kenny Aly to Italy Ave; ")
    assert sunday.description.endswith("; and 12 more")

    params = route.calls[0].request.url.params
    assert params["$where"] == (
        "type in ('Special Event') AND status in ('Permitted') "
        "AND start_dt >= '2026-10-05T00:00:00' AND start_dt < '2026-12-05T00:00:00'"
    )
    assert (params["$order"], params["$limit"]) == ("start_dt,objectid", "50000")


@respx.mock
def test_options_reach_query_and_filters(client: httpx.Client) -> None:
    route = respx.get(URL).respond(json=rows())
    statuses = ["Permitted", "Application In Review"]
    events = adapter(client, statuses=statuses, skip_pattern=None, max_span_hours=72).fetch(
        WINDOW, [], {}
    )
    titles = {e.title for e in events.events}
    review = next(e for e in events.events if e.title.startswith("ER Taylor"))
    assert review.notes[0] == "Permit status: Application In Review"
    assert {"ER Taylor Elementary School Street Block Party", "Midway - Corporate Event"} <= titles
    assert "San Francisco Fleet Week 2026" in titles
    assert (
        "status in ('Permitted', 'Application In Review')"
        in (route.calls[0].request.url.params["$where"])
    )


@respx.mock
def test_skip_pattern(client: httpx.Client) -> None:
    respx.get(URL).respond(json=rows())
    events = adapter(client, skip_pattern=r"night market|farmers").fetch(WINDOW, [], {}).events
    assert {e.title for e in events} == {"Fleet Week Block Party", "Sunday Streets Excelsior"}


@respx.mock
def test_malformed_rows_are_skipped(client: httpx.Client) -> None:
    good = next(r for r in rows() if r["case_name"] == "Fleet Week Block Party")
    respx.get(URL).respond(json=[{"case_name": "No dates"}, good | {"shape": None}])
    (event,) = adapter(client).fetch(WINDOW, [], {}).events
    assert event.geo is None


@respx.mock
def test_segments_ending_at_different_times_merge(client: httpx.Client) -> None:
    night = [r for r in rows() if r["case_name"] == "Chinatown Night Market 2026"]
    night[0] = night[0] | {"end_dt": "2026-10-09T21:00:00.000"}
    respx.get(URL).respond(json=night)
    (event,) = adapter(client).fetch(WINDOW, [], {}).events
    assert event.end == datetime(2026, 10, 9, 23, 59, tzinfo=TZ)


@respx.mock
def test_row_limit_is_an_error(client: httpx.Client) -> None:
    respx.get(URL).respond(json=rows()[:3])
    with pytest.raises(SourceError, match="row limit"):
        adapter(client, limit=3).fetch(WINDOW, [], {})


@pytest.mark.parametrize(
    ("body", "match"), [("<html>down</html>", "Not JSON"), ('{"error": true}', "JSON array")]
)
@respx.mock
def test_bad_body(client: httpx.Client, body: str, match: str) -> None:
    respx.get(URL).respond(text=body)
    with pytest.raises(SourceError, match=match):
        adapter(client).fetch(WINDOW, [], {})
