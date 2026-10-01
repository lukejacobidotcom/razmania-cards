# Card Movers

The graded cards whose price moved most, the busiest cards, and a chart of
every confirmed sale for any card a reader looks up. Built to give collectors
ideas: what is running, what is sliding, what everyone is trading. Readers can
then chart any card themselves.

Lives at **razmania.com/movers/** (page, shortcode `[razmania_movers]`) and
**razmania.com/movers/c/<id>-<slug>/** (one card), plugin
`wordpress/razmania-movers`. `[razmania_movers_strip]` is a three-and-three
module for the homepage.

## A move is a card against itself

Never a player, never a category. A player's median moves when a PSA 10 week
follows a raw week, without one card changing price. So the unit is one card
in one grade:

    category | player or Pokémon | year | set | number | parallel | grade

and a sale only joins a card when the title parser found a **number and a year
or set**. On Pokémon rows the character and the set are read off the title
(the FIRSTS helpers), because those rows carry no player and a `brand_set` of
fragments. Leading zeros and case are normalised. A grade qualifier ("PSA 9
(OC)") is part of the grade. `card_subject_of()` maps the classifier's short
names to one full name ("Shohei" and "Ohtani" are both Shohei Ohtani).

About 8,600 of the 23,800 confirmed sales (16 Sep 2026) join a card, across
roughly 4,900 cards. The rest have no parsed number, or no year and no set.

## The rules, and why each exists

The move is the median of the card's sales in the **last 14 settled days**
against the median of the **30 days before**. It lists only if all of these
hold:

| Rule | Default | Why |
|---|---|---|
| Graded | not `Raw` | "Raw" is every condition under one label. Its median moves with whichever copies sold. |
| Enough sales | ≥ 2 recent, ≥ 3 before | A median of one sale is that sale. |
| Earlier sales agree | robust log-spread ≤ 0.18 | A single card in a single grade sells in a tight band: spread 0.086 at the median, 0.14 at p75 (182 cards with 6+ sales). Every mover above 0.18 on 16 Sep was two cards under one name (Japanese Neo 2 promo and Topps Chrome TV, both "2000 Charizard #6") or a raw card. |
| Recent sales agree | ≥ 75% on the move's side of the earlier median | One big auction next to a normal sale is not a move. |
| Clear of the floor | earlier median ≥ $2,000 × 1.25 | Sales under $2,000 are never collected. A card trading near the floor loses its cheap sales, so its median reads high: a made-up gain. |
| Big enough | ≥ 10% | Smaller moves are within what two weeks of normal sales do. |
| Settled days only | windows end `index_settle_days_all` days back | The $2,000–9,999 range is scraped every few days. An unsettled day holds only the $10,000+ sales of a card near that line, which looks like a jump. |

On 16 Sep 2026 that left **28 movers (6 up, 22 down)** out of 109 graded
cards with enough sales in both windows. Every one was checked by listing
title and read as the same card, sale after sale. The biggest were a 2004 EX
FireRed & LeafGreen Gengar ex PSA 9 (+94%, $7.5K → $14.5K) and a 2005 Japanese
Play Promo Rayquaza PSA 10 (−44%). The lists are short because most cards do
not move 10% in two weeks. That is the honest answer, not something to tune
away.

The chart plots every confirmed sale, settled or not: a sale is a fact, a move
is a claim. Its line is the median of the 14 days up to each sale, drawn as
steps and broken where there were none. (A median of the last five sales stays
flat straight through a doubling on a thin card.)

## Tuning

Every threshold is a `schema_meta` row, seeded by `db/schema.sql` with
`ON CONFLICT DO NOTHING`. To change one, `UPDATE` the row; it applies at the
next refresh. Editing the seed in `schema.sql` does nothing to a database that
already has the key.

| Key | Default |
|---|---|
| `movers_recent_days` | 14 |
| `movers_base_days` | 30 |
| `movers_min_recent` | 2 |
| `movers_min_base` | 3 |
| `movers_max_spread` | 0.18 |
| `movers_agree` | 0.75 |
| `movers_floor_margin` | 1.25 |
| `movers_min_change` | 10 |

## Files

| Path | What |
|---|---|
| `db/schema.sql` → "CARD MOVERS" | `card_set_of()`, `card_subject_of()`, `grade_qualifier_of()`, `mv_card_sales`, `mv_cards` |
| `api/movers.py` | `/v1/movers`, `/v1/cards/search`, `/v1/cards/{id}`, `/movers/`, `/movers/c/{id}-{slug}`, `/movers/strip` |
| `movers/test_movers_sql.py` | Identity and every gate on synthetic sales, rolled back, any scratch Postgres |
| `movers/test_movers_render.py` | Renderer: search tokens, ticks, "why no move" wording, chart, escaping. No database. |
| `wordpress/razmania-movers/` | The plugin: page shortcode, card route, homepage strip |

## Publishing a change

```bash
DATABASE_URL=postgres://scratch python movers/test_movers_sql.py
python movers/test_movers_render.py
python wordpress/build_zip.py razmania-movers
```

SQL and API ship by `git push` (the cron re-applies `db/schema.sql` when its
hash changes). The plugin ships by upload. See `DEPLOY.md`, Step 8.

## Rough edges, all upstream

- **Parser splits.** The same card can land under two keys when one title
  names the set and another does not ("2004 Ex Fire Red Gengar #108" and
  "2004 Gengar #108"), or when the parser reads "SP" off one title and not the
  next. A split card has fewer sales per key, so it lists less often, but it
  never lists wrongly. The card page shows those keys together under "Other
  grades of this card". Better set parsing in `etl/parse_titles.py` fixes it
  at the source.
- **New name splits** need a row in `card_subject_of()` until
  `etl/classify.py` stores full names.
- **The 19 Aug – 5 Sep under-collection** (the scraper returned one page per
  price band) shows on card charts as a thin stretch, and it leaves fewer
  cards with enough sales in September's earlier window. Nobody has checked
  whether the rows that did arrive were a fair sample of each band. Until
  that's known, treat September moves that rest on those dates with care.
