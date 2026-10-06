"""Tests for the City Hall lighting adapter."""

from __future__ import annotations

from datetime import date, datetime

import httpx
import pytest
import respx

from sfcal import TZ
from sfcal.cfg import SourceCfg
from sfcal.sources.base import SourceError, Window
from sfcal.sources.cityhall import ADDRESS, GEO, CityHall, parse_entry
from tests.conftest import fixture_text

EN = "\u2013"  # en dash, as sf.gov writes it
URL = "https://www.sf.gov/location--san-francisco-city-hall"
WINDOW = Window(
    start=datetime(2026, 10, 5, tzinfo=TZ), end=datetime(2026, 12, 5, tzinfo=TZ), full=True
)


def adapter(client: httpx.Client) -> CityHall:
    """Build the adapter.

    Parameters:
        client (httpx.Client): HTTP client.

    Returns:
        CityHall: Adapter.
    """
    options = {"url": URL, "bucket": "city-hall-lights"}
    cfg = SourceCfg(adapter="cityhall", label="Test", priority=90, options=options)
    return CityHall("lights", cfg, client)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            f"Saturday, October 24, 2026 {EN} green/red/black/orange - in recognition of "
            "National Day of Zambia",
            ([date(2026, 10, 24)], "green/red/black/orange", "National Day of Zambia"),
        ),
        (
            f"Wednesday, July 29, 2026 {EN}red/white - in honor of Peru",
            ([date(2026, 7, 29)], "red/white", "Peru"),
        ),
        (
            "Friday, July 10, 2026 - blue/red/ yellow - National Day of the Bahamas",
            ([date(2026, 7, 10)], "blue/red/yellow", "National Day of the Bahamas"),
        ),
        (
            f"Saturday, July 4, 2026 {EN} red/white/blue",
            ([date(2026, 7, 4)], "red/white/blue", ""),
        ),
        (
            f"Monday, March 2 {EN} teal/pink in recognition of Cleft Awareness Month",
            None,
        ),
        (
            f"Sunday, February 1 - Sunday, February 3, 2026 {EN} blue/pink/yellow - "
            "SF Bay Area hosting Super Bowl LX",
            (
                [date(2026, 2, 1), date(2026, 2, 2), date(2026, 2, 3)],
                "blue/pink/yellow",
                "SF Bay Area hosting Super Bowl LX",
            ),
        ),
        (
            f"Monday, March 15-16, 2027 {EN} green in recognition of St. Patrick's Day",
            ([date(2027, 3, 15), date(2027, 3, 16)], "green", "St. Patrick's Day"),
        ),
        (
            f"December 31 - January 1, 2027 {EN} gold - New Year",
            ([date(2026, 12, 31), date(2027, 1, 1)], "gold", "New Year"),
        ),
        ("City Hall will be lit in the month of October for the following:", None),
        (f"Tuesday, October 6, 2026 {EN}", None),
    ],
)
def test_parse_entry(text: str, expected: tuple[list[date], str, str] | None) -> None:
    assert parse_entry(text) == expected


def test_parse_entry_bad_date() -> None:
    with pytest.raises(ValueError, match="Unknown month"):
        parse_entry(f"Friday, Octember 2, 2026 {EN} blue")


@respx.mock
def test_fetch_fixture(client: httpx.Client) -> None:
    respx.get(URL).respond(text=fixture_text("cityhall_page.html"))
    result = adapter(client).fetch(WINDOW, [], {})
    by_day = {e.start.date(): e for e in result.events}

    # Sep 30 through Oct 3 are before the window; they carry over in the pipeline.
    assert min(by_day) == date(2026, 10, 6)
    assert len(by_day) == 11
    zambia = by_day[date(2026, 10, 24)]
    assert zambia.title == "City Hall: green/red/black/orange for National Day of Zambia"
    assert zambia.all_day
    assert zambia.start == datetime(2026, 10, 24, tzinfo=TZ)
    assert zambia.end == datetime(2026, 10, 25, tzinfo=TZ)
    assert zambia.source_id == "2026-10-24"
    assert (zambia.venue, zambia.address, zambia.geo) == ("San Francisco City Hall", ADDRESS, GEO)
    assert zambia.url == URL
    assert zambia.bucket == "city-hall-lights"
    assert (
        zambia.description == "Lit green/red/black/orange in recognition of National Day of Zambia"
    )
    assert by_day[date(2026, 10, 17)].title == (
        "City Hall: pink for the Inaugural San Francisco Market Street Arts & Theater Festival"
    )


@respx.mock
def test_fetch_skips_bad_dates_and_keeps_first_entry(client: httpx.Client) -> None:
    html = (
        "<p>City Hall will be lit in the month of <b>October</b> for the following:</p>"
        f"<p><b>Friday, Octember 9, 2026 {EN} blue -</b> typo</p>"
        f"<p><b>Friday, October 9, 2026 {EN} red/white/blue</b></p>"
        f"<p><b>Friday, October 9, 2026 {EN} green -</b> duplicate</p>"
        f"<p>Saturday, October 10, 2026 {EN} no bold, not an entry</p>"
    )
    respx.get(URL).respond(text=html)
    (event,) = adapter(client).fetch(WINDOW, [], {}).events
    assert event.title == "City Hall: red/white/blue"
    assert event.description == "Lit red/white/blue"


@respx.mock
def test_fetch_empty_month_is_not_an_error(client: httpx.Client) -> None:
    respx.get(URL).respond(text="<p>City Hall will be lit in the month of <b>November</b></p>")
    assert adapter(client).fetch(WINDOW, [], {}).events == []


@respx.mock
def test_fetch_no_schedule(client: httpx.Client) -> None:
    respx.get(URL).respond(202, text="<html><script>AwsWafIntegration</script></html>")
    with pytest.raises(SourceError, match="No lighting schedule found"):
        adapter(client).fetch(WINDOW, [], {})
