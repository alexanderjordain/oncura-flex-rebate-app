"""User-safe error rendering.

Error banners used to render the full Python traceback to every logged-in
user. Tracebacks here don't contain secrets (verified: requests +
HTTPBasicAuth doesn't embed credentials), but they do leak server file
paths and module internals. Render policy:

- Everyone sees: exception type + message + a short reference ID.
- The full traceback is logged server-side (visible in the Streamlit Cloud
  app logs) tagged with the same reference ID, so reports correlate.
- The admin role additionally gets the traceback inline in an expander —
  the admin IS the support path for this app, and losing inline tracebacks
  would slow down every fix.
"""
from __future__ import annotations

import hashlib
import logging
import traceback

import streamlit as st

logger = logging.getLogger("oncura.errors")


def reference_id(tb_text: str) -> str:
    """Short ID derived from the traceback text. Deterministic on purpose:
    the same failure yields the same ID, so repeated operator reports are
    recognizably one issue."""
    return "ERR-" + hashlib.sha256(tb_text.encode("utf-8")).hexdigest()[:10].upper()


def capture(exc: BaseException) -> dict:
    """Snapshot an exception for safe rendering, now or on a later rerun.

    Logs the full traceback server-side and returns a session-state-friendly
    dict: {"summary", "ref", "traceback"}.
    """
    tb_text = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    ref = reference_id(tb_text)
    logger.error("[%s] %s", ref, tb_text)
    return {
        "summary": f"{type(exc).__name__}: {exc}",
        "ref": ref,
        "traceback": tb_text,
    }


def render_details(err: dict) -> None:
    """Reference-ID caption for everyone; traceback expander for admins only.

    Call directly under the st.error banner the caller renders — the banner
    text stays the caller's (it carries the workflow-specific coaching).
    """
    from . import auth

    st.caption(
        f"Reference `{err['ref']}` — quote this when asking for help. "
        "The full technical detail is in the app logs under the same ID."
    )
    if auth.can("admin"):
        with st.expander("Full traceback (admin)"):
            st.code(err["traceback"], language="text")
