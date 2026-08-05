"""
Vertical classification for eBay card titles.

Vocabularies are EXPLICIT LISTS, never whitespace-split blobs — a blob splits
"the rock" into the token "the", which then matches every listing on earth.
Multi-word phrases must stay intact.
"""

import re
import unicodedata

# ---------------------------------------------------------------- franchises
POKEMON = [
    "pokemon", "pokémon", "charizard", "pikachu", "umbreon", "eevee", "mewtwo",
    "rayquaza", "lugia", "moonbreon", "snorlax", "articuno", "zapdos", "moltres",
    "gengar", "blastoise", "venusaur", "sylveon", "espeon", "vaporeon", "jolteon",
    "flareon", "giratina", "arceus", "greninja", "gardevoir", "magikarp",
    "dragonite", "dragonair", "psyduck", "celebi", "ho-oh", "entei", "raikou",
    "suicune", "gyarados", "machamp", "alakazam", "ninetales", "jigglypuff",
    "mudkip", "lucario", "squirtle", "bulbasaur", "typhlosion", "feraligatr",
    "prismatic evolutions", "skyridge", "corocoro", "illustrator", "neo destiny",
    "team rocket returns", "hidden fates", "evolving skies", "crown zenith",
    "shining fates", "base set", "1st edition holo", "trainer promo",
]
ONE_PIECE = [
    "one piece", "luffy", "zoro", "roronoa", "nami", "sanji", "shanks", "kaido",
    "yamato", "nico robin", "chopper", "usopp", "portgas", "monkey.d", "monkey d",
    "op01", "op02", "op03", "op04", "op05", "op06", "op07", "op08", "op09",
]
MAGIC = [
    "magic the gathering", "magic: the gathering", "mtg", "black lotus",
    "ancestral recall", "time walk", "timetwister", "dual land", "tarmogoyf",
    "planeswalker", "mox pearl", "mox jet", "mox ruby", "mox sapphire", "mox emerald",
    "alpha edition", "beta edition", "revised edition", "commander deck",
]
YUGIOH = [
    "yugioh", "yu-gi-oh", "yu gi oh", "blue-eyes", "blue eyes white dragon",
    "dark magician", "exodia", "kuriboh", "konami", "ygo",
]
RIFTBOUND = ["riftbound", "league of legends"]

WWE = [
    "wwe", "wwf", "wcw", "ecw", "aew", "njpw", "tna", "wrestling", "wrestlemania",
    "hulk hogan", "stone cold", "steve austin", "undertaker", "roman reigns",
    "john cena", "rhea ripley", "becky lynch", "alexa bliss", "randy savage",
    "ric flair", "bret hart", "shawn michaels", "the rock wwe", "cody rhodes",
    "seth rollins", "bianca belair", "charlotte flair", "sasha banks", "bayley",
    "iyo sky", "liv morgan", "rey mysterio", "triple h", "andre the giant",
    "royal rumble", "summerslam", "oba femi", "jey uso", "jimmy uso", "logan paul wwe",
]

# ------------------------------------------------------------- sport signals
SPORT_WORDS = {
    "Baseball": ["baseball", " mlb", "mlb ", "world baseball classic", "topps heritage",
                 "allen & ginter", "allen and ginter", "gypsy queen", "stadium club baseball"],
    "Football": ["football", " nfl", "nfl ", "college football", "super bowl"],
    "Hockey": ["hockey", " nhl", "nhl ", "young guns", "stanley cup"],
    "Basketball": ["basketball", " nba", "nba ", "wnba"],
    "Soccer": ["soccer", "futbol", "uefa", "premier league", "la liga", "fifa",
               " mls ", "champions league", "ballon d'or"],
    "Motorsport": ["formula 1", "formula one", "formule", "nascar", "grand prix",
                   "f1 ", " f1"],
}

# Unambiguous team names ONLY. Rangers/Cardinals/Kings/Panthers/Jets/Giants are
# shared across leagues and are deliberately excluded.
TEAMS = {
    "Baseball": ["yankees", "red sox", "blue jays", "orioles", "rays", "guardians",
                 "tigers", "twins", "white sox", "royals", "astros", "mariners",
                 "braves", "marlins", "mets", "phillies", "nationals", "cubs",
                 "reds", "brewers", "pirates", "diamondbacks", "dbacks", "rockies",
                 "dodgers", "padres", "athletics"],
    "Football": ["patriots", "bills", "dolphins", "ravens", "bengals", "browns",
                 "steelers", "texans", "colts", "jaguars", "titans", "broncos",
                 "chiefs", "raiders", "chargers", "cowboys", "eagles", "commanders",
                 "buccaneers", "seahawks", "49ers", "niners", "vikings", "packers"],
    "Hockey": ["bruins", "sabres", "red wings", "canadiens", "senators", "lightning",
               "maple leafs", "hurricanes", "blue jackets", "islanders", "flyers",
               "penguins", "capitals", "blackhawks", "avalanche", "predators",
               "blues", "oilers", "canucks", "golden knights", "kraken", "flames"],
    "Basketball": ["celtics", "knicks", "76ers", "sixers", "raptors", "bulls",
                   "cavaliers", "pistons", "pacers", "bucks", "hornets", "nuggets",
                   "timberwolves", "thunder", "trail blazers", "mavericks", "rockets",
                   "grizzlies", "pelicans", "spurs", "lakers", "clippers", "warriors"],
}

PLAYERS = {
    "Baseball": ["ohtani", "shohei", "mickey mantle", "babe ruth", "ken griffey",
                 "nolan ryan", "cal ripken", "derek jeter", "pete rose", "ty cobb",
                 "mike trout", "aaron judge", "paul skenes", "juan soto", "jackie robinson",
                 "hank aaron", "willie mays", "roberto clemente", "sandy koufax",
                 "chipper jones", "frank thomas", "barry bonds", "mark mcgwire",
                 "sammy sosa", "randy johnson", "ryne sandberg", "rickey henderson",
                 "cal raleigh", "bobby witt", "elly de la cruz", "jasson dominguez",
                 "roman anthony", "dylan crews", "wyatt langford", "jackson holliday",
                 "max clark", "walker jenkins", "ronald acuna", "vladimir guerrero",
                 "fernando tatis", "julio rodriguez", "corbin carroll", "gunnar henderson"],
    "Football": ["mahomes", "tom brady", "joe montana", "jerry rice", "barry sanders",
                 "walter payton", "dan marino", "emmitt smith", "brett favre",
                 "peyton manning", "jayden daniels", "caleb williams", "drake maye",
                 "bo nix", "joe burrow", "travis kelce", "justin jefferson",
                 "ja'marr chase", "jamarr chase", "cj stroud", "c.j. stroud",
                 "lamar jackson", "josh allen", "myles garrett", "saquon barkley",
                 "bijan robinson", "puka nacua", "marvin harrison", "malik nabers",
                 "jalen hurts", "deion sanders", "lawrence taylor", "jim brown"],
    "Hockey": ["gretzky", "mario lemieux", "connor mcdavid", "mcdavid", "crosby",
               "ovechkin", "auston matthews", "bedard", "makar", "patrick roy",
               "bobby orr", "gordie howe", "maurice richard", "jaromir jagr",
               "nathan mackinnon", "leon draisaitl", "macklin celebrini"],
    "Basketball": ["lebron", "michael jordan", "kobe bryant", "curry", "wembanyama",
                   "cooper flagg", "ja morant", "anthony edwards", "caitlin clark",
                   "shaquille", "shaq ", "larry bird", "magic johnson",
                   "kevin durant", "giannis", "luka doncic", "jokic", "victor wemby",
                   "david robinson", "hakeem", "scottie pippen", "tim duncan",
                   "paige bueckers", "angel reese"],
}

TARGET = {"Baseball", "Football", "Hockey", "WWE", "Pokemon"}

NOISE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️⬀-⯿]")


def clean(t):
    t = unicodedata.normalize("NFKC", str(t))
    t = NOISE.sub(" ", t).replace("’", "'")
    return re.sub(r"\s+", " ", t).strip()


def _hit(low, terms):
    for w in terms:
        if not w:
            continue
        if w != w.strip():          # caller wanted the padding (e.g. " nfl")
            if w in low:
                return True
        elif " " in w or "." in w or "-" in w or "'" in w:
            if w in low:
                return True
        elif re.search(rf"\b{re.escape(w)}\b", low):
            return True
    return False


def classify(title, category_id=None):
    """Return (vertical, confidence)."""
    low = " " + clean(title).lower() + " "

    # 1. Trading-card-game franchises are unambiguous — check first.
    for name, vocab in (("Pokemon", POKEMON), ("One Piece", ONE_PIECE),
                        ("Magic", MAGIC), ("Yu-Gi-Oh", YUGIOH),
                        ("Riftbound", RIFTBOUND)):
        if _hit(low, vocab):
            return name, "high"

    # 2. WWE next: wrestling cards sit inside sports categories and would
    #    otherwise be captured by a stray team or set name.
    if _hit(low, WWE):
        return "WWE", "high"

    # 3. Explicit sport words.
    for sport, words in SPORT_WORDS.items():
        if _hit(low, words):
            return sport, "high"

    # 4. Named players.
    for sport, names in PLAYERS.items():
        if _hit(low, names):
            return sport, "high"

    # 5. Team names (unambiguous set only).
    for sport, teams in TEAMS.items():
        if _hit(low, teams):
            return sport, "medium"

    if str(category_id) == "183454":
        return "Other CCG", "low"
    return "Unknown", "low"
