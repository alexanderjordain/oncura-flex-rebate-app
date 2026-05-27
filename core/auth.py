"""Lightweight password gate for a public Streamlit Cloud URL.

The app is deployed to a public URL, so a shared password keeps clinic financial detail from
being world-readable. The password lives in Streamlit secrets, never in the repo.

Default (what we use): a single shared password, no roles. Anyone who enters it gets full access.

  secrets:
    APP_PASSWORD = "..."

Optional: add a [roles] table to split permissions. When [roles] exists, APP_PASSWORD becomes
read-only and a role password unlocks actions:
    [roles] alex/tanya/jennifer/marty = "..."
  alex            -> admin (everything)
  tanya           -> operator (run cycles, generate imports, approve)
  jennifer, marty -> approver (review + approve, no master edits)
  viewer          -> read-only
"""
from __future__ import annotations

import os

import streamlit as st

ROLE_PERMS = {
    "admin": {"admin", "operate", "approve", "view"},  # single-password setups land here
    "alex": {"admin", "operate", "approve", "view"},
    "tanya": {"operate", "approve", "view"},
    "jennifer": {"approve", "view"},
    "marty": {"approve", "view"},
    "viewer": {"view"},
}


def _secret(path, default=None):
    try:
        cur = st.secrets
        for p in path:
            cur = cur[p]
        return cur
    except Exception:
        return default


def _resolve_role(entered: str):
    """Return role name if `entered` matches a role password; else None."""
    roles = _secret(["roles"], {}) or {}
    for role, pw in roles.items():
        if pw and entered == pw:
            return role.lower()
    return None


def require_login():
    """Render the gate. Returns the role string once authenticated, else st.stop()s."""
    if st.session_state.get("auth_role"):
        return st.session_state["auth_role"]

    app_pw = _secret(["APP_PASSWORD"])
    # No password configured. Grant admin ONLY for explicit local dev (FLEXREBATE_LOCAL=1).
    # On a public Cloud URL this must NOT silently open admin access.
    if not app_pw and not _secret(["roles"]):
        if os.environ.get("FLEXREBATE_LOCAL") == "1":
            st.session_state["auth_role"] = "alex"
            return "alex"
        st.error(
            "App password not configured. Set `APP_PASSWORD` (and optional `[roles]`) in "
            "Streamlit secrets. For local dev, run with env var `FLEXREBATE_LOCAL=1`."
        )
        st.stop()

    st.title("Oncura FLEX + Rebate Accounting")
    st.caption("Enter the app password. Role passwords unlock approval/admin actions.")
    entered = st.text_input("Password", type="password")
    if st.button("Enter", type="primary"):
        role = _resolve_role(entered)
        if role:
            st.session_state["auth_role"] = role
            st.rerun()
        elif app_pw and entered == app_pw:
            # If [roles] are configured, the shared password is read-only and a role password
            # is needed for actions. With no roles, the shared password grants full access.
            st.session_state["auth_role"] = "viewer" if _secret(["roles"]) else "admin"
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()


def current_role() -> str:
    return st.session_state.get("auth_role", "viewer")


def can(permission: str) -> bool:
    return permission in ROLE_PERMS.get(current_role(), set())


def require(permission: str) -> bool:
    """True if allowed; otherwise render an info banner and return False."""
    if can(permission):
        return True
    st.info(f"Your role ({current_role()}) is read-only for this action.")
    return False


def sidebar_identity():
    role = current_role()
    with st.sidebar:
        st.markdown(f"**Role:** `{role}`")
        if st.button("Log out"):
            st.session_state.pop("auth_role", None)
            st.rerun()
