"""`sfcal` command line.

Exit codes:
- 0: every source refreshed and every feed written and validated
- 1: fatal, nothing written (bad config, bad state, invalid output)
- 3: degraded, feeds written but at least one source fell back to prior data
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx

from sfcal import USER_AGENT
from sfcal.cfg import load_cfg
from sfcal.ics import write_feeds
from sfcal.pipeline import BuildResult, build
from sfcal.state import State, load_state, save_state

LOGGER = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_DEGRADED = 3
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def make_client() -> httpx.Client:
    """Create the shared HTTP client.

    Returns:
        httpx.Client: Client with the project User-Agent, timeouts and connect retries.
    """
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
        transport=httpx.HTTPTransport(retries=2),
    )


def summarize(result: BuildResult) -> str:
    """Render a human-readable run summary.

    Parameters:
        result (BuildResult): Build output.

    Returns:
        str: Multi-line summary.
    """
    asm = result.assembly
    lines = ["Sources:"]
    for r in result.reports:
        status = f"FAILED ({r.error})" if r.failed else "ok"
        lines.append(
            f"- {r.name}: {status}, fetched {r.fetched}, carried {r.carried}, "
            f"window {r.window.start:%Y-%m-%d}..{r.window.end:%Y-%m-%d}"
        )
    buckets: dict[str, int] = {}
    for f in asm.events:
        buckets[f.event.bucket] = buckets.get(f.event.bucket, 0) + 1
    lines.append("Buckets:")
    lines.extend(f"- {b}: {n}" for b, n in sorted(buckets.items()))
    for label, counter in (
        ("Dropped outside SF", asm.sf_dropped),
        ("Moved by bucket rule", asm.rule_moved),
        ("Dropped all-day rule match", asm.rule_dropped),
        ("Dropped as cross-source duplicate", asm.deduped),
    ):
        if counter:
            lines.append(f"{label}: {dict(sorted(counter.items()))}")
    return "\n".join(lines)


def cmd_build(args: argparse.Namespace) -> int:
    """Build every feed and the state file.

    Parameters:
        args (argparse.Namespace): Parsed `build` arguments.

    Returns:
        int: Process exit code.
    """
    cfg = load_cfg(args.cfg)
    state = load_state(args.state)
    with make_client() as client:
        result = build(cfg, state, client, now=datetime.now(UTC), force_full=args.full)
    counts = write_feeds(cfg, result.assembly.events, args.out)
    save_state(result.state, args.state)
    sys.stdout.write(summarize(result) + "\n")
    LOGGER.info(f"Wrote feeds to '{args.out}': {counts}")
    if result.degraded:
        LOGGER.error(
            f"Degraded build, failed sources: {[r.name for r in result.reports if r.failed]}"
        )
        return EXIT_DEGRADED
    return EXIT_OK


def cmd_check_source(args: argparse.Namespace) -> int:
    """Dry-run one source and print what it would publish.

    Parameters:
        args (argparse.Namespace): Parsed `check-source` arguments.

    Returns:
        int: Process exit code.
    """
    cfg = load_cfg(args.cfg)
    state = load_state(args.state) if args.state else State()
    with make_client() as client:
        result = build(
            cfg, state, client, now=datetime.now(UTC), force_full=args.full, only=args.source
        )
    sys.stdout.write(summarize(result) + "\n")
    return EXIT_DEGRADED if result.degraded else EXIT_OK


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line arguments.

    Parameters:
        argv (Sequence[str] | None): Arguments, `None` for `sys.argv`.

    Returns:
        argparse.Namespace: Parsed arguments with a `func` handler.
    """
    parser = argparse.ArgumentParser(prog="sfcal", description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(required=True)

    p_build = sub.add_parser("build", help="fetch sources and write every feed")
    p_build.add_argument("--cfg", type=Path, default=Path("sfcal.toml"))
    p_build.add_argument("--state", type=Path, required=True, help="events.json path")
    p_build.add_argument("--out", type=Path, required=True, help="feed output directory")
    p_build.add_argument("--full", action="store_true", help="refresh the full horizon")
    p_build.set_defaults(func=cmd_build)

    p_check = sub.add_parser("check-source", help="dry-run one source, write nothing")
    p_check.add_argument("source", help="source name from sfcal.toml")
    p_check.add_argument("--cfg", type=Path, default=Path("sfcal.toml"))
    p_check.add_argument("--state", type=Path, default=None, help="optional events.json")
    p_check.add_argument("--full", action="store_true", help="refresh the full horizon")
    p_check.set_defaults(func=cmd_check_source)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point.

    Parameters:
        argv (Sequence[str] | None): Arguments, `None` for `sys.argv`.

    Returns:
        int: Process exit code.
    """
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as e:
        LOGGER.error(f"Fatal: {e}")
        return EXIT_FATAL


if __name__ == "__main__":
    sys.exit(main())
