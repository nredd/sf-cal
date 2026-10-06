"""San Francisco City Hall's exterior lighting schedule, scraped from sf.gov.

The City Hall location page carries a month-by-month list of colored lighting
nights, one paragraph per entry:

    <p><b>Saturday, October 24, 2026 [en dash] green/red/black/orange -</b>
    in recognition of National Day of Zambia</p>

Only the current month (and sometimes the tail of the last one) is listed, so
past nights survive through the pipeline's carry-over.

References:
- City Hall page: https://www.sf.gov/location--san-francisco-city-hall
- Lighting program: https://www.sf.gov/information--about-city-halls-exterior-lighting
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any, ClassVar, override

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from sfcal import TZ
from sfcal.models import Event
from sfcal.sources.base import Adapter, AdapterOptions, SourceError, SourceResult, Window

LOGGER = logging.getLogger(__name__)

DASH_CHARS = " -\u2013\u2014"
DASH = r"[-\u2013\u2014]"
WEEKDAY = r"(?:[A-Z][a-z]+,\s*)?"
MONTHS = {name.casefold(): i for i, name in enumerate(calendar.month_name) if name}
DATES = re.compile(
    rf"^{WEEKDAY}(?P<m1>[A-Z][a-z]+)\s+(?P<d1>\d{{1,2}})"
    rf"(?:\s*{DASH}\s*{WEEKDAY}(?:(?P<m2>[A-Z][a-z]+)\s+)?(?P<d2>\d{{1,2}}))?"
    r",?\s+(?P<year>\d{4})"
)
# Colors end at the first dash with a space on either side, or at "in recognition of".
SEPARATOR = re.compile(
    rf"\s+{DASH}\s*|\s*{DASH}\s+|(?=\bin (?:recognition|honor) of\b)", re.IGNORECASE
)
HONOREE_LEAD = re.compile(rf"^(?:\s|{DASH})*(?:in (?:recognition|honor) of\s+)?", re.IGNORECASE)
SLASH = re.compile(r"\s*/\s*")
SCHEDULE_MARKER = "city hall will be lit"

VENUE = "San Francisco City Hall"
ADDRESS = "1 Dr Carlton B Goodlett Pl, San Francisco, CA 94102"
GEO = (37.7793, -122.4193)


class CityHallOptions(AdapterOptions):
    url: str = Field(description="sf.gov City Hall location page")
    bucket: str = Field(description="Bucket every lighting night goes to")


def to_date(month: str, day: str, year: int) -> date:
    """Build a date from an English month name.

    Parameters:
        month (str): Month name, e.g. `"October"`.
        day (str): Day of month.
        year (int): Year.

    Returns:
        date: The date.

    Raises:
        ValueError: If the month name or day is invalid.
    """
    number = MONTHS.get(month.casefold())
    if number is None:
        raise ValueError(f"Unknown month: '{month}'")
    return date(year, number, int(day))


def parse_dates(match: re.Match[str]) -> list[date]:
    """Expand a `DATES` match into every day it covers.

    Parameters:
        match (re.Match[str]): Match of `DATES`.

    Returns:
        list[date]: Days in order; a range crossing New Year starts in the
        prior year.

    Raises:
        ValueError: If a month name or day is invalid.
    """
    year = int(match["year"])
    first = to_date(match["m1"], match["d1"], year)
    if match["d2"] is None:
        return [first]
    last_month = match["m2"] or match["m1"]
    last = to_date(last_month, match["d2"], year)
    if first > last:
        first = first.replace(year=year - 1)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def parse_entry(text: str) -> tuple[list[date], str, str] | None:
    """Parse one schedule paragraph.

    Parameters:
        text (str): Whitespace-normalized paragraph text.

    Returns:
        tuple[list[date], str, str] | None: Days, colors and honoree (possibly
        empty), or `None` when the text is not a schedule entry.

    Raises:
        ValueError: If the text starts with a date that does not exist.
    """
    match = DATES.match(text)
    if match is None:
        return None
    days = parse_dates(match)
    rest = text[match.end() :].strip().lstrip(DASH_CHARS)
    sep = SEPARATOR.search(rest)
    colors, honoree = (rest[: sep.start()], rest[sep.end() :]) if sep else (rest, "")
    colors = SLASH.sub("/", colors.strip(DASH_CHARS))
    honoree = HONOREE_LEAD.sub("", honoree).strip()
    if not colors:
        return None
    return days, colors, honoree


class CityHall(Adapter[CityHallOptions]):
    """sf.gov City Hall lighting schedule."""

    options_model = CityHallOptions
    always_full: ClassVar[bool] = True

    @override
    def referenced_buckets(self) -> set[str]:
        """The lighting bucket.

        Returns:
            set[str]: Bucket keys.
        """
        return {self.opts.bucket}

    @override
    def fetch(self, window: Window, prior: list[Event], meta: dict[str, Any]) -> SourceResult:
        """Fetch the page and emit one all-day event per lit night in the window.

        Parameters:
            window (Window): Range of nights to keep.
            prior (list[Event]): Unused; past nights carry over in the pipeline.
            meta (dict[str, Any]): Unused.

        Returns:
            SourceResult: Lighting nights.

        Raises:
            SourceError: If the page has no lighting schedule, e.g. a WAF
                challenge or a redesign.
            httpx.HTTPError: If the request fails.
        """
        html = self.get(self.opts.url).text
        events = self.parse(html)
        if not events and SCHEDULE_MARKER not in html.casefold():
            raise SourceError(f"No lighting schedule found on '{self.opts.url}'")
        kept = [e for e in events if window.contains(e)]
        LOGGER.info(f"`{self.name}`: {len(events)} lit nights, {len(kept)} in window")
        return SourceResult(events=kept)

    def parse(self, html: str) -> list[Event]:
        """Parse every schedule paragraph on the page.

        Parameters:
            html (str): Page HTML.

        Returns:
            list[Event]: One all-day event per day, first entry wins a day.
        """
        by_day: dict[date, Event] = {}
        for p in LexborHTMLParser(html).css("p"):
            if p.css_first("b") is None:
                continue
            text = " ".join(p.text(separator=" ").split())
            try:
                entry = parse_entry(text)
            except ValueError as e:
                LOGGER.warning(f"`{self.name}`: skipping '{text}': {e}")
                continue
            if entry is None:
                continue
            days, colors, honoree = entry
            for day in days:
                by_day.setdefault(day, self.event(day, colors, honoree))
        return [by_day[d] for d in sorted(by_day)]

    def event(self, day: date, colors: str, honoree: str) -> Event:
        """Build one lighting night.

        Parameters:
            day (date): Night.
            colors (str): Colors, e.g. `"red/white"`.
            honoree (str): What the lighting recognizes; may be empty.

        Returns:
            Event: All-day event.
        """
        start = datetime.combine(day, time(), tzinfo=TZ)
        return Event(
            source=self.name,
            source_id=day.isoformat(),
            title=f"City Hall: {colors} for {honoree}" if honoree else f"City Hall: {colors}",
            start=start,
            end=start + timedelta(days=1),
            all_day=True,
            bucket=self.opts.bucket,
            venue=VENUE,
            address=ADDRESS,
            locality="San Francisco",
            geo=GEO,
            url=self.opts.url,
            description=f"Lit {colors}" + (f" in recognition of {honoree}" if honoree else ""),
            is_free=True,
            categories=["City Hall lighting"],
        )
