"""
Image assets for the exhibitor pages: local logos and share cards.

Called by build_profiles.py after the rows are final. Two jobs:

1. LOGOS. Swoogo serves every logo from its "full" upload - one is 1.1 MB,
   and the first twenty on the directory weigh 15 MB between them. Each is
   downloaded once (cached in data/logo-cache/), fitted into a 264px box
   (2x the 132px it is drawn at) and written as WebP into the plugin at
   assets/logos/<slug>.webp. The row gets `logo_local`; the Swoogo URL stays
   in `logo` as the fallback.

2. SHARE CARDS. A 1200x630 link card for every exhibitor (this is also the
   page's og:image - a square logo makes a poor social preview) and a
   1080x1350 story card for every exhibitor holding a Best or Certified
   placing, so the accolade is something they can post the day they see it.
   JPEG, because Facebook and LinkedIn will not render WebP previews.

Fonts come from Windows (Georgia for the display face, Arial for the rest)
and fall back to Pillow's default so a build on another machine still
completes, just less handsomely.
"""

import io
import os
import re
import urllib.request

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "wordpress", "razmania-exhibitors")
LOGO_CACHE = os.path.join(HERE, "data", "logo-cache")
LOGO_OUT = os.path.join(PLUGIN, "assets", "logos")
SHARE_OUT = os.path.join(PLUGIN, "assets", "share")
UA = {"User-Agent": "Mozilla/5.0 (razmania-cards build_profiles)"}

PAPER = (251, 249, 245)
INK = (20, 17, 13)
INK2 = (87, 81, 74)
INK3 = (110, 104, 98)
GOLD = (245, 197, 24)
GOLD_TYPE = (138, 97, 0)
LINE = (222, 216, 206)

FONT_DIR = os.environ.get("WINDIR", r"C:\Windows") + r"\Fonts"


def font(name, size):
    for cand in ([name] if isinstance(name, str) else name):
        p = os.path.join(FONT_DIR, cand)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


SERIF_B = lambda s: font(["georgiab.ttf", "georgia.ttf"], s)
SANS = lambda s: font(["arial.ttf"], s)
SANS_B = lambda s: font(["arialbd.ttf", "arial.ttf"], s)


# ------------------------------------------------------------------- logos

def fetch_logo(row):
    """The original bytes, from cache or Swoogo. Returns None when there is
    no logo or it cannot be fetched; the page then draws initials."""
    url = row.get("logo") or ""
    if not url:
        return None
    os.makedirs(LOGO_CACHE, exist_ok=True)
    ext = os.path.splitext(url.split("?")[0])[1].lower() or ".img"
    path = os.path.join(LOGO_CACHE, re.sub(r"[^a-z0-9]", "_", row["id"]) + ext)
    if os.path.exists(path):
        return open(path, "rb").read()
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except Exception as e:                       # noqa: BLE001
        print(f"  (logo fetch failed for {row['name']}: {e})")
        return None
    with open(path, "wb") as f:
        f.write(data)
    return data


def open_image(data):
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        return im.convert("RGBA")
    except Exception:                            # noqa: BLE001
        return None


def build_logos(rows, offline=False):
    os.makedirs(LOGO_OUT, exist_ok=True)
    done = 0
    for r in rows:
        r["logo_local"] = ""
        if not r.get("logo"):
            continue
        out = os.path.join(LOGO_OUT, r["slug"] + ".webp")
        if not os.path.exists(out):
            # --offline still fetches a logo it has never seen: the cache is
            # per logo, and a missing one is a missing image on the page.
            data = fetch_logo(r)
            im = open_image(data) if data else None
            if im is None:
                continue
            im.thumbnail((264, 264), Image.LANCZOS)
            im.save(out, "WEBP", quality=70, method=6)
        r["logo_local"] = "assets/logos/" + r["slug"] + ".webp"
        done += 1
    return done


# ------------------------------------------------------------- share cards

def wrap(draw, text, fnt, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def logo_tile(row, size):
    """The logo fitted in a white tile with a hairline, or initials."""
    tile = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    d = ImageDraw.Draw(tile)
    d.rectangle([0, 0, size - 1, size - 1], outline=LINE, width=2)
    im = None
    local = os.path.join(PLUGIN, row.get("logo_local", "")) if row.get("logo_local") else ""
    if local and os.path.exists(local):
        im = open_image(open(local, "rb").read())
    if im is not None:
        pad = int(size * 0.08)
        im.thumbnail((size - 2 * pad, size - 2 * pad), Image.LANCZOS)
        tile.alpha_composite(im, ((size - im.width) // 2, (size - im.height) // 2))
    else:
        w = re.sub(r"[^A-Za-z0-9 ]", "", row["name"]).split()
        ini = "".join(x[0] for x in w[:2]).upper() or "RZ"
        f = SERIF_B(int(size * 0.42))
        tw = d.textlength(ini, font=f)
        d.text(((size - tw) / 2, size * 0.26), ini, font=f, fill=GOLD_TYPE)
    return tile


def top_placing(row):
    best = [a for a in row["accolades"] if a.get("key") in ("best", "certified")]
    best.sort(key=lambda a: (a["key"] != "best", a["rank"]))
    return best[0] if best else None


def card(row, size, story):
    W, H = size
    im = Image.new("RGB", size, PAPER)
    d = ImageDraw.Draw(im)
    band = int(H * (0.024 if not story else 0.018))
    d.rectangle([0, 0, W, band], fill=GOLD)
    m = int(W * 0.06)
    y = band + int(H * 0.07)

    eyebrow = "RAZMANIA 2026 EXHIBITOR"
    d.text((m, y), eyebrow, font=SANS_B(int(W * 0.019)), fill=GOLD_TYPE)
    y += int(W * 0.045)

    tile = int(W * (0.19 if not story else 0.26))
    logo_x = W - m - tile if not story else m
    logo_y = y if not story else y
    im.paste(logo_tile(row, tile), (logo_x, logo_y), logo_tile(row, tile))

    text_w = (W - 2 * m - tile - int(W * 0.05)) if not story else (W - 2 * m)
    if story:
        y = logo_y + tile + int(H * 0.05)

    name_f = SERIF_B(int(W * (0.068 if not story else 0.085)))
    lines = wrap(d, row["name"], name_f, text_w)[:2]
    for ln in lines:
        d.text((m, y), ln, font=name_f, fill=INK)
        y += int(name_f.size * 1.08)
    y += int(H * 0.02)

    tag = row.get("tagline") or row.get("summary") or ""
    if tag:
        tag_f = SERIF_B(int(W * (0.03 if not story else 0.04)))
        for ln in wrap(d, tag, tag_f, text_w)[:2]:
            d.text((m, y), ln, font=tag_f, fill=INK2)
            y += int(tag_f.size * 1.3)
        y += int(H * 0.025)

    p = top_placing(row)
    if p:
        best = p["key"] == "best"
        pill_h = int(H * (0.12 if not story else 0.09))
        d.rounded_rectangle([m, y, m + text_w, y + pill_h], radius=int(pill_h * 0.22),
                            fill=GOLD if best else INK)
        f1 = SERIF_B(int(pill_h * 0.36)); f2 = SANS_B(int(pill_h * 0.2))
        col = INK if best else PAPER
        x0 = m + int(pill_h * 0.3)
        # Georgia has no star or tick; Segoe UI Symbol does, and every Windows
        # box carries it. Without it, no glyph rather than a tofu box.
        sym = os.path.join(FONT_DIR, "seguisym.ttf")
        if os.path.exists(sym):
            mark_f = ImageFont.truetype(sym, int(pill_h * 0.34))
            mark = "\u2605" if best else "\u2713"
            d.text((x0, y + int(pill_h * 0.16)), mark, font=mark_f, fill=col)
            x0 += int(d.textlength(mark, font=mark_f)) + int(pill_h * 0.14)
        d.text((x0, y + int(pill_h * 0.14)), ("Best of RazMania 2026" if best else "RazMania Certified"), font=f1, fill=col)
        d.text((m + int(pill_h * 0.3), y + int(pill_h * 0.6)),
               f"#{p['rank']} of {p.get('of', 10)} \u00b7 {p.get('list', '')}", font=f2, fill=col)
        y += pill_h + int(H * 0.035)
    meta = " \u00b7 ".join(x for x in [("@" + row["instagram"]) if row.get("instagram") else "",
                                        row.get("location", ""), row.get("table_label", "")] if x)
    if meta:
        d.text((m, y), meta, font=SANS(int(W * 0.024)), fill=INK3)
        y += int(H * 0.06)

    foot_f = SANS_B(int(W * 0.02))
    url = "razmania.com/exhibitors/" + row["slug"]
    d.line([m, H - int(H * 0.11), W - m, H - int(H * 0.11)], fill=LINE, width=2)
    d.text((m, H - int(H * 0.085)), url, font=foot_f, fill=INK)
    brand = "RAZMANIA  \u00b7  MIDWEST SPORTS & TRADING CARD FESTIVAL"
    bw = d.textlength(brand, font=SANS_B(int(W * 0.016)))
    d.text((W - m - bw, H - int(H * 0.08)), brand, font=SANS_B(int(W * 0.016)), fill=INK3)
    return im


def build_share_cards(rows):
    os.makedirs(SHARE_OUT, exist_ok=True)
    n_link = n_story = 0
    for r in rows:
        link = os.path.join(SHARE_OUT, r["slug"] + "-link.jpg")
        card(r, (1200, 630), False).save(link, "JPEG", quality=74, optimize=True, progressive=True)
        r["share_link"] = "assets/share/" + r["slug"] + "-link.jpg"
        n_link += 1
        r["share_story"] = ""
        if top_placing(r):
            story = os.path.join(SHARE_OUT, r["slug"] + "-story.jpg")
            card(r, (1080, 1350), True).save(story, "JPEG", quality=74, optimize=True, progressive=True)
            r["share_story"] = "assets/share/" + r["slug"] + "-story.jpg"
            n_story += 1
    # sweep cards for exhibitors that no longer exist
    keep = {os.path.basename(r["share_link"]) for r in rows} | {os.path.basename(r["share_story"]) for r in rows if r["share_story"]}
    for f in os.listdir(SHARE_OUT):
        if f not in keep:
            os.remove(os.path.join(SHARE_OUT, f))
    return n_link, n_story
