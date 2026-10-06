"""Tests for the generic ICS adapter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from textwrap import dedent

import httpx
import pytest
import respx

from sfcal import TZ
from sfcal.cfg import SourceCfg, load_cfg
from sfcal.models import Event
from sfcal.pipeline import assemble
from sfcal.sources.base import SourceError, Window
from sfcal.sources.icsfeed import IcsFeed, as_local, split_location
from tests.conftest import FIXTURES, fixture_text

URL = "https://example.com/feed.ics"
WINDOW = Window(
    start=datetime(2026, 10, 5, tzinfo=TZ), end=datetime(2026, 12, 5, tzinfo=TZ), full=True
)
FEED = dedent(
    """\
    BEGIN:VCALENDAR
    VERSION:2.0
    PRODID:-//test//EN
    BEGIN:VEVENT
    UID:weekly@example.com
    SUMMARY:Weekly Open Mic
    DTSTART;TZID=America/Los_Angeles:20261001T190000
    DURATION:PT2H
    RRULE:FREQ=WEEKLY;COUNT=6
    LOCATION:The Lost Church: 988 Columbus Ave, San Francisco, CA 94133
    GEO:37.80;-122.41
    CATEGORIES:comedy,music
    X-SOURCE-NAME:Bay Area Open Mic Calendar
    END:VEVENT
    BEGIN:VEVENT
    UID:naive@example.com
    SUMMARY:Floating
    DTSTART:20261020T120000
    DTEND:20261020T130000
    END:VEVENT
    BEGIN:VEVENT
    UID:utc@example.com
    SUMMARY:Late show
    DTSTART:20261021T033000Z
    URL:https://example.com/late
    DESCRIPTION:<p>Bring <b>friends</b></p>
    END:VEVENT
    BEGIN:VEVENT
    UID:allday@example.com
    SUMMARY:Street fair
    DTSTART;VALUE=DATE:20261024
    LOCATION:1 Main St, Oakland, CA
    END:VEVENT
    BEGIN:VEVENT
    UID:htmlloc@example.com
    SUMMARY:Dune cleanup
    DTSTART;TZID=America/Los_Angeles:20261024T100000
    LOCATION:<p>Sunset Dunes</p> - 1611 Upper Great Highway San Francisco CA 94122
    END:VEVENT
    BEGIN:VEVENT
    UID:cancelled@example.com
    SUMMARY:Called off
    STATUS:CANCELLED
    DTSTART:20261025T030000Z
    END:VEVENT
    BEGIN:VEVENT
    UID:untitled@example.com
    DTSTART:20261026T030000Z
    END:VEVENT
    END:VCALENDAR
    """
).replace("\n", "\r\n")


def adapter(
    client: httpx.Client, attribution: str | None = None, max_span_days: int | None = None
) -> IcsFeed:
    """Build the adapter.

    Parameters:
        client (httpx.Client): HTTP client.
        attribution (str | None): Attribution option.
        max_span_days (int | None): Span limit option.

    Returns:
        IcsFeed: Adapter.
    """
    options = {
        "url": URL,
        "bucket": "comedy",
        "attribution": attribution,
        "max_span_days": max_span_days,
    }
    cfg = SourceCfg(adapter="ics", label="Test", priority=40, options=options)
    return IcsFeed("feed", cfg, client)


def test_as_local() -> None:
    assert as_local(datetime(2026, 10, 10, 3, tzinfo=UTC)) == (
        datetime(2026, 10, 9, 20, tzinfo=TZ),
        False,
    )
    assert as_local(datetime(2026, 10, 10, 3, tzinfo=TZ).replace(tzinfo=None))[0].tzinfo == TZ
    assert as_local(datetime(2026, 10, 10, tzinfo=TZ).date()) == (
        datetime(2026, 10, 10, tzinfo=TZ),
        True,
    )


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Gray Area, 2665 Mission St, SF", ("Gray Area", "2665 Mission St, SF")),
        ("DNA Lounge: 375 Eleventh Street, SF", ("DNA Lounge", "375 Eleventh Street, SF")),
        ("1799 McAllister St, SF", (None, "1799 McAllister St, SF")),
        ("Marina Green", ("Marina Green", None)),
        ("  ", (None, None)),
        (None, (None, None)),
    ],
)
def test_split_location(location: str | None, expected: tuple[str | None, str | None]) -> None:
    assert split_location(location) == expected


@respx.mock
def test_fetch_expands_and_converts(client: httpx.Client) -> None:
    respx.get(URL).respond(content=FEED.encode())
    result = adapter(client, attribution="ODbL notice").fetch(WINDOW, [], {})
    by_title: dict[str, list[Event]] = {}
    for event in result.events:
        by_title.setdefault(event.title, []).append(event)

    assert set(by_title) == {
        "Weekly Open Mic",
        "Floating",
        "Late show",
        "Street fair",
        "Dune cleanup",
    }
    (dunes,) = by_title["Dune cleanup"]
    assert dunes.venue == "Sunset Dunes - 1611 Upper Great Highway San Francisco CA 94122"
    mics = sorted(by_title["Weekly Open Mic"], key=lambda e: e.start)
    assert [e.start.date().isoformat()[5:] for e in mics] == [
        "10-08",
        "10-15",
        "10-22",
        "10-29",
        "11-05",
    ]
    assert len({e.source_id for e in mics}) == 5
    mic = mics[0]
    assert mic.end == mic.start + timedelta(hours=2)
    assert (mic.venue, mic.locality) == ("The Lost Church", "San Francisco")
    assert mic.geo == (37.8, -122.41)
    assert mic.categories == ["comedy", "music"]
    assert mic.notes == ["Published by Bay Area Open Mic Calendar", "ODbL notice"]
    assert mic.bucket == "comedy"

    (floating,) = by_title["Floating"]
    assert floating.start == datetime(2026, 10, 20, 12, tzinfo=TZ)
    (late,) = by_title["Late show"]
    assert late.start == datetime(2026, 10, 20, 20, 30, tzinfo=TZ)
    assert late.end is None
    assert (late.url, late.description) == ("https://example.com/late", "Bring friends")
    (fair,) = by_title["Street fair"]
    assert fair.all_day
    assert fair.end == datetime(2026, 10, 25, tzinfo=TZ)
    assert fair.locality == "Oakland"


@respx.mock
def test_fetch_is_stable(client: httpx.Client) -> None:
    respx.get(URL).respond(content=FEED.encode())
    a = adapter(client).fetch(WINDOW, [], {})
    b = adapter(client).fetch(WINDOW, [], {})
    assert [e.uid for e in a.events] == [e.uid for e in b.events]


@respx.mock
def test_fetch_not_a_calendar(client: httpx.Client) -> None:
    respx.get(URL).respond(text="<html>Attention Required! | Cloudflare</html>")
    with pytest.raises(SourceError, match="Not an iCalendar feed"):
        adapter(client).fetch(WINDOW, [], {})


@respx.mock
def test_max_span_days_drops_umbrellas(client: httpx.Client) -> None:
    respx.get(URL).respond(content=(FIXTURES / "sfciviccenter.ics").read_bytes())
    everything = adapter(client).fetch(WINDOW, [], {}).events
    assert "Heart of the City Farmers Market" in {e.title for e in everything}

    result = adapter(client, max_span_days=3).fetch(WINDOW, [], {})
    by_title = {e.title: e for e in result.events}
    assert "Heart of the City Farmers Market" not in by_title
    assert "SF Symphony 2026-2027 Season" not in by_title
    assert {"Fall Family Festival", "Civic Center Plaza Tree Lighting 2026"} <= set(by_title)
    assert len(result.events) < len(everything)
    # TZID=UTC 2026-10-18T00:00 is 17:00 PDT the evening before.
    fall = by_title["Fall Family Festival"]
    assert fall.start == datetime(2026, 10, 17, 17, tzinfo=TZ)
    assert fall.end == datetime(2026, 10, 17, 20, tzinfo=TZ)


@respx.mock
def test_caldiscovery_fixture_drops_fleet_week_umbrella(client: httpx.Client) -> None:
    respx.get(URL).respond(content=(FIXTURES / "caldiscovery_festival.ics").read_bytes())
    window = Window(
        start=datetime(2026, 10, 4, tzinfo=TZ), end=datetime(2026, 12, 5, tzinfo=TZ), full=True
    )
    result = adapter(client).fetch(window, [], {})
    umbrella = [e for e in result.events if e.title == "Fleet Week San Francisco"]
    assert umbrella
    assert all(e.all_day for e in umbrella)

    cfg = load_cfg(FIXTURES.parent.parent / "sfcal.toml")
    cfg.sources["feed"] = cfg.sources["caldiscovery-festival"]
    asm, _ = assemble(cfg, result.events, {}, datetime(2026, 10, 5, tzinfo=UTC))
    assert asm.rule_dropped["feed"] == len(umbrella)
    assert "Fleet Week San Francisco" not in {f.event.title for f in asm.events}
    assert "BEGIN:VCALENDAR" in fixture_text("caldiscovery_festival.ics")
