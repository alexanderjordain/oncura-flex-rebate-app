"""Microsoft Graph email integration — create drafts in the signed-in user's Outlook.

Why Graph instead of SMTP:
    - Email shows up in the user's own Outlook Sent/Drafts (proper audit trail)
    - User reviews the draft in their actual client and clicks Send themselves
    - No service-account / shared-mailbox compromise; multi-user safe
    - Tenant admin retains visibility via M365 Compliance / eDiscovery

Flow:
    1. is_configured()  → True if AZURE_CLIENT_ID + AZURE_TENANT_ID are in secrets.
    2. get_auth_url()   → Microsoft sign-in URL; user clicks "Connect Outlook".
    3. handle_callback(code) → exchanges the code for an access token in session.
    4. is_connected()   → True if we have a token for this session.
    5. create_draft(subject, body, to, attachments=[(name, bytes)])
                        → POSTs to Graph; returns the Outlook web link to the draft.
    6. disconnect()     → clear session tokens.

Token storage: in st.session_state (per-browser-session). Each user re-auths on
session start. Acceptable for an MVP — upgrade to a per-user encrypted token
cache only if usage justifies it.

Required Azure AD app registration:
    - Platform: Web
    - Redirect URI: <app URL>/  (e.g. https://oncura-programs.streamlit.app/)
    - Delegated permissions: Mail.ReadWrite, offline_access, User.Read
    - Treat the client_id as public, tenant_id as the Oncura tenant
"""
from __future__ import annotations

import base64
import urllib.parse

import streamlit as st


SCOPES = ["Mail.ReadWrite", "User.Read"]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ── Configuration ─────────────────────────────────────────────────────────────


def _secret(key: str, default: str = "") -> str:
    try:
        return st.secrets.get(key, default) or default
    except Exception:
        return default


def is_configured() -> bool:
    return bool(_secret("AZURE_CLIENT_ID")) and bool(_secret("AZURE_TENANT_ID"))


def _client_id() -> str:    return _secret("AZURE_CLIENT_ID")
def _tenant_id() -> str:    return _secret("AZURE_TENANT_ID")
def _redirect_uri() -> str:
    # Default to the prod Streamlit Cloud URL; override with AZURE_REDIRECT_URI for local dev.
    return _secret("AZURE_REDIRECT_URI", "https://oncura-programs.streamlit.app/")


def _authority() -> str:
    return f"https://login.microsoftonline.com/{_tenant_id()}"


def _msal_app():
    """Lazy-import msal so the app doesn't crash if the dep isn't installed yet."""
    import msal
    cache = st.session_state.setdefault("_msal_cache", msal.SerializableTokenCache())
    if isinstance(cache, str):
        c = msal.SerializableTokenCache()
        c.deserialize(cache)
        cache = c
        st.session_state["_msal_cache"] = c
    return msal.ConfidentialClientApplication(
        client_id=_client_id(),
        authority=_authority(),
        client_credential=None,        # public client, no secret
        token_cache=cache,
    ) if _secret("AZURE_CLIENT_SECRET") else msal.PublicClientApplication(
        client_id=_client_id(),
        authority=_authority(),
        token_cache=cache,
    )


# ── Auth flow ─────────────────────────────────────────────────────────────────


def get_auth_url(state: str = "graph_email_oauth") -> str:
    """Build the Microsoft sign-in URL for authorization code flow."""
    app = _msal_app()
    return app.get_authorization_request_url(
        scopes=SCOPES,
        redirect_uri=_redirect_uri(),
        state=state,
    )


def handle_callback(code: str) -> tuple[bool, str]:
    """Exchange an auth code for tokens. Call from app.py when ?code=… is on the URL."""
    app = _msal_app()
    try:
        result = app.acquire_token_by_authorization_code(
            code=code, scopes=SCOPES, redirect_uri=_redirect_uri(),
        )
    except Exception as e:
        return False, f"Token exchange failed: {e}"
    if "access_token" not in result:
        return False, result.get("error_description") or str(result)
    # Stash the home_account_id so we can re-acquire from cache on next page load
    accounts = app.get_accounts()
    if accounts:
        st.session_state["_msal_account_id"] = accounts[0]["home_account_id"]
    return True, "Connected."


def _get_access_token() -> str | None:
    """Pull an access token from the cache (silent refresh if needed)."""
    app = _msal_app()
    acct_id = st.session_state.get("_msal_account_id")
    accounts = app.get_accounts()
    account = None
    if acct_id:
        account = next((a for a in accounts if a.get("home_account_id") == acct_id), None)
    if not account and accounts:
        account = accounts[0]
    if not account:
        return None
    result = app.acquire_token_silent(SCOPES, account=account)
    if result and "access_token" in result:
        return result["access_token"]
    return None


def is_connected() -> bool:
    return _get_access_token() is not None


def disconnect():
    """Clear cached tokens. Forces re-auth on next use."""
    for k in ("_msal_cache", "_msal_account_id", "_graph_user"):
        st.session_state.pop(k, None)


def get_user_info() -> dict | None:
    """Cached lookup of the signed-in user's email + display name."""
    if "_graph_user" in st.session_state:
        return st.session_state["_graph_user"]
    tok = _get_access_token()
    if not tok: return None
    try:
        import requests
        r = requests.get(
            f"{GRAPH_BASE}/me",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            user = {
                "email": data.get("mail") or data.get("userPrincipalName"),
                "name":  data.get("displayName"),
            }
            st.session_state["_graph_user"] = user
            return user
    except Exception:
        return None
    return None


# ── Draft creation ────────────────────────────────────────────────────────────


def create_draft(
    subject: str,
    body: str,
    to: str,
    attachments: list[tuple[str, bytes]] | None = None,
) -> tuple[bool, str]:
    """Create a draft email in the signed-in user's Outlook with attachments.

    Returns (ok, info). On success info is the Outlook webLink to the draft.
    """
    tok = _get_access_token()
    if not tok:
        return False, "Not connected to Outlook. Click 'Connect Outlook' first."
    import requests
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

    payload = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": to}}],
    }

    # Step 1: create the draft message
    try:
        r = requests.post(f"{GRAPH_BASE}/me/messages", headers=headers, json=payload, timeout=20)
    except Exception as e:
        return False, f"Network error creating draft: {e}"
    if r.status_code not in (200, 201):
        return False, f"Graph error {r.status_code}: {r.text[:300]}"
    msg = r.json()
    msg_id = msg.get("id")
    web_link = msg.get("webLink", "")

    # Step 2: add attachments
    for name, blob in attachments or []:
        b64 = base64.b64encode(blob).decode()
        att_payload = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name,
            "contentBytes": b64,
        }
        try:
            ar = requests.post(
                f"{GRAPH_BASE}/me/messages/{msg_id}/attachments",
                headers=headers, json=att_payload, timeout=30,
            )
        except Exception as e:
            return False, f"Draft created but attachment '{name}' failed to upload: {e}"
        if ar.status_code not in (200, 201):
            return False, f"Draft created but attachment '{name}' rejected ({ar.status_code}): {ar.text[:200]}"

    return True, web_link or f"Draft created (id {msg_id})."
