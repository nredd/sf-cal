"""End-to-end CLI tests with HTTP mocked by `respx`."""

from __future__ import annotations

from pathlib import Path

import pytest
import respx
from icalendar import Calendar

from sfcal.cli import EXIT_DEGRADED, EXIT_FATAL, EXIT_OK, main
from sfcal.state import load_state
from tests.conftest import fixture_text

CAL_URL = "https://fleetweeksf.org/calendar-of-events/"
MAP_URL = "https://fleetweeksf.org/map-of-events/"
CFG = """
[buckets.fleet-week]
name = "Fleet Week"
description = "fw"

[buckets.music]
name = "Music"
description = "m"

[sources.fleetweeksf]
adapter = "fleetweeksf"
label = "SF Fleet Week"
priority = 100
sf_only = false
default_duration_minutes = 60
"""


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    """Write a Fleet-Week-only config.

    Parameters:
        tmp_path (Path): pytest temp dir.

    Returns:
        Path: Config path.
    """
    path = tmp_path / "sfcal.toml"
    path.write_text(CFG)
    return path


def build_args(cfg_path: Path, out: Path, *extra: str) -> list[str]:
    """Arguments for `sfcal build`.

    Parameters:
        cfg_path (Path): Config.
        out (Path): Output dir, also holding `events.json`.
        *extra (str): Extra flags.

    Returns:
        list[str]: argv.
    """
    return ["build", "--cfg", str(cfg_path), "--state", str(out / "events.json"),
            "--out", str(out), *extra]  # fmt: skip


@respx.mock
def test_build_then_degraded_rebuild(cfg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "feeds"
    respx.get(CAL_URL).respond(text=fixture_text("fleetweeksf_calendar.html"))
    respx.get(MAP_URL).respond(text=fixture_text("fleetweeksf_map.html"))
    assert main(build_args(cfg_path, out)) == EXIT_OK

    assert sorted(p.name for p in out.glob("*.ics")) == ["all.ics", "fleet-week.ics", "music.ics"]
    first = (out / "fleet-week.ics").read_bytes()
    assert len(Calendar.from_ical(first).walk("VEVENT")) == 59
    assert load_state(out / "events.json").last_full_refresh is not None

    assert main(build_args(cfg_path, out)) == EXIT_OK
    assert (out / "fleet-week.ics").read_bytes() == first

    respx.get(CAL_URL).respond(status_code=404)
    assert main(build_args(cfg_path, out, "--full")) == EXIT_DEGRADED
    assert (out / "fleet-week.ics").read_bytes() == first
    assert len(load_state(out / "events.json").events) == 59


def test_build_bad_cfg_is_fatal(tmp_path: Path) -> None:
    bad = tmp_path / "sfcal.toml"
    bad.write_text("[buckets.x]\nname = 1\n")
    assert main(build_args(bad, tmp_path / "feeds")) == EXIT_FATAL
    assert not (tmp_path / "feeds").exists()


def test_build_missing_cfg_is_fatal(tmp_path: Path) -> None:
    assert main(build_args(tmp_path / "nope.toml", tmp_path)) == EXIT_FATAL


@respx.mock
def test_check_source(cfg_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    respx.get(CAL_URL).respond(text=fixture_text("fleetweeksf_calendar.html"))
    respx.get(MAP_URL).respond(text=fixture_text("fleetweeksf_map.html"))
    assert main(["check-source", "fleetweeksf", "--cfg", str(cfg_path)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "- fleetweeksf: ok, fetched 59" in out
    assert "- fleet-week: 59" in out


def test_check_source_unknown(cfg_path: Path) -> None:
    assert main(["check-source", "nope", "--cfg", str(cfg_path)]) == EXIT_FATAL
