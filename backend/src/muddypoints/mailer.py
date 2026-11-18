"""Outgoing email. Provider is picked from the environment:

1. BREVO_API_KEY + MAIL_FROM  -> Brevo transactional email API (https://www.brevo.com, 300 mails/day free)
2. MAIL_FROM only             -> Amazon SES (sender must be verified in SES)
3. nothing                    -> emails are only written to the log (dev mode; reset links appear there)
"""
import json
import logging
import urllib.request

from . import aws
from .config import settings
from .log import get_logger, log_event

logger = get_logger(__name__)
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def _send_brevo(to: str, subject: str, body: str, reply_to: str | None = None) -> None:
    payload = {
        "sender": {"email": settings.mail_from, "name": settings.mail_from_name},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": body,
    }
    if reply_to:
        payload["replyTo"] = {"email": reply_to}
    request = urllib.request.Request(
        BREVO_URL, data=json.dumps(payload).encode(), method="POST",
        headers={"api-key": settings.brevo_api_key, "content-type": "application/json", "accept": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"Brevo returned HTTP {resp.status}")


def _send_ses(to: str, subject: str, body: str, reply_to: str | None = None) -> None:
    aws.ses().send_email(
        Source=f"{settings.mail_from_name} <{settings.mail_from}>",
        Destination={"ToAddresses": [to]},
        **({"ReplyToAddresses": [reply_to]} if reply_to else {}),
        Message={"Subject": {"Data": subject, "Charset": "UTF-8"},
                 "Body": {"Text": {"Data": body, "Charset": "UTF-8"}}},
    )


def send(to: str, subject: str, body: str, reply_to: str | None = None) -> bool:
    if not settings.mail_from:
        # Dev mode: the full body (incl. reset links) goes to the log so you can click it locally.
        log_event(logger, logging.WARNING, "email not sent (MAIL_FROM unset)", to=to, subject=subject, body=body)
        return False
    provider = "brevo" if settings.brevo_api_key else "ses"
    try:
        (_send_brevo if provider == "brevo" else _send_ses)(to, subject, body, reply_to)
        log_event(logger, logging.INFO, "email sent", to=to, subject=subject, provider=provider)
        return True
    except Exception as exc:  # never fail the request because of email problems
        detail = exc.read().decode(errors="replace")[:500] if hasattr(exc, "read") else repr(exc)
        log_event(logger, logging.ERROR, "email sending failed", to=to, subject=subject, provider=provider,
                  error=detail)
        return False
