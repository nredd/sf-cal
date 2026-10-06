"""Any published iCalendar feed, mapped wholesale onto one bucket.

Recurring events are expanded inside the window with
`recurring-ical-events`. Feeds are one request each, so this adapter always
reads the full horizon.

References:
- RFC 5545: https://www.rfc-editor.org/rfc/rfc5545
- recurring-ical-events: https://github.com/niccokunzmann/python-recurring-ical-events
- CalDiscovery (ODbL 1.0): https://caldiscovery.com/san-francisco-ca/
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any, ClassVar, override

import recurring_ical_events
from icalendar import Calendar
from icalendar import Event as VEvent
from pydantic import Field

from sfcal import TZ
from sfcal.models import Event
from sfcal.sources.base import (
    Adapter,
    AdapterOptions,
    SourceError,
    SourceResult,
    Window,
    html_to_text,
    locality_from_address,
)

LOGGER = logging.getLogger(__name__)

VENUE_SPLIT = re.compile(r"\s*[:,]\s*")
DESCRIPTION_LIMIT = 1500


class IcsOptions(AdapterOptions):
    url: str = Field(description="Feed URL")
    bucket: str = Field(description="Bucket every event in the feed goes to")
    attribution: str | None = Field(
        default=None, description="Line added to every description, e.g. a license notice"
    )
    max_span_days: int | None = Field(
        default=None, ge=0, description="Skip longer occurrences, e.g. season-long umbrellas"
    )


def as_local(value: date | datetime) -> tuple[datetime, bool]:
    """Normalize a DTSTART/DTEND value.

    Parameters:
        value (date | datetime): Decoded iCalendar value.

    Returns:
        tuple[datetime, bool]: Aware local datetime, and whether it was a DATE.
    """
    if not isinstance(value, datetime):
        return datetime.combine(value, time(), tzinfo=TZ), True
    return (value if value.tzinfo else value.replace(tzinfo=TZ)).astimezone(TZ), False


def split_location(location: str | None) -> tuple[str | None, str | None]:
    """Split `"Venue, 1 Main St, City, CA"` or `"Venue: 1 Main St"` into parts.

    Parameters:
        location (str | None): LOCATION text.

    Returns:
        tuple[str | None, str | None]: Venue and address; the venue is `None`
        when the text starts with a street number.
    """
    text = " ".join((location or "").split())
    if not text:
        return None, None
    if text[:1].isdigit():
        return None, text
    parts = VENUE_SPLIT.split(text, maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (text, None)


class IcsFeed(Adapter[IcsOptions]):
    """Generic ICS subscription."""

    options_model = IcsOptions
    always_full: ClassVar[bool] = True

    @override
    def referenced_buckets(self) -> set[str]:
        """The feed's bucket.

        Returns:
            set[str]: Bucket keys.
        """
        return {self.opts.bucket}

    @override
    def fetch(self, window: Window, prior: list[Event], meta: dict[str, Any]) -> SourceResult:
        """Fetch the feed and expand occurrences inside the window.

        Parameters:
            window (Window): Range to expand recurrences in.
            prior (list[Event]): Unused.
            meta (dict[str, Any]): Unused.

        Returns:
            SourceResult: One event per occurrence.

        Raises:
            SourceError: If the body is not an iCalendar feed.
            httpx.HTTPError: If the request fails.
        """
        body = self.get(self.opts.url).content
        try:
            cal = Calendar.from_ical(body)
        except ValueError as e:
            raise SourceError(f"Not an iCalendar feed: '{self.opts.url}': {e}") from e
        occurrences = recurring_ical_events.of(cal).between(window.start, window.end)
        events: list[Event] = []
        for component in occurrences:
            try:
                event = self.convert(component)
            except (ValueError, KeyError, TypeError) as e:
                LOGGER.warning(f"`{self.name}`: skipping '{component.get('summary')}': {e}")
                continue
            if event is not None:
                events.append(event)
        LOGGER.info(f"`{self.name}`: {len(events)} occurrences from '{self.opts.url}'")
        return SourceResult(events=events)

    def convert(self, component: VEvent) -> Event | None:
        """Convert one expanded VEVENT occurrence.

        Parameters:
            component (VEvent): Occurrence.

        Returns:
            Event | None: The event, or `None` when cancelled or longer than
            `max_span_days`.

        Raises:
            KeyError: If DTSTART is missing.
            ValueError: If the event fails validation.
        """
        if str(component.get("status", "")).upper() == "CANCELLED":
            return None
        start, all_day = as_local(component.decoded("dtstart"))
        # NOTE(redd): occurrences always carry DTEND (DURATION is folded in), and an
        # event without one gets DTEND = DTSTART. Zero length means "no end given".
        end = as_local(component.decoded("dtend"))[0] if "dtend" in component else None
        if end is not None and end <= start:
            end = None
        if all_day and end is None:
            end = start + timedelta(days=1)
        if (
            self.opts.max_span_days is not None
            and end is not None
            and end - start > timedelta(days=self.opts.max_span_days)
        ):
            return None

        uid = str(component.get("uid", "")) or str(component.get("summary", ""))
        digest = hashlib.sha1(f"{uid}|{start.isoformat()}".encode(), usedforsecurity=False)
        # NOTE(redd): CivicPlus (Rec & Park) puts HTML in LOCATION.
        location = html_to_text(str(component.get("location", "")))
        venue, address = split_location(location)
        geo = component.get("geo")
        categories = component.get("categories")
        cats = (
            [str(c) for c in categories.cats]
            if categories is not None and not isinstance(categories, list)
            else [str(c) for cat in categories or [] for c in cat.cats]
        )
        publisher = str(component.get("x-source-name", "")).strip()
        body = html_to_text(str(component.get("description", "")))
        if body and len(body) > DESCRIPTION_LIMIT:
            body = body[:DESCRIPTION_LIMIT].rstrip() + "..."
        return Event(
            source=self.name,
            source_id=digest.hexdigest()[:16],
            title=str(component.get("summary", "")),
            start=start,
            end=end,
            all_day=all_day,
            bucket=self.opts.bucket,
            venue=venue,
            address=address,
            locality=locality_from_address(location),
            geo=(geo.latitude, geo.longitude) if geo is not None else None,
            url=str(component["url"]) if "url" in component else None,
            description=body,
            categories=cats,
            notes=[
                *([f"Published by {publisher}"] if publisher else []),
                *([self.opts.attribution] if self.opts.attribution else []),
            ],
        )
