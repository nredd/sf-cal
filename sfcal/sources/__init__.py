"""Source adapters, keyed by the `adapter` value in `sfcal.toml`."""

from __future__ import annotations

from sfcal.sources.base import Adapter, SourceError, SourceResult, Window
from sfcal.sources.dostuff import DoStuff
from sfcal.sources.fleetweeksf import FleetWeekSF

ADAPTERS: dict[str, type[Adapter]] = {
    "dostuff": DoStuff,
    "fleetweeksf": FleetWeekSF,
}

__all__ = ["ADAPTERS", "Adapter", "SourceError", "SourceResult", "Window"]
