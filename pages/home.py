import streamlit as st

from core import loaders, store, ui

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
    st.markdown(
        "Single **FLEX Cycle** page walks the monthly process end-to-end:\n"
        "1. Finance company remittance → SaasAnt flex/scan imports\n"
        "2. Monthly credit memos\n"
        "3. Quarter-end unused recapture + overage list\n\n"
        "Program is CLOSED to new entrants; the active list only shrinks."
    )

with col_rebate:
    st.subheader("Rebate program")
    st.markdown(
        "- **Rebate Master** — view/edit the clinic list + rates\n"
        "- **Rebate Cycle** — pick one or more months, upload OPD detail, download a multi-tab "
        "report (one tab per finance bucket)\n\n"
        "Ultrasound 10% finance / 5% self-funded; rads 4% finance / 2% self-funded."
    )

st.divider()
with st.expander("Setup / status notes"):
    st.markdown(
        f"""
- **OPD source:** file import (consult-grid, OData ConsultService, or Invoices); live API later.
- **QBO:** SaasAnt import-file generation — no direct QBO writes in this version.
- **Persistence:** masters + config live in `data/` and persist to GitHub
  (`{store.GITHUB_REPO}` @ `{store.GITHUB_BRANCH}`) when `GITHUB_TOKEN` is set.
- **Rebate rates:** ultrasound 10% / 5%, rads 4% / 2% (finance / self-funded).
"""
    )
