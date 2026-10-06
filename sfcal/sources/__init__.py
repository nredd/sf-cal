"""Source adapters, keyed by the `adapter` value in `sfcal.toml`."""

from __future__ import annotations

from sfcal.sources.base import Adapter, SourceError, SourceResult, Window
from sfcal.sources.dostuff import DoStuff
from sfcal.sources.fleetweeksf import FleetWeekSF
from sfcal.sources.icsfeed import IcsFeed
from sfcal.sources.jsonld import JsonLd

ADAPTERS: dict[str, type[Adapter]] = {
    "dostuff": DoStuff,
    "fleetweeksf": FleetWeekSF,
    "ics": IcsFeed,
    "jsonld": JsonLd,
}

__all__ = ["ADAPTERS", "Adapter", "SourceError", "SourceResult", "Window"]
