#!/usr/bin/env bash
# Daily refresh. Any failing step aborts so a partial load can never silently
# become "today's data".
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL}"
: "${APIFY_TOKEN:?set APIFY_TOKEN}"

MIN_PRICE=${MIN_PRICE:-2000}
WORK=${WORK:-/tmp/razmania}
rm -rf "$WORK/raw"; mkdir -p "$WORK/raw"

# ------------------------------------------------------- window calculation
# BASE = how many whole days behind the database is.
#
# BASE alone is NOT a safe window. eBay sales land around the clock, so by the
# time this runs (09:00 UTC) a sale dated *today* has usually already landed —
# which makes max(sold_date) = today, BASE = 0, and a 1-day window. That would
# scrape only the current day, every day, and never revisit the sales that
# completed after yesterday's run. A permanent, silent daily gap.
#
# Fix: always add 2. Yesterday is therefore always fully re-read, so the
# leaderboard is provably complete, and a missed run widens the next window
# automatically. Capped at 6 because eBay's sold search gets unreliable beyond
# about a week and a runaway window is a runaway bill.
BASE=$(psql "$DATABASE_URL" -tAc \
  "SELECT GREATEST(COALESCE(current_date - max(sold_date), 2), 0) FROM sales")
DAYS=$(( BASE + 2 )); [ "$DAYS" -gt 6 ] && DAYS=6

echo "==> database is ${BASE}d behind -> ${DAYS}d window, floor \$${MIN_PRICE}"

echo "==> 1/4 scraping"
python3 etl/scrape.py --out "$WORK/raw" --days "$DAYS" --min-price "$MIN_PRICE"

echo "==> 2/4 loading + refreshing aggregates"
python3 etl/load.py "$WORK/raw/*.jsonl" --min-price "$MIN_PRICE"

echo "==> 3/4 retention + vacuum"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/retention.sql

echo "==> 4/4 freshness gate"
python3 - <<'PY'
import os, sys, datetime, psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"]); cur = conn.cursor()
cur.execute("SELECT max(sold_date), count(*) FROM sales")
last, n = cur.fetchone()
age = (datetime.date.today() - last).days
print(f"latest sale {last} ({age}d old), {n:,} rows")
# A refresh that "succeeds" while leaving stale prices on a card-value site is
# the failure mode that actually costs us. Fail loudly instead.
if age > 2:
    sys.exit(f"FAIL: newest sale is {age} days old - scrape likely returned nothing")
cur.execute("SELECT count(*) FROM mv_leaderboard_7d")
lb = cur.fetchone()[0]
if lb == 0:
    sys.exit("FAIL: leaderboard empty after refresh")
cur.execute("SELECT count(*) FROM sales WHERE sold_date >= current_date - 2")
print(f"leaderboard {lb:,} rows | last 48h: {cur.fetchone()[0]:,} sales")
print("freshness OK")
PY
echo "==> done"
