"""Oncura Pass-Through & Rebate Programs Ledger — entry point.

Uses st.navigation for explicit section grouping (Rebates / Pass-Through) so individual page
files don't carry auth/UI boilerplate.

Run locally:  streamlit run app.py
Deploy:       Streamlit Cloud, app file = app.py. Set APP_PASSWORD (+ GITHUB_TOKEN) in secrets.
"""
import streamlit as st

from core import auth, ui, graph_email, ema_graph_delegated

st.set_page_config(page_title="Pass-Through & Rebate Programs Ledger", page_icon="*", layout="wide")

# Auth + theme: gate is enforced first; theme CSS injected once per page run
auth.require_login()
ui.inject()

# OAuth callback handler — Microsoft Graph redirects here with ?code=... after sign-in.
# Two delegated flows share this redirect (the assistance-email button and the EMA
# renewal sender, which use different app registrations), so route by `state`.
_qp = st.query_params
if _qp.get("code"):
    _state = _qp.get("state", "")
    if _state == ema_graph_delegated.STATE and ema_graph_delegated.is_configured():
        _ok, _info = ema_graph_delegated.handle_callback(_qp["code"])
        _label = "EMA sending"
    elif graph_email.is_configured():
        _ok, _info = graph_email.handle_callback(_qp["code"])
        _label = "Outlook"
    else:
        _ok, _info, _label = None, None, None
    # Clear the code from the URL so a refresh doesn't try to re-exchange it
    st.query_params.clear()
    if _ok is True:
        st.success(f"{_label} connected. {_info}")
    elif _ok is False:
        st.error(f"{_label} connection failed: {_info}")

# Register all pages with st.navigation but suppress its auto-rendered sidebar nav
# (position="hidden"). We render the sidebar manually below using st.expander so the
# section groups (Rebates / FLEX / Admin) default-collapse reliably — fighting
# Streamlit's auto-nav re-renders with JS was unreliable.
pages = {
    "": [st.Page("pages/home.py", title="Home", default=True)],
    "Rebates": [
        st.Page("pages/rebate_cycle.py", title="Rebate Cycle"),
        st.Page("pages/rebate_master.py", title="Rebate Program Controls"),
    ],
    "Pass-Through Payments": [
        st.Page("pages/flex_cycle.py", title="Payment Cycle"),
        st.Page("pages/overage_tracker.py", title="Overage Tracker"),
        st.Page("pages/flex_master.py", title="Clinic Roster"),
        st.Page("pages/flex_tutorial.py", title="FLEX Tutorial"),
    ],
    "Admin": [
        st.Page("pages/ema_renewals.py", title="EMA Renewals"),
        st.Page("pages/settings.py", title="Settings"),
        st.Page("pages/audit_log.py", title="Audit & Tracking"),
    ],
}
nav = st.navigation(pages, position="hidden")

# Sidebar order: brand (logo + wordmark) → navigation → footer (role + logout).
ui.sidebar_brand()

with st.sidebar:
    st.markdown('<div class="oncura-nav">', unsafe_allow_html=True)
    st.page_link("pages/home.py", label="Home")
    with st.expander("Rebates", expanded=False):
        st.page_link("pages/rebate_cycle.py", label="Rebate Cycle")
        st.page_link("pages/rebate_master.py", label="Rebate Program Controls")
    with st.expander("Pass-Through Payments", expanded=False):
        st.page_link("pages/flex_cycle.py", label="Payment Cycle")
        st.page_link("pages/overage_tracker.py", label="Overage Tracker")
        st.page_link("pages/flex_master.py", label="Clinic Roster")
        st.page_link("pages/flex_tutorial.py", label="FLEX Tutorial")
    with st.expander("Admin", expanded=False):
        st.page_link("pages/ema_renewals.py", label="EMA Renewals")
        st.page_link("pages/settings.py", label="Settings")
        st.page_link("pages/audit_log.py", label="Audit & Tracking")
    st.markdown('</div>', unsafe_allow_html=True)

auth.sidebar_footer()

nav.run()
