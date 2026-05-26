import os

import pandas as pd
import streamlit as st

from core import auth, loaders, opd_adapter, rebate_calc, saasant

st.set_page_config(page_title="Rebate Cycle", layout="wide")
auth.require_login()
auth.sidebar_identity()

st.title("Rebate Cycle")
st.caption("Upload OPD line detail (ConsultService export or QBO invoice lines), calculate, review, export.")

master = loaders.rebate_master()
imap = loaders.item_map()

period = st.text_input("Period label (for remittance files)", value="")
up = st.file_uploader("OPD detail export (CSV or XLSX)", type=["csv", "xlsx", "xls"])

use_mock = False
if up is None:
    mock_path = os.path.join(os.path.dirname(__file__), "..", "data", "mock_opd_invoices.csv")
    use_mock = st.checkbox(f"No file — use bundled mock data for a dry run", value=False)

raw = None
if up is not None:
    raw = opd_adapter.read_upload(up)
elif use_mock:
    raw = pd.read_csv(os.path.normpath(mock_path))

if raw is None:
    st.info("Upload an OPD export (or tick the mock-data box) to run a cycle.")
    st.stop()

profile = opd_adapter.detect_profile(list(raw.columns))
st.write(f"Detected source profile: **{profile}**  ·  {len(raw):,} rows  ·  columns: {list(raw.columns)[:12]}")

norm = opd_adapter.normalize(raw, None, imap, profile=profile)
with st.expander("Category breakdown (normalized lines)"):
    st.write(norm["category"].value_counts().to_dict())

res = rebate_calc.calculate(norm, master, loaders.config())
per = res["per_clinic"]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Rate-based total", f"${res['grand_total']:,.2f}")
m2.metric("Feed-based total", f"${res['feed_grand_total']:,.2f}" if res["has_feed"] else "n/a")
m3.metric("Variance", f"${res['total_variance']:,.2f}" if res["has_feed"] else "n/a")
m4.metric("Clinics with activity", len(per))

if res["has_feed"] and abs(res["total_variance"]) > 0.01:
    st.warning(
        "Rate-based and feed-based rebate totals differ. The feed splits combo (US+rad) lines "
        "and applies eligibility windows a flat rate can't reproduce. Review per-clinic variance below."
    )

st.subheader("Per-clinic")
if not per.empty:
    show = per[[
        "finance_company", "clinic_name", "program_type",
        "ultrasound_revenue", "ultrasound_rebate", "rads_revenue", "rads_rebate",
        "rebate_rate_based", "rebate_feed_based", "variance", "match",
    ]]
    st.dataframe(show, use_container_width=True, height=420)
else:
    st.info("No matched clinics with activity in this file.")

if not res["unmatched"].empty:
    with st.expander(f"Unmatched OPD clinics ({len(res['unmatched'])}) — not in rebate master"):
        st.dataframe(res["unmatched"], use_container_width=True)

st.subheader("Exports")
if not per.empty:
    st.download_button(
        "Download full per-clinic detail (xlsx)",
        saasant.to_xlsx_bytes(per, "RebateDetail"),
        file_name=f"rebate_detail_{period or 'period'}.xlsx",
    )
    cols = st.columns(3)
    for i, fcname in enumerate(["OnePlace Capital", "NewLane Financed", "Self-Financed"]):
        rem = rebate_calc.remittance_frame(per, fcname, period or "period")
        with cols[i]:
            st.write(f"**{fcname}** — {len(rem)} clinics, ${rem['Rebate Amount'].sum():,.2f}" if not rem.empty else f"**{fcname}** — none")
            if not rem.empty:
                st.download_button(
                    f"Remittance: {fcname}",
                    saasant.to_xlsx_bytes(rem, "Remittance"),
                    file_name=f"remittance_{fcname.replace(' ', '_')}_{period or 'period'}.xlsx",
                    key=f"rem_{fcname}",
                )

st.divider()
if auth.can("approve"):
    if st.button("Mark cycle reviewed / approved"):
        st.success(f"Cycle approved by {auth.current_role()}. (Posting/remittance send is manual.)")
else:
    st.caption("Approval requires operator/approver role.")
