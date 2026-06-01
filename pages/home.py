import streamlit as st

from core import ledger, loaders, store, ui

ui.header("FLEX + Rebate Accounting",
          "Receive OPD activity and finance-company remittances, calculate, and produce audit-ready imports.",
          kicker="Oncura · Operations Ledger")

rebate = loaders.rebate_master()
flex = loaders.flex_master()
rc = rebate.get("clinics", [])
fc = flex.get("clinics", [])
ledger_summary = ledger.summary()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Rebate clinics", len(rc))
c2.metric("FLEX clinics", len(fc))
c3.metric("Ledger: payments", ledger_summary["payment_count"])
gh = "configured" if store._github_token() else "NOT set"
c4.metric("GitHub persistence", gh)

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
        "- **Rebate Clinic Roster** — view/edit the clinic list + rates\n"
        "- **Rebate Cycle** — pick one or more months, upload OPD detail, download a multi-tab "
        "report (one tab per finance bucket)\n\n"
        "Ultrasound 10% finance / 5% self-funded; rads 4% finance / 2% self-funded."
    )

st.divider()

# ── Module health (catches partial-deploy / import-broken state) ─────────────
with st.expander("Module health — verify every core module imports cleanly"):
    import importlib
    core_modules = [
        "accounting_handoff", "auth", "flex_credits", "flex_finance", "flex_overage",
        "flex_unused", "ledger", "loaders", "opd_adapter", "rebate_calc",
        "rebate_report", "saasant", "store", "ui",
    ]
    health = []
    for m in core_modules:
        try:
            importlib.import_module(f"core.{m}")
            health.append({"module": f"core.{m}", "status": "OK"})
        except Exception as e:
            health.append({"module": f"core.{m}", "status": f"{type(e).__name__}: {e}"})
    failed = [h for h in health if h["status"] != "OK"]
    if failed:
        st.error(f"{len(failed)} module(s) failed to import — see Recovery runbook (docs/RECOVERY.md).")
    else:
        st.success(f"All {len(health)} core modules import cleanly.")
    st.dataframe(health, use_container_width=True, hide_index=True)

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
