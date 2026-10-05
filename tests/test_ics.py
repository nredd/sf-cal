"""Tests for ICS rendering, writing and validation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from icalendar import Calendar

from sfcal.ics import FeedEvent, build_calendar, describe, validate_feed, write_feeds
from sfcal.models import Event, Revision
from tests.conftest import make_cfg, make_event

STAMP = datetime(2026, 10, 1, 12, tzinfo=UTC)


def feed(event: Event, also: tuple[str, ...] = (), sequence: int = 0) -> FeedEvent:
    """Wrap an event with a fixed revision.

    Parameters:
        event (Event): Event.
        also (tuple[str, ...]): Also-listed URLs.
        sequence (int): SEQUENCE.

    Returns:
        FeedEvent: Feed event.
    """
    rev = Revision(content_hash="x", sequence=sequence, last_modified=STAMP)
    return FeedEvent(event=event, also_listed=also, revision=rev)


def test_describe_full() -> None:
    event = make_event(
        description="Body text.",
        price_text="$25",
        sold_out=True,
        notes=["All ages"],
        ticket_url="https://t/1",
        url="https://d/1",
    )
    text = describe(event, "DoTheBay", ("https://x/1",))
    assert text == (
        "Body text.\n\n$25\nSold out\nAll ages\n\nTickets: https://t/1\nDetails: https://d/1\n"
        "Source: DoTheBay\n\nAlso listed on:\n- https://x/1"
    )


def test_describe_free_beats_price() -> None:
    text = describe(make_event(is_free=True, price_text="$0.00"), "S", ())
    assert text == "Free\n\nSource: S"


def test_vevent_properties() -> None:
    event = make_event(
        venue="The Castro",
        address="429 Castro St, San Francisco, CA, 94114",
        geo=(37.76, -122.43),
        url="https://d/1",
        image_url="https://img/1.jpg",
        categories=["Music"],
        end=datetime(2026, 10, 11, 6, tzinfo=UTC),
    )
    cal = build_calendar("Music", "desc", [feed(event, sequence=4)], {"alpha": "Alpha"})
    parsed = Calendar.from_ical(cal.to_ical())
    (vevent,) = parsed.walk("VEVENT")
    assert str(vevent["uid"]) == "alpha-1@sf-cal"
    assert str(vevent["location"]) == "The Castro, 429 Castro St, San Francisco, CA, 94114"
    assert vevent["sequence"] == 4
    assert vevent.decoded("dtstamp") == STAMP
    assert str(vevent["transp"]) == "TRANSPARENT"
    assert str(vevent["image"]) == "https://img/1.jpg"
    assert vevent["geo"].latitude == pytest.approx(37.76)
    assert "VALARM" not in cal.to_ical().decode()
    assert str(parsed["x-wr-calname"]) == "SF Events · Music"
    assert parsed["refresh-interval"].to_ical() == b"PT3H"


def test_location_falls_back_to_locality() -> None:
    assert make_event(venue="Pier 27").location == "Pier 27, San Francisco"
    assert make_event(venue=None, locality=None).location is None
    dup = make_event(venue="429 Castro St", address="429 Castro St, San Francisco")
    assert dup.location == "429 Castro St, San Francisco"


def test_all_day_uses_dates() -> None:
    event = make_event(
        start=datetime(2026, 10, 10, 7, tzinfo=UTC),
        end=datetime(2026, 10, 11, 7, tzinfo=UTC),
        all_day=True,
    )
    ical = build_calendar("M", "d", [feed(event)], {"alpha": "A"}).to_ical().decode()
    assert "DTSTART;VALUE=DATE:20261010" in ical
    assert "DTEND;VALUE=DATE:20261011" in ical


def test_write_feeds_all_buckets_and_empty(tmp_path: Path) -> None:
    cfg = make_cfg()
    events = [feed(make_event()), feed(make_event(source_id="2", bucket="nightlife"))]
    counts = write_feeds(cfg, events, tmp_path)
    assert counts == {"fleet-week": 0, "music": 1, "nightlife": 1, "all": 2}
    empty = Calendar.from_ical((tmp_path / "fleet-week.ics").read_bytes())
    assert empty.walk("VTIMEZONE")
    assert not list(tmp_path.glob(".sfcal-*"))


def test_write_feeds_is_deterministic(tmp_path: Path) -> None:
    cfg = make_cfg()
    a = feed(make_event(source_id="a", title="A"))
    b = feed(make_event(source_id="b", title="B"))
    write_feeds(cfg, [a, b], tmp_path / "one")
    write_feeds(cfg, [b, a], tmp_path / "two")
    for name in ("music.ics", "all.ics"):
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()


def test_validate_feed_count_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "x.ics"
    path.write_bytes(build_calendar("M", "d", [], {}).to_ical())
    assert validate_feed(path, expected=0) == 0
    with pytest.raises(ValueError, match="expected '1'"):
        validate_feed(path, expected=1)


def test_validate_feed_garbage(tmp_path: Path) -> None:
    path = tmp_path / "x.ics"
    path.write_text("not a calendar")
    with pytest.raises(ValueError, match="does not parse"):
        validate_feed(path, expected=0)
