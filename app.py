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
        st.Page("pages/rebate_master.py", title="Rebate Program Controls"),
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

# Default-collapse the sidebar nav sections (Rebates / FLEX / Admin). Streamlit's nav
# section toggles can render as either <details> or <button aria-expanded>; handle both,
# and keep retrying through the React hydration window so a late-rendered "open" state
# gets closed back down.
import streamlit.components.v1 as components
components.html(
    """
    <script>
      const doc = window.parent.document;
      const closeAll = () => {
        const sb = doc.querySelector('section[data-testid="stSidebar"]');
        if (!sb) return 0;
        let n = 0;
        sb.querySelectorAll('details[open]').forEach(d => { d.open = false; n++; });
        sb.querySelectorAll('button[aria-expanded="true"]').forEach(b => {
          const txt = (b.textContent || '').trim();
          // Section headers are short labels like "Rebates" / "FLEX" / "Admin".
          // Skip anything long (page titles, action buttons) so we don't click the wrong thing.
          if (txt && txt.length < 30) { b.click(); n++; }
        });
        return n;
      };
      // Retry on a 150ms interval for ~3s. Streamlit re-renders the nav after this script
      // runs, so a single close isn't enough — we need to re-close until hydration settles.
      let tries = 0;
      const intv = setInterval(() => {
        closeAll();
        if (++tries >= 20) clearInterval(intv);
      }, 150);
    </script>
    """,
    height=0,
)
