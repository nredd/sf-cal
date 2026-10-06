"""Tests for the DoStuff (DoTheBay) adapter against captured Oct 10 pages."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from sfcal import TZ
from sfcal.cfg import SourceCfg, load_cfg
from sfcal.pipeline import make_adapters
from sfcal.sources.base import SourceError, Window
from sfcal.sources.dostuff import DoStuff
from tests.conftest import FIXTURES, make_cfg

DAY_URL = "https://dothebay.com/events/2026/10/10.json"
ONE_DAY = Window(
    start=datetime(2026, 10, 10, tzinfo=TZ), end=datetime(2026, 10, 11, tzinfo=TZ), full=False
)


def page(n: int, total_pages: int = 2) -> dict[str, Any]:
    """Load a captured page with its page count overridden.

    Parameters:
        n (int): Page number, 1 or 2.
        total_pages (int): Value for `paging.total_pages`.

    Returns:
        dict[str, Any]: Page JSON.
    """
    body = json.loads((FIXTURES / f"dostuff_p{n}.json").read_text())
    body["paging"]["total_pages"] = total_pages
    return body


def listing(**overrides: Any) -> dict[str, Any]:
    """The first captured listing with overrides.

    Parameters:
        **overrides (Any): Fields to replace.

    Returns:
        dict[str, Any]: Listing JSON.
    """
    return page(1)["events"][0] | overrides


@pytest.fixture
def adapter(client: httpx.Client) -> DoStuff:
    """Adapter configured exactly as in the repo `sfcal.toml`.

    Parameters:
        client (httpx.Client): HTTP client.

    Returns:
        DoStuff: Adapter.
    """
    cfg = load_cfg(FIXTURES.parent.parent / "sfcal.toml").sources["dothebay"]
    return DoStuff(
        "dothebay", cfg.model_copy(update={"options": cfg.options | {"min_interval": 0}}), client
    )


@respx.mock
def test_fetch_pages(adapter: DoStuff) -> None:
    route = respx.get(DAY_URL)
    route.side_effect = [httpx.Response(200, json=page(1)), httpx.Response(200, json=page(2))]
    result = adapter.fetch(ONE_DAY, [], {})
    assert route.call_count == 2
    assert [c.request.url.params["page"] for c in route.calls] == ["1", "2"]
    ids = [e.source_id for e in result.events]
    assert len(ids) == len(set(ids)) > 20


@respx.mock
def test_fetch_bad_payload(adapter: DoStuff) -> None:
    respx.get(DAY_URL).respond(json={"nope": []})
    with pytest.raises(SourceError, match="Unexpected DoStuff payload"):
        adapter.fetch(ONE_DAY, [], {})


@respx.mock
def test_fetch_caps_pages(adapter: DoStuff) -> None:
    adapter.opts.max_pages = 1
    route = respx.get(DAY_URL).respond(json=page(1, total_pages=6))
    adapter.fetch(ONE_DAY, [], {})
    assert route.call_count == 1


def test_convert_fields_and_timezone(adapter: DoStuff) -> None:
    raw = next(e for e in page(1)["events"] if e["id"] == 17079043)
    assert raw["begin_time"].endswith("-05:00")
    event = adapter.convert(raw, set())
    assert event is not None
    assert event.start == datetime.fromisoformat(raw["tz_adjusted_begin_date"])
    assert event.start.utcoffset() == timedelta(hours=-7)
    assert event.end is None
    assert event.bucket == "music"
    assert event.url == f"https://dothebay.com{raw['permalink']}"
    assert event.locality == "San Francisco"
    assert event.geo == (raw["venue"]["latitude"], raw["venue"]["longitude"])
    assert event.image_url == raw["imagery"]["aws"]["cover_image_w_1200_h_450"]
    assert "Listed time is doors" in event.notes
    assert event.ticket_url == raw["buy_url"]


def test_music_gate(adapter: DoStuff) -> None:
    assert adapter.convert(listing(category="Music", popularity=49.0), set()) is None
    assert adapter.convert(listing(category="Music", popularity=50.0), set()) is not None
    free = listing(category="Music", popularity=1.0, is_free=True)
    assert adapter.convert(free, set()) is not None
    assert adapter.convert(listing(category="Comedy", popularity=1.0), set()) is not None


def test_exhibits_dropped(adapter: DoStuff) -> None:
    assert adapter.convert(listing(is_ongoing=True), set()) is None
    long = listing(begin_date="2026-10-10", end_date="2026-10-14")
    assert adapter.convert(long, set()) is None
    weekend = listing(begin_date="2026-10-10", end_date="2026-10-12")
    assert adapter.convert(weekend, set()) is not None


def test_categories(adapter: DoStuff) -> None:
    assert adapter.convert(listing(category="Digital Events"), set()) is None
    stage = adapter.convert(listing(category=" Theatre & Performing Arts "), set())
    assert stage is not None
    assert stage.bucket == "stage"
    unknown: set[str] = set()
    odd = adapter.convert(listing(category="Brand New"), unknown)
    assert odd is not None
    assert odd.bucket == "arts-community"
    assert unknown == {"Brand New"}


def test_venue_whitespace_and_missing(adapter: DoStuff) -> None:
    raw = listing()
    raw["venue"] = raw["venue"] | {"city": "San Francisco ", "latitude": None}
    event = adapter.convert(raw, set())
    assert event is not None
    assert event.locality == "San Francisco"
    assert event.geo is None
    bare = adapter.convert(listing(venue=None, imagery=None, description=None), set())
    assert bare is not None
    assert (bare.venue, bare.locality, bare.image_url, bare.description) == (None,) * 4


def test_malformed_listing_skipped(adapter: DoStuff) -> None:
    assert adapter.convert({"id": "x"}, set()) is None


def test_description_truncated(adapter: DoStuff) -> None:
    event = adapter.convert(listing(description="<p>" + "word " * 1000 + "</p>"), set())
    assert event is not None
    assert event.description is not None
    assert len(event.description) <= 1503
    assert event.description.endswith("...")


def test_rejects_unknown_bucket(client: httpx.Client) -> None:
    cfg = make_cfg(
        sources={
            "d": SourceCfg(
                adapter="dostuff",
                label="D",
                priority=1,
                options={"categories": {"Music": "nope"}, "default_bucket": "music"},
            ).model_dump()
        }
    )
    with pytest.raises(ValueError, match="Unknown bucket 'nope'"):
        make_adapters(cfg, client)
