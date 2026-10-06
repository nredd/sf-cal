"""WordPress sites whose posts embed schema.org `Event` JSON-LD (Funcheap).

Discovery goes through the WordPress REST API, which is cheap and returns
`modified_gmt` per post; only new or edited posts are fetched and parsed.
Everything else is reused from the previous run. The first run backfills the
whole lookback window and is slow; later runs fetch a handful of pages.

References:
- WordPress REST API posts: https://developer.wordpress.org/rest-api/reference/posts/
- schema.org Event: https://schema.org/Event
- Funcheap: https://sf.funcheap.com/
"""

from __future__ import annotations

import html
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, override

import httpx
from pydantic import BaseModel, ConfigDict, Field
from selectolax.lexbor import LexborHTMLParser

from sfcal import TZ
from sfcal.models import Event
from sfcal.sources.base import (
    Adapter,
    AdapterOptions,
    SourceError,
    SourceResult,
    Window,
    html_to_text,
    locality_from_address,
)

LOGGER = logging.getLogger(__name__)

CATEGORY_CLASS = re.compile(r"^category-(.+)$")
SF_SUFFIX = re.compile(r"\s*\((?:SF|San Francisco)\)\s*$", re.IGNORECASE)
THUMB_SIZE = re.compile(r"-\d+x\d+(?=\.\w+$)")
DISCLAIMER = re.compile(r"\n+Disclaimer:.*\Z", re.DOTALL)
DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DESCRIPTION_LIMIT = 1500


class JsonLdOptions(AdapterOptions):
    base_url: str = Field(description="WordPress site root, e.g. `https://sf.funcheap.com`")
    lookback_days: int = Field(default=45, gt=0, description="Discover posts published since")
    concurrency: int = Field(default=4, gt=0, description="Parallel post-page fetches")
    min_interval: float = Field(default=0.25, ge=0, description="Seconds between API pages")
    max_api_pages: int = Field(default=40, gt=0, description="Safety cap on discovery pages")
    categories: dict[str, str] = Field(
        description="Category slug to bucket; the first slug in this order that a post has wins"
    )
    skip_categories: list[str] = Field(
        default_factory=list, description="Posts with any of these slugs are never fetched"
    )
    default_bucket: str = Field(default="arts-community", description="For unmapped posts")


class Post(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    link: str
    modified_gmt: str
    class_list: list[str] = Field(default_factory=list)

    @property
    def slugs(self) -> list[str]:
        """Category slugs from `class_list`.

        Returns:
            list[str]: e.g. `["top-pick", "live-music-event"]`.
        """
        return [m.group(1) for c in self.class_list if (m := CATEGORY_CLASS.match(c))]


def parse_when(value: str) -> tuple[datetime, bool]:
    """Parse a schema.org date or datetime.

    Parameters:
        value (str): ISO 8601 date or datetime.

    Returns:
        tuple[datetime, bool]: Aware local datetime, and whether it was date-only.

    Raises:
        ValueError: If the value is not ISO 8601.
    """
    if DATE_ONLY.match(value):
        return datetime.combine(date.fromisoformat(value), time(), tzinfo=TZ), True
    parsed = datetime.fromisoformat(value)
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ)), False


def first(value: Any) -> Any:
    """Unwrap a schema.org value that may be a list.

    Parameters:
        value (Any): Value or list of values.

    Returns:
        Any: The first element of a list, otherwise `value`.
    """
    return value[0] if isinstance(value, list) and value else value


def find_events(page_html: str) -> list[dict[str, Any]]:
    """Collect every `Event` object from a page's JSON-LD blocks.

    Parameters:
        page_html (str): Post page HTML.

    Returns:
        list[dict[str, Any]]: Event objects, in page order.
    """
    found: list[dict[str, Any]] = []
    for script in LexborHTMLParser(page_html).css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text())
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        found.extend(
            item for item in items if isinstance(item, dict) and item.get("@type") == "Event"
        )
    return found


class JsonLd(Adapter[JsonLdOptions]):
    """Incremental WordPress post discovery plus JSON-LD `Event` parsing."""

    options_model = JsonLdOptions

    @override
    def referenced_buckets(self) -> set[str]:
        """Every bucket the category map and fallback can assign.

        Returns:
            set[str]: Bucket keys.
        """
        return {*self.opts.categories.values(), self.opts.default_bucket}

    @override
    def fetch(self, window: Window, prior: list[Event], meta: dict[str, Any]) -> SourceResult:
        """Discover recent posts and parse the new or edited ones.

        Parameters:
            window (Window): Unused; discovery is by publish date, not event date.
            prior (list[Event]): Previous events, reused for unchanged posts.
            meta (dict[str, Any]): `{"posts": {post_id: modified_gmt}}` from the last run.

        Returns:
            SourceResult: Events for every known post and the updated cache.

        Raises:
            SourceError: If discovery fails or every post fetch fails.
            httpx.HTTPError: If a discovery request fails.
        """
        seen: dict[str, str] = dict(meta.get("posts", {}))
        posts = [p for p in self.discover() if not set(p.slugs) & set(self.opts.skip_categories)]
        stale = [p for p in posts if seen.get(str(p.id)) != p.modified_gmt]
        LOGGER.info(f"`{self.name}`: {len(posts)} posts, {len(stale)} new or edited")

        with ThreadPoolExecutor(max_workers=self.opts.concurrency) as pool:
            pages = list(pool.map(self._page, stale))
        failed = sum(page is None for page in pages)
        if stale and failed == len(stale):
            raise SourceError(f"All {failed} post fetches failed")

        by_post: dict[str, list[Event]] = {}
        for event in prior:
            by_post.setdefault(event.source_id.partition("-")[0], []).append(event)
        unknown: set[str] = set()
        for post, page in zip(stale, pages, strict=True):
            if page is None:
                continue
            by_post[str(post.id)] = self.parse_post(post, page, unknown)
            seen[str(post.id)] = post.modified_gmt
        if unknown:
            LOGGER.warning(
                f"`{self.name}`: {len(unknown)} posts matched no category, sent to "
                f"'{self.opts.default_bucket}'"
            )

        live = {str(p.id) for p in posts}
        return SourceResult(
            events=[e for events in by_post.values() for e in events],
            meta={"posts": {k: v for k, v in sorted(seen.items()) if k in live}},
        )

    def discover(self) -> list[Post]:
        """List posts published within the lookback window.

        Returns:
            list[Post]: Posts, newest first.

        Raises:
            SourceError: If the API returns an unexpected shape.
            httpx.HTTPError: If a request fails.
        """
        after = datetime.now(UTC) - timedelta(days=self.opts.lookback_days)
        url = f"{self.opts.base_url}/wp-json/wp/v2/posts"
        posts: list[Post] = []
        page, total = 1, 1
        while page <= min(total, self.opts.max_api_pages):
            resp = self.get(
                url,
                min_interval=self.opts.min_interval,
                params={
                    "after": after.strftime("%Y-%m-%dT%H:%M:%S"),
                    "per_page": 100,
                    "page": page,
                    "_fields": "id,link,modified_gmt,class_list",
                },
            )
            try:
                posts.extend(Post.model_validate(item) for item in resp.json())
                total = int(resp.headers.get("X-WP-TotalPages", "1"))
            except (ValueError, TypeError) as e:
                raise SourceError(f"Unexpected WordPress payload for '{resp.url}': {e}") from e
            page += 1
        return posts

    def _page(self, post: Post) -> str | None:
        """Fetch one post page, logging rather than raising on failure.

        Parameters:
            post (Post): Post to fetch.

        Returns:
            str | None: Page HTML, or `None` if the fetch failed.
        """
        try:
            resp = self.client.get(post.link)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            LOGGER.warning(f"`{self.name}`: fetching post '{post.link}' failed: {e}")
            return None
        return resp.text

    def parse_post(self, post: Post, page_html: str, unknown: set[str]) -> list[Event]:
        """Convert a post's JSON-LD events.

        Parameters:
            post (Post): Post metadata.
            page_html (str): Post page HTML.
            unknown (set[str]): Collects ids of posts with no mapped category.

        Returns:
            list[Event]: Zero or more events; malformed JSON-LD is skipped.
        """
        slugs = set(post.slugs)
        slug = next((s for s in self.opts.categories if s in slugs), None)
        if slug is None:
            unknown.add(str(post.id))
        bucket = self.opts.categories[slug] if slug else self.opts.default_bucket
        page = LexborHTMLParser(page_html)
        og = page.css_first('meta[property="og:image"]')
        og_image = og.attributes.get("content") if og is not None else None

        items = find_events(page_html)
        events: list[Event] = []
        for i, item in enumerate(items):
            source_id = str(post.id) if len(items) == 1 else f"{post.id}-{i}"
            try:
                events.append(self._event(item, source_id, post, bucket, slug, og_image))
            except (ValueError, TypeError, KeyError) as e:
                LOGGER.warning(f"`{self.name}`: skipping event in '{post.link}': {e}")
        return events

    def _event(
        self,
        item: dict[str, Any],
        source_id: str,
        post: Post,
        bucket: str,
        slug: str | None,
        og_image: str | None,
    ) -> Event:
        """Convert one JSON-LD `Event`.

        Parameters:
            item (dict[str, Any]): JSON-LD object.
            source_id (str): Post id, suffixed when a post has several events.
            post (Post): Post metadata.
            bucket (str): Bucket from the category map.
            slug (str | None): Matched category slug.
            og_image (str | None): Page-level share image.

        Returns:
            Event: The event.

        Raises:
            KeyError: If `startDate` is missing.
            ValueError: If dates are malformed or the event fails validation.
        """
        start, all_day = parse_when(item["startDate"])
        end: datetime | None = None
        if item.get("endDate"):
            end, _ = parse_when(item["endDate"])
            end = end + timedelta(days=1) if all_day else end
            if end < start:
                # NOTE(redd): overnight events (21:00 -> 01:00) carry the start's date
                rolled = end + timedelta(days=1)
                end = rolled if rolled > start else None

        place = first(item.get("location")) or {}
        place = place if isinstance(place, dict) else {"name": str(place)}
        address = place.get("address")
        if isinstance(address, dict):
            parts = ("streetAddress", "addressLocality", "addressRegion", "postalCode")
            locality = address.get("addressLocality")
            address = ", ".join(str(address[k]) for k in parts if address.get(k))
        else:
            locality = locality_from_address(address)
        geo = place.get("geo") or {}
        offer = first(item.get("offers")) or {}
        price = offer.get("price") if isinstance(offer, dict) else None
        is_free = price is not None and float(price) == 0
        image = first(item.get("image"))
        image = image.get("url") if isinstance(image, dict) else image

        body = html_to_text(item.get("description"))
        body = DISCLAIMER.sub("", body).strip() if body else None
        if body and len(body) > DESCRIPTION_LIMIT:
            body = body[:DESCRIPTION_LIMIT].rstrip() + "..."
        venue = html.unescape(str(place.get("name") or "")).strip()
        return Event(
            source=self.name,
            source_id=source_id,
            title=SF_SUFFIX.sub("", html.unescape(str(item.get("name") or ""))),
            start=start,
            end=end,
            all_day=all_day,
            bucket=bucket,
            venue=venue or None,
            address=(str(address).strip() or None) if address else None,
            locality=locality,
            geo=(float(geo["latitude"]), float(geo["longitude"]))
            if geo.get("latitude") is not None and geo.get("longitude") is not None
            else None,
            url=post.link,
            ticket_url=offer.get("url") if isinstance(offer, dict) else None,
            price_text=None if is_free or price is None else f"${float(price):.2f}",
            is_free=is_free,
            description=body,
            image_url=og_image or (THUMB_SIZE.sub("", image) if image else None),
            categories=[slug.replace("-", " ").title()] if slug else [],
        )
