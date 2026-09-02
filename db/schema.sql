-- ============================================================================
-- RazMania card-sales warehouse — schema
-- Target: Render Postgres 16 (Basic-256mb is enough for years of this data)
--
-- Design rule: the DATABASE does the heavy, slow, set-wide aggregation once per
-- refresh (materialized views). The FRONT END does only cheap, per-request
-- presentation work. Nothing that scans the full sales table ever runs inside a
-- page request.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- fuzzy title search
CREATE EXTENSION IF NOT EXISTS unaccent;

-- ---------------------------------------------------------------- raw facts
-- One row per eBay listing. item_id is eBay's own ID and is the natural key,
-- which makes every reload idempotent — re-running an overlapping scrape
-- updates rather than duplicates.
CREATE TABLE IF NOT EXISTS sales (
    item_id             TEXT PRIMARY KEY,
    title               TEXT        NOT NULL,
    vertical            TEXT        NOT NULL,
    vertical_confidence TEXT        NOT NULL DEFAULT 'low',
    sold_price          NUMERIC(12,2),
    total_price         NUMERIC(12,2) NOT NULL,
    sold_date           DATE        NOT NULL,
    best_offer_accepted BOOLEAN     NOT NULL DEFAULT FALSE,
    listing_format      TEXT,                       -- 'Auction' | 'Buy It Now'
    bids                INTEGER,
    condition           TEXT,
    seller              TEXT,
    ebay_category_id    TEXT,
    is_junk             BOOLEAN     NOT NULL DEFAULT FALSE,
    url                 TEXT,
    image_url           TEXT,
    -- Parsed card attributes (populated by the title parser; nullable by design)
    card_year           SMALLINT,
    brand_set           TEXT,
    card_number         TEXT,
    parallel            TEXT,
    print_run           INTEGER,
    grader              TEXT,
    grade               NUMERIC(3,1),
    grade_label         TEXT,
    is_auto             BOOLEAN     NOT NULL DEFAULT FALSE,
    is_rookie           BOOLEAN     NOT NULL DEFAULT FALSE,
    player              TEXT,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON COLUMN sales.best_offer_accepted IS
  'TRUE means eBay published the seller ASKING price, not the accepted offer. '
  'These rows are price CEILINGS. Every published stat must exclude them.';

CREATE TABLE IF NOT EXISTS schema_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);

-- A single boolean that encodes "this row is safe to publish a price from".
-- Defined once here so the API, the views and the front end can never disagree.
--
-- Three conditions:
--   1. not best-offer-accepted  — eBay publishes the ASKING price on those
--   2. not junk                 — lots, reprints, "u pick", custom
--   3. at or above the publish floor
--
-- (3) is the important one. etl/scrape.py only COLLECTS at or above MIN_PRICE
-- (default $2,000, ~739 sales/day, ~$111/mo at Apify's per-row billing). If the
-- views were allowed to aggregate below that floor they would publish medians
-- over a range we only partially collect — a truncated sample presented as a
-- market read. Enforcing the floor here means every view, index and API
-- endpoint inherits it automatically and the site physically cannot publish a
-- number it can't back.
--
-- Rows BELOW the floor are still stored (the $500+ seed backfill, plus anything
-- collected before the floor was raised). They stay available to /v1/search and
-- to a future backfill; they just never reach a published aggregate.
--
-- Change publish_floor below to change coverage. The column is STORED and the
-- views depend on it, so the block rebuilds both only when the floor moves.
DO $$
DECLARE
    publish_floor CONSTANT int := 2000;
    built int;
BEGIN
    SELECT v::int INTO built FROM schema_meta WHERE k = 'publish_floor';
    IF built IS DISTINCT FROM publish_floor
       OR NOT EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_name = 'sales' AND column_name = 'is_publishable') THEN
        RAISE NOTICE 'building is_publishable with floor $%', publish_floor;
        ALTER TABLE sales DROP COLUMN IF EXISTS is_publishable CASCADE;
        EXECUTE format(
            'ALTER TABLE sales ADD COLUMN is_publishable BOOLEAN GENERATED ALWAYS AS '
            '(NOT best_offer_accepted AND NOT is_junk AND total_price >= %s) STORED',
            publish_floor);
        INSERT INTO schema_meta (k, v) VALUES ('publish_floor', publish_floor::text)
            ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS sales_sold_date_idx     ON sales (sold_date DESC);
CREATE INDEX IF NOT EXISTS sales_vertical_idx      ON sales (vertical, sold_date DESC);
CREATE INDEX IF NOT EXISTS sales_price_idx         ON sales (total_price DESC);
CREATE INDEX IF NOT EXISTS sales_publishable_idx   ON sales (is_publishable, sold_date DESC)
    WHERE is_publishable;
CREATE INDEX IF NOT EXISTS sales_player_idx        ON sales (player) WHERE player IS NOT NULL;
CREATE INDEX IF NOT EXISTS sales_card_idx          ON sales (card_year, brand_set, card_number);
CREATE INDEX IF NOT EXISTS sales_title_trgm_idx    ON sales USING gin (title gin_trgm_ops);

-- Bookkeeping so a silent stale-data failure is impossible to miss.
CREATE TABLE IF NOT EXISTS refresh_log (
    id           BIGSERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    rows_seen    INTEGER,
    rows_inserted INTEGER,
    rows_updated INTEGER,
    window_start DATE,
    window_end   DATE,
    status       TEXT NOT NULL DEFAULT 'running',
    note         TEXT
);

-- ============================================================================
-- AGGREGATE LAYER — refreshed once per load, never during a page request.
-- ============================================================================

-- 1. Daily market pulse per vertical. Powers sparklines and the "market index".
DROP MATERIALIZED VIEW IF EXISTS mv_daily_vertical CASCADE;
CREATE MATERIALIZED VIEW mv_daily_vertical AS
SELECT
    vertical,
    sold_date,
    count(*)                                              AS sales,
    count(*) FILTER (WHERE is_publishable)                AS confirmed_sales,
    sum(total_price) FILTER (WHERE is_publishable)        AS gmv,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY total_price)
        FILTER (WHERE is_publishable)                     AS median_price,
    avg(total_price) FILTER (WHERE is_publishable)        AS avg_price,
    max(total_price) FILTER (WHERE is_publishable)        AS max_price
FROM sales
GROUP BY vertical, sold_date;
CREATE UNIQUE INDEX mv_daily_vertical_pk ON mv_daily_vertical (vertical, sold_date);

-- 2. Rolling 7-day leaderboard. The single most-requested object on the site.
DROP MATERIALIZED VIEW IF EXISTS mv_leaderboard_7d CASCADE;
CREATE MATERIALIZED VIEW mv_leaderboard_7d AS
WITH win AS (SELECT max(sold_date) AS d FROM sales)
SELECT
    row_number() OVER (ORDER BY s.total_price DESC, s.item_id) AS rank,
    dense_rank() OVER (PARTITION BY s.vertical
                       ORDER BY s.total_price DESC, s.item_id) AS rank_in_vertical,
    s.item_id, s.title, s.vertical, s.total_price, s.sold_date,
    s.listing_format, s.bids, s.grade_label, s.player,
    s.url, s.image_url
FROM sales s, win
WHERE s.is_publishable
  AND s.sold_date > win.d - INTERVAL '7 days';
CREATE UNIQUE INDEX mv_leaderboard_7d_pk ON mv_leaderboard_7d (item_id);
CREATE INDEX mv_leaderboard_7d_rank ON mv_leaderboard_7d (rank);
CREATE INDEX mv_leaderboard_7d_vert ON mv_leaderboard_7d (vertical, rank_in_vertical);

-- 3. Per-card comps. The spine of every "what is X worth" page.
--    n >= 3 is enforced here so an under-evidenced price can never reach the site.
DROP MATERIALIZED VIEW IF EXISTS mv_card_comps CASCADE;
CREATE MATERIALIZED VIEW mv_card_comps AS
SELECT
    md5(concat_ws('|', coalesce(player,''), coalesce(card_year::text,''),
                       coalesce(brand_set,''), coalesce(card_number,''),
                       coalesce(parallel,''), coalesce(grade_label,'Raw')))  AS card_key,
    player, card_year, brand_set, card_number, parallel,
    coalesce(grade_label, 'Raw')                          AS grade_label,
    bool_or(is_rookie)                                    AS is_rookie,
    bool_or(is_auto)                                      AS is_auto,
    count(*)                                              AS sales,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY total_price) AS median_price,
    percentile_cont(0.25) WITHIN GROUP (ORDER BY total_price) AS p25,
    percentile_cont(0.75) WITHIN GROUP (ORDER BY total_price) AS p75,
    min(total_price)                                      AS min_price,
    max(total_price)                                      AS max_price,
    max(sold_date)                                        AS last_sold,
    (array_agg(image_url ORDER BY total_price DESC))[1]   AS image_url,
    (array_agg(url ORDER BY sold_date DESC))[1]           AS sample_url
FROM sales
WHERE is_publishable AND player IS NOT NULL
GROUP BY player, card_year, brand_set, card_number, parallel, coalesce(grade_label,'Raw')
HAVING count(*) >= 3;
CREATE UNIQUE INDEX mv_card_comps_pk ON mv_card_comps (card_key);
CREATE INDEX mv_card_comps_player ON mv_card_comps (player);

-- 4. Player rollup. Powers /card-values/<player>/ index pages.
DROP MATERIALIZED VIEW IF EXISTS mv_player_summary CASCADE;
CREATE MATERIALIZED VIEW mv_player_summary AS
SELECT
    player,
    mode() WITHIN GROUP (ORDER BY vertical)               AS vertical,
    count(*)                                              AS sales,
    sum(total_price)                                      AS gmv,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY total_price) AS median_price,
    max(total_price)                                      AS top_sale,
    max(sold_date)                                        AS last_sold,
    lower(regexp_replace(player, '[^a-zA-Z0-9]+', '-', 'g')) AS slug
FROM sales
WHERE is_publishable AND player IS NOT NULL
GROUP BY player;
CREATE UNIQUE INDEX mv_player_summary_pk ON mv_player_summary (player);
CREATE INDEX mv_player_summary_slug ON mv_player_summary (slug);

-- 5. Week-over-week vertical movement — the recurring editorial story.
DROP MATERIALIZED VIEW IF EXISTS mv_vertical_wow CASCADE;
CREATE MATERIALIZED VIEW mv_vertical_wow AS
WITH win AS (SELECT max(sold_date) AS d FROM sales),
this_week AS (
    SELECT vertical, count(*) AS sales, sum(total_price) AS gmv,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY total_price) AS median_price,
           max(total_price) AS top_sale
    FROM sales, win
    WHERE is_publishable AND sold_date > win.d - INTERVAL '7 days'
    GROUP BY vertical),
prior_week AS (
    SELECT vertical, count(*) AS sales, sum(total_price) AS gmv,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY total_price) AS median_price
    FROM sales, win
    WHERE is_publishable AND sold_date > win.d - INTERVAL '14 days'
                        AND sold_date <= win.d - INTERVAL '7 days'
    GROUP BY vertical)
SELECT
    t.vertical,
    t.sales, t.gmv, t.median_price, t.top_sale,
    p.sales AS prior_sales, p.gmv AS prior_gmv, p.median_price AS prior_median,
    CASE WHEN p.gmv > 0 THEN round(((t.gmv - p.gmv) / p.gmv * 100)::numeric, 1) END AS gmv_pct_change,
    CASE WHEN p.median_price > 0
         THEN round(((t.median_price - p.median_price) / p.median_price * 100)::numeric, 1) END AS median_pct_change
FROM this_week t LEFT JOIN prior_week p USING (vertical);
CREATE UNIQUE INDEX mv_vertical_wow_pk ON mv_vertical_wow (vertical);

-- 6. Small, cacheable site-wide header stats.
DROP MATERIALIZED VIEW IF EXISTS mv_site_stats CASCADE;
CREATE MATERIALIZED VIEW mv_site_stats AS
SELECT
    -- Both counts respect the publish floor. total_sales deliberately does NOT
    -- count every row in the table: rows below the floor exist but are not part
    -- of what the site claims to track, and a headline number the data can't
    -- back is worse than no headline number.
    (SELECT count(*) FROM sales
      WHERE total_price >= (SELECT v::numeric FROM schema_meta
                             WHERE k = 'publish_floor'))       AS total_sales,
    (SELECT count(*) FROM sales WHERE is_publishable)         AS confirmed_sales,
    (SELECT sum(total_price) FROM sales WHERE is_publishable) AS total_gmv,
    (SELECT max(total_price) FROM sales WHERE is_publishable) AS biggest_sale,
    (SELECT min(sold_date) FROM sales)                        AS first_date,
    (SELECT max(sold_date) FROM sales)                        AS last_date,
    (SELECT count(DISTINCT player) FROM sales WHERE player IS NOT NULL) AS players_tracked,
    -- Share of listings at/above the floor that are best-offer-accepted, i.e.
    -- the share whose published "sold" price is really the asking price. This
    -- is the number the index methodology cites, so it lives here, refreshed
    -- once a day, rather than being scanned per page view.
    (SELECT round(avg(best_offer_accepted::int)::numeric, 4) FROM sales
      WHERE total_price >= (SELECT v::numeric FROM schema_meta
                             WHERE k = 'publish_floor'))       AS best_offer_share,
    now()                                                     AS generated_at;


-- ============================================================================
-- 7. THE RAZMANIA INDEX — trailing-7-day price level, rebased to 100.
--
-- Published in TWO TIERS off the same machinery, because the scrape cadence
-- that makes one of them cheap is what makes the other one slow:
--
--   'bluechip'  $10,000+ only. That tier is scraped DAILY, so it settles in
--               2 days. ~9% of volume. This is the live, quotable number.
--   'all'       everything at or above publish_floor. The $2,000-9,999 tail is
--               scraped every TAIL_EVERY days, so it settles more slowly. This
--               is the broad market read.
--
-- Four things would each, on their own, turn this into a chart that lies. All
-- four are handled here rather than in the API or the front end, for the same
-- reason is_publishable lives in the table: one definition, no way to bypass it.
--
-- (a) SCRAPE CADENCE moves the recent days, not the market.
--     A tier's most recent days are incomplete until its next scrape lands. For
--     'all' those days hold the hot tier only — the most expensive 9% — and the
--     composite reads +38% high (+118% before the n>=20 floor below removes the
--     thinnest windows), then "corrects" when the tail arrives. Published as a
--     chart that is a weekly sawtooth showing the hobby doubling and crashing.
--     Pure artefact. Every row carries `settled`, and the API serves only
--     settled points unless explicitly asked otherwise.
--
--     THE LAG IS CONFIG-DRIVEN, NOT HARDCODED. It comes from schema_meta, which
--     etl/refresh_daily.sh rewrites from HOT_FLOOR and TAIL_EVERY on every run.
--     Change the cadence and the lag follows by itself. A hardcoded offset here
--     would silently publish unsettled days the first time the cadence moved,
--     which is precisely the failure this whole section exists to prevent.
--
-- (b) MIX SHIFT is not price movement. A quiet week in Basketball and a loud
--     one in Pokemon moves a pooled median without one card changing hands at
--     a different price. So each composite is a fixed-weight index: verticals
--     are rebased to 100 at their own base window, and 'All' weights them by
--     base-period GMV share.
--
-- (c) THE BASE MUST NOT DRIFT. If base levels were recomputed from `sales` on
--     every refresh, db/retention.sql pruning past 400 days would eventually
--     delete the base window and silently rebase every historical value. An
--     index whose history changes underneath it is not an index. Base date,
--     base median and weights are PINNED ONCE per (tier, vertical).
--
-- (d) 'Unknown' is excluded outright. It is ~17% of rows and it is a residue
--     bucket, not a market — its median tracks etl/classify.py, not the hobby.
--
-- MIN WINDOW SIZE. A window needs >= 20 confirmed sales to produce a point.
-- Card prices are roughly lognormal with sigma ~= 0.78 (measured on
-- seed/sales_all.csv.gz), and the standard error of a median is about
-- 1.253 * sigma / sqrt(n) in log terms:
--
--     n=5  -> +/-44%   a weekly median indistinguishable from noise
--     n=20 -> +/-22%
--     n=50 -> +/-14%
--
-- At n=5 a thin vertical prints +41% one week and -49% the next while nothing
-- actually happened. So thin verticals show a GAP instead of a line. This bites
-- hardest on 'bluechip', which is only ~68 sales/day in total: expect a handful
-- of constituents there, not the full category list. That is the honest
-- outcome, not a bug to tune away.
--
-- The threshold is defined ONCE, in v_index_windows, which both pin_index_base()
-- and mv_market_index read. It used to be written twice; if the two ever
-- disagreed, a vertical could be pinned on a window the view then refused to
-- produce, and it would have a base but never a series.
-- ============================================================================

-- 7a. Config the index reads. etl/refresh_daily.sh rewrites these every run so
-- the scraper's cadence and the index's lag can never disagree. Seeded here
-- with the shipped defaults so a fresh database is valid before the first run.
INSERT INTO schema_meta (k, v) VALUES
    ('hot_floor',                   '10000'),
    ('index_settle_days_all',       '4'),
    ('index_settle_days_bluechip',  '2')
ON CONFLICT (k) DO NOTHING;

-- 7b. The pinned base period. Written once per (tier, vertical), then never
-- again.
CREATE TABLE IF NOT EXISTS index_base (
    tier        TEXT        NOT NULL,
    vertical    TEXT        NOT NULL,
    base_date   DATE        NOT NULL,
    base_median NUMERIC     NOT NULL,
    -- NULL = has its own index line but is NOT a composite constituent. The
    -- constituent set is frozen at launch: a vertical qualifying later would
    -- enter at 100 while the others sit at 130, and the renormalised mean would
    -- step down on a day when no price moved.
    weight      NUMERIC,
    -- Share of the base window's sales at/above hot_floor. The composition
    -- guard in mv_market_index compares every later window against this.
    base_hot_share NUMERIC,
    pinned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tier, vertical)
);
ALTER TABLE index_base ADD COLUMN IF NOT EXISTS base_hot_share NUMERIC;

-- Migrate a pre-tier index_base in place. Dropping and rebuilding it would
-- rebase published history, which is the one thing this table exists to stop.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'index_base' AND column_name = 'tier') THEN
        RAISE NOTICE 'migrating index_base to tiered layout, preserving pinned base';
        ALTER TABLE index_base ADD COLUMN tier TEXT NOT NULL DEFAULT 'all';
        ALTER TABLE index_base DROP CONSTRAINT index_base_pkey;
        ALTER TABLE index_base ADD PRIMARY KEY (tier, vertical);
        ALTER TABLE index_base ALTER COLUMN tier DROP DEFAULT;
    END IF;
END $$;

COMMENT ON TABLE index_base IS
  'Pinned base period for mv_market_index, per (tier, vertical). Append-only by '
  'design: rows are inserted once by pin_index_base() and must never be updated '
  'or deleted, or every historical index value silently changes. To relaunch a '
  'tier with a new base, DELETE that tier deliberately and say so publicly. '
  'NOTE: changing publish_floor or hot_floor INVALIDATES the affected base '
  'medians -- they were measured over a different price range, so the index '
  'would print a crash caused purely by the config change. A floor move '
  'requires a deliberate relaunch, not a silent re-pin.';

-- 7c. The trailing windows, per tier. ONE definition, read by both the pin
-- function and the materialized view.
DROP MATERIALIZED VIEW IF EXISTS mv_market_index CASCADE;
DROP VIEW IF EXISTS v_index_windows CASCADE;
CREATE VIEW v_index_windows AS
WITH cfg AS (
    SELECT (SELECT v::numeric FROM schema_meta WHERE k = 'hot_floor') AS hot_floor
),
tiers AS (
    -- 'all' needs no floor of its own: is_publishable already enforces
    -- publish_floor, and duplicating it here would be a second place to get
    -- the floor wrong.
    SELECT 'all'::text AS tier, 0::numeric AS min_price
    UNION ALL
    SELECT 'bluechip', (SELECT hot_floor FROM cfg)
),
bounds AS (
    SELECT min(sold_date) AS first_date, max(sold_date) AS last_date
      FROM sales WHERE is_publishable
),
cal AS (
    SELECT d::date AS as_of
      FROM bounds b,
           generate_series(b.first_date + 6, b.last_date, INTERVAL '1 day') d
)
SELECT t.tier,
       c.as_of,
       s.vertical,
       count(*)           AS sales,
       count(*) FILTER (WHERE s.total_price >= (SELECT hot_floor FROM cfg)) AS hot_sales,
       sum(s.total_price) AS gmv,
       -- ::numeric is load-bearing, not decorative. percentile_cont returns
       -- DOUBLE PRECISION, and round(double precision, integer) does not exist
       -- in Postgres — every round() downstream would fail at apply time.
       -- mv_vertical_wow casts for exactly the same reason.
       percentile_cont(0.5) WITHIN GROUP (ORDER BY s.total_price)::numeric
                          AS median_price
  FROM tiers t
  CROSS JOIN cal c
  JOIN sales s
    ON s.is_publishable
   AND s.vertical <> 'Unknown'
   AND s.total_price >= t.min_price
   AND s.sold_date <= c.as_of
   AND s.sold_date >  c.as_of - 7
 GROUP BY t.tier, c.as_of, s.vertical
HAVING count(*) >= 20;      -- THE min-window rule. Defined here and nowhere else.

-- 7d. Pin the base. Idempotent: ON CONFLICT DO NOTHING is what makes the base
-- permanent — every refresh calls this, and every call after the first is a
-- no-op for a (tier, vertical) already pinned.
CREATE OR REPLACE FUNCTION pin_index_base() RETURNS void AS $$
BEGIN
    WITH firsts AS (
        SELECT DISTINCT ON (tier, vertical) tier, vertical, as_of, median_price, gmv,
               hot_sales::numeric / sales AS hot_share
          FROM v_index_windows ORDER BY tier, vertical, as_of
    ),
    -- Weights go only to verticals qualifying on the FIRST day any vertical in
    -- that tier does, so each composite starts at exactly 100.00.
    launch AS (SELECT tier, min(as_of) AS d FROM firsts GROUP BY tier),
    already AS (SELECT DISTINCT tier FROM index_base)
    INSERT INTO index_base (tier, vertical, base_date, base_median, weight, base_hot_share)
    SELECT f.tier, f.vertical, f.as_of, f.median_price,
           CASE WHEN f.as_of = l.d AND f.tier NOT IN (SELECT tier FROM already)
                THEN f.gmv / sum(f.gmv) FILTER (WHERE f.as_of = l.d)
                                        OVER (PARTITION BY f.tier)
           END,
           f.hot_share
      FROM firsts f JOIN launch l USING (tier)
    ON CONFLICT (tier, vertical) DO NOTHING;
    -- Rows pinned before this column existed: fill from the base window itself.
    UPDATE index_base b
       SET base_hot_share = w.hot_sales::numeric / w.sales
      FROM v_index_windows w
     WHERE b.base_hot_share IS NULL
       AND w.tier = b.tier AND w.vertical = b.vertical AND w.as_of = b.base_date;
END;
$$ LANGUAGE plpgsql;

-- 7e. The index itself.
CREATE MATERIALIZED VIEW mv_market_index AS
WITH bounds AS (
    SELECT max(sold_date) AS last_date FROM sales WHERE is_publishable
),
settle AS (
    SELECT 'all'::text AS tier,
           (SELECT v::int FROM schema_meta WHERE k = 'index_settle_days_all') AS days
    UNION ALL
    SELECT 'bluechip',
           (SELECT v::int FROM schema_meta WHERE k = 'index_settle_days_bluechip')
),
-- THE COMPOSITION GUARD. The settle lag assumes the slower tier has landed by
-- the time a day is published. That assumption held until 17 Aug 2026, when
-- the scraper's tail collection silently dropped to ~15% of normal for two
-- weeks and every broad window became ~30% hot-tier instead of ~9%. Those
-- windows were "settled" by the calendar and wildly wrong in fact — the broad
-- composite read 188 with a -45% week. So a window is also required to LOOK
-- like the base period: its $hot_floor+ share may be at most 2.5x the base
-- share. Under-collection of the cheap tier fails this directly, whatever the
-- cadence config says. 'bluechip' windows are 100% hot by construction, so the
-- guard is a no-op there — which is why it stayed correct through the outage.
vert AS (
    SELECT w.tier, w.as_of, w.vertical, w.sales, w.gmv, w.median_price,
           b.base_date,
           100 * w.median_price / b.base_median AS index_value,
           (w.tier = 'bluechip'
            OR b.base_hot_share IS NULL
            OR w.hot_sales::numeric / w.sales <= 2.5 * b.base_hot_share) AS complete
      FROM v_index_windows w
      JOIN index_base b USING (tier, vertical)
     WHERE w.as_of >= b.base_date
),
-- Renormalised over whichever constituents produced a point that day, so one
-- vertical dipping below the n>=20 floor rescales the composite instead of
-- silently dropping its weight and stepping the line.
composite AS (
    SELECT v.tier,
           v.as_of,
           'All'::text   AS vertical,
           sum(v.sales)  AS sales,
           sum(v.gmv)    AS gmv,
           NULL::numeric AS median_price,
           min(v.base_date) AS base_date,
           sum(v.index_value * b.weight) / sum(b.weight) AS index_value,
           bool_and(v.complete) AS complete
      FROM vert v
      JOIN index_base b
        ON b.tier = v.tier AND b.vertical = v.vertical AND b.weight IS NOT NULL
     GROUP BY v.tier, v.as_of
),
allrows AS (
    SELECT * FROM vert
    UNION ALL
    SELECT * FROM composite
)
SELECT
    a.tier,
    a.vertical,
    a.as_of,
    a.base_date,
    a.sales,
    a.gmv,
    round(a.median_price, 2) AS median_price,
    round(a.index_value, 2)  AS index_value,
    -- Joined on date, never lag(). A thin vertical can have gaps, and lag(7)
    -- would then quietly compare against whatever row sat 7 ROWS back.
    round(100 * (a.index_value / NULLIF(p7.index_value, 0)  - 1), 1) AS pct_change_7d,
    round(100 * (a.index_value / NULLIF(p30.index_value, 0) - 1), 1) AS pct_change_30d,
    s.days                       AS settle_days,
    a.complete,
    (a.as_of <= b.last_date - s.days AND a.complete) AS settled
FROM allrows a
CROSS JOIN bounds b
JOIN settle s ON s.tier = a.tier
LEFT JOIN allrows p7  ON p7.tier  = a.tier AND p7.vertical  = a.vertical
                     AND p7.as_of  = a.as_of - 7
LEFT JOIN allrows p30 ON p30.tier = a.tier AND p30.vertical = a.vertical
                     AND p30.as_of = a.as_of - 30;
CREATE UNIQUE INDEX mv_market_index_pk ON mv_market_index (tier, vertical, as_of);
CREATE INDEX mv_market_index_asof ON mv_market_index (tier, as_of DESC);

-- ---------------------------------------------------------------- refresh
-- CONCURRENTLY keeps the site readable during a refresh; it requires the unique
-- indexes declared above. mv_site_stats has no unique key so it refreshes plain.
CREATE OR REPLACE FUNCTION refresh_all_views() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_daily_vertical;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_leaderboard_7d;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_card_comps;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_player_summary;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_vertical_wow;
    -- Pins the base period on the first refresh that has data; a no-op
    -- on every refresh after that. Must run BEFORE the index refresh.
    PERFORM pin_index_base();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_market_index;
    REFRESH MATERIALIZED VIEW mv_site_stats;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------- read role
-- The API connects as this role. It cannot write, so a compromised API key or
-- an injection bug cannot damage the warehouse.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'razmania_read') THEN
        CREATE ROLE razmania_read LOGIN PASSWORD 'CHANGE_ME';
    END IF;
END $$;
-- current_database(), not a literal. Render appends a suffix when a name is
-- already taken (this one landed as "razmania_93dv"), and a hardcoded name
-- aborts the whole file at the last statement — after the views are built,
-- so it looks like it worked until the read role can't connect.
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO razmania_read', current_database());
END $$;
GRANT USAGE ON SCHEMA public TO razmania_read;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO razmania_read;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO razmania_read;
