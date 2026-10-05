"""Keep the hand-written subscribe links in sync with `sfcal.toml`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sfcal.cfg import ALL_FEED, load_cfg

ROOT = Path(__file__).parent.parent
RAW = "raw.githubusercontent.com/nredd/sf-cal/feeds/{}.ics"
GOOGLE = "https://calendar.google.com/calendar/u/0/r?cid=webcal://" + RAW
FEED_LINK = re.compile(r"/sf-cal/feeds/([a-z0-9-]+)\.ics")
FEEDS = [ALL_FEED, *load_cfg(ROOT / "sfcal.toml").buckets]


@pytest.mark.parametrize("feed", FEEDS)
def test_readme_links(feed: str) -> None:
    readme = (ROOT / "README.md").read_text()
    assert f"`https://{RAW.format(feed)}`" in readme
    assert GOOGLE.format(feed) in readme


@pytest.mark.parametrize("feed", FEEDS)
def test_index_links(feed: str) -> None:
    index = (ROOT / "index.html").read_text()
    assert f'href="webcal://{RAW.format(feed)}"' in index
    assert f'href="{GOOGLE.format(feed)}"' in index


@pytest.mark.parametrize("doc", ["README.md", "index.html"])
def test_no_stray_feeds(doc: str) -> None:
    linked = set(FEED_LINK.findall((ROOT / doc).read_text()))
    assert linked == set(FEEDS)
