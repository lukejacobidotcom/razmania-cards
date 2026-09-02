"""
The widget store: embeddable, server-rendered widgets other sites can paste in.

  GET /widgets                 the store - pick a widget, customise, copy the code
  GET /embed/index             the RazMania Index (tier, category, sparkline)
  GET /embed/top-sales         biggest confirmed sales, today or last 7 days
  GET /embed/movers            categories ranked by 7-day index change
  GET /embed/comps             latest confirmed sales matching a search term
  GET /badge/index.svg         the index as an image, for READMEs and signatures

Design rules, all deliberate:

  - Everything renders server-side into a self-contained HTML document. No
    external CSS or JS, no fonts, nothing the host page can block. The only
    script is a 6-line postMessage that reports the widget's own height so a
    host can size the iframe to fit.
  - These routes are OUTSIDE the /v1 API-key guard. They are the public
    product. They read the same materialized views as /v1, so a page view
    never triggers an aggregation, and they inherit the CDN cache headers.
  - Every widget carries an attribution link. That link is why the store
    exists - it is the backlink engine - so it is not an option.
  - Every user-supplied value is validated by pattern or escaped on output.
    The search term in /embed/comps is the only free text, and it goes through
    html.escape before it touches the page.
  - The floor is printed on every widget. The database cannot publish a number
    below it, but a widget on someone else's site can very easily mislabel one.
"""

import html
import json
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, Response

from api.db import q

router = APIRouter()

SITE = "https://razmania.com"
FLOOR_SQL = "(SELECT v::numeric FROM schema_meta WHERE k = 'publish_floor')"

# --------------------------------------------------------------- utilities

def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def money(n) -> str:
    if n is None:
        return "—"
    return "$" + f"{float(n):,.0f}"


def pct(v, suffix=""):
    if v is None:
        return '<span class="flat">—</span>'
    v = float(v)
    cls = "up" if v > 0.05 else ("down" if v < -0.05 else "flat")
    arrow = "▲" if v > 0.05 else ("▼" if v < -0.05 else "▬")
    return f'<span class="{cls}">{arrow} {"+" if v > 0 else ""}{v:.1f}%{esc(suffix)}</span>'


def nice_date(iso) -> str:
    if not iso:
        return "—"
    from datetime import date
    d = iso if isinstance(iso, date) else date.fromisoformat(str(iso)[:10])
    return d.strftime("%d %b %Y").lstrip("0")


def spark(points, w=160, h=36, stroke="var(--up)"):
    vals = [float(p["index_value"]) for p in points]
    n = len(vals)
    if n < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if hi - lo < 0.01:
        lo, hi = lo - 1, hi + 1
    d = "".join(
        f"{'M' if i == 0 else ' L'}{w * i / (n - 1):.1f} {h - 3 - (h - 6) * (v - lo) / (hi - lo):.1f}"
        for i, v in enumerate(vals))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
            f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="1.8" stroke-linejoin="round"/></svg>')


def attribution(campaign: str) -> str:
    qs = urlencode({"utm_source": "widget", "utm_medium": "embed", "utm_campaign": campaign})
    return (f'<a class="attr" href="{SITE}/?{qs}" target="_blank" rel="noopener">'
            f'<b>RazMania</b> Index · razmania.com</a>')


def floor() -> float:
    return float(q(f"SELECT {FLOOR_SQL} AS v")[0]["v"])


def suspended(tier: str):
    """A tier is SUSPENDED when its newest settled point sits more than a week
    past its own lag - the composition guard is withholding windows because the
    cheap tier is being under-collected. An embed on someone else's site must
    say so rather than show a two-week-old number as today's. Returns the
    settled-through date when suspended, else None."""
    r = q("""SELECT max(as_of) FILTER (WHERE settled) AS s, max(as_of) AS c, max(settle_days) AS d
               FROM mv_market_index WHERE tier = %s""", (tier,))[0]
    if r["s"] and r["c"] and (r["c"] - r["s"]).days > int(r["d"] or 0) + 7:
        return r["s"]
    return None


def suspended_note(since) -> str:
    return (f'<p class="empty"><b>Publication suspended</b> since {esc(nice_date(since))}: recent days fail '
            f'the composition check (the $2,000-9,999 range is being under-collected), so the index '
            f'withholds them rather than print an artefact. It resumes on its own when collection is complete.</p>')


# ------------------------------------------------------------------ shell

CSS = """
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 background:var(--bg);color:var(--ink);font-size:var(--fs);line-height:1.4;font-variant-numeric:tabular-nums;
 -webkit-font-smoothing:antialiased}
:root{--fs:14px;--bg:#FBF9F5;--s1:#FFFFFF;--s2:#F2EDE4;--ink:#14110D;--ink2:#57514A;--ink3:#6E6862;
 --line:rgba(26,22,16,.11);--line2:rgba(26,22,16,.22);--accent:#9A6B00;--up:#0a7d33;--down:#b3261e}
[data-theme=dark]{--bg:#14110D;--s1:#1E1A15;--s2:#2A251F;--ink:#F3EFE8;--ink2:#CFC8BE;--ink3:#9A928A;
 --line:rgba(255,255,255,.12);--line2:rgba(255,255,255,.24);--up:#5BD97E;--down:#FF7B6B}
@media (prefers-color-scheme:dark){[data-theme=auto]{--bg:#14110D;--s1:#1E1A15;--s2:#2A251F;--ink:#F3EFE8;
 --ink2:#CFC8BE;--ink3:#9A928A;--line:rgba(255,255,255,.12);--line2:rgba(255,255,255,.24);--up:#5BD97E;--down:#FF7B6B}}
[data-size=s]{--fs:12px}[data-size=l]{--fs:16px}
.w{padding:calc(var(--fs)*1.1) calc(var(--fs)*1.2);display:flex;flex-direction:column;gap:calc(var(--fs)*.6);min-height:100%}
.eyebrow{font-size:.72em;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--accent)}
.title{font-size:1.15em;font-weight:700;line-height:1.2;margin:0}
.big{font-size:2.6em;font-weight:800;line-height:1;letter-spacing:-.02em}
.deltas{font-size:.9em}.deltas>span+span{margin-left:.8em}
.up{color:var(--up)}.down{color:var(--down)}.flat,.muted{color:var(--ink3)}
.muted{font-size:.8em;line-height:1.35}
.spark{display:block;width:100%;height:calc(var(--fs)*2.6)}
.row{display:flex;align-items:baseline;justify-content:space-between;gap:.6em}
.list{display:flex;flex-direction:column;gap:.35em;margin:0;padding:0;list-style:none}
.item{display:grid;grid-template-columns:auto auto minmax(0,1fr) auto;gap:.6em;align-items:center;padding:.4em 0;border-top:1px solid var(--line)}
.item>span:nth-child(3){min-width:0}.item .t{display:block}
.item:first-child{border-top:0}
.item img{width:2.9em;height:2.9em;object-fit:cover;border-radius:.35em;background:var(--s2)}
.item .rk{width:1.4em;text-align:right;color:var(--ink3);font-size:.85em}
.item .t{font-size:.9em;line-height:1.25;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.item .t a{color:inherit;text-decoration:none}.item .t a:hover{text-decoration:underline}
.item .m{font-size:.75em;color:var(--ink3)}
.item .p{font-weight:800;white-space:nowrap}
.bar{height:.5em;background:var(--s2);border-radius:.25em;overflow:hidden}.bar i{display:block;height:100%}
.foot{margin-top:auto;padding-top:.5em;border-top:1px solid var(--line);display:flex;justify-content:space-between;align-items:baseline;gap:.6em;font-size:.72em;color:var(--ink3)}
.attr{color:var(--ink2);text-decoration:none;white-space:nowrap}.attr b{color:var(--accent)}
.empty{color:var(--ink3);font-size:.9em}
"""

RESIZE_JS = """
<script>(function(){function s(){try{parent.postMessage({type:'rzm-resize',height:document.documentElement.scrollHeight,id:new URLSearchParams(location.search).get('_id')||''},'*')}catch(e){}}
window.addEventListener('load',s);window.addEventListener('resize',s);setTimeout(s,300)})();</script>
"""


def shell(title: str, body: str, theme: str, size: str, accent: str) -> HTMLResponse:
    doc = (f'<!doctype html><html lang="en" data-theme="{esc(theme)}" data-size="{esc(size)}">'
           f'<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<meta name="robots" content="noindex"><title>{esc(title)}</title>'
           f'<style>{CSS}:root{{--accent:#{esc(accent)}}}</style></head>'
           f'<body><div class="w">{body}</div>{RESIZE_JS}</body></html>')
    return HTMLResponse(doc)


# Shared parameter validators. Regex-bounded so nothing free-form reaches SQL
# or the page except the comps search term, which is escaped on output.
THEME = Query("light", pattern="^(light|dark|auto)$")
SIZE = Query("m", pattern="^(s|m|l)$")
ACCENT = Query("9A6B00", pattern="^[0-9a-fA-F]{6}$")
TIER = Query("bluechip", pattern="^(all|bluechip)$")
CATEGORY = Query("", pattern="^[A-Za-z0-9 .'&-]{0,40}$")


# ---------------------------------------------------------------- widgets

def _index_rows(tier: str, category: str, days: int):
    vertical = category or "All"
    rows = q("""SELECT as_of, index_value, pct_change_7d, pct_change_30d, sales, median_price,
                       settle_days, base_date
                  FROM mv_market_index
                 WHERE tier = %s AND vertical = %s AND settled
                   AND as_of > (SELECT max(as_of) FROM mv_market_index
                                 WHERE tier = %s AND settled) - %s::int
                 ORDER BY as_of""", (tier, vertical, tier, days))
    return rows


@router.get("/embed/index", response_class=HTMLResponse)
def embed_index(tier: str = TIER, category: str = CATEGORY, days: int = Query(90, ge=14, le=365),
                chart: int = Query(1, ge=0, le=1), theme: str = THEME, size: str = SIZE,
                accent: str = ACCENT):
    rows = _index_rows(tier, category, days)
    fl = money(q(f"SELECT {('(SELECT v::numeric FROM schema_meta WHERE k=%s)')} AS v",
                 ("hot_floor" if tier == "bluechip" else "publish_floor",))[0]["v"])
    name = "Blue Chip Index" if tier == "bluechip" else "Index"
    label = category or "All tracked cards"
    since = suspended(tier)
    if since:
        body = (f'<span class="eyebrow">The RazMania {esc(name)}</span><span class="big" style="font-size:1.6em;color:var(--ink3)">Suspended</span>'
                + suspended_note(since)
                + f'<div class="foot"><span>Sales over {fl}</span>{attribution("index")}</div>')
        return shell("RazMania Index", body, theme, size, accent)
    if not rows:
        body = (f'<span class="eyebrow">The RazMania {esc(name)}</span><p class="empty">No settled data for '
                f'{esc(label)} yet.</p><div class="foot"><span>Sales over {fl}</span>{attribution("index")}</div>')
        return shell("RazMania Index", body, theme, size, accent)
    last = rows[-1]
    up = float(last["index_value"]) >= float(rows[0]["index_value"])
    body = (
        f'<span class="eyebrow">The RazMania {esc(name)}</span>'
        f'<div class="row"><div><span class="big">{float(last["index_value"]):.2f}</span>'
        f'<div class="deltas">{pct(last["pct_change_7d"], " 7d")}{pct(last["pct_change_30d"], " 30d")}</div></div>'
        f'<div class="muted" style="text-align:right">{esc(label)}<br>settled {esc(nice_date(last["as_of"]))}</div></div>'
        + (spark(rows, stroke="var(--up)" if up else "var(--down)") if chart else "")
        + f'<div class="foot"><span>Confirmed sales over {fl} · base 100 at {esc(nice_date(last["base_date"]))}'
          f' · best-offer listings excluded</span>{attribution("index")}</div>'
    )
    return shell("RazMania Index", body, theme, size, accent)


@router.get("/embed/top-sales", response_class=HTMLResponse)
def embed_top_sales(period: int = Query(1, ge=1, le=7), category: str = CATEGORY,
                    count: int = Query(5, ge=1, le=25), images: int = Query(1, ge=0, le=1),
                    theme: str = THEME, size: str = SIZE, accent: str = ACCENT):
    params = [period]
    where = "sold_date > (SELECT max(sold_date) FROM mv_leaderboard_7d) - %s::int"
    if category:
        where += " AND vertical = %s"
        params.append(category)
    params.append(count)
    rows = q(f"""SELECT title, vertical, total_price, sold_date, url, image_url, grade_label, listing_format
                  FROM mv_leaderboard_7d WHERE {where}
                 ORDER BY total_price DESC LIMIT %s""", tuple(params))
    fl = money(floor())
    when = "today" if period == 1 else f"last {period} days"
    items = "".join(
        f'<li class="item"><span class="rk">{i + 1}</span>'
        + (f'<img src="{esc(r["image_url"])}" alt="" loading="lazy" referrerpolicy="no-referrer">' if images and r["image_url"] else '<span></span>')
        + f'<span><span class="t"><a href="{esc(r["url"])}" target="_blank" rel="nofollow noopener">{esc(r["title"])}</a></span>'
          f'<span class="m">{esc(r["vertical"])}{(" · " + esc(r["grade_label"])) if r["grade_label"] else ""}'
          f' · {esc(nice_date(r["sold_date"]))}</span></span>'
          f'<span class="p">{money(r["total_price"])}</span></li>'
        for i, r in enumerate(rows))
    if not rows:
        items = f'<li class="empty">No confirmed sales over {fl} {esc(when)}{(" in " + esc(category)) if category else ""}.</li>'
    body = (f'<span class="eyebrow">Biggest confirmed sales · {esc(when)}</span>'
            f'<p class="title">{esc(category) if category else "Trading cards"} over {fl}</p>'
            f'<ul class="list">{items}</ul>'
            f'<div class="foot"><span>Confirmed eBay sales · best-offer listings excluded</span>{attribution("top-sales")}</div>')
    return shell("Biggest card sales", body, theme, size, accent)


@router.get("/embed/movers", response_class=HTMLResponse)
def embed_movers(tier: str = Query("all", pattern="^(all|bluechip)$"), count: int = Query(6, ge=2, le=14),
                 theme: str = THEME, size: str = SIZE, accent: str = ACCENT):
    rows = q("""SELECT DISTINCT ON (vertical) vertical, index_value, pct_change_7d, pct_change_30d, sales
                  FROM mv_market_index
                 WHERE tier = %s AND settled AND vertical <> 'All' AND pct_change_7d IS NOT NULL
                 ORDER BY vertical, as_of DESC""", (tier,))
    since = suspended(tier)
    rows = [] if since else rows
    rows = sorted(rows, key=lambda r: -abs(float(r["pct_change_7d"])))[:count]
    rows = sorted(rows, key=lambda r: -float(r["pct_change_7d"]))
    fl = money(q("SELECT (SELECT v::numeric FROM schema_meta WHERE k=%s) AS v",
                 ("hot_floor" if tier == "bluechip" else "publish_floor",))[0]["v"])
    mx = max((abs(float(r["pct_change_7d"])) for r in rows), default=1) or 1
    items = "".join(
        f'<li class="item" style="grid-template-columns:minmax(0,1fr) auto"><span><span class="t">{esc(r["vertical"])}'
        f' <span class="m">index {float(r["index_value"]):.1f}</span></span>'
        f'<span class="bar"><i style="width:{abs(float(r["pct_change_7d"])) / mx * 100:.0f}%;'
        f'background:var(--{"up" if float(r["pct_change_7d"]) >= 0 else "down"})"></i></span></span>'
        f'<span class="p">{pct(r["pct_change_7d"])}</span></li>' for r in rows)
    if since:
        items = "<li>" + suspended_note(since) + "</li>"
    elif not rows:
        items = '<li class="empty">Not enough settled history yet.</li>'
    body = (f'<span class="eyebrow">Market movers · 7 days</span>'
            f'<p class="title">{"Blue chip" if tier == "bluechip" else "Card market"} by category</p>'
            f'<ul class="list">{items}</ul>'
            f'<div class="foot"><span>RazMania {"Blue Chip " if tier == "bluechip" else ""}Index, sales over {fl}</span>{attribution("movers")}</div>')
    return shell("Card market movers", body, theme, size, accent)


@router.get("/embed/comps", response_class=HTMLResponse)
def embed_comps(request: Request, category: str = CATEGORY, count: int = Query(6, ge=1, le=25),
                images: int = Query(1, ge=0, le=1), theme: str = THEME, size: str = SIZE,
                accent: str = ACCENT, qs: Optional[str] = Query(None, alias="q", min_length=3, max_length=80)):
    fl = money(floor())
    rows = []
    if qs:
        params = [qs, qs]
        where = "is_publishable AND (title ILIKE '%%' || %s || '%%' OR title %% %s)"
        if category:
            where += " AND vertical = %s"
            params.append(category)
        params.append(count)
        rows = q(f"""SELECT title, vertical, total_price, sold_date, url, image_url, grade_label
                       FROM sales WHERE {where}
                      ORDER BY sold_date DESC, total_price DESC LIMIT %s""", tuple(params))
    items = "".join(
        f'<li class="item"><span class="rk">{i + 1}</span>'
        + (f'<img src="{esc(r["image_url"])}" alt="" loading="lazy" referrerpolicy="no-referrer">' if images and r["image_url"] else '<span></span>')
        + f'<span><span class="t"><a href="{esc(r["url"])}" target="_blank" rel="nofollow noopener">{esc(r["title"])}</a></span>'
          f'<span class="m">{esc(r["vertical"])}{(" · " + esc(r["grade_label"])) if r["grade_label"] else ""}'
          f' · {esc(nice_date(r["sold_date"]))}</span></span>'
          f'<span class="p">{money(r["total_price"])}</span></li>'
        for i, r in enumerate(rows))
    if not qs:
        items = '<li class="empty">Add <code>?q=</code> with a card, player or set name.</li>'
    elif not rows:
        items = f'<li class="empty">No confirmed sales over {fl} match “{esc(qs)}”.</li>'
    body = (f'<span class="eyebrow">Live comps · latest confirmed sales</span>'
            f'<p class="title">{esc(qs) if qs else "Live comps"}{(" · " + esc(category)) if category else ""}</p>'
            f'<ul class="list">{items}</ul>'
            f'<div class="foot"><span>eBay sales over {fl} · best-offer listings excluded</span>{attribution("comps")}</div>')
    return shell(f"Live comps: {qs or ''}", body, theme, size, accent)


# ------------------------------------------------------------------ badge

@router.get("/badge/index.svg")
def badge_index(tier: str = TIER, category: str = CATEGORY, theme: str = Query("light", pattern="^(light|dark)$"),
                accent: str = ACCENT):
    rows = _index_rows(tier, category, 14)
    name = "BLUE CHIP INDEX" if tier == "bluechip" else "CARD INDEX"
    dark = theme == "dark"
    bg, ink, ink3 = ("#14110D", "#F3EFE8", "#9A928A") if dark else ("#FFFFFF", "#14110D", "#6E6862")
    up, down = ("#5BD97E", "#FF7B6B") if dark else ("#0a7d33", "#b3261e")
    if suspended(tier):
        rows = []
    if rows:
        last = rows[-1]
        val = f"{float(last['index_value']):.2f}"
        ch = last["pct_change_7d"]
        chs = "—" if ch is None else f"{'▲' if float(ch) > 0.05 else ('▼' if float(ch) < -0.05 else '▬')} {'+' if float(ch) > 0 else ''}{float(ch):.1f}% 7d"
        chc = ink3 if ch is None else (up if float(ch) > 0.05 else (down if float(ch) < -0.05 else ink3))
        sub = esc(category or "all tracked cards") + " · " + esc(nice_date(last["as_of"]))
    else:
        val, chs, chc, sub = "—", "suspended" if suspended(tier) else "building", ink3,             "publication paused: data quality" if suspended(tier) else "no settled data yet"
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="260" height="64" viewBox="0 0 260 64" role="img" aria-label="RazMania {esc(name)} {val}">
<rect width="260" height="64" rx="10" fill="{bg}" stroke="{ink3}" stroke-opacity=".35"/>
<rect x="0" y="0" width="6" height="64" rx="3" fill="#{esc(accent)}"/>
<text x="18" y="18" font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif" font-size="9" font-weight="700" letter-spacing="1.2" fill="#{esc(accent)}">RAZMANIA {esc(name)}</text>
<text x="18" y="46" font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif" font-size="26" font-weight="800" fill="{ink}">{val}</text>
<text x="252" y="34" text-anchor="end" font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif" font-size="11" font-weight="700" fill="{chc}">{chs}</text>
<text x="252" y="50" text-anchor="end" font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif" font-size="8.5" fill="{ink3}">{sub}</text>
</svg>"""
    return Response(svg, media_type="image/svg+xml")


# ------------------------------------------------------------------ store

@router.get("/widgets", response_class=HTMLResponse)
def widget_store(request: Request):
    base = str(request.base_url).rstrip("/")
    cats = [r["vertical"] for r in q("""SELECT DISTINCT vertical FROM mv_market_index
                                        WHERE settled AND vertical <> 'All' ORDER BY 1""")]
    fl = money(floor())
    cfg = json.dumps({"base": base, "categories": cats, "floor": fl})
    return HTMLResponse(STORE_HTML.replace("__CFG__", cfg))


STORE_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RazMania Widgets</title>
<style>
:root{--bg:#FBF9F5;--s1:#fff;--s2:#F2EDE4;--ink:#14110D;--ink2:#57514A;--ink3:#6E6862;--line:rgba(26,22,16,.11);--line2:rgba(26,22,16,.22);--gold:#9A6B00}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:inherit}
.mast{padding:36px 28px 22px;border-bottom:1px solid var(--line2);max-width:1200px;margin:0 auto}
.eyebrow{font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--gold)}
h1{font-family:Georgia,"Times New Roman",serif;font-size:clamp(34px,5vw,52px);line-height:1;letter-spacing:-.02em;margin:8px 0 12px}
.dek{color:var(--ink2);max-width:64ch;font-size:17px;margin:0}
.wrap{display:grid;grid-template-columns:380px 1fr;gap:28px;max-width:1200px;margin:0 auto;padding:26px 28px 60px}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
.panel{background:var(--s1);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
h2{font-family:Georgia,serif;font-size:20px;margin:0 0 12px}
.pick{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px}
.pick button{text-align:left;padding:10px 12px;border:1px solid var(--line2);border-radius:10px;background:var(--bg);cursor:pointer;font:inherit;color:inherit}
.pick button b{display:block;font-size:14px}.pick button span{font-size:12px;color:var(--ink3)}
.pick button[aria-pressed=true]{border-color:var(--gold);box-shadow:inset 0 0 0 1px var(--gold);background:#fff}
label{display:block;font-size:12px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--ink3);margin:12px 0 4px}
input[type=text],input[type=number],select{width:100%;padding:8px 10px;border:1px solid var(--line2);border-radius:8px;background:#fff;font:inherit;color:inherit}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.seg{display:flex;gap:6px;flex-wrap:wrap}.seg button{padding:6px 10px;border:1px solid var(--line2);border-radius:999px;background:#fff;font:inherit;font-size:13px;cursor:pointer}
.seg button[aria-pressed=true]{background:var(--ink);color:#fff;border-color:var(--ink)}
.chk{display:flex;gap:16px;flex-wrap:wrap;font-size:14px;margin-top:8px}.chk label{display:flex;align-items:center;gap:6px;text-transform:none;letter-spacing:0;font-weight:500;color:var(--ink);margin:0;font-size:14px}
.preview{background:repeating-conic-gradient(var(--s2) 0 25%,transparent 0 50%) 0 0/24px 24px;border:1px solid var(--line);border-radius:14px;padding:18px;display:flex;justify-content:center;align-items:flex-start;min-height:340px;overflow:auto}
.preview iframe{border:0;border-radius:12px;background:#fff;box-shadow:0 8px 30px rgba(0,0,0,.12);max-width:100%}
.code{margin-top:18px}
.tabs{display:flex;gap:6px;margin-bottom:8px}.tabs button{padding:6px 12px;border:1px solid var(--line2);border-radius:8px 8px 0 0;background:#fff;font:inherit;font-size:13px;cursor:pointer}
.tabs button[aria-pressed=true]{background:var(--ink);color:#fff;border-color:var(--ink)}
pre{margin:0;background:#14110D;color:#F3EFE8;padding:14px 16px;border-radius:0 10px 10px 10px;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-all;max-height:220px;overflow:auto}
.copy{margin-top:8px;display:flex;gap:10px;align-items:center}
.copy button{padding:8px 14px;border:0;border-radius:8px;background:var(--gold);color:#fff;font:inherit;font-weight:700;cursor:pointer}
.copy span{font-size:13px;color:var(--ink3)}
.terms{margin-top:22px;font-size:13px;color:var(--ink2);line-height:1.55;max-width:70ch}
.terms b{color:var(--ink)}
.badges{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:8px}
</style></head><body>
<header class="mast">
<span class="eyebrow">RazMania widgets</span>
<h1>Put live card-market data on your site.</h1>
<p class="dek">Four widgets built from confirmed eBay sales over <b id="fl"></b>, with best-offer listings excluded — the ones where eBay shows the asking price, not what was paid. Pick one, make it yours, paste one line of HTML. Free, no key, no JavaScript required on your page.</p>
</header>
<div class="wrap">
<aside class="panel">
<h2>1 · Choose a widget</h2>
<div class="pick" id="pick">
<button data-w="index" aria-pressed="true"><b>The Index</b><span>Blue Chip or broad market, with sparkline</span></button>
<button data-w="top-sales"><b>Top Sales</b><span>Biggest confirmed sales today</span></button>
<button data-w="movers"><b>Market Movers</b><span>Categories by 7-day change</span></button>
<button data-w="comps"><b>Live Comps</b><span>Latest sales for any card or player</span></button>
</div>
<h2>2 · Customise</h2>
<div id="opts"></div>
<label>Size</label>
<div class="seg" id="sizes">
<button data-s="300x200">Small</button><button data-s="400x300" aria-pressed="true">Medium</button><button data-s="600x380">Large</button><button data-s="100%x300">Wide</button><button data-s="custom">Custom</button>
</div>
<div class="two" id="custom" hidden><div><label>Width</label><input type="number" id="cw" value="400" min="200" max="1200"></div><div><label>Height</label><input type="number" id="ch" value="300" min="120" max="1200"></div></div>
<label>Text size</label>
<div class="seg" id="fs"><button data-f="s">Small</button><button data-f="m" aria-pressed="true">Medium</button><button data-f="l">Large</button></div>
<label>Theme</label>
<div class="seg" id="themes"><button data-t="light" aria-pressed="true">Light</button><button data-t="dark">Dark</button><button data-t="auto">Match visitor</button></div>
<label>Accent colour</label>
<div class="two"><input type="color" id="accent" value="#9A6B00" style="height:38px;padding:2px;border:1px solid var(--line2);border-radius:8px;background:#fff"><input type="text" id="accentx" value="9A6B00" maxlength="6" pattern="[0-9a-fA-F]{6}"></div>
</aside>
<main>
<div class="preview"><iframe id="pv" title="Widget preview"></iframe></div>
<div class="code">
<h2>3 · Copy the code</h2>
<div class="tabs" id="tabs"><button data-c="iframe" aria-pressed="true">Embed</button><button data-c="auto">Embed, auto-height</button><button data-c="badge">Image badge</button><button data-c="json">JSON API</button></div>
<pre id="out"></pre>
<div class="copy"><button id="cp">Copy</button><span id="hint"></span></div>
<div class="badges" id="badges"></div>
</div>
<div class="terms">
<p><b>Free to use, with attribution.</b> The numbers are published under CC BY 4.0. Every widget carries a small “RazMania Index · razmania.com” link; please leave it in place — it is the only thing we ask for. The widgets read a cached feed that refreshes daily, so they cost your page nothing and never slow it down.</p>
<p><b>What the numbers are.</b> Confirmed eBay sales of trading-card singles over <span class="flx"></span>. Best-offer-accepted listings are excluded because eBay publishes the seller's asking price on those, not the sale. The Index is a trailing 7-day median rebased to 100, published only once a day has fully settled. <a href="https://razmania.com/index/">Methodology →</a></p>
</div>
</main>
</div>
<script>
const CFG=__CFG__;document.getElementById('fl').textContent=CFG.floor;document.querySelectorAll('.flx').forEach(e=>e.textContent=CFG.floor);
const S={w:'index',size:'400x300',cw:400,ch:300,fs:'m',theme:'light',accent:'9A6B00',code:'iframe',o:{}};
const OPTS={
 index:[['tier','select','Index',[['bluechip','Blue Chip · $10,000+ · 2-day lag'],['all','Broad · '+CFG.floor+'+ · 4-day lag']],'bluechip'],
        ['category','select','Category',[['','All tracked cards'],...CFG.categories.map(c=>[c,c])],''],
        ['days','select','History',[['30','30 days'],['90','90 days'],['180','180 days'],['365','1 year']],'90'],
        ['chart','check','Show sparkline',null,'1']],
 'top-sales':[['period','select','Period',[['1','Today'],['3','Last 3 days'],['7','Last 7 days']],'1'],
        ['category','select','Category',[['','All'],...CFG.categories.map(c=>[c,c])],''],
        ['count','number','How many',[1,25],'5'],['images','check','Show card images',null,'1']],
 movers:[['tier','select','Index',[['all','Broad · '+CFG.floor+'+'],['bluechip','Blue Chip · $10,000+']],'all'],
        ['count','number','How many categories',[2,14],'6']],
 comps:[['q','text','Card, player or set','e.g. Charizard, Luka Doncic, Prizm','Charizard'],
        ['category','select','Category',[['','All'],...CFG.categories.map(c=>[c,c])],''],
        ['count','number','How many',[1,25],'6'],['images','check','Show card images',null,'1']]};
function renderOpts(){const o=document.getElementById('opts');o.innerHTML='';S.o={};
 for(const [k,t,label,arg,def] of OPTS[S.w]){S.o[k]=def;
  if(t==='select'){o.insertAdjacentHTML('beforeend',`<label>${label}</label><select data-k="${k}">${arg.map(([v,l])=>`<option value="${v}" ${v===def?'selected':''}>${l}</option>`).join('')}</select>`)}
  else if(t==='number'){o.insertAdjacentHTML('beforeend',`<label>${label}</label><input type="number" data-k="${k}" value="${def}" min="${arg[0]}" max="${arg[1]}">`)}
  else if(t==='text'){o.insertAdjacentHTML('beforeend',`<label>${label}</label><input type="text" data-k="${k}" value="${def}" placeholder="${arg}" maxlength="80">`)}
  else if(t==='check'){o.insertAdjacentHTML('beforeend',`<div class="chk"><label><input type="checkbox" data-k="${k}" ${def==='1'?'checked':''}> ${label}</label></div>`)}}
 o.querySelectorAll('[data-k]').forEach(el=>el.addEventListener('input',()=>{S.o[el.dataset.k]=el.type==='checkbox'?(el.checked?'1':'0'):el.value;update()}))}
function seg(id,attr,key){document.getElementById(id).addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
 [...b.parentNode.children].forEach(x=>x.setAttribute('aria-pressed',x===b));S[key]=b.dataset[attr];
 if(key==='size'){document.getElementById('custom').hidden=S.size!=='custom'}update()})}
document.getElementById('pick').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
 [...b.parentNode.children].forEach(x=>x.setAttribute('aria-pressed',x===b));S.w=b.dataset.w;renderOpts();update()});
seg('sizes','s','size');seg('fs','f','fs');seg('themes','t','theme');seg('tabs','c','code');
document.getElementById('cw').addEventListener('input',e=>{S.cw=e.target.value;update()});
document.getElementById('ch').addEventListener('input',e=>{S.ch=e.target.value;update()});
document.getElementById('accent').addEventListener('input',e=>{S.accent=e.target.value.slice(1);document.getElementById('accentx').value=S.accent;update()});
document.getElementById('accentx').addEventListener('input',e=>{if(/^[0-9a-fA-F]{6}$/.test(e.target.value)){S.accent=e.target.value;document.getElementById('accent').value='#'+S.accent;update()}});
function dims(){if(S.size==='custom')return[S.cw,S.ch];const[w,h]=S.size.split('x');return[w,h]}
function url(){const p=new URLSearchParams();for(const[k,v]of Object.entries(S.o)){if(v!==''&&v!=null)p.set(k,v)}
 p.set('theme',S.theme);p.set('size',S.fs);if(S.accent.toUpperCase()!=='9A6B00')p.set('accent',S.accent);return `${CFG.base}/embed/${S.w}?${p}`}
function badgeUrl(){const p=new URLSearchParams();if(S.o.tier)p.set('tier',S.o.tier);if(S.o.category)p.set('category',S.o.category);if(S.theme==='dark')p.set('theme','dark');if(S.accent.toUpperCase()!=='9A6B00')p.set('accent',S.accent);return `${CFG.base}/badge/index.svg?${p}`}
function jsonUrl(){const m={index:`/v1/index?tier=${S.o.tier||'bluechip'}`,'top-sales':`/v1/leaderboard?limit=${S.o.count||5}`,movers:`/v1/index?tier=${S.o.tier||'all'}`,comps:`/v1/search?q=${encodeURIComponent(S.o.q||'')}`};return CFG.base+m[S.w]}
const TITLES={index:'RazMania Index','top-sales':'Biggest card sales today',movers:'Card market movers',comps:'Live card comps'};
let t;function update(){clearTimeout(t);t=setTimeout(()=>{const[w,h]=dims();const u=url();const pv=document.getElementById('pv');
 pv.style.width=(w==='100%'?'100%':w+'px');pv.style.height=h+'px';if(pv.src!==u)pv.src=u;
 const wa=w==='100%'?'100%':w;let code='';
 if(S.code==='iframe')code=`<iframe src="${u}" width="${wa}" height="${h}" title="${TITLES[S.w]}" style="border:0;border-radius:12px;max-width:100%" loading="lazy"></iframe>`;
 else if(S.code==='auto')code=`<iframe src="${u}&_id=rzm1" id="rzm1" width="${wa}" height="${h}" title="${TITLES[S.w]}" style="border:0;border-radius:12px;max-width:100%" loading="lazy"></iframe>\n<script>addEventListener('message',e=>{if(e.data&&e.data.type==='rzm-resize'&&e.data.id==='rzm1')document.getElementById('rzm1').style.height=e.data.height+'px'})<\/script>`;
 else if(S.code==='badge')code=`<!-- HTML -->\n<a href="https://razmania.com/index/"><img src="${badgeUrl()}" alt="RazMania Index" width="260" height="64"></a>\n\n<!-- Markdown -->\n[![RazMania Index](${badgeUrl()})](https://razmania.com/index/)`;
 else code=`# JSON, cached 30 min. Attribution required.\ncurl "${jsonUrl()}"`;
 document.getElementById('out').textContent=code;
 document.getElementById('badges').innerHTML=S.code==='badge'?`<img src="${badgeUrl()}" alt="" width="260" height="64"> <img src="${badgeUrl().replace(/theme=\w+&?/,'')}${badgeUrl().includes('?')&&!badgeUrl().endsWith('?')?'&':''}theme=dark" alt="" width="260" height="64">`:''},150)}
document.getElementById('cp').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(document.getElementById('out').textContent);document.getElementById('hint').textContent='Copied.'}catch(e){document.getElementById('hint').textContent='Select the code and copy it.'}setTimeout(()=>document.getElementById('hint').textContent='',1800)});
addEventListener('message',e=>{if(e.data&&e.data.type==='rzm-resize'&&S.code==='auto'){document.getElementById('pv').style.height=e.data.height+'px'}});
renderOpts();update();
</script></body></html>"""
