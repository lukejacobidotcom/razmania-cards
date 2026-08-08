"""
Daily eBay scrape via the Apify REST API.

  export APIFY_TOKEN=...
  python3 etl/scrape.py --out /tmp/razmania/raw --days 2

Runs the caffein.dev/ebay-sold-listings Actor once per (category, price band)
and writes each run's dataset to raw/<runId>.jsonl.

ONE KNOB: MIN_PRICE (env or --min-price, default 2000).

  Apify bills per ROW RETURNED, not per run. The database upsert on item_id
  dedupes perfectly, but only AFTER Apify has billed. So the only levers on cost
  are the price floor and the scrape window width — cadence is irrelevant.

  At $2,000+ the site tracks ~739 sales/day for ~$111/mo. Dropping the floor to
  $500 quadruples volume to ~3,109/day and ~$466/mo. The leaderboard is
  IDENTICAL either way: the 50th-biggest sale of a week is ~$27,000.

  Raising the floor is not free sampling — it biases every published median
  upward (Pokemon median $1,131 at $500+ vs $2,282 at $1,000+). That is why the
  floor is also enforced in db/schema.sql, so the site can only ever publish
  aggregates over the range actually being collected.

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

CATEGORIES = ["261328", "183454"]   # Sports Card Singles, CCG Singles.
# eBay's legacy IDs 213/215/216 (baseball/football/hockey) are silently IGNORED
# by eBay — they return unfiltered results with no error. Never use them.

# Band edges. Bands are built from every cut at or above MIN_PRICE, so they are
# disjoint by construction and no row is ever fetched (and billed) twice.
# Sized so even a 6-day catch-up window stays under ~1,200 rows/band, well clear
# of the Actor's ~3,000-row OOM ceiling.
CUTS = [500, 650, 800, 1000, 1500, 2000, 3000, 5000]

DEFAULT_MIN_PRICE = 2000
DEFAULT_DAYS = 2
COUNT = 3000


def bands_for(min_price):
    """(category, lo, hi) tuples covering [min_price, infinity), disjoint."""
    edges = [c for c in CUTS if c >= min_price] or [min_price]
    edges[0] = min_price
    out = []
    for cat in CATEGORIES:
        for i, lo in enumerate(edges):
            hi = edges[i + 1] - 1 if i + 1 < len(edges) else None
            out.append((cat, lo, hi))
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
    with open(out / f"{ds_id}.jsonl", "w") as fh:
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
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--min-price", type=int,
                    default=int(os.environ.get("MIN_PRICE", DEFAULT_MIN_PRICE)))
    args = ap.parse_args()
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("Set APIFY_TOKEN")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    bands = bands_for(args.min_price)
    print(f"min_price=${args.min_price:,} days={args.days} bands={len(bands)}")
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
