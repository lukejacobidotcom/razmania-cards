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
    -- The TIER-WIDE base hot share, pinned once. This is what the composition
    -- guard compares against. Per-vertical shares are kept above for
    -- diagnostics but are far too noisy to gate on: a category with one hot
    -- sale in its base window flips on the next one.
    INSERT INTO schema_meta (k, v)
    SELECT 'index_base_hot_share_' || w.tier,
           (sum(w.hot_sales)::numeric / sum(w.sales))::text
      FROM v_index_windows w
      JOIN (SELECT tier, min(base_date) AS d FROM index_base GROUP BY tier) l
        ON l.tier = w.tier AND l.d = w.as_of
     GROUP BY w.tier
    ON CONFLICT (k) DO NOTHING;
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
-- like the base period: the tier's $hot_floor+ share on that day may be at
-- most 2.5x the tier's base share. Under-collection of the cheap tier fails
-- this directly, whatever the cadence config says.
--
-- It is evaluated TIER-WIDE, once per day, and applied to every row of that
-- tier. The first version gated each category on its own base share and
-- flagged 30 of 31 real production days, including days the tail was full,
-- because small categories have base shares near zero and flip on a single
-- extra hot sale. Under-collection hits every category at once, so the
-- tier-wide share is both the right signal and a far less noisy one.
-- 'bluechip' windows are 100% hot by construction, so the guard is a no-op
-- there — which is why it stayed correct through the outage.
tierday AS (
    SELECT w.tier, w.as_of,
           sum(w.hot_sales)::numeric / sum(w.sales) AS hot_share
      FROM v_index_windows w
     GROUP BY w.tier, w.as_of
),
guard AS (
    SELECT t.tier, t.as_of,
           (t.tier = 'bluechip'
            OR m.v IS NULL
            OR t.hot_share <= 2.5 * m.v::numeric) AS complete
      FROM tierday t
      LEFT JOIN schema_meta m ON m.k = 'index_base_hot_share_' || t.tier
),
vert AS (
    SELECT w.tier, w.as_of, w.vertical, w.sales, w.gmv, w.median_price,
           b.base_date,
           100 * w.median_price / b.base_median AS index_value,
           g.complete
      FROM v_index_windows w
      JOIN index_base b USING (tier, vertical)
      JOIN guard g ON g.tier = w.tier AND g.as_of = w.as_of
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

-- ============================================================================
-- FIRSTS — the daily series at razmania.com/firsts/. Every day it publishes
-- one to three milestones the warehouse has just witnessed for the first
-- time: "First $250,000 Pokémon sale", "First 2026 football rookie card to
-- sell for $10,000", "First day with 100 Pokémon sales over $2,000",
-- "Blue Chip · Baseball index closes above 110 for the first time".
--
-- Two kinds of first, and the site never lets them blur:
--
--   HISTORICAL  "the first baseball card ever to sell for $10,000". Researched
--               by hand, sourced, and kept in firsts/firsts.json. Those sales
--               predate this warehouse by decades; nothing here can produce
--               them, and nothing here pretends to.
--   TRACKED     everything in firsts_log. Detected below from confirmed sales
--               since collection began. Labelled "tracked" / "since 28 Jul
--               2026" everywhere it appears. Never "ever" — even a 2026
--               product may have sold before collection started.
--
-- HOW IT STAYS HONEST
--   * Append-only, like index_base: (kind, vertical, subject, threshold,
--     floor) is unique and inserts are ON CONFLICT DO NOTHING. A first that
--     moves is not a first, and a first must outlive the sale that produced
--     it (db/retention.sql deletes sales after 400 days), so the sale is
--     copied into the row.
--   * Settled days only. A scrape can deliver a sale dated earlier than one
--     already loaded, so the newest days are not final. Sales at/above
--     hot_floor settle in index_settle_days_bluechip days; anything that
--     counts cheaper sales (day counts over $2,000, cumulative totals) waits
--     index_settle_days_all. Both come from schema_meta, nothing is
--     hardcoded, and when the tail is disabled (settle 36500) those kinds
--     simply never publish rather than publish on partial data.
--   * Under-collection cannot invent a first. Counts and volumes only ever
--     read LOW on a bad scrape day, so a starved tail delays a count first;
--     it never fabricates one.
--   * Confirmed sales only: is_publishable, never Unknown.
--   * Ties: two sales on the same day carry no time of day, so the larger
--     price wins, deterministically.
--
-- PUBLISHING. record_firsts() writes every first it can find (hundreds a
-- day at card level — that is the log, searchable at /v1/firsts/log).
-- publish_firsts() then picks the day's 1–3 by score, one per kind and one
-- per category, and stamps published_on. The score is an editorial weighting
-- in firsts_score() — the one place to tune what "newsworthy" means.
CREATE TABLE IF NOT EXISTS firsts_lines (
    metric TEXT    NOT NULL,     -- price | count | gmv | index | floor
    value  NUMERIC NOT NULL,
    PRIMARY KEY (metric, value)
);
INSERT INTO firsts_lines (metric, value) VALUES
    ('price', 2500), ('price', 5000), ('price', 7500), ('price', 10000), ('price', 15000),
    ('price', 20000), ('price', 25000), ('price', 30000), ('price', 40000), ('price', 50000),
    ('price', 75000), ('price', 100000), ('price', 150000), ('price', 200000), ('price', 250000),
    ('price', 300000), ('price', 400000), ('price', 500000), ('price', 750000), ('price', 1000000),
    ('price', 2500000), ('price', 5000000), ('price', 10000000),
    ('count', 10), ('count', 25), ('count', 50), ('count', 100), ('count', 250), ('count', 500),
    ('count', 1000), ('count', 2500), ('count', 5000), ('count', 10000),
    ('gmv', 100000), ('gmv', 250000), ('gmv', 500000), ('gmv', 1000000), ('gmv', 2500000),
    ('gmv', 5000000), ('gmv', 10000000), ('gmv', 25000000), ('gmv', 50000000), ('gmv', 100000000),
    ('index', 80), ('index', 85), ('index', 90), ('index', 95),
    ('index', 105), ('index', 110), ('index', 115), ('index', 120), ('index', 130), ('index', 150),
    -- count floors: "N sales over $floor". 0 means the publish floor itself.
    ('floor', 0), ('floor', 5000), ('floor', 10000), ('floor', 25000)
ON CONFLICT DO NOTHING;
INSERT INTO schema_meta (k, v) VALUES ('firsts_pick_floor', '4.5') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS firsts_log (
    id           BIGSERIAL PRIMARY KEY,
    kind         TEXT        NOT NULL,   -- price:vertical|subject|card|card_grade|set|year|year_rookie,
                                         -- count:day|week|total, gmv:day|week|total, index:above|below
    vertical     TEXT        NOT NULL,   -- 'All' for market-wide
    subject      TEXT        NOT NULL,   -- human label: a category, player, Pokémon, card, set, year
    threshold    NUMERIC     NOT NULL,   -- the line crossed (price, count, dollars, index level)
    floor        NUMERIC     NOT NULL DEFAULT 0,  -- count kinds: sales counted at/above this price
    first_date   DATE        NOT NULL,
    value        NUMERIC     NOT NULL,   -- what was actually observed
    item_id      TEXT,                   -- the sale, when a single sale did it
    title        TEXT,
    url          TEXT,
    image_url    TEXT,
    detail       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    headline     TEXT        NOT NULL,
    score        NUMERIC     NOT NULL,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_on DATE,
    publish_rank SMALLINT,
    UNIQUE (kind, vertical, subject, threshold, floor)
);
CREATE INDEX IF NOT EXISTS firsts_log_published ON firsts_log (published_on DESC, publish_rank)
    WHERE published_on IS NOT NULL;
CREATE INDEX IF NOT EXISTS firsts_log_first_date ON firsts_log (first_date DESC);
CREATE INDEX IF NOT EXISTS firsts_log_vertical ON firsts_log (vertical, kind);
CREATE INDEX IF NOT EXISTS firsts_log_recorded ON firsts_log (recorded_at);

-- ------------------------------------------------------------- helpers
CREATE OR REPLACE FUNCTION firsts_money(x NUMERIC) RETURNS TEXT AS $$
    SELECT '$' || to_char(round(x), 'FM999,999,999,999')
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION firsts_vlabel(v TEXT) RETURNS TEXT AS $$
    SELECT CASE v WHEN 'Pokemon' THEN 'Pokémon' WHEN 'All' THEN 'the hobby' ELSE v END
$$ LANGUAGE sql IMMUTABLE;

-- The subject a sale is about. Sports rows carry the parsed player. Pokémon
-- rows carry no player, so the character is read off the title with the same
-- vocabulary etl/classify.py uses — a Charizard first must be findable.
CREATE OR REPLACE FUNCTION firsts_subject(p_vertical TEXT, p_player TEXT, p_title TEXT)
RETURNS TEXT AS $$
    SELECT CASE
        WHEN p_player IS NOT NULL THEN p_player
        WHEN p_vertical = 'Pokemon' THEN (
            SELECT initcap(m[1]) FROM regexp_matches(lower(p_title),
              '\m(charizard|pikachu|umbreon|mewtwo|rayquaza|lugia|snorlax|articuno|zapdos|moltres|'
              'gengar|blastoise|venusaur|sylveon|espeon|vaporeon|jolteon|flareon|eevee|giratina|'
              'arceus|greninja|gardevoir|magikarp|dragonite|dragonair|psyduck|celebi|ho-oh|entei|'
              'raikou|suicune|gyarados|machamp|alakazam|ninetales|jigglypuff|mudkip|lucario|'
              'squirtle|bulbasaur|typhlosion|feraligatr|mew)\M') AS m
            LIMIT 1)
        ELSE NULL
    END
$$ LANGUAGE sql IMMUTABLE;

-- The Pokémon title parser often finds no year and no set ("CGC 10 PRISTINE
-- Charizard 146/144 Skyridge Holo"), and a first with no card is a first for
-- every Charizard, which is not what happened. Read the set off the title.
CREATE OR REPLACE FUNCTION firsts_pokemon_set(p_title TEXT) RETURNS TEXT AS $$
    SELECT CASE WHEN m IS NULL THEN NULL
                WHEN lower(m) = 'corocoro' THEN 'CoroCoro'
                ELSE initcap(m) END
    FROM (SELECT (regexp_matches(p_title,
          '\m(skyridge|aquapolis|expedition|neo genesis|neo destiny|neo discovery|neo revelation|'
          'base set 2|base set|jungle|fossil|team rocket returns|team rocket|gym heroes|gym challenge|'
          'legendary collection|southern islands|shining legends|evolving skies|crown zenith|'
          'hidden fates|shining fates|prismatic evolutions|celebrations|obsidian flames|paldean fates|'
          'gold star|ex deoxys|ex dragon frontiers|ex fire red|ex team magma|delta species|'
          'corocoro|illustrator|topsun|vending|trophy|no rarity|shadowless|1st edition|'
          'silver tempest|lost origin|brilliant stars|fusion strike|chilling reign|battle styles|'
          'vivid voltage|champion.s path|darkness ablaze|rebel clash|sword . shield|cosmic eclipse|'
          'unified minds|unbroken bonds|team up|lost thunder|dragon majesty|forbidden light|'
          'ultra prism|crimson invasion|burning shadows|guardians rising|sun . moon|'
          'evolutions|steam siege|fates collide|generations|breakpoint|breakthrough|ancient origins|'
          'roaring skies|primal clash|phantom forces|furious fists|flashfire|xy|plasma|boundaries crossed|'
          'dragons exalted|dark explorers|next destinies|noble victories|emerging powers|black . white|'
          'call of legends|triumphant|undaunted|unleashed|heartgold|platinum|stormfront|legends awakened|'
          'majestic dawn|great encounters|secret wonders|mysterious treasures|diamond . pearl|'
          'power keepers|dragon frontiers|crystal guardians|holon phantoms|legend maker|unseen forces|'
          'emerald|team rocket|ruby . sapphire|sandstorm|dragon|magma . aqua|hidden legends|'
          '151|twilight masquerade|temporal forces|paradox rift|surging sparks|stellar crown|'
          'shrouded fable|journey together|destined rivals|black bolt|white flare)\M', 'i'))[1] AS m) x
$$ LANGUAGE sql IMMUTABLE;

-- A card, as the title parser saw it: "2003 Topps Chrome LeBron James #111
-- Refractor". NULL unless there is a subject AND a year or a set — a bare
-- player name is not a card.
CREATE OR REPLACE FUNCTION firsts_card_label(p_subject TEXT, p_year SMALLINT, p_set TEXT,
                                             p_number TEXT, p_parallel TEXT)
RETURNS TEXT AS $$
    SELECT CASE WHEN p_subject IS NULL OR (p_year IS NULL AND p_set IS NULL) THEN NULL
           ELSE concat_ws(' ', p_year::text, p_set, p_subject,
                          CASE WHEN p_number IS NOT NULL THEN '#' || p_number END, p_parallel)
           END
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION firsts_headline(p_kind TEXT, p_vertical TEXT, p_subject TEXT,
                                           p_threshold NUMERIC, p_floor NUMERIC, p_detail JSONB)
RETURNS TEXT AS $$
DECLARE
    v TEXT := firsts_vlabel(p_vertical);
    who TEXT := CASE WHEN p_subject = p_vertical THEN v ELSE p_subject END;   -- category or named subject
    n TEXT := to_char(p_threshold, 'FM999,999,999');
BEGIN
    RETURN CASE p_kind
        WHEN 'price:vertical'    THEN CASE WHEN p_vertical = 'All' THEN format('First %s card sale', firsts_money(p_threshold))
                                           ELSE format('First %s %s sale', firsts_money(p_threshold), v) END
        -- Names the card that did it, not the player or character as a whole:
        -- "First $100,000 Charizard sale" reads as a claim about every
        -- Charizard ever; "First $100,000 sale of a 1996 Charizard Holo" is
        -- the fact. The article's deck carries the wider, scoped claim.
        WHEN 'price:subject'     THEN CASE WHEN p_detail->>'card' IS NOT NULL
                                           THEN format('First %s sale of a %s', firsts_money(p_threshold), p_detail->>'card')
                                           WHEN p_detail->>'grade' IS NOT NULL
                                           THEN format('First %s %s sale in RazMania tracking: a %s', firsts_money(p_threshold), p_subject, p_detail->>'grade')
                                           ELSE format('First %s %s sale in RazMania tracking', firsts_money(p_threshold), p_subject) END
        WHEN 'price:card'        THEN format('First %s sale of a %s', firsts_money(p_threshold), p_subject)
        WHEN 'price:card_grade'  THEN format('First %s sale of a %s', firsts_money(p_threshold), p_subject)
        WHEN 'price:set'         THEN format('First %s sale out of %s', firsts_money(p_threshold), p_subject)
        WHEN 'price:year'        THEN format('First %s %s card to sell for %s', p_subject, v, firsts_money(p_threshold))
        WHEN 'price:year_rookie' THEN format('First %s %s rookie card to sell for %s', p_subject, v, firsts_money(p_threshold))
        WHEN 'count:day'         THEN CASE WHEN p_vertical = 'All'
                                           THEN format('First day with %s card sales over %s', n, firsts_money(p_floor))
                                           ELSE format('First day with %s %s sales over %s', n, who, firsts_money(p_floor)) END
        WHEN 'count:week'        THEN CASE WHEN p_vertical = 'All'
                                           THEN format('First week with %s card sales over %s', n, firsts_money(p_floor))
                                           ELSE format('First week with %s %s sales over %s', n, who, firsts_money(p_floor)) END
        WHEN 'gmv:day'           THEN format('First %s day for %s', firsts_money(p_threshold), who)
        WHEN 'gmv:week'          THEN format('First %s week for %s', firsts_money(p_threshold), who)
        WHEN 'count:total'       THEN CASE WHEN p_vertical = 'All'
                                           THEN format('The %sth tracked card sale over %s', n, firsts_money(p_floor))
                                           ELSE format('The %sth tracked %s sale over %s', n, who, firsts_money(p_floor)) END
        WHEN 'gmv:total'         THEN format('%s passes %s in tracked sales', initcap(left(who, 1)) || substr(who, 2), firsts_money(p_threshold))
        WHEN 'index:above'       THEN format('%s index closes above %s for the first time', p_subject, n)
        WHEN 'index:below'       THEN format('%s index closes below %s for the first time', p_subject, n)
        ELSE format('%s: %s %s', p_kind, p_subject, n)
    END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Editorial weight. Roughly: one point per order of magnitude, plus what
-- kind of thing it is, plus how big a name it happened to. Picks 2 and 3
-- of a day need firsts_pick_floor (4.5); pick 1 is always made.
CREATE OR REPLACE FUNCTION firsts_score(p_kind TEXT, p_vertical TEXT, p_subject TEXT,
                                        p_threshold NUMERIC, p_floor NUMERIC,
                                        p_year INT, p_prom INT)
RETURNS NUMERIC AS $$
DECLARE
    s NUMERIC := 0;
    recent BOOLEAN := p_year IS NOT NULL AND p_year >= extract(year FROM current_date)::int - 1;
    prom NUMERIC := CASE WHEN p_prom IS NULL THEN 0 WHEN p_prom <= 10 THEN 1.5 WHEN p_prom <= 40 THEN 0.75 ELSE 0 END;
    fl NUMERIC := 0.5 * log(greatest(p_floor, 1000) / 1000.0);   -- $2k -> .15, $10k -> .5, $25k -> .7
BEGIN
    s := CASE split_part(p_kind, ':', 1)
        WHEN 'price' THEN log(p_threshold)
                          + CASE p_kind
                              WHEN 'price:vertical'    THEN CASE WHEN p_vertical = 'All' THEN 3.0 ELSE 2.5 END
                              WHEN 'price:subject'     THEN 1.0 + prom
                              WHEN 'price:card'        THEN 0.5 + prom
                              WHEN 'price:card_grade'  THEN 0.0 + prom
                              WHEN 'price:set'         THEN CASE WHEN recent THEN 1.5 ELSE 0.25 END
                              WHEN 'price:year'        THEN CASE WHEN recent THEN 1.5 ELSE -1.0 END
                              WHEN 'price:year_rookie' THEN CASE WHEN recent THEN 2.0 ELSE -0.5 END
                              ELSE 0 END
        WHEN 'count' THEN CASE WHEN p_kind = 'count:total' THEN 1.5 + log(p_threshold)
                               ELSE 2.0 + log(p_threshold) + fl END
                          + CASE WHEN p_vertical = 'All' THEN 1.5 WHEN p_subject = p_vertical THEN 1.0 ELSE 0.5 + prom END
                          - CASE WHEN p_kind = 'count:week' THEN 0.5 ELSE 0 END
        WHEN 'gmv'   THEN CASE WHEN p_kind = 'gmv:total' THEN log(p_threshold) - 2.0 ELSE log(p_threshold) - 1.5 END
                          + CASE WHEN p_vertical = 'All' THEN 1.5 WHEN p_subject = p_vertical THEN 1.0 ELSE 0.5 + prom END
                          - CASE WHEN p_kind = 'gmv:week' THEN 0.5 ELSE 0 END
        WHEN 'index' THEN 3.5 + abs(p_threshold - 100) / 20.0 + CASE WHEN p_vertical = 'All' THEN 1.0 ELSE 0 END
        ELSE 0 END;
    RETURN round(s, 2);
END;
$$ LANGUAGE plpgsql STABLE;

-- --------------------------------------------------------------- record
CREATE OR REPLACE FUNCTION record_firsts() RETURNS integer AS $$
DECLARE
    last_date  DATE;
    first_day  DATE;
    hot        NUMERIC := coalesce((SELECT v::numeric FROM schema_meta WHERE k = 'hot_floor'), 10000);
    pfloor     NUMERIC := coalesce((SELECT v::numeric FROM schema_meta WHERE k = 'publish_floor'), 2000);
    settle_bc  INT := coalesce((SELECT v::int FROM schema_meta WHERE k = 'index_settle_days_bluechip'), 2);
    settle_all INT := coalesce((SELECT v::int FROM schema_meta WHERE k = 'index_settle_days_all'), 4);
    upto_bc    DATE;
    upto_all   DATE;
    n_before   BIGINT;
    n_after    BIGINT;
BEGIN
    SELECT max(sold_date), min(sold_date) INTO last_date, first_day FROM sales WHERE is_publishable;
    IF last_date IS NULL THEN RETURN 0; END IF;
    upto_bc  := last_date - settle_bc;
    upto_all := last_date - settle_all;
    SELECT count(*) INTO n_before FROM firsts_log;
    -- ON COMMIT DROP is not enough when the function runs twice in one
    -- transaction (tests, a manual re-run inside a refresh).
    DROP TABLE IF EXISTS _fc, _prom, _days, _weeks;

    -- Settled, confirmed, classified sales with their subject and card label.
    CREATE TEMP TABLE _fc ON COMMIT DROP AS
    SELECT s.item_id, s.title, s.vertical, s.total_price, s.sold_date, s.url, s.image_url,
           s.grade_label, s.listing_format, s.card_year, s.brand_set, s.card_number, s.parallel,
           s.is_rookie,
           firsts_subject(s.vertical, s.player, s.title) AS subj,
           (s.total_price >= hot AND s.sold_date <= upto_bc) OR s.sold_date <= upto_all AS price_ok,
           s.sold_date <= upto_all AS tail_ok
    FROM sales s
    WHERE s.is_publishable AND s.vertical <> 'Unknown' AND s.sold_date <= upto_bc;
    ALTER TABLE _fc ADD COLUMN card TEXT;
    UPDATE _fc SET card = firsts_card_label(subj, card_year,
                        coalesce(brand_set, CASE WHEN vertical = 'Pokemon' THEN firsts_pokemon_set(title) END),
                        card_number, parallel);

    -- Who is a big name: rank of every subject by tracked volume.
    CREATE TEMP TABLE _prom ON COMMIT DROP AS
    SELECT subj, dense_rank() OVER (ORDER BY sum(total_price) DESC)::int AS rk,
           mode() WITHIN GROUP (ORDER BY vertical) AS home
    FROM _fc WHERE subj IS NOT NULL GROUP BY subj;
    -- A subject belongs to one category. A stray sale filed under another
    -- (a classifier miss) must not hand it a first there.
    UPDATE _fc c SET subj = NULL FROM _prom p WHERE p.subj = c.subj AND p.home <> c.vertical;
    UPDATE _fc SET card = NULL WHERE subj IS NULL;

    -- 1. PRICE firsts: the first sale at/above each line, per category,
    --    subject, card, card+grade, set, release year, release-year rookie.
    WITH keyed AS (
        SELECT 'price:vertical' AS kind, c.vertical, c.vertical AS subject, NULL::int AS yr, c.item_id, c.title, c.total_price, c.sold_date, c.url, c.image_url, c.grade_label, c.listing_format, c.subj, c.card
          FROM _fc c WHERE price_ok
        UNION ALL
        SELECT 'price:vertical', 'All', 'All', NULL::int, c.item_id, c.title, c.total_price, c.sold_date, c.url, c.image_url, c.grade_label, c.listing_format, c.subj, c.card
          FROM _fc c WHERE price_ok
        UNION ALL
        SELECT 'price:subject', c.vertical, c.subj, NULL, c.item_id, c.title, c.total_price, c.sold_date, c.url, c.image_url, c.grade_label, c.listing_format, c.subj, c.card
          FROM _fc c WHERE price_ok AND subj IS NOT NULL
        UNION ALL
        SELECT 'price:card', c.vertical, c.card, c.card_year::int, c.item_id, c.title, c.total_price, c.sold_date, c.url, c.image_url, c.grade_label, c.listing_format, c.subj, c.card
          FROM _fc c WHERE price_ok AND card IS NOT NULL
        UNION ALL
        SELECT 'price:card_grade', c.vertical, c.card || ' · ' || coalesce(c.grade_label, 'Raw'), c.card_year::int, c.item_id, c.title, c.total_price, c.sold_date, c.url, c.image_url, c.grade_label, c.listing_format, c.subj, c.card
          FROM _fc c WHERE price_ok AND card IS NOT NULL
        UNION ALL
        SELECT 'price:set', c.vertical, c.card_year::text || ' ' || c.brand_set, c.card_year::int, c.item_id, c.title, c.total_price, c.sold_date, c.url, c.image_url, c.grade_label, c.listing_format, c.subj, c.card
          FROM _fc c WHERE price_ok AND brand_set IS NOT NULL AND card_year IS NOT NULL
        UNION ALL
        SELECT 'price:year', c.vertical, c.card_year::text, c.card_year::int, c.item_id, c.title, c.total_price, c.sold_date, c.url, c.image_url, c.grade_label, c.listing_format, c.subj, c.card
          FROM _fc c WHERE price_ok AND card_year IS NOT NULL
        UNION ALL
        SELECT 'price:year_rookie', c.vertical, c.card_year::text, c.card_year::int, c.item_id, c.title, c.total_price, c.sold_date, c.url, c.image_url, c.grade_label, c.listing_format, c.subj, c.card
          FROM _fc c WHERE price_ok AND card_year IS NOT NULL AND is_rookie
    ),
    fr AS (
        SELECT DISTINCT ON (k.kind, k.vertical, k.subject, l.value)
               k.kind, k.vertical, k.subject, l.value AS threshold, k.yr,
               k.item_id, k.title, k.total_price, k.sold_date, k.url, k.image_url,
               k.grade_label, k.listing_format, k.subj, k.card
        FROM keyed k JOIN firsts_lines l ON l.metric = 'price' AND k.total_price >= l.value
        ORDER BY k.kind, k.vertical, k.subject, l.value, k.sold_date, k.total_price DESC, k.item_id
    )
    INSERT INTO firsts_log (kind, vertical, subject, threshold, floor, first_date, value,
                            item_id, title, url, image_url, detail, headline, score)
    SELECT f.kind, f.vertical, f.subject, f.threshold, 0, f.sold_date, f.total_price,
           f.item_id, f.title, f.url, f.image_url,
           jsonb_strip_nulls(jsonb_build_object('grade', f.grade_label, 'format', f.listing_format,
                                                'year', f.yr, 'player', f.subj, 'card', f.card, 'prom', p.rk)),
           firsts_headline(f.kind, f.vertical, f.subject, f.threshold, 0, jsonb_build_object('card', f.card, 'grade', f.grade_label)),
           firsts_score(f.kind, f.vertical, f.subject, f.threshold, 0, f.yr, p.rk)
    FROM fr f LEFT JOIN _prom p ON p.subj = f.subj
    ON CONFLICT DO NOTHING;

    -- 2. DAY and WEEK firsts: count and volume per calendar day, per category
    --    and market-wide (and per subject at the publish floor). A floor below
    --    hot_floor needs the tail settled; at/above it, the hot tier suffices.
    CREATE TEMP TABLE _days ON COMMIT DROP AS
    WITH fl AS (SELECT CASE WHEN value = 0 THEN pfloor ELSE value END AS floor FROM firsts_lines WHERE metric = 'floor'),
    base AS (
        SELECT c.vertical, c.vertical AS subject, c.sold_date, fl.floor, c.total_price
        FROM _fc c JOIN fl ON c.total_price >= fl.floor
        WHERE (fl.floor >= hot) OR c.tail_ok
        UNION ALL
        SELECT 'All', 'All', c.sold_date, fl.floor, c.total_price
        FROM _fc c JOIN fl ON c.total_price >= fl.floor
        WHERE (fl.floor >= hot) OR c.tail_ok
        UNION ALL
        SELECT c.vertical, c.subj, c.sold_date, pfloor, c.total_price
        FROM _fc c WHERE c.subj IS NOT NULL AND c.tail_ok
    )
    SELECT vertical, subject, floor, sold_date, count(*)::numeric AS n, sum(total_price) AS gmv
    FROM base GROUP BY vertical, subject, floor, sold_date;

    CREATE TEMP TABLE _weeks ON COMMIT DROP AS
    SELECT vertical, subject, floor, sold_date,
           sum(n)   OVER w AS n,
           sum(gmv) OVER w AS gmv
    FROM _days
    WINDOW w AS (PARTITION BY vertical, subject, floor ORDER BY sold_date
                 RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW);
    DELETE FROM _weeks WHERE sold_date < first_day + 6;   -- no full week of data yet

    INSERT INTO firsts_log (kind, vertical, subject, threshold, floor, first_date, value, detail, headline, score)
    SELECT x.kind, x.vertical, x.subject, x.threshold, x.floor, x.sold_date, x.value,
           jsonb_strip_nulls(jsonb_build_object('prom', p.rk)),
           firsts_headline(x.kind, x.vertical, x.subject, x.threshold, x.floor, '{}'::jsonb),
           firsts_score(x.kind, x.vertical, x.subject, x.threshold, x.floor, NULL, p.rk)
    FROM (
        (SELECT DISTINCT ON (d.vertical, d.subject, d.floor, l.value)
               'count:day' AS kind, d.vertical, d.subject, l.value AS threshold, d.floor, d.sold_date, d.n AS value
        FROM _days d JOIN firsts_lines l ON l.metric = 'count' AND d.n >= l.value
        ORDER BY d.vertical, d.subject, d.floor, l.value, d.sold_date)
        UNION ALL
        (SELECT DISTINCT ON (d.vertical, d.subject, l.value)
               'gmv:day', d.vertical, d.subject, l.value, 0, d.sold_date, d.gmv
        FROM _days d JOIN firsts_lines l ON l.metric = 'gmv' AND d.gmv >= l.value
        WHERE d.floor = pfloor
        ORDER BY d.vertical, d.subject, l.value, d.sold_date)
        UNION ALL
        (SELECT DISTINCT ON (w.vertical, w.subject, w.floor, l.value)
               'count:week', w.vertical, w.subject, l.value, w.floor, w.sold_date, w.n
        FROM _weeks w JOIN firsts_lines l ON l.metric = 'count' AND w.n >= l.value
        ORDER BY w.vertical, w.subject, w.floor, l.value, w.sold_date)
        UNION ALL
        (SELECT DISTINCT ON (w.vertical, w.subject, l.value)
               'gmv:week', w.vertical, w.subject, l.value, 0, w.sold_date, w.gmv
        FROM _weeks w JOIN firsts_lines l ON l.metric = 'gmv' AND w.gmv >= l.value
        WHERE w.floor = pfloor
        ORDER BY w.vertical, w.subject, l.value, w.sold_date)
    ) x
    LEFT JOIN _prom p ON p.subj = x.subject AND x.subject <> x.vertical
    ON CONFLICT DO NOTHING;

    -- 3. CUMULATIVE firsts: the Nth tracked sale, and volume passing $Y, per
    --    category, market-wide and per subject. The Nth sale is the row.
    WITH run AS (
        SELECT vertical, subject, item_id, title, url, image_url, sold_date, total_price,
               count(*)          OVER w AS k,
               sum(total_price)  OVER w AS cum
        FROM (
            SELECT vertical, vertical AS subject, item_id, title, url, image_url, sold_date, total_price FROM _fc WHERE tail_ok
            UNION ALL
            SELECT 'All', 'All', item_id, title, url, image_url, sold_date, total_price FROM _fc WHERE tail_ok
            UNION ALL
            SELECT vertical, subj, item_id, title, url, image_url, sold_date, total_price FROM _fc WHERE tail_ok AND subj IS NOT NULL
        ) u
        WINDOW w AS (PARTITION BY vertical, subject ORDER BY sold_date, total_price DESC, item_id
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
    )
    INSERT INTO firsts_log (kind, vertical, subject, threshold, floor, first_date, value,
                            item_id, title, url, image_url, detail, headline, score)
    SELECT x.kind, x.vertical, x.subject, x.threshold, x.floor, x.sold_date, x.value,
           x.item_id, x.title, x.url, x.image_url,
           jsonb_strip_nulls(jsonb_build_object('prom', p.rk)),
           firsts_headline(x.kind, x.vertical, x.subject, x.threshold, x.floor, '{}'::jsonb),
           firsts_score(x.kind, x.vertical, x.subject, x.threshold, x.floor, NULL, p.rk)
    FROM (
        (SELECT DISTINCT ON (r.vertical, r.subject, l.value)
               'count:total' AS kind, r.vertical, r.subject, l.value AS threshold, pfloor AS floor,
               r.sold_date, r.k::numeric AS value, r.item_id, r.title, r.url, r.image_url
        FROM run r JOIN firsts_lines l ON l.metric = 'count' AND r.k >= l.value
        ORDER BY r.vertical, r.subject, l.value, r.sold_date, r.k)
        UNION ALL
        (SELECT DISTINCT ON (r.vertical, r.subject, l.value)
               'gmv:total', r.vertical, r.subject, l.value, 0,
               r.sold_date, r.cum, r.item_id, r.title, r.url, r.image_url
        FROM run r JOIN firsts_lines l ON l.metric = 'gmv' AND r.cum >= l.value
        ORDER BY r.vertical, r.subject, l.value, r.sold_date, r.cum)
    ) x
    LEFT JOIN _prom p ON p.subj = x.subject AND x.subject <> x.vertical
    ON CONFLICT DO NOTHING;

    -- 4. INDEX firsts: first settled close above/below a level, per tier and
    --    category. Settled rows only — an unsettled index point is withheld
    --    from the site for exactly the reason a first must not move.
    IF to_regclass('public.mv_market_index') IS NOT NULL THEN
        INSERT INTO firsts_log (kind, vertical, subject, threshold, floor, first_date, value, headline, score)
        SELECT x.kind, x.vertical, x.subject, x.threshold, 0, x.as_of, x.index_value,
               firsts_headline(x.kind, x.vertical, x.subject, x.threshold, 0, '{}'::jsonb),
               firsts_score(x.kind, x.vertical, x.subject, x.threshold, 0, NULL, NULL)
        FROM (
            SELECT DISTINCT ON (m.tier, m.vertical, l.value)
                   CASE WHEN l.value > 100 THEN 'index:above' ELSE 'index:below' END AS kind,
                   m.vertical,
                   (CASE m.tier WHEN 'bluechip' THEN 'Blue Chip' ELSE 'Broad' END) || ' · ' || firsts_vlabel(m.vertical) AS subject,
                   l.value AS threshold, m.as_of, m.index_value
            FROM mv_market_index m
            JOIN firsts_lines l ON l.metric = 'index'
                 AND ((l.value > 100 AND m.index_value >= l.value) OR (l.value < 100 AND m.index_value <= l.value))
            WHERE m.settled
            ORDER BY m.tier, m.vertical, l.value, m.as_of
        ) x
        ON CONFLICT DO NOTHING;
    END IF;

    SELECT count(*) INTO n_after FROM firsts_log;
    RETURN (n_after - n_before)::int;
END;
$$ LANGUAGE plpgsql;

-- -------------------------------------------------------------- publish
-- The day's 1–3. Candidates are unpublished firsts dated within the last
-- week (what just settled); only if there are none does it reach back into
-- the backlog, so there is always at least one. Three rules keep the feed
-- from repeating itself:
--   * one sale, one story: of the lines a single sale crossed at once, only
--     the highest is publishable, and a sale that has already been published
--     under one kind is never published again under another;
--   * a day's picks differ in kind, in category and in subject, and a named
--     subject (player, Pokémon, card) headlines at most once in five days;
--   * picks 2 and 3 must clear firsts_pick_floor. Pick 1 is always made.
--   * ranking is score less a fifth of a point per day of age, so a strong
--     first from five days ago yields to a slightly weaker one from yesterday.
-- Idempotent per day: a re-run of the refresh publishes nothing extra.
CREATE OR REPLACE FUNCTION publish_firsts(p_day DATE DEFAULT current_date) RETURNS integer AS $$
DECLARE
    pick_floor NUMERIC := coalesce((SELECT v::numeric FROM schema_meta WHERE k = 'firsts_pick_floor'), 4.5);
    r RECORD;
    picked INT := 0;
    kinds TEXT[] := '{}';
    verts TEXT[] := '{}';
    subjects TEXT[] := '{}';
    items TEXT[] := '{}';
    recent BOOLEAN;
BEGIN
    IF EXISTS (SELECT 1 FROM firsts_log WHERE published_on = p_day) THEN RETURN 0; END IF;
    recent := EXISTS (SELECT 1 FROM firsts_log WHERE published_on IS NULL AND first_date > p_day - 8 AND first_date <= p_day);
    FOR r IN
        SELECT f.id, f.kind, f.vertical, f.subject, f.item_id, f.score
        FROM firsts_log f
        WHERE f.published_on IS NULL
          AND f.first_date <= p_day
          AND (NOT recent OR f.first_date > p_day - 8)
          -- a subject headlines at most once in five days, under any kind
          AND NOT EXISTS (SELECT 1 FROM firsts_log g
                          WHERE g.published_on > p_day - 5
                            AND coalesce(g.detail->>'player', g.subject) = coalesce(f.detail->>'player', f.subject)
                            AND g.subject <> g.vertical)
          AND NOT EXISTS (SELECT 1 FROM firsts_log g
                          WHERE g.kind = f.kind AND g.vertical = f.vertical AND g.subject = f.subject
                            AND g.floor = f.floor AND g.first_date = f.first_date AND g.threshold > f.threshold)
          AND NOT EXISTS (SELECT 1 FROM firsts_log g
                          WHERE f.item_id IS NOT NULL AND g.item_id = f.item_id AND g.published_on IS NOT NULL)
        ORDER BY f.score - 0.2 * (p_day - f.first_date) DESC, f.first_date DESC, f.id
        LIMIT 500
    LOOP
        EXIT WHEN picked >= 3;
        IF picked > 0 AND (r.score < pick_floor OR r.kind = ANY(kinds) OR r.vertical = ANY(verts)
                           OR r.subject = ANY(subjects) OR (r.item_id IS NOT NULL AND r.item_id = ANY(items))) THEN
            CONTINUE;
        END IF;
        UPDATE firsts_log SET published_on = p_day, publish_rank = picked + 1 WHERE id = r.id;
        picked := picked + 1;
        kinds := kinds || r.kind;
        verts := verts || r.vertical;
        subjects := subjects || r.subject;
        IF r.item_id IS NOT NULL THEN items := items || r.item_id; END IF;
    END LOOP;
    RETURN picked;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- CARD MOVERS — razmania.com/movers/. The cards whose price moved most, the
-- busiest cards, and a chart of every confirmed sale for any card a reader
-- looks up.
--
-- A MOVE IS A CARD AGAINST ITSELF. Never a player, never a category. A
-- player's median moves when a PSA 10 week follows a raw week without one
-- card changing price (the index handles that with fixed weights; a per-card
-- list has no such luxury). So the unit is one card in one grade:
--
--     category | player or Pokémon | year | set | number | parallel | grade
--
-- and a sale only joins a card when the title parser found a number AND a
-- year or set. "2025 Topps Cooper Flagg, Raw" (39 sales, no number) is a
-- dozen different cards under one name; it would top every list on mix alone.
-- Pokémon rows carry no player and a brand_set that is mostly fragments
-- ("Star" from "Black Star Promo"), so the character and the set are read off
-- the title with the FIRSTS helpers above. Leading zeros and case are
-- normalised ("021" = "21", "sm168" = "SM168").
--
-- Two more things split what the parser lumps. A grade qualifier ("PSA 9
-- (OC)", off-centre) sells at a fraction of the clean grade, so it becomes part
-- of the grade: a 1986 Fleer Jordan PSA 9 read -68% on four OC and crossover
-- sales before this. And the classifier's player list files one man under two
-- short names ("Shohei" and "Ohtani"), which put the same card on the losers
-- list twice; card_subject_of() maps those to one full name.
--
-- WHY THE GATES. The same card in the same grade sells in a tight band: the
-- robust log-spread within a card is 0.086 at the median (0.14 at p75),
-- measured over the 182 cards with 6+ confirmed sales on 16 Sep 2026. A card
-- whose own sales disagree by far more than that is almost always two cards
-- under one key (Japanese Neo 2 promo and Topps Chrome TV, both "2000
-- Charizard #6"; three different 1998 Japanese Charizards #6). On that day
-- every mover with a before-window spread above 0.18 was a lumped key or a
-- raw card of unknown condition, and every one at or below it was one card,
-- sale after sale. So a card is a mover only if ALL of these hold:
--
--   * it is graded. "Raw" is every condition from mint to creased at one
--     label, so its median moves with whichever copies happened to sell;
--   * at least movers_min_recent sales in the last movers_recent_days SETTLED
--     days, and movers_min_base in the movers_base_days before that;
--   * the before-window agrees with itself: robust log-spread (1.4826 x MAD)
--     <= movers_max_spread. Above it the key is probably several cards;
--   * the recent sales agree about the direction: at least movers_agree of
--     them sit on the move's side of the before median. One lucky auction
--     next to a normal one is not a move;
--   * the before median clears publish_floor x movers_floor_margin. Sales
--     under the floor are never collected, so a card trading near it loses
--     its cheap sales and its median reads high: an invented gain;
--   * the move is at least movers_min_change percent.
--
-- On 16 Sep 2026 that left 28 movers (6 up, 22 down, 17 of them Pokémon) out
-- of 109 graded cards with enough sales in both windows, and every one read as
-- the same card sale after sale when checked by title. Most of the market does
-- not move 10% in two weeks, which is the honest reason the lists are short.
--
-- SETTLED DAYS ONLY. The $2,000-9,999 tail is scraped every few days, so an
-- unsettled day holds only the $10,000+ sales of a card that straddles that
-- line: a fake jump. Both windows end at last_date - index_settle_days_all,
-- the same lag the broad index publishes on. With the tail disabled that lag
-- is 36500 days and nothing qualifies, by design. The chart itself plots every
-- confirmed sale, settled or not: a sale is a fact, a move is a claim.
--
-- Tunables live in schema_meta and are only seeded here (ON CONFLICT DO
-- NOTHING); change one with an UPDATE and it applies on the next refresh.
-- ============================================================================
INSERT INTO schema_meta (k, v) VALUES
    ('movers_recent_days', '14'),
    ('movers_base_days',   '30'),
    ('movers_min_recent',  '2'),
    ('movers_min_base',    '3'),
    ('movers_max_spread',  '0.18'),
    ('movers_agree',       '0.75'),
    ('movers_floor_margin', '1.25'),
    ('movers_min_change',  '10')
ON CONFLICT (k) DO NOTHING;

-- The set a card identity uses. Sports rows keep the parsed brand_set; Pokémon
-- reads it off the title, because brand_set there is noise that would split
-- one card into several keys.
CREATE OR REPLACE FUNCTION card_set_of(p_vertical TEXT, p_brand_set TEXT, p_title TEXT)
RETURNS TEXT AS $$
    SELECT CASE WHEN p_vertical = 'Pokemon' THEN firsts_pokemon_set(p_title) ELSE p_brand_set END
$$ LANGUAGE sql IMMUTABLE;

-- One name per person. etl/classify.py matches short tokens ("lebron",
-- "shohei", "ohtani") and stores them as found, so a card needs the full name
-- both to read properly and to stay one card. Anything not listed passes
-- through unchanged. Add a row when a new split shows up in /v1/cards/search.
CREATE OR REPLACE FUNCTION card_subject_of(p_subject TEXT) RETURNS TEXT AS $$
    SELECT CASE p_subject
        WHEN 'Shohei'            THEN 'Shohei Ohtani'
        WHEN 'Ohtani'            THEN 'Shohei Ohtani'
        WHEN 'Lebron'            THEN 'LeBron James'
        WHEN 'Curry'             THEN 'Stephen Curry'
        WHEN 'Wembanyama'        THEN 'Victor Wembanyama'
        WHEN 'Victor Wemby'      THEN 'Victor Wembanyama'
        WHEN 'Shaquille'         THEN 'Shaquille O''Neal'
        WHEN 'Shaq'              THEN 'Shaquille O''Neal'
        WHEN 'Mahomes'           THEN 'Patrick Mahomes'
        WHEN 'Ken Griffey'       THEN 'Ken Griffey Jr.'
        WHEN 'Gretzky'           THEN 'Wayne Gretzky'
        WHEN 'Jokic'             THEN 'Nikola Jokic'
        WHEN 'Giannis'           THEN 'Giannis Antetokounmpo'
        WHEN 'Hakeem'            THEN 'Hakeem Olajuwon'
        WHEN 'Crosby'            THEN 'Sidney Crosby'
        WHEN 'Ovechkin'          THEN 'Alex Ovechkin'
        WHEN 'Bedard'            THEN 'Connor Bedard'
        WHEN 'Makar'             THEN 'Cale Makar'
        WHEN 'Mcdavid'           THEN 'Connor McDavid'
        WHEN 'Connor Mcdavid'    THEN 'Connor McDavid'
        WHEN 'Mark Mcgwire'      THEN 'Mark McGwire'
        WHEN 'Cj Stroud'         THEN 'C.J. Stroud'
        WHEN 'C.j. Stroud'       THEN 'C.J. Stroud'
        WHEN 'Ja''marr Chase'    THEN 'Ja''Marr Chase'
        WHEN 'Jamarr Chase'      THEN 'Ja''Marr Chase'
        WHEN 'Bobby Witt'        THEN 'Bobby Witt Jr.'
        WHEN 'Fernando Tatis'    THEN 'Fernando Tatis Jr.'
        WHEN 'Ronald Acuna'      THEN 'Ronald Acuña Jr.'
        WHEN 'Marvin Harrison'   THEN 'Marvin Harrison Jr.'
        ELSE p_subject
    END
$$ LANGUAGE sql IMMUTABLE;

-- A grade qualifier printed next to the grade: "PSA 9 (OC)", "BGS 9 MC".
-- NULL when there is none. OC off-centre, MC miscut, ST stain, PD print
-- defect, OF out of focus, MK marks.
CREATE OR REPLACE FUNCTION grade_qualifier_of(p_title TEXT) RETURNS TEXT AS $$
    SELECT (regexp_match(upper(p_title),
            '(PSA|SGC|BGS|CGC|BVG)[ -]*[0-9]+(\.5)?[ -]*\(?(OC|MC|ST|PD|OF|MK)\M'))[3]
$$ LANGUAGE sql IMMUTABLE;

-- Every confirmed sale that belongs to an identifiable card, keyed. The chart
-- for a card is one indexed range scan of this, never a pass over `sales`.
DROP MATERIALIZED VIEW IF EXISTS mv_card_sales CASCADE;
CREATE MATERIALIZED VIEW mv_card_sales AS
WITH keyed AS (
    SELECT s.item_id, s.title, s.vertical, s.total_price, s.sold_date, s.listing_format, s.bids,
           s.url, s.image_url, s.is_rookie, s.is_auto,
           card_subject_of(firsts_subject(s.vertical, s.player, s.title)) AS subject,
           s.card_year,
           card_set_of(s.vertical, s.brand_set, s.title)              AS card_set,
           regexp_replace(upper(s.card_number), '^0+(?=[0-9])', '')  AS card_number,
           s.parallel,
           coalesce(s.grade_label, 'Raw')
               || coalesce(' (' || grade_qualifier_of(s.title) || ')', '') AS grade_label
    FROM sales s
    WHERE s.is_publishable AND s.vertical <> 'Unknown' AND s.card_number IS NOT NULL
)
-- format() prints NULL as an empty string, so a missing year and a missing
-- set cannot collide the way concat_ws (which skips NULLs) would let them.
SELECT left(md5(format('%s|%s|%s|%s|%s|%s|%s', vertical, subject, card_year, card_set,
                       card_number, parallel, grade_label)), 16) AS card_id,
       keyed.*
FROM keyed
WHERE subject IS NOT NULL AND (card_year IS NOT NULL OR card_set IS NOT NULL);
CREATE UNIQUE INDEX mv_card_sales_pk ON mv_card_sales (item_id);
CREATE INDEX mv_card_sales_card ON mv_card_sales (card_id, sold_date);

-- One row per card: what it is, what it has done, and whether it moved.
DROP MATERIALIZED VIEW IF EXISTS mv_cards CASCADE;
CREATE MATERIALIZED VIEW mv_cards AS
WITH cfg AS (
    SELECT d.last_date,
           d.last_date - coalesce((SELECT v::int FROM schema_meta WHERE k = 'index_settle_days_all'), 4)
                                                                                  AS settled,
           (SELECT v::int     FROM schema_meta WHERE k = 'movers_recent_days')   AS recent_days,
           (SELECT v::int     FROM schema_meta WHERE k = 'movers_base_days')     AS base_days,
           (SELECT v::int     FROM schema_meta WHERE k = 'movers_min_recent')    AS min_recent,
           (SELECT v::int     FROM schema_meta WHERE k = 'movers_min_base')      AS min_base,
           (SELECT v::float8  FROM schema_meta WHERE k = 'movers_max_spread')    AS max_spread,
           (SELECT v::float8  FROM schema_meta WHERE k = 'movers_agree')         AS agree,
           (SELECT v::float8  FROM schema_meta WHERE k = 'movers_floor_margin')  AS floor_margin,
           (SELECT v::float8  FROM schema_meta WHERE k = 'movers_min_change')    AS min_change,
           (SELECT v::float8  FROM schema_meta WHERE k = 'publish_floor')        AS floor
    FROM (SELECT max(sold_date) AS last_date FROM sales WHERE is_publishable) d
),
b AS (
    SELECT cfg.*,
           settled - recent_days             AS recent_after,   -- recent: (recent_after, settled]
           settled - recent_days - base_days AS base_after      -- before: (base_after, recent_after]
    FROM cfg
),
tagged AS (
    SELECT cs.card_id, cs.total_price::float8 AS p,
           CASE WHEN cs.sold_date > b.recent_after AND cs.sold_date <= b.settled      THEN 'recent'
                WHEN cs.sold_date > b.base_after   AND cs.sold_date <= b.recent_after THEN 'base'
           END AS w
    FROM mv_card_sales cs CROSS JOIN b
),
base AS (
    SELECT card_id, count(*) AS n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY p)     AS median,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY ln(p)) AS log_median
    FROM tagged WHERE w = 'base' GROUP BY card_id
),
base_spread AS (
    SELECT t.card_id,
           1.4826 * percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(ln(t.p) - base.log_median)) AS spread
    FROM tagged t JOIN base USING (card_id)
    WHERE t.w = 'base'
    GROUP BY t.card_id
),
recent AS (
    SELECT t.card_id, count(*) AS n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY t.p) AS median,
           avg(CASE WHEN t.p > base.median THEN 1.0 ELSE 0.0 END) AS above,
           avg(CASE WHEN t.p < base.median THEN 1.0 ELSE 0.0 END) AS below
    FROM tagged t LEFT JOIN base USING (card_id)
    WHERE t.w = 'recent'
    GROUP BY t.card_id
),
roll AS (
    -- card_id is a hash of these columns, so min() of each is simply its value.
    SELECT card_id,
           min(vertical) AS vertical, min(subject) AS subject, min(card_year) AS card_year,
           min(card_set) AS card_set, min(card_number) AS card_number, min(parallel) AS parallel,
           min(grade_label) AS grade_label,
           bool_or(is_rookie) AS is_rookie, bool_or(is_auto) AS is_auto,
           count(*)                                                  AS sales,
           sum(total_price)                                          AS gmv,
           percentile_cont(0.5)  WITHIN GROUP (ORDER BY total_price) AS median_price,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY total_price) AS p25,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY total_price) AS p75,
           min(total_price)                                          AS min_price,
           max(total_price)                                          AS max_price,
           min(sold_date)                                            AS first_sold,
           max(sold_date)                                            AS last_sold,
           (array_agg(total_price ORDER BY sold_date DESC, total_price DESC))[1] AS last_price,
           (array_agg(image_url ORDER BY sold_date DESC, total_price DESC)
                FILTER (WHERE image_url IS NOT NULL))[1]             AS image_url,
           (array_agg(title ORDER BY sold_date DESC, total_price DESC))[1]       AS sample_title
    FROM mv_card_sales
    GROUP BY card_id
),
labelled AS (
    SELECT r.*, concat_ws(' ', r.card_year::text, r.card_set, r.subject,
                          '#' || r.card_number, r.parallel) AS card_label
    FROM roll r
)
SELECT l.*,
       rtrim(left(trim(BOTH '-' FROM regexp_replace(lower(l.card_label || ' ' || l.grade_label),
                                                   '[^a-z0-9]+', '-', 'g')), 80), '-') AS slug,
       lower(concat_ws(' ', l.card_label, l.grade_label, l.vertical,
                       CASE WHEN l.vertical = 'Pokemon' THEN 'pokémon' END, l.sample_title)) AS search_text,
       rc.n            AS recent_sales,
       rc.median       AS recent_median,
       bs.n            AS base_sales,
       bs.median       AS base_median,
       sp.spread       AS base_spread,
       round((100 * (rc.median / NULLIF(bs.median, 0) - 1))::numeric, 1) AS change_pct,
       CASE WHEN rc.median > bs.median THEN rc.above ELSE rc.below END    AS agree_share,
       coalesce(l.grade_label <> 'Raw'
                AND rc.n >= b.min_recent
                AND bs.n >= b.min_base
                AND sp.spread <= b.max_spread
                AND bs.median >= b.floor * b.floor_margin
                AND abs(rc.median / bs.median - 1) * 100 >= b.min_change
                AND (CASE WHEN rc.median > bs.median THEN rc.above ELSE rc.below END) >= b.agree,
                false)  AS is_mover,
       b.base_after + 1   AS base_from,
       b.recent_after     AS base_to,
       b.recent_after + 1 AS recent_from,
       b.settled          AS settled_through
FROM labelled l
CROSS JOIN b
LEFT JOIN recent      rc USING (card_id)
LEFT JOIN base        bs USING (card_id)
LEFT JOIN base_spread sp USING (card_id);
CREATE UNIQUE INDEX mv_cards_pk ON mv_cards (card_id);
CREATE INDEX mv_cards_movers ON mv_cards (change_pct) WHERE is_mover;
CREATE INDEX mv_cards_busiest ON mv_cards (recent_sales DESC) WHERE recent_sales IS NOT NULL;
CREATE INDEX mv_cards_subject ON mv_cards (vertical, subject);
CREATE INDEX mv_cards_search_trgm ON mv_cards USING gin (search_text gin_trgm_ops);

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
    -- Card movers: the keyed sales first, then the per-card rollup that reads them.
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_card_sales;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_cards;
    -- Append-only: records every newly settled first, then publishes today's 1-3.
    PERFORM record_firsts();
    PERFORM publish_firsts();
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
