# RazMania Card Data Platform

**Deploying? Start with `DEPLOY.md`.**

eBay card-sales warehouse that powers razmania.com (WordPress on GoDaddy),
hosted on Render.

```
Apify (DAILY scrape)
      │
      ▼
Render Cron  ──▶  Render Postgres  ──▶  Render Web Service (FastAPI)
 scrape+load        raw + materialized        read-only JSON API
                    views (all the                    │
                    heavy aggregation)                ▼
                                          WordPress plugin (PHP)
                                          server-side render + transient cache
                                                      │
                                                      ▼
                                                 razmania.com
```

## The aggregation split

The rule: **anything that scans the whole table happens in Postgres, once per
refresh. The front end only formats and slices what it was handed.** A page
request must never trigger an aggregation.

| Work | Where | Why |
|---|---|---|
| Median / percentile / GMV / sell-through per vertical | **Postgres** (`mv_daily_vertical`, `mv_vertical_wow`) | Full-table scans. Seconds in SQL, impossible per-request. |
| 7-day leaderboard + rank | **Postgres** (`mv_leaderboard_7d`) | Window functions over the full set. |
| Per-card comps with the n≥3 rule | **Postgres** (`mv_card_comps`) | The rule is enforced in SQL so no client can bypass it. |
| Player rollups + slugs | **Postgres** (`mv_player_summary`) | URL slugs generated once, not per request. |
| The index: trailing-7d median rebased to 100, per vertical + composite | **Postgres** (`mv_market_index`, `index_base`) | Needs a 7-day window at every date, a pinned base and a settled cutoff. None of that can be redone per request. |
| Excluding best-offer rows **and rows below the publish floor** | **Postgres** (`is_publishable` generated column) | Defined in exactly one place so API, views and site can never disagree. |
| Sorting/filtering a fetched page, tab switching, search-as-you-type over loaded rows | **Front end** | Zero-latency, no network. |
| Currency/date formatting, sparkline drawing, responsive tables | **Front end** | Presentation. |
| Full-text search across all sales | **Postgres** (`pg_trgm`) | Needs the index. Exposed as `/v1/search`. |

`is_publishable` is the load-bearing piece. It is a **generated column**:
`NOT best_offer_accepted AND NOT is_junk AND total_price >= publish_floor`.
About 30% of listings are best-offer-accepted, and on those eBay publishes the
seller's **asking price, not what was paid**. Every view filters on this column,
so neither a best-offer price nor a partially-collected price range can reach
the site by accident.

## The RazMania Index

A trailing-7-day median of confirmed sales, rebased to 100, per vertical plus a
fixed-weight composite. `mv_market_index` + `index_base` + `v_index_windows`,
served at `/v1/index`, rendered by `[razmania_index]`.

### Two tiers, because the cadence is the lag

An index cannot settle a day until every tier has swept it, so how often you
scrape *is* how far behind you publish. That gives two honest products off one
piece of machinery:

| Tier | Range | Scraped | Settles in | What it is |
|---|---|---|---|---|
| `bluechip` | `$10,000+` | daily | **2 days** | The live, quotable number. ~9% of volume. |
| `all` | `$2,000+` | every 3 days | **4 days** | The broad market read. |

`bluechip` is free — that tier was already scraped daily for the leaderboard. It
is also the narrower index: at ~68 sales/day it will support the composite and a
handful of big categories, not the full list. That is the honest outcome of the
n≥20 rule below, not something to tune away.

**The lag is config-driven, not hardcoded.** `refresh_daily.sh` writes
`hot_floor` and the two settle values into `schema_meta` on every run, derived
from `HOT_FLOOR` and `TAIL_EVERY`. Change the cadence and the published lag
follows by itself. A literal offset in SQL would silently publish unsettled days
the first time the cadence moved — which is the exact failure the design exists
to prevent, so there isn't one anywhere in the file.

### The four rules that keep it honest

**1. Recent days are withheld.** A tier's newest days are incomplete until its
next scrape lands. For `all`, those days hold the hot tier only — the most
expensive 9% — so they read high, then "correct". Published as a chart, that is
a sawtooth showing the hobby doubling and crashing, manufactured entirely by the
scrape schedule. Measured on synthetic data calibrated to the real split, the
withheld distortion is **+9%** at the current 3-day cadence and was **+100%**
under the old weekly one. Every row carries `settled`; the API serves nothing
else unless you ask. `/v1/leaderboard` stays live, because only `$10,000+` sales
reach it. **A page showing both must say which is which.**

**2. The base is pinned, not recomputed.** `index_base` stores each
`(tier, vertical)`'s base date, base median and composite weight, written once by
`pin_index_base()` and never again. Without this, `db/retention.sql` pruning past
400 days would eventually delete the base window and silently rebase every
historical value — an index whose history changes underneath it is not an index.
The table is append-only: **updating or deleting a row rewrites published
history.**

**3. Thin verticals get a gap, not a line.** A window needs n≥20, defined once in
`v_index_windows` and read by both the pin function and the view. Card prices are
roughly lognormal with σ≈0.78, so the standard error of a weekly median is ±44%
at n=5 and ±22% at n=20 — at n=5 a category prints +41% then −49% while nothing
happened. `Unknown` (~17% of rows) is excluded outright: it tracks
`etl/classify.py`, not the hobby.

**4. The composite is fixed-weight,** because a pooled median moves when the
*mix* moves. Doubling one vertical's volume without touching a price shifts a
pooled median by ~9% and the composite by **0.00**.

**5. The calendar is not trusted on its own — every window must also *look like*
the base period.** A window publishes only if its `$10,000+` share is at most
2.5× the base window's. This exists because of what production data showed on
launch day: on **17 Aug 2026 the scraper's tail collection silently fell to
~15% of normal** (four price bands returning an identical ~36 rows — a
pagination failure in the Apify actor), the hot tier to ~50%, and nothing
noticed for sixteen days because the freshness gate only checks staleness.
Every broad window since was "settled" by the calendar and ~30% hot-tier in
fact; the composite read **188 with a −45% week**. The composition guard
withholds exactly those windows (`complete = false`), and the plugin shows the
tier as *suspended* rather than as a stale number. Reproduced in the test suite:
starving the tail to 15% for 14 days pulls the broad frontier back 9 days and
flags 62 windows that would have read ~214, while Blue Chip — 100% hot by
construction — is untouched. `refresh_daily.sh` now also fails the run when
7-day volume drops below 40% of the prior month, so a collapse is loud.

### Two ways to break it

**Moving a price floor invalidates that tier's base.** This matters, because this
README plans `MIN_PRICE=500` for player pages. The pinned base medians were
measured over `$2,000+` sales; the day the floor drops, every window starts
including `$500–1,999` sales and the median falls off a cliff — the index prints
a catastrophic crash caused entirely by a config change. A floor move means
**deliberately relaunching**: delete that tier from `index_base`, let
`pin_index_base()` re-pin, and say publicly that the series was rebased. Changing
`HOT_FLOOR` does the same to `bluechip`.

**Changing the cadence without the window.** `refresh_daily.sh` derives the
window floor from `TAIL_EVERY`. The old code fixed it at 8 days; running that on
a 3-day cadence would cost ~$136/mo — worse than daily, for a worse lag.

### What it needs before it says anything

**13 days of `sold_date` coverage** for `bluechip` (7 for the first window, 2 to
settle, plus the n≥20 warm-up) and **11+ for `all`** — call it two weeks either
way, and ~6 weeks before the chart reads as a trend. The shipped seed is only a
7-day snapshot, so the curve comes from live refreshes. Since eBay exposes only
~90 days of sold data, history not collected now cannot be bought later.

## Cost

| Service | Plan | Cost |
|---|---|---|
| Render Postgres | `basic-256mb` + 5 GB storage | **$7.50/mo** |
| Render Web Service (API) | `starter` | **$7/mo** |
| Render Cron (daily) | `starter`, ~15 min/day | **~$0.75/mo** |
| Apify scrape | `MIN_PRICE=2000`, tiered cadence | **~$78/mo** |
| **Total** | | **~$93/mo** |

### Apify is the real cost — and it scales with rows, not runs

Apify bills per row **returned**. The `item_id` upsert dedupes perfectly, but
only *after* Apify has billed. Three levers exist: the **price floor**, the
**window width**, and **how often each price range is scraped**.

#### The measurement that set the config

Every one of the **top 50 weekly sales came from the `$10,000+` slice** — and
that slice is 9% of the volume:

| Slice | Sales/day | Share | In the top 50? |
|---|---|---|---|
| **$10,000+** | 68 | 9% | **all 50** |
| $5,000–9,999 | 134 | 18% | none |
| $3,000–4,999 | 215 | 29% | none |
| $2,000–2,999 | 323 | 44% | none |

The other 91% only moves medians and GMV — and a median does not change
materially between Tuesday and Wednesday.

#### So the cadence is tiered

| Slice | Cadence | Window | Rows/mo | Cost |
|---|---|---|---|---|
| `$10,000+` (`HOT_FLOOR`) | **daily** | 2 days | 4,080 | **$10/mo** |
| `$2,000–9,999` | **every 3 days** (`TAIL_EVERY`) | 4 days | 27,197 | **$68/mo** |
| | | | | **$78/mo** |

Flat daily-everything is **$112/mo**. The saving is entirely from not re-buying
yesterday's cheap rows, not from dropping data.

Set `TAIL_EVERY=0` to switch the tail off entirely: **~$10/mo**, leaderboard
unchanged, no medians, and the broad index publishes nothing.

#### Why every 3 days and not weekly

Cost is set by **redundancy**, not volume. A cadence of `k` days needs a `k+1`
day window, so the redundancy factor is `1 + 1/k` — which barely moves until `k`
gets small:

| `TAIL_EVERY` | Window | Redundancy | Tail cost | Total platform | Index lag |
|---|---|---|---|---|---|
| 7 | 8 days | 1.14× | $58/mo | $83/mo | **9 days** |
| **3 (current)** | 4 days | 1.33× | $68/mo | **$93/mo** | **4 days** |
| 2 | 3 days | 1.50× | $77/mo | $102/mo | 3 days |
| 1 | 2 days | 2.00× | $102/mo | $127/mo | 2 days |

**Weekly was the right answer on cost alone, and the wrong one once the index
existed.** The index cannot settle a day until this tier has swept it, so the
cadence *is* the lag. Going from weekly to every 3 days costs **$10/mo** and cuts
the lag from 9 days to 4. Measured on synthetic data calibrated to the real
9/91 split, it also shrinks the withheld distortion from **+100% to +9%** — an
11× reduction in how wrong the recent, unpublished days are.

Going all the way to daily costs **$34/mo more** for two further days, because at
`k=1` every row is bought twice purely as overlap insurance. Not worth it.

**Never change the cadence without the window.** The window floor is derived from
`TAIL_EVERY` in `refresh_daily.sh`. The old code floored it at 8 days regardless;
running that every 3 days would cost **~$136/mo** — worse than daily, for a worse
lag.

#### Floor economics, if you ever move it

| `MIN_PRICE` | Sales/day | Daily-everything | With tiered cadence |
|---|---|---|---|
| **2000 (current)** | 739 | $111/mo | **$68/mo** |
| 1000 | 1,477 | $222/mo | ~$130/mo |
| 500 | 3,109 | $466/mo | **~$279/mo** |

Raising the floor is **not** free sampling — it truncates the distribution and
biases every median upward (Pokemon median $1,131 at $500+ vs $2,282 at
$1,000+). That is why the floor is enforced a second time in the database as
`publish_floor` in `db/schema.sql`: rows below it are stored but can never reach
a published aggregate, so the site cannot show a median over a range it only
partially collects.

**Move the floor to $500 when player value pages ship.** At $2,000+ only 17
cards clear the n>=3 comps rule and 72 players have data; at $500+ it was 111
players and far more comps. The SEO engine needs the tail; the homepage does not.
Change `MIN_PRICE` in `render.yaml` **and** `publish_floor` in `db/schema.sql`,
then re-run that file.

Every scrape prints `COST: N rows x $0.0025 = $X` so a config regression that
triples spend is visible in the first log line, not the monthly invoice.

Do **not** use Render's free plans here: free Postgres **expires after 30 days**,
and a free web service **sleeps after 15 minutes** with a ~1 minute cold start —
which would show visitors a loading page.

## Deploy

1. Push this repo to GitHub.
2. Render → **New → Blueprint** → select the repo. `render.yaml` creates the
   database, API and cron job.
3. In the cron job's settings, set `APIFY_TOKEN`. `MIN_PRICE` is already `2000`.
4. Apply the schema once:
   ```bash
   psql "$DATABASE_URL" -f db/schema.sql
   ```
   **After the first apply you never run this by hand again.**
   `etl/refresh_daily.sh` records the file's md5 in `schema_meta.schema_hash`
   and re-applies `db/schema.sql` on the next run whenever the hash changes —
   so a schema change ships by `git push` and lands at 05:00 ET, or immediately
   via **Trigger Run** on the cron job. The apply is idempotent: it rebuilds
   every materialized view (a few seconds of 500s on `/v1` at 05:00), and
   `index_base` is `CREATE TABLE IF NOT EXISTS`, so it **preserves the pinned
   index base** rather than rebasing published history.
5. Backfill the 21,766-row seed that ships with this repo. It is $500+ data,
   which is deliberate — it is already paid for, it feeds `/v1/search`, and it
   is there if you ever drop the floor. The publish floor keeps it out of every
   aggregate, so `/v1/stats` will correctly report 5,175 tracked, not 21,766:
   ```bash
   DATABASE_URL=... python3 etl/load.py --csv seed/sales_all.csv.gz
   ```
6. Copy the API's generated `API_KEY` from the Render dashboard.
7. Upload `wordpress/razmania-cards/` to `wp-content/plugins/`, activate it,
   then **Settings → RazMania Cards** and paste the API base URL and key.

## WordPress usage

**The index ships as its own plugin, `wordpress/razmania-index/`.** The copy of
`razmania-cards` running on razmania.com has grown a design system, email
capture and theme integration that are not in this repository, so the index is
not a patch to that file — it is a second plugin that reuses the first one's API
settings and its CSS tokens (`--ink`, `--bg`, `--gold`, `--up`, `--down`) and
depends on nothing else.

```
[razmania_index_page]            the landing page: masthead, both tiers, tape, charts, methodology, citation
[razmania_index]                 broad index ($2,000+)
[razmania_index tier="bluechip"] blue-chip index ($10,000+)
[razmania_index_hero]            the two headline numbers — homepage
[razmania_ticker]                the live tape — homepage
[razmania_index_methodology]     the academic section alone
```

`razmania-cards` (this repo's copy is behind production, but its shortcodes are unchanged):

```
[razmania_stats]
[razmania_verticals]
[razmania_leaderboard limit="25"]
[razmania_leaderboard vertical="Pokemon" limit="10" title="Biggest Pokémon sales this week"]
[razmania_player slug="michael-jordan"]
```

**Everything renders server-side in PHP.** That is deliberate: if the numbers
only appear after client-side JavaScript, Google may not index them, and the
entire SEO thesis for this project dies. Responses are cached in WordPress
transients for 30 minutes, with a 7-day stale copy served if the API is
unreachable — so an API outage degrades to slightly old numbers instead of a
broken page.

The plugin also exposes `/wp-json/razmania/v1/<endpoint>` as a same-origin
proxy, so interactive JS can filter and sort without CORS and without ever
seeing the API key.

## The widget store

`GET /widgets` on the API service is a configurator: pick a widget, set size,
theme, accent and options, watch the live preview, copy one line of HTML.
Everything is server-rendered into a self-contained document, so the host page
needs no JavaScript and nothing it can block. Routes live in `api/embeds.py`.

| Widget | Route | Options |
|---|---|---|
| The Index | `/embed/index` | `tier`, `category`, `days`, `chart` |
| Top Sales | `/embed/top-sales` | `period` (1–7 days), `category`, `count`, `images` |
| Market Movers | `/embed/movers` | `tier`, `count` |
| Live Comps | `/embed/comps` | `q` (card / player / set), `category`, `count`, `images` |
| Image badge | `/badge/index.svg` | `tier`, `category`, `theme` — for READMEs and signatures |

Common to all: `theme=light|dark|auto` (auto follows the visitor's OS), `size=s|m|l`
(text scale), `accent=RRGGBB`. Size is the iframe's own width/height; the store
offers presets, custom dimensions, and an auto-height snippet driven by a
6-line `postMessage` in the widget.

Rules that are not options:

- **The attribution link stays.** It is the reason the store exists. Every
  widget links to razmania.com with `utm_campaign=<widget>`, so the value of
  each widget is measurable in analytics.
- **The floor is printed on every widget.** The database cannot publish a
  number below it, but a widget on someone else's site can easily mislabel one.
- **These routes sit outside the `/v1` API-key guard on purpose** — they are the
  public product — and read the same materialized views, so no page view on
  any host site triggers an aggregation. They inherit the CDN cache headers.
- Every parameter is regex-bounded; the comps search term is the only free
  text and is HTML-escaped on output (a `<script>` in `q` comes back as text).

Vanity domain: point `widgets.razmania.com` at the API service in Render's
custom-domain settings and the store's generated snippets pick it up
automatically — they use the request's own host.

## API

| Endpoint | Purpose |
|---|---|
| `GET /v1/health` | Freshness gate: `fresh`, `days_stale`, `last_successful_refresh`. Also Render's health check. |
| `GET /v1/stats` | Site-wide header numbers. |
| `GET /v1/verticals` | Per-vertical, this week vs last week. |
| `GET /v1/leaderboard?vertical=&limit=&offset=` | Biggest confirmed sales, 7 days. |
| `GET /widgets`, `/embed/*`, `/badge/index.svg` | The widget store and its embeds (public, no key). See above. |
| `GET /v1/index?tier=&vertical=&days=&include_unsettled=` | The RazMania Index. `tier=all` (default, 4-day lag) or `tier=bluechip` (2-day lag). Settled points only unless you opt in. |
| `GET /v1/daily?vertical=&days=` | Daily series for charts. |
| `GET /v1/players?q=&limit=` | Player index. |
| `GET /v1/players/{slug}` | Player page: summary + comps + recent sales, one call. |
| `GET /v1/comps?player=&grade=` | Card-level comps (n≥3 only). |
| `GET /v1/search?q=` | Fuzzy title search. |
| `GET /v1/sales?...` | Raw rows for ad-hoc filtering. |

All responses carry `Cache-Control: s-maxage=1800, stale-while-revalidate=86400`.
The API connects as the read-only `razmania_read` role, so a leaked key or an
injection bug cannot write to the warehouse.

## Daily refresh

`etl/refresh_daily.sh` runs **every day at 05:00 ET** (`0 9 * * *` UTC), so the
homepage is current before US morning traffic:

1. **Adaptive window, per tier** — each tier tracks its own staleness from
   `max(sold_date)` within its price range. Hot gets `behind + 2` (capped 6); the
   `+2` is load-bearing, because eBay sales land around the clock and at 09:00 UTC
   a sale dated *today* has usually already landed — a 1-day window would
   permanently miss everything completing after each run. Tail gets `behind + 1`,
   floored at `TAIL_EVERY + 1` and capped at `TAIL_EVERY + 2`, and fires whenever
   it has drifted `TAIL_EVERY` days stale — so a missed run self-heals and the
   window can never be wider than the cadence needs.
2. `etl/scrape.py` — Apify. 2 bands on a hot day, 6 more on tail day, disjoint by
   construction. Retries 402/429/5xx with backoff and staggers starts, because a
   burst of simultaneous run-starts is enough to make Apify reject the lot.
3. `etl/load.py` — idempotent upsert on `item_id`, then `refresh_all_views()`
4. `db/retention.sql` — prunes past 400 days, then `VACUUM ANALYZE` (the daily
   upsert churns rows; without this the trigram search and date scans degrade)
5. **Freshness gate** — fails loudly if the newest sale is more than 2 days old
   or the leaderboard is empty. A refresh that "succeeds" while leaving stale
   prices on a card-value site is the failure mode that matters most.

Re-running any step is safe. `item_id` is the primary key, so overlapping
scrapes update rather than duplicate.

`GET /v1/health` exposes `fresh`, `days_stale` and `last_successful_refresh` so
the front end can show a warning instead of silently rendering old prices.

### Growth and retention

At ~739 sales/day the table grows ~270k rows and roughly 0.2 GB per year.
Render bills expandable storage at $0.30/GB. `db/retention.sql` keeps 400 days.
**Raise that number, never lower it** — eBay only exposes ~90 days of sold data,
so deleted history cannot be re-scraped.

## Scraper gotchas encoded in the code

- **eBay category IDs 213 / 215 / 216 are legacy and silently ignored** — they
  return unfiltered results with *no error*. Only `261328` and `183454` are
  verified to filter. A category ID that comes back with `category: null` is
  the tell that the filter was dropped.
- **The Actor rejects an empty keyword**, but any real keyword drops listings
  that omit that word. `-zzqqxx` (a nonsense *negative* keyword) excludes
  nothing and gives a true full-category browse.
- **The Actor OOMs past ~3,000 rows** in one run — hence price bands.
- Sport is inferred from the **title**, not the eBay category, since the
  category IDs can't be trusted. See `etl/classify.py`.

## Known gaps

- **Everything published is $2,000+.** That is a deliberate cost choice, not a
  bug — but it must be labelled on the site. Say "tracked sales over $2,000".
- **~17% of rows classify as `Unknown`** — titles with no team, player or sport
  keyword. Reduce by extending the player lists in `etl/classify.py`.
- `mv_card_comps` is thin at this floor (17 cards clear n≥3). Player value pages
  need `MIN_PRICE=500`. Homepage modules do not.
- Week-over-week columns stay `NULL` until two full weeks are loaded.
- **The scraper is under-collecting since ~17 Aug 2026** (see index rule 5).
  The broad index is self-suspended until it is fixed; the leaderboard and
  Blue Chip are running on roughly half their normal hot-tier volume. Likely an
  Apify actor pagination regression (the actor was modified ~23 Aug); eBay only
  exposes ~90 days of sold data, so the Aug 17→fix gap can still be backfilled
  with a wide window once the actor returns full pages again.
- **The index needs ~2 weeks of `sold_date` coverage** before a settled point
  exists, and ~6 weeks before the chart reads as a trend. At the $2,000 floor the
  thin verticals (Motorsport, WWE, Hockey) will not clear n≥20 and are correctly
  absent; `MIN_PRICE=500` brings them in with real sample sizes. The `bluechip`
  tier is narrower still — expect the composite plus a few big categories.
