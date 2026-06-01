"""Oncura FLEX + Rebate Accounting — entry point.

Uses st.navigation for explicit section grouping (Rebates / FLEX) so individual page files
don't carry auth/UI boilerplate.

Run locally:  streamlit run app.py
Deploy:       Streamlit Cloud, app file = app.py. Set APP_PASSWORD (+ GITHUB_TOKEN) in secrets.
"""
import streamlit as st

from core import auth, ui, graph_email

st.set_page_config(page_title="Oncura FLEX + Rebate", page_icon="*", layout="wide")

# Auth + theme + sidebar wordmark/logo apply across every page in the app
auth.require_login()
ui.inject()
auth.sidebar_identity()

# OAuth callback handler — Microsoft Graph redirects here with ?code=... after sign-in
_qp = st.query_params
if _qp.get("code") and graph_email.is_configured():
    ok, info = graph_email.handle_callback(_qp["code"])
    # Clear the code from the URL so a refresh doesn't try to re-exchange it
    st.query_params.clear()
    if ok:
        st.success(f"Outlook connected. {info}")
    else:
        st.error(f"Outlook connection failed: {info}")

pages = {
    "": [st.Page("pages/home.py", title="Home", default=True)],
    "Rebates": [
        st.Page("pages/rebate_cycle.py", title="Rebate Cycle"),
        st.Page("pages/rebate_master.py", title="Rebate Clinic Roster"),
    ],
    "FLEX": [
        st.Page("pages/flex_cycle.py", title="FLEX Cycle"),
        st.Page("pages/flex_tutorial.py", title="FLEX Tutorial"),
    ],
    "Admin": [
        st.Page("pages/settings.py", title="Settings"),
    ],
}
st.navigation(pages).run()

# Default-collapse the sidebar nav sections (Rebates / FLEX / Admin). Streamlit renders
# them as <details> elements; this script flips them closed on render. Lives at the end
# so the DOM is built by the time it runs.
import streamlit.components.v1 as components
components.html(
    """
    <script>
      const closeNavSections = () => {
        const doc = window.parent.document;
        const sb  = doc.querySelector('section[data-testid="stSidebar"]');
        if (!sb) return false;
        const details = sb.querySelectorAll('details');
        if (!details.length) return false;
        details.forEach(d => d.open = false);
        return true;
      };
      // Try a few times — Streamlit hydrates the sidebar asynchronously
      if (!closeNavSections()) {
        let tries = 0;
        const t = setInterval(() => {
          tries++;
          if (closeNavSections() || tries > 20) clearInterval(t);
        }, 100);
      }
    </script>
    """,
    height=0,
)
