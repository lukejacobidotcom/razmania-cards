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

-- A single boolean that encodes "this row is safe to publish a price from".
-- Defined once here so the API, the views and the front end can never disagree.
-- Added conditionally: the materialized views below depend on this column, so a
-- drop-and-recreate would fail on every re-run of this file.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'sales' AND column_name = 'is_publishable') THEN
        ALTER TABLE sales ADD COLUMN is_publishable BOOLEAN
            GENERATED ALWAYS AS (NOT best_offer_accepted AND NOT is_junk) STORED;
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
    (SELECT count(*) FROM sales)                              AS total_sales,
    (SELECT count(*) FROM sales WHERE is_publishable)         AS confirmed_sales,
    (SELECT sum(total_price) FROM sales WHERE is_publishable) AS total_gmv,
    (SELECT max(total_price) FROM sales WHERE is_publishable) AS biggest_sale,
    (SELECT min(sold_date) FROM sales)                        AS first_date,
    (SELECT max(sold_date) FROM sales)                        AS last_date,
    (SELECT count(DISTINCT player) FROM sales WHERE player IS NOT NULL) AS players_tracked,
    now()                                                     AS generated_at;

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
GRANT CONNECT ON DATABASE razmania TO razmania_read;
GRANT USAGE ON SCHEMA public TO razmania_read;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO razmania_read;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO razmania_read;
