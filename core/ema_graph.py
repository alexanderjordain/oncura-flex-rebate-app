"""Microsoft Graph — APP-ONLY (client-credentials) client for the EMA renewal bot.

Distinct from ``core/graph_email.py``, which uses *delegated* auth (a human signs
in per browser session to create Outlook drafts). This module authenticates as
the **application itself** (no user, no interactive sign-in), which is the only
way a headless Render cron can:

  1. create a real calendar event on Mark's Outlook calendar and invite the
     clinic (Graph emails the invitation automatically),
  2. send the branded renewal email, and
  3. cancel the event (notifying the clinic) once the clinic pays.

Why app-only and not the delegated module: a cron has no browser and no user to
click "Connect Outlook", so the delegated authorization-code flow cannot run.
Client-credentials mints a token from the app's own secret.

Config (env vars on Render; loaded into env from secrets for local dev):
  GRAPH_TENANT_ID      the Oncura M365 tenant (GUID or domain)
  GRAPH_CLIENT_ID      the app registration's Application (client) ID
  GRAPH_CLIENT_SECRET  a client secret on that registration

Required Entra app registration (see docs/EMA_GRAPH_SETUP.md):
  - API permissions: **Application** (not delegated) Mail.Send + Calendars.ReadWrite,
    with tenant-admin consent granted.
  - Strongly recommended: an ApplicationAccessPolicy restricting the app to just
    the organizer/sender mailbox, so the (tenant-wide) app permissions can't be
    used to send as or read anyone else.

Nothing here is Streamlit-aware — it runs anywhere Python + requests + msal do.
GET/POST to Graph only; never touches OPD or QBO.
"""
from __future__ import annotations

import datetime as dt
import os

import requests

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPE = ["https://graph.microsoft.com/.default"]
# Graph accepts Windows time-zone names; "Eastern Standard Time" correctly
# observes EDT/EST, so call times land on the right wall-clock hour year-round.
DEFAULT_TZ = "Eastern Standard Time"

_TIMEOUT = 30


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def is_configured() -> bool:
    return bool(_cfg("GRAPH_TENANT_ID") and _cfg("GRAPH_CLIENT_ID") and _cfg("GRAPH_CLIENT_SECRET"))


def _authority() -> str:
    return f"https://login.microsoftonline.com/{_cfg('GRAPH_TENANT_ID')}"


def _token() -> str:
    """Acquire an app-only access token via client credentials. Raises on failure
    so a misconfigured cron fails loudly rather than silently sending nothing."""
    import msal

    app = msal.ConfidentialClientApplication(
        client_id=_cfg("GRAPH_CLIENT_ID"),
        authority=_authority(),
        client_credential=_cfg("GRAPH_CLIENT_SECRET"),
    )
    # client-credentials has no user, so acquire_token_silent has no account to
    # match; acquire_token_for_client manages its own in-memory cache.
    result = app.acquire_token_for_client(scopes=_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(
            f"Graph app-only token failed: {result.get('error')}: "
            f"{result.get('error_description', '')[:300]}"
        )
    return result["access_token"]


def _headers(token: str | None = None) -> dict:
    return {"Authorization": f"Bearer {token or _token()}", "Content-Type": "application/json"}


def _recips(x) -> list[dict]:
    """Normalise a 'Name <email>' / bare-email string, or list thereof, into the
    Graph emailAddress shape."""
    from email.utils import parseaddr

    items = x if isinstance(x, (list, tuple)) else ([x] if x else [])
    out = []
    for a in items:
        if not a:
            continue
        name, addr = parseaddr(str(a))
        if not addr:
            continue
        ea = {"address": addr}
        if name:
            ea["name"] = name
        out.append({"emailAddress": ea})
    return out


def _graph_dt(d: dt.datetime) -> str:
    """Graph wants a naive local wall-clock ISO string paired with a timeZone."""
    return d.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


# ── Email ─────────────────────────────────────────────────────────────────────
def send_mail(sender: str, subject: str, html: str, to,
              *, cc=None, reply_to=None, token: str | None = None) -> tuple[bool, str]:
    """Send an email AS ``sender`` (a mailbox UPN, e.g. mark@oncurapartners.com).

    The message lands in that mailbox's Sent Items — a real audit trail. `to`/`cc`
    accept a 'Name <email>' string or a list. Returns (ok, info).
    """
    tok = token or _token()
    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html},
        "toRecipients": _recips(to),
    }
    if cc:
        message["ccRecipients"] = _recips(cc)
    if reply_to:
        message["replyTo"] = _recips(reply_to)
    payload = {"message": message, "saveToSentItems": True}
    try:
        r = requests.post(f"{GRAPH_BASE}/users/{sender}/sendMail",
                          headers=_headers(tok), json=payload, timeout=_TIMEOUT)
    except requests.RequestException as e:
        return False, f"Network error sending mail: {e}"
    if r.status_code in (200, 202):
        return True, "sent"
    return False, f"Graph sendMail error {r.status_code}: {r.text[:300]}"


# ── Calendar ──────────────────────────────────────────────────────────────────
def create_event(organizer: str, subject: str, html_body: str,
                 start: dt.datetime, end: dt.datetime, attendees,
                 *, tz: str = DEFAULT_TZ, location: str | None = None,
                 token: str | None = None) -> tuple[bool, str, str]:
    """Create a calendar event on ``organizer``'s calendar and invite ``attendees``.

    Because the event carries attendees, Graph emails them the meeting invitation
    automatically — this IS the booking (no invitee action required), which is why
    we use Graph rather than a Calendly link that the clinic would have to click.

    Returns (ok, event_id, web_link_or_error). Keep the event_id — cancel_event
    needs it to pull the call back if the clinic pays first.
    """
    tok = token or _token()
    body = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "start": {"dateTime": _graph_dt(start), "timeZone": tz},
        "end": {"dateTime": _graph_dt(end), "timeZone": tz},
        "attendees": [{"emailAddress": r["emailAddress"], "type": "required"}
                      for r in _recips(attendees)],
        "isOnlineMeeting": False,
        "responseRequested": True,
    }
    if location:
        body["location"] = {"displayName": location}
    try:
        r = requests.post(f"{GRAPH_BASE}/users/{organizer}/events",
                          headers=_headers(tok), json=body, timeout=_TIMEOUT)
    except requests.RequestException as e:
        return False, "", f"Network error creating event: {e}"
    if r.status_code not in (200, 201):
        return False, "", f"Graph create event error {r.status_code}: {r.text[:300]}"
    j = r.json()
    return True, j.get("id", ""), j.get("webLink", "")


def cancel_event(organizer: str, event_id: str,
                 comment: str = "This renewal call is no longer needed — thank you for renewing.",
                 *, token: str | None = None) -> tuple[bool, str]:
    """Cancel an event as its organizer. Graph sends a cancellation to attendees,
    so the clinic's copy is withdrawn too. Use this (not DELETE) when a clinic pays
    before the call, so they get a clean cancellation rather than a silent removal.
    """
    tok = token or _token()
    try:
        r = requests.post(f"{GRAPH_BASE}/users/{organizer}/events/{event_id}/cancel",
                          headers=_headers(tok), json={"comment": comment}, timeout=_TIMEOUT)
    except requests.RequestException as e:
        return False, f"Network error cancelling event: {e}"
    if r.status_code in (200, 202, 204):
        return True, "cancelled"
    return False, f"Graph cancel error {r.status_code}: {r.text[:300]}"
