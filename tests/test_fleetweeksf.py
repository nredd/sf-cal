"""Tests for the Fleet Week adapter against a captured 2026 page."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx
import pytest
import respx
from selectolax.lexbor import LexborHTMLParser

from sfcal import TZ
from sfcal.cfg import SourceCfg
from sfcal.sources.base import SourceError, Window
from sfcal.sources.fleetweeksf import (
    FleetWeekSF,
    parse_day,
    parse_geo,
    parse_map,
    parse_times,
    parse_year,
)
from tests.conftest import fixture_text

CAL_URL = "https://fleetweeksf.org/calendar-of-events/"
MAP_URL = "https://fleetweeksf.org/map-of-events/"
WINDOW = Window(
    start=datetime(2026, 10, 4, tzinfo=TZ), end=datetime(2026, 12, 5, tzinfo=TZ), full=True
)


def adapter(client: httpx.Client) -> FleetWeekSF:
    """Build the adapter with default options.

    Parameters:
        client (httpx.Client): HTTP client.

    Returns:
        FleetWeekSF: Adapter.
    """
    cfg = SourceCfg(adapter="fleetweeksf", label="FW", priority=100, sf_only=False)
    return FleetWeekSF("fleetweeksf", cfg, client)


def test_parse_year() -> None:
    page = LexborHTMLParser(fixture_text("fleetweeksf_calendar.html"))
    assert parse_year(page) == 2026


def test_parse_year_missing() -> None:
    with pytest.raises(SourceError, match="heading"):
        parse_year(LexborHTMLParser("<html><body><h1>Events</h1></body></html>"))


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Tuesday, Oct 6", date(2026, 10, 6)),
        ("Wednesday October 7", date(2026, 10, 7)),
        ("Oct 12", date(2026, 10, 12)),
        ("  Saturday,   Oct 10 ", date(2026, 10, 10)),
    ],
)
def test_parse_day(label: str, expected: date) -> None:
    assert parse_day(label, 2026) == expected


@pytest.mark.parametrize("label", ["Someday", "Tuesday, Foo 6", ""])
def test_parse_day_rejects(label: str) -> None:
    with pytest.raises(SourceError, match="day label"):
        parse_day(label, 2026)


def test_parse_times_range() -> None:
    start, end = parse_times("10:00 AM - 4:00 PM", date(2026, 10, 7))
    assert start == datetime(2026, 10, 7, 10, tzinfo=TZ)
    assert end == datetime(2026, 10, 7, 16, tzinfo=TZ)


def test_parse_times_single_and_noon() -> None:
    start, end = parse_times("12:30 PM", date(2026, 10, 6))
    assert start == datetime(2026, 10, 6, 12, 30, tzinfo=TZ)
    assert end is None
    assert parse_times("12:00 AM", date(2026, 10, 6))[0].hour == 0


@pytest.mark.parametrize("text", ["TBA", "noon", "13:00 PM", "5 PM"])
def test_parse_times_rejects(text: str) -> None:
    with pytest.raises(ValueError, match="Unrecognized"):
        parse_times(text, date(2026, 10, 6))


def test_parse_geo() -> None:
    href = "https://www.google.com/maps/search/?api=1&query=37.7898491%2C-122.3963675"
    assert parse_geo(href) == (37.7898491, -122.3963675)
    assert parse_geo(None) is None
    assert parse_geo("https://example.com/?query=nowhere") is None
    assert parse_geo("https://example.com/?query=a,b") is None


def test_parse_map() -> None:
    entries = parse_map(fixture_text("fleetweeksf_map.html"))
    assert len(entries) == 54
    assert "Neighborhood Concert Program" in entries["53953"].text


def test_parse_map_missing() -> None:
    with pytest.raises(SourceError, match="oum_all_locations"):
        parse_map("<html></html>")


def test_parse_dedups_both_layouts(client: httpx.Client) -> None:
    descriptions = parse_map(fixture_text("fleetweeksf_map.html"))
    events = adapter(client).parse(fixture_text("fleetweeksf_calendar.html"), descriptions)

    assert len(events) == 59
    assert len({e.uid for e in events}) == 59
    assert {e.bucket for e in events} == {"fleet-week"}
    assert all(e.start.tzinfo is not None for e in events)

    quintet = next(e for e in events if e.source_id == "53560-20261006")
    assert quintet.title == "1st Marine Division Brass Quintet"
    assert quintet.start == datetime(2026, 10, 6, 12, 30, tzinfo=TZ)
    assert quintet.end is None
    assert quintet.venue == "TJPA Salesforce Park"
    assert quintet.geo == (37.7898491, -122.3963675)
    assert quintet.url == "https://fleetweeksf.org/events/free-concert-series/"
    assert quintet.categories == ["Music"]
    assert quintet.description is not None
    assert "Neighborhood Concert Program" in quintet.description

    tours = [e for e in events if e.title == "Ship Tours" and e.start.day == 7]
    assert {e.venue for e in tours} == {"Pier 27", "Pier 35"}
    assert all(e.end is not None and e.end - e.start >= timedelta(hours=3) for e in tours)


def test_parse_skips_url_only_description(client: httpx.Client) -> None:
    descriptions = parse_map(fixture_text("fleetweeksf_map.html"))
    events = adapter(client).parse(fixture_text("fleetweeksf_calendar.html"), descriptions)
    assert not any(e.description and e.description.startswith("http") for e in events)


def test_parse_skips_unparseable_time(client: httpx.Client) -> None:
    page = fixture_text("fleetweeksf_calendar.html").replace(
        '<span class="fw-event-date">12:30 PM</span>', '<span class="fw-event-date">TBA</span>'
    )
    events = adapter(client).parse(page, {})
    assert len(events) == 59 - 3
    assert all(e.description is None for e in events)


def test_parse_zero_rows_raises(client: httpx.Client) -> None:
    page = "<html><body><h1>2026 Fleet Week Calendar of Events</h1></body></html>"
    with pytest.raises(SourceError, match="no day sections"):
        adapter(client).parse(page, {})


@respx.mock
def test_fetch(client: httpx.Client) -> None:
    respx.get(CAL_URL).respond(text=fixture_text("fleetweeksf_calendar.html"))
    respx.get(MAP_URL).respond(text=fixture_text("fleetweeksf_map.html"))
    result = adapter(client).fetch(WINDOW, [], {})
    assert len(result.events) == 59
    assert sum(e.description is not None for e in result.events) > 40


@respx.mock
def test_fetch_survives_map_failure(client: httpx.Client) -> None:
    respx.get(CAL_URL).respond(text=fixture_text("fleetweeksf_calendar.html"))
    respx.get(MAP_URL).respond(status_code=500)
    result = adapter(client).fetch(WINDOW, [], {})
    assert len(result.events) == 59
    assert all(e.description is None for e in result.events)


@respx.mock
def test_fetch_calendar_failure_raises(client: httpx.Client) -> None:
    respx.get(CAL_URL).respond(status_code=404)
    with pytest.raises(httpx.HTTPStatusError):
        adapter(client).fetch(WINDOW, [], {})
