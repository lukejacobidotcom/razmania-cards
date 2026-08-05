#!/usr/bin/env bash
# Daily refresh. Any failing step aborts so a partial load can never silently
# become "today's data".
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL}"
: "${APIFY_TOKEN:?set APIFY_TOKEN}"

TIER=${TIER:-full}          # full ($500+) | top ($2,000+)
WORK=${WORK:-/tmp/razmania}
rm -rf "$WORK/raw"; mkdir -p "$WORK/raw"

# ---------------------------------------------------------------- adaptive window
# You pay Apify per sale RETURNED, so a fixed 2-day window doubles the bill every
# single day just to insure against the occasional missed run. Instead: look at
# how stale the database actually is and scrape exactly that far back (+1 day of
# safety). Normal day => 1 day of data. After a failed run => it widens by itself.
# Halves the running cost versus a fixed 2-day window, with the same self-healing.
GAP=$(psql "$DATABASE_URL" -tAc \
  "SELECT LEAST(GREATEST(COALESCE(current_date - max(sold_date), 3) + 1, 1), 5) FROM sales")
DAYS=${DAYS:-$GAP}
echo "==> database is $(( DAYS - 1 ))d behind; scraping a ${DAYS}d window (tier=$TIER)"

echo "==> 1/4 scraping"
python3 etl/scrape.py --out "$WORK/raw" --tier "$TIER" --days "$DAYS"

echo "==> 2/4 loading + refreshing aggregates"
python3 etl/load.py "$WORK/raw/*.jsonl"

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
# On a daily cadence anything over 2 days old means the scrape came back empty.
# A refresh that "succeeds" while leaving stale prices on a card-value site is
# the failure mode that actually costs us.
if age > 2:
    sys.exit(f"FAIL: newest sale is {age} days old — scrape likely returned nothing")
cur.execute("SELECT count(*) FROM mv_leaderboard_7d")
lb = cur.fetchone()[0]
if lb == 0:
    sys.exit("FAIL: leaderboard empty after refresh")
cur.execute("SELECT count(*) FROM sales WHERE sold_date >= current_date - 2")
print(f"leaderboard {lb:,} rows | last 48h: {cur.fetchone()[0]:,} sales")
print("freshness OK")
PY
echo "==> done"
