"""Permitted special events, from SFMTA's temporary street closures on DataSF.

Every block party, street fair, night market, Sunday Streets and farmers
market that closes a street gets an SFMTA permit, and DataSF publishes one row
per closed street segment. Rows are grouped back into one event per permit
occurrence (case and start), ending with the last segment to reopen.

`start_dt` / `end_dt` are floating local times, so the query bounds are too.

References:
- Dataset: https://data.sf.gov/d/8x25-yybr
- SODA queries: https://dev.socrata.com/docs/queries/
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta
from functools import cached_property
from typing import Any, ClassVar, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sfcal import TZ
from sfcal.models import Event
from sfcal.sources.base import Adapter, AdapterOptions, SourceError, SourceResult, Window

LOGGER = logging.getLogger(__name__)

DETAILS_URL = "https://data.sf.gov/d/8x25-yybr"
SODA_TIME = "%Y-%m-%dT%H:%M:%S"
LOWERCASE_WORDS = frozenset({"between", "and", "of", "from", "to"})
MAX_BLOCKS = 10
NOTES = [
    "Times are SFMTA street-closure permit times and include setup and teardown",
    "Source: DataSF Temporary Street Closures",
]


class StreetClosuresOptions(AdapterOptions):
    url: str = Field(description="SODA resource endpoint of the closures dataset")
    bucket: str = Field(description="Bucket every event goes to")
    types: list[str] = Field(default=["Special Event"], min_length=1, description="`type` values")
    statuses: list[str] = Field(default=["Permitted"], min_length=1, description="`status` values")
    max_span_hours: float = Field(
        default=24, gt=0, description="Longer closures are umbrellas and skipped"
    )
    skip_pattern: str | None = Field(
        default=None, description="Case-insensitive regex; matching `case_name`s are skipped"
    )
    limit: int = Field(default=50000, gt=0, description="SODA `$limit`; hitting it is an error")

    @cached_property
    def skip_regex(self) -> re.Pattern[str] | None:
        """Compiled `skip_pattern`.

        Returns:
            re.Pattern[str] | None: The pattern, or `None` when unset.
        """
        return re.compile(self.skip_pattern, re.IGNORECASE) if self.skip_pattern else None


class Shape(BaseModel):
    model_config = ConfigDict(extra="ignore")

    coordinates: list[list[float]] = Field(description="LineString points as `[lon, lat]`")


class ClosureRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_num: str = Field(description="Permit case number")
    case_name: str = Field(min_length=1, description="Event name on the permit")
    type: str = Field(description="Closure type, e.g. `Special Event`")
    status: str = Field(description="Permit status, e.g. `Permitted`")
    start_dt: datetime = Field(description="Floating local start")
    end_dt: datetime = Field(description="Floating local end")
    loc_desc: str | None = Field(default=None, description="Closure extent, upper case")
    street: str | None = Field(default=None, description="Closed segment's street")
    from_st: str | None = Field(default=None, description="Segment start cross street")
    to_st: str | None = Field(default=None, description="Segment end cross street")
    shape: Shape | None = Field(default=None, description="Segment geometry")


def quote(value: str) -> str:
    """Quote a SoQL string literal.

    Parameters:
        value (str): Raw value.

    Returns:
        str: `'value'` with embedded quotes doubled.
    """
    return "'" + value.replace("'", "''") + "'"


def street_case(text: str) -> str:
    """Title-case an upper-case street description, keeping joiners lowercase.

    Parameters:
        text (str): E.g. `"MISSION ST between THERESA ST and GENEVA AVE"`.

    Returns:
        str: E.g. `"Mission St between Theresa St and Geneva Ave"`.
    """
    words = text.split()
    return " ".join(
        w.lower() if i and w.lower() in LOWERCASE_WORDS else w.capitalize()
        for i, w in enumerate(words)
    )


def midpoint(shape: Shape | None) -> tuple[float, float] | None:
    """Midpoint of a segment's endpoints.

    Parameters:
        shape (Shape | None): LineString in `[lon, lat]` order.

    Returns:
        tuple[float, float] | None: `(latitude, longitude)`, or `None` without
        geometry.
    """
    if shape is None or not shape.coordinates:
        return None
    (lon1, lat1), (lon2, lat2) = shape.coordinates[0][:2], shape.coordinates[-1][:2]
    return round((lat1 + lat2) / 2, 6), round((lon1 + lon2) / 2, 6)


class StreetClosures(Adapter[StreetClosuresOptions]):
    """DataSF temporary street closures, special events only."""

    options_model = StreetClosuresOptions
    always_full: ClassVar[bool] = True

    @override
    def referenced_buckets(self) -> set[str]:
        """The street-events bucket.

        Returns:
            set[str]: Bucket keys.
        """
        return {self.opts.bucket}

    def params(self, window: Window) -> dict[str, str]:
        """SODA query parameters for the window.

        Parameters:
            window (Window): Range of start times.

        Returns:
            dict[str, str]: `$where`, `$order` and `$limit`.
        """
        opts = self.opts
        lo, hi = (t.astimezone(TZ).strftime(SODA_TIME) for t in (window.start, window.end))
        where = " AND ".join(
            [
                f"type in ({', '.join(map(quote, opts.types))})",
                f"status in ({', '.join(map(quote, opts.statuses))})",
                f"start_dt >= {quote(lo)}",
                f"start_dt < {quote(hi)}",
            ]
        )
        return {"$where": where, "$order": "start_dt,objectid", "$limit": str(opts.limit)}

    @override
    def fetch(self, window: Window, prior: list[Event], meta: dict[str, Any]) -> SourceResult:
        """Query closures starting in the window and group them into events.

        Parameters:
            window (Window): Range of start times.
            prior (list[Event]): Unused.
            meta (dict[str, Any]): Unused.

        Returns:
            SourceResult: One event per permit occurrence.

        Raises:
            SourceError: If the body is not a JSON array, or the row limit was hit.
            httpx.HTTPError: If the request fails.
        """
        resp = self.get(self.opts.url, params=self.params(window))
        try:
            raw = resp.json()
        except ValueError as e:
            raise SourceError(f"Not JSON from '{self.opts.url}': {e}") from e
        if not isinstance(raw, list):
            raise SourceError(f"Expected a JSON array from '{self.opts.url}', got: '{raw}'")
        if len(raw) >= self.opts.limit:
            raise SourceError(f"Hit the '{self.opts.limit}' row limit, results are truncated")

        # NOTE(redd): segments of one occurrence can end at different times; the
        # event ends with the last one.
        groups: dict[tuple[str, datetime], list[ClosureRow]] = {}
        for item in raw:
            try:
                row = ClosureRow.model_validate(item)
            except ValidationError as e:
                LOGGER.warning(f"`{self.name}`: skipping malformed row: {e}")
                continue
            if row.type in self.opts.types and row.status in self.opts.statuses:
                groups.setdefault((row.case_num, row.start_dt), []).append(row)

        events = [e for rows in groups.values() if (e := self.convert(rows)) is not None]
        LOGGER.info(f"`{self.name}`: {len(raw)} segments, {len(events)} events")
        return SourceResult(events=events)

    def convert(self, rows: list[ClosureRow]) -> Event | None:
        """Turn one permit occurrence's segments into an event.

        Parameters:
            rows (list[ClosureRow]): Segments sharing case and start.

        Returns:
            Event | None: The event, or `None` when it is an umbrella or skipped.
        """
        first = rows[0]
        end = max(r.end_dt for r in rows)
        if end - first.start_dt > timedelta(hours=self.opts.max_span_hours):
            return None
        regex = self.opts.skip_regex
        if regex is not None and regex.search(first.case_name):
            return None
        start = first.start_dt.replace(tzinfo=TZ)
        extents = Counter(r.loc_desc for r in rows if r.loc_desc)
        venue = street_case(extents.most_common(1)[0][0]) if extents else None
        blocks = list(
            dict.fromkeys(
                street_case(f"{r.street} from {r.from_st} to {r.to_st}")
                for r in rows
                if r.street and r.from_st and r.to_st
            )
        )
        more = len(blocks) - MAX_BLOCKS
        description = (
            "Closed: "
            + "; ".join(blocks[:MAX_BLOCKS])
            + (f"; and {more} more" if more > 0 else "")
            if blocks
            else None
        )
        return Event(
            source=self.name,
            source_id=f"{first.case_num}-{start.isoformat()}",
            title=first.case_name,
            start=start,
            end=end.replace(tzinfo=TZ),
            bucket=self.opts.bucket,
            venue=venue,
            locality="San Francisco",
            geo=midpoint(first.shape),
            url=DETAILS_URL,
            description=description,
            categories=[first.type],
            notes=[f"Permit status: {first.status}", *NOTES],
        )
