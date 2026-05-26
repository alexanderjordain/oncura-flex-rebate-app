import datetime as dt

import pandas as pd
import streamlit as st

from core import auth, flex_finance, opd_adapter, saasant

st.set_page_config(page_title="Finance Payment Import", layout="wide")
auth.require_login()
auth.sidebar_identity()

st.title("FLEX Finance Company Payment Import")
st.caption(
    "Turn a finance-company remittance into SaasAnt imports. Each row gets a UNIQUE "
    "'Ref No (Receive Payment No)' — a shared reference collapses all rows onto one customer."
)

c1, c2, c3 = st.columns(3)
company = c1.selectbox("Finance company", ["GreatAmerica", "OnePlace", "NewLane"])
pay_date = c2.date_input("Payment / deposit date", value=dt.date.today())
start_ref = int(c3.number_input("Starting scan Invoice No (QBO max + 1)", value=49000, step=1))

meta = flex_finance.COMPANY_META.get(company, {})
st.write(f"Bank feed label: **{meta.get('bank_feed', '?')}**  ·  payment reference label: **{meta.get('label')}**")

up = st.file_uploader("Remittance file (CSV/XLSX)", type=["csv", "xlsx", "xls"])
if up is None:
    st.info("Upload the finance company's remittance export.")
    st.stop()

raw = opd_adapter.read_upload(up)
cols = list(raw.columns)
st.write(f"{len(raw):,} rows. Map the columns:")

mc1, mc2, mc3 = st.columns(3)
def _guess(cands):
    for i, c in enumerate(cols):
        if any(k in str(c).lower() for k in cands):
            return i
    return 0

customer_col = mc1.selectbox("QB Customer name column", cols, index=_guess(["customer"]))
amount_col = mc2.selectbox("Amount column", cols, index=_guess(["paid", "amount", "payment"]))
id_label = "Payment Invoice Number" if company == "GreatAmerica" else "Contract #"
id_col = mc3.selectbox(f"{id_label} column (for unique ref)", cols, index=_guess(["invoice", "contract"]))

split_mode = st.radio(
    "Flex vs scan split",
    ["By contract rule (5-digit, '04' = flex)", "All flex", "All scan"],
    horizontal=True,
)

work = pd.DataFrame({
    "customer": raw[customer_col].astype(str).str.strip(),
    "amount": raw[amount_col].map(opd_adapter._coerce_amount),
    "ident": raw[id_col],
})
work = work[work["amount"] != 0]

def classify(v):
    if split_mode == "All flex":
        return "flex"
    if split_mode == "All scan":
        return "scan"
    return flex_finance.classify_contract(v)

work["kind"] = work["ident"].map(classify)
st.write("Split:", work["kind"].value_counts().to_dict())

unknown = work[work["kind"] == "unknown"]
if not unknown.empty:
    st.warning(f"{len(unknown)} rows couldn't be auto-classified (contract format). "
               "Use 'All flex'/'All scan' or pre-split the file.")

flex_rows = [
    {"customer": r.customer, "amount": r.amount,
     "invoice_number": r.ident, "contract": r.ident}
    for r in work[work["kind"] == "flex"].itertuples()
]
scan_rows = [
    {"customer": r.customer, "amount": r.amount,
     "invoice_number": r.ident, "contract": r.ident}
    for r in work[work["kind"] == "scan"].itertuples()
]

st.divider()
st.subheader("1. Flex receive payments (intentionally unapplied)")
if flex_rows:
    fdf, label = flex_finance.build_receive_payments(flex_rows, company, pay_date)
    st.dataframe(fdf, use_container_width=True, height=260)
    st.download_button("Download flex payments (xlsx)", saasant.to_xlsx_bytes(fdf, "FlexPayments"),
                       file_name=f"{company}_FlexPayments_{pay_date}.xlsx")
else:
    st.caption("No flex rows.")

if scan_rows:
    st.subheader("2. Scan-package invoices (upload BEFORE scan payments)")
    sidf, next_ref = flex_finance.build_scan_invoices(scan_rows, pay_date, start_ref)
    st.dataframe(sidf, use_container_width=True, height=220)
    st.download_button("Download scan invoices (xlsx)", saasant.to_xlsx_bytes(sidf, "ScanInvoices"),
                       file_name=f"{company}_ScanInvoices_{pay_date}.xlsx")

    st.subheader("3. Scan-package receive payments")
    spdf, _ = flex_finance.build_receive_payments(scan_rows, company, pay_date)
    st.dataframe(spdf, use_container_width=True, height=220)
    st.download_button("Download scan payments (xlsx)", saasant.to_xlsx_bytes(spdf, "ScanPayments"),
                       file_name=f"{company}_ScanPayments_{pay_date}.xlsx")
    st.caption(f"Next scan invoice number: {next_ref}")

st.divider()
total = work["amount"].sum()
st.metric("Remittance total (must equal bank feed deposit)", f"${total:,.2f}")
st.caption("Import order: scan invoices -> flex payments -> scan payments. Match the combined "
           "total to the bank feed after all uploads. Only one SaasAnt job at a time.")
