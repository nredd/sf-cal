"""San Francisco Fleet Week official calendar.

The calendar page renders every event twice -- a desktop grid of day columns
and a mobile accordion of days -- so rows are collected from both layouts and
deduplicated by (post id, date). Descriptions are not on the calendar page;
they come from the Open User Map JSON embedded in the map page, joined on the
WordPress post id.

References:
- Calendar: https://fleetweeksf.org/calendar-of-events/
- Map: https://fleetweeksf.org/map-of-events/
- Open User Map plugin: https://wordpress.org/plugins/open-user-map/
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time
from typing import Any, override
from urllib.parse import parse_qs, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from sfcal import TZ
from sfcal.models import Event
from sfcal.sources.base import (
    Adapter,
    AdapterOptions,
    SourceError,
    SourceResult,
    Window,
    html_to_text,
)

LOGGER = logging.getLogger(__name__)

YEAR = re.compile(r"\b(20\d{2}) Fleet Week Calendar of Events\b")
DAY_LABEL = re.compile(
    r"^(?:[A-Za-z]+day,?\s+)?(?P<month>[A-Za-z]{3})[A-Za-z]*\.?\s+(?P<day>\d{1,2})$"
)
TIME_RANGE = re.compile(
    r"^(?P<start>\d{1,2}:\d{2}\s*[AP]M)(?:\s*[-\u2013\u2014]\s*(?P<end>\d{1,2}:\d{2}\s*[AP]M))?$",
    re.IGNORECASE,
)
CLOCK = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<half>[AP])M$", re.IGNORECASE)
MAP_JSON = re.compile(r"var oum_all_locations = (\[.*?\]);\s*$", re.MULTILINE | re.DOTALL)
OUM_TYPE = re.compile(r"\boum-type-([a-z0-9-]+)\b")
MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}


class FleetWeekOptions(AdapterOptions):
    calendar_url: str = Field(default="https://fleetweeksf.org/calendar-of-events/")
    map_url: str = Field(default="https://fleetweeksf.org/map-of-events/")
    bucket: str = Field(default="fleet-week", description="Bucket every event goes to")


class MapEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    post_id: str = Field(description="WordPress post id, the join key")
    title: str = Field(default="", description="Location title")
    address: str = Field(default="", description="Free-text location name")
    text: str = Field(default="", description="HTML description")


def parse_year(page: LexborHTMLParser) -> int:
    """Read the edition year from the calendar heading.

    Parameters:
        page (LexborHTMLParser): Calendar page.

    Returns:
        int: Year, e.g. `2026`.

    Raises:
        SourceError: If the heading is missing.
    """
    body = page.body
    match = YEAR.search(body.text() if body else "")
    if not match:
        raise SourceError("Fleet Week calendar heading with a year not found")
    return int(match.group(1))


def parse_day(label: str, year: int) -> date:
    """Parse a day label such as `"Tuesday, Oct 6"` or `"Tuesday October 6"`.

    Parameters:
        label (str): Day label.
        year (int): Edition year.

    Returns:
        date: The calendar date.

    Raises:
        SourceError: If the label is not recognized.
    """
    match = DAY_LABEL.match(" ".join(label.split()))
    month = MONTHS.get(match.group("month").casefold()) if match else None
    if not match or month is None:
        raise SourceError(f"Unrecognized Fleet Week day label: '{label}'")
    return date(year, month, int(match.group("day")))


def parse_times(text: str, day: date) -> tuple[datetime, datetime | None]:
    """Parse `"10:00 AM - 4:00 PM"` or `"5:00 PM"` on `day`.

    Parameters:
        text (str): Time text from the row.
        day (date): Date of the row.

    Returns:
        tuple[datetime, datetime | None]: Aware start, and end when a range was given.

    Raises:
        ValueError: If the text is not a time or time range.
    """
    match = TIME_RANGE.match(" ".join(text.split()))
    if not match:
        raise ValueError(f"Unrecognized Fleet Week time: '{text}'")

    def at(clock: str) -> datetime:
        """Combine `day` with a 12-hour clock string.

        Parameters:
            clock (str): e.g. `"5:00 PM"`.

        Returns:
            datetime: Aware local datetime.
        """
        match = CLOCK.match(clock)
        if not match or not 1 <= int(match.group("hour")) <= 12:
            raise ValueError(f"Unrecognized Fleet Week clock time: '{clock}'")
        hour = int(match.group("hour")) % 12 + (12 if match.group("half").upper() == "P" else 0)
        return datetime.combine(day, time(hour, int(match.group("minute"))), tzinfo=TZ)

    start = at(match.group("start"))
    end = at(match.group("end")) if match.group("end") else None
    return start, end


def parse_geo(href: str | None) -> tuple[float, float] | None:
    """Pull `lat,lng` out of a Google Maps search link.

    Parameters:
        href (str | None): `https://www.google.com/maps/search/?api=1&query=lat%2Clng`.

    Returns:
        tuple[float, float] | None: Coordinates, or `None` when absent or malformed.
    """
    if not href:
        return None
    query = parse_qs(urlparse(href).query).get("query", [""])[0]
    lat, sep, lng = query.partition(",")
    try:
        return (float(lat), float(lng)) if sep else None
    except ValueError:
        return None


def parse_map(page_html: str) -> dict[str, MapEntry]:
    """Extract Open User Map entries keyed by post id.

    Parameters:
        page_html (str): Map page HTML.

    Returns:
        dict[str, MapEntry]: Entries by WordPress post id.

    Raises:
        SourceError: If the embedded JSON is missing or malformed.
    """
    match = MAP_JSON.search(page_html)
    if not match:
        raise SourceError("`oum_all_locations` not found on the Fleet Week map page")
    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        raise SourceError(f"Malformed `oum_all_locations` JSON: {e}") from e
    entries = [MapEntry.model_validate(item) for item in raw]
    return {entry.post_id: entry for entry in entries}


def day_sections(page: LexborHTMLParser) -> list[tuple[str, list[LexborNode]]]:
    """Collect (day label, post nodes) from both page layouts.

    Parameters:
        page (LexborHTMLParser): Calendar page.

    Returns:
        list[tuple[str, list[LexborNode]]]: One entry per rendered day.
    """
    sections: list[tuple[str, list[LexborNode]]] = []
    for col in page.css(".fl-col"):
        headings = col.css(".datename .fl-heading-text")
        if len(headings) == 1:
            sections.append((headings[0].text(strip=True), col.css(".pp-content-post")))
    for item in page.css(".uabb-adv-accordion-item"):
        label = item.css_first(".uabb-adv-accordion-button-label")
        if label is not None:
            sections.append((label.text(strip=True), item.css(".pp-content-post")))
    return sections


class FleetWeekSF(Adapter[FleetWeekOptions]):
    """Official SF Fleet Week schedule: concerts, ship tours, air shows, Fleet Fest."""

    options_model = FleetWeekOptions

    @override
    def referenced_buckets(self) -> set[str]:
        """The one bucket every Fleet Week event goes to.

        Returns:
            set[str]: Bucket keys.
        """
        return {self.opts.bucket}

    @override
    def fetch(self, window: Window, prior: list[Event], meta: dict[str, Any]) -> SourceResult:
        """Scrape the calendar and enrich rows from the map page.

        Parameters:
            window (Window): Unused; the page is one request for the whole week.
            prior (list[Event]): Unused.
            meta (dict[str, Any]): Unused.

        Returns:
            SourceResult: Every event on the calendar.

        Raises:
            SourceError: If the calendar has no days or no rows.
            httpx.HTTPError: If the calendar request fails.
        """
        opts = self.opts
        calendar_html = self.get(opts.calendar_url).text
        try:
            descriptions = parse_map(self.get(opts.map_url).text)
        except (httpx.HTTPError, SourceError) as e:
            LOGGER.warning(f"Fleet Week map unavailable, continuing without descriptions: {e}")
            descriptions = {}
        return SourceResult(events=self.parse(calendar_html, descriptions))

    def parse(self, calendar_html: str, descriptions: dict[str, MapEntry]) -> list[Event]:
        """Turn the calendar page into events.

        Parameters:
            calendar_html (str): Calendar page HTML.
            descriptions (dict[str, MapEntry]): Map entries by post id.

        Returns:
            list[Event]: Deduplicated events.

        Raises:
            SourceError: If the page has no days or no rows.
        """
        opts = self.opts
        page = LexborHTMLParser(calendar_html)
        year = parse_year(page)
        sections = day_sections(page)
        if not sections or not any(posts for _, posts in sections):
            raise SourceError("Fleet Week calendar has no day sections or no event rows")

        events: dict[str, Event] = {}
        for label, posts in sections:
            day = parse_day(label, year)
            for post in posts:
                event = self._row(post, day, descriptions, opts.bucket)
                if event is not None:
                    events.setdefault(event.source_id, event)
        LOGGER.info(f"`{self.name}`: {len(events)} events from {len(sections)} day sections")
        return list(events.values())

    def _row(
        self, post: LexborNode, day: date, descriptions: dict[str, MapEntry], bucket: str
    ) -> Event | None:
        """Convert one calendar row.

        Parameters:
            post (LexborNode): `.pp-content-post` node.
            day (date): Day the row is listed under.
            descriptions (dict[str, MapEntry]): Map entries by post id.
            bucket (str): Target bucket.

        Returns:
            Event | None: The event, or `None` when the row is unusable.
        """
        post_id = post.attributes.get("data-id") or ""
        title_node = post.css_first(".fw-event-title")
        time_node = post.css_first(".fw-event-date")
        if not post_id or title_node is None or time_node is None:
            LOGGER.warning(f"Skipping Fleet Week row missing id, title or time on '{day}'")
            return None
        title = title_node.text(separator="", strip=False)
        try:
            start, end = parse_times(time_node.text(strip=True), day)
        except ValueError as e:
            LOGGER.warning(f"Skipping Fleet Week row '{title.strip()}': {e}")
            return None

        link = title_node.css_first("a")
        loc_node = post.css_first(".fw-event-location")
        loc_link = loc_node.css_first("a") if loc_node is not None else None
        entry = descriptions.get(post_id)
        venue = loc_node.text(strip=True) if loc_node is not None else ""
        body = html_to_text(entry.text) if entry else None
        type_match = OUM_TYPE.search(post.attributes.get("class") or "")

        return Event(
            source=self.name,
            source_id=f"{post_id}-{day:%Y%m%d}",
            title=title,
            start=start,
            end=end,
            bucket=bucket,
            venue=venue or (entry.address if entry else None) or None,
            locality="San Francisco",
            geo=parse_geo(loc_link.attributes.get("href") if loc_link is not None else None),
            url=link.attributes.get("href") if link is not None else None,
            description=None if body is None or body.startswith("http") else body,
            categories=[type_match.group(1).replace("-", " ").title()] if type_match else [],
        )
