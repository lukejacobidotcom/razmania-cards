#!/usr/bin/env bash
# Daily refresh. Any failing step aborts so a partial load can never silently
# become "today's data".
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL}"
: "${APIFY_TOKEN:?set APIFY_TOKEN}"

MIN_PRICE=${MIN_PRICE:-2000}      # nothing below this is collected or published
HOT_FLOOR=${HOT_FLOOR:-10000}     # the slice that gets a DAILY scrape
TAIL_EVERY=${TAIL_EVERY:-3}       # days between cheap-slice scrapes (0 = off)
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

# ------------------------------------------------------------ schema migration
# db/schema.sql is idempotent and is the single source of truth for every view.
# Apply it whenever its content hash differs from the one recorded at the last
# apply, so a schema change ships by `git push` and lands on the next run. No
# hand-run psql against production, and no way for the repo and the database
# to drift apart. Runs BEFORE any scraping so a broken schema costs $0 in Apify.
#
# The apply drops and rebuilds every materialized view, so /v1 endpoints that
# read them can 500 for the few seconds it takes. That is why this runs at
# 05:00 ET and not on a page view.
SCHEMA_HASH=$(md5sum db/schema.sql | cut -c1-32)
APPLIED=$(sql "SELECT v FROM schema_meta WHERE k='schema_hash'" 2>/dev/null || true)
if [ "$SCHEMA_HASH" != "$APPLIED" ]; then
  echo "==> 0/4 schema changed (${APPLIED:-none} -> $SCHEMA_HASH): applying db/schema.sql"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f db/schema.sql
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c \
    "INSERT INTO schema_meta (k,v) VALUES ('schema_hash','$SCHEMA_HASH')
     ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v;"
else
  echo "==> schema up to date ($SCHEMA_HASH)"
fi

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

# --------------------------------------------- TAIL tier, every TAIL_EVERY days
# CADENCE AND WINDOW MOVE TOGETHER, OR THE CHANGE IS WORSE THAN USELESS.
#
# Apify bills per row RETURNED, so this tier's cost is driven by how many times
# each row is re-bought, not by how much data arrives. A cadence of k days needs
# a k+1 day window, which makes the redundancy factor (1 + 1/k):
#
#   k=7 (was)   1.14x    $58/mo    index settles in  9 days
#   k=3 (now)   1.33x    $68/mo    index settles in  4 days
#   k=2         1.50x    $77/mo    index settles in  3 days
#   k=1         2.00x   $102/mo    index settles in  2 days
#
# Cutting the lag from 9 days to 4 therefore costs $10/mo. Going all the way to
# daily costs $44/mo more for two further days, because at k=1 every row is
# bought twice purely as overlap insurance.
#
# The old code floored the window at 8 days regardless of cadence. Running THAT
# every 3 days would have cost ~$136/mo — worse than daily, for a worse lag —
# which is why the floor below is derived from TAIL_EVERY instead of fixed.
if [ "$TAIL_EVERY" -gt 0 ]; then
  TAIL_BASE=$(sql "SELECT GREATEST(COALESCE(current_date - max(sold_date), $TAIL_EVERY), 0)
                   FROM sales
                   WHERE total_price >= $MIN_PRICE AND total_price < $HOT_FLOOR")

  if [ "$TAIL_BASE" -ge "$TAIL_EVERY" ]; then
    TAIL_DAYS=$(( TAIL_BASE + 1 ))
    [ "$TAIL_DAYS" -lt $(( TAIL_EVERY + 1 )) ] && TAIL_DAYS=$(( TAIL_EVERY + 1 ))
    [ "$TAIL_DAYS" -gt $(( TAIL_EVERY + 2 )) ] && TAIL_DAYS=$(( TAIL_EVERY + 2 ))
    echo "==> TAIL \$$MIN_PRICE-$((HOT_FLOOR-1)) : ${TAIL_BASE}d behind -> ${TAIL_DAYS}d window (every ${TAIL_EVERY}d)"
    python3 etl/scrape.py --out "$WORK/raw" --days "$TAIL_DAYS" \
            --min-price "$MIN_PRICE" --max-price "$((HOT_FLOOR - 1))"
  else
    echo "==> TAIL skipped (${TAIL_BASE}d behind, cadence is ${TAIL_EVERY}d)"
  fi
else
  echo "==> TAIL disabled (TAIL_EVERY=0) — leaderboard unaffected, no medians"
fi

# ---------------------------------------------------------- publish the config
# The index's settle lag is DERIVED FROM THIS, not hardcoded in db/schema.sql,
# so the scraper's cadence and the date the site calls "settled" cannot drift
# apart. This must run BEFORE load.py, because load.py calls refresh_all_views()
# and the index reads these values as it is built.
#
# TAIL_EVERY=0 means the $2,000-9,999 tier is never collected, so the 'all' tier
# can never settle — its recent windows would be permanently hot-only. Rather
# than publish a distorted broad index, settle it into the far future so nothing
# qualifies and only the blue-chip index appears. Deliberately visible, not a
# silent degradation.
if [ "$TAIL_EVERY" -gt 0 ]; then SETTLE_ALL=$(( TAIL_EVERY + 1 )); else SETTLE_ALL=36500; fi
echo "==> config: hot_floor=$HOT_FLOOR settle_all=${SETTLE_ALL}d settle_bluechip=2d"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c \
  "INSERT INTO schema_meta (k,v) VALUES
     ('hot_floor','$HOT_FLOOR'),
     ('index_settle_days_all','$SETTLE_ALL'),
     ('index_settle_days_bluechip','2')
   ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v;"

echo "==> 2/4 loading + refreshing aggregates"
python3 etl/load.py "$WORK/raw/*.jsonl" --min-price "$MIN_PRICE"

echo "==> 3/4 retention + vacuum"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/retention.sql

echo "==> 4/4 freshness gate"
MIN_PRICE="$MIN_PRICE" HOT_FLOOR="$HOT_FLOOR" TAIL_EVERY="$TAIL_EVERY" python3 - <<'PY'
import os, sys, datetime, psycopg2
hot = int(os.environ["HOT_FLOOR"]); floor = int(os.environ["MIN_PRICE"])
every = int(os.environ["TAIL_EVERY"])
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
print(f"tail tier: latest {tail} ({tail_age}d old), cadence {every}d")
# The tail is deliberately intermittent, so staleness here is a WARNING, not a
# failure — but well past its cadence it means the run is not firing at all.
# Both thresholds are derived from TAIL_EVERY: hardcoding 8/10 here would start
# lying the moment the cadence changed, which is the same class of bug as
# hardcoding the settle offset in SQL.
if every == 0:
    print("tail disabled (TAIL_EVERY=0); broad index will publish nothing")
elif tail_age > every + 7:
    sys.exit(f"FAIL: tail is {tail_age}d old on a {every}d cadence - the run is not firing")
elif tail_age > every + 1:
    print(f"WARN: tail {tail_age}d old on a {every}d cadence, run is due")

cur.execute("SELECT count(*) FROM mv_leaderboard_7d")
lb = cur.fetchone()[0]
if lb == 0:
    sys.exit("FAIL: leaderboard empty after refresh")
print(f"leaderboard {lb:,} rows")
print("freshness OK")
PY
echo "==> done"
