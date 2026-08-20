"""
Minimal Swoogo REST client — stdlib only, no new dependencies.

Swoogo issues an OAuth2 client-credentials token that lives about an hour, so a
cron process that runs for five seconds gets a fresh one every time and never
needs to persist it.

  export SWOOGO_KEY=...  SWOOGO_SECRET=...
  python3 -c "from swoogo import Swoogo; print(len(list(Swoogo().registrants(370376))))"

Why a full sweep and not a filtered query: Swoogo's `search` parameter silently
IGNORES comparisons against custom fields. `c_9161333>55620600` was tested live
and returned all 136 registrants unfiltered — the filter looks like it works and
does nothing. Every quantity is therefore fetched and compared here, in Python,
where the comparison is real. At this event's size that is one HTTP call.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("SWOOGO_API", "https://api.swoogo.com/api/v1")
TIMEOUT = int(os.environ.get("SWOOGO_TIMEOUT", "30"))


class SwoogoError(RuntimeError):
    pass


RETRIES = int(os.environ.get("SWOOGO_RETRIES", "3"))
BACKOFF = (1, 4, 10)          # seconds; well inside a five-minute cron slot


def _request(url, data=None, headers=None, method=None):
    """One HTTP call, retried on the failures that are worth retrying.

    A blip - a 502, a reset connection, a slow DNS answer - used to cost the
    whole poll. The next run five minutes later would recover, so nothing was
    lost, but a sale sat unannounced for ten minutes instead of five for no
    good reason. 5xx and network errors are retried; 4xx are not, because a 401
    or a 404 will still be a 401 or a 404 in four seconds.
    """
    last = None
    for attempt in range(RETRIES):
        req = urllib.request.Request(url, data=data, headers=headers or {},
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:500]
            if e.code < 500:
                return e.code, {"_error": body}
            last = f"HTTP {e.code}: {body[:200]}"
        except urllib.error.URLError as e:
            last = f"network error: {e.reason}"
        except (TimeoutError, OSError) as e:
            last = f"socket error: {e}"
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
    raise SwoogoError(f"{url} failed after {RETRIES} attempts - {last}")


class Swoogo:
    def __init__(self, key=None, secret=None):
        self.key = key or os.environ.get("SWOOGO_KEY", "")
        self.secret = secret or os.environ.get("SWOOGO_SECRET", "")
        if not self.key or not self.secret:
            raise SwoogoError("set SWOOGO_KEY and SWOOGO_SECRET "
                              "(Swoogo > your name, top right > My Profile > API Credentials)")
        self._tok = None
        self._exp = 0.0

    def token(self):
        if self._tok and time.time() < self._exp:
            return self._tok
        basic = base64.b64encode(f"{self.key}:{self.secret}".encode()).decode()
        status, body = _request(
            f"{BASE}/oauth2/token",
            data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST")
        if status != 200 or "access_token" not in body:
            raise SwoogoError(f"token request failed ({status}): {body}")
        self._tok = body["access_token"]
        # Refresh a minute early rather than discover expiry mid-sweep.
        self._exp = time.time() + int(body.get("expires_in", 3600)) - 60
        return self._tok

    def get(self, path, **params):
        url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
        status, body = _request(url, headers={"Authorization": f"Bearer {self.token()}"})
        if status == 401:                      # token rotated or revoked — retry once
            self._tok, self._exp = None, 0.0
            status, body = _request(url, headers={"Authorization": f"Bearer {self.token()}"})
        if status != 200:
            raise SwoogoError(f"GET {path} failed ({status}): {body}")
        return body

    def registrants(self, event_id, fields=None, per_page=250):
        """Yield every registrant for an event, one page at a time.

        Swoogo returns Yii-flavoured pagination (`items` plus `_meta`), but the
        page loop does not trust it: it also stops on a short page and on a
        repeated first id, so a schema change upstream degrades into "we read
        one page" rather than an infinite loop against a paid API.
        """
        page, seen_first = 1, None
        while True:
            args = {"event_id": event_id, "page": page, "per-page": per_page}
            if fields:
                args["fields"] = fields
            body = self.get("registrants.json", **args)
            items = body.get("items", body) if isinstance(body, dict) else body
            if not isinstance(items, list) or not items:
                return
            if seen_first is not None and items[0].get("id") == seen_first:
                return                          # server ignored `page` — bail out
            seen_first = items[0].get("id")
            for it in items:
                yield it
            meta = body.get("_meta") if isinstance(body, dict) else None
            if meta and meta.get("pageCount") and page >= int(meta["pageCount"]):
                return
            if len(items) < per_page:
                return
            page += 1
