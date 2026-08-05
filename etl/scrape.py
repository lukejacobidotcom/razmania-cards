"""
Daily eBay scrape via the Apify REST API (no MCP, no desktop bridge needed).

  export APIFY_TOKEN=...
  python3 etl/scrape.py --out /tmp/razmania/raw --tier full --days 2

Runs the caffein.dev/ebay-sold-listings Actor once per (category, price band)
and writes each run's dataset to raw/<runId>.jsonl.

Why this Actor: it is the only one tested that returns `isBestOfferAccepted`.
At $500+, ~30% of listings are best-offer-accepted, and eBay publishes the
seller's ASKING price on those. Without that flag every published median and
every leaderboard is biased high.

Why the `-zzqqxx` keyword: the Actor rejects an empty keyword, but any real
keyword silently drops listings that don't contain that word. A nonsense
NEGATIVE keyword excludes nothing, so it behaves as a full category browse.

Why price bands: a single eBay search caps out near 10,000 results, and the
Actor OOMs past roughly 3,000 rows in one run. Bands solve both at once.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ACTOR = "caffein.dev~ebay-sold-listings"
API = "https://api.apify.com/v2"

SPORTS, CCG = "261328", "183454"          # both VERIFIED to actually filter.
# eBay's legacy IDs 213/215/216 (baseball/football/hockey) are silently IGNORED
# by eBay — they return unfiltered results with no error. Never use them.

ALL_BANDS = [
    (SPORTS, 500, 649), (SPORTS, 650, 799), (SPORTS, 800, 999),
    (SPORTS, 1000, 1499), (SPORTS, 1500, 2999), (SPORTS, 3000, None),
    (CCG, 500, 699), (CCG, 700, 999), (CCG, 1000, 1999), (CCG, 2000, None),
]

# Daily cadence. A 2-day window means every run re-scrapes yesterday, so a single
# missed or failed run self-heals on the next one. That overlap is the ONLY extra
# cost of running daily instead of weekly — you pay per sale returned either way.
DEFAULT_DAYS = 2
COUNT = 3000                              # per band; the Actor OOMs much past this

# Tiers let you trade freshness for spend without touching code.
#   full  — everything over $500. ~6,200 rows/run at a 2-day window.
#   top   — only $2,000+. ~1,500 rows/run. The leaderboard is identical either way
#           (the 50th-biggest sale of the week is ~$27k), but medians and market
#           GMV go stale on the low end, so pair this with a weekly `full` run.
TIER_MIN_PRICE = {"full": 500, "top": 2000}


def bands_for(tier):
    """Drop bands entirely below the tier floor; clamp the one that straddles it."""
    floor = TIER_MIN_PRICE[tier]
    out = []
    for cat, lo, hi in ALL_BANDS:
        if hi is not None and hi < floor:
            continue
        out.append((cat, max(lo, floor), hi))
    return out


def post(path, payload, token, timeout=60):
    req = urllib.request.Request(
        f"{API}/{path}?token={token}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(path, token, timeout=120):
    with urllib.request.urlopen(f"{API}/{path}{'&' if '?' in path else '?'}token={token}",
                                timeout=timeout) as r:
        return json.loads(r.read())


def start(cat, lo, hi, token, days):
    inp = {
        "keywords": ["-zzqqxx"], "daysToScrape": days, "count": COUNT,
        "categoryId": cat, "ebaySite": "ebay.com",
        "sortOrder": "endedRecently", "minPrice": lo,
        "includeCompletedListings": True,
    }
    if hi:
        inp["maxPrice"] = hi
    r = post(f"acts/{ACTOR}/runs?memory=4096&timeout=2400", inp, token)
    return r["data"]["id"], r["data"]["defaultDatasetId"]


def wait(run_id, token, limit=2400):
    t0 = time.time()
    while time.time() - t0 < limit:
        st = get(f"actor-runs/{run_id}", token)["data"]["status"]
        if st in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            return st
        time.sleep(10)
    return "TIMEOUT"


def download(ds_id, out, token):
    n, offset = 0, 0
    path = out / f"{ds_id}.jsonl"
    with open(path, "w") as fh:
        while True:
            qs = urllib.parse.urlencode({"offset": offset, "limit": 1000,
                                         "format": "json", "clean": "true"})
            rows = get(f"datasets/{ds_id}/items?{qs}", token)
            if not rows:
                break
            for row in rows:
                fh.write(json.dumps(row) + "\n")
            n += len(rows)
            offset += len(rows)
            if len(rows) < 1000:
                break
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="raw")
    ap.add_argument("--tier", choices=["full", "top"], default="full")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = ap.parse_args()
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("Set APIFY_TOKEN")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    bands = bands_for(args.tier)
    print(f"tier={args.tier} days={args.days} bands={len(bands)}")
    started, total, failed = [], 0, []
    for cat, lo, hi in bands:
        try:
            run_id, ds_id = start(cat, lo, hi, token, args.days)
            started.append((cat, lo, hi, run_id, ds_id))
            print(f"started cat={cat} ${lo}-{hi or 'up'} run={run_id}")
        except urllib.error.HTTPError as e:                       # noqa: PERF203
            failed.append((cat, lo, hi, f"start failed: {e}"))

    for cat, lo, hi, run_id, ds_id in started:
        st = wait(run_id, token)
        rows = download(ds_id, out, token)     # partial data from a failed run is
        total += rows                          # still real data — keep it.
        print(f"  cat={cat} ${lo}-{hi or 'up'} {st} -> {rows:,} rows")
        if st != "SUCCEEDED":
            failed.append((cat, lo, hi, st))

    print(f"\n{total:,} rows across {len(started)} runs -> {out}")
    for f in failed:
        print("  WARN:", f)
    if total == 0:
        sys.exit("FAIL: scrape returned no rows at all")


if __name__ == "__main__":
    main()
