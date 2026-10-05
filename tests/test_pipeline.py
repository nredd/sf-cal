"""Tests for source merging, filtering, routing, dedup and revisions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, override

import httpx
import pytest

from sfcal import TZ
from sfcal.cfg import Cfg
from sfcal.models import Event
from sfcal.pipeline import (
    assemble,
    build,
    dedup_key,
    in_sf,
    is_full_run,
    make_adapters,
    make_window,
)
from sfcal.sources import ADAPTERS
from sfcal.sources.base import Adapter, AdapterOptions, SourceError, SourceResult, Window
from sfcal.state import State
from tests.conftest import make_cfg, make_event

NOW = datetime(2026, 10, 6, 18, tzinfo=UTC)  # 11:00 PDT on Oct 6

Behavior = Callable[[Window, list[Event], dict[str, Any]], SourceResult]


class FakeAdapter(Adapter[AdapterOptions]):
    """Adapter whose behavior is set per source name by the test."""

    options_model = AdapterOptions
    behaviors: ClassVar[dict[str, Behavior]] = {}
    windows: ClassVar[list[Window]] = []

    @override
    def fetch(self, window: Window, prior: list[Event], meta: dict[str, Any]) -> SourceResult:
        """Delegate to the configured behavior.

        Parameters:
            window (Window): Window.
            prior (list[Event]): Prior events.
            meta (dict[str, Any]): Prior cache.

        Returns:
            SourceResult: Whatever the behavior returns.
        """
        FakeAdapter.windows.append(window)
        return self.behaviors[self.name](window, prior, meta)


@pytest.fixture(autouse=True)
def fake_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register `FakeAdapter` as adapter `fake` and reset its behaviors."""
    monkeypatch.setitem(ADAPTERS, "fake", FakeAdapter)
    FakeAdapter.behaviors = {}
    FakeAdapter.windows = []


def returns(*events: Event, meta: dict[str, Any] | None = None) -> Behavior:
    """Behavior that returns fixed events.

    Parameters:
        *events (Event): Events to return.
        meta (dict[str, Any] | None): Cache to return.

    Returns:
        Behavior: The behavior.
    """
    return lambda _w, _p, _m: SourceResult(events=list(events), meta=meta or {})


def raises(_w: Window, _p: list[Event], _m: dict[str, Any]) -> SourceResult:
    """Behavior that fails like a broken page.

    Raises:
        SourceError: Always.
    """
    raise SourceError("page changed")


def run(cfg: Cfg, state: State | None = None, **kwargs: Any) -> Any:
    """Run `build` with the fake adapter.

    Parameters:
        cfg (Cfg): Config.
        state (State | None): Prior state.
        **kwargs (Any): Passed to `build`.

    Returns:
        Any: The `BuildResult`.
    """
    with httpx.Client() as client:
        return build(cfg, state or State(), client, now=kwargs.pop("now", NOW), **kwargs)


def at(day: int, hour: int = 20) -> datetime:
    """Local October 2026 datetime.

    Parameters:
        day (int): Day of month.
        hour (int): Hour.

    Returns:
        datetime: Aware datetime.
    """
    return datetime(2026, 10, day, hour, tzinfo=TZ)


def test_make_window_local_days() -> None:
    w = make_window(NOW, 14, full=False)
    assert w.start == datetime(2026, 10, 5, tzinfo=TZ)
    assert w.end == datetime(2026, 10, 21, tzinfo=TZ)
    assert len(w.days()) == 16
    late = make_window(datetime(2026, 10, 7, 6, tzinfo=UTC), 14, full=False)  # 23:00 Oct 6 PDT
    assert late.start == datetime(2026, 10, 5, tzinfo=TZ)


def test_is_full_run() -> None:
    cfg = make_cfg()
    assert is_full_run(cfg, State(), NOW, force=False)
    fresh = State(last_full_refresh=NOW - timedelta(hours=3))
    assert not is_full_run(cfg, fresh, NOW, force=False)
    assert is_full_run(cfg, fresh, NOW, force=True)
    assert is_full_run(cfg, State(last_full_refresh=NOW - timedelta(hours=21)), NOW, force=False)


def test_tiered_windows_and_last_full_refresh() -> None:
    cfg = make_cfg()
    FakeAdapter.behaviors = {"alpha": returns(make_event()), "beta": returns()}
    first = run(cfg)
    assert FakeAdapter.windows[0].full
    assert first.state.last_full_refresh == NOW

    later = NOW + timedelta(hours=3)
    second = run(cfg, first.state, now=later)
    assert not FakeAdapter.windows[-1].full
    assert second.state.last_full_refresh == NOW


def test_carry_over_outside_near_window() -> None:
    cfg = make_cfg()
    far = make_event(source_id="far", start=at(30))
    near_old = make_event(source_id="gone", start=at(8))
    prior = State(last_full_refresh=NOW, events=[far, near_old])
    FakeAdapter.behaviors = {"alpha": returns(make_event(source_id="new")), "beta": returns()}

    result = run(cfg, prior)
    ids = {e.source_id for e in result.state.events}
    assert ids == {"new", "far"}
    alpha = next(r for r in result.reports if r.name == "alpha")
    assert (alpha.fetched, alpha.carried, alpha.failed) == (1, 1, False)


def test_failure_keeps_prior_and_degrades() -> None:
    cfg = make_cfg()
    prior_event = make_event(source="beta", source_id="b1")
    state = State(events=[prior_event], source_meta={"beta": {"k": 1}})
    FakeAdapter.behaviors = {"alpha": returns(make_event()), "beta": raises}

    result = run(cfg, state)
    assert result.degraded
    assert {e.uid for e in result.state.events} == {make_event().uid, prior_event.uid}
    assert result.state.source_meta["beta"] == {"k": 1}
    beta = next(r for r in result.reports if r.name == "beta")
    assert beta.error == "SourceError: page changed"


def test_zero_in_window_after_nonzero_is_failure() -> None:
    cfg = make_cfg()
    state = State(events=[make_event(source_id="x", start=at(9))])
    FakeAdapter.behaviors = {"alpha": returns(), "beta": returns()}
    result = run(cfg, state)
    assert result.degraded
    assert [e.source_id for e in result.state.events] == ["x"]


def test_empty_source_with_no_history_is_fine() -> None:
    FakeAdapter.behaviors = {"alpha": returns(), "beta": returns()}
    result = run(make_cfg())
    assert not result.degraded
    assert result.assembly.events == []


def test_disabled_source_is_dropped() -> None:
    cfg = make_cfg()
    cfg.sources["beta"].enabled = False
    state = State(events=[make_event(source="beta")])
    FakeAdapter.behaviors = {"alpha": returns()}
    result = run(cfg, state)
    assert result.state.events == []
    assert [r.name for r in result.reports] == ["alpha"]


def test_prune_old_events() -> None:
    cfg = make_cfg()
    old = make_event(source_id="old", start=NOW - timedelta(days=9))
    recent = make_event(source_id="recent", start=NOW - timedelta(days=3))
    state = State(last_full_refresh=NOW, events=[old, recent])
    FakeAdapter.behaviors = {"alpha": raises, "beta": returns()}
    result = run(cfg, state)
    assert [e.source_id for e in result.state.events] == ["recent"]


def test_make_adapters_errors() -> None:
    with httpx.Client() as client:
        with pytest.raises(ValueError, match="Unknown source"):
            make_adapters(make_cfg(), client, "nope")
        cfg = make_cfg()
        cfg.sources["alpha"].adapter = "missing"
        with pytest.raises(ValueError, match="Unknown adapter"):
            make_adapters(cfg, client)
        cfg = make_cfg()
        cfg.sources["alpha"].options = {"bogus": 1}
        with pytest.raises(ValueError, match="Invalid options"):
            make_adapters(cfg, client)


def test_build_requires_aware_now() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        run(make_cfg(), now=NOW.replace(tzinfo=None))


def test_only_runs_one_source() -> None:
    FakeAdapter.behaviors = {"beta": returns(make_event(source="beta"))}
    cfg = make_cfg()
    cfg.sources["beta"].enabled = False
    result = run(cfg, only="beta")
    assert [r.name for r in result.reports] == ["beta"]
    assert len(result.assembly.events) == 1


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"geo": (37.78, -122.41), "locality": None}, True),
        ({"geo": (37.80, -122.27), "locality": "Oakland"}, False),
        ({"geo": (37.80, -122.27), "locality": " San Francisco "}, True),
        ({"geo": None, "locality": "Berkeley"}, False),
        ({"geo": None, "locality": None}, True),
        ({"geo": (0.0, 0.0), "locality": None}, False),
    ],
)
def test_in_sf(fields: dict[str, Any], expected: bool) -> None:
    assert in_sf(make_event(**fields)) is expected


def test_assemble_sf_filter_respects_source_flag() -> None:
    cfg = make_cfg()
    oak = make_event(locality="Oakland")
    asm, _ = assemble(cfg, [oak], {}, NOW)
    assert asm.events == []
    assert asm.sf_dropped == {"alpha": 1}
    cfg.sources["alpha"].sf_only = False
    asm, _ = assemble(cfg, [oak], {}, NOW)
    assert len(asm.events) == 1


def test_assemble_default_durations() -> None:
    cfg = make_cfg()
    cfg.sources["beta"].default_duration_minutes = 60
    timed = make_event()
    hour = make_event(source="beta", source_id="b", title="Other")
    allday = make_event(source_id="d", title="Day", start=at(10, 0), all_day=True)
    asm, _ = assemble(cfg, [timed, hour, allday], {}, NOW)
    ends = {f.event.source_id: (f.event.end or f.event.start) - f.event.start for f in asm.events}
    assert ends == {"1": timedelta(hours=2), "b": timedelta(hours=1), "d": timedelta(days=1)}


def test_bucket_rules_move_and_word_boundaries() -> None:
    cfg = make_cfg()
    party = make_event(source_id="p", title="Dirtybird x OTG: Fleet Week", bucket="nightlife")
    by_desc = make_event(source_id="d", title="Rooftop", description="Watch the Blue Angels!")
    fleetwood = make_event(source_id="f", title="Rumours of Fleetwood Mac")
    asm, _ = assemble(cfg, [party, by_desc, fleetwood], {}, NOW)
    buckets = {f.event.source_id: f.event.bucket for f in asm.events}
    assert buckets == {"p": "fleet-week", "d": "fleet-week", "f": "music"}
    assert asm.rule_moved == {"alpha": 2}


def test_bucket_rules_drop_all_day_matches() -> None:
    umbrella = make_event(title="Fleet Week San Francisco", start=at(6, 0), all_day=True)
    asm, _ = assemble(make_cfg(), [umbrella], {}, NOW)
    assert asm.events == []
    assert asm.rule_dropped == {"alpha": 1}


def test_unknown_bucket_dropped() -> None:
    asm, _ = assemble(make_cfg(), [make_event(bucket="nope")], {}, NOW)
    assert asm.events == []


def test_dedup_key_normalizes() -> None:
    a = make_event(title="She & Him!")
    b = make_event(title="  she   him ")
    assert dedup_key(a) == dedup_key(b) == ("she him", "2026-10-10")


def test_dedup_priority_and_also_listed() -> None:
    cfg = make_cfg()
    hi = make_event(source="alpha", source_id="a", title="She & Him", url="https://a/1")
    lo = make_event(source="beta", source_id="b", title="SHE & HIM", url="https://b/1")
    other_day = make_event(source="beta", source_id="c", title="She & Him", start=at(11))
    asm, _ = assemble(cfg, [lo, hi, other_day], {}, NOW)
    by_id = {f.event.source_id: f for f in asm.events}
    assert set(by_id) == {"a", "c"}
    assert by_id["a"].also_listed == ("https://b/1",)
    assert by_id["c"].also_listed == ()
    assert asm.deduped == {"beta": 1}


def test_dedup_keeps_same_source_duplicates() -> None:
    tours = [make_event(source_id=str(i), title="Ship Tours", venue=f"Pier {i}") for i in (27, 35)]
    asm, _ = assemble(make_cfg(), tours, {}, NOW)
    assert len(asm.events) == 2


def test_revisions_bump_only_on_change() -> None:
    cfg = make_cfg()
    event = make_event()
    asm1, revs1 = assemble(cfg, [event], {}, NOW)
    assert asm1.events[0].revision.sequence == 0

    later = NOW + timedelta(hours=3)
    asm2, revs2 = assemble(cfg, [event], revs1, later)
    assert revs2 == revs1
    assert asm2.events[0].revision.last_modified == NOW

    changed = event.model_copy(update={"venue": "The Castro"})
    asm3, revs3 = assemble(cfg, [changed], revs2, later)
    assert asm3.events[0].revision.sequence == 1
    assert revs3[event.uid].last_modified == later


def test_assembly_is_sorted() -> None:
    events = [make_event(source_id=str(i), title=f"E{i}", start=at(20 - i)) for i in range(5)]
    asm, _ = assemble(make_cfg(), events, {}, NOW)
    starts = [f.event.start for f in asm.events]
    assert starts == sorted(starts)


def test_cfg_fixture_is_valid() -> None:
    assert isinstance(make_cfg(), Cfg)
