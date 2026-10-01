"""
Card Movers — razmania.com/movers/. JSON under /v1 (key-guarded by the
middleware in main.py) and server-rendered HTML under /movers (public, like
the embeds and Firsts).

  GET /v1/movers?vertical=&limit=10      gainers, losers and busiest cards, with the window
  GET /v1/cards/search?q=&vertical=      cards matching a search: "plot any card"
  GET /v1/cards/{card_id}                one card: what it is, its move, every sale
  GET /movers/?vertical=&q=              the page      (?fragment=1 -> {"meta","css","html"})
  GET /movers/c/{card_id}-{slug}         one card's chart page (?fragment=1 likewise)
  GET /movers/strip                      the compact homepage module (?fragment=1)

Everything reads mv_cards and mv_card_sales (db/schema.sql, "CARD MOVERS").
The lists are an indexed filter over a few thousand rows and a card page is
one indexed range scan, so nothing here aggregates `sales` per request. What
counts as a card, what counts as a move, and why the lists are short is
decided there, once; this file only words it.
"""

import os
import re
from datetime import date, timedelta
from statistics import median
from typing import Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from api.db import q
from api.firsts import _jsonable, esc, money, num, short, when

router = APIRouter()

SITE = os.environ.get("MOVERS_SITE_URL", "https://razmania.com/movers/").rstrip("/") + "/"
HOME = os.environ.get("SITE_URL", "https://razmania.com").rstrip("/")

VERTICAL = Query("", pattern="^[A-Za-z0-9 .'&-]{0,40}$")
CARD_REF = re.compile(r"^([0-9a-f]{16})(?:-[a-z0-9-]*)?$")
VLABEL = {"Pokemon": "Pokémon"}

CARD_COLS = """card_id, vertical, subject, card_year, card_set, card_number, parallel, grade_label,
               card_label, slug, is_rookie, is_auto, sales, gmv, median_price, p25, p75,
               min_price, max_price, first_sold, last_sold, last_price, image_url,
               recent_sales, recent_median, base_sales, base_median, base_spread,
               change_pct, agree_share, is_mover, base_from, base_to, recent_from, settled_through"""


# ------------------------------------------------------------------ helpers
def vlabel(v):
    return VLABEL.get(v, v)


def card_url(c):
    return f"{SITE}c/{c['card_id']}-{c['slug']}/"


def day(d):
    """'3 Aug' — axis ticks and compact ranges."""
    return d.strftime("%d %b").lstrip("0")


def span(a, b):
    return f"{day(a)} – {when(b)}" if a.year == b.year else f"{when(a)} – {when(b)}"


def delta(v, bold=True):
    """Signed percent. The number stays in ink; the arrow beside it carries the
    direction in colour, so the value never depends on telling red from green."""
    if v is None:
        return '<span class="mv-muted">—</span>'
    v = float(v)
    up = v > 0
    arrow = f'<i class="mv-arrow mv-arrow--{"up" if up else "down"}" aria-hidden="true">{"▲" if up else "▼"}</i>'
    txt = f'{"+" if up else "−"}{abs(v):.1f}%'
    return f'<span class="mv-delta">{arrow}{"<b>" + txt + "</b>" if bold else txt}</span>'


def config():
    keys = ["movers_recent_days", "movers_base_days", "movers_min_recent", "movers_min_base",
            "movers_max_spread", "movers_agree", "movers_floor_margin", "movers_min_change",
            "publish_floor", "index_settle_days_all"]
    got = {r["k"]: r["v"] for r in q("SELECT k, v FROM schema_meta WHERE k = ANY(%s)", (keys,))}
    f = lambda k, d: float(got.get(k, d))
    return {
        "recent_days": int(f("movers_recent_days", 14)), "base_days": int(f("movers_base_days", 30)),
        "min_recent": int(f("movers_min_recent", 2)), "min_base": int(f("movers_min_base", 3)),
        "max_spread": f("movers_max_spread", 0.18), "agree": f("movers_agree", 0.75),
        "floor_margin": f("movers_floor_margin", 1.25), "min_change": f("movers_min_change", 10),
        "floor": f("publish_floor", 2000), "settle_days": int(f("index_settle_days_all", 4)),
    }


def window():
    """The dates every row in mv_cards was measured on (all rows share them)."""
    w = q("""SELECT base_from, base_to, recent_from, settled_through FROM mv_cards LIMIT 1""")
    s = q("SELECT first_date, last_date FROM mv_site_stats")
    out = dict(w[0]) if w else {"base_from": None, "base_to": None, "recent_from": None, "settled_through": None}
    out.update(s[0] if s else {"first_date": None, "last_date": None})
    return out


# ------------------------------------------------------------------ data
def movers(vertical="", limit=10):
    cat = " AND vertical = %s" if vertical else ""
    p = (vertical,) if vertical else ()
    gainers = q(f"""SELECT {CARD_COLS} FROM mv_cards WHERE is_mover AND change_pct > 0{cat}
                    ORDER BY change_pct DESC, recent_sales DESC, card_id LIMIT %s""", p + (limit,))
    losers = q(f"""SELECT {CARD_COLS} FROM mv_cards WHERE is_mover AND change_pct < 0{cat}
                   ORDER BY change_pct ASC, recent_sales DESC, card_id LIMIT %s""", p + (limit,))
    busiest = q(f"""SELECT {CARD_COLS} FROM mv_cards WHERE recent_sales IS NOT NULL{cat}
                    ORDER BY recent_sales DESC, recent_median DESC, card_id LIMIT %s""", p + (limit,))
    return {"gainers": gainers, "losers": losers, "busiest": busiest}


def categories():
    """Filter chips: every category with a card sold in the recent window,
    busiest first, with how many movers each holds."""
    return q("""SELECT vertical, count(*) FILTER (WHERE is_mover) AS movers,
                       count(*) FILTER (WHERE recent_sales IS NOT NULL) AS active
                  FROM mv_cards GROUP BY vertical
                 HAVING count(*) FILTER (WHERE recent_sales IS NOT NULL) > 0
                 ORDER BY active DESC, vertical""")


def window_sales(card_ids, since):
    """The sales behind each sparkline, grouped by card."""
    if not card_ids or since is None:
        return {}
    rows = q("""SELECT card_id, sold_date, total_price FROM mv_card_sales
                 WHERE card_id = ANY(%s) AND sold_date >= %s
                 ORDER BY sold_date, total_price""", (list(card_ids), since))
    out = {}
    for r in rows:
        out.setdefault(r["card_id"], []).append(r)
    return out


def tokens(text):
    """'Gengar #108 psa9' -> ['gengar', '108', 'psa', '9']. Letters and digits
    are split apart so 'psa10' finds 'PSA 10' and 'sm168' still finds '#SM168'."""
    t = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", " ", (text or "").lower())
    return re.findall(r"[^\W_]+", t)[:8]


def search(text, vertical="", limit=30):
    toks = tokens(text)
    if not toks:
        return {"total": 0, "results": []}
    where, p = [], []
    for t in toks:
        where.append("search_text LIKE %s")
        p.append(f"%{t}%")
    if vertical:
        where.append("vertical = %s")
        p.append(vertical)
    w = " AND ".join(where)
    total = q(f"SELECT count(*) AS n FROM mv_cards WHERE {w}", tuple(p))[0]["n"]
    rows = q(f"""SELECT {CARD_COLS} FROM mv_cards WHERE {w}
                 ORDER BY sales DESC, last_sold DESC, card_id LIMIT %s""", tuple(p) + (limit,))
    return {"total": total, "results": rows}


def card_bundle(card_id):
    got = q(f"SELECT {CARD_COLS}, sample_title FROM mv_cards WHERE card_id = %s", (card_id,))
    if not got:
        raise HTTPException(404, "card not found")
    c = got[0]
    sales = q("""SELECT item_id, title, total_price, sold_date, listing_format, bids, url, image_url
                   FROM mv_card_sales WHERE card_id = %s
                  ORDER BY sold_date, total_price""", (card_id,))
    # Same card, other grades. A title that named no set still counts as the
    # same card when everything else matches; a different named set does not.
    grades = q(f"""SELECT {CARD_COLS} FROM mv_cards
                   WHERE vertical = %s AND subject = %s AND card_number = %s
                     AND card_year IS NOT DISTINCT FROM %s
                     AND (card_set IS NOT DISTINCT FROM %s OR card_set IS NULL OR %s::text IS NULL)
                     AND parallel IS NOT DISTINCT FROM %s AND card_id <> %s
                   ORDER BY median_price DESC""",
               (c["vertical"], c["subject"], c["card_number"], c["card_year"], c["card_set"], c["card_set"],
                c["parallel"], card_id))
    related = q(f"""SELECT {CARD_COLS} FROM mv_cards
                    WHERE vertical = %s AND subject = %s AND card_id <> %s
                    ORDER BY sales DESC, last_sold DESC, card_id LIMIT 12""",
                (c["vertical"], c["subject"], card_id))
    return {"card": c, "sales": sales, "other_grades": grades, "related": related,
            "config": config(), "window": window(), "url": card_url(c)}


def move_status(c, cfg):
    """(is_move, sentence): what the card did, or exactly which rule kept it
    off the lists. Mirrors the is_mover expression in db/schema.sql."""
    rd, bd = cfg["recent_days"], cfg["base_days"]
    if c["is_mover"]:
        up = float(c["change_pct"]) > 0
        return True, (f"Over the last {rd} settled days its median sale was {money(c['recent_median'])}, "
                      f"{'up' if up else 'down'} {abs(float(c['change_pct'])):.1f}% on the {bd} days before "
                      f"({num(c['base_sales'])} sales then, {num(c['recent_sales'])} since).")
    if c["grade_label"] == "Raw":
        return False, ("Raw copies sell in every condition under one label, so RazMania does not call a price "
                       "move on them. The chart still shows every sale.")
    rn, bn = int(c["recent_sales"] or 0), int(c["base_sales"] or 0)
    if rn < cfg["min_recent"]:
        return False, (f"{'No sales' if rn == 0 else 'One sale' if rn == 1 else f'{rn} sales'} in the last {rd} settled days, "
                       f"and a move needs {cfg['min_recent']}, so there is no move to report.")
    if bn < cfg["min_base"]:
        return False, (f"{'No sales' if bn == 0 else 'One sale' if bn == 1 else f'{bn} sales'} in the {bd} days before the "
                       f"last {rd}, and a move needs {cfg['min_base']} to compare against.")
    if c["base_spread"] is not None and float(c["base_spread"]) > cfg["max_spread"]:
        return False, ("Its earlier sales disagree with each other too much to be one card in one condition "
                       "(listings under this name may be more than one card), so no move is called.")
    if c["base_median"] is not None and float(c["base_median"]) < cfg["floor"] * cfg["floor_margin"]:
        return False, (f"It trades too close to the {money(cfg['floor'])} collection floor to measure fairly: "
                       f"sales below the floor are never collected, so a fall would read smaller than it was.")
    ch = float(c["change_pct"] or 0)
    if abs(ch) < cfg["min_change"]:
        return False, (f"Its median moved {'+' if ch > 0 else '−' if ch < 0 else ''}{abs(ch):.1f}% over the last {rd} "
                       f"settled days, inside the {cfg['min_change']:.0f}% a move needs.")
    return False, "Its recent sales landed on both sides of the earlier median, so no direction is called."


# ------------------------------------------------------------------ charts
def nice_ticks(lo, hi, n=4):
    """Round tick values spanning [lo, hi]: steps of 1, 2, 2.5 or 5 x 10^k."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = (lo // step) * step
    ticks, t = [], start
    while t <= hi + step * 0.001:
        if t >= lo - step * 0.001:
            ticks.append(t)
        t += step
    if not ticks or ticks[0] > lo:
        ticks.insert(0, start)
    if ticks[-1] < hi:
        ticks.append(ticks[-1] + step)
    return ticks


def date_ticks(d0, d1, want=5):
    days = (d1 - d0).days
    step = next(s for s in (1, 2, 7, 14, 30, 61, 91) if days / s <= want) if days > 0 else 1
    if step >= 30:
        out, d = [], date(d0.year, d0.month, 1)
        while d <= d1:
            if d >= d0:
                out.append(d)
            m = d.month - 1 + step // 30
            d = date(d.year + m // 12, m % 12 + 1, 1)
        return out
    return [d0 + timedelta(days=i) for i in range(0, days + 1, step)]


def plot_card(c, sales, cfg, w=760, h=320):
    """Every confirmed sale as a dot, the running median as a step line, and
    the two windows a move is measured on as bands behind them.

    The line is the median of the sales in the `recent_days` up to each sale,
    the same span the move is measured on, drawn as steps and broken wherever
    no sale fell inside that span. A median of the last N sales would be
    simpler and wrong for thin cards: five sales a month apart keep it flat
    straight through a doubling."""
    if not sales:
        return ""
    pts = [(s["sold_date"], float(s["total_price"]), s) for s in sales]
    d0, d1 = pts[0][0], pts[-1][0]
    have_windows = c["base_from"] is not None and c["settled_through"] is not None
    if have_windows and (c["recent_sales"] or c["base_sales"]):
        d0 = min(d0, c["base_from"])
        d1 = max(d1, c["settled_through"])
    d0, d1 = d0 - timedelta(days=1), d1 + timedelta(days=1)
    vals = [p for _, p, _ in pts]
    lo, hi = min(vals), max(vals)
    pad = max((hi - lo) * 0.1, hi * 0.04)
    yt = nice_ticks(max(lo - pad, 0), hi + pad)
    y0, y1 = yt[0], yt[-1]
    L, R, T, B = 64, 18, 34, 34
    iw, ih = w - L - R, h - T - B
    span_days = max((d1 - d0).days, 1)
    px = lambda d: round(L + iw * (d - d0).days / span_days, 1)
    py = lambda v: round(T + ih * (1 - (v - y0) / ((y1 - y0) or 1)), 1)
    o = [f'<svg class="mv-plot" viewBox="0 0 {w} {h}" role="img" '
         f'aria-label="{esc(c["card_label"] + " " + c["grade_label"])}: {len(pts)} confirmed sales, table below">']

    # bands: the windows a move is measured on, split by a 2px surface gap
    if have_windows:
        for a, b, label, med in ((c["base_from"], c["base_to"], f"{cfg['base_days']} days before", c["base_median"]),
                                 (c["recent_from"], c["settled_through"], f"Last {cfg['recent_days']} settled days", c["recent_median"])):
            if b < d0 or a > d1:
                continue
            xa = px(max(a, d0))
            xb = px(min(b + timedelta(days=1), d1))
            o.append(f'<rect x="{xa + 1}" y="{T}" width="{max(xb - xa - 2, 1)}" height="{ih}" class="mv-band"/>')
            txt = label + (f" · median {short(med)}" if med is not None else " · no sales")
            o.append(f'<text x="{xa + 6}" y="{T - 10}" class="mv-band-lab">{esc(txt)}</text>')

    for t in yt:
        o.append(f'<line x1="{L}" x2="{w - R}" y1="{py(t)}" y2="{py(t)}" class="mv-grid"/>'
                 f'<text x="{L - 8}" y="{py(t) + 4}" text-anchor="end" class="mv-axis">{esc(short(t))}</text>')
    for d in date_ticks(d0 + timedelta(days=1), d1 - timedelta(days=1)):
        o.append(f'<text x="{px(d)}" y="{h - 10}" text-anchor="middle" class="mv-axis">{esc(day(d))}</text>')

    # running median over the move's own span, as steps, broken across gaps
    if len(pts) >= 3:
        span_n = cfg["recent_days"]
        by_day = {}
        for d, p, _ in pts:
            by_day.setdefault(d, []).append(p)
        days_sold = sorted(by_day)
        steps = [(d, median([p for dd, p, _ in pts if d - timedelta(days=span_n) < dd <= d])) for d in days_sold]
        path, prev = [], None
        for d, v in steps:
            if prev is None or (d - prev[0]).days >= span_n:
                path.append(f"M{px(d)} {py(v)}")
            else:
                path.append(f"H{px(d)} V{py(v)}")
            prev = (d, v)
        o.append(f'<path d="{" ".join(path)}" class="mv-med"/>')

    dots, hits = [], []
    for i, (d, p, s) in enumerate(pts):
        fmt = s.get("listing_format") or ""
        bids = f", {s['bids']} bids" if s.get("bids") else ""
        tip = f"{when(d)} · {money(p)} · {fmt}{bids}"
        dots.append(f'<circle cx="{px(d)}" cy="{py(p)}" r="4.5" class="mv-dot" data-i="{i}"/>')
        hits.append(f'<circle cx="{px(d)}" cy="{py(p)}" r="12" class="mv-hit" data-i="{i}" tabindex="0" '
                    f'data-d="{esc(when(d))}" data-p="{esc(money(p))}" data-f="{esc(fmt + bids)}" '
                    f'data-t="{esc(s["title"])}" aria-label="{esc(tip)}"><title>{esc(tip)}</title></circle>')
    o.append('<g>' + "".join(dots) + '</g><g>' + "".join(hits) + "</g></svg>")
    return ('<div class="mv-plot-wrap" data-mv-plot>' + "".join(o)
            + '<div class="mv-tip" hidden><b></b><span></span><small></small></div></div>')


def spark(c, rows, w=132, h=40):
    """A row's move at a glance: its sales in both windows, the earlier median
    as a grey rule, the recent one as a rule in the direction's colour."""
    if not rows or c["base_from"] is None:
        return '<span class="mv-spark"></span>'
    d0, d1 = c["base_from"], c["settled_through"]
    vals = [float(r["total_price"]) for r in rows if d0 <= r["sold_date"] <= d1]
    meds = [float(x) for x in (c["base_median"], c["recent_median"]) if x is not None]
    if not vals:
        return '<span class="mv-spark"></span>'
    lo, hi = min(vals + meds), max(vals + meds)
    pad = (hi - lo) * 0.15 or hi * 0.05
    lo, hi = lo - pad, hi + pad
    days = max((d1 - d0).days, 1)
    px = lambda d: round(3 + (w - 6) * (d - d0).days / days, 1)
    py = lambda v: round(3 + (h - 6) * (1 - (v - lo) / (hi - lo)), 1)
    o = [f'<svg class="mv-spark" viewBox="0 0 {w} {h}" aria-hidden="true" focusable="false">']
    o.append(f'<line x1="{px(c["recent_from"])}" x2="{px(c["recent_from"])}" y1="0" y2="{h}" class="mv-spark-cut"/>')
    for r in rows:
        if d0 <= r["sold_date"] <= d1:
            o.append(f'<circle cx="{px(r["sold_date"])}" cy="{py(float(r["total_price"]))}" r="2.2" class="mv-spark-dot"/>')
    if c["base_median"] is not None:
        y = py(float(c["base_median"]))
        o.append(f'<line x1="{px(d0)}" x2="{px(c["base_to"])}" y1="{y}" y2="{y}" class="mv-spark-base"/>')
    if c["recent_median"] is not None:
        y = py(float(c["recent_median"]))
        up = float(c["change_pct"] or 0) >= 0
        o.append(f'<line x1="{px(c["recent_from"])}" x2="{px(d1)}" y1="{y}" y2="{y}" class="mv-spark-now mv-spark-now--{"up" if up else "down"}"/>')
    o.append("</svg>")
    return "".join(o)


# ------------------------------------------------------------------ pieces
def thumb(c, cls="mv-thumb"):
    if c.get("image_url"):
        return f'<img class="{cls}" src="{esc(c["image_url"])}" alt="" loading="lazy">'
    return f'<span class="{cls} mv-noimg" aria-hidden="true"></span>'


def mover_row(i, c, rows):
    return (f'<li><a class="mv-row" href="{esc(card_url(c))}">'
            f'<span class="mv-rank">{i}</span>{thumb(c)}'
            f'<span class="mv-name"><b>{esc(c["card_label"])}</b>'
            f'<small>{esc(c["grade_label"])} · {esc(vlabel(c["vertical"]))}</small></span>'
            f'{spark(c, rows)}'
            f'<span class="mv-move">{delta(c["change_pct"])}'
            f'<small>{esc(short(c["base_median"]))} → {esc(short(c["recent_median"]))}</small>'
            f'<small>{num(c["base_sales"])} → {num(c["recent_sales"])} sales</small></span>'
            f'</a></li>')


def mover_list(title, sub, cards, sparks, empty):
    if not cards:
        body = f'<p class="mv-empty">{empty}</p>'
    else:
        body = '<ol class="mv-list">' + "".join(mover_row(i + 1, c, sparks.get(c["card_id"], [])) for i, c in enumerate(cards)) + "</ol>"
    return f'<section class="mv-col"><h2 class="mv-h2">{esc(title)}</h2><p class="mv-sub">{sub}</p>{body}</section>'


def search_form(text="", vertical="", cats=(), label="Plot any card"):
    opts = '<option value="">All categories</option>' + "".join(
        f'<option value="{esc(x["vertical"])}"{" selected" if x["vertical"] == vertical else ""}>{esc(vlabel(x["vertical"]))}</option>'
        for x in cats)
    return (f'<form class="mv-search" method="get" action="{esc(SITE)}" role="search">'
            f'<label for="mv-q">{esc(label)}</label>'
            f'<div class="mv-search-row"><input id="mv-q" type="search" name="q" value="{esc(text)}" '
            f'placeholder="Player or Pokémon, year, set, number, grade" minlength="2" maxlength="80" required>'
            f'<select name="vertical" aria-label="Category">{opts}</select>'
            f'<button type="submit">Search</button></div>'
            f'<p class="mv-hint">Try <a href="{esc(SITE)}?q=gengar+108">gengar 108</a>, '
            f'<a href="{esc(SITE)}?q=brady+bowman+chrome+psa+9">brady bowman chrome psa 9</a> or '
            f'<a href="{esc(SITE)}?q=jordan+1986+fleer">jordan 1986 fleer</a>.</p></form>')


def card_table(cards, recent_days=None):
    """Cards as table rows. With recent_days, the counts and medians are the
    recent window's (the busiest list is ranked on them, so it shows them);
    otherwise they are every tracked sale."""
    rows = []
    for c in cards:
        move = delta(c["change_pct"], bold=False) if c["is_mover"] else '<span class="mv-muted">—</span>'
        n, med = (c["recent_sales"], c["recent_median"]) if recent_days else (c["sales"], c["median_price"])
        rows.append(f'<tr><td><a class="mv-cell-card" href="{esc(card_url(c))}">{thumb(c)}'
                    f'<span><b>{esc(c["card_label"])}</b><small>{esc(c["grade_label"])} · {esc(vlabel(c["vertical"]))}</small></span></a></td>'
                    f'<td class="r">{num(n)}</td><td class="r">{money(med)}</td>'
                    f'<td class="r mv-hide-s">{esc(when(c["last_sold"]))}</td>'
                    f'<td class="r">{move}</td></tr>')
    head = (f'<th class="r">Sales, {recent_days} days</th><th class="r">Median, {recent_days} days</th>' if recent_days
            else '<th class="r">Sales</th><th class="r">Median</th>')
    return ('<div class="mv-table-wrap"><table class="mv-table"><thead><tr><th>Card</th>' + head
            + f'<th class="r mv-hide-s">Last sold</th><th class="r">Move</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def method(cfg, w):
    return (f'<section class="mv-sec"><div class="mv-method"><strong>How moves are measured.</strong> '
            f'RazMania tracks completed eBay card sales over {esc(money(cfg["floor"]))}, collected daily since '
            f'{esc(when(w.get("first_date")))}. Listings closed by Best Offer are left out, because eBay shows the asking price on those, not the sale. '
            f'A card is one card in one grade: player or Pokémon, year, set, number, parallel and grade, read from the listing title. '
            f'Its move is the median of its sales in the last {cfg["recent_days"]} settled days against the median of the {cfg["base_days"]} days before, '
            f'and it only makes a list when every rule holds: it is graded; it sold at least {cfg["min_recent"]} times recently and {cfg["min_base"]} times before; '
            f'its earlier sales agree with each other, so the name is not covering two different cards; at least {cfg["agree"] * 100:.0f}% of its recent sales '
            f'landed on the same side of the earlier median; it trades well clear of the collection floor; and it moved at least {cfg["min_change"]:.0f}%. '
            f'The newest {cfg["settle_days"]} days are left out until every price range has been collected for them. '
            f'Most cards do not move {cfg["min_change"]:.0f}% in two weeks, which is why the lists are short. '
            f'This is a record of what sold, not advice on what to buy or sell.</div></section>')


# ------------------------------------------------------------------ pages
def render_movers(vertical="", text="", limit=10):
    cfg, w = config(), window()
    cats = categories()
    m = movers(vertical, limit)
    shown = m["gainers"] + m["losers"]
    sparks = window_sales({c["card_id"] for c in shown}, w.get("base_from"))
    where = f" in {vlabel(vertical)}" if vertical else ""
    parts = []
    settled = f'<span class="mv-pill">Settled through {esc(when(w["settled_through"]))}</span>' if w.get("settled_through") else ""
    parts.append(f'<div class="mv-kicker"><span class="mv-eyebrow">Card Movers</span>{settled}</div>')
    parts.append('<h1 class="mv-h1">Biggest gainers and losers</h1>')
    if w.get("recent_from"):
        parts.append(f'<p class="mv-stand">The graded cards whose price moved most: confirmed eBay sales over {esc(money(cfg["floor"]))} in the last '
                     f'{cfg["recent_days"]} settled days ({esc(span(w["recent_from"], w["settled_through"]))}) against the {cfg["base_days"]} days before. '
                     f'Every card is measured only against itself.</p>')
    parts.append(search_form(text, vertical, cats))

    if len(text) >= 2:
        res = search(text, vertical, 30)
        if res["results"]:
            more = f' Showing the {len(res["results"])} with the most sales.' if res["total"] > len(res["results"]) else ""
            parts.append(f'<section class="mv-sec mv-results"><h2 class="mv-h2">{num(res["total"])} card{"s" if res["total"] != 1 else ""} match “{esc(text)}”</h2>'
                         f'<p class="mv-sub">Pick one to see every confirmed sale on a chart.{more}</p>{card_table(res["results"])}</section>')
        else:
            parts.append(f'<section class="mv-sec mv-results"><h2 class="mv-h2">No cards match “{esc(text)}”</h2>'
                         f'<p class="mv-sub">Cards are only listed once a sale over {esc(money(cfg["floor"]))} names the player or Pokémon, '
                         f'the card number, and a year or set. Try fewer words, or just the name and number.</p></section>')

    chips = [f'<a class="mv-chip{" is-on" if not vertical else ""}" href="{esc(SITE)}">All</a>']
    for x in cats:
        on = " is-on" if x["vertical"] == vertical else ""
        n = f' <span>{x["movers"]}</span>' if x["movers"] else ""
        chips.append(f'<a class="mv-chip{on}" href="{esc(SITE)}?vertical={esc(quote_plus(x["vertical"]))}">{esc(vlabel(x["vertical"]))}{n}</a>')
    parts.append(f'<nav class="mv-chips" aria-label="Category">{"".join(chips)}</nav>')

    empty = (f"No card{esc(where)} cleared every rule this period. Most cards do not move {cfg['min_change']:.0f}% "
             f"in two weeks; see how moves are measured below.")
    parts.append('<div class="mv-cols">'
                 + mover_list("Gainers", f"Up the most{esc(where)}, last {cfg['recent_days']} settled days vs the {cfg['base_days']} before.",
                              m["gainers"], sparks, empty)
                 + mover_list("Losers", f"Down the most{esc(where)}, same windows.", m["losers"], sparks, empty)
                 + "</div>")
    parts.append('<p class="mv-key"><span class="mv-key-dot"></span>each sale<span class="mv-key-base"></span>median before'
                 '<span class="mv-key-now"></span>median, last ' + str(cfg["recent_days"]) + ' days</p>')

    if m["busiest"]:
        parts.append(f'<section class="mv-sec"><h2 class="mv-h2">Busiest cards{esc(where)}</h2>'
                     f'<p class="mv-sub">Most confirmed sales in the last {cfg["recent_days"]} settled days '
                     f'({esc(span(w["recent_from"], w["settled_through"]))}).</p>'
                     f'{card_table(m["busiest"], cfg["recent_days"])}</section>')
    parts.append(method(cfg, w))
    body = f'<div class="mv mv-page">{"".join(parts)}</div>'
    meta = {"title": "Card Movers: biggest gainers and losers" + (f" in {vlabel(vertical)}" if vertical else ""),
            "description": (f"The graded trading cards whose confirmed eBay sale prices moved most in the last {cfg['recent_days']} settled days, "
                            f"measured card by card. Search any card to chart every sale."),
            "url": SITE + (f"?vertical={quote_plus(vertical)}" if vertical else ""),
            "settled_through": w.get("settled_through")}
    return meta, body


def standfirst(c, cfg, w):
    n = int(c["sales"])
    since = when(w.get("first_date"))
    if n == 1:
        lead = (f"One confirmed sale over {money(cfg['floor'])} since RazMania began tracking on {since}: "
                f"{money(c['last_price'])} on {when(c['last_sold'])}.")
    else:
        lead = (f"{num(n)} confirmed sales over {money(cfg['floor'])} since {when(c['first_sold'])}, most recently "
                f"{money(c['last_price'])} on {when(c['last_sold'])}.")
    return lead + " " + move_status(c, cfg)[1]


def render_card(b):
    c, sales, cfg, w = b["card"], b["sales"], b["config"], b["window"]
    is_move, why = move_status(c, cfg)
    sf = standfirst(c, cfg, w)
    parts = [f'<div class="mv-kicker"><span class="mv-eyebrow">Card Movers</span><span>{esc(vlabel(c["vertical"]))}</span>'
             f'<span>{esc(c["grade_label"])}</span><span class="mv-pill">Tracked since {esc(when(w.get("first_date")))}</span></div>',
             f'<h1 class="mv-h1 mv-h1--card">{esc(c["card_label"])} <span class="mv-grade">{esc(c["grade_label"])}</span></h1>',
             f'<p class="mv-stand">{esc(sf)}</p>']

    img = (f'<div class="mv-img"><img src="{esc(c["image_url"])}" alt="{esc(c["card_label"] + " " + c["grade_label"])}" loading="lazy"></div>'
           if c.get("image_url") else "")
    rng = f'{short(c["p25"])} – {short(c["p75"])}' if int(c["sales"]) >= 4 else "—"
    tiles = (f'<div class="mv-tile"><span>Last sale</span><b>{money(c["last_price"])}</b><small>{esc(when(c["last_sold"]))}</small></div>'
             f'<div class="mv-tile"><span>Median</span><b>{money(c["median_price"])}</b><small>all {num(c["sales"])} tracked sales</small></div>'
             f'<div class="mv-tile"><span>Typical range</span><b>{rng}</b><small>middle half of sales</small></div>'
             f'<div class="mv-tile"><span>Highest · lowest</span><b>{short(c["max_price"])} · {short(c["min_price"])}</b><small>since {esc(when(c["first_sold"]))}</small></div>')
    if is_move:
        up = float(c["change_pct"]) > 0
        movebox = (f'<div class="mv-movebox"><span class="mv-eyebrow">On the {"gainers" if up else "losers"} list</span>'
                   f'<p class="mv-movebox-val">{delta(c["change_pct"])}</p>'
                   f'<p>{esc(short(c["base_median"]))} → {esc(short(c["recent_median"]))} median, '
                   f'{esc(span(c["recent_from"], c["settled_through"]))} against the {cfg["base_days"]} days before.</p></div>')
    else:
        movebox = f'<div class="mv-movebox mv-movebox--quiet"><span class="mv-eyebrow">No move called</span><p>{esc(why)}</p></div>'
    parts.append(f'<div class="mv-hero{" mv-hero--noimg" if not img else ""}">{img}<div><div class="mv-tiles">{tiles}</div>{movebox}</div></div>')

    parts.append('<section class="mv-sec"><h2 class="mv-h2">Every confirmed sale</h2>'
                 f'<p class="mv-sub">Each dot is one sale over {esc(money(cfg["floor"]))}; hover or tab to a dot for the details.'
                 + (f' The line is the median of the sales in the {cfg["recent_days"]} days up to each one, broken where there were none.'
                    if len(sales) >= 3 else "") + '</p>'
                 '<p class="mv-key"><span class="mv-key-dot"></span>each sale'
                 + (f'<span class="mv-key-med"></span>{cfg["recent_days"]}-day median' if len(sales) >= 3 else "")
                 + '<span class="mv-key-band"></span>windows a move is measured on</p>'
                 + plot_card(c, sales, cfg) + "</section>")

    if b["other_grades"]:
        parts.append(f'<section class="mv-sec"><h2 class="mv-h2">Other grades of this card</h2>'
                     f'<p class="mv-sub">The same card in other grades, and listings that left the set out of the title. '
                     f'Each is tracked on its own.</p>{card_table(b["other_grades"])}</section>')

    shown = list(reversed(sales))[:100]
    rows = "".join(
        f'<tr><td class="mv-nowrap">{esc(when(s["sold_date"]))}</td>'
        f'<td><a href="{esc(s["url"])}" rel="nofollow noopener" target="_blank">{esc(s["title"])}</a></td>'
        f'<td class="mv-hide-s">{esc(s.get("listing_format") or "")}{esc(", " + str(s["bids"]) + " bids") if s.get("bids") else ""}</td>'
        f'<td class="r"><b>{money(s["total_price"])}</b></td></tr>' for s in shown)
    more = f" The newest 100 of {num(len(sales))} are listed." if len(sales) > 100 else ""
    parts.append(f'<section class="mv-sec"><h2 class="mv-h2">The sales</h2><p class="mv-sub">Newest first, linked to the eBay listing.{more}</p>'
                 f'<div class="mv-table-wrap"><table class="mv-table"><thead><tr><th>Sold</th><th>Listing</th><th class="mv-hide-s">Format</th>'
                 f'<th class="r">Price</th></tr></thead><tbody>{rows}</tbody></table></div></section>')

    if b["related"]:
        parts.append(f'<section class="mv-sec"><h2 class="mv-h2">More {esc(c["subject"])} cards</h2>'
                     f'<p class="mv-sub">The most-traded {esc(c["subject"])} cards RazMania tracks.</p>{card_table(b["related"])}</section>')
    parts.append(f'<section class="mv-sec">{search_form(label="Plot another card")}</section>')
    parts.append(method(cfg, w))
    body = f'<article class="mv mv-card">{"".join(parts)}</article>' + TIP_JS

    title = f'{c["card_label"]} {c["grade_label"]}: price chart and sales'
    meta = {"card_id": c["card_id"], "slug": c["slug"], "title": title, "description": sf, "url": b["url"],
            "image": c.get("image_url"), "vertical": c["vertical"], "sales": c["sales"],
            # One sale is a fact, not a page worth indexing.
            "noindex": int(c["sales"]) < 3,
            "jsonld": [{"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "RazMania", "item": HOME + "/"},
                {"@type": "ListItem", "position": 2, "name": "Card Movers", "item": SITE},
                {"@type": "ListItem", "position": 3, "name": f'{c["card_label"]} {c["grade_label"]}', "item": b["url"]}]}]}
    return meta, body


def render_strip(limit=3):
    cfg, w = config(), window()
    m = movers("", limit)
    if not (m["gainers"] or m["losers"]):
        return ""

    def items(cards):
        return "".join(f'<li><a href="{esc(card_url(c))}">{thumb(c, "mv-thumb mv-thumb--s")}'
                       f'<span class="mv-name"><b>{esc(c["card_label"])}</b><small>{esc(c["grade_label"])}</small></span>'
                       f'{delta(c["change_pct"])}</a></li>' for c in cards) or '<li class="mv-muted">None this period.</li>'
    return (f'<section class="mv mv-strip"><div class="mv-kicker"><span class="mv-eyebrow">Card Movers · last {cfg["recent_days"]} settled days</span>'
            f'<span><a href="{esc(SITE)}">All movers and any card’s chart →</a></span></div>'
            f'<div class="mv-strip-cols"><div><h3>Gainers</h3><ol>{items(m["gainers"])}</ol></div>'
            f'<div><h3>Losers</h3><ol>{items(m["losers"])}</ol></div></div></section>')


def page(title, desc, body, noindex=False):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)} — RazMania</title><meta name="description" content="{esc(desc)}">'
            + ('<meta name="robots" content="noindex">' if noindex else "")
            + f'<style>body{{margin:0;background:#FBF9F5}}{CSS}</style></head><body>{body}</body></html>')


# ------------------------------------------------------------------ routes
@router.get("/v1/movers")
def v1_movers(vertical: str = VERTICAL, limit: int = Query(10, ge=1, le=50)):
    return _jsonable({"window": window(), "config": config(), **movers(vertical, limit)})


@router.get("/v1/cards/search")
def v1_search(q_: str = Query(..., alias="q", min_length=2, max_length=80),
              vertical: str = VERTICAL, limit: int = Query(30, ge=1, le=100)):
    return _jsonable(search(q_, vertical, limit))


@router.get("/v1/cards/{card_id}")
def v1_card(card_id: str):
    if not re.fullmatch(r"[0-9a-f]{16}", card_id):
        raise HTTPException(404, "card not found")
    return _jsonable(card_bundle(card_id))


@router.get("/movers/", response_class=HTMLResponse)
def movers_page(vertical: str = VERTICAL, q_: Optional[str] = Query(None, alias="q", max_length=80),
                fragment: bool = False):
    meta, body = render_movers(vertical, (q_ or "").strip())
    if fragment:
        return JSONResponse({"meta": _jsonable(meta), "css": CSS, "html": body})
    return HTMLResponse(page(meta["title"], meta["description"], body))


@router.get("/movers/strip", response_class=HTMLResponse)
def movers_strip(fragment: bool = False):
    body = render_strip()
    if fragment:
        return JSONResponse({"css": CSS, "html": body})
    return HTMLResponse(page("Card Movers", "Biggest gainers and losers.", body, noindex=True))


@router.get("/movers/c/{ref}", response_class=HTMLResponse)
def card_page(ref: str, fragment: bool = False):
    m = CARD_REF.match(ref)
    if not m:
        raise HTTPException(404, "card not found")
    meta, body = render_card(card_bundle(m.group(1)))
    if fragment:
        return JSONResponse({"meta": _jsonable(meta), "css": CSS, "html": body})
    return HTMLResponse(page(meta["title"], meta["description"], body, noindex=meta["noindex"]))


# ------------------------------------------------------------------ hover
# One small script: the tooltip for the card chart. The chart, its numbers and
# the sales table are all in the HTML without it; this only adds the readout.
# Values go in with textContent, never innerHTML, because titles are eBay's.
TIP_JS = """<script>(function(){document.querySelectorAll('[data-mv-plot]:not([data-wired])').forEach(function(w){
w.setAttribute('data-wired','1');var tip=w.querySelector('.mv-tip'),on=null;
function show(h){var i=h.getAttribute('data-i'),d=w.querySelector('.mv-dot[data-i="'+i+'"]');if(on)on.classList.remove('is-on');
if(d){d.classList.add('is-on');on=d;}tip.children[0].textContent=h.getAttribute('data-p');
tip.children[1].textContent=h.getAttribute('data-d')+(h.getAttribute('data-f')?' · '+h.getAttribute('data-f'):'');
tip.children[2].textContent=h.getAttribute('data-t');tip.hidden=false;
var r=h.getBoundingClientRect(),b=w.getBoundingClientRect(),x=r.left-b.left+r.width/2,y=r.top-b.top;
tip.style.left=Math.max(0,Math.min(x-tip.offsetWidth/2,b.width-tip.offsetWidth))+'px';
tip.style.top=(y-tip.offsetHeight-6<0?y+r.height+6:y-tip.offsetHeight-6)+'px';}
function hide(){tip.hidden=true;if(on){on.classList.remove('is-on');on=null;}}
w.querySelectorAll('.mv-hit').forEach(function(h){h.addEventListener('pointerenter',function(){show(h);});
h.addEventListener('focus',function(){show(h);});h.addEventListener('pointerleave',hide);h.addEventListener('blur',hide);});});})();</script>"""


# ------------------------------------------------------------------ styles
CSS = """
.mv{--ink:#14110D;--ink2:#57514A;--ink3:#6E6862;--bg:#FBF9F5;--s1:#fff;--s2:#F2EDE4;--line:rgba(26,22,16,.11);--line2:rgba(26,22,16,.2);--gold:#9A6B00;--up:#0a7d33;--down:#b3261e;
 color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.5;max-width:1080px;margin:0 auto;padding:0 20px}
.mv *{box-sizing:border-box}.mv a{color:inherit}
.mv-eyebrow{display:block;font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--gold)}
.mv-kicker{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:baseline;margin:26px 0 8px}
.mv-kicker .mv-eyebrow{display:inline}
.mv-kicker span+span{color:var(--ink3);font-size:12px;letter-spacing:.08em;text-transform:uppercase}
.mv-pill{display:inline-block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink2);border:1px solid var(--line2);border-radius:999px;padding:2px 9px}
.mv-h1{font-family:Georgia,"Times New Roman",serif;font-weight:700;font-size:clamp(34px,5vw,58px);line-height:1.02;letter-spacing:-.02em;margin:6px 0 14px;color:var(--ink)}
.mv-h1--card{font-size:clamp(28px,4.2vw,48px)}
.mv-grade{display:inline-block;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:.42em;font-weight:800;letter-spacing:.02em;vertical-align:middle;background:var(--ink);color:#fff;border-radius:6px;padding:4px 9px;position:relative;top:-.15em}
.mv-stand{font-size:clamp(16px,1.8vw,20px);line-height:1.45;color:var(--ink2);max-width:68ch;margin:0 0 18px}
.mv-h2{font-family:Georgia,serif;font-size:clamp(22px,2.6vw,30px);line-height:1.15;margin:4px 0 4px;color:var(--ink)}
.mv-sub{margin:0 0 14px;font-size:13px;color:var(--ink3)}
.mv-muted{color:var(--ink3);font-size:13px}
.mv-sec{margin:0 0 30px;padding-top:22px;border-top:1px solid var(--line2)}
.mv-search{background:var(--s1);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin:0 0 22px}
.mv-search label{display:block;font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--gold);margin:0 0 8px}
.mv-search-row{display:flex;gap:8px;flex-wrap:wrap}
.mv-search input{flex:1 1 260px;min-width:0;font:inherit;font-size:16px;padding:10px 12px;border:1px solid var(--line2);border-radius:10px;background:#fff;color:var(--ink)}
.mv-search select{font:inherit;font-size:15px;padding:10px;border:1px solid var(--line2);border-radius:10px;background:#fff;color:var(--ink)}
.mv-search button{font:inherit;font-size:15px;font-weight:700;padding:10px 18px;border:0;border-radius:10px;background:var(--ink);color:#fff;cursor:pointer}
.mv-search input:focus-visible,.mv-search select:focus-visible,.mv-search button:focus-visible,.mv a:focus-visible{outline:2px solid var(--gold);outline-offset:2px}
.mv-hint{margin:8px 0 0;font-size:13px;color:var(--ink3)}
.mv-chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 18px}
.mv-chip{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;text-decoration:none;border:1px solid var(--line2);border-radius:999px;padding:5px 12px;background:var(--s1)}
.mv-chip span{font-size:11px;font-weight:700;background:var(--s2);border-radius:999px;padding:0 6px;color:var(--ink2)}
.mv-chip.is-on{background:var(--ink);border-color:var(--ink);color:#fff}.mv-chip.is-on span{background:rgba(255,255,255,.18);color:#fff}
.mv-cols{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:26px;margin:0 0 6px}
@media(max-width:860px){.mv-cols{grid-template-columns:1fr}}
.mv-col{min-width:0}
.mv-list{list-style:none;margin:0;padding:0}
.mv-row{display:grid;grid-template-columns:18px 48px minmax(0,1fr) 132px 104px;gap:10px;align-items:center;padding:10px 4px;border-bottom:1px solid var(--line);text-decoration:none}
.mv-row:hover{background:var(--s2)}
.mv-rank{font-size:12px;font-weight:700;color:var(--ink3);text-align:right}
.mv-thumb{width:48px;height:48px;object-fit:cover;border-radius:6px;background:var(--s2);display:block}
.mv-thumb--s{width:40px;height:40px}
.mv-noimg{display:block}
.mv-name{min-width:0}.mv-name b{display:block;font-size:14px;line-height:1.3;font-weight:650}
.mv-name small,.mv-move small{display:block;font-size:12px;color:var(--ink3);line-height:1.35}
.mv-move{text-align:right;font-variant-numeric:tabular-nums}
.mv-delta{white-space:nowrap;font-variant-numeric:tabular-nums}.mv-delta b{font-size:16px}
.mv-arrow{font-style:normal;font-size:.8em;margin-right:4px}.mv-arrow--up{color:var(--up)}.mv-arrow--down{color:var(--down)}
.mv-spark{display:block;width:132px;height:40px}
.mv-spark-dot{fill:var(--ink3);opacity:.55}.mv-spark-cut{stroke:var(--line2);stroke-width:1}
.mv-spark-base{stroke:var(--ink2);stroke-width:2;stroke-linecap:round}
.mv-spark-now{stroke-width:2.5;stroke-linecap:round}.mv-spark-now--up{stroke:var(--up)}.mv-spark-now--down{stroke:var(--down)}
@media(max-width:520px){.mv-row{grid-template-columns:18px 44px minmax(0,1fr) 92px}.mv-row .mv-spark{display:none}.mv-thumb{width:44px;height:44px}}
.mv-empty{font-size:14px;color:var(--ink2);background:var(--s2);border-radius:10px;padding:14px 16px;margin:0}
.mv-key{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;font-size:12px;color:var(--ink3);margin:8px 0 26px}
.mv-key span{display:inline-block;margin-left:8px}.mv-key span:first-child{margin-left:0}
.mv-key-dot{width:8px;height:8px;border-radius:50%;background:var(--ink3)}
.mv-key-base{width:16px;height:2px;background:var(--ink2)}
.mv-key-now{width:16px;height:3px;background:linear-gradient(90deg,var(--up) 50%,var(--down) 50%)}
.mv-key-med{width:16px;height:2px;background:var(--gold)}
.mv-key-band{width:14px;height:10px;background:var(--s2);border:1px solid var(--line)}
.mv-table-wrap{overflow-x:auto}
.mv-table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
.mv-table th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink3);border-bottom:2px solid var(--line2);padding:6px 8px;white-space:nowrap}
.mv-table td{padding:8px;border-bottom:1px solid var(--line);vertical-align:middle}
.mv-table td.r,.mv-table th.r{text-align:right;white-space:nowrap}
.mv-table td a{text-decoration:none}.mv-table td a:hover b,.mv-table td a:hover{text-decoration:underline}
.mv-cell-card{display:flex;gap:10px;align-items:center;min-width:220px}
.mv-cell-card .mv-thumb{width:40px;height:40px;flex:0 0 40px}
.mv-cell-card b{display:block;font-weight:650}.mv-cell-card small{display:block;font-size:12px;color:var(--ink3)}
.mv-nowrap{white-space:nowrap}
@media(max-width:640px){.mv-hide-s{display:none}}
.mv-hero{display:grid;grid-template-columns:minmax(0,300px) minmax(0,1fr);gap:22px;align-items:start;margin:0 0 30px}
.mv-hero--noimg{grid-template-columns:1fr}
@media(max-width:720px){.mv-hero{grid-template-columns:1fr}}
.mv-img{background:var(--s2);border:1px solid var(--line);border-radius:14px;display:flex;align-items:center;justify-content:center;min-height:240px;overflow:hidden}
.mv-img img{max-width:100%;max-height:420px;object-fit:contain;display:block}
.mv-tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:0 0 12px}
.mv-tile{background:var(--s1);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.mv-tile span{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink3)}
.mv-tile b{display:block;font-size:24px;font-weight:800;letter-spacing:-.02em;margin-top:2px}
.mv-tile small{display:block;font-size:12px;color:var(--ink3)}
.mv-movebox{background:var(--ink);color:#e9e4dc;border-radius:12px;padding:14px 16px}
.mv-movebox .mv-eyebrow{color:#F5C518}
.mv-movebox p{margin:4px 0 0;font-size:14px;line-height:1.5}
.mv-movebox-val .mv-delta b{font-size:34px;color:#fff;letter-spacing:-.02em}
.mv-movebox .mv-arrow{font-size:22px}.mv-movebox .mv-arrow--up{color:#5fd68a}.mv-movebox .mv-arrow--down{color:#ff8a80}
.mv-movebox--quiet{background:var(--s2);color:var(--ink2)}.mv-movebox--quiet .mv-eyebrow{color:var(--gold)}
.mv-plot-wrap{position:relative;margin:6px 0 4px}
.mv-plot{display:block;width:100%;height:auto;overflow:visible}
.mv-grid{stroke:var(--line);stroke-width:1}
.mv-axis{font-size:11px;fill:var(--ink3);font-variant-numeric:tabular-nums}
.mv-band{fill:var(--s2)}
.mv-band-lab{font-size:11px;fill:var(--ink2);font-weight:600}
.mv-med{fill:none;stroke:var(--gold);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.mv-dot{fill:var(--ink3);stroke:var(--s1);stroke-width:2}
.mv-dot.is-on{fill:var(--ink);r:6.5}
.mv-hit{fill:transparent;cursor:pointer;outline:none}
.mv-hit:focus-visible{stroke:var(--gold);stroke-width:2}
.mv-tip{position:absolute;z-index:2;max-width:300px;background:#fff;border:1px solid var(--line2);border-radius:10px;box-shadow:0 6px 20px rgba(20,17,13,.14);padding:8px 10px;pointer-events:none}
.mv-tip b{display:block;font-size:16px;font-variant-numeric:tabular-nums}
.mv-tip span{display:block;font-size:12px;color:var(--ink2)}
.mv-tip small{display:block;font-size:12px;color:var(--ink3);line-height:1.35;margin-top:3px}
.mv-method{background:var(--s2);border-left:3px solid var(--gold);border-radius:0 10px 10px 0;padding:14px 18px;font-size:14px;line-height:1.6;color:var(--ink2)}
.mv-strip{padding-bottom:10px}
.mv-strip-cols{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
@media(max-width:700px){.mv-strip-cols{grid-template-columns:1fr}}
.mv-strip h3{font-family:Georgia,serif;font-size:20px;margin:0 0 4px}
.mv-strip ol{list-style:none;margin:0;padding:0}
.mv-strip li a{display:grid;grid-template-columns:40px minmax(0,1fr) auto;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line);text-decoration:none}
"""
