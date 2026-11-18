"""Optional live check against the real Brevo API. Skipped unless explicitly enabled; never runs in CI.

    BREVO_LIVE_KEY=... BREVO_LIVE_FROM=verified-sender@... BREVO_LIVE_TO=you@... pytest tests/test_brevo_live.py

It sends one real mail through mailer.send() and then asks Brevo's event log what happened. That second step
matters: Brevo answers 201 and rejects bad senders or blocked IPs afterwards, so a 201 alone proves nothing.
"""
import json
import os
import time
import urllib.parse
import urllib.request

import pytest

from muddypoints import mailer

KEY, FROM, TO = (os.environ.get(k) for k in ("BREVO_LIVE_KEY", "BREVO_LIVE_FROM", "BREVO_LIVE_TO"))
pytestmark = pytest.mark.skipif(not (KEY and FROM and TO), reason="set BREVO_LIVE_KEY, BREVO_LIVE_FROM, BREVO_LIVE_TO")
BAD = {"error", "blocked", "invalid", "hardBounces", "softBounces", "spam"}


def _events() -> list[dict]:
    url = "https://api.brevo.com/v3/smtp/statistics/events?" + urllib.parse.urlencode(
        {"email": TO, "limit": 10, "sort": "desc"})
    req = urllib.request.Request(url, headers={"api-key": KEY, "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp).get("events", [])


def test_real_mail_is_accepted_and_not_rejected(monkeypatch):
    monkeypatch.setenv("MAIL_FROM", FROM)
    monkeypatch.setenv("BREVO_API_KEY", KEY)
    subject = f"Muddy Points live test {int(time.time())}"
    assert mailer.send(TO, subject, "Live test from the test suite.\nhttps://example.org/link\n") is True
    seen: set[str] = set()
    for _ in range(10):                                   # Brevo reports the outcome within a few seconds
        time.sleep(2)
        seen = {e["event"] for e in _events() if e.get("subject") == subject}
        if seen & (BAD | {"delivered"}):
            break
    assert not seen & BAD, f"Brevo rejected the mail: {sorted(seen)}"
    assert "requests" in seen
