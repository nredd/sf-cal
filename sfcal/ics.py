"""Render events to RFC 5545 calendars and validate the written files.

Output is deterministic: no build timestamps, events sorted by start then
UID, and DTSTAMP pinned to the revision's last content change. A run that
finds nothing new rewrites byte-identical files and produces no commit.

References:
- RFC 5545: https://www.rfc-editor.org/rfc/rfc5545
- RFC 7986 (NAME, REFRESH-INTERVAL, IMAGE): https://www.rfc-editor.org/rfc/rfc7986
- icalendar: https://icalendar.readthedocs.io/
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, timedelta
from pathlib import Path
from typing import Any

from icalendar import Calendar, Timezone, vDuration, vUri
from icalendar import Event as VEvent

from sfcal import TZ
from sfcal.cfg import ALL_FEED, Cfg
from sfcal.models import Event, Revision

LOGGER = logging.getLogger(__name__)

PRODID = "-//nredd//sf-cal//EN"
CALNAME_PREFIX = "SF Events"
REFRESH = timedelta(hours=3)
TZ_FIRST_DATE = date(2020, 1, 1)
TZ_LAST_DATE = date(2038, 1, 1)


@dataclass(frozen=True)
class FeedEvent:
    """An event ready to publish: final bucket, dedup links and revision."""

    event: Event
    also_listed: tuple[str, ...]
    revision: Revision


def describe(event: Event, source_label: str, also_listed: tuple[str, ...]) -> str:
    """Build the DESCRIPTION body.

    Parameters:
        event (Event): Event to describe.
        source_label (str): Human-readable source name.
        also_listed (tuple[str, ...]): URLs of duplicate listings on other sources.

    Returns:
        str: Plain-text description, sections separated by blank lines.
    """
    facts = [
        *(["Free"] if event.is_free else [event.price_text] if event.price_text else []),
        *(["Sold out"] if event.sold_out else []),
        *event.notes,
    ]
    links = [
        *([f"Tickets: {event.ticket_url}"] if event.ticket_url else []),
        *([f"Details: {event.url}"] if event.url else []),
        f"Source: {source_label}",
    ]
    sections = [
        event.description or "",
        "\n".join(facts),
        "\n".join(links),
        "\n".join(["Also listed on:", *(f"- {u}" for u in also_listed)]) if also_listed else "",
    ]
    return "\n\n".join(s for s in sections if s)


def render(event: Event, source_label: str, also_listed: tuple[str, ...]) -> dict[str, Any]:
    """Compute every published property value of an event.

    This is the single source of truth for both the VEVENT and its content
    hash, so SEQUENCE bumps exactly when published content changes.

    Parameters:
        event (Event): Event with its final bucket and a filled-in end.
        source_label (str): Human-readable source name.
        also_listed (tuple[str, ...]): URLs of duplicate listings on other sources.

    Returns:
        dict[str, Any]: Property name to value, `None` for absent properties.
    """
    start: date = event.start.date() if event.all_day else event.start
    end: date | None = event.end
    if event.all_day and event.end is not None:
        end = event.end.date()
    return {
        "summary": event.title,
        "dtstart": start,
        "dtend": end,
        "location": event.location,
        "geo": event.geo,
        "url": event.url,
        "categories": event.categories or None,
        "image": event.image_url,
        "description": describe(event, source_label, also_listed),
        "bucket": event.bucket,
    }


def to_vevent(feed_event: FeedEvent, source_label: str) -> VEvent:
    """Convert a feed event into an icalendar VEVENT.

    Parameters:
        feed_event (FeedEvent): Event to convert.
        source_label (str): Human-readable source name.

    Returns:
        VEvent: The component.
    """
    event, rev = feed_event.event, feed_event.revision
    fields = render(event, source_label, feed_event.also_listed)
    vevent = VEvent()
    vevent.add("uid", event.uid)
    vevent.add("dtstamp", rev.last_modified.astimezone(UTC))
    vevent.add("last-modified", rev.last_modified.astimezone(UTC))
    vevent.add("sequence", rev.sequence)
    vevent.add("summary", fields["summary"])
    vevent.add("dtstart", fields["dtstart"])
    if fields["dtend"] is not None:
        vevent.add("dtend", fields["dtend"])
    vevent.add("transp", "TRANSPARENT")
    for key in ("location", "url", "description"):
        if fields[key]:
            vevent.add(key, fields[key])
    if fields["geo"]:
        vevent.add("geo", fields["geo"])
    if fields["categories"]:
        vevent.add("categories", fields["categories"])
    if fields["image"]:
        vevent.add("image", vUri(fields["image"]), parameters={"VALUE": "URI"})
    return vevent


def build_calendar(
    name: str, description: str, events: list[FeedEvent], labels: dict[str, str]
) -> Calendar:
    """Assemble one feed.

    Parameters:
        name (str): Bucket display name.
        description (str): Bucket description.
        events (list[FeedEvent]): Events in the feed, any order.
        labels (dict[str, str]): Source name to human-readable label.

    Returns:
        Calendar: The calendar, with a VTIMEZONE even when empty.

    Every `Event` is normalized to `TZ`, so the one explicit VTIMEZONE covers
    every DTSTART/DTEND. It is added unconditionally because RFC 5545 needs at
    least one component per calendar, and `Calendar.add_missing_timezones`
    raises `KeyError` when a VTIMEZONE is present but unreferenced (icalendar 6.3).
    """
    cal = Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("name", f"{CALNAME_PREFIX} · {name}")
    cal.add("x-wr-calname", f"{CALNAME_PREFIX} · {name}")
    cal.add("x-wr-caldesc", description)
    cal.add("x-wr-timezone", str(TZ))
    cal.add("refresh-interval", vDuration(REFRESH), parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", vDuration(REFRESH))
    cal.add_component(
        Timezone.from_tzinfo(TZ, tzid=str(TZ), first_date=TZ_FIRST_DATE, last_date=TZ_LAST_DATE)
    )
    for feed_event in sorted(events, key=lambda f: (f.event.start, f.event.uid)):
        cal.add_component(to_vevent(feed_event, labels[feed_event.event.source]))
    return cal


def write_feeds(cfg: Cfg, events: list[FeedEvent], out: Path) -> dict[str, int]:
    """Write one `.ics` per bucket plus `all.ics`, validating before replacing anything.

    `all.ics` leaves out buckets with `in_all = false`.

    Every feed is written to a scratch directory and re-parsed first, so a
    validation failure leaves the previous feeds untouched.

    Parameters:
        cfg (Cfg): Config, for bucket names and source labels.
        events (list[FeedEvent]): Every published event.
        out (Path): Output directory.

    Returns:
        dict[str, int]: Feed name to number of events written.

    Raises:
        ValueError: If a written file does not parse back with the same event count.
    """
    out = Path(str(out)).resolve()
    out.mkdir(parents=True, exist_ok=True)
    labels = {name: src.label for name, src in cfg.sources.items()}
    feeds: dict[str, tuple[str, str, list[FeedEvent]]] = {
        key: (b.name, b.description, [f for f in events if f.event.bucket == key])
        for key, b in cfg.buckets.items()
    }
    everything = [f for f in events if cfg.buckets[f.event.bucket].in_all]
    feeds[ALL_FEED] = ("All", "Every SF event from every source and bucket", everything)

    counts: dict[str, int] = {}
    with tempfile.TemporaryDirectory(dir=out, prefix=".sfcal-") as scratch:
        staged = Path(scratch)
        for key, (name, description, members) in feeds.items():
            path = staged / f"{key}.ics"
            path.write_bytes(build_calendar(name, description, members, labels).to_ical())
            counts[key] = validate_feed(path, expected=len(members))
        for key in feeds:
            (staged / f"{key}.ics").replace(out / f"{key}.ics")
    return counts


def validate_feed(path: Path, *, expected: int) -> int:
    """Re-parse a written feed and check its event count.

    Parameters:
        path (Path): `.ics` file.
        expected (int): Number of VEVENTs that were written.

    Returns:
        int: Number of VEVENTs parsed.

    Raises:
        ValueError: If the file does not parse or the count differs.
    """
    try:
        cal = Calendar.from_ical(path.read_bytes())
    except ValueError as e:
        raise ValueError(f"Written feed does not parse: '{path}': {e}") from e
    found = len(cal.walk("VEVENT"))
    if found != expected:
        raise ValueError(f"Feed '{path}' has '{found}' events, expected '{expected}'")
    return found
