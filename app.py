"""Oncura FLEX + Rebate Accounting — entry point.

Uses st.navigation for explicit section grouping (Rebates / FLEX) so individual page files
don't carry auth/UI boilerplate.

Run locally:  streamlit run app.py
Deploy:       Streamlit Cloud, app file = app.py. Set APP_PASSWORD (+ GITHUB_TOKEN) in secrets.
"""
import streamlit as st

from core import auth, ui

st.set_page_config(page_title="Oncura FLEX + Rebate", page_icon="*", layout="wide")

# Auth + theme + sidebar wordmark/logo apply across every page in the app
auth.require_login()
ui.inject()
auth.sidebar_identity()

pages = {
    "": [st.Page("pages/home.py", title="Home", default=True)],
    "Rebates": [
        st.Page("pages/rebate_master.py", title="Rebate Master"),
        st.Page("pages/rebate_cycle.py", title="Rebate Cycle"),
    ],
    "FLEX": [
        st.Page("pages/flex_cycle.py", title="FLEX Cycle"),
    ],
    "Admin": [
        st.Page("pages/settings.py", title="Settings"),
    ],
}
st.navigation(pages).run()
