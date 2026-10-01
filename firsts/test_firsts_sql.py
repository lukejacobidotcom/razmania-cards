"""
Functional test of the Firsts engine (db/schema.sql, "FIRSTS" section) on
synthetic sales, inside ONE transaction that is always rolled back. Nothing is
written, so any Postgres will do — including one that already holds other
tables, as long as it has no `sales` or `schema_meta`.

  DATABASE_URL=postgres://... python3 firsts/test_firsts_sql.py

Skips (exit 0, says so) when no DATABASE_URL is available or the database
already has a `sales` table — the fake one must never shadow the real one.

What it proves, because these are the ways a "first" quietly becomes a lie:

  * unsettled days are ignored, per tier: a $10k+ sale settles in 2 days, a
    day COUNT over $2,000 waits for the tail (4 days);
  * best-offer rows and the Unknown vertical never produce a first;
  * on a same-day tie the larger sale wins, deterministically;
  * a Pokémon sale with no player still gets a subject (Charizard), a card
    label needs a year or set, and a bare name is not a card;
  * every line a sale clears is filled, not just the largest;
  * headlines read as intended for every kind;
  * recording is idempotent and append-only: an EARLIER sale arriving after
    a first was recorded does not replace it;
  * publishing picks 1–3 a day, one per kind and per category, never
    re-publishes a day, and still publishes one on a quiet day.
"""

import os
import sys
from datetime import date, timedelta
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


def firsts_block():
    s = (REPO / "db" / "schema.sql").read_text(encoding="utf-8")
    start = s.index("-- FIRSTS")
    # CARD MOVERS follows FIRSTS and needs columns this fake table lacks; it has
    # its own test (movers/test_movers_sql.py).
    end = s.rindex("\n-- ====", 0, s.index("-- CARD MOVERS")) + 1
    return s[start:end]


FAKE_SCHEMA = """
CREATE TABLE schema_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
INSERT INTO schema_meta VALUES ('index_settle_days_bluechip', '2'), ('index_settle_days_all', '4'),
                               ('hot_floor', '10000'), ('publish_floor', '2000');
CREATE TABLE sales (
    item_id TEXT PRIMARY KEY, title TEXT NOT NULL, vertical TEXT NOT NULL,
    total_price NUMERIC(12,2) NOT NULL, sold_date DATE NOT NULL,
    grade_label TEXT, listing_format TEXT, url TEXT, image_url TEXT, player TEXT,
    card_year SMALLINT, brand_set TEXT, card_number TEXT, parallel TEXT,
    is_rookie BOOLEAN NOT NULL DEFAULT FALSE,
    is_publishable BOOLEAN NOT NULL DEFAULT TRUE
);
"""

D0 = date(2026, 8, 1)          # first day of data
LAST = date(2026, 9, 5)        # newest day -> $10k+ settled through 09-03, tail through 09-01

# (item_id, title, vertical, price, date, player, year, set, number, parallel, rookie, publishable)
SALES = [
    # Baseball: Mantle crosses $10k on 08-01, $100k on 08-03; Ruth $11k on 08-02.
    ("b1", "1952 Topps Mickey Mantle PSA 5",  "Baseball", 12000,  "2026-08-01", "Mickey Mantle", 1952, "Topps", "311", None, False, True),
    ("b2", "1933 Goudey Babe Ruth SGC 4",     "Baseball", 11000,  "2026-08-02", "Babe Ruth",     1933, "Goudey", None, None, False, True),
    ("b3", "1952 Topps Mickey Mantle PSA 8",  "Baseball", 150000, "2026-08-03", "Mickey Mantle", 1952, "Topps", "311", None, False, True),
    # Best offer: a $500k asking price on day one must not become anything.
    ("b4", "T206 Honus Wagner",               "Baseball", 500000, "2026-08-01", None, None, None, None, None, False, False),
    # Unsettled for the hot tier: dated 09-05 with settle 2.
    ("b5", "1952 Topps Mickey Mantle SGC 9",  "Baseball", 1000000, "2026-09-05", "Mickey Mantle", 1952, "Topps", "311", None, False, True),
    # Football: same-day tie at $10k -> the $30k sale is the first. Mahomes is a 2026 rookie card (synthetic).
    ("f1", "2000 Contenders Tom Brady auto",  "Football", 20000,  "2026-08-01", "Tom Brady", 2000, "Playoff Contenders", "144", None, True, True),
    ("f2", "2026 Prizm Rookie Silver",        "Football", 30000,  "2026-08-01", "Patrick Mahomes", 2026, "Prizm", "1", "Silver", True, True),
    # Pokémon: no player column ever; subject comes from the title. p2 names no character.
    ("p1", "1999 Base Set 1st Edition Charizard PSA 10", "Pokemon", 250000, "2026-08-04", None, 1999, "Base Set", "4", "1st Edition", False, True),
    ("p2", "Pop 2 BGS 10 Gold Star 1st Ed Clash of the Blue Sky", "Pokemon", 60000, "2026-08-02", None, None, None, None, None, False, True),
    # No year, no set from the parser: the set must come off the title.
    ("p3", "CGC 10 PRISTINE Charizard 146/144 Skyridge Holo Pokemon Card", "Pokemon", 125000, "2026-08-06", None, None, None, None, None, False, True),
    # Unknown: never a first.
    ("u1", "mystery slab",                    "Unknown",  900000, "2026-08-01", None, None, None, None, None, False, True),
]
# Basketball: 30 cheap Jordan sales on 08-10 (a day-count first at $2,000+) and
# 30 more on 09-03 (hot-settled but NOT tail-settled: must not count yet).
for i in range(30):
    SALES.append((f"j{i}", "1986 Fleer Michael Jordan", "Basketball", 2500 + i, "2026-08-10", "Michael Jordan", 1986, "Fleer", "57", None, True, True))
    SALES.append((f"k{i}", "1986 Fleer Michael Jordan", "Basketball", 2500 + i, "2026-09-03", "Michael Jordan", 1986, "Fleer", "57", None, True, True))
# A late filler so LAST is the newest day.
SALES.append(("z0", "filler", "Baseball", 2100, LAST.isoformat(), None, None, None, None, None, False, True))

# The sale that arrives late, dated BEFORE the recorded Baseball $10k first.
LATE = ("b0", "1909 T206 Ty Cobb PSA 3", "Baseball", 15000, "2026-07-30", "Ty Cobb", 1909, "T206", None, None, False, True)

INS = ("INSERT INTO sales (item_id,title,vertical,total_price,sold_date,player,card_year,brand_set,"
       "card_number,parallel,is_rookie,is_publishable) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")


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

    def one(kind, vertical, subject, thr, floor=None):
        sql = ("SELECT item_id, value, first_date, headline, score FROM firsts_log "
               "WHERE kind=%s AND vertical=%s AND subject=%s AND threshold=%s")
        p = [kind, vertical, subject, thr]
        if floor is not None:
            sql += " AND floor=%s"
            p.append(floor)
        r = rows(sql, *p)
        return r[0] if r else None

    try:
        cur.execute("SELECT to_regclass('public.sales')")
        if cur.fetchone()[0] is not None:
            print("SKIP: this database already has a sales table; use a scratch one")
            return 0
        cur.execute(FAKE_SCHEMA)
        cur.executemany(INS, SALES)
        cur.execute(firsts_block())

        # -- helpers
        for title, want in [("1999 Base Set 1st Edition Charizard PSA 10", "Charizard"),
                            ("Umbreon VMAX Alt Art Moonbreon PSA 10", "Umbreon"),
                            ("Mew ex 2024 Promo", "Mew"), ("Pop 2 BGS 10 sealed booster", None)]:
            got = rows("SELECT firsts_subject('Pokemon', NULL, %s)", title)[0][0]
            check(got == want, f"subject({title!r}) = {got!r}")
        check(rows("SELECT firsts_card_label('LeBron James', 2003::smallint, 'Topps Chrome', '111', 'Refractor')")[0][0]
              == "2003 Topps Chrome LeBron James #111 Refractor", "card label reads naturally")
        check(rows("SELECT firsts_card_label('LeBron James', NULL::smallint, NULL, '111', NULL)")[0][0] is None,
              "a bare name is not a card")
        check(rows("SELECT firsts_money(12600000)")[0][0] == "$12,600,000", "money formatting")
        check(rows("SELECT firsts_pokemon_set('CGC 10 PRISTINE Charizard 146/144 Skyridge Holo')")[0][0] == "Skyridge", "set read off a Pokémon title")
        check(rows("SELECT firsts_pokemon_set('Charizard VMAX rainbow')")[0][0] is None, "no set, no guess")

        # -- record
        n = rows("SELECT record_firsts()")[0][0]
        check(n > 0, f"first run recorded {n} rows")

        # price firsts
        check(one("price:vertical", "Baseball", "Baseball", 10000)[0] == "b1", "Baseball $10k first is the 08-01 Mantle, not the best-offer row")
        check(one("price:vertical", "Baseball", "Baseball", 100000)[0] == "b3", "Baseball $100k first is the 08-03 sale")
        check(one("price:vertical", "Baseball", "Baseball", 1000000) is None, "the unsettled 09-05 $1M sale is NOT recorded")
        check(one("price:subject", "Baseball", "Mickey Mantle", 25000)[0] == "b3", "Mantle $25k filled by the $150k sale")
        check(one("price:subject", "Baseball", "Babe Ruth", 10000)[0] == "b2", "Ruth has his own $10k first")
        check(one("price:vertical", "Football", "Football", 10000)[0] == "f2", "same-day tie goes to the larger sale")
        check(one("price:subject", "Football", "Tom Brady", 10000)[0] == "f1", "Brady still gets his own first")
        r = one("price:year_rookie", "Football", "2026", 25000)
        check(r is not None and r[0] == "f2" and r[3] == "First 2026 Football rookie card to sell for $25,000",
              f"2026 rookie first + headline: {r[3] if r else None}")
        r = one("price:card", "Football", "2026 Prizm Patrick Mahomes #1 Silver", 30000)
        check(r is not None and r[3] == "First $30,000 sale of a 2026 Prizm Patrick Mahomes #1 Silver", f"card headline: {r[3] if r else None}")
        r = one("price:card_grade", "Baseball", "1952 Topps Mickey Mantle #311 · Raw", 100000)
        check(r is not None, "card+grade first recorded (Raw when no grade)")
        r = one("price:set", "Pokemon", "1999 Base Set", 250000)
        check(r is not None and r[3] == "First $250,000 sale out of 1999 Base Set", f"set headline: {r[3] if r else None}")
        r = one("price:subject", "Pokemon", "Charizard", 250000)
        check(r is not None and r[3] == "First $250,000 sale of a 1999 Base Set Charizard #4 1st Edition",
              f"Charizard first from the title names the specific card: {r[3] if r else None}")
        r = one("price:subject", "Baseball", "Babe Ruth", 10000)
        check(r is not None and r[3] == "First $10,000 sale of a 1933 Goudey Babe Ruth", f"player first names the card: {r[3] if r else None}")
        r = one("price:card", "Pokemon", "Skyridge Charizard", 100000)
        check(r is not None and r[0] == "p3" and r[3] == "First $100,000 sale of a Skyridge Charizard",
              f"a Pokémon card with no parsed year still gets a specific headline: {r[3] if r else None}")
        r = one("price:vertical", "Pokemon", "Pokemon", 10000)
        check(r is not None and r[0] == "p2" and r[3] == "First $10,000 Pokémon sale",
              "a Pokémon sale naming no character still counts for the category, with the accent")
        check(one("price:subject", "Pokemon", "Pokemon", 10000) is None, "…but produces no subject row")
        check(rows("SELECT count(*) FROM firsts_log WHERE vertical='Unknown'")[0][0] == 0, "Unknown never records")
        check(rows("SELECT count(*) FROM firsts_log WHERE item_id='b4'")[0][0] == 0, "best-offer rows never record")

        # count / gmv firsts and tail settlement
        r = one("count:day", "Basketball", "Basketball", 25, 2000)
        check(r is not None and str(r[2]) == "2026-08-10" and r[3] == "First day with 25 Basketball sales over $2,000",
              f"day-count first on 08-10: {r[3] if r else None}")
        r = one("count:day", "Basketball", "Michael Jordan", 25, 2000)
        check(r is not None and r[3] == "First day with 25 Michael Jordan sales over $2,000", "subject day-count headline")
        check(one("count:day", "All", "All", 25, 2000) is not None, "market-wide day count")
        # Within a day sales are ordered larger-price-first (the same convention
        # as the tie rule), so the 25th of thirty same-day sales is j5 ($2,505).
        r = one("count:total", "Basketball", "Michael Jordan", 25, 2000)
        check(r is not None and r[0] == "j5" and r[3] == "The 25th tracked Michael Jordan sale over $2,000",
              f"cumulative: the 25th sale is the row ({r[0] if r else None})")
        r = one("gmv:total", "Baseball", "Baseball", 100000)
        check(r is not None and r[3] == "Baseball passes $100,000 in tracked sales", "cumulative volume headline")
        check(one("count:day", "Basketball", "Basketball", 50, 2000) is None,
              "09-03's 30 sales are hot-settled but not tail-settled: no 50-count first yet")
        check(one("count:week", "Basketball", "Basketball", 25, 2000) is not None, "week-count first")

        # idempotent, append-only
        check(rows("SELECT record_firsts()")[0][0] == 0, "second run records nothing")
        cur.execute(INS, LATE)
        rows("SELECT record_firsts()")
        check(one("price:vertical", "Baseball", "Baseball", 10000)[0] == "b1", "Baseball $10k first unchanged by the late 07-30 sale")
        check(one("price:subject", "Baseball", "Ty Cobb", 10000)[0] == "b0", "…while the new subject's own first is recorded")

        # settlement moves forward
        cur.execute(INS, ("z9", "filler", "Baseball", 2100, "2026-09-09", None, None, None, None, None, False, True))
        rows("SELECT record_firsts()")
        check(one("price:vertical", "Baseball", "Baseball", 1000000)[0] == "b5", "after the day settles, the $1M first is recorded")
        r = one("count:total", "Basketball", "Michael Jordan", 50, 2000)
        check(r is not None and str(r[2]) == "2026-09-03", "and once 09-03 is tail-settled, the 50th Jordan sale is recorded there")
        check(str(one("count:day", "Basketball", "Basketball", 25, 2000)[2]) == "2026-08-10", "the 08-10 day-count first did not move")

        # -- publish
        picks = rows("SELECT publish_firsts(%s)", date(2026, 9, 6))[0][0]
        check(1 <= picks <= 3, f"publishes 1–3 ({picks})")
        pub = rows("SELECT kind, vertical, headline, score FROM firsts_log WHERE published_on=%s ORDER BY publish_rank", date(2026, 9, 6))
        for p in pub:
            print("      pick:", p[3], p[2])
        check(len({p[0] for p in pub}) == len(pub), "picks differ in kind")
        check(len({p[1] for p in pub}) == len(pub), "picks differ in category")
        check(pub[0][3] == max(x[3] for x in pub), "pick 1 is the top score")
        check(rows("SELECT count(*) FROM firsts_log WHERE published_on IS NOT NULL AND first_date <= %s - 8", date(2026, 9, 6))[0][0] == 0,
              "picks come from the last settled week, not the launch backlog")
        check(rows("""SELECT count(*) FROM firsts_log f WHERE f.published_on IS NOT NULL AND EXISTS (
                        SELECT 1 FROM firsts_log g WHERE g.kind=f.kind AND g.vertical=f.vertical AND g.subject=f.subject
                          AND g.floor=f.floor AND g.first_date=f.first_date AND g.threshold > f.threshold)""")[0][0] == 0,
              "only the highest line a sale crossed is published")
        check(rows("SELECT count(*) - count(DISTINCT item_id) FROM firsts_log WHERE published_on IS NOT NULL AND item_id IS NOT NULL")[0][0] == 0,
              "one sale, one story")
        check(rows("SELECT headline FROM firsts_log WHERE kind='price:vertical' AND vertical='All' AND threshold=1000000")[0][0]
              == "First $1,000,000 card sale", "market-wide price first reads as a card sale")
        check(rows("SELECT publish_firsts(%s)", date(2026, 9, 6))[0][0] == 0, "same day again publishes nothing")
        # quiet day: nothing new recorded, backlog still yields exactly-at-least one
        picks2 = rows("SELECT publish_firsts(%s)", date(2026, 9, 7))[0][0]
        check(picks2 >= 1, f"quiet day still publishes from the backlog ({picks2})")
        check(rows("SELECT count(*) FROM firsts_log WHERE published_on IS NOT NULL AND publish_rank IS NULL")[0][0] == 0,
              "every published row has a rank")
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
