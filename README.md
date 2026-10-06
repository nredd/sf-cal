# sf-cal

Subscribable calendars of San Francisco events, scraped from several sources every 3 hours by
GitHub Actions. No server: the feeds are plain `.ics` files on the `feeds` branch, served by
`raw.githubusercontent.com`.

One-click subscribe page: **https://nredd.github.io/sf-cal/**

## Feeds

| Feed | What | Google | URL (Apple, Outlook, anything else) |
| --- | --- | --- | --- |
| All | Every bucket below in one calendar | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/all.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/all.ics` |
| Fleet Week | SF Fleet Week: official concerts, ship tours, air shows, plus side events | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/fleet-week.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/fleet-week.ics` |
| Music | Concerts and live music | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/music.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/music.ics` |
| Comedy | Stand-up, improv and comedy shows | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/comedy.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/comedy.ics` |
| Stage | Theatre, dance and performing arts | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/stage.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/stage.ics` |
| Film | Screenings and film events | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/film.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/film.ics` |
| Nightlife | DJ sets, parties and LGBTQ nights | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/nightlife.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/nightlife.ics` |
| Food & Drink | Tastings, food events and pop-ups | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/food-drink.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/food-drink.ics` |
| Outdoors | Outdoor recreation and sports | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/outdoors.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/outdoors.ics` |
| Arts & Community | Arts, family, lectures, festivals, markets and everything else | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/arts-community.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/arts-community.ics` |

The feed URLs are permanent. Feeds whose sources aren't wired up yet are valid empty calendars
that fill in as sources land, so nobody has to re-subscribe.

`webcal://` links only work from the Pages site: GitHub's Markdown sanitizer strips any link
that isn't `http`, `https`, `mailto` or relative.

## Using it

- Subscribe: Google via the Add link (or "Other calendars" -> "+" -> "From URL"); Apple Calendar
  via the Pages buttons or File -> New Calendar Subscription; Outlook via "Add calendar" ->
  "Subscribe from web". Paste the URL from the table
- Hide a category: toggle its calendar off, or unsubscribe. Subscribe per bucket for control
- Alerts / invite people: subscribed calendars are read-only and clients drop per-event edits on
  refresh. Copy the event into your own calendar (Google: open it -> "Copy to my calendar";
  Apple: drag it onto your calendar or duplicate it), then add alerts and guests there
- Every event carries time, duration, location with a map pin, a details link, a tickets link
  when there is one, and the source. Events are marked free (TRANSP:TRANSPARENT) so they never
  show you as busy
- Google polls subscribed URLs every ~8-24h on its own schedule. Apple honors the 3h
  `REFRESH-INTERVAL`

## Sources

| Source | Adapter | Feeds | Notes |
| --- | --- | --- | --- |
| [SF Fleet Week](https://fleetweeksf.org/calendar-of-events/) | `fleetweeksf` | `fleet-week` | Official schedule; descriptions joined from the map page |
| [DoTheBay](https://dothebay.com/events) | `dostuff` | all categories | The site's day-listing JSON. Music is gated to `popularity >= 50` or free; exhibits (ongoing, or spanning >3 days) are dropped |
| [Funcheap SF](https://sf.funcheap.com/) | `jsonld` | all categories | WordPress API discovery of the last 45 days of posts, then schema.org `Event` JSON-LD from each post page. Only new or edited posts are fetched; the first run backfills ~1.5k pages and takes a few minutes |
| [CalDiscovery](https://caldiscovery.com/san-francisco-ca/) | `ics` | `arts-community` (festival), `comedy` | Any ICS feed, one bucket per feed, RRULEs expanded in the window. CalDiscovery republishes ~80 venue and org calendars under ODbL 1.0 |

Keyword routing (`[[bucket_rules]]` in `sfcal.toml`) moves any timed event matching
`\b(fleet week|blue angels|parade of ships)\b` from any source into `fleet-week`. All-day
matches are dropped as umbrella listings.

Same event on several sources is published once, from the highest `priority` source, with the
others under "Also listed on". Two listings are the same event when they share a normalized
title and date, or when they come from different sources, start at the same minute and at
least half the words of the shorter venue name match (`TJPA Salesforce Park` /
`Salesforce Park`). The second rule is what folds Funcheap's own Fleet Week series into the
official schedule.

## How it works

`.github/workflows/build.yml` runs at `:17` every 3 hours (off the top of the hour, when GitHub
delays and drops scheduled runs), on demand, and on pushes that touch `sfcal/` or `sfcal.toml`.

- `sfcal build` loads `events.json` from the `feeds` branch, refreshes yesterday..+14d from every
  source, and once a day (whenever the last full run is >20h old) refreshes +60d
- Events outside the refreshed window are carried over; ended events are pruned after 7 days
- A source that errors, or suddenly returns nothing, keeps its last good events. The feeds are
  still written and committed, then the job exits 3 and fails so GitHub emails you
- Output is deterministic and `SEQUENCE` / `DTSTAMP` only move when an event's content does, so
  a run with nothing new commits nothing
- Every written `.ics` is re-parsed before it replaces the old one

Exit codes: `0` clean, `3` degraded (written, a source fell back), `1` fatal (nothing written).

Scheduled workflows in a public repo are auto-disabled after 60 days without repo activity. The
bot's `feeds` commits are expected to count; if GitHub ever disables it anyway you get an email,
and `gh workflow enable build.yml` fixes it.

## Development

```sh
make install                       # uv sync + prek hooks
make all                           # format, lint, ty, tests with coverage
make build                         # live build into site/ for preview
uv run sfcal check-source dothebay --full      # dry-run one source, writes nothing
gh workflow run build.yml -f full=true         # force a full refresh on Actions
```

Add a source: write an adapter in `sfcal/sources/` (subclass `Adapter`, implement `fetch`),
register it in `sfcal/sources/__init__.py`, add a `[sources.<name>]` table to `sfcal.toml`, and
capture a fixture for its tests.

Layout:

- `sfcal/sources/` -- one adapter per source format: `fleetweeksf`, `dostuff`, `jsonld`, `ics`
  (`icsfeed.py`)
- `sfcal/pipeline.py` -- windows, carry-over, SF filter, bucket rules, dedup, SEQUENCE ledger
- `sfcal/ics.py` -- RFC 5545 writer and validation
- `sfcal.toml` -- buckets, sources, rules
- `index.html` -- the Pages subscribe page (Pages serves `main` /)

Not affiliated with any listed source. Event details belong to their publishers. Contains
information from CalDiscovery, made available under the
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
