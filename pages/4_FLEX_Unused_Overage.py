import datetime as dt

import pandas as pd
import streamlit as st

from core import auth, flex_unused, loaders, opd_adapter, saasant, ui

st.set_page_config(page_title="FLEX Unused / Overage", layout="wide")
auth.require_login()
ui.inject()
auth.sidebar_identity()

ui.header("FLEX Unused Recapture + Overage",
          "Monthly. Only clinics whose staggered quarter ends this month; activity (Subtotal + Admin Fee) vs threshold.",
          kicker="Flex · Recapture")

flex = loaders.flex_master()
clinics = flex.get("clinics", [])

c1, c2, c3 = st.columns(3)
today = dt.date.today()
year = int(c1.number_input("Recapture year", value=today.year, step=1))
month = int(c2.selectbox("Recapture month", list(range(1, 13)), index=today.month - 1,
                         format_func=lambda m: dt.date(2000, m, 1).strftime("%B")))
start_ref = int(c3.number_input("Starting Invoice No (from QBO max + 1)", value=60000, step=1))

win_start, win_end = flex_unused.quarter_window(year, month)
group = [c for c in clinics if c.get("active") and flex_unused.is_quarter_end(c.get("calendar_spread"), month)]
st.write(
    f"Quarter window: **{win_start:%b %d %Y} – {win_end:%b %d %Y}**  ·  "
    f"clinics with a quarter-end this month: **{len(group)}**"
)

up = st.file_uploader("OPD Invoices export covering the quarter (CSV/XLSX)", type=["csv", "xlsx", "xls"])
sales_class = st.text_input("Sales class for the unused invoice", value="03-Telemedicine")

if up is None:
    st.info("Upload the OPD Invoices export to compute unused / overage.")
    st.stop()

raw = opd_adapter.read_upload(up)
activity = opd_adapter.flex_activity_from_invoices(raw, start=win_start, end=win_end)
st.caption(f"Parsed activity for {len(activity)} clinics from the invoice export.")

recap = flex_unused.compute_recapture(clinics, activity, year, month)
rdf = pd.DataFrame(recap)

if rdf.empty:
    st.warning("No clinics have a quarter-end this month.")
    st.stop()

no_act = rdf[rdf["activity_match"] == "none"]
if not no_act.empty:
    st.warning(f"{len(no_act)} quarter-end clinics had no matched OPD activity — verify names: "
               + ", ".join(no_act["clinic_name"].head(8)))

m1, m2, m3 = st.columns(3)
m1.metric("Unused (recapture) total", f"${rdf['unused'].fillna(0).sum():,.2f}")
m2.metric("Overage total", f"${rdf['overage'].fillna(0).sum():,.2f}")
m3.metric("Clinics processed", len(rdf))

st.subheader("Per-clinic")
st.dataframe(rdf, use_container_width=True, height=380)

udf, next_ref = flex_unused.build_unused_invoice_import(recap, year, month, start_ref, sales_class)
st.subheader(f"Unused recapture invoice import — {len(udf)} rows")
if not udf.empty:
    st.dataframe(udf, use_container_width=True)
    st.download_button(
        "Download unused-flex invoice import (xlsx)",
        saasant.to_xlsx_bytes(udf, "UnusedFlex"),
        file_name=f"UnusedFlex_{dt.date(2000,month,1):%b}_{year}.xlsx",
    )
    st.caption(f"Next available invoice number: {next_ref}")

overs = flex_unused.overage_rows(recap)
if overs:
    st.subheader(f"Overage clinics — Tanya bills separately (SOP-5): {len(overs)}")
    odf = pd.DataFrame(overs)[["clinic_name", "qb_name", "finance_company", "quarterly_threshold", "quarter_activity", "overage"]]
    st.dataframe(odf, use_container_width=True)
    st.download_button(
        "Download overage list (xlsx)",
        saasant.to_xlsx_bytes(odf, "Overage"),
        file_name=f"Overage_{dt.date(2000,month,1):%b}_{year}.xlsx",
    )
