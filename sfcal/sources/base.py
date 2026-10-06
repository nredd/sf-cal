"""Adapter interface every event source implements."""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
from selectolax.lexbor import LexborHTMLParser

from sfcal.cfg import SourceCfg
from sfcal.models import Event

LOGGER = logging.getLogger(__name__)

BLOCK_TAGS = re.compile(r"<\s*(?:br|/p|/div|/h[1-6]|/li)\s*/?>", re.IGNORECASE)
BLANK_RUNS = re.compile(r"\n\s*\n\s*(?:\n\s*)+")
LOCALITY = re.compile(r"(?:^|,)\s*([A-Za-z][A-Za-z .'-]+?),\s*(?:CA|California)\b")


class SourceError(Exception):
    """A source returned something the adapter cannot use."""


class AdapterOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Window:
    """Half-open range of event start times a run refreshes."""

    start: datetime
    end: datetime
    full: bool

    def contains(self, event: Event) -> bool:
        """Report whether `event` starts inside the window.

        Parameters:
            event (Event): Event to test.

        Returns:
            bool: `start <= event.start < end`.
        """
        return self.start <= event.start < self.end

    def days(self) -> list[datetime]:
        """List the local midnights covered by the window.

        Returns:
            list[datetime]: One aware datetime per day, in order.
        """
        count = (self.end.date() - self.start.date()).days
        return [self.start + timedelta(days=i) for i in range(count)]


@dataclass
class SourceResult:
    """What an adapter returns: events plus its updated cache."""

    events: list[Event]
    meta: dict[str, Any] = field(default_factory=dict)


class Adapter[OptionsT: AdapterOptions](ABC):
    """Base class for event sources.

    Subclasses set `options_model` to their option model and implement `fetch`.
    """

    options_model: type[OptionsT]
    always_full: ClassVar[bool] = False

    def __init__(self, name: str, cfg: SourceCfg, client: httpx.Client) -> None:
        """Bind the adapter to its config and HTTP client.

        Parameters:
            name (str): Source name, used in UIDs.
            cfg (SourceCfg): Source config.
            client (httpx.Client): Shared HTTP client.

        Raises:
            ValueError: If `cfg.options` does not validate against `options_model`.
        """
        self.name = name
        self.cfg = cfg
        self.client = client
        try:
            self.opts: OptionsT = self.options_model.model_validate(cfg.options)
        except ValidationError as e:
            raise ValueError(f"Invalid options for source `{name}`: {e}") from e
        self._last_request = 0.0

    @abstractmethod
    def fetch(self, window: Window, prior: list[Event], meta: dict[str, Any]) -> SourceResult:
        """Fetch events.

        Parameters:
            window (Window): Start-time range to refresh.
            prior (list[Event]): This source's events from the previous run.
            meta (dict[str, Any]): This source's cache from the previous run.

        Returns:
            SourceResult: Fresh events and the updated cache.

        Raises:
            SourceError: If the source cannot be parsed.
            httpx.HTTPError: If a request fails.
        """

    def referenced_buckets(self) -> set[str]:
        """Buckets this adapter's options can assign, checked against `sfcal.toml`.

        Returns:
            set[str]: Bucket keys; empty when the adapter has none of its own.
        """
        return set()

    def get(self, url: str, *, min_interval: float = 0.0, **kwargs: Any) -> httpx.Response:
        """GET `url`, pacing requests and raising on HTTP errors.

        Parameters:
            url (str): URL to fetch.
            min_interval (float): Minimum seconds between this adapter's requests.
            **kwargs (Any): Passed through to `httpx.Client.get`.

        Returns:
            httpx.Response: The successful response.

        Raises:
            httpx.HTTPStatusError: On a non-2xx response.
            httpx.TransportError: On a network failure.
        """
        wait = self._last_request + min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        resp = self.client.get(url, **kwargs)
        resp.raise_for_status()
        return resp


def html_to_text(fragment: str | None) -> str | None:
    """Flatten an HTML fragment to readable plain text.

    Parameters:
        fragment (str | None): HTML, or plain text with entities.

    Returns:
        str | None: Text with block breaks as newlines, or `None` when empty.
    """
    if not fragment:
        return None
    marked = BLOCK_TAGS.sub("\n", fragment)
    text = LexborHTMLParser(f"<body>{marked}</body>").text(separator="")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    joined = BLANK_RUNS.sub("\n\n", "\n".join(lines)).strip()
    return joined or None


def locality_from_address(address: str | None) -> str | None:
    """Pull the city out of a `"street, City, CA zip"` address.

    Parameters:
        address (str | None): Free-form address.

    Returns:
        str | None: The city, or `None` when it cannot be found.
    """
    if not address:
        return None
    match = LOCALITY.search(address)
    return match.group(1).strip() if match else None
