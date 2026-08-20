"""
Send a short text to a fixed list of people. Stdlib only.

Two backends, chosen with SMS_VIA:

  textbelt (default)  One HTTP POST per number, one credit per 160 characters.
                      No account, no phone number to rent, and crucially no A2P
                      10DLC registration to wait on — which is the whole reason
                      it is the default here.

  email               Free. Sends to the carrier's email-to-SMS gateway
                      (5551234567@vtext.com and friends). No cost and no
                      delivery guarantee whatsoever: carriers throttle and
                      silently drop these. A stopgap, not an alert path for
                      money.

Recipients come from ALERT_TO, comma separated. Under `textbelt` they are phone
numbers in any format — 5551234567, (248) 207-0141 and +15551234567 all
normalise to the same thing. Under `email` they are gateway addresses.

TEXTBELT CREDITS ARE FINITE AND SILENT WHEN THEY RUN OUT.
Every alert costs one credit PER RECIPIENT, so three phones is three credits an
alert. Textbelt returns HTTP 200 with {"success": false} when the balance hits
zero — it looks exactly like a successful call unless you read the body, which
is why _textbelt() checks `success` and not the status code. Remaining quota is
logged on every send and warned about below TEXTBELT_LOW_QUOTA.

  python3 -c "import sms; print(sms.quota())"
"""

import json
import os
import re
import smtplib
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

VIA = os.environ.get("SMS_VIA", "textbelt").strip().lower()
TIMEOUT = int(os.environ.get("SMS_TIMEOUT", "20"))
LOW_QUOTA = int(os.environ.get("TEXTBELT_LOW_QUOTA", "50"))
TEXTBELT_URL = os.environ.get("TEXTBELT_URL", "https://textbelt.com")

# Common US carrier gateways, for the `email` backend. Verizon and T-Mobile are
# the reliable two; AT&T drops a lot.
GATEWAYS = {
    "verizon": "vtext.com", "att": "txt.att.net", "tmobile": "tmomail.net",
    "sprint": "messaging.sprintpcs.com", "uscellular": "email.uscc.net",
    "googlefi": "msg.fi.google.com",
}


def recipients():
    return [r.strip() for r in os.environ.get("ALERT_TO", "").split(",") if r.strip()]


def normalise(phone):
    """Any US phone format -> +1XXXXXXXXXX.

    Textbelt accepts bare digits, but normalising here means ALERT_TO can be
    written however it was pasted out of a contact card and two spellings of the
    same number cannot become two texts.
    """
    d = re.sub(r"\D", "", phone)
    if len(d) == 10:
        return "+1" + d
    if len(d) == 11 and d.startswith("1"):
        return "+" + d
    return phone.strip()          # not a US number; hand it over untouched


def _post(url, form):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(form).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def quota():
    """Credits left on the Textbelt key. A GET; sends nothing and costs nothing."""
    key = os.environ.get("TEXTBELT_KEY", "")
    if not key:
        return None
    try:
        with urllib.request.urlopen(f"{TEXTBELT_URL}/quota/{key}", timeout=TIMEOUT) as r:
            return json.loads(r.read().decode()).get("quotaRemaining")
    except Exception:                                            # noqa: BLE001
        return None


def _textbelt(to, body):
    key = os.environ.get("TEXTBELT_KEY", "")
    if not key:
        raise RuntimeError("set TEXTBELT_KEY (textbelt.com dashboard)")
    try:
        res = _post(f"{TEXTBELT_URL}/text",
                    {"phone": normalise(to), "message": body, "key": key})
    except urllib.error.HTTPError as e:
        raise RuntimeError("textbelt {}: {}".format(
            e.code, e.read().decode("utf-8", "replace")[:200])) from e
    # Textbelt answers 200 whether it sent or not. `success` is the only truth.
    if not res.get("success"):
        raise RuntimeError("textbelt refused: {}".format(res.get("error", res))[:300])
    left = res.get("quotaRemaining")
    if isinstance(left, int) and left <= LOW_QUOTA:
        print("  !! textbelt credits low: {} left. Top up at textbelt.com "
              "before the alerts go quiet.".format(left), file=sys.stderr)
    return "{} (quota {})".format(res.get("textId", "sent"), left)


def _email(to, body):
    host = os.environ.get("SMTP_HOST", "")
    if not host:
        raise RuntimeError("set SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/SMTP_FROM")
    msg = EmailMessage()
    msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", ""))
    msg["To"] = to
    msg["Subject"] = ""            # gateways prepend the subject to the SMS body
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")),
                      timeout=TIMEOUT) as s:
        s.starttls()
        user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
        if user and pw:
            s.login(user, pw)
        s.send_message(msg)
    return "sent"


def send(body, to=None, dry_run=False):
    """Text everyone. Returns [(recipient, ok, detail)] - never raises.

    One dead number, or a Textbelt balance that ran out mid-list, must not stop
    the other two people's alert. Every failure is caught per recipient and
    handed back for the audit table.
    """
    out = []
    for r in (to or recipients()):
        if dry_run:
            out.append((r, True, "dry-run"))
            continue
        try:
            out.append((r, True, _textbelt(r, body) if VIA == "textbelt"
                        else _email(r, body)))
        except Exception as e:                                   # noqa: BLE001
            out.append((r, False, str(e)[:300]))
    return out
