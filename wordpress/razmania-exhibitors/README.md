# RazMania Exhibitors

The exhibitor directory and every exhibitor profile, on razmania.com:

| URL | What |
|---|---|
| `/exhibitors/` | The directory: every table, searchable, filterable by category, sponsors listed separately |
| `/exhibitors/<slug>/` | One profile per exhibitor (143 today: 106 written profiles, 37 basic listings) |
| `/exhibitors/verified/` | What the Verified Exhibitor stamp means |
| `/exhibitors/best/` | The Best of RazMania: ten ranked lists from `data/rankings.json`, built by `exhibitors/rankings.py` |

Everything is rendered **server-side from `data/profiles.json`**. No API, no
database, no JavaScript needed to read a page. That is the point of the move:
on razmaniasports.com the same profiles were painted by a Swoogo snippet after
load, so Google indexed an empty "Sponsor Details" shell for every one of them.

## Where the data comes from

`exhibitors/build_profiles.py` in the repo scrapes the live Swoogo pages
(the directory's `sponsorsObj` and slug map, and the profile snippet's
REG/TAG/ED/TABLES/RZ_DAYS objects plus the hand-written sections), merges
`exhibitors/profiles.overrides.json`, and writes `profiles.json` here and in
`exhibitors/`. Sponsorship levels come from the Swoogo API when
`SWOOGO_KEY`/`SWOOGO_SECRET` are in `.env`.

To change a profile: edit the overrides file, run the build, re-upload. The
settings screen deliberately edits nothing.

```bash
python -X utf8 exhibitors/build_profiles.py          # live fetch
python -X utf8 exhibitors/build_profiles.py --offline # from the last fetch
python wordpress/build_zip.py razmania-exhibitors     # -> wordpress/dist/razmania-exhibitors.zip
```

## Install

1. Build the zip with `build_zip.py` (not PowerShell's `Compress-Archive`,
   which writes backslash paths and breaks the upload; see DEPLOY.md).
2. Plugins → Add New → Upload, activate.
3. **Deactivate "RazMania Exhibitor Share Meta"** if it is installed. This
   plugin does its job (Rank Math stripping, titles, og:image) and the two
   would fight over the head.
4. Settings → RazMania Exhibitors: set the tickets URL and the event status.
   Defaults already describe RazMania 2026 as a past event.
5. Open `/exhibitors/`. If it 404s, Settings → Permalinks → Save (once).
6. Purge `/exhibitors/*` in Cloudflare. The site's HTML is cached there for 31
   days, and the old response for `/exhibitors/` is the News page.
7. View source on a profile. The tagline, editorial and table number must be
   in the raw HTML.

## Event status

Two copies of every line that depends on time. **Past** (default) reads
"Exhibited at RazMania 2026", "Where they were", and turns the ticket button
into "Tickets for the next RazMania". **Upcoming** reads "Exhibiting at",
"Find them at", "Get RazMania tickets". When the 2027 registry is built, the
event name and dates change in the same screen.

## Accolades and rankings

Each exhibitor carries an `accolades` list. Today every one has
`{"key":"verified"}`, which renders as the stamp in the hero. Any other entry
renders in a strip under the chips, from data alone:

```json
{"key":"rank", "label":"Most-followed Pokémon table", "category":"Pokemon / TCG", "rank":1, "year":2026,
 "detail":"28,000 Instagram followers"}
```

So a ranking programme needs no template change to ship. The proposal, the
categories and the scoring are in `exhibitors/RANKINGS.md`.

## The older razmania.com exhibitor system

razmania.com already had exhibitor pages before this plugin: the live
**RazMania Cards** plugin (v1.12, never committed to this repo) carries an
`inc/exhibitors.php` that claims every `/exhibitors/<slug>/` URL and served 54
profiles from `data/exhibitors.json` dated 18 Aug 2026. Its titles were all
"News", its directory route never worked, and its share-kit graphics 404.

This plugin takes over without editing that code: it accepts the old query
vars (`rzm_exh`, `rzm_exh_slug`), answers on `template_redirect` at priority 0
(the old handler runs at 10 and is never reached), and removes the old
closures on `wp_head` and the document title. The old data was copied to
`exhibitors/data/legacy-razmania.json` and merged by the build: Instagram
follower/post counts, "Our take" editorials, "at the table" facts, eight
exhibitors that existed only there, and seven slugs that were emailed to
exhibitors, which now 301 to the Swoogo slug (`legacy_redirects` in the
overrides file).

Deployed 3 Sep 2026 through wp-admin: v1.0.0 (directory only, profiles
shadowed), v1.1.0 (takeover + legacy merge), v1.1.2 (own `<title>` tag, since
stripping Rank Math had left the pages without one; no superlative follower
award, because only 20 of 153 tables have a count), v1.2.0 (The Best of
RazMania: ten ranked lists at `/exhibitors/best/`, Best / Certified banners on
profiles and badges on directory cards).

## What the Swoogo side still points at

Swoogo sponsor descriptions already say "Full profile: razmania.com/exhibitors/<slug>".
The Swoogo directory enhancer (snippet 21022) still links each logo to the
Swoogo profile; once these pages are live, point it at razmania.com and the
Swoogo profile snippet can be retired.
