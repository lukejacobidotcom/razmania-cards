"""
Text Luke, Jason and Patrick every time RazMania sells anything.

  python3 alerts/watch.py            # one poll, send what's new
  python3 alerts/watch.py --dry-run  # print the texts, send none
  python3 alerts/watch.py --seed     # record every existing sale, silently
  python3 alerts/watch.py --test     # send one sample text and stop

Covers both Swoogo events, because RazMania sells through two of them:

  370376  attendee tickets - VIP, Adult, Child, Partners Circle - and the
          Sponsor a Kid field the campaign page feeds
  372565  exhibitor booths, a separate registration form with its own fields,
          where the buyer is a vendor and the name that matters is the company

Run it from Render Cron every few minutes (see render.yaml). It is a poller, not
a webhook, because Swoogo has no outbound webhook for a registration - the only
way to learn that money moved is to ask. One poll is two HTTP calls.

WHAT COUNTS AS A SALE
  A registrant whose registration_status is `confirmed` and whose order total is
  above zero. Abandoned carts, comped entries and $0 registrations stay quiet.
  The dollar figure is always Swoogo's own `individual_gross`, never something
  recomputed from quantity times price: those two disagree on the live data (one
  exhibitor shows 2 tables at a 1-table price), and when they disagree Swoogo's
  total is the one that was actually charged.

WHY IT CANNOT DOUBLE-COUNT
  Each buyer's order total is stored in `sales_seen` AFTER the text goes out. A
  run that finds the same total sends nothing. A crash between sending and
  storing re-sends one duplicate on the next run. That asymmetry is deliberate:
  a duplicate text is a nuisance, a missed sale is not.

WHY --seed EXISTS, AND WHY THE FIRST RUN REFUSES TO SEND
  Both events already have hundreds of paid registrants. A first run against an
  empty table would try to text about every one of them. --seed records them all
  and sends nothing; and if the table is empty and the sweep finds more than
  FIRST_RUN_GUARD sales, the run aborts and tells you to seed rather than
  emptying the Textbelt balance in one go.
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config                                                   # noqa: E402
import sms                                                      # noqa: E402
from swoogo import Swoogo, SwoogoError                          # noqa: E402

# Render's log stream is UTF-8; a Windows console is not. Never let a log line
# be the thing that kills a run.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

BATCH = os.environ.get("ALERT_BATCH", "1") != "0"
# 0 means every sale texts, which is what was asked for. It is also ~30 texts a
# day per phone right now and climbing toward the event. Raising this (e.g.
# "100" for $100) keeps booths and sponsorships loud and lets $10 kid tickets
# pass quietly - they are still recorded, and still counted in the running
# total, they just do not buzz anyone.
MIN_ALERT = int(round(float(os.environ.get("MIN_ALERT_DOLLARS", "0")) * 100))
FIRST_RUN_GUARD = int(os.environ.get("FIRST_RUN_GUARD", "5"))
SMS_LIMIT = 160          # one Textbelt credit; past this it costs two


def cents(value):
    """'1656.00' -> 165600. Anything unparseable -> 0."""
    try:
        return int(round(float(str(value).replace(",", "").strip() or 0) * 100))
    except (TypeError, ValueError):
        return 0


def usd(c, short=False):
    """165600 -> '$1,656'. short=True gives '$1.7k' for big running totals."""
    if short and c >= 1_000_000:
        return "${:,.0f}k".format(c / 100_000)
    whole, rem = divmod(abs(c), 100)
    sign = "-" if c < 0 else ""
    return "{}${:,}".format(sign, whole) if rem == 0 else \
           "{}${:,}.{:02d}".format(sign, whole, rem)


def count_of(raw):
    """A quantity field's value as an int.

    Ticket dropdowns arrive as {"id": 55620601, "value": "1"}; booth number
    fields arrive as a bare "8" or ""; a field that predates the registrant
    arrives as {"id": "", "value": null}. All of them, and any surprise fourth
    shape, must read as zero.
    """
    if isinstance(raw, dict):
        raw = raw.get("value")
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return 0


def describe(reg, ev):
    """'2 Adult, 1 VIP', or '' when nothing configured matched.

    An empty string is the honest answer when the field ids in config.py have
    drifted: the text still names the buyer and the amount, it just cannot say
    what they bought.
    """
    parts = []
    for field, label in ev["items"].items():
        n = count_of(reg.get(field))
        if n > 0:
            parts.append("{} {}".format(n, label))
    return ", ".join(parts)


# Vendors type these into the Company box when they do not have one. Leading a
# text with "N/A (Lin Zhang)" is worse than just using their name - seen live on
# the exhibitor form, so it is filtered rather than theorised about.
JUNK_COMPANY = {"", "n/a", "na", "n\\a", "none", "-", "--", ".", "self",
                "individual", "personal", "no", "null"}


def clean_company(reg):
    company = (reg.get("company") or "").strip()
    return "" if company.lower() in JUNK_COMPANY else company


def who(reg, ev):
    name = "{} {}".format(reg.get("first_name", ""),
                          reg.get("last_name", "")).strip()
    company = clean_company(reg)
    if ev["kind"] == "exhibitor" and company:
        # The vendor is the buyer here; the person is the contact on the booth.
        return "{} ({})".format(company, name) if name else company
    if company and company.lower() not in name.lower():
        return "{}, {}".format(name, company) if name else company
    return name or "Someone"


def paid_state(reg):
    p = reg.get("payment_status")
    if isinstance(p, dict):
        p = p.get("value")
    return (p or "").strip()


def trim(body, email):
    """Drop the email rather than pay a second Textbelt credit for one line."""
    if len(body) <= SMS_LIMIT or not email:
        return body
    shorter = body.replace(" | " + email, "")
    return shorter if len(shorter) < len(body) else body


def compose(kind, row, booked):
    """One sale, one text. ASCII only - see the note in the README."""
    ev = config.event(row["event_id"])
    bits = [x for x in [row["items"], usd(row["cents"])] if x]
    if kind == "increase":
        head = "{} added to their order: now {}".format(row["who"], " - ".join(bits))
    elif kind == "decrease":
        return "{}: heads up, {} dropped from {} to {}. Check Swoogo.".format(
            ev["prefix"], row["who"], usd(row["was"]), usd(row["cents"]))
    else:
        head = "{} bought {}".format(row["who"], " - ".join(bits))
    tail = " | ".join(x for x in [paid_state(row["reg"]), row["email"]] if x)
    body = "{}: {}{} | {} booked".format(
        ev["prefix"], head, (" | " + tail) if tail else "", usd(booked, short=True))
    return trim(body, row["email"])


def compose_batch(rows, booked):
    """Several sales in one poll, one text.

    Five sales is five texts times three phones is fifteen credits. During a
    push that is the difference between a balance lasting a month and lasting a
    weekend, so a burst inside one poll goes out as a single summary.
    """
    total = sum(r["cents"] - r["was"] for _, r in rows)
    head = "RazMania: {} sales, {}".format(len(rows), usd(total))
    body, listed = head, 0
    for _, r in rows:
        piece = "{}{} {}".format(" - " if listed == 0 else "; ",
                                 r["who"], usd(r["cents"]))
        if len(body) + len(piece) > SMS_LIMIT - 30:
            break
        body += piece
        listed += 1
    if listed < len(rows):
        body += "; +{} more".format(len(rows) - listed)
    return body + " | {} booked".format(usd(booked, short=True))


def sweep(sw, ev):
    """Every confirmed, paid-for registrant on one event, as alert rows."""
    out = {}
    for r in sw.registrants(ev["id"], fields=config.fields_for(ev)):
        if (r.get("registration_status") or "") != "confirmed":
            continue
        c = cents(r.get("individual_gross")) or cents(r.get("group_gross"))
        if c <= 0:
            continue
        out[int(r["id"])] = {
            "event_id": ev["id"], "kind": ev["kind"], "who": who(r, ev),
            "name": "{} {}".format(r.get("first_name", ""),
                                   r.get("last_name", "")).strip(),
            "company": clean_company(r),
            "email": r.get("email") or "", "items": describe(r, ev),
            "cents": c, "reg": r,
        }
    return out


def store(cur, rid, row):
    cur.execute(
        """INSERT INTO sales_seen
               (registrant_id, event_id, kind, name, company, email, items, cents)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (registrant_id) DO UPDATE
               SET cents = EXCLUDED.cents, items = EXCLUDED.items,
                   name = EXCLUDED.name, company = EXCLUDED.company,
                   email = EXCLUDED.email, last_seen_at = now()""",
        (rid, row["event_id"], row["kind"], row["name"], row["company"],
         row["email"], row["items"], row["cents"]))


def record(cur, rid, kind, row, body, results):
    for to, ok, detail in results:
        print("  {:>18}  {}  {}".format(to, "ok" if ok else "FAILED", detail))
        cur.execute(
            """INSERT INTO sales_alerts
                   (registrant_id, kind, cents_from, cents_to, body, recipient,
                    ok, error)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (rid, kind, row.get("was") if row else None,
             row.get("cents") if row else None, body, to, ok,
             None if ok else detail))
    return any(ok for _, ok, _ in results)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print texts, send none")
    ap.add_argument("--seed", action="store_true",
                    help="record every existing sale without texting")
    ap.add_argument("--test", action="store_true",
                    help="send one sample text to ALERT_TO and exit")
    args = ap.parse_args()

    if args.test:
        for to, ok, detail in sms.send(
                "RazMania test: this is where a sale alert lands.",
                dry_run=args.dry_run):
            print("  {:>18}  {}  {}".format(to, "ok" if ok else "FAILED", detail))
        return 0

    if not sms.recipients():
        print("ALERT_TO is empty - nobody to text.", file=sys.stderr)
        return 2

    # Looked up here, not at import, so --test needs nothing but ALERT_TO.
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute((Path(__file__).resolve().parent / "schema.sql").read_text())

    # ------------------------------------------------------------- read Swoogo
    live = {}
    try:
        sw = Swoogo()
        for ev in config.EVENTS:
            found = sweep(sw, ev)
            print("event {} ({}): {} paid registrant(s), {}".format(
                ev["id"], ev["kind"], len(found),
                usd(sum(r["cents"] for r in found.values()))))
            live.update(found)
    except SwoogoError as e:
        print("swoogo: {}".format(e), file=sys.stderr)
        return 1
    if not live:
        # An empty sweep is either a genuinely empty event or a broken read.
        # Treating it as "everyone refunded" would fire a wall of false alarms.
        print("swoogo returned no paid registrants - nothing to do", file=sys.stderr)
        return 1

    booked = sum(r["cents"] for r in live.values())

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT registrant_id, name, company, email, cents FROM sales_seen")
        seen = {int(r["registrant_id"]): r for r in cur.fetchall()}

    # ---------------------------------------------------------------- diff it
    changes = []
    for rid, row in live.items():
        was = int(seen[rid]["cents"]) if rid in seen else 0
        if row["cents"] > was:
            changes.append((rid, "new" if was == 0 else "increase", dict(row, was=was)))
        elif row["cents"] < was:
            changes.append((rid, "decrease", dict(row, was=was)))
    for rid, old in seen.items():
        if rid not in live and int(old["cents"]) > 0:
            changes.append((rid, "decrease", {
                "event_id": 0, "kind": "", "reg": {}, "items": "",
                "who": ("{} ({})".format(old["company"], old["name"]).strip()
                        if old["company"] else old["name"]) or "Someone",
                "name": old["name"], "company": old["company"],
                "email": old["email"] or "", "cents": 0,
                "was": int(old["cents"])}))

    if args.seed:
        with conn.cursor() as cur:
            for rid, row in live.items():
                store(cur, rid, row)
        print("seeded {} sale(s), {} booked - no texts sent".format(
            len(live), usd(booked)))
        return 0

    if not changes:
        print("no change - {} sale(s), {} booked".format(len(live), usd(booked)))
        return 0

    if not seen and len(changes) > FIRST_RUN_GUARD:
        print("REFUSING TO SEND: the state table is empty and there are already "
              "{} sales. That would be {} texts across {} phone(s) and empty the "
              "Textbelt balance.\nRun this once first:\n"
              "    python3 alerts/watch.py --seed".format(
                  len(changes), len(changes), len(sms.recipients())),
              file=sys.stderr)
        return 3

    # Below the threshold: recorded, counted, not texted. State is still written
    # so a quiet sale cannot resurface as a loud one after the threshold moves.
    quiet = [c for c in changes if 0 < c[2]["cents"] < MIN_ALERT]
    changes = [c for c in changes if c not in quiet]
    if quiet:
        print("{} sale(s) below the {} alert threshold - recorded, not texted"
              .format(len(quiet), usd(MIN_ALERT)))
        with conn.cursor() as cur:
            for rid, _kind, row in quiet:
                if not args.dry_run:
                    store(cur, rid, row)
    if not changes:
        return 0

    # ------------------------------------------------- send, THEN record state
    if BATCH and len(changes) > 1:
        rows = [(rid, row) for rid, _, row in changes]
        body = compose_batch(rows, booked)
        results = sms.send(body, dry_run=args.dry_run)
        print("[batch x{}] {}".format(len(rows), body))
        with conn.cursor() as cur:
            delivered = record(cur, None, "batch", None, body, results)
            if args.dry_run:
                return 0
            if not delivered:
                print("  !! no recipient reached - will retry next run",
                      file=sys.stderr)
                return 1
            for rid, row in rows:
                if row["cents"] == 0:
                    cur.execute("DELETE FROM sales_seen WHERE registrant_id = %s",
                                (rid,))
                else:
                    store(cur, rid, row)
        return 0

    for rid, kind, row in changes:
        body = compose(kind, row, booked)
        results = sms.send(body, dry_run=args.dry_run)
        print("[{}] {}".format(kind, body))
        with conn.cursor() as cur:
            delivered = record(cur, rid, kind, row, body, results)
            if args.dry_run:
                continue
            if not delivered:
                # Leave the old total in place so the next run tries again
                # rather than marking a sale as announced.
                print("  !! no recipient reached - will retry next run",
                      file=sys.stderr)
                continue
            if row["cents"] == 0:
                cur.execute("DELETE FROM sales_seen WHERE registrant_id = %s", (rid,))
            else:
                store(cur, rid, row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
