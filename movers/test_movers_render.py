"""
Offline test of the Card Movers renderer (api/movers.py). No database: api.db
is replaced by a stub before import, and every function under test takes its
rows as arguments.

  python3 movers/test_movers_render.py

What it proves:

  * search tokens split letters from digits, so "psa10" finds "PSA 10";
  * axis ticks are round numbers that cover the data;
  * every "no move" sentence names the rule that actually held the card off,
    in the same order the SQL applies them;
  * the chart plots one dot per sale, breaks its median line across gaps
    longer than the window, and escapes eBay titles;
  * card labels are escaped in list rows, a card URL is the id plus the slug,
    and the card route only accepts a 16-hex id.
"""

import sys
import types
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
stub = types.ModuleType("api.db")
stub.q = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no database in this test"))
sys.modules["api.db"] = stub

from api import movers as mv  # noqa: E402

failures = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


CFG = {"recent_days": 14, "base_days": 30, "min_recent": 2, "min_base": 3, "max_spread": 0.18,
       "agree": 0.75, "floor_margin": 1.25, "min_change": 10.0, "floor": 2000.0, "settle_days": 4}


def card(**kw):
    c = {"card_id": "3cff1fd2660eef88", "slug": "2004-ex-fire-red-gengar-108-holo-psa-9", "vertical": "Pokemon",
         "subject": "Gengar", "card_label": "2004 Ex Fire Red Gengar #108 Holo", "grade_label": "PSA 9",
         "is_mover": False, "recent_sales": 2, "base_sales": 4, "base_spread": 0.05, "base_median": Decimal("7525"),
         "recent_median": Decimal("14500"), "change_pct": Decimal("92.7"), "agree_share": 1.0,
         "base_from": date(2026, 7, 31), "base_to": date(2026, 8, 29), "recent_from": date(2026, 8, 30),
         "settled_through": date(2026, 9, 12), "image_url": None}
    c.update(kw)
    return c


# -- search tokens
check(mv.tokens("Gengar #108 psa10") == ["gengar", "108", "psa", "10"], f"tokens split digits ({mv.tokens('Gengar #108 psa10')})")
check(mv.tokens("sm168") == ["sm", "168"], "tokens: sm168 -> sm, 168")
check(mv.tokens("pokémon 1st") == ["pokémon", "1", "st"], f"tokens keep accents ({mv.tokens('pokémon 1st')})")
check(mv.tokens("100% _ %") == ["100"], "LIKE wildcards never reach the query")
check(len(mv.tokens(" ".join(["a"] * 20))) == 8, "at most 8 tokens")

# -- ticks
t = mv.nice_ticks(7000, 15000)
check(t[0] <= 7000 and t[-1] >= 15000, f"ticks cover the data ({t})")
check(all(float(x) % 500 == 0 for x in t), "ticks are round")
check(len(mv.date_ticks(date(2026, 7, 31), date(2026, 9, 12))) <= 6, "date ticks stay sparse")

# -- move status, one gate at a time
check(mv.move_status(card(is_mover=True), CFG)[1].startswith("Over the last 14 settled days its median sale was $14,500, up 92.7%"),
      "a mover says what it did")
check("Raw copies" in mv.move_status(card(grade_label="Raw"), CFG)[1], "raw: condition sentence")
check("One sale in the last 14" in mv.move_status(card(recent_sales=1), CFG)[1], "too few recent sales")
check("No sales in the last 14" in mv.move_status(card(recent_sales=None), CFG)[1], "no recent sales")
check("2 sales in the 30 days before" in mv.move_status(card(base_sales=2), CFG)[1], "too few earlier sales")
check("disagree" in mv.move_status(card(base_spread=0.4), CFG)[1], "lumped key")
check("collection floor" in mv.move_status(card(base_median=Decimal("2300")), CFG)[1], "near the floor")
check("inside the 10%" in mv.move_status(card(change_pct=Decimal("7.0")), CFG)[1], "small move")
check("both sides" in mv.move_status(card(), CFG)[1], "split recent sales")

# -- the chart
sales = [{"sold_date": d, "total_price": Decimal(p), "listing_format": "Auction", "bids": 3,
          "title": 'Gengar <script>alert("x")</script> & co'}
         for d, p in ((date(2026, 8, 5), 7500), (date(2026, 8, 9), 7000), (date(2026, 8, 10), 7600),
                      (date(2026, 9, 1), 14000), (date(2026, 9, 6), 15000))]
svg = mv.plot_card(card(), sales, CFG)
check(svg.count('class="mv-dot"') == 5 and svg.count('class="mv-hit"') == 5, "one dot and one hit target per sale")
check("<script>alert" not in svg and "&lt;script&gt;" in svg, "titles are escaped in the chart")
med = svg.split('class="mv-med"')[0].rsplit('<path d="', 1)[1]
check(med.count("M") == 2, f"median line breaks across the 22-day gap ({med})")
check("30 days before · median $7.5K" in svg and "Last 14 settled days · median $14.5K" in svg, "bands carry their medians")
check(mv.plot_card(card(), [], CFG) == "", "no sales, no chart")
sp = mv.spark(card(), [{"sold_date": s["sold_date"], "total_price": s["total_price"]} for s in sales])
check("mv-spark-now--up" in sp and "mv-spark-base" in sp, "sparkline draws both medians, recent one in the up colour")

# -- pieces
check(mv.card_url(card()) == mv.SITE + "c/3cff1fd2660eef88-2004-ex-fire-red-gengar-108-holo-psa-9/", "card URL is id-slug")
check(mv.CARD_REF.match("3cff1fd2660eef88-anything-here") and mv.CARD_REF.match("3cff1fd2660eef88")
      and not mv.CARD_REF.match("3cff1fd2660eef8") and not mv.CARD_REF.match("../etc/passwd"), "card ref pattern")
d = mv.delta(Decimal("-20.6"))
check("−20.6%" in d and "mv-arrow--down" in d and "▼" in d, "a fall reads as a minus, a down arrow and the down colour")
row = mv.mover_row(1, card(is_mover=True, card_label="A <b>card</b>"), [])
check("<b>card</b>" not in row.replace("<b>A", ""), "labels are escaped in list rows")

if failures:
    print(f"\n{len(failures)} FAILED")
    sys.exit(1)
print("\nall checks passed")
