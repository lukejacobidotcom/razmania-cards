#!/usr/bin/env python3
"""
Build exhibitors/profiles.json - the canonical exhibitor profile data - and
copy it into the WordPress plugin so razmania.com/exhibitors/ serves it.

    python -X utf8 exhibitors/build_profiles.py            # fetch live, write both files
    python -X utf8 exhibitors/build_profiles.py --offline  # rebuild from the last fetch

Three sources, one file
-----------------------
1. razmaniasports.com (Swoogo). The profiles were written into snippet 21011
   (widget 113500296 on every /2026/sponsor/<id>/<slug> page): REG (chips,
   Instagram, summary, shop link), TAG (tagline), ED (editorial), TABLES and
   RZ_DAYS, plus hand-written HTML sections for a few exhibitors. The
   directory at /2026/exhibitors carries `sponsorsObj` (name, logo, category)
   and the enhancer's MAP (name -> [id, slug]), which is where the public
   slugs come from. All of it rendered client-side; Google never saw a word.

2. The Swoogo API (sponsorship level, and a name for registry entries that
   never made the directory), when SWOOGO_KEY/SECRET are in .env.

3. razmania.com's earlier exhibitor system: data/legacy-razmania.json is the
   `data/exhibitors.json` that the live RazMania Cards plugin (v1.12, not in
   this repo) served for 54 exhibitors from 18 Aug 2026. It carries things
   Swoogo never had - Instagram follower and post counts, an "Our take"
   editorial, "at the table" facts - and 16 slugs that exhibitors were
   emailed. Those are merged in, and the slugs that changed become redirects.

exhibitors/profiles.overrides.json applies last. From here on profiles.json
is the source of truth: edit the overrides, re-run, deploy. Nothing here
writes to Swoogo or to the site.
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, "data", "swoogo-cache")
LEGACY = os.path.join(HERE, "data", "legacy-razmania.json")
OUT = os.path.join(HERE, "profiles.json")
PLUGIN_OUT = os.path.join(ROOT, "wordpress", "razmania-exhibitors", "data", "profiles.json")
OVERRIDES = os.path.join(HERE, "profiles.overrides.json")
IG_PROFILES = os.path.join(HERE, "data", "ig_profiles.json")
RANK_OUT = os.path.join(HERE, "rankings.json")
RANK_PLUGIN_OUT = os.path.join(ROOT, "wordpress", "razmania-exhibitors", "data", "rankings.json")

DIRECTORY_URL = "https://www.razmaniasports.com/2026/exhibitors"
SPONSOR_URL = "https://www.razmaniasports.com/2026/sponsor/{id}/{slug}"
SWOOGO_EVENT = 370376          # attendee event; the sponsor records live here

UA = {"User-Agent": "Mozilla/5.0 (razmania-cards build_profiles)"}
VERIFIED = {"key": "verified", "label": "RazMania Verified Exhibitor", "year": 2026}


# ----------------------------------------------------------------- fetching

def fetch(url, name, offline):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if offline:
        with open(path, encoding="utf-8") as f:
            return f.read()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8", "replace")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return body


def js_object(src, name):
    """Pull `var NAME={...};` out of the snippet. The objects are JSON-valid
    (they were generated), so a non-greedy match up to `};` is enough as long
    as the object holds no `};` inside a string - none do today. Parsed with
    json so a broken blob fails loudly instead of yielding half a registry."""
    m = re.search(r"\bvar\s+" + name + r"\s*=\s*(\{.*?\});", src, re.S)
    if not m:
        raise SystemExit(f"could not find `var {name}=` in the snippet")
    return json.loads(m.group(1))


def load_directory(offline):
    h = fetch(DIRECTORY_URL, "directory.html", offline)
    m = re.search(r"sponsorsObj\s*=\s*(\{.*?\});", h, re.S)
    if not m:
        raise SystemExit("sponsorsObj not found on the directory page")
    sponsors = json.loads(m.group(1))
    m = re.search(r"var\s+MAP\s*=\s*(\{.*?\});", h, re.S)
    slug_map = json.loads(m.group(1)) if m else {}
    return sponsors, slug_map


def load_snippet(offline, sid, slug):
    h = fetch(SPONSOR_URL.format(id=sid, slug=slug), "sponsor.html", offline)
    css = re.search(r'<style id="snippet-css-\d+">(.*?)</style>', h, re.S)
    js = re.search(r'<script id="snippet-js-\d+">(.*?)</script>', h, re.S)
    if not css or not js:
        raise SystemExit("profile snippet not found on the sponsor page")
    tpl = h[css.end():js.start()]
    return js.group(1), tpl


def hand_written_sections(tpl):
    """The template carries every hand-written block for every exhibitor,
    tagged data-only="<id>"; the runtime deletes the ones that do not match.
    Collect them per id, outer HTML intact, in page order."""
    out = {}
    depth_re = re.compile(r"<(/?)(section|aside|div)\b", re.I)
    for m in re.finditer(r'<(section|aside)\b[^>]*\bdata-only="(\d+)"[^>]*>', tpl):
        sid, start = m.group(2), m.start()
        depth, pos = 0, start
        for t in depth_re.finditer(tpl, start):
            depth += -1 if t.group(1) else 1
            if depth == 0:
                pos = tpl.find(">", t.start()) + 1
                break
        out.setdefault(sid, []).append(tpl[start:pos])
    return out


def swoogo_sponsors(offline):
    """Level, name, logo per sponsor from the Swoogo API, when credentials are
    present. Optional: the profile does not depend on it. Cached alongside the
    pages so --offline rebuilds keep it."""
    cache = os.path.join(CACHE, "sponsors.json")
    if offline:
        return json.load(open(cache, encoding="utf-8")) if os.path.exists(cache) else {}
    env = os.path.join(ROOT, ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line.startswith("SWOOGO_") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    if not os.environ.get("SWOOGO_KEY") or not os.environ.get("SWOOGO_SECRET"):
        return {}
    sys.path.insert(0, os.path.join(ROOT, "alerts"))
    try:
        from swoogo import Swoogo
        out = {}
        for s in Swoogo().paged("sponsors.json", event_id=SWOOGO_EVENT,
                                fields="id,name,level,website,description,logo_id"):
            lvl = s.get("level")
            logo = s.get("logo_id") or ""
            out[str(s["id"])] = {
                "name": s.get("name") or "",
                "level": (lvl or {}).get("value") if isinstance(lvl, dict) else (lvl or ""),
                "logo": ("https:" + logo) if logo.startswith("//") else logo,
                "swoogo_description": s.get("description") or "",
            }
        os.makedirs(CACHE, exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        return out
    except Exception as e:                      # noqa: BLE001 - optional enrichment
        print(f"  (skipping Swoogo API: {e})", file=sys.stderr)
        return {}


# ------------------------------------------------------------------- shaping

def slugify(name):
    s = html.unescape(name).lower()
    s = re.sub(r"['’]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "exhibitor"


def namekey(s):
    return re.sub(r"[^a-z0-9]", "", html.unescape(s or "").lower())


def split_chip(c):
    """'📍 Columbus, Ohio' -> ('📍', 'Columbus, Ohio'). A chip with no leading
    emoji keeps an empty icon rather than losing its first word."""
    c = c.strip()
    m = re.match(r"^(\W+)\s+(.*)$", c)
    if m and not m.group(1).isalnum():
        return m.group(1), m.group(2)
    return "", c


CATEGORY_HINTS = (
    # chip label fragment (lower-case) -> Swoogo directory category
    (("pokémon", "pokemon", "one piece", "tcg", "riftbound", "lorcana", "magic"), "Pokemon / TCG"),
    (("sports card", "football", "basketball", "baseball", "hockey", "soccer", "rookie", "michigan teams"), "Sports Trading Cards"),
    (("memorabilia", "signings", "autos"), "Memorabilia"),
    (("merch",), "Merchandise"),
)
DEALER_HINTS = ("buying", "trades", "slabs", "graded", "grails", "breaks", "storefront",
                "vintage", "singles", "premium cards", "market data", "card vending")


def derive_categories(cats, specialties):
    """Swoogo left 23 exhibitors with no directory category at all, which
    would drop them out of every filter on the razmania.com directory. Infer
    from their own chips, and only when Swoogo gave nothing - a set category
    is never second-guessed."""
    if cats:
        return cats
    out = []
    labels = [s.lower() for s in specialties]
    for needles, cat in CATEGORY_HINTS:
        if any(n in l for l in labels for n in needles) and cat not in out:
            out.append(cat)
    if out or any(n in l for l in labels for n in DEALER_HINTS):
        out.insert(0, "Dealers")
    return out


# The legacy file used its own tag vocabulary; fold it onto the directory's.
LEGACY_TAGS = {
    "Sports cards": "Sports Trading Cards",
    "Pokémon & TCG": "Pokemon / TCG",
    "Michigan teams": "Sports Trading Cards",
    "Live breaks": "Dealers",
    "Graded": "Dealers",
    "Vintage": "Dealers",
    "Media": "Media",
    "Food & drink": "Food & drink",
}


def format_tables(tables):
    nums = sorted(t for t in tables if isinstance(t, int))
    alpha = [t for t in tables if not isinstance(t, int)]
    runs = []
    for n in nums:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    parts = [str(a) if a == b else f"{a}–{b}" for a, b in runs] + [str(a) for a in alpha]
    if not parts:
        return ""
    return ("Tables " if len(tables) > 1 else "Table ") + ", ".join(parts)


def make_row(sid, name, logo, cats, slug, reg, tag, ed, tables, days, sections, level):
    chips = [dict(zip(("icon", "label"), split_chip(c))) for c in reg.get("c", [])]
    location = next((c["label"] for c in chips if c["icon"] == "\U0001F4CD"), "")
    specialties = [c["label"] for c in chips if c["icon"] != "\U0001F4CD"]
    return {
        "id": sid,
        "slug": slug,
        "name": name,
        "logo": logo,
        "categories": derive_categories(cats, specialties),
        "level": level,
        "tagline": tag,
        "summary": reg.get("s", ""),
        "editorial": ed,
        "chips": chips,
        "location": location,
        "specialties": specialties,
        "instagram": reg.get("ig", ""),
        "website": reg.get("u", ""),
        "tables": tables,
        "table_label": format_tables(tables),
        "days": days,
        "profile": "full" if (reg or ed) else "basic",
        "accolades": [dict(VERIFIED)],
        "sections_html": sections,
        "swoogo_url": SPONSOR_URL.format(id=sid, slug=slug) if sid.isdigit() else "",
        # filled from the legacy razmania.com data when there is a match
        "legacy_slug": "",
        "ig_followers": 0,
        "ig_posts": 0,
        "take": [],
        "take_date": "",
        "facts": [],
    }


def legacy_row(o, slug):
    """An exhibitor that existed only on razmania.com's earlier system (they
    registered on the exhibitor event but never had a Swoogo sponsor record).
    Their URL was emailed to them, so the page stays, built from what the old
    file had."""
    ig = o.get("ig") or {}
    cats = []
    for t in o.get("tags", []):
        c = LEGACY_TAGS.get(t, t)
        if c not in cats:
            cats.append(c)
    if cats and "Dealers" not in cats and any(c in ("Sports Trading Cards", "Pokemon / TCG", "Memorabilia") for c in cats):
        cats.insert(0, "Dealers")
    row = make_row("legacy:" + slug, o["name"], o.get("logo") or "", cats, slug,
                   {"ig": o.get("instagram", ""), "u": ig.get("link", ""), "s": ig.get("bio", ""), "c": []},
                   "", "", [], "Both days · Aug 29–30", [], "")
    row["profile"] = "full" if o.get("take") else "basic"
    apply_legacy(row, o)
    return row


def apply_legacy(row, o):
    ig = o.get("ig") or {}
    if o["slug"] != row["slug"]:
        row["legacy_slug"] = o["slug"]
    row["ig_followers"] = int(ig.get("followers") or 0)
    row["ig_posts"] = int(ig.get("posts") or 0)
    take = o.get("take") or {}
    row["take"] = list(take.get("paras") or [])
    row["take_date"] = take.get("date", "")
    row["facts"] = [{"k": f["k"], "v": f["v"]} for f in (o.get("facts") or []) if f.get("on", 1)]
    if not row["summary"] and ig.get("bio"):
        row["summary"] = ig["bio"]
    if not row["instagram"] and o.get("instagram"):
        row["instagram"] = o["instagram"]
    if not row["website"] and ig.get("link"):
        row["website"] = ig["link"]
    if not row["logo"] and o.get("logo"):
        row["logo"] = o["logo"]
    if row["profile"] == "basic" and row["take"]:
        row["profile"] = "full"


def enrich_followers(rows):
    """Instagram audience from every source we hold: the August 2026 Apify
    scrape (data/ig_profiles.json, 45 accounts), the legacy razmania.com file,
    and a count quoted in the exhibitor's own summary ("28,000 following").
    The largest wins; the source is recorded so the page can cite it."""
    scraped = {}
    if os.path.exists(IG_PROFILES):
        for x in json.load(open(IG_PROFILES, encoding="utf-8")):
            if x.get("username"):
                scraped[x["username"].lower()] = x
    for r in rows:
        cands = [(r["ig_followers"], "razmania.com, Aug 2026")]
        x = scraped.get(r["instagram"].lower()) if r["instagram"] else None
        if x and x.get("followersCount"):
            cands.append((int(x["followersCount"]), "Instagram, Aug 2026"))
            r["ig_posts"] = r["ig_posts"] or int(x.get("postsCount") or 0)
        m = re.search(r"([\d,]{4,})\s*(?:following|followers)", r["summary"] + " " + r["editorial"])
        if m:
            cands.append((int(m.group(1).replace(",", "")), "exhibitor's own profile, Aug 2026"))
        n, src = max(cands, key=lambda c: c[0])
        r["ig_followers"], r["followers_source"] = n, (src if n else "")


def follower_accolades(rows):
    """The first data-driven accolade (see RANKINGS.md): audience size, from
    the Instagram counts the legacy file snapshotted in August 2026. Only
    exhibitors with a count can hold one; the rest are simply unranked."""
    with_counts = [r for r in rows if r["ig_followers"] > 0]
    if not with_counts:
        return
    # No "biggest audience" award: only 20 of 150 tables have a count, so a
    # superlative would be false (Atomic TCG's 28,000 is not in the file).
    # A per-exhibitor threshold is true for each one on its own.
    for r in with_counts:
        n = r["ig_followers"]
        if n >= 2000:
            r["accolades"].append({"key": "audience", "label": f"Over {n // 1000}k followers",
                                   "detail": "Instagram, Aug 2026", "year": 2026})


def build(offline):
    sponsors, slug_map = load_directory(offline)
    by_id = {v["id"]: v for v in sponsors.values()}
    slug_of = {sid: slug for _, (sid, slug) in slug_map.items()}

    # Any sponsor page carries the whole snippet; use the first mapped one.
    first_id, first_slug = next(iter(slug_map.values()), ("1108495", "614-rips"))
    js, tpl = load_snippet(offline, first_id, first_slug)
    REG, TAG, ED = js_object(js, "REG"), js_object(js, "TAG"), js_object(js, "ED")
    TABLES, DAYS = js_object(js, "TABLES"), js_object(js, "RZ_DAYS")
    sections = hand_written_sections(tpl)

    ov = json.load(open(OVERRIDES, encoding="utf-8")) if os.path.exists(OVERRIDES) else {}
    exclude = set(ov.get("exclude", {}).keys())
    api = swoogo_sponsors(offline)
    legacy = json.load(open(LEGACY, encoding="utf-8")) if os.path.exists(LEGACY) else []

    # Directory entries, plus registry entries the directory never listed but
    # the API can still name (they were dropped from Swoogo's sponsor widget,
    # not from the show).
    ids = list(by_id)
    for sid in REG:
        if sid not in by_id and sid not in exclude and api.get(sid, {}).get("name"):
            ids.append(sid)

    used, rows = set(), []
    for sid in ids:
        if sid in exclude:
            continue
        s = by_id.get(sid, {})
        name = ov.get("rename", {}).get(sid) or html.unescape(s.get("name") or api[sid]["name"]).strip()
        slug = slug_of.get(sid) or slugify(name)
        base, n = slug, 2
        while slug in used:
            slug = f"{base}-{n}"
            n += 1
        used.add(slug)
        logo = s.get("logo") or api.get(sid, {}).get("logo") or ""
        if logo.startswith("//"):
            logo = "https:" + logo
        cats = [c.strip() for c in (s.get("category") or "").split(",") if c.strip()]
        rows.append(make_row(sid, name, logo, cats, slug, REG.get(sid, {}), TAG.get(sid, ""),
                             ED.get(sid, ""), TABLES.get(sid, []), DAYS.get(sid, "Both days · Aug 29–30"),
                             sections.get(sid, []), api.get(sid, {}).get("level") or ""))

    # Legacy razmania.com data: match by slug, then by the hand map, then by name.
    by_slug = {r["slug"]: r for r in rows}
    by_name = {namekey(r["name"]): r for r in rows}
    hand = ov.get("legacy_redirects", {})
    redirects = {}
    for o in legacy:
        r = by_slug.get(o["slug"]) or by_slug.get(hand.get(o["slug"], "")) or by_name.get(namekey(o["name"]))
        if r is None:
            r = legacy_row(o, o["slug"])
            rows.append(r)
            by_slug[r["slug"]] = r
            continue
        apply_legacy(r, o)
        if o["slug"] != r["slug"]:
            redirects[o["slug"]] = r["slug"]

    enrich_followers(rows)
    follower_accolades(rows)
    from rankings import compute as compute_rankings
    rankings = compute_rankings(rows)
    from assets import build_logos, build_share_cards
    n_logo = build_logos(rows, offline)
    n_link, n_story = build_share_cards(rows)
    print(f"  assets: {n_logo} local logos, {n_link} link cards, {n_story} story cards")

    # Hand corrections win over everything, field by field.
    for r in rows:
        for k, v in ov.get("exhibitors", {}).get(r["id"], {}).items():
            if not k.startswith("_"):
                r[k] = v
        for a in ov.get("accolades", {}).get(r["id"], []):
            r["accolades"].append(a)

    rows.sort(key=lambda r: r["name"].lower())
    doc = {
        "meta": {
            "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "source": [DIRECTORY_URL, SPONSOR_URL.format(id=first_id, slug=first_slug),
                       "razmania.com razmania-cards v1.12 data/exhibitors.json (18 Aug 2026)"],
            "event": {"name": "RazMania 2026", "dates": "August 29–30, 2026",
                      "venue": "UWM Sports Complex", "address": "867 S Blvd E, Pontiac, MI 48341"},
            "counts": {"exhibitors": len(rows),
                       "full_profiles": sum(r["profile"] == "full" for r in rows),
                       "basic_listings": sum(r["profile"] == "basic" for r in rows),
                       "hand_written_sections": sum(len(r["sections_html"]) for r in rows),
                       "legacy_only": sum(r["id"].startswith("legacy:") for r in rows),
                       "with_followers": sum(r["ig_followers"] > 0 for r in rows)},
            "redirects": redirects,
        },
        "exhibitors": rows,
    }
    return doc, rankings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="rebuild from exhibitors/data/swoogo-cache")
    a = ap.parse_args()
    doc, rankings = build(a.offline)
    for path in (RANK_OUT, RANK_PLUGIN_OUT):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rankings, f, ensure_ascii=False, indent=1)
            f.write("\n")
    for path in (OUT, PLUGIN_OUT):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.write("\n")
    c = doc["meta"]["counts"]
    print(f"wrote {OUT}\n  and {PLUGIN_OUT}")
    print(f"  {c['exhibitors']} exhibitors: {c['full_profiles']} full profiles, {c['basic_listings']} basic listings, "
          f"{c['hand_written_sections']} hand-written sections, {c['legacy_only']} legacy-only, "
          f"{c['with_followers']} with follower counts")
    print("  lists:", "; ".join(f"{l['title']} ({l['field']} in field)" for l in rankings["lists"]))
    if doc["meta"]["redirects"]:
        print("  redirects:", ", ".join(f"{a} -> {b}" for a, b in doc["meta"]["redirects"].items()))


if __name__ == "__main__":
    main()
