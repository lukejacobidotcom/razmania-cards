"""
Functional test of Card Movers (db/schema.sql, "CARD MOVERS") on synthetic
sales, inside ONE transaction that is always rolled back. Nothing is written,
so any Postgres will do, as long as it has no `sales` table of its own.

  DATABASE_URL=postgres://... python3 movers/test_movers_sql.py

Skips (exit 0, says so) when no DATABASE_URL is available or the database
already has a `sales` table: the fake one must never shadow the real one.

What it proves, because these are the ways a gainers list quietly lies:

  * a card is one card: same number with or without leading zeros or case is
    one card; no number, or no year and no set, is not a card; Unknown and a
    Pokémon title with no character are not cards; "Shohei" and "Ohtani" are
    one man; a "PSA 9 (OC)" is not a PSA 9; best-offer rows never join;
  * the Pokémon set comes off the title, not the noisy brand_set;
  * a clean doubling and a clean fall are movers, with the right sign and size;
  * every gate holds on its own: a lumped key (spread), a raw card, a card
    trading near the floor, recent sales that split both ways, a small move,
    too few sales, and sales on unsettled days all stay off the lists;
  * the windows are where the settle lag says, and unsettled sales still
    reach the chart;
  * the tunables in schema_meta apply on the next refresh.
"""

import os
import sys
from datetime import date
from pathlib import Path

import psycopg2

REPO = Path(__file__).resolve().parents[1]


def _dsn():
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        return dsn
    env = REPO / ".env"
    if env.exists():
        # .env on this machine is UTF-8 with UTF-16 lines appended by PowerShell.
        for line in env.read_bytes().decode("utf-8", "ignore").replace(chr(0), "").splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def blocks():
    """The FIRSTS helpers (firsts_subject, firsts_pokemon_set) and the CARD
    MOVERS section, without the trigram index this test does not need and a
    scratch database may not support."""
    s = (REPO / "db" / "schema.sql").read_text(encoding="utf-8")
    start = s.index("-- FIRSTS")
    end = s.index("-- ---------------------------------------------------------------- refresh")
    out = s[start:end]
    return "\n".join(l for l in out.splitlines() if "gin_trgm_ops" not in l)


FAKE_SCHEMA = """
CREATE TABLE schema_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
INSERT INTO schema_meta VALUES ('index_settle_days_bluechip', '2'), ('index_settle_days_all', '4'),
                               ('hot_floor', '10000'), ('publish_floor', '2000');
CREATE TABLE sales (
    item_id TEXT PRIMARY KEY, title TEXT NOT NULL, vertical TEXT NOT NULL,
    total_price NUMERIC(12,2) NOT NULL, sold_date DATE NOT NULL,
    grade_label TEXT, listing_format TEXT, bids INTEGER, url TEXT, image_url TEXT, player TEXT,
    card_year SMALLINT, brand_set TEXT, card_number TEXT, parallel TEXT,
    is_rookie BOOLEAN NOT NULL DEFAULT FALSE, is_auto BOOLEAN NOT NULL DEFAULT FALSE,
    is_publishable BOOLEAN NOT NULL DEFAULT TRUE
);
"""

LAST = date(2026, 9, 16)     # newest sale -> settled through 09-12 (settle_all 4)
# recent window: 08-30 .. 09-12     before window: 07-31 .. 08-29

GENGAR = "2004 Pokemon EX Fire Red & Leaf Green #108 Gengar ex Holo PSA 9"
BRADY = "2000 Bowman Chrome Tom Brady Rookie RC #236 PSA 9"

# (item_id, title, vertical, price, date, player, year, brand_set, number, parallel, grade, publishable)
SALES = [
    # A clean doubling. brand_set 'Star' is the parser noise Pokémon rows carry;
    # '0108' must join '108'.
    ("g1", GENGAR, "Pokemon", 7500, "2026-08-05", None, 2004, "Star", "108", "Holo", "PSA 9", True),
    ("g2", GENGAR, "Pokemon", 7000, "2026-08-09", None, 2004, None, "108", "Holo", "PSA 9", True),
    ("g3", GENGAR, "Pokemon", 7600, "2026-08-10", None, 2004, None, "0108", "Holo", "PSA 9", True),
    ("g4", GENGAR, "Pokemon", 7550, "2026-08-20", None, 2004, None, "108", "Holo", "PSA 9", True),
    ("g5", GENGAR, "Pokemon", 14000, "2026-09-01", None, 2004, None, "108", "Holo", "PSA 9", True),
    ("g6", GENGAR, "Pokemon", 15000, "2026-09-06", None, 2004, None, "108", "Holo", "PSA 9", True),
    # The same card off-centre is a different card.
    ("g7", GENGAR.replace("PSA 9", "PSA 9 (OC)"), "Pokemon", 3000, "2026-09-02", None, 2004, None, "108", "Holo", "PSA 9", True),
    # A clean fall, with a best-offer "sale" at an asking price that must not count.
    ("b1", BRADY, "Football", 12000, "2026-08-02", "Tom Brady", 2000, "Bowman Chrome", "236", None, "PSA 9", True),
    ("b2", BRADY, "Football", 12400, "2026-08-08", "Tom Brady", 2000, "Bowman Chrome", "236", None, "PSA 9", True),
    ("b3", BRADY, "Football", 11800, "2026-08-14", "Tom Brady", 2000, "Bowman Chrome", "236", None, "PSA 9", True),
    ("b4", BRADY, "Football", 12200, "2026-08-16", "Tom Brady", 2000, "Bowman Chrome", "236", None, "PSA 9", True),
    ("b5", BRADY, "Football", 9300, "2026-09-04", "Tom Brady", 2000, "Bowman Chrome", "236", None, "PSA 9", True),
    ("b6", BRADY, "Football", 9500, "2026-09-08", "Tom Brady", 2000, "Bowman Chrome", "236", None, "PSA 9", True),
    ("b7", BRADY, "Football", 9100, "2026-09-10", "Tom Brady", 2000, "Bowman Chrome", "236", None, "PSA 9", True),
    ("b8", BRADY, "Football", 40000, "2026-09-09", "Tom Brady", 2000, "Bowman Chrome", "236", None, "PSA 9", False),
    # Two cards under one key: before-window spread far above 0.18.
    ("l1", "2000 Charizard #6 PSA 10 Japanese promo", "Pokemon", 4000, "2026-08-03", None, 2000, None, "6", None, "PSA 10", True),
    ("l2", "2000 Charizard #6 PSA 10 Japanese promo", "Pokemon", 4100, "2026-08-04", None, 2000, None, "6", None, "PSA 10", True),
    ("l3", "2000 Topps Chrome TV Charizard #6 PSA 10", "Pokemon", 7000, "2026-08-05", None, 2000, None, "6", None, "PSA 10", True),
    ("l4", "2000 Topps Chrome TV Charizard #6 PSA 10", "Pokemon", 7200, "2026-08-06", None, 2000, None, "6", None, "PSA 10", True),
    ("l5", "2000 Charizard #6 PSA 10 Japanese promo", "Pokemon", 4000, "2026-09-03", None, 2000, None, "6", None, "PSA 10", True),
    ("l6", "2000 Charizard #6 PSA 10 Japanese promo", "Pokemon", 3900, "2026-09-05", None, 2000, None, "6", None, "PSA 10", True),
    # Raw: a clean-looking fall that must not be called.
    ("r1", "2018 Bowman Chrome Shohei Ohtani #1 RC", "Baseball", 3000, "2026-08-03", "Shohei", 2018, "Bowman Chrome", "1", None, "Raw", True),
    ("r2", "2018 Bowman Chrome Shohei Ohtani #1 RC", "Baseball", 3050, "2026-08-04", "Ohtani", 2018, "Bowman Chrome", "1", None, "Raw", True),
    ("r3", "2018 Bowman Chrome Shohei Ohtani #1 RC", "Baseball", 2950, "2026-08-05", "Shohei", 2018, "Bowman Chrome", "1", None, "Raw", True),
    ("r4", "2018 Bowman Chrome Shohei Ohtani #1 RC", "Baseball", 2500, "2026-09-03", "Ohtani", 2018, "Bowman Chrome", "1", None, "Raw", True),
    ("r5", "2018 Bowman Chrome Shohei Ohtani #1 RC", "Baseball", 2450, "2026-09-05", "Shohei", 2018, "Bowman Chrome", "1", None, "Raw", True),
    # Near the floor: +30% on sales whose cheaper siblings are never collected.
    ("f1", "2021 Pikachu #25 PSA 10", "Pokemon", 2300, "2026-08-03", None, 2021, None, "25", None, "PSA 10", True),
    ("f2", "2021 Pikachu #25 PSA 10", "Pokemon", 2310, "2026-08-04", None, 2021, None, "25", None, "PSA 10", True),
    ("f3", "2021 Pikachu #25 PSA 10", "Pokemon", 2290, "2026-08-05", None, 2021, None, "25", None, "PSA 10", True),
    ("f4", "2021 Pikachu #25 PSA 10", "Pokemon", 3000, "2026-09-03", None, 2021, None, "25", None, "PSA 10", True),
    ("f5", "2021 Pikachu #25 PSA 10", "Pokemon", 3100, "2026-09-05", None, 2021, None, "25", None, "PSA 10", True),
    # Recent sales split both ways: 2 of 4 above the before median.
    ("a1", "1996 Topps Kobe Bryant #138 PSA 10", "Basketball", 5000, "2026-08-03", "Kobe Bryant", 1996, "Topps", "138", None, "PSA 10", True),
    ("a2", "1996 Topps Kobe Bryant #138 PSA 10", "Basketball", 5100, "2026-08-04", "Kobe Bryant", 1996, "Topps", "138", None, "PSA 10", True),
    ("a3", "1996 Topps Kobe Bryant #138 PSA 10", "Basketball", 4900, "2026-08-05", "Kobe Bryant", 1996, "Topps", "138", None, "PSA 10", True),
    ("a4", "1996 Topps Kobe Bryant #138 PSA 10", "Basketball", 6500, "2026-09-01", "Kobe Bryant", 1996, "Topps", "138", None, "PSA 10", True),
    ("a5", "1996 Topps Kobe Bryant #138 PSA 10", "Basketball", 4800, "2026-09-02", "Kobe Bryant", 1996, "Topps", "138", None, "PSA 10", True),
    ("a6", "1996 Topps Kobe Bryant #138 PSA 10", "Basketball", 6600, "2026-09-03", "Kobe Bryant", 1996, "Topps", "138", None, "PSA 10", True),
    ("a7", "1996 Topps Kobe Bryant #138 PSA 10", "Basketball", 4700, "2026-09-04", "Kobe Bryant", 1996, "Topps", "138", None, "PSA 10", True),
    # A 7% move: real, and too small to list.
    ("s1", "2009 Topps Curry #321 PSA 8", "Basketball", 5000, "2026-08-03", "Curry", 2009, "Topps", "321", None, "PSA 8", True),
    ("s2", "2009 Topps Curry #321 PSA 8", "Basketball", 5000, "2026-08-04", "Curry", 2009, "Topps", "321", None, "PSA 8", True),
    ("s3", "2009 Topps Curry #321 PSA 8", "Basketball", 5000, "2026-08-05", "Curry", 2009, "Topps", "321", None, "PSA 8", True),
    ("s4", "2009 Topps Curry #321 PSA 8", "Basketball", 5300, "2026-09-03", "Curry", 2009, "Topps", "321", None, "PSA 8", True),
    ("s5", "2009 Topps Curry #321 PSA 8", "Basketball", 5400, "2026-09-05", "Curry", 2009, "Topps", "321", None, "PSA 8", True),
    # Only unsettled recent sales: a jump the tail has not caught up with.
    ("u1", "2019 Mewtwo #SM191 PSA 10", "Pokemon", 5000, "2026-08-03", None, 2019, None, "sm191", None, "PSA 10", True),
    ("u2", "2019 Mewtwo #SM191 PSA 10", "Pokemon", 5000, "2026-08-04", None, 2019, None, "SM191", None, "PSA 10", True),
    ("u3", "2019 Mewtwo #SM191 PSA 10", "Pokemon", 5000, "2026-08-05", None, 2019, None, "SM191", None, "PSA 10", True),
    ("u4", "2019 Mewtwo #SM191 PSA 10", "Pokemon", 9000, "2026-09-14", None, 2019, None, "SM191", None, "PSA 10", True),
    ("u5", "2019 Mewtwo #SM191 PSA 10", "Pokemon", 9100, "2026-09-15", None, 2019, None, "SM191", None, "PSA 10", True),
    # Too few before: two sales.
    ("t1", "1989 Upper Deck Ken Griffey #1 PSA 10", "Baseball", 4000, "2026-08-03", "Ken Griffey", 1989, "Upper Deck", "1", None, "PSA 10", True),
    ("t2", "1989 Upper Deck Ken Griffey #1 PSA 10", "Baseball", 4000, "2026-08-04", "Ken Griffey", 1989, "Upper Deck", "1", None, "PSA 10", True),
    ("t3", "1989 Upper Deck Ken Griffey #1 PSA 10", "Baseball", 6000, "2026-09-03", "Ken Griffey", 1989, "Upper Deck", "1", None, "PSA 10", True),
    ("t4", "1989 Upper Deck Ken Griffey #1 PSA 10", "Baseball", 6000, "2026-09-05", "Ken Griffey", 1989, "Upper Deck", "1", None, "PSA 10", True),
    # Not cards.
    ("n1", "2025 Topps Cooper Flagg RC", "Basketball", 9000, "2026-09-03", "Cooper Flagg", 2025, "Topps", None, None, "Raw", True),
    ("n2", "Michael Jordan #23 PSA 10", "Basketball", 9000, "2026-09-03", "Michael Jordan", None, None, "23", None, "PSA 10", True),
    ("n3", "mystery slab #1 PSA 10", "Unknown", 9000, "2026-09-03", "Michael Jordan", 1986, "Fleer", "57", None, "PSA 10", True),
    ("n4", "Pokemon Japanese Promo #1 PSA 10", "Pokemon", 9000, "2026-09-03", None, 1999, None, "1", None, "PSA 10", True),
    # A late filler so LAST is the newest day.
    ("z0", "filler", "Baseball", 2100, LAST.isoformat(), None, None, None, None, None, "Raw", True),
]

INS = ("INSERT INTO sales (item_id,title,vertical,total_price,sold_date,player,card_year,brand_set,"
       "card_number,parallel,grade_label,is_publishable) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")


def main():
    dsn = _dsn()
    if not dsn:
        print("SKIP: no DATABASE_URL")
        return 0
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()
    failures = []

    def check(cond, msg):
        print(("ok   " if cond else "FAIL ") + msg)
        if not cond:
            failures.append(msg)

    def rows(sql, *p):
        cur.execute(sql, p)
        return cur.fetchall()

    def card_of(item_id):
        r = rows("SELECT card_id FROM mv_card_sales WHERE item_id = %s", item_id)
        return r[0][0] if r else None

    def card(item_id):
        cur.execute("SELECT * FROM mv_cards WHERE card_id = %s", (card_of(item_id),))
        r = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], r)) if r else None

    try:
        cur.execute("SELECT to_regclass('public.sales')")
        if cur.fetchone()[0] is not None:
            print("SKIP: this database already has a sales table; use a scratch one")
            return 0
        cur.execute(FAKE_SCHEMA)
        cur.executemany(INS, SALES)
        cur.execute(blocks())

        # -- identity
        check(rows("SELECT card_subject_of('Shohei'), card_subject_of('Ohtani'), card_subject_of('Tom Brady')")[0]
              == ("Shohei Ohtani", "Shohei Ohtani", "Tom Brady"), "short player names map to one full name")
        check(rows("SELECT grade_qualifier_of('1986 Fleer Jordan PSA 9 (OC)'), grade_qualifier_of('PSA 9 OC- Brady'), "
                   "grade_qualifier_of('PSA 10 GEM MINT')")[0] == ("OC", "OC", None), "grade qualifiers are read off the title")
        check(card_of("g1") == card_of("g3"), "'0108' and '108' are one card")
        check(card_of("u1") == card_of("u2"), "'sm191' and 'SM191' are one card")
        check(card_of("r1") == card_of("r2"), "'Shohei' and 'Ohtani' rows are one card")
        check(card_of("g7") is not None and card_of("g7") != card_of("g5"), "PSA 9 (OC) is not a PSA 9")
        check(card("g7")["grade_label"] == "PSA 9 (OC)", "…and says so in its grade")
        for i, why in (("n1", "no card number"), ("n2", "no year and no set"), ("n3", "Unknown category"),
                       ("n4", "Pokémon title with no character"), ("b8", "best-offer row"), ("z0", "no subject")):
            check(card_of(i) is None, f"not a card: {why}")
        g = card("g1")
        check(g["card_set"] == "Ex Fire Red", f"Pokémon set comes off the title, not brand_set ({g['card_set']!r})")
        check(g["card_label"] == "2004 Ex Fire Red Gengar #108 Holo", f"label reads as a card ({g['card_label']!r})")
        check(g["slug"] == "2004-ex-fire-red-gengar-108-holo-psa-9", f"slug ({g['slug']!r})")
        check("pokémon" in g["search_text"] and "leaf green" in g["search_text"], "search text carries the category and a real title")
        check(card("r1")["card_label"] == "2018 Bowman Chrome Shohei Ohtani #1", "label uses the full name")

        # -- windows
        check((g["base_from"], g["base_to"], g["recent_from"], g["settled_through"])
              == (date(2026, 7, 31), date(2026, 8, 29), date(2026, 8, 30), date(2026, 9, 12)),
              "windows: before 07-31..08-29, recent 08-30..09-12 (settled = last day - 4)")

        # -- movers
        check(g["is_mover"] and g["base_sales"] == 4 and g["recent_sales"] == 2, "Gengar doubling is a mover on 4 -> 2 sales")
        check(90 < float(g["change_pct"]) < 95, f"…at about +93% ({g['change_pct']})")
        b = card("b1")
        check(b["is_mover"] and -26 < float(b["change_pct"]) < -21, f"Brady fall is a mover at about -23% ({b['change_pct']})")
        check(b["recent_sales"] == 3, "…and the best-offer asking price is not one of its sales")
        c = card("l1")
        check(not c["is_mover"] and float(c["base_spread"]) > 0.18, f"lumped key: spread {float(c['base_spread']):.3f}, not a mover")
        check(not card("r1")["is_mover"], "raw card: not a mover")
        f = card("f1")
        check(not f["is_mover"] and float(f["change_pct"]) > 25, "near the floor: +30% but not a mover")
        a = card("a1")
        check(not a["is_mover"] and float(a["agree_share"]) == 0.5, "recent sales split 2/4: not a mover")
        s = card("s1")
        check(not s["is_mover"] and 5 < float(s["change_pct"]) < 10, "a 7% move: not a mover")
        u = card("u1")
        check(not u["is_mover"] and u["recent_sales"] is None, "unsettled sales do not count toward a move")
        check(u["sales"] == 5 and u["last_price"] == 9100, "…but they are on the card and its chart")
        check(not card("t1")["is_mover"], "two sales before: not a mover")
        check(rows("SELECT count(*) FROM mv_cards WHERE is_mover")[0][0] == 2, "exactly the two clean moves are listed")
        check(rows("SELECT count(*) - count(DISTINCT card_id) FROM mv_cards")[0][0] == 0, "card_id is unique")

        # -- each case is held off by its own gate, not by some other one
        for k, loose, item, what in (("movers_max_spread", "1", "l1", "lumped key"),
                                     ("movers_floor_margin", "1", "f1", "near-floor card"),
                                     ("movers_agree", "0.5", "a1", "split card"),
                                     ("movers_min_change", "5", "s1", "7% move"),
                                     ("movers_min_base", "2", "t1", "thin card")):
            before = rows("SELECT v FROM schema_meta WHERE k = %s", k)[0][0]
            cur.execute("UPDATE schema_meta SET v = %s WHERE k = %s", (loose, k))
            cur.execute("REFRESH MATERIALIZED VIEW mv_cards")
            check(card(item)["is_mover"], f"with {k} = {loose} the {what} would list: that gate is what holds it")
            cur.execute("UPDATE schema_meta SET v = %s WHERE k = %s", (before, k))
        cur.execute("REFRESH MATERIALIZED VIEW mv_cards")
        check(rows("SELECT count(*) FROM mv_cards WHERE is_mover")[0][0] == 2, "restored: two movers again")

        # -- tunables
        cur.execute("UPDATE schema_meta SET v = '50' WHERE k = 'movers_min_change'")
        cur.execute("REFRESH MATERIALIZED VIEW mv_card_sales")
        cur.execute("REFRESH MATERIALIZED VIEW mv_cards")
        check(card("g1")["is_mover"] and not card("b1")["is_mover"], "movers_min_change = 50 keeps the doubling, drops the -23%")
        cur.execute("UPDATE schema_meta SET v = '10' WHERE k = 'movers_min_change'")
        cur.execute("UPDATE schema_meta SET v = '8' WHERE k = 'index_settle_days_all'")
        cur.execute("REFRESH MATERIALIZED VIEW mv_cards")
        check(card("g1")["settled_through"] == date(2026, 9, 8), "the settle lag moves the windows with it")
    finally:
        conn.rollback()
        conn.close()
        print("rolled back")

    if failures:
        print(f"\n{len(failures)} FAILED")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
