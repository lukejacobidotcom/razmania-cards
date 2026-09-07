"""
The Best of RazMania: ten ranked lists, built from profiles.json.

Called by build_profiles.py after the profile rows are assembled. Writes
exhibitors/rankings.json (and the plugin copy) and appends the resulting
accolades to each exhibitor:

    rank 1-5   -> {"key": "best",      "label": "Best of RazMania 2026", ...}
    rank 6-10  -> {"key": "certified", "label": "RazMania Certified",    ...}

Every entry carries the list title, the rank and the field size, so the page
can say "#3 of 10" and the profile can print the banner without any lookup.

How a table is ranked
---------------------
Membership comes from the exhibitor's own chips and directory category -
nobody is placed in a list by hand. Within a list, tables are ordered by
Instagram audience (the largest count we hold from any source, snapshotted
August 2026), and where two tables have no count, by how complete their
profile is: editorial, tagline, links, table assignment. That second signal
is weak and is said so on the page; it exists so a list can reach ten.

Sponsors and partners are never ranked. A list with fewer than five members
is not published at all - a "top 10" of three is a joke, not a ranking.

One rule from the organiser: no exhibitor is #1 in more than one list. Lists
are filled smallest field first, so the narrow categories get first pick of
their natural leader; a table that already holds a #1 slides to #2 in the
next list it would have led.
"""

import re

YEAR = 2026
TOP = 10
BEST_CUTOFF = 5
MIN_FIELD = 5

MICHIGAN = re.compile(
    r"\bMI\b|Michigan|Detroit|Lansing|Ann Arbor|Pontiac|Lapeer|Shelby|Waterford|Mount Pleasant|"
    r"Lake Orion|Bloomfield|Grand Rapids|Flint|\bTroy\b|Warren|Sterling Heights|Livonia|Dearborn|"
    r"Royal Oak|Novi|Canton|Macomb|Rochester|Clinton Twp|Auburn Hills|Southfield|Kalamazoo|Saginaw",
    re.I)


def _chips(r):
    return " | ".join(s.lower() for s in r["specialties"])


def _text(r):
    return (r["summary"] + " " + r["editorial"] + " " + r["tagline"]).lower()


def is_sports(r):
    return "Sports Trading Cards" in r["categories"] or "sports card" in _chips(r)


def is_pokemon(r):
    return "pokémon" in _chips(r) or "pokemon" in _chips(r) or "Pokemon / TCG" in r["categories"]


def is_other_tcg(r):
    c = _chips(r)
    return any(k in c for k in ("one piece", "riftbound", "lorcana", "magic", "tcg")) and not (
        c.count("pokémon") + c.count("pokemon") and not any(k in c for k in ("one piece", "riftbound", "lorcana", "magic", "tcg")))


def is_buying(r):
    return "buying" in _chips(r) or "buy &" in _chips(r) or "buy ·" in _chips(r)


def is_trades(r):
    return "trade" in _chips(r)


def is_slabs(r):
    c = _chips(r)
    return any(k in c for k in ("slab", "graded", "psa", "premium cards", "ultra rare", "grails"))


def is_breaks(r):
    return any(k in _chips(r) for k in ("break", "rips"))


def is_memorabilia(r):
    return "Memorabilia" in r["categories"] or any(k in _chips(r) for k in ("memorabilia", "signings", "autos"))


def is_michigan(r):
    return bool(r["location"]) and bool(MICHIGAN.search(r["location"]))


def is_shop(r):
    c = _chips(r)
    return any(k in c for k in ("storefront", "shop", "store", "lcs")) or any(
        k in _text(r) for k in ("hobby shop", "card shop", "storefront", "brick", "local card shop", "inside "))


# (key, title, strapline, one-line "who is in it", matcher). Smallest fields
# are listed first on purpose - see the #1 rule in the module docstring.
CATEGORIES = [
    ("breaks",      "Break Kings",               "The tables that rip on camera.",
                    "Every exhibitor who runs live breaks or rips at the table.", is_breaks),
    ("shops",       "Card Shop All-Stars",       "Bricks-and-mortar tables you can visit all year.",
                    "Exhibitors who run a real storefront or hobby shop.", is_shop),
    ("memorabilia", "Memorabilia Legends",       "Signed, framed, game-worn.",
                    "Exhibitors listed under memorabilia, signings or autographs.", is_memorabilia),
    ("tcg",         "TCG Trailblazers",          "One Piece, Riftbound, Lorcana and beyond.",
                    "Trading-card-game tables outside Pokémon.", is_other_tcg),
    ("slabs",       "Slab Elite",                "Graded, numbered, showcase-only.",
                    "Tables built around slabs, graded singles and grails.", is_slabs),
    ("trades",      "Trade Table Champions",     "Bring the binder.",
                    "Every exhibitor who trades at the table.", is_trades),
    ("buying",      "Top Buyers on the Floor",   "Sell to these tables first.",
                    "Every exhibitor who buys collections at the show.", is_buying),
    ("pokemon",     "Pokémon Power Tables",      "The Pokémon floor, ranked.",
                    "Every Pokémon table at the show.", is_pokemon),
    ("michigan",    "Michigan's Finest",         "Hometown tables.",
                    "Exhibitors based in Michigan.", is_michigan),
    ("sports",      "Sports Card Heavyweights",  "The biggest floor at the show, ranked.",
                    "Every sports-card table.", is_sports),
]

METHOD = ("Ranked by Instagram audience, the largest count we hold from any source as of August 2026, "
          "and where two tables have no count, by how complete their RazMania profile is. "
          "Membership comes from each exhibitor's own registration; nothing is placed by hand, "
          "nothing can be bought, and sponsors are not ranked. No exhibitor is #1 in more than one list.")


def depth(r):
    return ((r["profile"] == "full") * 3 + bool(r["editorial"]) * 2 + len(r["sections_html"]) * 3
            + bool(r["tagline"]) + bool(r["website"]) + bool(r["instagram"]) + bool(r["take"]) * 2
            + min(len(r["chips"]), 5) + min(len(r["tables"]), 3) + bool(r["logo"]))


def is_sponsor(r):
    return "Sponsors" in r["categories"] or r["level"] == "Trusted Partner"


def compute(rows):
    """Return the rankings document and append accolades to rows in place."""
    ranked = [r for r in rows if not is_sponsor(r)]
    taken_first = set()
    lists = []
    for key, title, strap, who, match in CATEGORIES:
        field = [r for r in ranked if match(r)]
        field.sort(key=lambda r: (-r["ig_followers"], -depth(r), r["name"].lower()))
        if len(field) < MIN_FIELD:
            continue
        top = field[:TOP + 1]
        # the #1 rule
        lead = next((r for r in top if r["id"] not in taken_first), None)
        if lead is not None and top[0] is not lead:
            top.remove(lead)
            top.insert(0, lead)
        top = top[:TOP]
        if top:
            taken_first.add(top[0]["id"])
        entries = []
        for i, r in enumerate(top, 1):
            tier = "best" if i <= BEST_CUTOFF else "certified"
            entries.append({
                "rank": i, "id": r["id"], "slug": r["slug"], "name": r["name"], "logo": r["logo"],
                "tagline": r["tagline"] or r["summary"], "followers": r["ig_followers"],
                "location": r["location"], "tier": tier,
            })
            r["accolades"].append({
                "key": tier,
                "label": "Best of RazMania %d" % YEAR if tier == "best" else "RazMania Certified",
                "list": title, "list_key": key, "rank": i, "of": len(top), "year": YEAR,
            })
        lists.append({"key": key, "title": title, "strapline": strap, "who": who,
                      "field": len(field), "entries": entries})
    return {"year": YEAR, "method": METHOD, "best_cutoff": BEST_CUTOFF, "lists": lists}
