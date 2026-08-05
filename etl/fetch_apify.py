"""
Pull Apify dataset items to local JSONL.

Usage:
    export APIFY_TOKEN=apify_api_xxx
    python3 fetch_apify.py aJjWFyBpfFTCou5dI YbfombbCNGL2zXUMa ...

Writes raw/<datasetId>.jsonl (one JSON object per line).
Paginates at 1000 rows/request so datasets of any size stream to disk
without ever being held in memory.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.apify.com/v2/datasets/{ds}/items"
PAGE = 1000
RAW = Path(__file__).resolve().parent / "raw"


def fetch_dataset(ds: str, token: str) -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"{ds}.jsonl"
    total = 0
    with open(out, "w") as fh:
        offset = 0
        while True:
            qs = urllib.parse.urlencode({
                "token": token, "offset": offset, "limit": PAGE,
                "format": "json", "clean": "true",
            })
            req = urllib.request.Request(f"{API.format(ds=ds)}?{qs}")
            for attempt in range(5):
                try:
                    with urllib.request.urlopen(req, timeout=120) as r:
                        rows = json.loads(r.read())
                    break
                except Exception as e:                      # noqa: BLE001
                    if attempt == 4:
                        raise
                    print(f"  retry {attempt + 1} after {e}")
                    time.sleep(2 ** attempt)
            if not rows:
                break
            for row in rows:
                fh.write(json.dumps(row) + "\n")
            total += len(rows)
            offset += len(rows)
            print(f"  {ds}: {total:,} rows", end="\r")
            if len(rows) < PAGE:
                break
    print(f"  {ds}: {total:,} rows -> {out}")
    return total


def main():
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("Set APIFY_TOKEN first (Apify Console -> Settings -> API & Integrations).")
    ids = sys.argv[1:]
    if not ids:
        sys.exit("Pass one or more dataset IDs.")
    grand = sum(fetch_dataset(d, token) for d in ids)
    print(f"\nTotal: {grand:,} rows across {len(ids)} datasets.")


if __name__ == "__main__":
    main()
