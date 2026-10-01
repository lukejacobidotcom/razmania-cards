# Firsts

A RazMania series that publishes **one to three card-market milestones a
day**, each as its own news article, the first time the warehouse sees the
market do something:

> First $100,000 sale of a Skyridge Charizard · First 2026 Football rookie
> card to sell for $20,000 · First day with 100 Pokémon sales over $2,000 · The 2,500th tracked
> Basketball sale over $2,000 · LeBron passes $2,500,000 in tracked sales ·
> Blue Chip · Football index closes above 150 for the first time

Plus the hobby's **all-time firsts** — the first card past $10,000, $100,000,
$1,000,000 and $10,000,000 in baseball, football, basketball and Pokémon —
researched by hand, one cell at a time.

Lives at **razmania.com/firsts/** (series page, shortcode `[razmania_firsts]`)
and **razmania.com/firsts/<id>-<slug>/** (one article per milestone), plugin
`wordpress/razmania-firsts`.

## The two halves, and why they are kept apart

| | Historical (all-time) | Tracked (daily) |
|---|---|---|
| The claim | "The first baseball card ever to sell for $10,000" | "The first $100,000 Charizard sale RazMania has tracked" |
| Source | Hand research, in `firsts/firsts.json`, with a status per cell | `record_firsts()` in `db/schema.sql`, from confirmed eBay sales over $2,000 |
| Reach | 1972 onward | 28 Jul 2026 onward |
| Served by | The plugin reads its own `data/firsts.json`; no API call | `/v1/firsts`, `/v1/firsts/log`, `/firsts/a/<id>` |
| Changes | Only by editing the JSON and re-uploading | Never: rows are append-only, and a first is never revised |

The warehouse cannot say anything about 1991 or 2000, so the historical half
is editorial. The tracked half is the honest thing the data *can* say, and it
generates new content on its own. Every tracked article says "tracked by
RazMania since 28 Jul 2026" and prints the floor. **A tracked first is never
described as "first ever"** — even a 2026 product may have sold before
collection began. And a first is stated for the thing it happened to: a headline names the
specific card, and the article's deck says separately whether the player or
character as a whole had crossed the line in tracking, and what hobby
history says (from the all-time table).

## The daily engine

Two functions, both run at the end of `refresh_all_views()`:

**`record_firsts()`** scans settled, confirmed sales and writes every first it
can find into `firsts_log` (`ON CONFLICT DO NOTHING`, so nothing recorded
ever changes). Kinds:

| Kind | What it detects | Example headline |
|---|---|---|
| `price:vertical` | first sale over each price line, per category and market-wide | First $250,000 Pokémon sale |
| `price:subject` | … per player / Pokémon character, in its home category. The headline names the card that did it, never the character as a whole | First $100,000 sale of a Skyridge Charizard |
| `price:card`, `price:card_grade` | … per card as the title parser saw it, with and without grade | First $50,000 sale of a 1986 Fleer Michael Jordan #57 · PSA 9 |
| `price:set`, `price:year`, `price:year_rookie` | … per set, release year, and release-year rookie | First 2026 Football rookie card to sell for $20,000 |
| `count:day`, `count:week` | first day / trailing week with N sales over a floor ($2,000, $5,000, $10,000, $25,000) | First day with 100 Pokémon sales over $2,000 |
| `gmv:day`, `gmv:week` | first day / week past $Y of confirmed volume | First $2,500,000 day for Pokémon |
| `count:total`, `gmv:total` | the Nth tracked sale; cumulative volume past $Y | The 2,500th tracked Basketball sale over $2,000 |
| `index:above`, `index:below` | first settled index close past a level, per tier and category | Blue Chip · Football index closes above 150 for the first time |

Lines live in `firsts_lines` (price $2,500 → $10M, counts 10 → 10,000,
volume $100K → $100M, index 80 → 150). Add a line, and the next run fills it
from history.

**`publish_firsts(day)`** picks the day's 1–3 from unpublished firsts dated
within the last week (the backlog only when there are none), ranked by
`firsts_score()` less a fifth of a point per day of age, with rules that
keep the feed from repeating itself: of the lines one sale crossed at once
only the highest is publishable; a sale is published once, under one kind;
a day's picks differ in kind, category and subject; a named subject headlines
at most once in five days; picks 2 and 3 must clear `firsts_pick_floor`
(schema_meta, 4.5). Idempotent per day.

**`firsts_score()`** is the one editorial dial: roughly one point per order
of magnitude, plus what kind of thing it is, plus how big a name it happened
to (`_prom`, rank by tracked volume). Tune it there, nowhere else.

### How it stays honest

- **Settled days only, per tier.** Sales at/above the $10,000 hot floor settle
  in `index_settle_days_bluechip` days (2); anything counting cheaper sales
  waits `index_settle_days_all` (4). Both read from `schema_meta`. With the
  tail disabled those kinds never publish rather than publish on partial data.
- **Under-collection cannot invent a first.** Counts and volumes only ever
  read low on a bad scrape day.
- **Confirmed sales only**: `is_publishable`, never `Unknown`, and a subject
  only earns firsts in its home category (a classifier miss cannot hand
  Ohtani a Yu-Gi-Oh first).
- **Ties** go to the larger price, deterministically.
- **Copies, not references**: the row keeps title, price, URL and image,
  because `db/retention.sql` deletes sales after 400 days.

### Yield, measured on the real data (6 Sep 2026)

Replaying all 39,408 rows: 26,333 firsts recorded, of which roughly 300–490
are dated each recent day and 200–350 score ≥ 4.5. The picker always has
plenty; the constraint is quality, which is what the score and the
repetition rules are for. A replayed week of picks is in the session notes;
representative: *First $100,000 sale of a Skyridge Charizard · First $200,000 sale of a
1984 Star Michael Jordan #101 Silver · First 2026 Football rookie card to
sell for $20,000 · First $75,000 sale of a 2012 Rayquaza #11 · First $40,000
sale out of 2025 Topps (Motorsport) · Lebron passes $2,500,000 in tracked
sales · The 2,500th tracked Basketball sale over $2,000.*

Known rough edges, all upstream of this series: the player list capitalises
"Lebron" and splits "Ohtani"/"Shohei"; Pokémon card labels lean on a weak
set parser ("2012 Rayquaza #11"). Fixing `etl/classify.py` and
`etl/parse_titles.py` improves the headlines directly.

## The article

`api/firsts.py` renders each milestone as a full article: kicker, headline,
a generated standfirst, the sale (image, price, format, bids, grade, rank
among all tracked sales), the **ladder** of every line that subject has
crossed, the **subject in numbers** (sales, volume, median and typical range,
top sale, sales-per-day chart, five biggest, comps with n ≥ 3), what made the
day for volume firsts, the **category this week** (7-day tiles, both index
tiers, confirmed-sales and median charts with the day marked), more firsts
nearby, the rest of the day's picks, and the method note. `NewsArticle` and
`BreadcrumbList` JSON-LD, Open Graph, canonical. Everything server-rendered;
charts are inline SVG.

WordPress hosts it: `/firsts/<id>-<slug>/` fetches
`/firsts/a/<id>?fragment=1` (meta + HTML + CSS, cached a day), prints its own
title, canonical, Open Graph, `article:published_time` and the JSON-LD, and
renders inside the theme. `/firsts-sitemap.xml` lists every article with
`news:news` tags for the last two days, and is appended to robots.txt.
`[razmania_firsts_today]` is the homepage module (also injected at the top
of the front page while the setting is on).

Env on the API service (all optional): `FIRSTS_SITE_URL`
(default `https://razmania.com/firsts/`, the base the API prints in article
links and JSON-LD), `SITE_URL`, `SITE_LOGO_URL` (publisher logo for
NewsArticle — set it; Google wants one).

## Statuses in the all-time table

- **verified** — two independent sources agree on card, price and date.
- **reported** — widely cited; one detail unconfirmed, named in the note.
- **open** — nobody has sourced it. The cell prints what is known and
  "Know the sale? Tell us." **Never fill an open cell from memory.**
- **not_yet** — no card has crossed the line; the record is shown instead.

`firsts/build.py --check` enforces all of that. The research worksheet (three
open $10K cells, several venues and dates, all source URLs) is unchanged
from launch and lives at the end of this file.

## Files

| Path | What |
|---|---|
| `db/schema.sql` → "FIRSTS" | `firsts_lines`, `firsts_log`, helpers, `record_firsts()`, `publish_firsts()`, `firsts_score()` |
| `api/firsts.py` | `/v1/firsts`, `/v1/firsts/log`, `/v1/firsts/{id}`, `/firsts/a/{id}`, `/firsts/today`, `/firsts/` |
| `firsts/firsts.json` | The all-time half. The source of truth. |
| `firsts/build.py` | Validates the JSON, copies it into the plugin, zips the plugin |
| `firsts/test_firsts_sql.py` | Engine test on synthetic sales in a rolled-back transaction, any scratch Postgres |
| `wordpress/razmania-firsts/` | The plugin: series page, article route, sitemap, homepage module |

## Publishing a change

```bash
python firsts/build.py --check          # validate the all-time JSON
python firsts/test_firsts_sql.py        # engine test (needs a scratch DATABASE_URL)
python firsts/build.py                  # copy into plugin + wordpress/dist/razmania-firsts.zip
```

SQL and API ship by `git push` (the cron re-applies `db/schema.sql` when its
hash changes; the first run records every first since 28 Jul and publishes
that day's three). The plugin ships by upload. See `DEPLOY.md`, Step 7.

## Research worksheet — the open and reported all-time cells

1. **Football $10K** — first five-figure football sale (start: 1935 National
   Chicle Nagurski, 1980s–90s hobby press).
2. **Basketball $10K** — first five-figure basketball sale (1948 Bowman Mikan,
   1986 Fleer Jordan PSA 10).
3. **Pokémon $10K** — the Illustrator's $54,970 at Heritage (Nov 2016) is a
   ceiling, not the first; look at 2013–16 Yahoo! Auctions Japan and eBay.
4. **Baseball $10K** — confirm 1985 / $25,000 / Mastro, or find earlier.
5. **Baseball $100K** — is the 1987 $110,000 private sale to Copeland
   documented well enough to be *the* first?
6. **Football $100K** — venue and date of the Nagurski PSA 9 at ~$240,000.
7. **Basketball $100K** — venue of the Mikan PSA 10 at $403,664 (2015).
8. **Pokémon $100K** — grade and exact date, Oct 2019 Weiss Illustrator.
9. **Pokémon $1M** — exact date of the PSA 9 Illustrator at $1,275,000.
10. **Charizard $10K**, **Mantle $10K / $100K**, **Mantle $1M** exact date.
11. **Source URLs everywhere** — publisher names are in; links are not.
