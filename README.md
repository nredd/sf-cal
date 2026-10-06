# sf-cal

Subscribable calendars of San Francisco events, scraped from several sources every 3 hours by
GitHub Actions. No server: the feeds are plain `.ics` files on the `feeds` branch, served by
`raw.githubusercontent.com`.

One-click subscribe page: **https://nredd.github.io/sf-cal/**

## Feeds

| Feed | What | Google | URL (Apple, Outlook, anything else) |
| --- | --- | --- | --- |
| All | Every bucket below in one calendar, except pending street events | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/all.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/all.ics` |
| Fleet Week | SF Fleet Week: official concerts, ship tours, air shows, plus side events | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/fleet-week.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/fleet-week.ics` |
| Music | Concerts and live music | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/music.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/music.ics` |
| Comedy | Stand-up, improv and comedy shows | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/comedy.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/comedy.ics` |
| Stage | Theatre, dance and performing arts | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/stage.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/stage.ics` |
| Film | Screenings and film events | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/film.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/film.ics` |
| Nightlife | DJ sets, parties and LGBTQ nights | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/nightlife.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/nightlife.ics` |
| Food & Drink | Tastings, food events and pop-ups | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/food-drink.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/food-drink.ics` |
| Outdoors | Outdoor recreation and sports | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/outdoors.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/outdoors.ics` |
| Arts & Community | Arts, family, lectures, festivals, markets and everything else | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/arts-community.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/arts-community.ics` |
| City Hall Lights | What color City Hall is lit tonight, and why | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/city-hall-lights.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/city-hall-lights.ics` |
| Street Events | Permitted street closures: block parties, street fairs, night markets, Sunday Streets | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/street-events.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/street-events.ics` |
| Street Events (Pending) | Street closures still awaiting an SFMTA permit; some never happen. Not in All | [Add](https://calendar.google.com/calendar/u/0/r?cid=webcal://raw.githubusercontent.com/nredd/sf-cal/feeds/street-events-pending.ics) | `https://raw.githubusercontent.com/nredd/sf-cal/feeds/street-events-pending.ics` |

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
| [SF.gov City Hall](https://www.sf.gov/location--san-francisco-city-hall) | `cityhall` | `city-hall-lights` | The page's month-by-month lighting list, one all-day event per lit night. Only the current month is posted, past nights carry over. Disabled: WAF-blocked on Actions |
| [Robelius/sf-city-hall-lighting-calendar](https://github.com/Robelius/sf-city-hall-lighting-calendar) | `ics` | `city-hall-lights` | Stopgap for the above: a third-party repo scraping the same page with a headless browser, refreshed on the 1st/2nd of the month and Mondays. Titles are `CHC: <colors>`, the honoree is in the description |
| [DataSF street closures](https://data.sf.gov/d/8x25-yybr) | `streetclosures` | `street-events`, `street-events-pending` | SFMTA closure permits with `type = 'Special Event'`, one SODA query per source: `Permitted` into `street-events`, every not-yet-permitted status into `street-events-pending`. Every event notes its permit status. Segments are grouped into one event per permit occurrence; closures over 24h (umbrellas) and `corporate` names are skipped |
| [DoTheBay](https://dothebay.com/events) | `dostuff` | all categories | The site's day-listing JSON. Music is gated to `popularity >= 50` or free; exhibits (ongoing, or spanning >3 days) are dropped |
| [Funcheap SF](https://sf.funcheap.com/) | `jsonld` | all categories | WordPress API discovery of the last 45 days of posts, then schema.org `Event` JSON-LD from each post page. Only new or edited posts are fetched; the first run backfills ~1.9k pages and takes a few minutes |
| [CalDiscovery](https://caldiscovery.com/san-francisco-ca/) | `ics` | `arts-community` (festival), `comedy` | Any ICS feed, one bucket per feed, RRULEs expanded in the window. CalDiscovery republishes ~80 venue and org calendars under ODbL 1.0 |
| [SF Civic Center](https://sfciviccenter.org/events/) | `ics` | `arts-community` | The CBD's Events Calendar export. `max_span_days = 3` drops the season-long umbrellas, leaving plaza one-offs (Fall Family Festival, the tree lighting) |
| [Yerba Buena Gardens Festival](https://ybgfestival.org/) | `ics` | `music` | Free outdoor music and dance, May to November |
| [SF Rec & Park](https://sfrecpark.org/iCalendar.aspx) | `ics` | `outdoors`, `music` (bandshell) | CivicPlus calendars `catID=14` (main), `35` (Golden Gate Bandshell), `40` (UN, Fulton and Civic Center plazas, mostly fitness classes) |

Keyword routing (`[[bucket_rules]]` in `sfcal.toml`) moves any timed event matching
`\b(fleet week|blue angels|parade of ships)\b` from any source into `fleet-week`. All-day
matches are dropped as umbrella listings. A source with `apply_bucket_rules = false` skips the
rules entirely; both City Hall lighting sources do, so a Fleet Week lighting night stays put.

SF only: a source with `sf_only = true` (the default) keeps an event when its geo is inside
lat 37.70..37.83, lon -122.52..-122.35, or its locality is `San Francisco` (trimmed,
case-insensitive). Events with no location at all are kept. `fleetweeksf` sets
`sf_only = false`.

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

## Changing feeds

- Feed URLs come from the bucket keys in `sfcal.toml`. Renaming or removing a bucket breaks
  every subscription to it. Don't; retire a bucket by leaving it empty
- Adding a bucket: add it to `sfcal.toml`, then a row to the `README.md` table and a `<li>` to
  `index.html`. `tests/test_docs.py` fails until all three agree
- Re-categorizing is safe: buckets, the SF filter, rules and dedup are recomputed from
  `events.json` on every run, so a `sfcal.toml` change applies everywhere on the next build
  with no re-fetch

## When the build fails

GitHub emails on a failed `build` run. The feeds were still published unless the exit code
was 1.

1. Open the run log. The `Sources:` block names the failed source and its error
2. Reproduce locally: `uv run sfcal check-source <name> --full`. It writes nothing
3. `403` / Cloudflare page: the site is blocking us. Try the request with `curl -A` and the
   `USER_AGENT` from `sfcal/__init__.py`. If it only fails from Actions IPs, set
   `enabled = false` for that source
4. `SourceError` / parse error / "returned no events in window": the page or API changed.
   Re-capture the fixture into `tests/fixtures/`, update the adapter until its tests pass
   against the new fixture, push
5. `sf-city-hall-lights` failing with "No lighting schedule found" from Actions but not locally
   is sf.gov's AWS WAF challenging GitHub's IPs. Set `enabled = false` for it
6. To stop the emails while you fix it, set `enabled = false` for the source and push. Its
   events drop out of the feeds on the next build
7. Exit 1 (fatal): bad `sfcal.toml`, corrupt `events.json`, or a feed that failed validation.
   Nothing was written. `make build` locally shows the same error

The cron got disabled (GitHub emails that too): `gh workflow enable build.yml -R nredd/sf-cal`.

## Known limitations

- About 3 Fleet Week events still appear twice: Funcheap's copy names the venue too
  differently to match safely (e.g. Marines' Memorial Theatre vs the band name)
- Funcheap music isn't popularity-gated like DoTheBay's, so `music` runs ~60 events a week
- DoTheBay's `/events/YYYY/M/D.json` is undocumented and can change or vanish without notice
- `fleetweeksf` scrapes page markup that's reused year to year, but a redesign breaks it
- `events.json` is ~3.4 MB and is re-committed whenever anything changes, so the `feeds`
  branch grows by a few MB a day while events churn
- Google refreshes subscriptions every ~8-24h no matter what the feed asks for
- sf.gov sits behind AWS WAF, which challenges GitHub Actions IPs but not home IPs. So
  `sf-city-hall-lights` is `enabled = false` and `city-hall-lights` comes from
  `city-hall-lights-ics`, a third-party repo that can lag sf.gov by up to a week or stop
  updating. `uv run sfcal check-source sf-city-hall-lights --full` still works locally
- Street-event times are SFMTA permit times, so they include setup and teardown. Pending
  applications only show in `street-events-pending`, which is kept out of `all.ics` with
  `in_all = false` on the bucket. A permit can land days before the event
- SF Civic Center's feed is mostly umbrellas and yields a handful of one-offs, most with no
  LOCATION (they're on the Civic Center plazas)
- The Rec & Park bandshell feed is empty off-season; that's not a failure, since it never had
  events in the window

## Development

```sh
make install                       # uv sync + prek hooks
make all                           # format, lint, ty, tests with coverage (rewrites files)
make check                         # same gate without rewriting; this is what CI runs
uv run tox                         # tests on 3.13 and 3.14
make build                         # live build into site/ for preview
uv run sfcal check-source dothebay --full      # dry-run one source, writes nothing
gh workflow run build.yml -f full=true         # force a full refresh on Actions
```

Add a source: write an adapter in `sfcal/sources/` (subclass `Adapter`, implement `fetch`),
register it in `sfcal/sources/__init__.py`, add a `[sources.<name>]` table to `sfcal.toml`, and
capture a fixture for its tests.

Gotchas:

- `USER_AGENT` in `sfcal/__init__.py`: DoTheBay returns 403 for any UA containing `+https://`
  (the usual bot-UA convention). Keep it as `sf-cal/<version> (github.com/nredd/sf-cal)`
- `icalendar` is pinned to 6.x and Dependabot ignores its majors. 7.x changes the typed
  property API the writer and tests rely on; move deliberately
- `make all | tail` hides failures. Use `set -o pipefail` if you pipe the gate

Layout:

- `sfcal/sources/` -- one adapter per source format: `fleetweeksf`, `dostuff`, `jsonld`, `ics`
  (`icsfeed.py`), `cityhall`, `streetclosures`
- `sfcal/pipeline.py` -- windows, carry-over, SF filter, bucket rules, dedup, SEQUENCE ledger
- `sfcal/ics.py` -- RFC 5545 writer and validation
- `sfcal.toml` -- buckets, sources, rules
- `index.html` -- the Pages subscribe page (Pages serves `main` /)

Not affiliated with any listed source. Event details belong to their publishers. Contains
information from CalDiscovery, made available under the
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
