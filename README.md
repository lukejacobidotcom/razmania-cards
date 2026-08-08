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

## Cost

| Service | Plan | Cost |
|---|---|---|
| Render Postgres | `basic-256mb` + 5 GB storage | **$7.50/mo** |
| Render Web Service (API) | `starter` | **$7/mo** |
| Render Cron (daily) | `starter`, ~15 min/day | **~$0.75/mo** |
| Apify scrape | `MIN_PRICE=2000`, tiered cadence | **~$68/mo** |
| **Total** | | **~$83/mo** |

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
| `$2,000–9,999` | **weekly** (`TAIL_DOW`) | 8 days | 23,278 | **$58/mo** |
| | | | | **$68/mo** |

Flat daily-everything was **$111/mo**. Same rows collected, redundancy drops
from **2.0x to 1.2x** — the saving is entirely from not re-buying yesterday's
cheap rows, not from dropping data. **$43/mo, $525/yr.**

Set `TAIL_DOW` above 6 to switch the tail off entirely: **~$10/mo**, leaderboard
unchanged, no medians.

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

## API

| Endpoint | Purpose |
|---|---|
| `GET /v1/health` | Freshness gate: `fresh`, `days_stale`, `last_successful_refresh`. Also Render's health check. |
| `GET /v1/stats` | Site-wide header numbers. |
| `GET /v1/verticals` | Per-vertical, this week vs last week. |
| `GET /v1/leaderboard?vertical=&limit=&offset=` | Biggest confirmed sales, 7 days. |
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
   floored at 8 and capped at 9, and fires on `TAIL_DOW` **or** any day it has
   drifted past 8 days stale, so a missed week self-heals.
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
