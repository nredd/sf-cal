"""Fetch every source, merge with prior state, and assemble the published feeds.

Two stages, deliberately separate:

1. Sources -> state. Each adapter refreshes its events inside the run's
   window; prior events outside the window are carried over, and a source
   that fails keeps everything it had. State holds raw adapter output.
2. State -> feeds. Default durations, the SF filter, keyword bucket rules
   and cross-source dedup are applied fresh on every run, so a config change
   takes effect everywhere at once without a re-fetch.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

import httpx

from sfcal import TZ
from sfcal.cfg import Cfg
from sfcal.ics import FeedEvent, render
from sfcal.models import Event, Revision, content_hash
from sfcal.sources import ADAPTERS, Adapter, Window
from sfcal.state import State

LOGGER = logging.getLogger(__name__)

SF_LAT = (37.70, 37.83)
SF_LON = (-122.52, -122.35)
SF_LOCALITY = "san francisco"
PUNCT = re.compile(r"[^\w\s]")


@dataclass
class SourceReport:
    """Outcome of one source in one run."""

    name: str
    window: Window
    fetched: int = 0
    carried: int = 0
    failed: bool = False
    error: str | None = None


@dataclass
class Assembly:
    """Published events plus what assembly dropped or moved, for reporting."""

    events: list[FeedEvent]
    sf_dropped: Counter[str] = field(default_factory=Counter)
    rule_moved: Counter[str] = field(default_factory=Counter)
    rule_dropped: Counter[str] = field(default_factory=Counter)
    deduped: Counter[str] = field(default_factory=Counter)


@dataclass
class BuildResult:
    """Everything a build produced."""

    state: State
    assembly: Assembly
    reports: list[SourceReport]

    @property
    def degraded(self) -> bool:
        """Report whether any source failed.

        Returns:
            bool: True when at least one source fell back to prior data.
        """
        return any(r.failed for r in self.reports)


def make_window(now: datetime, days: int, *, full: bool) -> Window:
    """Window from local yesterday through `days` days ahead, whole days.

    Parameters:
        now (datetime): Aware current time.
        days (int): Days ahead of today to cover.
        full (bool): Whether this is the full-horizon window.

    Returns:
        Window: Half-open window on local midnights.
    """
    today = now.astimezone(TZ).date()
    return Window(
        start=datetime.combine(today - timedelta(days=1), time(), tzinfo=TZ),
        end=datetime.combine(today + timedelta(days=days + 1), time(), tzinfo=TZ),
        full=full,
    )


def is_full_run(cfg: Cfg, state: State, now: datetime, *, force: bool) -> bool:
    """Decide whether this run refreshes the full horizon.

    Parameters:
        cfg (Cfg): Config.
        state (State): Prior state.
        now (datetime): Aware current time.
        force (bool): `--full` was passed.

    Returns:
        bool: True when forced, never done, or the last full run is stale.
    """
    if force or state.last_full_refresh is None:
        return True
    return now - state.last_full_refresh >= timedelta(hours=cfg.full_refresh_hours)


def make_adapters(cfg: Cfg, client: httpx.Client, only: str | None = None) -> dict[str, Adapter]:
    """Instantiate adapters for enabled sources, validating their options.

    Parameters:
        cfg (Cfg): Config.
        client (httpx.Client): Shared HTTP client.
        only (str | None): Restrict to this one source, enabled or not.

    Returns:
        dict[str, Adapter]: Adapters by source name, in config order.

    Raises:
        ValueError: On an unknown source, unknown adapter, or invalid options.
    """
    if only is not None and only not in cfg.sources:
        raise ValueError(f"Unknown source '{only}', expected one of {list(cfg.sources)}")
    adapters: dict[str, Adapter] = {}
    for name, src in cfg.sources.items():
        if (only is not None and name != only) or (only is None and not src.enabled):
            continue
        adapter_cls = ADAPTERS.get(src.adapter)
        if adapter_cls is None:
            raise ValueError(
                f"Unknown adapter '{src.adapter}' for source `{name}`, "
                f"expected one of {list(ADAPTERS)}"
            )
        adapters[name] = adapter_cls(name, src, client)
    return adapters


def refresh_source(
    adapter: Adapter, window: Window, prior: list[Event], meta: dict[str, Any]
) -> tuple[list[Event], dict[str, Any], SourceReport]:
    """Run one adapter and merge its output with its prior events.

    This is the per-source isolation boundary: any exception from the adapter
    is logged and the source keeps its prior events and cache.

    Parameters:
        adapter (Adapter): Adapter to run.
        window (Window): Window to refresh.
        prior (list[Event]): The source's events from state.
        meta (dict[str, Any]): The source's cache from state.

    Returns:
        tuple[list[Event], dict[str, Any], SourceReport]: Merged events,
        updated cache, and the report.
    """
    report = SourceReport(name=adapter.name, window=window)
    try:
        result = adapter.fetch(window, prior, dict(meta))
    except (
        Exception
    ) as e:  # NOTE(redd): isolation boundary, one bad source must not sink the build
        LOGGER.exception(f"Source `{adapter.name}` failed, keeping {len(prior)} prior events")
        report.failed, report.error = True, f"{type(e).__name__}: {e}"
        return prior, meta, report

    by_uid: dict[str, Event] = {}
    for event in result.events:
        by_uid.setdefault(event.uid, event)
    fetched = list(by_uid.values())
    report.fetched = len(fetched)
    had_in_window = sum(window.contains(p) for p in prior)
    if had_in_window and not any(window.contains(e) for e in fetched):
        LOGGER.error(
            f"Source `{adapter.name}` returned no events in the window but previously had "
            f"{had_in_window}, keeping prior events"
        )
        report.failed, report.error = True, "returned no events in window"
        return prior, meta, report

    seen = {e.uid for e in fetched}
    carried = [p for p in prior if not window.contains(p) and p.uid not in seen]
    report.carried = len(carried)
    return fetched + carried, result.meta, report


def in_sf(event: Event) -> bool:
    """Report whether an event is in San Francisco.

    Parameters:
        event (Event): Event to test.

    Returns:
        bool: True inside the SF bounding box, or with an SF locality, or with
        no location information at all.
    """
    if event.geo is not None:
        lat, lon = event.geo
        if SF_LAT[0] <= lat <= SF_LAT[1] and SF_LON[0] <= lon <= SF_LON[1]:
            return True
    if event.locality and event.locality.strip().casefold() == SF_LOCALITY:
        return True
    return event.geo is None and not event.locality


def dedup_key(event: Event) -> tuple[str, str]:
    """Cross-source identity: normalized title plus local start date.

    Parameters:
        event (Event): Event.

    Returns:
        tuple[str, str]: `(normalized title, ISO date)`.
    """
    title = " ".join(PUNCT.sub(" ", event.title.casefold()).split())
    return title, event.start.date().isoformat()


def assemble(
    cfg: Cfg, events: list[Event], revisions: dict[str, Revision], now: datetime
) -> tuple[Assembly, dict[str, Revision]]:
    """Turn raw state events into published feed events.

    Parameters:
        cfg (Cfg): Config.
        events (list[Event]): Raw events from state.
        revisions (dict[str, Revision]): Prior SEQUENCE ledger.
        now (datetime): Aware current time, stamped on changed events.

    Returns:
        tuple[Assembly, dict[str, Revision]]: Feed events and the new ledger.
    """
    asm = Assembly(events=[])
    kept: list[Event] = []
    for event in events:
        src = cfg.sources[event.source]
        if event.end is None:
            span = (
                timedelta(days=1)
                if event.all_day
                else timedelta(minutes=src.default_duration_minutes)
            )
            event = event.model_copy(update={"end": event.start + span})
        if src.sf_only and not in_sf(event):
            asm.sf_dropped[event.source] += 1
            continue
        haystack = f"{event.title}\n{event.description or ''}"
        rule = next((r for r in cfg.bucket_rules if r.regex.search(haystack)), None)
        if rule is not None:
            if event.all_day:
                asm.rule_dropped[event.source] += 1
                continue
            if event.bucket != rule.bucket:
                asm.rule_moved[event.source] += 1
                event = event.model_copy(update={"bucket": rule.bucket})
        if event.bucket not in cfg.buckets:
            LOGGER.warning(f"Dropping '{event.uid}' with unknown bucket '{event.bucket}'")
            continue
        kept.append(event)

    groups: defaultdict[tuple[str, str], list[Event]] = defaultdict(list)
    for event in kept:
        groups[dedup_key(event)].append(event)

    published: list[tuple[Event, tuple[str, ...]]] = []
    for group in groups.values():
        winner = max({e.source for e in group}, key=lambda s: cfg.sources[s].priority)
        losers = [e for e in group if e.source != winner]
        also = tuple(dict.fromkeys(e.url for e in losers if e.url))
        asm.deduped.update(e.source for e in losers)
        published.extend((e, also) for e in group if e.source == winner)

    new_revisions: dict[str, Revision] = {}
    for event, also in sorted(published, key=lambda p: (p[0].start, p[0].uid)):
        digest = content_hash(render(event, cfg.sources[event.source].label, also))
        old = revisions.get(event.uid)
        if old is not None and old.content_hash == digest:
            rev = old
        else:
            rev = Revision(
                content_hash=digest,
                sequence=old.sequence + 1 if old is not None else 0,
                last_modified=now,
            )
        new_revisions[event.uid] = rev
        asm.events.append(FeedEvent(event=event, also_listed=also, revision=rev))
    return asm, new_revisions


def build(
    cfg: Cfg,
    state: State,
    client: httpx.Client,
    *,
    now: datetime,
    force_full: bool = False,
    only: str | None = None,
) -> BuildResult:
    """Run every source, update state, and assemble the feeds.

    Parameters:
        cfg (Cfg): Config.
        state (State): Prior state; not mutated.
        client (httpx.Client): Shared HTTP client.
        now (datetime): Aware current time.
        force_full (bool): Refresh the full horizon regardless of state.
        only (str | None): Dry-run a single source, ignoring all others.

    Returns:
        BuildResult: New state, assembled feeds and per-source reports.

    Raises:
        ValueError: If `now` is naive or the source config is invalid.
    """
    if now.tzinfo is None:
        raise ValueError(f"`now` must be timezone-aware: '{now.isoformat()}'")
    adapters = make_adapters(cfg, client, only)
    full = is_full_run(cfg, state, now, force=force_full)
    near = make_window(now, cfg.near_days, full=False)
    wide = make_window(now, cfg.full_days, full=True)
    LOGGER.info(f"{'Full' if full else 'Near'} run, sources: {list(adapters)}")

    events: list[Event] = []
    meta: dict[str, dict[str, Any]] = {}
    reports: list[SourceReport] = []
    for name, adapter in adapters.items():
        window = wide if full or adapter.always_full else near
        merged, source_meta, report = refresh_source(
            adapter, window, state.events_for(name), state.source_meta.get(name, {})
        )
        events.extend(merged)
        meta[name] = source_meta
        reports.append(report)

    cutoff = now - timedelta(days=cfg.prune_days)
    live = [e for e in events if (e.end or e.start) >= cutoff]
    asm, revisions = assemble(cfg, live, state.revisions, now)
    new_state = State(
        last_full_refresh=now if full else state.last_full_refresh,
        events=live,
        source_meta=meta,
        revisions=revisions,
    )
    return BuildResult(state=new_state, assembly=asm, reports=reports)
