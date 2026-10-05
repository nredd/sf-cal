"""Normalized event model shared by every source adapter.

References:
- schema.org Event: https://schema.org/Event
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sfcal import TZ

LOGGER = logging.getLogger(__name__)


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="Config key of the source that produced the event")
    source_id: str = Field(description="Stable identifier of the event within its source")
    title: str = Field(min_length=1, description="Event title, whitespace-normalized")
    start: datetime = Field(description="Aware start; local midnight for all-day events")
    end: datetime | None = Field(
        default=None, description="Aware end; exclusive local midnight for all-day events"
    )
    all_day: bool = Field(default=False, description="Emit DTSTART/DTEND as DATE values")
    bucket: str = Field(description="Category bucket assigned by the adapter")
    venue: str | None = Field(default=None, description="Venue display name")
    address: str | None = Field(default=None, description="Street address of the venue")
    locality: str | None = Field(default=None, description="City the venue is in")
    geo: tuple[float, float] | None = Field(default=None, description="(latitude, longitude)")
    url: str | None = Field(default=None, description="Event details page")
    ticket_url: str | None = Field(default=None, description="Ticket purchase page")
    price_text: str | None = Field(default=None, description="Human-readable price")
    is_free: bool = Field(default=False, description="Event is free to attend")
    sold_out: bool = Field(default=False, description="Event is sold out")
    description: str | None = Field(default=None, description="Plain-text event body")
    image_url: str | None = Field(default=None, description="Cover image")
    categories: list[str] = Field(default_factory=list, description="Source category labels")
    notes: list[str] = Field(
        default_factory=list, description="Extra lines for the description, e.g. ticket info"
    )

    @field_validator("title")
    @classmethod
    def _collapse_title(cls, value: str) -> str:
        """Collapse runs of whitespace in the title.

        Parameters:
            value (str): Raw title.

        Returns:
            str: Title with single spaces and no surrounding whitespace.
        """
        return " ".join(value.split())

    @field_validator("start", "end")
    @classmethod
    def _localize(cls, value: datetime | None) -> datetime | None:
        """Require aware datetimes and convert them to the calendar timezone.

        Parameters:
            value (datetime | None): Start or end.

        Returns:
            datetime | None: The same instant in `TZ`.

        Raises:
            ValueError: If `value` is naive.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"Naive datetime not allowed: '{value.isoformat()}'")
        return value.astimezone(TZ)

    @model_validator(mode="after")
    def _check_order(self) -> Event:
        """Reject events that end before they start.

        Returns:
            Event: The validated event.

        Raises:
            ValueError: If `end` precedes `start`.
        """
        if self.end is not None and self.end < self.start:
            raise ValueError(
                f"Event '{self.uid}' ends before it starts: "
                f"'{self.start.isoformat()}' > '{self.end.isoformat()}'"
            )
        return self

    @property
    def uid(self) -> str:
        """Globally unique, stable iCalendar UID.

        Returns:
            str: `<source>-<source_id>@sf-cal`.
        """
        return f"{self.source}-{self.source_id}@sf-cal"

    @property
    def location(self) -> str | None:
        """Venue and address joined for the LOCATION property.

        Without a street address the locality stands in, so clients that
        geocode LOCATION (Google) resolve `"Pier 27"` to the one in SF.

        Returns:
            str | None: `"Venue, address"` or `"Venue, locality"`, whichever parts
            exist, or `None`.
        """
        parts = [p for p in (self.venue, self.address or self.locality) if p]
        if len(parts) == 2 and parts[1].casefold().startswith(parts[0].casefold()):
            parts = parts[1:]
        return ", ".join(parts) or None


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_hash: str = Field(description="Hash of the rendered event content")
    sequence: int = Field(ge=0, description="iCalendar SEQUENCE, bumped on content change")
    last_modified: datetime = Field(description="When the content last changed, UTC")


def content_hash(payload: dict[str, object]) -> str:
    """Hash a rendered event payload deterministically.

    Parameters:
        payload (dict[str, object]): JSON-serializable rendered fields.

    Returns:
        str: Hex SHA-256 of the canonical JSON encoding.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()
