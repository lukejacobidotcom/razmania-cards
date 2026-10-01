#!/usr/bin/env python3
"""
Validate firsts/firsts.json, copy it into the WordPress plugin, and zip the
plugin for upload.

    python firsts/build.py            # validate + copy + zip
    python firsts/build.py --check    # validate only (what the test runs)

The JSON is the single source of truth for the historical half of the series.
The plugin ships a copy in data/ because razmania.com renders it server-side
with no API call; the tracked half comes from /v1/firsts at request time.

Validation is strict on purpose. An entry that claims to be verified but has no
date, or a "first" with a price and no card, is exactly the kind of thing that
reads fine in a JSON file and prints as a false fact on the page.
"""

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(HERE, "firsts.json")
PLUGIN = os.path.join(REPO, "wordpress", "razmania-firsts")
DEST = os.path.join(PLUGIN, "data", "firsts.json")

STATUSES = {"verified", "reported", "open", "not_yet"}
DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
VERTICALS = ["Baseball", "Football", "Basketball", "Pokemon"]


def validate(d):
    errs = []
    e = errs.append
    s = d.get("series") or {}
    ms = s.get("milestones")
    if ms != [10000, 100000, 1000000, 10000000]:
        e(f"series.milestones must be the four lines, got {ms}")
    for k in ("name", "tagline", "dek", "updated", "rules", "statuses"):
        if not s.get(k):
            e(f"series.{k} missing")
    if s.get("updated") and not DATE.match(s["updated"]):
        e("series.updated must be YYYY-MM-DD")

    def check_first(where, f):
        st = f.get("status")
        if st not in STATUSES:
            e(f"{where}: bad status {st!r}")
            return
        thr = f.get("threshold")
        if thr not in (ms or []):
            e(f"{where}: threshold {thr} is not a series milestone")
        if st in ("verified", "reported"):
            for k in ("card", "date"):
                if not f.get(k):
                    e(f"{where}: {st} entry needs {k}")
            if not isinstance(f.get("price"), (int, float)) or f["price"] < thr:
                e(f"{where}: {st} entry needs a price >= its threshold")
            if f.get("date") and not DATE.match(f["date"]):
                e(f"{where}: date {f['date']!r} must be YYYY, YYYY-MM or YYYY-MM-DD")
            if st == "verified" and len(f.get("sources") or []) < 2:
                e(f"{where}: verified needs two sources")
            if st == "reported" and not f.get("note"):
                e(f"{where}: reported must say in the note what is unconfirmed")
        if st == "open" and not f.get("note"):
            e(f"{where}: open entry needs a note saying what is known")
        if st == "not_yet" and f.get("price") is not None:
            e(f"{where}: not_yet cannot carry a price")

    def check_group(where, g, need_vertical):
        firsts = g.get("firsts") or []
        if [f.get("threshold") for f in firsts] != ms:
            e(f"{where}: must list exactly the four milestones in order")
        for f in firsts:
            check_first(f"{where} ${f.get('threshold')}", f)
        if any(f.get("status") == "not_yet" for f in firsts):
            r = g.get("record") or {}
            if not (r.get("card") and isinstance(r.get("price"), (int, float)) and r.get("date")):
                e(f"{where}: has a not_yet line, so needs a record (card, price, date)")
        if need_vertical and g.get("vertical") not in VERTICALS:
            e(f"{where}: vertical must be one of {VERTICALS}")

    vs = d.get("verticals") or []
    if [v.get("vertical") for v in vs] != VERTICALS:
        e(f"verticals must be exactly {VERTICALS} in that order")
    for v in vs:
        for k in ("label", "slug", "universe"):
            if not v.get(k):
                e(f"{v.get('vertical')}: {k} missing")
        check_group(v.get("vertical", "?"), v, True)
    for c in d.get("cards") or []:
        if not c.get("subject") or not c.get("card"):
            e("cards[]: subject and card required")
        check_group(f"card {c.get('subject')}", c, True)
    return errs


def main():
    check_only = "--check" in sys.argv
    with open(SRC, encoding="utf-8") as fh:
        d = json.load(fh)
    errs = validate(d)
    if errs:
        for x in errs:
            print("ERROR:", x)
        sys.exit(f"{len(errs)} problem(s) in {SRC}")
    n_first = sum(1 for v in d["verticals"] for f in v["firsts"] if f["status"] in ("verified", "reported"))
    n_open = sum(1 for v in d["verticals"] for f in v["firsts"] if f["status"] == "open")
    print(f"firsts.json ok: {len(d['verticals'])} categories, {n_first} firsts filled, "
          f"{n_open} open, {len(d.get('cards', []))} card profiles")
    if check_only:
        return
    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    with open(DEST, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"copied -> {os.path.relpath(DEST, REPO)}")
    subprocess.check_call([sys.executable, os.path.join(REPO, "wordpress", "build_zip.py"), "razmania-firsts"])


if __name__ == "__main__":
    main()
