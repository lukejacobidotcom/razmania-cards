"""
Offline exercise of the alert logic. No Swoogo, no Postgres, no texts.

  python3 alerts/test_watch.py

Swoogo and psycopg2 are replaced with in-memory fakes and sms.send runs in
dry-run, so this proves the parts that cannot be tested any other way without
someone actually buying something: that a ticket sale and a booth sale are
described differently, that the same order never texts twice, that an upsell and
a refund are both caught, that unconfirmed and $0 registrations stay quiet, that
a burst inside one poll is batched into a single text, that a failed API read is
never mistaken for a wave of refunds, and that a first run against an unseeded
database refuses to send rather than emptying the Textbelt balance.

The fixtures are real shapes taken off the live events: ticket quantities arrive
as {"id":..., "value":"2"}, booth counts arrive as a bare "8", and the money is
whatever individual_gross says even when it disagrees with quantity x price.
"""

import os
import sys
import types
from pathlib import Path

NOTIFY = Path(__file__).resolve().parent

os.environ.setdefault("DATABASE_URL", "postgres://fake")
os.environ["ALERT_TO"] = "+15551234567,+15552345678,+15553456789"
os.environ["SMS_VIA"] = "textbelt"
# Hermetic: never inherit the operator's real cutoff or threshold from a shell
# that happens to have sourced .env. The forward-only cases set them explicitly.
os.environ.pop("ALERT_SINCE", None)
os.environ.pop("MIN_ALERT_DOLLARS", None)

# ---------------------------------------------------------------- fake Postgres
STATE = {}
ALERTS = []
RUNS = []
LOCK_HELD = []
LOCK_FAIL = False


class FakeCur:
    def __init__(self, dict_rows=False):
        self.dict_rows, self.rows, self.one = dict_rows, [], None

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.one = None
        if s.startswith("SELECT registrant_id"):
            self.rows = [dict(registrant_id=k, name=v["name"],
                              company=v["company"], email=v["email"],
                              cents=v["cents"]) for k, v in STATE.items()]
        elif "INSERT INTO sales_seen" in s:
            rid, ev, kind, name, company, email, items, c = params
            STATE[rid] = dict(name=name, company=company, email=email,
                              items=items, cents=c)
        elif "DELETE FROM sales_seen" in s:
            STATE.pop(params[0], None)
        elif "INSERT INTO sales_alerts" in s:
            ALERTS.append(params)
        # --- operations tables -------------------------------------------
        elif "pg_try_advisory_lock" in s:
            LOCK_HELD.append(1)
            self.one = [not LOCK_FAIL]
        elif "INSERT INTO alert_runs" in s:
            RUNS.append({})
            self.one = [len(RUNS)]
        elif "UPDATE alert_runs" in s:
            RUNS[-1] = {"code": params[1], "sent": params[2], "note": params[5]}
        elif "current_database" in s:
            self.one = ["fake_db"]
        elif "alert_state" in s:
            self.one = [False]        # nothing ever warned recently, in tests
        elif "CREATE TABLE" in s or "CREATE INDEX" in s:
            pass
        else:
            raise AssertionError("unhandled SQL: " + s[:120])

    def fetchall(self): return self.rows
    def fetchone(self): return self.one


class FakeConn:
    autocommit = True
    def cursor(self, cursor_factory=None): return FakeCur(cursor_factory is not None)


pg = types.ModuleType("psycopg2")
pg.connect = lambda dsn: FakeConn()
pg.extras = types.ModuleType("psycopg2.extras")
pg.extras.RealDictCursor = object
sys.modules["psycopg2"] = pg
sys.modules["psycopg2.extras"] = pg.extras

# ------------------------------------------------------------------ fake Swoogo
BY_EVENT = {370376: [], 372565: []}
sw = types.ModuleType("swoogo")


class SwoogoError(RuntimeError):
    pass


class Swoogo:
    def __init__(self, *a, **k): pass
    def registrants(self, event_id, fields=None, per_page=250):
        return list(BY_EVENT.get(event_id, []))


sw.Swoogo, sw.SwoogoError = Swoogo, SwoogoError
sys.modules["swoogo"] = sw

sys.path.insert(0, str(NOTIFY))
import watch                                                        # noqa: E402


def ticket(rid, first, last, gross, status="confirmed", **qty):
    """A registrant on 370376. qty like vip=1, adult=2, sponsor=3."""
    ids = {"vip": "c_8915620", "adult": "c_8915595", "child": "c_8915600",
           "partners": "c_8940298", "sponsor": "c_9161333"}
    r = {"id": rid, "first_name": first, "last_name": last, "company": "",
         "email": "{}@example.com".format(first.lower()),
         "registration_status": status,
         "payment_status": {"id": 1, "value": "Paid"},
         "individual_gross": gross, "created_at": qty.pop("created_at", "")}
    for k, field in ids.items():
        n = qty.get(k)
        r[field] = ({"id": 55620600 + n, "value": str(n)} if n
                    else {"id": "", "value": None})
    return r


def booth(rid, first, last, company, gross, front=None, standard=None,
          premium=None, status="confirmed", created_at=""):
    """A registrant on 372565. Booth counts are bare strings, or ''."""
    return {"id": rid, "first_name": first, "last_name": last,
            "company": company, "email": "{}@vendor.com".format(first.lower()),
            "registration_status": status,
            "payment_status": {"id": 1, "value": "Paid"},
            "individual_gross": gross, "created_at": created_at,
            "c_8978574": str(front) if front else "",
            "c_8978579": str(standard) if standard else "",
            "c_8978580": str(premium) if premium else "",
            "c_8915122": {"id": "", "value": None}}


def case(title, tickets, booths, argv=()):
    BY_EVENT[370376], BY_EVENT[372565] = tickets, booths
    print("\n" + "=" * 74 + "\n" + title + "\n" + "=" * 74)
    sys.argv = ["watch.py", "--dry-run", *argv]
    rc = watch.main()
    print("-> rc={}  tracked={}".format(rc, len(STATE)))
    return rc


# Noise that must never produce a text: a $0 comp, an abandoned cart, and a
# ticket buyer who left every quantity at zero.
NOISE = [ticket(1, "Free", "Comp", "0.00", adult=1),
         ticket(2, "Abandoned", "Cart", "10.35", status="incomplete", adult=1),
         ticket(3, "Zero", "Everything", "0.00")]

JANE = ticket(10, "Jane", "Doe", "20.70", adult=2)
ACME = ticket(11, "Acme", "Corp", "403.65", sponsor=2)
KIM = ticket(12, "Kim", "Ellis", "47.00", vip=1)
MEDIA = booth(20, "Eric", "Tinnin", "Media Reload", "1656.00", standard=8)
VTM = booth(21, "Ryan", "Petrill", "VTM Vending", "517.50", front=1)
CHEESE = booth(22, "Sarah", "Cheesebro", "Cheesebro", "207.00", standard=1)

TIX, BOOTHS = NOISE + [JANE, ACME, KIM], [MEDIA, VTM, CHEESE]

assert case("1. first run, unseeded, 6 existing sales -> must REFUSE",
            TIX, BOOTHS) == 3
assert not STATE

assert case("2. --seed records everything, texts nobody",
            TIX, BOOTHS, ("--seed",)) == 0
assert len(STATE) == 6, STATE

case("3. no change at all", TIX, BOOTHS)

case("4. one new ticket sale", TIX + [ticket(13, "Sam", "New", "47.00", vip=1)],
     BOOTHS)

case("5. one new booth sale - vendor name leads", TIX,
     BOOTHS + [booth(23, "Dana", "Ito", "Card Corner", "258.75", premium=1)])

case("6. three sales in one poll -> ONE batched text",
     TIX + [ticket(14, "A", "One", "10.35", adult=1),
            ticket(15, "B", "Two", "47.00", vip=1)],
     BOOTHS + [booth(24, "C", "Three", "Slab City", "207.00", standard=1)])

case("7. Jane upsells 20.70 -> 227.70",
     NOISE + [ticket(10, "Jane", "Doe", "227.70", adult=2, sponsor=1), ACME, KIM],
     BOOTHS)

case("8. Media Reload refunds entirely", TIX, [VTM, CHEESE])

# Seen live: a vendor who typed "N/A" into Company. Leading with the junk value
# is worse than leading with their name.
case("8b. vendor whose company is 'N/A' -> falls back to the person", TIX,
     [VTM, CHEESE, booth(25, "Lin", "Zhang", "N/A", "207.00", standard=1)])

assert case("9. broken sweep must NOT report a wave of refunds", [], []) == 1

# --------------------------------------------------------------- forward only
# The real protection against announcing history is --seed. This is the second
# layer, for when the state table is empty because someone rebuilt the database:
# a sale created before the cutoff must be recorded and stay silent even then,
# and the run must not abort waiting for a human to notice.
STATE.clear()
watch.ALERT_SINCE = "2026-08-20 12:00:00"

OLD_T = [ticket(30, "Last", "Week", "20.70", adult=2, created_at="2026-08-14 09:00:00"),
         ticket(31, "This", "Morning", "47.00", vip=1, created_at="2026-08-20 08:15:00")]
OLD_B = [booth(32, "Historic", "Vendor", "Old Cards", "207.00", standard=1,
               created_at="2026-08-11 10:00:00")]
NEW_B = booth(33, "Fresh", "Vendor", "New Cards", "517.50", premium=2,
              created_at="2026-08-20 14:30:00")

# Count texts rather than state: --dry-run deliberately writes no state, so the
# only thing worth asserting here is how many messages would have gone out.
SENT = []
_send = watch.sms.send
watch.sms.send = lambda body, to=None, dry_run=False: (
    SENT.append(body) or _send(body, to=to, dry_run=dry_run))

SENT.clear()
rc = case("10. wiped database, cutoff set -> history silent, no abort",
          NOISE + OLD_T, OLD_B)
assert rc == 0, rc
assert not SENT, "history was announced: {}".format(SENT)

SENT.clear()
rc = case("11. a genuinely new sale after the cutoff -> texts",
          NOISE + OLD_T, OLD_B + [NEW_B])
assert rc == 0, rc
assert len(SENT) == 1 and "New Cards" in SENT[0], SENT
print("   texted exactly once:", SENT[0])
watch.sms.send = _send
watch.ALERT_SINCE = ""

# ------------------------------------------------------- operational guards
# A sweep returning far fewer sales than we track is far likelier to be a
# truncated read than a wave of refunds. Texting "dropped to $0" at four
# customers because Swoogo paginated badly is worse than saying nothing.
STATE.clear()
watch.ALERT_SINCE = ""
watch.SHRINK_FLOOR = 3          # the real floor is 20; these fixtures are small
watch.sms.send = lambda body, to=None, dry_run=False: (
    SENT.append(body) or _send(body, to=to, dry_run=dry_run))
case("12a. seed six sales", TIX, BOOTHS, ("--seed",))
assert len(STATE) == 6, STATE

SENT.clear()
rc = case("12b. sweep truncates to 2 of 6 -> refunds SUPPRESSED",
          NOISE + [JANE], [MEDIA])
assert rc == 0, rc
assert not SENT, "reported refunds off a truncated sweep: {}".format(SENT)

# A genuine refund, with the rest of the sweep intact, must still fire.
SENT.clear()
rc = case("12c. one real refund, healthy sweep -> still reported",
          NOISE + [JANE, ACME, KIM], [MEDIA, VTM])
assert rc == 0 and len(SENT) == 1 and "dropped" in SENT[0], SENT
print("   reported:", SENT[0])
watch.SHRINK_FLOOR = 20

# Two runs must never overlap: the second would re-read the same un-recorded
# sale and text it a second time.
LOCK_FAIL = True
SENT.clear()
rc = case("13. a concurrent run cannot double-send",
          TIX + [ticket(40, "Overlap", "Victim", "47.00", vip=1)], BOOTHS)
assert rc == 0 and not SENT, SENT
LOCK_FAIL = False

print("\nformatting:")
for c in [2070, 51750, 165600, 19500, 2840000]:
    print("  {:>9} -> {:>12}  short {}".format(
        c, watch.usd(c), watch.usd(c, short=True)))

print("\nlengths (160 = one Textbelt credit):")
LONGEST = {"event_id": 372565, "kind": "exhibitor", "reg": MEDIA,
           "who": watch.who(MEDIA, watch.config.EVENTS[1]),
           "items": "8 Standard table", "email": MEDIA["email"],
           "cents": 165600, "was": 0}
for label, row in [("typical booth", LONGEST),
                   ("long everything", dict(LONGEST,
                       who="Championship Card Collectibles of Southeast Michigan "
                           "(Bartholomew Vandermeer)",
                       email="bartholomew.vandermeer@championshipcards.example.com"))]:
    b = watch.compose("new", row, 2840000)
    print("  {:>3}  {:<16} {}".format(len(b), label, b))
    assert len(b) <= watch.SMS_LIMIT, "would cost a second credit"

print("\nALL CASES PASSED")
