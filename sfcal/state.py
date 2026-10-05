"""Persisted build state, `events.json` on the `feeds` branch.

The state doubles as the per-source fallback when a source fails, the
incremental cache for adapters that need one, and the SEQUENCE ledger that
keeps unchanged events byte-identical between runs.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sfcal.models import Event, Revision

LOGGER = logging.getLogger(__name__)

STATE_VERSION = 1


class State(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=STATE_VERSION, description="Schema version of this file")
    last_full_refresh: datetime | None = Field(
        default=None, description="When the last full-horizon run finished, UTC"
    )
    events: list[Event] = Field(default_factory=list, description="Raw adapter output")
    source_meta: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Opaque per-source cache, owned by each adapter"
    )
    revisions: dict[str, Revision] = Field(
        default_factory=dict, description="SEQUENCE ledger keyed by output UID"
    )

    def events_for(self, source: str) -> list[Event]:
        """Return the stored events of one source.

        Parameters:
            source (str): Source name.

        Returns:
            list[Event]: Events whose `source` matches.
        """
        return [e for e in self.events if e.source == source]


def load_state(path: Path) -> State:
    """Load the state file, or start empty when it does not exist yet.

    Parameters:
        path (Path): `events.json` path.

    Returns:
        State: Parsed state.

    Raises:
        ValueError: If the file exists but is not valid state.
    """
    path = Path(str(path)).resolve()
    if not path.exists():
        LOGGER.warning(f"No state at '{path}', starting empty")
        return State()
    try:
        state = State.model_validate_json(path.read_text())
    except ValidationError as e:
        raise ValueError(f"Failed to load state '{path}': {e}") from e
    if state.version != STATE_VERSION:
        raise ValueError(
            f"Unsupported state `version` in '{path}': '{state.version}' != '{STATE_VERSION}'"
        )
    return state


def save_state(state: State, path: Path) -> None:
    """Write the state deterministically so unchanged runs produce no diff.

    Parameters:
        state (State): State to write.
        path (Path): Destination `events.json`.
    """
    path = Path(str(path)).resolve()
    ordered = state.model_copy(
        update={
            "events": sorted(state.events, key=lambda e: (e.source, e.start, e.uid)),
            "source_meta": dict(sorted(state.source_meta.items())),
            "revisions": dict(sorted(state.revisions.items())),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ordered.model_dump_json(indent=1) + "\n")
