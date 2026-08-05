# RazMania Card Data — Front-End Build Brief

You are building the front end for **razmania.com**, a trading-card media site
(WordPress on GoDaddy). The card-sales data layer already exists and is live —
do not rebuild it, do not scrape anything, do not invent numbers. Read from the
API described below.

---

## 1. What the data is

A warehouse of **eBay completed card sales over $500**, refreshed **every day at ~05:00 ET**.
Covers baseball, football, hockey, basketball, soccer, WWE, Pokémon, One Piece,
Magic and Yu-Gi-Oh. Current load: ~21,800 sales in a rolling 7-day window,
~$31M of tracked volume.

- **Postgres on Render**, all heavy aggregation pre-computed in materialized views
- **FastAPI read API** — every endpoint reads a pre-built view, so responses are fast and cheap
- **A WordPress plugin already exists** (`razmania-cards`) with shortcodes and a
  same-origin proxy. Reuse it or replace it, but keep its rules.

**API base:** `https://razmania-cards-api.onrender.com` ← replace with the real
Render URL. Auth: send header `x-api-key: <key>` if one is configured.

All responses send `Cache-Control: public, s-maxage=1800, stale-while-revalidate=86400`.

---

## 2. NON-NEGOTIABLE data rules

Get these wrong and the site publishes false prices.

### 2.1 Best-offer sales are NOT sale prices
About **30% of listings over $500 are "best offer accepted."** On those, eBay
publishes the seller's **asking price**, never what the buyer actually paid.

The API already excludes them from every endpoint listed here — `/v1/leaderboard`,
`/v1/comps`, `/v1/players/*` and all aggregates return **confirmed sales only**.
You get 15,386 confirmed of 21,766 total.

**Only `/v1/sales` can return them, and only if you pass `confirmed_only=false`.**
If you ever do that, you must label those rows "asking price" in the UI. Never
average them. Never rank them.

### 2.2 Never publish a price backed by fewer than 3 sales
`/v1/comps` and `/v1/players/{slug}` already enforce `n >= 3`. Always display the
sale count next to any median. A price with no visible sample size is not
publishable.

### 2.3 Show the range, not just the median
Every comp returns `p25` and `p75`. Display as "typical range" — it is the middle
half of sales. A single number implies a precision this data does not have.

### 2.4 "Unknown" is a real vertical
~17% of rows have no team, player or sport keyword in the title and classify as
`Unknown`. **Do not render "Unknown" as a category to users.** Filter it out of
category lists, or bucket it as "Other". Never label it as a sport.

### 2.5 Always show the as-of date
Data refreshes daily. Every module must display the window (`first_date`–`last_date`
from `/v1/stats`). A card-value page with an invisible date is worse than no page.

**Check `/v1/health` before rendering prices.** It returns `fresh` (bool),
`days_stale` (int) and `last_successful_refresh`. If `fresh` is false, show a
"data updating" note rather than presenting stale prices as current. The refresh
runs daily at ~05:00 ET, so `days_stale` should normally be 0 or 1.

---

## 3. API reference (exact shapes, captured live)

### `GET /v1/stats` — site-wide header numbers
```json
{"total_sales":21766,"confirmed_sales":15386,"total_gmv":30941535.96,
 "biggest_sale":576350.0,"first_date":"2026-07-28","last_date":"2026-08-03",
 "players_tracked":111,"generated_at":"2026-08-03T20:40:26Z"}
```

### `GET /v1/verticals` — HOTTEST MARKETS (this week vs last week)
```json
{"verticals":[
 {"vertical":"Pokemon","sales":5366,"gmv":12945823.63,"median_price":1130.99,
  "top_sale":576350.0,"prior_sales":null,"prior_gmv":null,"prior_median":null,
  "gmv_pct_change":null,"median_pct_change":null}]}
```
Sorted by `gmv` desc. **`prior_*` and `*_pct_change` are `null` until two full
weeks are loaded** — handle null, do not render "NaN%" or "0%". Show "—".

### `GET /v1/leaderboard?vertical=&limit=&offset=` — BIGGEST SALES (7 days)
```json
{"total":15386,"limit":2,"offset":0,"results":[
 {"rank":1,"rank_in_vertical":1,"item_id":"236962688405",
  "title":"Pop 2 BGS 10 Rayquaza Gold Star 1st Ed - Clash of the Blue Sky 067/082 Pokemon",
  "vertical":"Pokemon","total_price":576350.0,"sold_date":"2026-07-29",
  "listing_format":"Buy It Now","bids":null,"grade_label":"BGS 10","player":null,
  "url":"https://www.ebay.com/itm/236962688405","image_url":"https://i.ebayimg.com/..."}]}
```
Use `rank` for the all-category list and `rank_in_vertical` when `vertical` is set.
`limit` max 500. Every row has a real card image — use it.

### `GET /v1/daily?vertical=&days=` — trend series for charts/sparklines
```json
{"series":[{"vertical":"Pokemon","sold_date":"2026-08-01","sales":1492,
 "confirmed_sales":1135,"gmv":2352609.82,"median_price":985.48,
 "avg_price":2072.78,"max_price":296659.71}]}
```
Plot `median_price` or `gmv`. **Do not plot `avg_price`** — single mega-sales
distort it badly (avg $2,072 vs median $985 on the same day).

### `GET /v1/players?q=&limit=&offset=` — player index
```json
{"results":[{"player":"Michael Jordan","vertical":"Basketball","sales":576,
 "gmv":1449047.84,"median_price":941.135,"top_sale":230000.23,
 "last_sold":"2026-08-03","slug":"michael-jordan"}]}
```
`slug` is the canonical URL segment. Use `/card-values/<slug>/`.

### `GET /v1/players/{slug}` — full player page in ONE call
```json
{"player":{...as above...},
 "comps":[{"card_key":"7f8ac967...","player":"Michael Jordan","card_year":1990,
   "brand_set":"Fleer","card_number":"26","parallel":null,"grade_label":"PSA 10",
   "is_rookie":false,"is_auto":false,"sales":11,"median_price":780.99,
   "p25":752.69,"p75":813.5,"min_price":725.99,"max_price":872.0,
   "last_sold":"2026-08-02","image_url":"...","sample_url":"..."}],
 "recent_sales":[{"item_id":"...","title":"...","total_price":...,"sold_date":"...",
   "grade_label":"...","listing_format":"...","url":"...","image_url":"..."}]}
```
Returns 404 for an unknown slug. One request per page — do not fan out.

### `GET /v1/comps?player=&grade=&limit=` — card-level comps
### `GET /v1/search?q=&limit=` — fuzzy title search (min 3 chars)
```json
{"results":[{"item_id":"287499823126","title":"1st Edition Charizard",
 "vertical":"Pokemon","total_price":784.54,"sold_date":"2026-08-02",
 "grade_label":"Raw","url":"...","image_url":"...","score":0.4545}]}
```
### `GET /v1/sales?vertical=&min_price=&max_price=&date_from=&date_to=&confirmed_only=&limit=&offset=`
Raw rows for ad-hoc filtering. `confirmed_only` defaults to `true` — leave it.

### `GET /v1/health` — freshness gate, call before rendering prices
```json
{"ok":true,"fresh":true,"last_sale":"2026-08-03","rows":21766,"days_stale":1,
 "last_successful_refresh":{"finished_at":"2026-08-03T20:40:23Z","status":"ok",
 "rows_inserted":0,"rows_updated":21766}}
```

---

## 4. What to build

### Priority 1 — Homepage modules
1. **Biggest Sales This Week** — `/v1/leaderboard?limit=25`. Image, title, price,
   category, sold date, format (+ bid count for auctions), link to eBay
   (`rel="nofollow noopener" target="_blank"`). This is the flagship module.
2. **Hottest Markets** — `/v1/verticals`. Table or card grid: category, sales,
   total value, median, biggest sale, WoW change (green/red once non-null).
   **Filter out `Unknown`.**
3. **Stat bar** — `/v1/stats`. Confirmed sales tracked, total value, biggest sale,
   players tracked. Plus the as-of date.

### Priority 2 — Category pages (`/pokemon/`, `/baseball/`, …)
`/v1/leaderboard?vertical=Pokemon&limit=50` + `/v1/daily?vertical=Pokemon&days=90`
for a median-price trend line. One page per vertical, minus `Unknown`.

### Priority 3 — Player value pages (`/card-values/<slug>/`)
This is the SEO engine. Keyword research showed **~180,000 monthly searches on
"<player> rookie card value"-type terms, most at difficulty 0–2.** One page per
player from `/v1/players`, each built from a single `/v1/players/{slug}` call.

Page structure that matches search intent:
- H1: "<Player> Card Values"
- Stat bar: top sale, median, sales tracked
- Table: card / grade / median / typical range (p25–p75) / sales count
- "Recent sales" list with images
- FAQ block answering "how much is a <player> card worth", "does grading matter"
- CTA to the RazMania Midwest Sports & Trading Card Festival (Pontiac, MI)

### Priority 4 — Search page
`/v1/search?q=` with debounced input.

---

## 5. Hard technical requirements

### 5.1 Render server-side. This is not negotiable.
The numbers must be in the HTML **before** JavaScript runs. If prices only appear
client-side, Google may not index them and the entire SEO thesis fails. In
WordPress that means PHP renders the tables. Client-side JS is fine for *extra*
interactivity (sorting an already-rendered table, live search), never for the
primary content.

### 5.2 Cache every API response server-side
GoDaddy shared hosting will otherwise make one outbound HTTP call per pageview.
Use WordPress transients, 30-minute TTL, plus a 7-day stale copy served if the
API is unreachable. **An API outage must degrade to slightly-old numbers, never
to a broken page or a blank module.** The existing plugin already does this.

### 5.3 Never expose the API key to the browser
The existing plugin proxies at `/wp-json/razmania/v1/<endpoint>` — same origin,
no CORS, key stays server-side. Use it for any client-side calls.

### 5.4 Schema markup
- Leaderboard → `ItemList` JSON-LD
- Player pages → `FAQPage` JSON-LD for the Q&A block
- Include `Dataset` markup on data pages with `temporalCoverage`

### 5.5 Images
`image_url` points at eBay's CDN (`i.ebayimg.com`), 1600px. Always `loading="lazy"`,
always a real `alt` from the title, always constrain dimensions. Do not hotlink at
full size in a grid.

---

## 6. Editorial angles the data supports

- **Pokémon is 30% of the entire $500+ card market** and holds the top 3 sales.
  Bigger than baseball and football combined. That is the recurring story.
- Basketball holds the two largest *sports* card sales ($365k LeBron Exquisite
  RC auto, $230k 1986 Fleer Jordan auto).
- Auction vs Buy-It-Now behaviour differs sharply by category — `listing_format`
  and `bids` are on every row.
- The data updates daily, so "Biggest Sales" is a genuinely live module — treat
  it as a returning-visitor hook, not a static list. A daily or weekly recap post
  writes itself from `/v1/leaderboard`.

---

## 7. Do NOT claim

- **Do not claim complete coverage.** Several price bands hit a 3,000-row scrape
  cap, so the low end ($500–900) is a recent-first sample. eBay does roughly
  2,007 sports-card sales over $500 *per day*, so the true population is larger
  than what is stored. Say "tracked sales", not "all sales".
- **Do not present medians as appraisals.** Say "market read", not "value".
- **Do not show `Unknown` as a sport.**
- **Do not average `avg_price`** into anything user-facing — use medians.
- **Do not claim price history.** eBay only exposes ~90 days of sold data and
  there is no backfill; history accrues from launch forward. No "5-year chart".

---

## 8. Definition of done

- [ ] Homepage renders biggest sales + hottest markets + stat bar, server-side
- [ ] Every price module shows sample size and as-of date
- [ ] `Unknown` filtered from all user-facing category lists
- [ ] Null `*_pct_change` renders as "—", not "NaN%" or "0%"
- [ ] API responses cached server-side; API down = stale data, not a broken page
- [ ] API key never appears in browser-visible source
- [ ] Player pages have FAQ + ItemList schema and pass Rich Results Test
- [ ] Mobile: tables scroll or reflow, images lazy-load
- [ ] Lighthouse SEO ≥ 95 on a player page
