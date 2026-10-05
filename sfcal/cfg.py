"""`sfcal.toml` schema and loader.

References:
- TOML 1.0: https://toml.io/en/v1.0.0
"""

from __future__ import annotations

import logging
import re
import tomllib
from functools import cached_property
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

LOGGER = logging.getLogger(__name__)

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALL_FEED = "all"


class BucketCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Display name, used in the calendar name")
    description: str = Field(description="One-line summary shown in README and index.html")


class SourceCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str = Field(description="Adapter key in `sfcal.sources.ADAPTERS`")
    label: str = Field(description="Human-readable source name shown in event descriptions")
    enabled: bool = Field(default=True, description="Skip the source entirely when false")
    sf_only: bool = Field(default=True, description="Drop events located outside SF")
    priority: int = Field(description="Cross-source dedup winner is the highest priority")
    default_duration_minutes: int = Field(
        default=120, gt=0, description="Duration given to events that have no end"
    )
    options: dict[str, Any] = Field(
        default_factory=dict, description="Adapter-specific options, validated by the adapter"
    )


class BucketRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket: str = Field(description="Bucket matching timed events move to")
    pattern: str = Field(description="Case-insensitive regex over title and description")

    @cached_property
    def regex(self) -> re.Pattern[str]:
        """Compiled, case-insensitive `pattern`.

        Returns:
            re.Pattern[str]: The compiled pattern.
        """
        return re.compile(self.pattern, re.IGNORECASE)


class Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    near_days: int = Field(default=14, gt=0, description="Horizon of a normal run")
    full_days: int = Field(default=60, gt=0, description="Horizon of a full run")
    full_refresh_hours: float = Field(
        default=20, gt=0, description="Force a full run when the last one is older than this"
    )
    prune_days: int = Field(default=7, ge=0, description="Keep ended events this long")
    buckets: dict[str, BucketCfg] = Field(description="Feeds, in display order")
    sources: dict[str, SourceCfg] = Field(description="Event sources by name")
    bucket_rules: list[BucketRule] = Field(
        default_factory=list, description="Keyword routing applied before dedup"
    )

    @model_validator(mode="after")
    def _check_refs(self) -> Cfg:
        """Validate slugs, cross-references and regexes.

        Returns:
            Cfg: The validated config.

        Raises:
            ValueError: On a bad slug, an unknown bucket, or an invalid regex.
        """
        if self.full_days < self.near_days:
            raise ValueError(
                f"`full_days` must be >= `near_days`: '{self.full_days}' < '{self.near_days}'"
            )
        for key in [*self.buckets, *self.sources]:
            if not SLUG.match(key):
                raise ValueError(f"Bucket and source keys must be kebab-case slugs: '{key}'")
        if ALL_FEED in self.buckets:
            raise ValueError(f"Bucket key is reserved for the combined feed: '{ALL_FEED}'")
        for rule in self.bucket_rules:
            self.require_bucket(rule.bucket)
            try:
                _ = rule.regex
            except re.error as e:
                raise ValueError(f"Invalid `bucket_rules` pattern '{rule.pattern}': {e}") from e
        return self

    def require_bucket(self, bucket: str) -> str:
        """Assert that `bucket` is a configured bucket.

        Parameters:
            bucket (str): Bucket key.

        Returns:
            str: `bucket`, unchanged.

        Raises:
            ValueError: If the bucket is not configured.
        """
        if bucket not in self.buckets:
            raise ValueError(f"Unknown bucket '{bucket}', expected one of {list(self.buckets)}")
        return bucket


def load_cfg(path: Path) -> Cfg:
    """Load and validate `sfcal.toml`.

    Parameters:
        path (Path): Config file.

    Returns:
        Cfg: Validated config.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is not valid TOML or fails validation.
    """
    path = Path(str(path)).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Config file does not exist: '{path}'")
    try:
        raw = tomllib.loads(path.read_text())
        return Cfg.model_validate(raw)
    except (tomllib.TOMLDecodeError, ValidationError) as e:
        raise ValueError(f"Failed to load config '{path}': {e}") from e
