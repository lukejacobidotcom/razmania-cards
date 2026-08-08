#!/usr/bin/env bash
# Daily refresh. Any failing step aborts so a partial load can never silently
# become "today's data".
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL}"
: "${APIFY_TOKEN:?set APIFY_TOKEN}"

MIN_PRICE=${MIN_PRICE:-2000}      # nothing below this is collected or published
HOT_FLOOR=${HOT_FLOOR:-10000}     # the slice that gets a DAILY scrape
TAIL_DOW=${TAIL_DOW:-0}           # weekday the cheap slice runs (0 = Sunday)
WORK=${WORK:-/tmp/razmania}
rm -rf "$WORK/raw"; mkdir -p "$WORK/raw"

# ============================================================================
# WHY TWO TIERS
#
# Apify bills per row RETURNED. A 2-day window run daily therefore pays for
# every sale TWICE — the overlap is insurance, and on this dataset that
# insurance was costing more than the data.
#
# Measured over the loaded week, at $2,000+:
#
#   $10,000+        68 sales/day    ALL of the top 50 weekly sales
#   $5,000-9,999   134 sales/day    none of the top 50
#   $3,000-4,999   215 sales/day    none of the top 50
#   $2,000-2,999   323 sales/day    none of the top 50
#
# Every single sale on the homepage leaderboard came from the $10,000+ slice —
# which is 9% of the volume. The other 91% only moves medians and GMV, and a
# median does not change materially between Tuesday and Wednesday.
#
# So: scrape the leaderboard slice DAILY (it must never miss a big sale), and
# the median slice WEEKLY on an 8-day window. Same rows collected, ~1.1x
# redundancy instead of 2x. ~$111/mo -> ~$68/mo with zero coverage loss.
#
# The two ranges are disjoint, so no row is ever billed twice in a week.
# ============================================================================

sql() { psql "$DATABASE_URL" -tAc "$1"; }

# ------------------------------------------------------------- HOT tier, daily
# BASE alone is NOT a safe window. eBay sales land around the clock, so by the
# time this runs (09:00 UTC) a sale dated *today* has usually already landed —
# which makes max(sold_date) = today, BASE = 0, and a 1-day window. That would
# scrape only the current day, every day, and never revisit sales that completed
# after yesterday's run. A permanent, silent daily gap. Hence the +2.
HOT_BASE=$(sql "SELECT GREATEST(COALESCE(current_date - max(sold_date), 2), 0)
                FROM sales WHERE total_price >= $HOT_FLOOR")
HOT_DAYS=$(( HOT_BASE + 2 )); [ "$HOT_DAYS" -gt 6 ] && HOT_DAYS=6

echo "==> HOT  \$$HOT_FLOOR+ : ${HOT_BASE}d behind -> ${HOT_DAYS}d window"
echo "==> 1/4 scraping"
python3 etl/scrape.py --out "$WORK/raw" --days "$HOT_DAYS" --min-price "$HOT_FLOOR"

# ------------------------------------------------------ TAIL tier, weekly-ish
# Runs on TAIL_DOW, or any day the tail has drifted past 8 days stale (so a
# missed week self-heals instead of silently rotting the medians).
TAIL_BASE=$(sql "SELECT GREATEST(COALESCE(current_date - max(sold_date), 8), 0)
                 FROM sales
                 WHERE total_price >= $MIN_PRICE AND total_price < $HOT_FLOOR")
TODAY_DOW=$(date -u +%w)

if [ "$TODAY_DOW" = "$TAIL_DOW" ] || [ "$TAIL_BASE" -ge 8 ]; then
  TAIL_DAYS=$(( TAIL_BASE + 1 )); [ "$TAIL_DAYS" -lt 8 ] && TAIL_DAYS=8
  [ "$TAIL_DAYS" -gt 9 ] && TAIL_DAYS=9
  echo "==> TAIL \$$MIN_PRICE-$((HOT_FLOOR-1)) : ${TAIL_BASE}d behind -> ${TAIL_DAYS}d window"
  python3 etl/scrape.py --out "$WORK/raw" --days "$TAIL_DAYS" \
          --min-price "$MIN_PRICE" --max-price "$((HOT_FLOOR - 1))"
else
  echo "==> TAIL skipped (runs on dow=$TAIL_DOW, today=$TODAY_DOW, ${TAIL_BASE}d behind)"
fi

echo "==> 2/4 loading + refreshing aggregates"
python3 etl/load.py "$WORK/raw/*.jsonl" --min-price "$MIN_PRICE"

echo "==> 3/4 retention + vacuum"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/retention.sql

echo "==> 4/4 freshness gate"
MIN_PRICE="$MIN_PRICE" HOT_FLOOR="$HOT_FLOOR" python3 - <<'PY'
import os, sys, datetime, psycopg2
hot = int(os.environ["HOT_FLOOR"]); floor = int(os.environ["MIN_PRICE"])
conn = psycopg2.connect(os.environ["DATABASE_URL"]); cur = conn.cursor()

cur.execute("SELECT max(sold_date), count(*) FROM sales WHERE total_price >= %s", (hot,))
last, n = cur.fetchone()
age = (datetime.date.today() - last).days if last else 999
print(f"hot tier: latest {last} ({age}d old), {n:,} rows")
# A refresh that "succeeds" while leaving stale prices on a card-value site is
# the failure mode that actually costs us. Fail loudly instead.
if age > 2:
    sys.exit(f"FAIL: newest ${hot:,}+ sale is {age} days old - scrape returned nothing")

cur.execute("SELECT max(sold_date) FROM sales WHERE total_price >= %s AND total_price < %s",
            (floor, hot))
tail = cur.fetchone()[0]
tail_age = (datetime.date.today() - tail).days if tail else 999
print(f"tail tier: latest {tail} ({tail_age}d old)")
# The tail is deliberately weekly, so staleness here is a WARNING, not a failure
# — but past 10 days it means the weekly run is not firing at all.
if tail_age > 10:
    sys.exit(f"FAIL: tail is {tail_age} days old - the weekly run is not firing")
if tail_age > 8:
    print(f"WARN: tail {tail_age}d old, weekly run due")

cur.execute("SELECT count(*) FROM mv_leaderboard_7d")
lb = cur.fetchone()[0]
if lb == 0:
    sys.exit("FAIL: leaderboard empty after refresh")
print(f"leaderboard {lb:,} rows")
print("freshness OK")
PY
echo "==> done"
