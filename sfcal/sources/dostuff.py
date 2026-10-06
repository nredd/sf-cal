"""DoStuff Media city guides (DoTheBay), via the site's own JSON endpoint.

`/events/YYYY/M/D.json?page=N` is what the site's listing pages fetch. It is
undocumented, so every field is optional and each event validates
independently: one malformed listing is skipped, not the whole source.

`begin_time` carries a wrong UTC offset (`-05:00` for Pacific venues), so the
start comes from `tz_adjusted_begin_date`. `tz_adjusted_end_date` is a
synthesized default, not a real end, and is ignored.

References:
- DoTheBay: https://dothebay.com/events
- robots.txt: https://dothebay.com/robots.txt
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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

DESCRIPTION_LIMIT = 1500
COVER_IMAGE = "cover_image_w_1200_h_450"


class DoStuffOptions(AdapterOptions):
    base_url: str = Field(default="https://dothebay.com", description="City guide root")
    min_interval: float = Field(default=0.5, ge=0, description="Seconds between requests")
    max_pages: int = Field(default=20, gt=0, description="Safety cap on pages per day")
    max_span_days: int = Field(default=3, ge=0, description="Longer events are exhibits")
    categories: dict[str, str] = Field(description="Category label to bucket")
    drop_categories: list[str] = Field(default_factory=list, description="Labels to skip")
    default_bucket: str = Field(default="arts-community", description="For unknown labels")
    popularity_gates: dict[str, float] = Field(
        default_factory=dict,
        description="Category label to minimum `popularity`; free events bypass the gate",
    )


class DoStuffVenue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    full_address: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class DoStuffImagery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    aws: dict[str, str] = Field(default_factory=dict)


class DoStuffEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    permalink: str
    title: str
    category: str = ""
    tz_adjusted_begin_date: datetime
    begin_date: date | None = None
    end_date: date | None = None
    presented_by: str | None = None
    description: str | None = None
    ticket_info: str | None = None
    buy_url: str | None = None
    popularity: float | None = None
    is_free: bool = False
    is_ongoing: bool = False
    sold_out: bool = False
    doors: bool = False
    imagery: DoStuffImagery | None = None
    venue: DoStuffVenue | None = None


class DoStuff(Adapter[DoStuffOptions]):
    """Day-by-day listings from a DoStuff city guide."""

    options_model = DoStuffOptions

    @override
    def referenced_buckets(self) -> set[str]:
        """Every bucket the category map and fallback can assign.

        Returns:
            set[str]: Bucket keys.
        """
        return {*self.opts.categories.values(), self.opts.default_bucket}

    @override
    def fetch(self, window: Window, prior: list[Event], meta: dict[str, Any]) -> SourceResult:
        """Walk every day page in the window.

        Parameters:
            window (Window): Days to fetch.
            prior (list[Event]): Unused; the endpoint is cheap to re-read.
            meta (dict[str, Any]): Unused.

        Returns:
            SourceResult: Kept events, deduplicated by DoStuff id.

        Raises:
            SourceError: If a page is not the expected JSON shape.
            httpx.HTTPError: If a request fails.
        """
        events: dict[str, Event] = {}
        unknown: set[str] = set()
        for day in window.days():
            for raw in self._day(day.date()):
                event = self.convert(raw, unknown)
                if event is not None:
                    events.setdefault(event.source_id, event)
        if unknown:
            LOGGER.warning(
                f"`{self.name}`: unmapped categories sent to "
                f"'{self.opts.default_bucket}': {sorted(unknown)}"
            )
        LOGGER.info(f"`{self.name}`: kept {len(events)} events over {len(window.days())} days")
        return SourceResult(events=list(events.values()))

    def _day(self, day: date) -> list[dict[str, Any]]:
        """Fetch every page of one day.

        Parameters:
            day (date): Local date.

        Returns:
            list[dict[str, Any]]: Raw event dicts.

        Raises:
            SourceError: If a page is not the expected JSON shape.
        """
        url = f"{self.opts.base_url}/events/{day.year}/{day.month}/{day.day}.json"
        raw: list[dict[str, Any]] = []
        page, total = 1, 1
        while page <= min(total, self.opts.max_pages):
            resp = self.get(url, params={"page": page}, min_interval=self.opts.min_interval)
            try:
                body = resp.json()
                raw.extend(body["events"])
                total = int(body["paging"]["total_pages"])
            except (ValueError, KeyError, TypeError) as e:
                raise SourceError(f"Unexpected DoStuff payload for '{resp.url}': {e}") from e
            page += 1
        return raw

    def convert(self, raw: dict[str, Any], unknown: set[str]) -> Event | None:
        """Convert one listing, applying category, gate and exhibit rules.

        Parameters:
            raw (dict[str, Any]): Listing JSON.
            unknown (set[str]): Collects unmapped category labels.

        Returns:
            Event | None: The event, or `None` when it is filtered out or malformed.
        """
        opts = self.opts
        try:
            item = DoStuffEvent.model_validate(raw)
        except ValidationError as e:
            LOGGER.warning(f"`{self.name}`: skipping malformed listing '{raw.get('id')}': {e}")
            return None

        label = item.category.strip()
        span = (item.end_date - item.begin_date).days if item.begin_date and item.end_date else 0
        if label in opts.drop_categories or item.is_ongoing or span > opts.max_span_days:
            return None
        gate = opts.popularity_gates.get(label)
        if gate is not None and not item.is_free and (item.popularity or 0) < gate:
            return None
        bucket = opts.categories.get(label)
        if bucket is None:
            unknown.add(label)
            bucket = opts.default_bucket

        venue = item.venue or DoStuffVenue()
        geo = (
            (venue.latitude, venue.longitude)
            if venue.latitude is not None and venue.longitude is not None
            else None
        )
        body = html_to_text(item.description)
        if body and len(body) > DESCRIPTION_LIMIT:
            body = body[:DESCRIPTION_LIMIT].rstrip() + "..."
        presented = (item.presented_by or "").strip()
        cover = item.imagery.aws.get(COVER_IMAGE) if item.imagery else None
        return Event(
            source=self.name,
            source_id=str(item.id),
            title=item.title,
            start=item.tz_adjusted_begin_date,
            bucket=bucket,
            venue=(venue.title or "").strip() or None,
            address=(venue.full_address or "").strip() or None,
            locality=(venue.city or "").strip() or None,
            geo=geo,
            url=f"{opts.base_url}{item.permalink}",
            ticket_url=item.buy_url or None,
            price_text=(item.ticket_info or "").strip() or None,
            is_free=item.is_free,
            sold_out=item.sold_out,
            description=body,
            image_url=cover or None,
            categories=[label] if label else [],
            notes=[
                *([presented] if presented else []),
                *(["Listed time is doors"] if item.doors else []),
            ],
        )
