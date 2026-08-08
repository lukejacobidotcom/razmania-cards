"""
Daily eBay scrape via the Apify REST API.

  export APIFY_TOKEN=...
  python3 etl/scrape.py --out /tmp/razmania/raw --days 2

Runs the caffein.dev/ebay-sold-listings Actor once per (category, price band)
and writes each run's dataset to raw/<runId>.jsonl.

COST MODEL — read this before changing anything.

  Apify bills per ROW RETURNED, not per run. The upsert on item_id dedupes
  perfectly, but only AFTER Apify has billed. So there are exactly three levers:
  the price floor, the window width, and how often each price range is scraped.

  --min-price / --max-price exist for the third one. refresh_daily.sh scrapes
  $10,000+ every day and $2,000-9,999 once a week, because measurement showed
  ALL of the top 50 weekly sales came from the $10,000+ slice — 9% of the volume.
  Redundancy drops from 2.0x to 1.2x: ~$111/mo -> ~$68/mo, same rows collected.

  Volume at $0.0025/row, measured over a full week:
      $10,000+          68 sales/day
      $5,000-9,999     134 sales/day
      $3,000-4,999     215 sales/day
      $2,000-2,999     323 sales/day
      $500-1,999     2,370 sales/day   (not collected; see below)

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
CUTS = [500, 650, 800, 1000, 1500, 2000, 3000, 5000, 10000]

DEFAULT_MIN_PRICE = 2000
DEFAULT_DAYS = 2
COUNT = 3000
PRICE_PER_ROW = 0.0025          # Apify tier price for this account. Log-only.


def bands_for(min_price, max_price=None):
    """(category, lo, hi) tuples covering [min_price, max_price], disjoint.

    max_price exists so the daily job can scrape an expensive-but-tiny top slice
    every day and a cheap-but-huge lower slice on a weekly cadence, without ever
    fetching the same row twice. See etl/refresh_daily.sh.
    """
    edges = [c for c in CUTS
             if c >= min_price and (max_price is None or c <= max_price)] or [min_price]
    edges[0] = min_price
    out = []
    for cat in CATEGORIES:
        for i, lo in enumerate(edges):
            if i + 1 < len(edges):
                hi = edges[i + 1] - 1
            else:
                hi = max_price
            out.append((cat, lo, hi))
    return out


def post(path, payload, token, timeout=60):
    # The separator MUST be conditional. Callers pass paths that already carry a
    # query string ("runs?memory=4096&timeout=2400"); appending "?token=" made a
    # URL with TWO '?', so Apify parsed the token as part of `timeout` and saw an
    # ANONYMOUS request -- which cannot run a paid Actor, so every start came back
    # "402 Payment Required". get() always had this right; post() did not.
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(
        f"{API}/{path}{sep}token={token}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(path, token, timeout=120):
    with urllib.request.urlopen(f"{API}/{path}{'&' if '?' in path else '?'}token={token}",
                                timeout=timeout) as r:
        return json.loads(r.read())


# Apify occasionally rejects a burst of simultaneous run-starts with 402/429/5xx
# even on a healthy account — six bands firing inside two seconds is enough to
# trigger it. Observed 2026-08-08: all six bands got "402 Payment Required" at
# 21:40 UTC, and an identical single run started fine three minutes later.
# Without a retry that transient blip silently costs a whole day of data.
RETRY_STATUS = {402, 429, 500, 502, 503, 504}


def start(cat, lo, hi, token, days, attempts=4):
    inp = {
        "keywords": ["-zzqqxx"], "daysToScrape": days, "count": COUNT,
        "categoryId": cat, "ebaySite": "ebay.com",
        "sortOrder": "endedRecently", "minPrice": lo,
        "includeCompletedListings": True,
    }
    if hi:
        inp["maxPrice"] = hi
    for i in range(attempts):
        try:
            r = post(f"acts/{ACTOR}/runs?memory=4096&timeout=2400", inp, token)
            return r["data"]["id"], r["data"]["defaultDatasetId"]
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or i == attempts - 1:
                raise
            wait_s = 5 * 2 ** i                       # 5s, 10s, 20s
            print(f"  retry {i + 1}/{attempts - 1} cat={cat} ${lo}: "
                  f"HTTP {e.code}, sleeping {wait_s}s")
            time.sleep(wait_s)
    raise RuntimeError("unreachable")


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
    ap.add_argument("--max-price", type=int, default=None,
                    help="inclusive ceiling; omit for unbounded")
    args = ap.parse_args()
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("Set APIFY_TOKEN")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    bands = bands_for(args.min_price, args.max_price)
    ceiling = f"${args.max_price:,}" if args.max_price else "up"
    print(f"range=${args.min_price:,}-{ceiling} days={args.days} bands={len(bands)}")
    started, total, failed = [], 0, []
    for cat, lo, hi in bands:
        try:
            run_id, ds_id = start(cat, lo, hi, token, args.days)
            started.append((cat, lo, hi, run_id, ds_id))
            print(f"started cat={cat} ${lo}-{hi or 'up'} run={run_id}")
            time.sleep(1.5)          # stagger: don't fire every band in one burst
        except urllib.error.HTTPError as e:                       # noqa: PERF203
            failed.append((cat, lo, hi, f"start failed: {e}"))

    for cat, lo, hi, run_id, ds_id in started:
        st = wait(run_id, token)
        rows = download(ds_id, out, token)     # partial data from a failed run is
        total += rows                          # still real data — keep it.
        print(f"  cat={cat} ${lo}-{hi or 'up'} {st} -> {rows:,} rows")
        if st != "SUCCEEDED":
            failed.append((cat, lo, hi, st))

    # Apify bills per row RETURNED. Printing the cost every run is the cheapest
    # possible guard against a silent config regression quietly tripling spend.
    print(f"\n{total:,} rows across {len(started)} runs -> {out}")
    print(f"COST: {total:,} rows x ${PRICE_PER_ROW} = ${total * PRICE_PER_ROW:,.2f}")
    for f in failed:
        print("  WARN:", f)
    if total == 0:
        sys.exit("FAIL: scrape returned no rows at all")


if __name__ == "__main__":
    main()
