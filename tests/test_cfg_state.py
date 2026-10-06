"""Tests for config loading, the event model and state persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from sfcal import TZ
from sfcal.cfg import load_cfg
from sfcal.models import Revision
from sfcal.sources.base import html_to_text, locality_from_address
from sfcal.state import State, load_state, save_state
from tests.conftest import make_cfg, make_event

REPO_CFG = Path(__file__).parent.parent / "sfcal.toml"


def test_repo_cfg_loads() -> None:
    cfg = load_cfg(REPO_CFG)
    # Feed URLs come from these keys: new buckets append, existing ones never move.
    assert list(cfg.buckets)[:9] == [
        "fleet-week",
        "music",
        "comedy",
        "stage",
        "film",
        "nightlife",
        "food-drink",
        "outdoors",
        "arts-community",
    ]
    assert cfg.bucket_rules[0].regex.search("watch the BLUE ANGELS")


def test_load_cfg_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_cfg(tmp_path / "nope.toml")


def test_load_cfg_bad_toml(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text("buckets = [")
    with pytest.raises(ValueError, match="Failed to load config"):
        load_cfg(path)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"bucket_rules": [{"bucket": "nope", "pattern": "x"}]}, "Unknown bucket"),
        ({"bucket_rules": [{"bucket": "music", "pattern": "("}]}, "Invalid `bucket_rules`"),
        ({"buckets": {"Bad Key": {"name": "n", "description": "d"}}}, "kebab-case"),
        ({"buckets": {"all": {"name": "n", "description": "d"}}}, "reserved"),
        ({"near_days": 90}, "full_days"),
    ],
)
def test_cfg_validation(overrides: dict[str, object], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        make_cfg(**overrides)


def test_event_requires_aware_and_ordered() -> None:
    with pytest.raises(ValidationError, match="Naive"):
        make_event(start=datetime(2026, 10, 10, 20, tzinfo=TZ).replace(tzinfo=None))
    with pytest.raises(ValidationError, match="ends before"):
        make_event(end=datetime(2026, 10, 10, 19, tzinfo=TZ))


def test_event_normalizes() -> None:
    event = make_event(title="  Two \n  Words ", start=datetime(2026, 10, 11, 3, tzinfo=UTC))
    assert event.title == "Two Words"
    assert event.start.tzinfo == TZ
    assert event.start.hour == 20


def test_state_round_trip_sorted(tmp_path: Path) -> None:
    path = tmp_path / "events.json"
    b = make_event(source="beta", source_id="b")
    a = make_event(source="alpha", source_id="a")
    rev = Revision(content_hash="h", sequence=1, last_modified=datetime(2026, 1, 1, tzinfo=UTC))
    state = State(events=[b, a], source_meta={"z": {}, "a": {"k": 1}}, revisions={"u": rev})
    save_state(state, path)
    loaded = load_state(path)
    assert [e.source for e in loaded.events] == ["alpha", "beta"]
    assert list(loaded.source_meta) == ["a", "z"]
    assert loaded.revisions == {"u": rev}
    assert loaded.events_for("beta") == [b]


def test_load_state_missing_is_empty(tmp_path: Path) -> None:
    assert load_state(tmp_path / "nope.json") == State()


def test_load_state_invalid(tmp_path: Path) -> None:
    path = tmp_path / "events.json"
    path.write_text('{"events": 3}')
    with pytest.raises(ValueError, match="Failed to load state"):
        load_state(path)
    path.write_text('{"version": 99}')
    with pytest.raises(ValueError, match="Unsupported state"):
        load_state(path)


def test_html_to_text() -> None:
    assert html_to_text(None) is None
    assert html_to_text("<p> </p>") is None
    text = html_to_text("<p>Kids&#8217; swag &amp; more</p><p>Line<br>two</p>\n\n\n\n<p>x</p>")
    assert text == "Kids\u2019 swag & more\nLine\ntwo\n\nx"


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("900 Innes Ave, San Francisco, CA 94124", "San Francisco"),
        ("1 Main St, Oakland, California", "Oakland"),
        ("Somewhere", None),
        (None, None),
    ],
)
def test_locality_from_address(address: str | None, expected: str | None) -> None:
    assert locality_from_address(address) == expected
