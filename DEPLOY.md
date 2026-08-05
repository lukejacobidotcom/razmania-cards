# Deploy checklist — RazMania Card Data

Everything is built and tested. This is what a human has to do. ~45 minutes.

---

## Decision to make first

**Pick a tier.** This is the only real choice; everything else is mechanical.

| | Apify cost | Biggest-sales module | Medians / hottest markets |
|---|---|---|---|
| `TIER=full` ($500+) | **~$236/mo** | complete | complete |
| `TIER=top` ($2,000+) | **~$56/mo** | **identical** | only reflects $2,000+ sales |

The leaderboard is byte-identical either way — the 50th-biggest sale of the week
is ~$27,000, so nothing under $2,000 ever appears on it.

**Recommended start: `TIER=top` (~$56/mo)**, plus one weekly `TIER=full` run
(~$54/wk) to keep medians honest. Move to daily `full` when traffic justifies it.
Total ~$110/mo vs ~$250/mo, with no visible difference on the homepage.

---

## Step 1 — Rotate the Apify token (2 min) ⚠️

The token you pasted is in chat history and in this container. Apify Console →
Settings → Integrations → revoke it, create a new one. Use the new one below.

## Step 2 — Push to GitHub (5 min)

```bash
unzip razmania-cards-platform.zip && cd razmania-cards
git init && git add . && git commit -m "RazMania card data platform"
git remote add origin git@github.com:<you>/razmania-cards.git
git push -u origin main
```

`.gitignore` already excludes `.env`, `raw/` and `out/`. The seed data
(`seed/sales_all.csv.gz`, 1.4 MB) **is** committed on purpose — it's your backfill.

## Step 3 — Render Blueprint (10 min)

Render → **New → Blueprint** → pick the repo. `render.yaml` creates:

- `razmania-db` — Postgres `basic-256mb` ($6/mo)
- `razmania-cards-api` — web service `starter` ($7/mo)
- `razmania-daily-refresh` — cron, daily 09:00 UTC (~$0.75/mo)

Then on the **cron job** → Environment:

- `APIFY_TOKEN` = your new token
- `TIER` = `top` or `full` (see the decision above)

Do **not** switch anything to a free plan. Free Postgres expires after 30 days
and a free web service sleeps after 15 min with a ~1 min cold start.

## Step 4 — Initialise the database (5 min)

Copy the **External Database URL** from the Render Postgres page, then locally:

```bash
export DATABASE_URL='postgres://...'          # from Render
psql "$DATABASE_URL" -f db/schema.sql          # creates tables + 6 materialized views
python3 -m pip install -r requirements.txt
python3 etl/load.py --csv seed/sales_all.csv.gz
```

Expected output — this is the exact result verified on a clean database:

```
read 21,766 rows -> 21,766 unique, >= $500
inserted 21,766 new / updated 0 | table now 21,766
materialized views refreshed
```

Then set the read-only role's password (schema.sql creates it with a placeholder):

```bash
psql "$DATABASE_URL" -c "ALTER ROLE razmania_read PASSWORD '<something-long>';"
```

## Step 5 — Verify the API (2 min)

```bash
curl https://<your-api>.onrender.com/v1/health
```

Should return `"ok":true`, `"fresh":true`, `"rows":21766`. If `fresh` is false,
the daily cron hasn't run yet — that's fine on day one.

Copy the auto-generated `API_KEY` from the API service's Environment tab.

## Step 6 — WordPress (10 min)

1. Upload `wordpress/razmania-cards/` to `wp-content/plugins/` (or zip that
   folder and use Plugins → Add New → Upload).
2. Activate it.
3. **Settings → RazMania Cards** → paste the API base URL and the API key.
4. Drop a shortcode on a page and confirm it renders:
   ```
   [razmania_leaderboard limit="10"]
   [razmania_verticals]
   [razmania_stats]
   ```
5. **View source** on that page. The prices must be in the raw HTML. If they
   only appear after JavaScript runs, Google won't index them and the SEO case
   for this whole project collapses.

## Step 7 — Watch the first cron run

Render → `razmania-daily-refresh` → Logs, after the first 09:00 UTC firing.
A healthy run ends with `freshness OK` then `done`. It **exits non-zero on
purpose** if the newest sale is more than 2 days old — that failure is the alert.

Turn on Render's failure notifications for that service. This is the one thing
that will silently rot if nobody watches it.

---

## Send me back

1. The API base URL (`https://....onrender.com`)
2. Confirmation the shortcodes render server-side

I'll update the front-end brief with the real URL so the other chat can build
against it, and generate the player-page URL list for the SEO build.

---

## Things I could not do for you

- Rotate the Apify token (your account)
- Create the GitHub repo / Render services (your accounts, your billing)
- Upload to GoDaddy WordPress (your hosting credentials)
- Point razmania.com at the WordPress install if it isn't already
