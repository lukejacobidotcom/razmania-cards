"""
Load scraped eBay sales into Postgres and refresh the aggregate layer.

  export DATABASE_URL=postgres://user:pw@host/db
  python3 etl/load.py raw/*.jsonl
  python3 etl/load.py --csv out/sales_all.csv

Idempotent: item_id is the primary key, so overlapping scrapes UPDATE rather
than duplicate. Safe to re-run any time.
"""

import argparse
import csv
import glob
import gzip
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify import classify, clean, PLAYERS                      # noqa: E402
import parse_titles as pt                                          # noqa: E402

JUNK = re.compile(
    r"\bu\s*pick\b|\byou\s*pick\b|\bpick\s*(your|any|one)\b|\bchoose\b|"
    r"\blot\s*of\b|\bcard\s*lot\b|\bbulk\b|\bcollection\s*of\b|"
    r"\breprint\b|\bcustom\b|\bnovelty\b|\bfacsimile\b|\bproxy\b|\bdigital\b|"
    r"\bcomplete\s*set\b", re.I)

# Flat player lookup, longest names first so "ken griffey jr" beats "ken griffey".
ALL_PLAYERS = sorted({p for lst in PLAYERS.values() for p in lst}, key=len, reverse=True)


def find_player(title):
    low = " " + clean(title).lower() + " "
    for p in ALL_PLAYERS:
        if p in low:
            return " ".join(w.capitalize() for w in p.strip().split())
    return None


def fnum(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def inum(v):
    """CSV round-trips integers as '69.0', which Postgres rejects for INTEGER."""
    f = fnum(v)
    return None if f is None else int(f)


DATE_RX = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})")


def parse_date(v):
    if not v:
        return None
    s = str(v)
    m = DATE_RX.search(s)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y").date()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def to_record(o):
    title = clean(o.get("title") or o.get("basic_info.title") or "")
    if not title:
        return None
    item_id = str(o.get("itemId") or o.get("item_id") or "").strip()
    if not item_id:
        return None
    price = fnum(o.get("soldPrice")) or fnum(o.get("sold_price")) or fnum(o.get("priceValue"))
    total = fnum(o.get("totalPrice")) or fnum(o.get("total_price")) or price
    if total is None:
        return None
    sold = parse_date(o.get("endedAt") or o.get("sold_date") or o.get("soldDate"))
    if sold is None:
        return None

    vertical, conf = classify(title, o.get("categoryId") or o.get("category_id"))
    grader, grade, grade_label = pt.parse_grade(title)
    best = o.get("isBestOfferAccepted")
    if best is None:
        best = str(o.get("best_offer_accepted", "")).lower() in ("true", "1")
    fmt = o.get("format") or ("Auction" if o.get("buyingFormat") == "auction" else "Buy It Now")

    return (
        item_id, title, vertical, conf, price, total, sold, bool(best), fmt,
        inum(o.get("bidCount") or o.get("bids")),
        o.get("condition"), o.get("sellerUsername") or o.get("seller"),
        str(o.get("categoryId") or o.get("category_id") or ""),
        bool(JUNK.search(title)),
        o.get("url"),
        o.get("fullResThumbnailUrl") or o.get("thumbnailUrl") or o.get("image"),
        pt.parse_year(title), pt.parse_brand(title), pt.parse_card_number(title),
        pt.parse_parallel(title), inum(pt.parse_print_run(title)),
        grader, grade, grade_label,
        pt.has_auto(title), pt.is_rookie(title), find_player(title),
    )


COLS = """item_id title vertical vertical_confidence sold_price total_price sold_date
best_offer_accepted listing_format bids condition seller ebay_category_id is_junk url
image_url card_year brand_set card_number parallel print_run grader grade grade_label
is_auto is_rookie player""".split()

UPSERT = f"""
INSERT INTO sales ({','.join(COLS)}) VALUES %s
ON CONFLICT (item_id) DO UPDATE SET
  {', '.join(f'{c}=EXCLUDED.{c}' for c in COLS if c != 'item_id')},
  updated_at = now()
"""


def read_rows(paths, csv_path):
    if csv_path:
        opener = gzip.open if str(csv_path).endswith(".gz") else open
        with opener(csv_path, "rt", newline="") as fh:
            yield from csv.DictReader(fh)
        return
    for pattern in paths:
        for p in sorted(glob.glob(pattern)):
            with open(p) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    o = json.loads(line)
                    if o.get("_analytics") or o.get("type") == "sold-price-summary":
                        continue
                    yield o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", help="JSONL files or globs")
    ap.add_argument("--csv", help="load from a built CSV instead")
    ap.add_argument("--min-price", type=float, default=500.0)
    ap.add_argument("--no-refresh", action="store_true")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Set DATABASE_URL")

    seen, recs = 0, {}
    for o in read_rows(args.paths, args.csv):
        seen += 1
        r = to_record(o)
        if r and r[5] >= args.min_price:
            recs[r[0]] = r                       # de-dupe in memory on item_id
    rows = list(recs.values())
    print(f"read {seen:,} rows -> {len(rows):,} unique, >= ${args.min_price:,.0f}")
    if not rows:
        sys.exit("nothing to load")

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO refresh_log (rows_seen) VALUES (%s) RETURNING id", (seen,))
        log_id = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM sales")
        before = cur.fetchone()[0]
        psycopg2.extras.execute_values(cur, UPSERT, rows, page_size=1000)
        cur.execute("SELECT count(*) FROM sales")
        after = cur.fetchone()[0]
        inserted = after - before
        cur.execute("SELECT min(sold_date), max(sold_date) FROM sales")
        w0, w1 = cur.fetchone()
        cur.execute(
            "UPDATE refresh_log SET finished_at=now(), rows_inserted=%s, rows_updated=%s,"
            " window_start=%s, window_end=%s, status='ok' WHERE id=%s",
            (inserted, len(rows) - inserted, w0, w1, log_id))
    print(f"inserted {inserted:,} new / updated {len(rows) - inserted:,} | table now {after:,}")

    if not args.no_refresh:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT refresh_all_views()")
        print("materialized views refreshed")
    conn.close()


if __name__ == "__main__":
    main()
