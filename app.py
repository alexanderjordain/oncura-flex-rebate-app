"""Oncura FLEX + Rebate Accounting — home / status.

Run locally:  streamlit run app.py
Deploy:       Streamlit Cloud, app file = app.py. Set secrets (see .streamlit/secrets.toml.example).
"""
import streamlit as st

from core import auth, loaders, store, ui

st.set_page_config(page_title="Oncura FLEX + Rebate", page_icon="*", layout="wide")

auth.require_login()
ui.inject()
auth.sidebar_identity()

ui.header("FLEX + Rebate Accounting",
          "Receive OPD activity and finance-company remittances, calculate, and produce audit-ready imports.",
          kicker="Oncura · Operations Ledger")

rebate = loaders.rebate_master()
flex = loaders.flex_master()
rc = rebate.get("clinics", [])
fc = flex.get("clinics", [])

c1, c2, c3 = st.columns(3)
c1.metric("Rebate clinics", len(rc))
c2.metric("FLEX clinics", len(fc))
gh = "configured" if store._github_token() else "NOT set"
c3.metric("GitHub persistence", gh)

st.divider()

col_flex, col_rebate = st.columns(2)

with col_flex:
    st.subheader("FLEX program")
    by_fc = {}
    for c in fc:
        by_fc[c.get("finance_company")] = by_fc.get(c.get("finance_company"), 0) + 1
    st.write("Clinics by finance company:")
    st.write(by_fc)
    st.markdown(
        "- **FLEX Credits** — monthly credit-memo SaasAnt import\n"
        "- **FLEX Unused / Overage** — quarter-end recapture (per staggered calendar)\n"
        "- **Finance Payment Import** — Great America / OnePlace / NewLane remittances\n\n"
        "Program is CLOSED to new entrants; the active list only shrinks."
    )

with col_rebate:
    st.subheader("Rebate program")
    by_prog = {}
    for c in rc:
        by_prog[c.get("program_type")] = by_prog.get(c.get("program_type"), 0) + 1
    st.write("Clinics by program type:")
    st.write(by_prog)
    st.markdown(
        "- **Rebate Master** — view/edit the clinic list + rates\n"
        "- **Rebate Cycle** — upload OPD detail, calculate, review, export remittances\n\n"
        "Ultrasound 10% finance / 5% self-funded; rads 4% finance / 2% self-funded."
    )

st.divider()
with st.expander("Setup / status notes"):
    st.markdown(
        f"""
- **OPD source:** file import today (generic + OData ConsultService profiles supported);
  swap to the OPD API later by pointing the adapter at it.
- **QBO:** import-file generation (SaasAnt) — no direct QBO writes in this version.
- **Persistence:** masters + config live in `data/` and persist to GitHub
  (`{store.GITHUB_REPO}` @ `{store.GITHUB_BRANCH}`) when `GITHUB_TOKEN` is set.
- **Rebate rates:** ultrasound 10% finance / 5% self-funded; rads 4% finance / 2% self-funded.
"""
    )
