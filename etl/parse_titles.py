"""
RazMania sports-card comps pipeline.

Turns raw eBay sold-listing titles into a structured card database.
The eBay title is unstructured text; this module is where the value is added.

Input : newline-delimited JSON rows from the memo23/ebay-search-scraper-ppe Actor
Output: cards_listings.csv (one row per sale, parsed)
        cards_comps.csv    (one row per card+grade, aggregated)
"""

import json
import re
import unicodedata
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------
# Reference vocabularies
# --------------------------------------------------------------------------

# Order matters: longest / most specific brand strings first so that
# "Bowman Chrome Draft" wins over "Bowman", and "Upper Deck SP" over "Upper Deck".
BRANDS = [
    "Bowman Chrome Draft", "Bowman Draft Chrome", "Bowman's Best", "Bowman Sterling",
    "Bowman Chrome", "Bowman Draft", "Bowman Platinum", "Bowman Heritage", "Bowman",
    "Topps Chrome Update", "Topps Chrome", "Topps Finest", "Topps Heritage",
    "Topps Stadium Club", "Topps Update", "Topps Traded", "Topps Tribute",
    "Topps Allen & Ginter", "Topps Gypsy Queen", "Topps Gallery", "Topps Now",
    "Topps Big League", "Topps Archives", "Topps Total", "Topps Fire", "Topps",
    "Upper Deck SP Authentic", "Upper Deck SPx", "Upper Deck Black Diamond",
    "Upper Deck Ice", "Upper Deck Young Guns", "Upper Deck", "SP Authentic",
    "Panini Prizm Draft", "Panini Prizm", "Panini Select", "Panini Mosaic",
    "Panini Optic", "Panini Donruss Optic", "Panini Immaculate",
    "Panini National Treasures", "Panini Flawless", "Panini Contenders",
    "Panini Absolute", "Panini Certified", "Panini Chronicles", "Panini Obsidian",
    "Panini Origins", "Panini Phoenix", "Panini Revolution", "Panini Spectra",
    "Panini Illusions", "Panini Instant", "Panini One", "Panini",
    "Donruss Optic", "Donruss Elite", "Donruss The Rookies", "Donruss Rookies",
    "Donruss Rated Rookie", "Donruss",
    "Fleer Ultra", "Fleer Metal", "Fleer Flair", "Fleer Tradition", "Fleer",
    "Score Traded", "Score Select", "Score",
    "Playoff Contenders", "Playoff Prestige", "Playoff",
    "Leaf Metal", "Leaf Limited", "Leaf",
    "Stadium Club", "Skybox", "Hoops", "Pinnacle", "Pacific", "Classic",
    "Mother's Cookies", "Mothers Cookies", "Metal Universe", "Collector's Choice",
    "Sage", "Wild Card", "ProSet", "Pro Set", "O-Pee-Chee", "OPC",
    "Select", "Prizm", "Optic", "Mosaic", "Finest", "Chrome", "Heritage",
    "Contenders", "Prestige", "Elite", "Certified", "Absolute", "Immaculate",
    "Flawless", "Spectra", "Revolution", "Origins", "Obsidian", "Illusions",
    "Ultra", "Flair", "Star",
]

GRADERS = ["PSA", "BGS", "BCCG", "SGC", "CGC", "CSG", "HGA", "GMA", "FGS", "ISA", "TAG", "AGS"]

# Parallel / insert descriptors, longest first.
PARALLELS = [
    "Superfractor", "Gold Refractor", "Orange Refractor", "Red Refractor",
    "Blue Refractor", "Green Refractor", "Purple Refractor", "Black Refractor",
    "Pink Refractor", "Atomic Refractor", "X-Fractor", "Xfractor", "Refractor",
    "Silver Prizm", "Gold Prizm", "Green Prizm", "Blue Prizm", "Red Prizm",
    "Orange Prizm", "Purple Prizm", "Black Prizm", "Pink Prizm",
    "Silver Wave", "Hyper Prizm", "Mojo Prizm", "Prizm Silver",
    "Holo Foil", "Holofoil", "Holo", "Shimmer", "Sparkle", "Cracked Ice",
    "Downtown", "Kaboom", "Case Hit", "Short Print", "SSP", "SP",
    "1st Bowman", "1st Edition", "First Edition",
    # NOTE: "Rated Rookie" / "Star Rookie" / "Gem Mint" are deliberately NOT here.
    # They are rookie designations or condition words, not parallels — treating
    # them as parallels splits one card into several phantom comps.
    "Young Guns",
    "Die Cut", "Die-Cut", "Negative", "Disco", "Wave", "Ice", "Lazer", "Laser",
    "Gold", "Silver", "Bronze", "Platinum", "Sapphire", "Emerald", "Ruby",
]

CONDITION_WORDS = {
    "gem mint": "Gem Mint", "mint": "Mint", "nm-mt": "NM-MT", "nm mt": "NM-MT",
    "near mint": "Near Mint", "nm": "NM", "ex-mt": "EX-MT", "excellent": "EX",
    "very good": "VG", "good": "GD", "poor": "PR", "fair": "FR",
}

NOISE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️⬀-⯿]")


def clean_title(t: str) -> str:
    """Strip emoji and normalise whitespace/unicode so regexes behave."""
    t = unicodedata.normalize("NFKC", str(t))
    t = NOISE.sub(" ", t)
    t = t.replace("’", "'").replace("`", "'")
    return re.sub(r"\s+", " ", t).strip()


def parse_year(t: str):
    """First plausible card year in the title. Prefers a leading year."""
    lead = re.match(r"^\s*((?:19|20)\d{2})\b", t)
    if lead:
        return int(lead.group(1))
    hits = re.findall(r"\b((?:19|20)\d{2})\b", t)
    for h in hits:
        y = int(h)
        if 1900 <= y <= 2027:
            return y
    return None


def parse_brand(t: str):
    """Longest-match brand/set lookup, case-insensitive."""
    low = t.lower()
    for b in BRANDS:
        if b.lower() in low:
            return b
    return None


def parse_grade(t: str):
    """Return (grader, numeric grade, display label). Raw when no grader present."""
    for g in GRADERS:
        m = re.search(rf"\b{g}\s*\.?\s*(10|[1-9](?:\.5)?)\b", t, re.I)
        if m:
            return g.upper(), float(m.group(1)), f"{g.upper()} {m.group(1)}"
        # "PSA GEM MT 10" / "BGS GEM MINT 9.5" style
        m2 = re.search(rf"\b{g}\b[^0-9]{{0,18}}?(10|[1-9](?:\.5)?)\b", t, re.I)
        if m2:
            return g.upper(), float(m2.group(1)), f"{g.upper()} {m2.group(1)}"
    return None, None, "Raw"


def parse_card_number(t: str):
    """Card number after a #. Handles #1, #33, #100T, #CDA-MC, #BDC-76."""
    m = re.search(r"#\s*([A-Za-z]{0,5}-?[A-Za-z]{0,4}\d{1,4}[A-Za-z]{0,3})\b", t)
    if m:
        return m.group(1).upper().strip("-")
    m = re.search(r"#\s*([A-Za-z]{2,6}-[A-Za-z]{1,4})\b", t)
    if m:
        return m.group(1).upper()
    # All-alpha inserts such as #CDACC, #BDC, #RA
    m = re.search(r"#\s*([A-Za-z]{2,8})\b", t)
    return m.group(1).upper() if m else None


def parse_print_run(t: str):
    """Serial numbering like /499 or 30/71 -> 499 / 71."""
    m = re.search(r"(?<![\d.])/\s*(\d{1,5})\b", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b\d{1,5}\s*/\s*(\d{1,5})\b", t)
    if m:
        n = int(m.group(1))
        return n if n > 1 else None
    return None


def parse_parallel(t: str):
    low = t.lower()
    for p in PARALLELS:
        if p.lower() in low:
            return p
    return None


def has_auto(t: str) -> bool:
    return bool(re.search(r"\bauto(graph(ed)?)?\b|\bsigned\b|\bon[- ]card\b", t, re.I))


def has_patch(t: str) -> bool:
    return bool(re.search(r"\bpatch\b|\bjersey\b|\brelic\b|\bmem\b|\bswatch\b", t, re.I))


def is_rookie(t: str) -> bool:
    return bool(re.search(r"\brookie\b|\brc\b|\byg\b|\b1st bowman\b", t, re.I))


def is_lot(t: str) -> bool:
    """Multi-card lots destroy comps — they must be excluded from medians."""
    return bool(re.search(r"\blot\b|\bset of\b|\d+\s*card lot|\bbulk\b|\bcollection\b|"
                          r"\breprint\b|\bcustom\b|\bnovelty\b|\bfacsimile\b|\bcoin\b|"
                          r"\bposter\b|\bsticker\b", t, re.I))


def parse_condition(t: str):
    low = t.lower()
    for k, v in CONDITION_WORDS.items():
        if re.search(rf"\b{re.escape(k)}\b", low):
            return v
    return None


def name_tokens(name: str):
    """Tokens used to confirm the listing really is the player we searched for."""
    drop = {"jr", "sr", "ii", "iii", "the", "card", "rookie", "rc"}
    toks = re.findall(r"[a-z']+", name.lower())
    return [t for t in toks if t not in drop and len(t) > 1]


def player_matches(title: str, player: str) -> bool:
    """Require the surname (last meaningful token) to appear in the title."""
    toks = name_tokens(player)
    if not toks:
        return False
    return toks[-1] in title.lower()


def player_from_query(skw: str) -> str:
    """'ken griffey jr rc' -> 'Ken Griffey Jr'."""
    s = re.sub(r"\b(rookie|rc|card|cards)\b", "", str(skw), flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s.title()


SOLD_DATE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})")


def parse_sold_date(s):
    """'Sold  3 Aug 2026' -> Timestamp."""
    if not isinstance(s, str):
        return pd.NaT
    m = SOLD_DATE.search(s)
    if not m:
        return pd.NaT
    return pd.to_datetime(f"{m.group(1)} {m.group(2)} {m.group(3)}",
                          format="%d %b %Y", errors="coerce")


SHIP = re.compile(r"([\d,]+\.\d{2})")


def parse_shipping(s):
    if not isinstance(s, str):
        return 0.0
    if "free" in s.lower():
        return 0.0
    m = SHIP.search(s)
    return float(m.group(1).replace(",", "")) if m else 0.0


def parse_row(row: dict) -> dict:
    raw = row.get("title") or row.get("basic_info.title") or ""
    t = clean_title(raw)
    skw = row.get("basic_info.skw") or row.get("query") or ""
    player = player_from_query(skw)
    grader, grade, grade_label = parse_grade(t)
    price = row.get("priceValue")
    ship = parse_shipping(row.get("shipping"))
    try:
        price = float(price)
    except (TypeError, ValueError):
        price = None
    return {
        "item_id": row.get("itemId"),
        "epid": row.get("epid"),
        "title": t,
        "player": player,
        "player_ok": player_matches(t, player),
        "year": parse_year(t),
        "brand_set": parse_brand(t),
        "card_number": parse_card_number(t),
        "parallel": parse_parallel(t),
        "print_run": parse_print_run(t),
        "is_auto": has_auto(t),
        "is_patch": has_patch(t),
        "is_rookie": is_rookie(t),
        "is_lot": is_lot(t),
        "grader": grader,
        "grade": grade,
        "grade_label": grade_label,
        "raw_condition": parse_condition(t),
        "ebay_condition": row.get("condition"),
        "price": price,
        "shipping": ship,
        "total_price": None if price is None else round(price + ship, 2),
        "sold_date": parse_sold_date(row.get("soldDate")),
        "item_location": row.get("itemLocation"),
        "url": row.get("url"),
        "image": row.get("image"),
        "query": skw,
    }


def load_rows(paths):
    rows = []
    for p in paths:
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("_analytics") or obj.get("type") == "sold-price-summary":
                    continue
                rows.append(obj)
    return rows


def build(paths, outdir="out"):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    parsed = [parse_row(r) for r in load_rows(paths)]
    df = pd.DataFrame(parsed)

    # Deduplicate: the same listing is returned by both the "rookie" and "rc" query.
    df = df.drop_duplicates(subset=["item_id"])

    df.to_csv(outdir / "cards_listings_all.csv", index=False)

    # Comp-grade rows only: right player, single card, real price.
    ok = df[df.player_ok & ~df.is_lot & df.price.notna() & (df.price > 0)].copy()
    ok.to_csv(outdir / "cards_listings.csv", index=False)

    key = ["player", "year", "brand_set", "card_number", "parallel", "grade_label"]
    g = ok.groupby(key, dropna=False)["total_price"]
    comps = g.agg(
        sales="count", median_price="median", mean_price="mean",
        min_price="min", max_price="max",
        p25=lambda s: s.quantile(0.25), p75=lambda s: s.quantile(0.75),
    ).reset_index()
    comps["last_sold"] = ok.groupby(key, dropna=False)["sold_date"].max().values
    comps["is_rookie"] = ok.groupby(key, dropna=False)["is_rookie"].max().values
    comps["is_auto"] = ok.groupby(key, dropna=False)["is_auto"].max().values
    comps["sample_url"] = ok.groupby(key, dropna=False)["url"].first().values
    comps["sample_image"] = ok.groupby(key, dropna=False)["image"].first().values

    for c in ("median_price", "mean_price", "min_price", "max_price", "p25", "p75"):
        comps[c] = comps[c].round(2)

    comps = comps.sort_values(["player", "sales"], ascending=[True, False])
    comps.to_csv(outdir / "cards_comps.csv", index=False)

    # A comp with n < 3 is an anecdote, not a price. Publishable slice only.
    pub = comps[comps.sales >= 3].copy()
    pub.to_csv(outdir / "cards_comps_publishable.csv", index=False)

    return df, ok, comps, pub


if __name__ == "__main__":
    import sys
    files = sys.argv[1:] or sorted(Path("raw").glob("*.jsonl"))
    df, ok, comps, pub = build(files)
    print(f"raw rows parsed     : {len(df):,}")
    print(f"comp-grade rows     : {len(ok):,}")
    print(f"distinct card+grade : {len(comps):,}")
    print(f"publishable (n>=3)  : {len(pub):,}")
