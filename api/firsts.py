"""
Firsts — the daily series. JSON under /v1 (key-guarded by the middleware in
main.py) and server-rendered HTML under /firsts (public, like the embeds).

  GET /v1/firsts?days=14            the published feed, newest day first
  GET /v1/firsts/log?...            every recorded first, filterable
  GET /v1/firsts/{id}               one milestone with all of its context
  GET /firsts/a/{id}                the article, as a page
  GET /firsts/a/{id}?fragment=1     {"meta": {...}, "html": "..."} for WordPress to host
  GET /firsts/today                 today's picks as a fragment (?full=1 for a page)
  GET /firsts/                      index of published days (preview)

Every number on the article is read from a materialized view or a single
indexed scan and cached by the CDN and by WordPress; nothing aggregates the
whole table per request except the subject block, which is one filtered scan
over a few tens of thousands of rows and is cached with the page.

The article is honest by construction: every claim is "tracked by RazMania
since <first day>", the floor is printed, and a first is never described as
"ever". See db/schema.sql, "FIRSTS".
"""

import html
import json
import math
import os
import re
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from api.db import q

router = APIRouter()

SITE = os.environ.get("FIRSTS_SITE_URL", "https://razmania.com/firsts/").rstrip("/") + "/"
HOME = os.environ.get("SITE_URL", "https://razmania.com").rstrip("/")
LOGO = os.environ.get("SITE_LOGO_URL", "")

VLABEL = {"Pokemon": "Pokémon", "All": "the hobby"}
ALLTIME_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firsts", "firsts.json")


def alltime():
    """The hand-researched all-time table (firsts/firsts.json), so a tracked
    first can say what hobby history says about the same line."""
    if not hasattr(alltime, "_d"):
        try:
            with open(ALLTIME_PATH, encoding="utf-8") as fh:
                alltime._d = json.load(fh)
        except Exception:                                   # noqa: BLE001
            alltime._d = {"verticals": [], "cards": []}
    return alltime._d


def alltime_context(vertical, subject, threshold):
    """The highest all-time line at or below `threshold` that a subject (card
    profile) or its category has crossed, as a sentence — or None."""
    d = alltime()
    groups = []
    for c in d.get("cards", []):
        if subject and c.get("subject") and c["subject"].lower() == subject.lower() and c.get("vertical") == vertical:
            groups.append(("subject", c))
    for v in d.get("verticals", []):
        if v.get("vertical") == vertical:
            groups.append(("vertical", v))
    for level, g in groups:
        best = None
        for f in g.get("firsts", []):
            if f.get("status") in ("verified", "reported") and float(f["threshold"]) <= float(threshold):
                best = f
        if best:
            who = subject if level == "subject" else f"a {vlabel(vertical)} card"
            line = money(best["threshold"])
            return (f"In hobby history, {who} first cleared {line} in {when_loose(best['date'])}: {best['card']}, "
                    f"{money(best['price'])}{', ' + best['venue'] if best.get('venue') else ''}"
                    f"{' (' + best['status'] + ')' if best.get('status') == 'reported' else ''}.")
    return None


def when_loose(d):
    if not d:
        return "—"
    if re.match(r"^\d{4}$", d):
        return d
    if re.match(r"^\d{4}-\d{2}$", d):
        return datetime.strptime(d, "%Y-%m").strftime("%B %Y")
    return when(d)


KIND_LABEL = {
    "price:vertical": "Category milestone", "price:subject": "Player / character milestone", "price:card": "Card milestone",
    "price:card_grade": "Card milestone", "price:set": "Set milestone", "price:year": "Release milestone",
    "price:year_rookie": "Rookie milestone", "count:day": "Volume milestone", "count:week": "Volume milestone",
    "count:total": "Cumulative milestone", "gmv:day": "Volume milestone", "gmv:week": "Volume milestone",
    "gmv:total": "Cumulative milestone", "index:above": "Index milestone", "index:below": "Index milestone",
}


# ------------------------------------------------------------------ helpers
def vlabel(v):
    return VLABEL.get(v, v)


def money(n, dec=0):
    if n is None:
        return "—"
    return "$" + f"{float(n):,.{dec}f}"


def short(n):
    n = float(n)
    if n >= 1_000_000:
        s = f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"${s}M"
    if n >= 1000:
        s = f"{n / 1000:.1f}".rstrip("0").rstrip(".")
        return f"${s}K"
    return f"${n:,.0f}"


def num(n):
    return f"{float(n):,.0f}" if n is not None else "—"


def when(d, long=False):
    if not d:
        return "—"
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return d.strftime("%A %d %B %Y").replace(" 0", " ") if long else d.strftime("%d %b %Y").lstrip("0")


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:80].rstrip("-")


def esc(s):
    return html.escape("" if s is None else str(s), quote=True)


def article_url(m):
    return f"{SITE}{m['id']}-{slugify(m['headline'])}/"


def ordinal(n):
    n = int(n)
    return f"{n:,}" + ("th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def _jsonable(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
        f = float(v)
        return int(f) if f.is_integer() and abs(f) < 1e15 else f
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


# ------------------------------------------------------------------ data
def window():
    w = q("""SELECT min(sold_date) AS tracked_since, max(sold_date) AS last_sale,
                    max(sold_date) - coalesce((SELECT v::int FROM schema_meta WHERE k='index_settle_days_bluechip'), 2) AS settled_hot,
                    max(sold_date) - coalesce((SELECT v::int FROM schema_meta WHERE k='index_settle_days_all'), 4) AS settled_all,
                    (SELECT v::numeric FROM schema_meta WHERE k='publish_floor') AS floor
             FROM sales WHERE is_publishable""")[0]
    return w


def feed(days=14):
    rows = q("""SELECT * FROM firsts_log WHERE published_on IS NOT NULL
                  AND published_on > (SELECT max(published_on) FROM firsts_log) - %s
                ORDER BY published_on DESC, publish_rank""", (days,))
    out = []
    for r in rows:
        if not out or out[-1]["published_on"] != r["published_on"]:
            out.append({"published_on": r["published_on"], "items": []})
        r["url"] = article_url(r)
        out[-1]["items"].append(r)
    return out


def named_subject(m):
    """True when the row is about a player, Pokémon, card or set — not a whole category."""
    return m["vertical"] != "All" and m["subject"] != m["vertical"] and m["kind"] in (
        "price:subject", "count:day", "count:week", "count:total", "gmv:day", "gmv:week", "gmv:total")


def subject_name(m):
    """The player / Pokémon a row is about, if any (cards carry it in detail)."""
    if m["kind"] in ("price:card", "price:card_grade"):
        return (m.get("detail") or {}).get("player")
    if named_subject(m):
        return m["subject"]
    return None


def bundle(mid):
    rows = q("SELECT * FROM firsts_log WHERE id = %s", (mid,))
    if not rows:
        raise HTTPException(404, "no such first")
    m = rows[0]
    w = window()
    b = {"milestone": m, "window": w, "url": article_url(m), "kind_label": KIND_LABEL.get(m["kind"], "Milestone"),
         "vertical_label": vlabel(m["vertical"])}
    v = m["vertical"]

    # The sale itself (copied fields survive retention; the live row adds bids etc.)
    if m["item_id"]:
        s = q("SELECT * FROM sales WHERE item_id = %s", (m["item_id"],))
        b["sale"] = s[0] if s else {"item_id": m["item_id"], "title": m["title"], "total_price": m["value"],
                                    "sold_date": m["first_date"], "url": m["url"], "image_url": m["image_url"]}
        if v != "All":
            b["sale_rank_vertical"] = q("""SELECT count(*) + 1 AS n FROM sales
                                           WHERE is_publishable AND vertical = %s AND total_price > %s""",
                                        (v, b["sale"]["total_price"]))[0]["n"]
        b["sale_rank_all"] = q("SELECT count(*) + 1 AS n FROM sales WHERE is_publishable AND total_price > %s",
                               (b["sale"]["total_price"],))[0]["n"]

    # The ladder: every line this subject has crossed under this kind.
    b["ladder"] = q("""SELECT id, threshold, first_date, value, item_id, title, url, headline
                       FROM firsts_log WHERE kind = %s AND vertical = %s AND subject = %s AND floor = %s
                       ORDER BY threshold""", (m["kind"], v, m["subject"], m["floor"]))

    # The subject in numbers, computed live for players and Pokémon alike.
    name = subject_name(m)
    if name:
        st = q("""SELECT count(*) AS sales, sum(total_price) AS gmv,
                         percentile_cont(0.5) WITHIN GROUP (ORDER BY total_price) AS median_price,
                         percentile_cont(0.25) WITHIN GROUP (ORDER BY total_price) AS p25,
                         percentile_cont(0.75) WITHIN GROUP (ORDER BY total_price) AS p75,
                         max(total_price) AS top_sale, min(sold_date) AS first_seen, max(sold_date) AS last_sold
                  FROM sales WHERE is_publishable AND vertical = %s
                    AND firsts_subject(vertical, player, title) = %s""", (v, name))[0]
        if st["sales"]:
            b["subject"] = {"name": name, **st}
            b["subject_recent"] = q("""SELECT item_id, title, total_price, sold_date, grade_label, listing_format, bids, url, image_url
                                       FROM sales WHERE is_publishable AND vertical = %s
                                         AND firsts_subject(vertical, player, title) = %s
                                       ORDER BY sold_date DESC, total_price DESC LIMIT 8""", (v, name))
            b["subject_top"] = q("""SELECT item_id, title, total_price, sold_date, grade_label, url, image_url
                                    FROM sales WHERE is_publishable AND vertical = %s
                                      AND firsts_subject(vertical, player, title) = %s
                                    ORDER BY total_price DESC LIMIT 5""", (v, name))
            b["subject_daily"] = q("""SELECT sold_date, count(*) AS n, sum(total_price) AS gmv, max(total_price) AS top
                                      FROM sales WHERE is_publishable AND vertical = %s
                                        AND firsts_subject(vertical, player, title) = %s
                                      GROUP BY sold_date ORDER BY sold_date""", (v, name))
            b["comps"] = q("""SELECT card_year, brand_set, card_number, parallel, grade_label, sales, median_price, p25, p75, image_url
                              FROM mv_card_comps WHERE player = %s ORDER BY sales DESC, median_price DESC LIMIT 6""", (name,))
            b["subject_firsts"] = q("""SELECT id, kind, headline, first_date, threshold, value, score, published_on
                                       FROM firsts_log WHERE vertical = %s AND (subject = %s OR detail->>'player' = %s) AND id <> %s
                                       ORDER BY first_date DESC, score DESC LIMIT 12""", (v, name, name, mid))

    # The category this week, its 60-day median, its index.
    if v != "All":
        wow = q("SELECT * FROM mv_vertical_wow WHERE vertical = %s", (v,))
        b["vertical_week"] = wow[0] if wow else None
        b["vertical_daily"] = q("""SELECT sold_date, sales, confirmed_sales, gmv, median_price, max_price
                                   FROM mv_daily_vertical WHERE vertical = %s ORDER BY sold_date""", (v,))
        b["index"] = q("""SELECT tier, as_of, index_value, pct_change_7d, pct_change_30d
                          FROM mv_market_index m WHERE vertical = %s AND settled
                            AND as_of = (SELECT max(as_of) FROM mv_market_index x WHERE x.tier = m.tier AND x.vertical = m.vertical AND x.settled)
                          ORDER BY tier""", (v,))
    else:
        b["vertical_daily"] = q("""SELECT sold_date, sum(sales) AS sales, sum(confirmed_sales) AS confirmed_sales,
                                          sum(gmv) AS gmv, max(max_price) AS max_price
                                   FROM mv_daily_vertical WHERE vertical <> 'Unknown' GROUP BY sold_date ORDER BY sold_date""")
        b["index"] = q("""SELECT tier, as_of, index_value, pct_change_7d, pct_change_30d
                          FROM mv_market_index m WHERE vertical = 'All' AND settled
                            AND as_of = (SELECT max(as_of) FROM mv_market_index x WHERE x.tier = m.tier AND x.vertical = 'All' AND x.settled)
                          ORDER BY tier""")
    b["vertical_firsts"] = q("""SELECT id, kind, headline, first_date, score, published_on FROM firsts_log
                                WHERE vertical = %s AND id <> %s AND score >= 5
                                ORDER BY first_date DESC, score DESC LIMIT 10""", (v, mid))

    # Day / week milestones: what made the day.
    if m["kind"] in ("count:day", "gmv:day", "count:week", "gmv:week"):
        d0 = m["first_date"] if m["kind"].endswith("day") else m["first_date"]
        span = 0 if m["kind"].endswith("day") else 6
        flt = "vertical <> 'Unknown'" if v == "All" else "vertical = %s"
        params = [] if v == "All" else [v]
        subj = "" if not named_subject(m) else " AND firsts_subject(vertical, player, title) = %s"
        if subj:
            params.append(m["subject"])
        floor = float(m["floor"]) if m["kind"].startswith("count") else float(w["floor"])
        b["day_top"] = q(f"""SELECT item_id, title, total_price, sold_date, grade_label, url, image_url
                             FROM sales WHERE is_publishable AND {flt}{subj}
                               AND sold_date BETWEEN %s AND %s AND total_price >= %s
                             ORDER BY total_price DESC LIMIT 6""", (*params, d0 - (span and __import__("datetime").timedelta(days=span)), d0, floor))
        b["day_stats"] = q(f"""SELECT count(*) AS n, sum(total_price) AS gmv, percentile_cont(0.5) WITHIN GROUP (ORDER BY total_price) AS median_price,
                                      count(*) FILTER (WHERE listing_format = 'Auction') AS auctions
                               FROM sales WHERE is_publishable AND {flt}{subj}
                                 AND sold_date BETWEEN %s AND %s AND total_price >= %s""",
                           (*params, d0 - (span and __import__("datetime").timedelta(days=span)), d0, floor))[0]

    # Index milestones: the series, with the level.
    if m["kind"].startswith("index:"):
        tier = "bluechip" if m["subject"].startswith("Blue Chip") else "all"
        b["index_series"] = q("""SELECT as_of, index_value, settled FROM mv_market_index
                                 WHERE tier = %s AND vertical = %s AND settled ORDER BY as_of""", (tier, v))
        b["index_tier"] = tier

    # The rest of the day's picks.
    if m["published_on"]:
        b["same_day"] = q("""SELECT id, kind, vertical, headline, first_date, image_url, publish_rank FROM firsts_log
                             WHERE published_on = %s AND id <> %s ORDER BY publish_rank""", (m["published_on"], mid))
    return b


# ------------------------------------------------------------------ svg
def svg_line(pts, ykey, w=720, h=220, mark_x=None, level=None, money_axis=True, label=""):
    pts = [p for p in pts if p.get(ykey) is not None]
    if len(pts) < 2:
        return ""
    vals = [float(p[ykey]) for p in pts]
    lo, hi = min(vals), max(vals)
    if level is not None:
        lo, hi = min(lo, level), max(hi, level)
    if hi - lo < 1e-9:
        lo -= 1
        hi += 1
    pad = (hi - lo) * 0.12
    lo -= pad
    hi += pad
    L, R, T, B = 58, 16, 16, 30
    iw, ih = w - L - R, h - T - B
    n = len(pts)
    px = lambda i: round(L + iw * i / (n - 1), 1)
    py = lambda v: round(T + ih * (1 - (v - lo) / (hi - lo)), 1)
    d = " ".join(f"{'M' if i == 0 else 'L'}{px(i)} {py(v)}" for i, v in enumerate(vals))
    area = d + f" L{px(n - 1)} {T + ih} L{px(0)} {T + ih} Z"
    fmt = (lambda v: short(v)) if money_axis else (lambda v: f"{v:,.0f}")
    out = [f'<svg class="fx-chart" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(label)}">']
    for frac in (0, 0.5, 1):
        yv = lo + (hi - lo) * frac
        out.append(f'<line x1="{L}" x2="{w - R}" y1="{py(yv)}" y2="{py(yv)}" class="fx-grid"/>'
                   f'<text x="{L - 8}" y="{py(yv) + 4}" text-anchor="end" class="fx-axis">{esc(fmt(yv))}</text>')
    if level is not None:
        out.append(f'<line x1="{L}" x2="{w - R}" y1="{py(level)}" y2="{py(level)}" class="fx-level"/>'
                   f'<text x="{w - R}" y="{py(level) - 5}" text-anchor="end" class="fx-axis fx-axis--gold">{esc(fmt(level))}</text>')
    out.append(f'<path d="{area}" class="fx-area"/><path d="{d}" class="fx-line"/>')
    if mark_x is not None:
        for i, p in enumerate(pts):
            if str(p.get("sold_date") or p.get("as_of")) == str(mark_x):
                out.append(f'<circle cx="{px(i)}" cy="{py(vals[i])}" r="5" class="fx-dot"/>'
                           f'<line x1="{px(i)}" x2="{px(i)}" y1="{T}" y2="{T + ih}" class="fx-mark"/>')
    out.append(f'<text x="{L}" y="{h - 8}" class="fx-axis">{esc(when(pts[0].get("sold_date") or pts[0].get("as_of")))}</text>'
               f'<text x="{w - R}" y="{h - 8}" text-anchor="end" class="fx-axis">{esc(when(pts[-1].get("sold_date") or pts[-1].get("as_of")))}</text>')
    out.append("</svg>")
    return "".join(out)


def svg_ladder(ladder, current_id, metric="price"):
    """The lines crossed, as steps: each rung is a threshold with its date."""
    if not ladder:
        return ""
    n = len(ladder)
    w, rung_h = 720, 34
    h = rung_h * n + 20
    lo = math.log10(float(ladder[0]["threshold"]))
    hi = math.log10(float(ladder[-1]["threshold"]))
    out = [f'<svg class="fx-ladder" viewBox="0 0 {w} {h}" role="img" aria-label="Milestones crossed">']
    for i, r in enumerate(reversed(ladder)):
        y = 10 + i * rung_h
        frac = 1 if hi == lo else (math.log10(float(r["threshold"])) - lo) / (hi - lo)
        bw = 120 + int(440 * frac)
        cur = r["id"] == current_id
        out.append(f'<rect x="150" y="{y}" width="{bw}" height="{rung_h - 8}" rx="6" class="fx-rung{" fx-rung--cur" if cur else ""}"/>')
        lab = short(r["threshold"]) if metric == "price" else (f"{float(r['threshold']):,.0f}" if metric == "count" else short(r["threshold"]))
        out.append(f'<text x="140" y="{y + 18}" text-anchor="end" class="fx-rung-lab{" fx-rung-lab--cur" if cur else ""}">{esc(lab)}</text>')
        out.append(f'<text x="160" y="{y + 18}" class="fx-rung-date">{esc(when(r["first_date"]))}'
                   + (f' · {esc(short(r["value"]) if metric != "count" else num(r["value"]))}' if r.get("value") is not None else "") + '</text>')
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------ copy
def standfirst(b):
    m, w = b["milestone"], b["window"]
    k, v, subj = m["kind"], m["vertical"], m["subject"]
    since = when(w["tracked_since"])
    floor = money(w["floor"])
    vl = vlabel(v)
    d = when(m["first_date"], long=True)
    if k.startswith("price:") and b.get("sale"):
        s = b["sale"]
        what = {"price:vertical": f"{'a card' if v == 'All' else 'a ' + vl + ' card'}",
                "price:subject": f"a {subj} card", "price:card": f"a {subj}", "price:card_grade": f"a {subj}",
                "price:set": f"a card from {subj}", "price:year": f"a {subj} {vl} card",
                "price:year_rookie": f"a {subj} {vl} rookie card"}[k]
        fmt = f" in a {s['listing_format'].lower()}" if s.get("listing_format") else ""
        bids = f" after {int(s['bids'])} bids" if s.get("bids") else ""
        return (f"On {d}, {what} sold on eBay for {money(s['total_price'])}{fmt}{bids} — the first time RazMania has "
                f"tracked one clearing {money(m['threshold'])} since collection began on {since}.")
    if k in ("count:day", "count:week"):
        who = "card" if v == "All" else (vl if subj == v else subj)
        span = "day" if k.endswith("day") else "seven-day stretch"
        return (f"{'On' if k.endswith('day') else 'In the week ending'} {d}, {num(m['value'])} {who} sales over {money(m['floor'])} "
                f"closed on eBay — the first {span} past {num(m['threshold'])} that RazMania has tracked since {since}.")
    if k in ("gmv:day", "gmv:week"):
        who = "the hobby" if v == "All" else (vl if subj == v else subj)
        return (f"{'On' if k.endswith('day') else 'In the week ending'} {d}, confirmed {who} sales over {floor} totalled "
                f"{money(m['value'])} — the first {'day' if k.endswith('day') else 'week'} past {money(m['threshold'])} RazMania has tracked since {since}.")
    if k == "count:total":
        who = "card" if v == "All" else (vl if subj == v else subj)
        return (f"On {d}, RazMania recorded its {ordinal(m['threshold'])} confirmed {who} sale over {floor} since collection began on {since}"
                + (f": {b['sale']['title']} at {money(b['sale']['total_price'])}." if b.get("sale") else "."))
    if k == "gmv:total":
        who = "the hobby" if v == "All" else (vl if subj == v else subj)
        return (f"On {d}, confirmed {who} sales over {floor} tracked by RazMania since {since} passed {money(m['threshold'])} in total, "
                f"reaching {money(m['value'])}.")
    if k.startswith("index:"):
        return (f"On {d}, the {subj} reading of the RazMania Index settled at {float(m['value']):.2f}, its first close "
                f"{'above' if k.endswith('above') else 'below'} {num(m['threshold'])} since the index's base period. Base is 100.")
    return m["headline"]


def deck(b):
    """One paragraph under the headline that says precisely what is a first
    for what — this card, the player or character as a whole in tracking,
    never the hobby — and what the all-time table says about the same line."""
    m, w = b["milestone"], b["window"]
    k, v = m["kind"], m["vertical"]
    since = when(w["tracked_since"])
    T = money(m["threshold"])
    name = subject_name(m)
    card = (m.get("detail") or {}).get("card") or (b.get("sale") or {}).get("title")
    scope = f"in RazMania's record of confirmed eBay sales over {money(w['floor'])}, which begins {since}"
    out = []
    if k == "price:subject" and name:
        out.append(f"A first for this card, and the first {name} card of any kind past {T} {scope}.")
    elif k in ("price:card", "price:card_grade") and name:
        earlier = q("""SELECT first_date, detail->>'card' AS card FROM firsts_log
                       WHERE kind = 'price:subject' AND vertical = %s AND subject = %s AND threshold = %s""",
                    (v, name, m["threshold"]))
        if earlier and str(earlier[0]["first_date"]) < str(m["first_date"]):
            out.append(f"A first for this card only: {name} as a whole first cleared {T} {scope}, "
                       f"on {when(earlier[0]['first_date'])} with a {earlier[0]['card'] or 'different card'}.")
        else:
            out.append(f"A first for this card, and the first {name} card of any kind past {T} {scope}.")
    elif k == "price:vertical":
        out.append(f"The first {'card' if v == 'All' else vlabel(v) + ' card'} of any kind past {T} {scope}.")
    elif k in ("price:set", "price:year", "price:year_rookie"):
        out.append(f"The first card from {m['subject'] if k == 'price:set' else 'the ' + m['subject'] + ' releases'} past {T} {scope}.")
    else:
        out.append(f"A first {scope}.")
    out.append("Not a hobby-history claim.")
    hist = alltime_context(v, name, m["threshold"])
    if hist:
        out.append(hist)
    return " ".join(out)


# ------------------------------------------------------------------ html
CSS = """
.fx{--ink:#14110D;--ink2:#57514A;--ink3:#6E6862;--bg:#FBF9F5;--s1:#fff;--s2:#F2EDE4;--line:rgba(26,22,16,.11);--line2:rgba(26,22,16,.2);--gold:#9A6B00;--up:#0a7d33;--down:#b3261e;
 color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-variant-numeric:tabular-nums;line-height:1.5;max-width:1080px;margin:0 auto;padding:0 20px}
.fx *{box-sizing:border-box}.fx a{color:inherit}
.fx-eyebrow{display:block;font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--gold)}
.fx-kicker{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:baseline;margin:26px 0 8px}
.fx-kicker .fx-eyebrow{display:inline}
.fx-kicker span+span{color:var(--ink3);font-size:12px;letter-spacing:.08em;text-transform:uppercase}
.fx-h1{font-family:Georgia,"Times New Roman",serif;font-weight:700;font-size:clamp(34px,5vw,58px);line-height:1.02;letter-spacing:-.02em;margin:6px 0 16px;color:var(--ink)}
.fx-stand{font-size:clamp(17px,1.9vw,21px);line-height:1.45;color:var(--ink2);max-width:66ch;margin:0 0 14px}
.fx-deck{font-size:clamp(15px,1.5vw,17px);line-height:1.5;color:var(--ink);max-width:70ch;margin:0 0 12px;padding:10px 14px;border-left:3px solid var(--gold);background:var(--s2);border-radius:0 8px 8px 0}
.fx-pill{display:inline-block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink2);border:1px solid var(--line2);border-radius:999px;padding:2px 9px}
.fx-byline{font-size:13px;color:var(--ink3);margin:0 0 24px;display:flex;flex-wrap:wrap;gap:6px 16px}
.fx-hero{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:26px;align-items:stretch;margin:0 0 30px}
@media(max-width:780px){.fx-hero{grid-template-columns:1fr}}
.fx-img{background:var(--s2);border:1px solid var(--line);border-radius:14px;display:flex;align-items:center;justify-content:center;min-height:320px;overflow:hidden}
.fx-img img{max-width:100%;max-height:520px;object-fit:contain;display:block}
.fx-big{background:var(--ink);color:#f1ede6;border-radius:14px;padding:26px 28px;display:flex;flex-direction:column;justify-content:space-between;gap:16px}
.fx-big .fx-eyebrow{color:#F5C518}
.fx-big-val{font-size:clamp(46px,6.5vw,84px);font-weight:800;line-height:.95;letter-spacing:-.03em;color:#fff}
.fx-big-sub{font-size:14px;color:#c9c2b8;line-height:1.5}
.fx-big-sub strong{color:#fff}
.fx-facts{display:grid;grid-template-columns:repeat(2,1fr);gap:10px 18px;border-top:1px solid rgba(255,255,255,.14);padding-top:14px}
.fx-facts div{font-size:12px;color:#a8a199;text-transform:uppercase;letter-spacing:.08em}
.fx-facts div b{display:block;font-size:17px;color:#fff;letter-spacing:0;text-transform:none;font-weight:700;margin-top:2px}
.fx-cta{display:inline-block;margin-top:4px;font-weight:700;color:#F5C518;text-decoration:none;border-bottom:2px solid #F5C518}
.fx-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px;margin:0 0 30px}
.fx-sec{margin:0 0 30px;padding-top:22px;border-top:1px solid var(--line2)}
.fx-h2{font-family:Georgia,serif;font-size:clamp(22px,2.6vw,30px);line-height:1.15;margin:4px 0 6px;color:var(--ink)}
.fx-sub{margin:0 0 14px;font-size:13px;color:var(--ink3)}
.fx-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 16px}
.fx-tile{background:var(--s1);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.fx-tile span{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink3)}
.fx-tile b{display:block;font-size:24px;font-weight:800;letter-spacing:-.02em;margin-top:2px}
.fx-tile small{display:block;font-size:12px;color:var(--ink3)}
.fx-up{color:var(--up)}.fx-down{color:var(--down)}.fx-muted{color:var(--ink3);font-size:13px}
.fx-chart,.fx-ladder{display:block;width:100%;height:auto;margin:6px 0 10px}
.fx-grid-line,.fx-grid{stroke:var(--line);stroke-width:1}
.fx-axis{font-size:11px;fill:var(--ink3)}.fx-axis--gold{fill:var(--gold);font-weight:700}
.fx-level{stroke:var(--gold);stroke-width:1.2;stroke-dasharray:4 4}
.fx-area{fill:var(--gold);opacity:.08}.fx-line{fill:none;stroke:var(--gold);stroke-width:2.2;stroke-linejoin:round}
.fx-dot{fill:var(--ink)}.fx-mark{stroke:var(--ink);stroke-width:1;stroke-dasharray:2 3}
.fx-rung{fill:var(--s2)}.fx-rung--cur{fill:var(--gold)}
.fx-rung-lab{font-size:14px;font-weight:800;fill:var(--ink)}.fx-rung-lab--cur{fill:var(--gold)}
.fx-rung-date{font-size:12px;fill:var(--ink2)}
.fx-table{width:100%;border-collapse:collapse;font-size:14px}
.fx-table th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink3);border-bottom:2px solid var(--line2);padding:6px 8px}
.fx-table td{padding:8px;border-bottom:1px solid var(--line);vertical-align:middle}
.fx-table td.r,.fx-table th.r{text-align:right}
.fx-thumb{width:44px;height:44px;object-fit:cover;border-radius:6px;background:var(--s2);vertical-align:middle;margin-right:10px}
.fx-list{list-style:none;padding:0;margin:0}
.fx-list li{display:flex;gap:12px;align-items:baseline;padding:9px 0;border-bottom:1px solid var(--line);font-size:14px}
.fx-list li time{flex:0 0 92px;color:var(--ink3);font-size:12px}
.fx-list li a{text-decoration:none;font-weight:600}.fx-list li a:hover{text-decoration:underline}
.fx-list li em{font-style:normal;color:var(--ink3);font-size:12px;margin-left:auto;white-space:nowrap}
.fx-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
.fx-card{display:block;background:var(--s1);border:1px solid var(--line);border-radius:12px;padding:14px 16px;text-decoration:none}
.fx-card:hover{border-color:var(--line2)}
.fx-card .fx-eyebrow{margin-bottom:6px}
.fx-card h3{font-family:Georgia,serif;font-size:18px;line-height:1.25;margin:0 0 6px;color:var(--ink)}
.fx-card p{margin:0;font-size:13px;color:var(--ink3)}
.fx-method{background:var(--s2);border-left:3px solid var(--gold);border-radius:0 10px 10px 0;padding:14px 18px;font-size:14px;line-height:1.6;color:var(--ink2)}
.fx-today{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.fx-today .fx-card{display:grid;grid-template-columns:72px 1fr;gap:12px;align-items:start}
.fx-today .fx-card img{width:72px;height:72px;object-fit:cover;border-radius:8px;background:var(--s2)}
.fx-today .fx-card .fx-noimg{width:72px;height:72px;border-radius:8px;background:var(--ink);color:#F5C518;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:15px}
"""


def tile(label, value, sub=""):
    return f'<div class="fx-tile"><span>{esc(label)}</span><b>{value}</b>{f"<small>{esc(sub)}</small>" if sub else ""}</div>'


def pct(v, suffix=""):
    if v is None:
        return '<span class="fx-muted">—</span>'
    v = float(v)
    cls = "fx-up" if v > 0.05 else ("fx-down" if v < -0.05 else "fx-muted")
    arrow = "▲" if v > 0.05 else ("▼" if v < -0.05 else "▬")
    return f'<span class="{cls}">{arrow} {"+" if v > 0 else ""}{v:.1f}%{esc(suffix)}</span>'


def sale_row(s, show_date=True):
    img = f'<img class="fx-thumb" src="{esc(s["image_url"])}" alt="" loading="lazy">' if s.get("image_url") else ""
    return (f'<tr><td>{img}<a href="{esc(s["url"])}" rel="nofollow noopener" target="_blank">{esc(s["title"])}</a></td>'
            f'<td>{esc(s.get("grade_label") or "")}</td>'
            + (f'<td>{esc(when(s["sold_date"]))}</td>' if show_date else "")
            + f'<td class="r"><strong>{money(s["total_price"])}</strong></td></tr>')


def render_article(b, fragment=False):
    m, w = b["milestone"], b["window"]
    k, v = m["kind"], m["vertical"]
    sf = standfirst(b)
    parts = []
    parts.append(f'<div class="fx-kicker"><span class="fx-eyebrow">Firsts</span><span>{esc(vlabel(v) if v != "All" else "Across the hobby")}</span>'
                 f'<span>{esc(b["kind_label"])}</span><span class="fx-pill">Tracked since {esc(when(w["tracked_since"]))}</span></div>')
    parts.append(f'<h1 class="fx-h1">{esc(m["headline"])}</h1>')
    parts.append(f'<p class="fx-deck">{esc(deck(b))}</p>')
    parts.append(f'<p class="fx-stand">{esc(sf)}</p>')
    parts.append(f'<p class="fx-byline"><span>By the RazMania data desk</span>'
                 + (f'<span>Published {esc(when(m["published_on"], long=True))}</span>' if m["published_on"] else "")
                 + f'<span>Settled {esc(when(m["first_date"]))}</span><span>Tracked sales over {esc(money(w["floor"]))} since {esc(when(w["tracked_since"]))}</span></p>')

    # ---- hero
    s = b.get("sale")
    big_val = money(s["total_price"]) if (s and k.startswith("price:")) else (
        num(m["value"]) if k.startswith("count") else (f"{float(m['value']):.2f}" if k.startswith("index") else money(m["value"])))
    big_lab = {"price": "The sale", "count": "Sales counted", "gmv": "Confirmed volume", "index": "Index close"}[k.split(":")[0]]
    facts = []
    if s and k.startswith("price:"):
        facts += [("Sold", when(s["sold_date"])), ("Format", (s.get("listing_format") or "eBay") + (f" · {int(s['bids'])} bids" if s.get("bids") else "")),
                  ("Grade", s.get("grade_label") or "Raw"), ("Line crossed", money(m["threshold"]))]
        if b.get("sale_rank_vertical"):
            facts.append((f"Rank, all tracked {vlabel(v)}", f"#{b['sale_rank_vertical']:,}"))
        facts.append(("Rank, all tracked sales", f"#{b['sale_rank_all']:,}"))
    elif k.startswith("count") or k.startswith("gmv"):
        ds = b.get("day_stats") or {}
        facts += [("Line crossed", num(m["threshold"]) if k.startswith("count") else money(m["threshold"])),
                  ("Counting sales over", money(m["floor"] if k.startswith("count") and m["floor"] else w["floor"]))]
        if ds:
            facts += [("Volume", money(ds.get("gmv"))), ("Median sale", money(ds.get("median_price"))),
                      ("Auctions", f"{int(ds.get('auctions') or 0):,} of {int(ds.get('n') or 0):,}")]
        if s:
            facts.append((f"The {ordinal(m['threshold'])} sale", money(s["total_price"])))
    elif k.startswith("index"):
        facts += [("Level", num(m["threshold"])), ("Base", "100 at the base period"), ("Settled", when(m["first_date"]))]
    facts_html = "".join(f"<div>{esc(a)}<b>{esc(bv)}</b></div>" for a, bv in facts)
    cta = (f'<a class="fx-cta" href="{esc(s["url"])}" rel="nofollow noopener" target="_blank">See the listing on eBay →</a>'
           if s and s.get("url") else "")
    img_html = (f'<div class="fx-img"><img src="{esc(s["image_url"])}" alt="{esc(s["title"])}"></div>' if s and s.get("image_url") else "")
    if not img_html and k.startswith("index") and b.get("index_series"):
        img_html = '<div class="fx-img" style="padding:16px">' + svg_line(b["index_series"], "index_value", level=float(m["threshold"]),
                                                                          mark_x=m["first_date"], money_axis=False, label="Index series") + "</div>"
    if not img_html and b.get("vertical_daily"):
        img_html = '<div class="fx-img" style="padding:16px">' + svg_line(b["vertical_daily"], "gmv" if k.startswith("gmv") else "confirmed_sales",
                                                                          mark_x=m["first_date"], money_axis=k.startswith("gmv"),
                                                                          label="Daily series") + "</div>"
    parts.append(f'<div class="fx-hero">{img_html}<div class="fx-big"><div><span class="fx-eyebrow">{esc(big_lab)}</span>'
                 f'<div class="fx-big-val">{big_val}</div>'
                 + (f'<p class="fx-big-sub"><strong>{esc(s["title"])}</strong></p>' if s else "")
                 + f'</div><div class="fx-facts">{facts_html}</div>{cta}</div></div>')

    # ---- ladder
    if len(b["ladder"]) > 1:
        metric = "price" if k.startswith("price") or k.startswith("gmv") else ("count" if k.startswith("count") else "index")
        who = "this card" if k.startswith("price:card") else ("the hobby" if v == "All" else (vlabel(v) if m["subject"] == v else m["subject"]))
        parts.append(f'<section class="fx-sec"><h2 class="fx-h2">Every line {esc(who)} has crossed</h2>'
                     f'<p class="fx-sub">Each rung is the first time the line was cleared in tracked sales, with the date and the figure that did it.</p>'
                     + svg_ladder(b["ladder"], m["id"], metric) + "</section>")

    # ---- subject
    sub = b.get("subject")
    if sub:
        n = sub["name"]
        parts.append(f'<section class="fx-sec"><h2 class="fx-h2">{esc(n)} in tracked sales</h2>'
                     f'<p class="fx-sub">Confirmed eBay sales over {esc(money(w["floor"]))}, {esc(when(sub["first_seen"]))} to {esc(when(sub["last_sold"]))}. Best-offer listings excluded.</p>'
                     '<div class="fx-tiles">'
                     + tile("Sales tracked", num(sub["sales"])) + tile("Volume", money(sub["gmv"]))
                     + tile("Median sale", money(sub["median_price"]), f"typical range {money(sub['p25'])}–{money(sub['p75'])}")
                     + tile("Top sale", money(sub["top_sale"])) + "</div>")
        if b.get("subject_daily") and len(b["subject_daily"]) > 2:
            parts.append('<p class="fx-sub">Sales per day</p>' + svg_line(b["subject_daily"], "n", h=160, mark_x=m["first_date"], money_axis=False, label=f"{n} sales per day"))
        if b.get("subject_top"):
            parts.append('<h3 class="fx-sub" style="font-weight:700;margin-top:14px">Five biggest</h3><table class="fx-table"><thead><tr><th>Sale</th><th>Grade</th><th>Sold</th><th class="r">Price</th></tr></thead><tbody>'
                         + "".join(sale_row(x) for x in b["subject_top"]) + "</tbody></table>")
        if b.get("comps"):
            rows = "".join(f'<tr><td>{esc(" ".join(str(x) for x in (c["card_year"], c["brand_set"], c["card_number"] and "#" + c["card_number"], c["parallel"]) if x))}</td>'
                           f'<td>{esc(c["grade_label"])}</td><td class="r">{money(c["median_price"])}</td>'
                           f'<td class="r">{money(c["p25"])}–{money(c["p75"])}</td><td class="r">{int(c["sales"])}</td></tr>' for c in b["comps"])
            parts.append(f'<h3 class="fx-sub" style="font-weight:700;margin-top:16px">Comps with three or more sales</h3><table class="fx-table"><thead><tr><th>Card</th><th>Grade</th><th class="r">Median</th><th class="r">Typical range</th><th class="r">n</th></tr></thead><tbody>{rows}</tbody></table>')
        parts.append("</section>")

    # ---- day breakdown
    if b.get("day_top"):
        parts.append(f'<section class="fx-sec"><h2 class="fx-h2">What made the {"day" if k.endswith("day") else "week"}</h2>'
                     f'<p class="fx-sub">The biggest of the {esc(num((b.get("day_stats") or {}).get("n")))} confirmed sales counted.</p>'
                     '<table class="fx-table"><thead><tr><th>Sale</th><th>Grade</th><th>Sold</th><th class="r">Price</th></tr></thead><tbody>'
                     + "".join(sale_row(x) for x in b["day_top"]) + "</tbody></table></section>")

    # ---- the category
    vw = b.get("vertical_week")
    if b.get("vertical_daily"):
        title = "The hobby this week" if v == "All" else f"{vlabel(v)} this week"
        parts.append(f'<section class="fx-sec"><h2 class="fx-h2">{esc(title)}</h2>')
        if vw:
            parts.append('<div class="fx-tiles">' + tile("Sales, 7 days", num(vw["sales"]), "") + tile("Volume", money(vw["gmv"]))
                         + tile("Median", money(vw["median_price"])) + tile("Biggest sale", money(vw["top_sale"]))
                         + f'<div class="fx-tile"><span>Week over week</span><b style="font-size:18px">{pct(vw.get("gmv_pct_change"), " volume")}<br>{pct(vw.get("median_pct_change"), " median")}</b></div></div>')
        if b.get("index"):
            parts.append('<div class="fx-tiles">' + "".join(
                tile(("Blue Chip" if i["tier"] == "bluechip" else "Broad") + " index", f"{float(i['index_value']):.2f}",
                     f"settled {when(i['as_of'])}") for i in b["index"]) + "</div>")
        parts.append('<p class="fx-sub">Confirmed sales per day</p>' + svg_line(b["vertical_daily"], "confirmed_sales", h=180, mark_x=m["first_date"], money_axis=False, label="Confirmed sales per day"))
        if any(x.get("median_price") for x in b["vertical_daily"]):
            parts.append('<p class="fx-sub">Daily median sale</p>' + svg_line(b["vertical_daily"], "median_price", h=180, mark_x=m["first_date"], label="Daily median"))
        parts.append("</section>")

    # ---- related firsts
    rel = (b.get("subject_firsts") or []) + [x for x in (b.get("vertical_firsts") or []) if x["id"] not in {y["id"] for y in (b.get("subject_firsts") or [])}]
    if rel:
        items = "".join(f'<li><time>{esc(when(x["first_date"]))}</time><a href="{esc(article_url(x))}">{esc(x["headline"])}</a>'
                        f'<em>{esc(KIND_LABEL.get(x["kind"], ""))}{" · published" if x.get("published_on") else ""}</em></li>' for x in rel[:12])
        parts.append(f'<section class="fx-sec"><h2 class="fx-h2">More firsts nearby</h2><p class="fx-sub">Other lines crossed for the first time around this one.</p><ul class="fx-list">{items}</ul></section>')

    # ---- same day
    if b.get("same_day"):
        cards = "".join(f'<a class="fx-card" href="{esc(article_url(x))}"><span class="fx-eyebrow">{esc(vlabel(x["vertical"]))} · {esc(KIND_LABEL.get(x["kind"], ""))}</span>'
                        f'<h3>{esc(x["headline"])}</h3><p>Settled {esc(when(x["first_date"]))}</p></a>' for x in b["same_day"])
        parts.append(f'<section class="fx-sec"><h2 class="fx-h2">Also published {esc(when(m["published_on"]))}</h2><div class="fx-cards">{cards}</div></section>')

    # ---- method
    parts.append(f'<section class="fx-sec"><div class="fx-method"><strong>How this was decided.</strong> RazMania tracks completed eBay card sales over {esc(money(w["floor"]))}, '
                 f'collected daily since {esc(when(w["tracked_since"]))}. Listings closed by Best Offer are excluded, because eBay shows the seller\'s asking price on those, not the sale. '
                 f'A first is recorded only once its day has settled ({esc(when(w["settled_hot"]))} for sales over $10,000, {esc(when(w["settled_all"]))} for anything that counts cheaper sales), and it is never revised. '
                 f'"First" here means first in RazMania\'s tracking, not first in history: a card printed this year may have sold before collection began. '
                 f'The hobby\'s all-time firsts are a separate, hand-researched table on the series page. <a href="{esc(SITE)}">About the series →</a></div></section>')

    body = f'<article class="fx fx-article">{"".join(parts)}</article>'
    meta = {
        "id": m["id"], "title": m["headline"], "description": sf, "url": b["url"], "published_on": m["published_on"],
        "first_date": m["first_date"], "vertical": v, "kind": k, "image": (s or {}).get("image_url") or m.get("image_url"),
        "jsonld": jsonld(b, sf),
    }
    if fragment:
        return meta, body
    return page(m["headline"], sf, body, meta)


def jsonld(b, sf):
    m = b["milestone"]
    pub = m["published_on"] or m["first_date"]
    ts = f"{pub.isoformat()}T09:00:00Z"
    img = (b.get("sale") or {}).get("image_url") or m.get("image_url")
    art = {"@context": "https://schema.org", "@type": "NewsArticle",
           "headline": m["headline"][:110], "description": sf, "url": b["url"], "mainEntityOfPage": b["url"],
           "datePublished": ts, "dateModified": ts, "articleSection": vlabel(m["vertical"]),
           "keywords": ", ".join(x for x in ["trading cards", vlabel(m["vertical"]), m["subject"], "card sales", "RazMania Firsts"] if x),
           "isAccessibleForFree": True,
           "author": {"@type": "Organization", "name": "RazMania", "url": HOME},
           "publisher": {"@type": "Organization", "name": "RazMania", "url": HOME,
                         **({"logo": {"@type": "ImageObject", "url": LOGO}} if LOGO else {})}}
    if img:
        art["image"] = [img]
    crumbs = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "RazMania", "item": HOME + "/"},
        {"@type": "ListItem", "position": 2, "name": "Firsts", "item": SITE},
        {"@type": "ListItem", "position": 3, "name": m["headline"], "item": b["url"]}]}
    return [art, crumbs]


def page(title, desc, body, meta=None):
    ld = "".join(f'<script type="application/ld+json">{json.dumps(_jsonable(x), ensure_ascii=False)}</script>' for x in (meta or {}).get("jsonld", []))
    og = ""
    if meta:
        og = (f'<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}">'
              f'<meta property="og:type" content="article"><meta property="og:url" content="{esc(meta["url"])}">'
              + (f'<meta property="og:image" content="{esc(meta["image"])}">' if meta.get("image") else "")
              + '<meta name="twitter:card" content="summary_large_image">')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)} — RazMania Firsts</title><meta name="description" content="{esc(desc)}">{og}{ld}'
            f'<style>body{{margin:0;background:#FBF9F5}}{CSS}</style></head><body>{body}</body></html>')


def render_today(days_back=1):
    f = feed(days_back)
    if not f:
        return '<div class="fx"><p class="fx-muted">Firsts publishes after the next refresh.</p></div>'
    day = f[0]
    cards = []
    for x in day["items"]:
        img = (f'<img src="{esc(x["image_url"])}" alt="" loading="lazy">' if x.get("image_url")
               else f'<div class="fx-noimg">{esc(short(x["threshold"]) if x["kind"].startswith(("price", "gmv")) else num(x["threshold"]))}</div>')
        cards.append(f'<a class="fx-card" href="{esc(x["url"])}">{img}<div><span class="fx-eyebrow">{esc(vlabel(x["vertical"]) if x["vertical"] != "All" else "The hobby")} · {esc(KIND_LABEL.get(x["kind"], ""))}</span>'
                     f'<h3>{esc(x["headline"])}</h3><p>Settled {esc(when(x["first_date"]))}</p></div></a>')
    return (f'<section class="fx fx-today-wrap"><div class="fx-kicker"><span class="fx-eyebrow">Firsts · {esc(when(day["published_on"], long=True))}</span>'
            f'<span><a href="{esc(SITE)}" style="text-decoration:none">The series →</a></span></div><div class="fx-today">{"".join(cards)}</div></section>')


# ------------------------------------------------------------------ routes
@router.get("/v1/firsts")
def v1_feed(days: int = Query(14, ge=1, le=365)):
    """The published feed: the day's 1–3, newest day first, each with its article URL."""
    return {**window(), "days": feed(days)}


@router.get("/v1/firsts/log")
def v1_log(vertical: Optional[str] = None, kind: Optional[str] = None, subject: Optional[str] = None,
           min_score: float = Query(0), days: Optional[int] = Query(None, ge=1, le=400),
           published: Optional[bool] = None,
           limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    """Every recorded first. `kind` may be a comma list. `days` limits by first_date."""
    sql = "SELECT * FROM firsts_log WHERE score >= %s"
    p = [min_score]
    if vertical:
        sql += " AND vertical = %s"
        p.append(vertical)
    if kind:
        ks = [k.strip() for k in kind.split(",") if k.strip()]
        sql += " AND kind = ANY(%s)"
        p.append(ks)
    if subject:
        sql += " AND subject ILIKE %s"
        p.append(f"%{subject}%")
    if days:
        sql += " AND first_date > (SELECT max(first_date) FROM firsts_log) - %s"
        p.append(days)
    if published is not None:
        sql += " AND published_on IS " + ("NOT NULL" if published else "NULL")
    sql += " ORDER BY first_date DESC, score DESC, id LIMIT %s OFFSET %s"
    p += [limit, offset]
    rows = q(sql, tuple(p))
    for r in rows:
        r["url"] = article_url(r)
    return {"limit": limit, "offset": offset, "count": len(rows), "firsts": rows}


@router.get("/v1/firsts/{mid}")
def v1_one(mid: int):
    return _jsonable(bundle(mid))


@router.get("/firsts/a/{mid}", response_class=HTMLResponse)
def article(mid: int, fragment: bool = False):
    b = bundle(mid)
    if fragment:
        meta, body = render_article(b, fragment=True)
        return JSONResponse({"meta": _jsonable(meta), "css": CSS, "html": body})
    return HTMLResponse(render_article(b))


@router.get("/firsts/a/{mid}-{slug}", response_class=HTMLResponse)
def article_slug(mid: int, slug: str, fragment: bool = False):
    return article(mid, fragment)


@router.get("/firsts/today", response_class=HTMLResponse)
def today(full: bool = False, fragment: bool = False):
    body = render_today()
    if fragment:
        return JSONResponse({"css": CSS, "html": body})
    if full:
        return HTMLResponse(page("Today's firsts", "The card-market milestones RazMania tracked for the first time.", body))
    return HTMLResponse(body)


@router.get("/firsts/", response_class=HTMLResponse)
def index_page(days: int = Query(30, ge=1, le=365)):
    f = feed(days)
    secs = []
    for d in f:
        cards = "".join(f'<a class="fx-card" href="{esc(x["url"])}"><span class="fx-eyebrow">{esc(vlabel(x["vertical"]) if x["vertical"] != "All" else "The hobby")} · {esc(KIND_LABEL.get(x["kind"], ""))}</span>'
                        f'<h3>{esc(x["headline"])}</h3><p>Settled {esc(when(x["first_date"]))} · score {float(x["score"]):.1f}</p></a>' for x in d["items"])
        secs.append(f'<section class="fx-sec"><h2 class="fx-h2">{esc(when(d["published_on"], long=True))}</h2><div class="fx-cards">{cards}</div></section>')
    body = f'<div class="fx"><div class="fx-kicker"><span class="fx-eyebrow">A RazMania series</span></div><h1 class="fx-h1">Firsts</h1><p class="fx-stand">One to three milestones a day, the first time the warehouse sees them.</p>{"".join(secs)}</div>'
    return HTMLResponse(page("Firsts", "One to three card-market milestones a day.", body))
