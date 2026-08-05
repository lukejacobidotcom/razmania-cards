-- Retention + maintenance. Runs after every daily load.
--
-- At ~3,100 sales/day the table grows ~1.1M rows and roughly 0.7 GB per year.
-- Render Postgres bills expandable storage at $0.30/GB, so this is cheap to
-- keep — but the aggregates only ever look back 90 days, and eBay itself only
-- exposes ~90 days of sold data, so rows older than the retention window are
-- dead weight in every index.
--
-- Set to 400 days: long enough for year-over-year comparisons once we have the
-- history, short enough that indexes stay small. Raise it, don't lower it —
-- deleted history is NOT recoverable, because eBay cannot be re-scraped for it.

\set retention_days 400

DELETE FROM sales
WHERE sold_date < current_date - (:retention_days || ' days')::interval;

-- Keep the refresh audit trail bounded too.
DELETE FROM refresh_log WHERE started_at < now() - INTERVAL '180 days';

-- The daily upsert churns rows, which leaves dead tuples and stale planner
-- stats. Without this the trigram search and the date-range scans degrade.
VACUUM (ANALYZE) sales;
ANALYZE refresh_log;
