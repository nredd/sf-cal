"""Shared fixtures and factories."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from sfcal import TZ, USER_AGENT
from sfcal.cfg import Cfg
from sfcal.models import Event

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    """Read a captured fixture.

    Parameters:
        name (str): File name under `tests/fixtures`.

    Returns:
        str: File contents.
    """
    return (FIXTURES / name).read_text()


def make_event(**overrides: Any) -> Event:
    """Build an event with sensible defaults.

    Parameters:
        **overrides (Any): Field overrides.

    Returns:
        Event: The event.
    """
    fields: dict[str, Any] = {
        "source": "alpha",
        "source_id": "1",
        "title": "Some Show",
        "start": datetime(2026, 10, 10, 20, tzinfo=TZ),
        "bucket": "music",
        "locality": "San Francisco",
    }
    return Event.model_validate(fields | overrides)


def make_cfg(**overrides: Any) -> Cfg:
    """Build a small config with two sources and three buckets.

    Parameters:
        **overrides (Any): Top-level field overrides.

    Returns:
        Cfg: The config.
    """
    raw: dict[str, Any] = {
        "buckets": {
            "fleet-week": {"name": "Fleet Week", "description": "fw"},
            "music": {"name": "Music", "description": "m"},
            "nightlife": {"name": "Nightlife", "description": "n"},
        },
        "sources": {
            "alpha": {"adapter": "fake", "label": "Alpha", "priority": 80},
            "beta": {"adapter": "fake", "label": "Beta", "priority": 40},
        },
        "bucket_rules": [
            {"bucket": "fleet-week", "pattern": r"\b(fleet week|blue angels|parade of ships)\b"}
        ],
    }
    return Cfg.model_validate(raw | overrides)


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    """HTTP client matching production settings, for use under `respx`.

    Yields:
        httpx.Client: Client.
    """
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as c:
        yield c
