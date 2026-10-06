"""Tests for the WordPress + JSON-LD adapter against a captured Funcheap post."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
import pytest
import respx

from sfcal import TZ
from sfcal.cfg import load_cfg
from sfcal.models import Event
from sfcal.sources.base import SourceError, Window
from sfcal.sources.jsonld import JsonLd, Post, find_events, parse_when
from tests.conftest import FIXTURES, fixture_text

API = "https://sf.funcheap.com/wp-json/wp/v2/posts"
LINK = "https://sf.funcheap.com/free-boat-rides-fleet-week-waterfront-fun-india-basin-park-sf/"
WINDOW = Window(
    start=datetime(2026, 10, 5, tzinfo=TZ), end=datetime(2026, 10, 21, tzinfo=TZ), full=False
)


def post(pid: int = 1640001, modified: str = "2026-10-01T16:31:17", **kw: Any) -> dict[str, Any]:
    """A discovery API item.

    Parameters:
        pid (int): Post id.
        modified (str): `modified_gmt`.
        **kw (Any): Overrides.

    Returns:
        dict[str, Any]: Post JSON.
    """
    classes = ["post", "category-outdoors", "category-live-music-event", "category-top-pick"]
    return {"id": pid, "link": LINK, "modified_gmt": modified, "class_list": classes} | kw


def page_with(*events: dict[str, Any]) -> str:
    """A minimal post page carrying the given JSON-LD events.

    Parameters:
        *events (dict[str, Any]): JSON-LD Event objects.

    Returns:
        str: HTML.
    """
    blocks = "".join(
        f'<script type="application/ld+json">{json.dumps(e)}</script>' for e in events
    )
    return f"<html><head>{blocks}</head><body></body></html>"


@pytest.fixture
def adapter(client: httpx.Client) -> JsonLd:
    """Adapter configured as in the repo `sfcal.toml`.

    Parameters:
        client (httpx.Client): HTTP client.

    Returns:
        JsonLd: Adapter.
    """
    cfg = load_cfg(FIXTURES.parent.parent / "sfcal.toml").sources["funcheap"]
    opts = cfg.options | {"min_interval": 0, "concurrency": 2}
    return JsonLd("funcheap", cfg.model_copy(update={"options": opts}), client)


def test_parse_when() -> None:
    assert parse_when("2026-10-10") == (datetime(2026, 10, 10, tzinfo=TZ), True)
    start, all_day = parse_when("2026-10-10T11:00:00-07:00")
    assert (start.hour, all_day) == (11, False)
    assert parse_when("2026-10-10T11:00:00")[0].tzinfo == TZ
    with pytest.raises(ValueError, match="Invalid isoformat"):
        parse_when("soon")


def test_post_from_captured_discovery() -> None:
    posts = [Post.model_validate(p) for p in json.loads(fixture_text("funcheap_posts.json"))]
    assert len(posts) == 5
    assert posts[0].slugs == ["dance", "live-music-event"]


def test_find_events() -> None:
    events = find_events(fixture_text("funcheap_event.html"))
    assert [e["@type"] for e in events] == ["Event"]
    assert find_events('<script type="application/ld+json">{nope</script>') == []
    graph = page_with({"@graph": [{"@type": "Event", "name": "x"}, {"@type": "WebPage"}]})
    assert len(find_events(graph)) == 1


def test_parse_post_fixture(adapter: JsonLd) -> None:
    (event,) = adapter.parse_post(Post(**post()), fixture_text("funcheap_event.html"), set())
    assert event.title == "Free Boat Rides + Fleet Week Waterfront Fun at India Basin Park"
    assert event.start == datetime(2026, 10, 10, 11, tzinfo=TZ)
    assert event.end == datetime(2026, 10, 10, 15, tzinfo=TZ)
    assert event.venue == "India Basin Waterfront Park"
    assert event.address == "900 Innes Ave, San Francisco, CA 94124"
    assert event.locality == "San Francisco"
    assert event.is_free
    assert event.price_text is None
    assert event.ticket_url is not None
    assert event.ticket_url.startswith("https://www.eventbrite.com/")
    assert event.bucket == "music"
    assert event.categories == ["Live Music Event"]
    assert (
        event.image_url
        == "https://cdn.funcheap.com/wp-content/uploads/2026/09/Fleet-Week-Family-Day.png"
    )
    assert event.description is not None
    assert "kids\u2019 swag" in event.description
    assert "Disclaimer" not in event.description


def test_parse_post_variants(adapter: JsonLd) -> None:
    overnight = {
        "@type": "Event",
        "name": "Late &amp; Loud (SF)",
        "startDate": "2026-10-09T21:00:00-07:00",
        "endDate": "2026-10-09T01:00:00-07:00",
        "location": [
            {
                "name": "Club",
                "address": {"streetAddress": "1 Main St", "addressLocality": "Oakland"},
                "geo": {"latitude": "37.8", "longitude": "-122.27"},
            }
        ],
        "offers": [{"price": "15", "url": "https://t"}],
        "image": {"url": "https://img/a-80x80.jpg"},
    }
    allday = {"@type": "Event", "name": "Fair", "startDate": "2026-10-10", "endDate": "2026-10-11"}
    broken = {"@type": "Event", "name": "No start"}
    page = page_with(overnight, allday, broken)
    unknown: set[str] = set()
    events = adapter.parse_post(Post(**post(class_list=["category-other"])), page, unknown)

    assert [e.source_id for e in events] == ["1640001-0", "1640001-1"]
    late, fair = events
    assert late.title == "Late & Loud"
    assert late.end == datetime(2026, 10, 10, 1, tzinfo=TZ)
    assert (late.address, late.locality, late.geo) == (
        "1 Main St, Oakland",
        "Oakland",
        (37.8, -122.27),
    )
    assert (late.price_text, late.is_free, late.ticket_url) == ("$15.00", False, "https://t")
    assert late.image_url == "https://img/a.jpg"
    assert late.bucket == "arts-community"
    assert unknown == {"1640001"}
    assert fair.all_day
    assert fair.end == datetime(2026, 10, 12, tzinfo=TZ)


def test_parse_post_unfixable_end(adapter: JsonLd) -> None:
    item = {
        "@type": "Event",
        "name": "Odd",
        "startDate": "2026-10-09T21:00:00-07:00",
        "endDate": "2026-10-07T20:00:00-07:00",
    }
    (event,) = adapter.parse_post(Post(**post()), page_with(item), set())
    assert event.end is None


@respx.mock
def test_fetch_incremental(adapter: JsonLd) -> None:
    api = respx.get(API).respond(json=[post()], headers={"X-WP-TotalPages": "1"})
    page = respx.get(LINK).respond(text=fixture_text("funcheap_event.html"))

    first = adapter.fetch(WINDOW, [], {})
    assert [e.source_id for e in first.events] == ["1640001"]
    assert first.meta == {"posts": {"1640001": "2026-10-01T16:31:17"}}
    assert api.calls.last.request.url.params["per_page"] == "100"

    second = adapter.fetch(WINDOW, first.events, first.meta)
    assert page.call_count == 1
    assert second.events == first.events

    api.respond(json=[post(modified="2026-10-02T00:00:00")], headers={"X-WP-TotalPages": "1"})
    third = adapter.fetch(WINDOW, first.events, first.meta)
    assert page.call_count == 2
    assert third.meta == {"posts": {"1640001": "2026-10-02T00:00:00"}}


@respx.mock
def test_fetch_skips_and_prunes(adapter: JsonLd) -> None:
    skipped = post(pid=2, class_list=["category-east-bay"])
    respx.get(API).respond(json=[skipped], headers={"X-WP-TotalPages": "1"})
    old = Event(
        source="funcheap",
        source_id="99",
        title="Old post",
        start=datetime(2026, 10, 30, tzinfo=TZ),
        bucket="music",
    )
    result = adapter.fetch(WINDOW, [old], {"posts": {"99": "x"}})
    assert result.events == [old]
    assert result.meta == {"posts": {}}


@respx.mock
def test_fetch_all_pages_fail(adapter: JsonLd) -> None:
    respx.get(API).respond(json=[post()], headers={"X-WP-TotalPages": "1"})
    respx.get(LINK).respond(status_code=503)
    with pytest.raises(SourceError, match="All 1 post fetches failed"):
        adapter.fetch(WINDOW, [], {})


@respx.mock
def test_fetch_partial_failure_retries_next_run(adapter: JsonLd) -> None:
    other = "https://sf.funcheap.com/other/"
    respx.get(API).respond(
        json=[post(), post(pid=2, link=other)], headers={"X-WP-TotalPages": "1"}
    )
    respx.get(LINK).respond(text=fixture_text("funcheap_event.html"))
    respx.get(other).respond(status_code=500)
    result = adapter.fetch(WINDOW, [], {})
    assert list(result.meta["posts"]) == ["1640001"]


@respx.mock
def test_discover_pages_and_bad_payload(adapter: JsonLd) -> None:
    route = respx.get(API)
    route.side_effect = [
        httpx.Response(200, json=[post()], headers={"X-WP-TotalPages": "2"}),
        httpx.Response(200, json=[post(pid=2)], headers={"X-WP-TotalPages": "2"}),
    ]
    assert [p.id for p in adapter.discover()] == [1640001, 2]
    route.side_effect = None
    route.respond(json={"code": "rest_error"})
    with pytest.raises(SourceError, match="Unexpected WordPress payload"):
        adapter.discover()
