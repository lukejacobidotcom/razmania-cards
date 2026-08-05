"""
RazMania card-data read API.

Every endpoint reads a materialized view or a single indexed table scan — no
endpoint aggregates the full sales table at request time. That is the whole
point of the split: Postgres does the expensive work once per refresh, this
service only serves it.

  uvicorn api.main:app --host 0.0.0.0 --port $PORT
"""

import os
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg2 import pool

DSN = os.environ["DATABASE_URL"]
ALLOWED = [o.strip() for o in os.environ.get(
    "ALLOWED_ORIGINS", "https://razmania.com,https://www.razmania.com").split(",") if o.strip()]
API_KEY = os.environ.get("API_KEY")            # optional; unset = open read API
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "1800"))

# Data changes once a day. A tiny pool is plenty and keeps us inside the
# connection limit of Render's smallest Postgres plan.
POOL = pool.ThreadedConnectionPool(1, 6, DSN)

app = FastAPI(title="RazMania Card Data API", version="1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=ALLOWED, allow_methods=["GET"], allow_headers=["*"])


@contextmanager
def cursor():
    conn = POOL.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        conn.rollback()          # read-only: never leave a transaction open
    finally:
        POOL.putconn(conn)


def q(sql, params=()):
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


@app.middleware("http")
async def guard_and_cache(request: Request, call_next):
    if API_KEY and request.url.path.startswith("/v1") and request.url.path != "/v1/health":
        if request.headers.get("x-api-key") != API_KEY:
            return JSONResponse({"detail": "bad or missing x-api-key"}, status_code=401)
    resp = await call_next(request)
    if request.method == "GET" and resp.status_code == 200:
        # Long CDN cache + stale-while-revalidate: a daily-refreshed dataset
        # should essentially never be fetched from Postgres by a real visitor.
        # 30 min means a fresh scrape is visible site-wide within half an hour.
        resp.headers["Cache-Control"] = (
            f"public, max-age=300, s-maxage={CACHE_SECONDS}, "
            f"stale-while-revalidate=86400")
    return resp


@app.get("/v1/health")
def health():
    """Includes staleness so the front end can surface a warning instead of
    silently rendering week-old prices as if they were today's."""
    try:
        row = q("""SELECT max(sold_date) AS last_sale,
                          count(*) AS rows,
                          (current_date - max(sold_date)) AS days_stale
                   FROM sales""")[0]
        last_run = q("""SELECT finished_at, status, rows_inserted, rows_updated
                        FROM refresh_log
                        WHERE status = 'ok'
                        ORDER BY id DESC LIMIT 1""")
        return {
            "ok": True,
            "fresh": row["days_stale"] is not None and row["days_stale"] <= 2,
            **row,
            "last_successful_refresh": last_run[0] if last_run else None,
        }
    except Exception as e:                                  # noqa: BLE001
        raise HTTPException(503, f"database unreachable: {e}")


@app.get("/v1/stats")
def stats():
    """Header numbers for the whole site."""
    return q("SELECT * FROM mv_site_stats")[0]


@app.get("/v1/verticals")
def verticals():
    """This week vs last week per vertical — the recurring editorial story."""
    return {"verticals": q("SELECT * FROM mv_vertical_wow ORDER BY gmv DESC NULLS LAST")}


@app.get("/v1/leaderboard")
def leaderboard(
    vertical: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Biggest confirmed sales of the last 7 days."""
    if vertical:
        rows = q("""SELECT * FROM mv_leaderboard_7d WHERE vertical = %s
                    ORDER BY rank_in_vertical LIMIT %s OFFSET %s""",
                 (vertical, limit, offset))
    else:
        rows = q("SELECT * FROM mv_leaderboard_7d ORDER BY rank LIMIT %s OFFSET %s",
                 (limit, offset))
    total = q("SELECT count(*) AS n FROM mv_leaderboard_7d"
              + (" WHERE vertical = %s" if vertical else ""),
              (vertical,) if vertical else ())[0]["n"]
    return {"total": total, "limit": limit, "offset": offset, "results": rows}


@app.get("/v1/daily")
def daily(vertical: Optional[str] = None, days: int = Query(90, ge=1, le=730)):
    """Daily series for sparklines and trend charts."""
    sql = """SELECT * FROM mv_daily_vertical
             WHERE sold_date > (SELECT max(sold_date) FROM sales) - %s::int"""
    params = [days]
    if vertical:
        sql += " AND vertical = %s"
        params.append(vertical)
    sql += " ORDER BY sold_date"
    return {"series": q(sql, tuple(params))}


@app.get("/v1/players")
def players(
    q_: Optional[str] = Query(None, alias="q"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    if q_:
        return {"results": q(
            """SELECT * FROM mv_player_summary
               WHERE player ILIKE %s ORDER BY gmv DESC LIMIT %s""",
            (f"%{q_}%", limit))}
    return {"results": q(
        "SELECT * FROM mv_player_summary ORDER BY gmv DESC LIMIT %s OFFSET %s",
        (limit, offset))}


@app.get("/v1/players/{slug}")
def player_detail(slug: str):
    """Everything a /card-values/<player>/ page needs, in one round trip."""
    head = q("SELECT * FROM mv_player_summary WHERE slug = %s", (slug,))
    if not head:
        raise HTTPException(404, "player not found")
    player = head[0]["player"]
    return {
        "player": head[0],
        "comps": q("""SELECT * FROM mv_card_comps WHERE player = %s
                      ORDER BY sales DESC, median_price DESC LIMIT 60""", (player,)),
        "recent_sales": q("""SELECT item_id, title, total_price, sold_date, grade_label,
                                    listing_format, url, image_url
                             FROM sales
                             WHERE player = %s AND is_publishable
                             ORDER BY sold_date DESC, total_price DESC LIMIT 25""", (player,)),
    }


@app.get("/v1/comps")
def comps(player: Optional[str] = None, grade: Optional[str] = None,
          limit: int = Query(100, ge=1, le=500)):
    sql = "SELECT * FROM mv_card_comps WHERE 1=1"
    params = []
    if player:
        sql += " AND player ILIKE %s"
        params.append(f"%{player}%")
    if grade:
        sql += " AND grade_label = %s"
        params.append(grade)
    sql += " ORDER BY median_price DESC LIMIT %s"
    params.append(limit)
    return {"results": q(sql, tuple(params))}


@app.get("/v1/search")
def search(q_: str = Query(..., alias="q", min_length=3),
           limit: int = Query(40, ge=1, le=200)):
    """Fuzzy title search, backed by the trigram index."""
    return {"results": q(
        """SELECT item_id, title, vertical, total_price, sold_date, grade_label,
                  url, image_url, similarity(title, %s) AS score
           FROM sales
           WHERE is_publishable AND title %% %s
           ORDER BY score DESC, total_price DESC LIMIT %s""",
        (q_, q_, limit))}


@app.get("/v1/sales")
def sales(vertical: Optional[str] = None,
          min_price: float = Query(500, ge=0),
          max_price: Optional[float] = None,
          date_from: Optional[str] = None,
          date_to: Optional[str] = None,
          confirmed_only: bool = True,
          limit: int = Query(100, ge=1, le=1000),
          offset: int = Query(0, ge=0)):
    """Raw row access for ad-hoc front-end filtering."""
    sql = "SELECT * FROM sales WHERE total_price >= %s"
    params = [min_price]
    if confirmed_only:
        sql += " AND is_publishable"
    for cond, val in ((" AND vertical = %s", vertical),
                      (" AND total_price <= %s", max_price),
                      (" AND sold_date >= %s", date_from),
                      (" AND sold_date <= %s", date_to)):
        if val is not None:
            sql += cond
            params.append(val)
    sql += " ORDER BY total_price DESC LIMIT %s OFFSET %s"
    params += [limit, offset]
    return {"limit": limit, "offset": offset, "results": q(sql, tuple(params))}
