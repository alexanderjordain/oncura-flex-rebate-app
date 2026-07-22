"""Delegated Microsoft Graph for the EMA bot — the operator's signed-in Outlook.

Why this exists: app-only (client-credentials) Graph needs an Entra tenant admin
to "Grant admin consent", which Alexander's account cannot do. Delegated auth
sidesteps that entirely — the operator signs in once ("Connect Outlook") and
consents *for themselves* to user-consentable scopes (Mail.Send, Calendars.
ReadWrite). This is the same OAuth dance core/graph_email.py uses for the
assistance-email button, so it's known to work in this tenant.

In this model:
  * the renewal email sends **as the signed-in user** (Alexander) — /me/sendMail;
  * the call is created on the signed-in user's calendar with **Mark + the clinic
    invited** (/me/events) — so it lands on Mark's calendar when he accepts and the
    clinic gets the invite, with no cross-mailbox permission or calendar sharing.

Streamlit-coupled by design (token lives in st.session_state, per browser session).
Uses the EMA app registration's GRAPH_CLIENT_ID / GRAPH_TENANT_ID as a PUBLIC
client — no client secret, so the AADSTS7000215 secret-value problem can't occur.

Redirect: the OAuth ?code comes back to the app root; app.py routes by `state`
(STATE below) so this flow and graph_email's don't collide.
"""
from __future__ import annotations

import datetime as dt
import os

import streamlit as st

from . import ema_graph

SCOPES = ["Mail.Send", "Calendars.ReadWrite", "User.Read"]
STATE = "ema_graph_oauth"
_DEFAULT_REDIRECT = "https://oncura-programs.streamlit.app/"


def _secret(key: str, default: str = "") -> str:
    try:
        v = st.secrets.get(key)
    except Exception:
        v = None
    return str(v) if v else (os.environ.get(key, default) or default)


def is_configured() -> bool:
    return bool(_secret("GRAPH_CLIENT_ID") and _secret("GRAPH_TENANT_ID"))


def _authority() -> str:
    return f"https://login.microsoftonline.com/{_secret('GRAPH_TENANT_ID')}"


def _redirect_uri() -> str:
    return _secret("GRAPH_REDIRECT_URI", _DEFAULT_REDIRECT)


def _msal_app():
    import msal
    cache = st.session_state.setdefault("_ema_msal_cache", msal.SerializableTokenCache())
    if isinstance(cache, str):
        c = msal.SerializableTokenCache()
        c.deserialize(cache)
        cache = c
        st.session_state["_ema_msal_cache"] = c
    return msal.PublicClientApplication(
        client_id=_secret("GRAPH_CLIENT_ID"), authority=_authority(), token_cache=cache)


def get_auth_url() -> str:
    return _msal_app().get_authorization_request_url(
        scopes=SCOPES, redirect_uri=_redirect_uri(), state=STATE)


def handle_callback(code: str) -> tuple[bool, str]:
    app = _msal_app()
    try:
        result = app.acquire_token_by_authorization_code(
            code=code, scopes=SCOPES, redirect_uri=_redirect_uri())
    except Exception as e:  # noqa: BLE001
        return False, f"Token exchange failed: {e}"
    if "access_token" not in result:
        return False, result.get("error_description") or str(result)
    accounts = app.get_accounts()
    if accounts:
        st.session_state["_ema_msal_account_id"] = accounts[0]["home_account_id"]
    return True, "Connected."


def _token() -> str | None:
    if not is_configured():   # no creds -> fail safe, never build MSAL on an empty authority
        return None
    app = _msal_app()
    accounts = app.get_accounts()
    acct_id = st.session_state.get("_ema_msal_account_id")
    account = next((a for a in accounts if a.get("home_account_id") == acct_id), None) \
        or (accounts[0] if accounts else None)
    if not account:
        return None
    result = app.acquire_token_silent(SCOPES, account=account)
    return result.get("access_token") if result else None


def is_connected() -> bool:
    return _token() is not None


def disconnect():
    for k in ("_ema_msal_cache", "_ema_msal_account_id", "_ema_graph_user"):
        st.session_state.pop(k, None)


def connected_user() -> str | None:
    if "_ema_graph_user" in st.session_state:
        return st.session_state["_ema_graph_user"]
    tok = _token()
    if not tok:
        return None
    import requests
    try:
        r = requests.get(f"{ema_graph.GRAPH_BASE}/me",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=10)
        if r.status_code == 200:
            d = r.json()
            who = d.get("mail") or d.get("userPrincipalName")
            st.session_state["_ema_graph_user"] = who
            return who
    except Exception:
        return None
    return None


# ── Graph actions (delegated /me) ─────────────────────────────────────────────
def send_mail(subject: str, html: str, to, *, reply_to=None) -> tuple[bool, str]:
    tok = _token()
    if not tok:
        return False, "Outlook not connected."
    import requests
    message = {"subject": subject, "body": {"contentType": "HTML", "content": html},
               "toRecipients": ema_graph._recips(to)}
    if reply_to:
        message["replyTo"] = ema_graph._recips(reply_to)
    try:
        r = requests.post(f"{ema_graph.GRAPH_BASE}/me/sendMail",
                          headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                          json={"message": message, "saveToSentItems": True}, timeout=30)
    except requests.RequestException as e:
        return False, f"Network error sending mail: {e}"
    return (True, "sent") if r.status_code in (200, 202) else \
        (False, f"Graph sendMail error {r.status_code}: {r.text[:300]}")


def create_event(subject: str, html_body: str, start: dt.datetime, end: dt.datetime,
                 attendees, *, tz: str = ema_graph.DEFAULT_TZ) -> tuple[bool, str, str]:
    tok = _token()
    if not tok:
        return False, "", "Outlook not connected."
    import requests
    body = {
        "subject": subject, "body": {"contentType": "HTML", "content": html_body},
        "start": {"dateTime": ema_graph._graph_dt(start), "timeZone": tz},
        "end": {"dateTime": ema_graph._graph_dt(end), "timeZone": tz},
        "attendees": [{"emailAddress": r["emailAddress"], "type": "required"}
                      for r in ema_graph._recips(attendees)],
        "isOnlineMeeting": False, "responseRequested": True,
    }
    try:
        r = requests.post(f"{ema_graph.GRAPH_BASE}/me/events",
                          headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                          json=body, timeout=30)
    except requests.RequestException as e:
        return False, "", f"Network error creating event: {e}"
    if r.status_code not in (200, 201):
        return False, "", f"Graph create event error {r.status_code}: {r.text[:300]}"
    j = r.json()
    return True, j.get("id", ""), j.get("webLink", "")


def cancel_event(event_id: str,
                 comment: str = "This renewal call is no longer needed — thank you for renewing.",
                 ) -> tuple[bool, str]:
    tok = _token()
    if not tok:
        return False, "Outlook not connected."
    import requests
    try:
        r = requests.post(f"{ema_graph.GRAPH_BASE}/me/events/{event_id}/cancel",
                          headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                          json={"comment": comment}, timeout=30)
    except requests.RequestException as e:
        return False, f"Network error cancelling event: {e}"
    return (True, "cancelled") if r.status_code in (200, 202, 204) else \
        (False, f"Graph cancel error {r.status_code}: {r.text[:300]}")


def check() -> dict:
    """Verify the delegated session can act: connected + read /me/calendar."""
    from . import ema_bot
    who = connected_user()
    if not is_connected():
        return {"ok": False, "detail": "Outlook not connected — click 'Connect Outlook'.",
                "sender": who, "organizer": ema_bot.organizer_mailbox()}
    import requests
    r = requests.get(f"{ema_graph.GRAPH_BASE}/me/calendar",
                     headers={"Authorization": f"Bearer {_token()}"}, timeout=30)
    if r.status_code == 200:
        return {"ok": True, "detail": f"Connected as {who}; calendar access OK. "
                f"Calls are created on your calendar with {ema_bot.organizer_mailbox()} invited.",
                "sender": who, "organizer": ema_bot.organizer_mailbox()}
    return {"ok": False, "detail": f"Calendar check failed {r.status_code}: {r.text[:200]}",
            "sender": who, "organizer": ema_bot.organizer_mailbox()}


# ── Backend adapter for core.ema_bot ──────────────────────────────────────────
class DelegatedBackend:
    """ema_bot backend that sends via the operator's Outlook. The call is created
    on the operator's calendar with Mark + the clinic invited."""
    label = "delegated"

    def ready(self) -> bool:
        return is_connected()

    def check(self) -> dict:
        return check()

    def send_mail(self, subject: str, html: str, to):
        return send_mail(subject, html, to)

    def create_call(self, subject, html, start, end, clinic_email):
        from . import ema_bot
        return create_event(subject, html, start, end, [ema_bot.organizer_mailbox(), clinic_email])

    def cancel(self, event_id: str):
        return cancel_event(event_id)
