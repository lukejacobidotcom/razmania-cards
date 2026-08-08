# Deploy checklist — RazMania Card Data

Config is already decided and committed: **`MIN_PRICE=2000`, ~$126/mo all-in.**
Nothing below needs a decision — it's mechanical. ~30 minutes.

| | |
|---|---|
| Apify (`MIN_PRICE=2000`, 739 sales/day, 2-day window) | ~$111/mo |
| Render Postgres `basic-256mb` + 5 GB | $7.50/mo |
| Render web service `starter` | $7/mo |
| Render cron `starter`, ~10 min/day | ~$0.75/mo |
| **Total** | **~$126/mo** |

The leaderboard — the flagship homepage module — is **byte-identical** to what
$500+ coverage would produce. The 50th-biggest sale of a week is ~$27,000.

---

## Step 1 — Render Blueprint (10 min)

Render → **New → Blueprint** → pick `lukejacobidotcom/razmania-cards`.
`render.yaml` creates three things:

- `razmania-db` — Postgres `basic-256mb`
- `razmania-cards-api` — web service `starter`
- `razmania-daily-refresh` — cron, daily 09:00 UTC (05:00 ET)

You already have an **empty** `razmania-db` (`dpg-d9p5hsht0dsc73capt70-a`).
Let the Blueprint create its own and **delete the old empty one** — do not run two.

Then on the **cron job** → Environment, set one value by hand:

- `APIFY_TOKEN` = your Apify token

`MIN_PRICE=2000` is already in `render.yaml`. Do not switch anything to a free
plan: free Postgres expires after 30 days and a free web service sleeps after
15 min with a ~1 min cold start.

## Step 2 — Initialise the database (5 min)

Copy the **External Database URL** from the Render Postgres page, then locally:

```bash
export DATABASE_URL='postgres://...'          # from Render
psql "$DATABASE_URL" -f db/schema.sql
python3 -m pip install -r requirements.txt
python3 etl/load.py --csv seed/sales_all.csv.gz
psql "$DATABASE_URL" -c "ALTER ROLE razmania_read PASSWORD '<something-long>';"
```

Expected output, verified end-to-end on a clean database:

```
NOTICE:  building is_publishable with floor $2000
read 21,766 rows -> 21,766 unique, >= $500
inserted 21,766 new / updated 0 | table now 21,766
materialized views refreshed
```

**21,766 loaded but only 5,175 tracked is correct, not a bug.** The seed is
$500+ data you already paid for. It stays in the table for `/v1/search` and for
the day you drop the floor, but the publish floor keeps it out of every
aggregate. Sanity check:

```bash
psql "$DATABASE_URL" -c \
  "SELECT count(*) total, count(*) FILTER (WHERE is_publishable) publishable,
          min(total_price) FILTER (WHERE is_publishable) min_published FROM sales;"
```
Must return `21766 | 3815 | 2000.00`. If `min_published` is under 2000 the floor
did not apply — stop and re-run `db/schema.sql`.

## Step 3 — Verify the API (2 min)

```bash
curl https://<your-api>.onrender.com/v1/health
curl "https://<your-api>.onrender.com/v1/search?q=charizard&limit=3"
```

`/v1/health` should return `"ok":true` and `"rows":5175`. `fresh` will be false
until the first cron run — expected on day one. Search must return rows; empty
means the trigram index didn't build.

Copy the auto-generated `API_KEY` from the API service's Environment tab.

## Step 4 — Prove the cron runs (do NOT skip)

Don't wait for 09:00 UTC to discover it's broken. On `razmania-daily-refresh`
hit **Trigger Run** and watch the logs. A healthy run prints:

```
==> database is 4d behind -> 6d window, floor $2000
==> 1/4 scraping
min_price=$2,000 days=6 bands=6
==> 2/4 loading + refreshing aggregates
==> 3/4 retention + vacuum
==> 4/4 freshness gate
freshness OK
==> done
```

It **exits non-zero on purpose** if the newest sale is more than 2 days old or
the leaderboard is empty. That failure is the alert — turn on Render's failure
notifications for this service. It is the one thing that will silently rot.

The first run costs more than a normal day (6-day catch-up window, ~4,400 rows
≈ $11). Every run after that is ~1,478 rows ≈ $3.70.

**Check back in a week.** `/v1/health` should show `days_stale` of 0 or 1 and a
`last_successful_refresh` from that morning.

## Step 5 — WordPress (10 min)

1. Upload `wordpress/razmania-cards/` to `wp-content/plugins/` (or zip that
   folder and use Plugins → Add New → Upload).
2. Activate it.
3. **Settings → RazMania Cards** → paste the API base URL and the API key.
4. Drop shortcodes on a page:
   ```
   [razmania_leaderboard limit="10"]
   [razmania_verticals]
   [razmania_stats]
   ```
5. **View source.** The prices must be in the raw HTML. If they only appear
   after JavaScript runs, Google won't index them and the SEO case for this
   whole project collapses.
6. **Label the floor.** Headline copy must say "biggest card sales over $2,000"
   or "tracked sales over $2,000" — never "all card sales". The database cannot
   publish a number below the floor, but it cannot stop you mislabelling one.

---

## When to change the floor

Drop `MIN_PRICE` to `500` (and `publish_floor` in `db/schema.sql` to match, then
re-run that file) when you start building **player value pages**. At $2,000+ only
17 cards clear the n≥3 comps rule and 72 players have data — not enough for the
SEO engine. At $500+ it was 111 players and far deeper comps. That change takes
Apify from ~$111 to ~$466/mo, so make it when the pages actually ship.

Homepage modules need no such change. They are complete today.

## Housekeeping

- **Revoke the GitHub PAT** — it has Contents + Administration write on every
  repo in your account. GitHub → Settings → Developer settings → Personal access
  tokens → revoke.
- **Delete the stray `.git` in `C:/Users/luke`.** Run `git log --oneline -1`
  there first to confirm it isn't a real repo, then `rm -rf ~/.git`.

## Send me back

1. The API base URL (`https://....onrender.com`)
2. Confirmation the shortcodes render server-side

I'll point the front-end brief at the real URL and generate the player-page URL
list for the SEO build.
