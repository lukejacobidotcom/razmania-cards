-- ============================================================================
-- RazMania sale alerts — state.
--
-- Lives in the same Postgres as the card warehouse purely because it is already
-- there, paid for, and backed up. Nothing here joins to the sales tables.
--
-- Design rule: STATE IS WRITTEN AFTER THE TEXT IS SENT, never before. A crash
-- in between therefore re-sends one duplicate on the next run instead of
-- silently swallowing the alert. For a real order, a double text is a nuisance
-- and a missed sale is a problem.
-- ============================================================================

-- One row per registrant who has ever paid anything, across both events.
-- `cents` is the order total we last texted about — that comparison is the
-- whole idempotence mechanism, and it catches an upsell as well as a refund.
CREATE TABLE IF NOT EXISTS sales_seen (
    registrant_id BIGINT PRIMARY KEY,
    event_id      BIGINT      NOT NULL,
    kind          TEXT        NOT NULL,          -- ticket | exhibitor
    name          TEXT        NOT NULL,
    company       TEXT,
    email         TEXT,
    items         TEXT,                          -- "2 Adult, 1 VIP" as last seen
    cents         BIGINT      NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sales_seen_event_idx ON sales_seen (event_id);

-- Audit trail. Every text attempted, including the ones that failed to send —
-- without this the only record that a sale happened is a text on three phones.
CREATE TABLE IF NOT EXISTS sales_alerts (
    id            BIGSERIAL PRIMARY KEY,
    registrant_id BIGINT,                        -- null on a batched summary
    kind          TEXT        NOT NULL,          -- new | increase | decrease | batch
    cents_from    BIGINT,
    cents_to      BIGINT,
    body          TEXT        NOT NULL,
    recipient     TEXT        NOT NULL,
    ok            BOOLEAN     NOT NULL,
    error         TEXT,
    sent_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sales_alerts_sent_idx ON sales_alerts (sent_at DESC);

-- ---------------------------------------------------------------- operations
-- One row per run. Without this the only evidence the job is alive is a text
-- that did not arrive, which is how today went: the service failed every five
-- minutes for an hour on a psycopg2/Python 3.14 import error and nothing said
-- so. `--doctor` reads this.
CREATE TABLE IF NOT EXISTS alert_runs (
    id           BIGSERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    ok           BOOLEAN,
    exit_code    INTEGER,
    sales_count  INTEGER,
    booked_cents BIGINT,
    alerts_sent  INTEGER NOT NULL DEFAULT 0,
    note         TEXT
);

CREATE INDEX IF NOT EXISTS alert_runs_started_idx ON alert_runs (started_at DESC);

-- Tiny key/value scratchpad, used to make warnings FIRE ONCE rather than every
-- five minutes forever. A low-credit warning that repeats 288 times a day is
-- itself the outage.
CREATE TABLE IF NOT EXISTS alert_state (
    k          TEXT PRIMARY KEY,
    v          TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

