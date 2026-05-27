import datetime as dt

import streamlit as st

from core import auth, flex_finance, loaders, opd_adapter, saasant

st.set_page_config(page_title="Finance Payment Import", layout="wide")
auth.require_login()
auth.sidebar_identity()

st.title("FLEX Finance Company Payment Import")
st.caption(
    "Turn a finance-company remittance into SaasAnt imports. Each row gets a UNIQUE "
    "'Ref No (Receive Payment No)' — a shared reference collapses all rows onto one customer."
)

nm = loaders.name_map()

c1, c2, c3 = st.columns(3)
company = c1.selectbox("Finance company", ["NewLane", "GreatAmerica", "OnePlace"])
pay_date = c2.date_input("Payment date", value=dt.date.today())
inv_date = c3.date_input("Invoice date (scan packages)", value=dt.date.today())
start_inv = int(st.number_input("Starting scan Invoice No (QBO max + 1)", value=49000, step=1))

meta = flex_finance.COMPANY_META.get(company, {})
st.write(f"Bank feed label: **{meta.get('bank_feed','?')}**  ·  flex label: **{meta.get('flex_label')}**"
         + (f"  ·  scan label: **{meta.get('scan_label')}**" if meta.get("scan_label") else ""))

if company == "NewLane":
    st.info("NewLane mixes flex + scan in one remittance. Split is by cents: whole-dollar (.00) "
            "= scan package, non-round = flex. Scan invoices upload before scan payments.")

up = st.file_uploader("Remittance file (CSV/XLSX)", type=["csv", "xlsx", "xls"])
if up is None:
    st.info("Upload the finance company's remittance export.")
    st.stop()

raw = opd_adapter.read_upload(up)
cols = list(raw.columns)
st.write(f"{len(raw):,} rows.")

def _guess(cands, default=0):
    for i, c in enumerate(cols):
        if any(k in str(c).lower() for k in cands):
            return i
    return default

mc1, mc2, mc3 = st.columns(3)
customer_col = mc1.selectbox("Customer name column", cols, index=_guess(["customer_name", "customer name", "customer"]))
amount_col = mc2.selectbox("Amount column", cols, index=_guess(["payment_amount", "paid", "amount"]))
id_label = "Payment Invoice Number" if company == "GreatAmerica" else "Contract # / ID"
id_col = mc3.selectbox(f"{id_label} column (unique ref basis)", cols, index=_guess(["payment invoice", "contract"]))

if company == "NewLane":
    split = "by_cents"
elif company == "GreatAmerica":
    split = "all_flex"
else:
    split = {"By cents (.00 = scan)": "by_cents", "All flex": "all_flex", "All scan": "all_scan"}[
        st.radio("Split mode", ["By cents (.00 = scan)", "All flex", "All scan"], horizontal=True)
    ]

res = flex_finance.process_remittance(
    raw, company,
    customer_col=customer_col, amount_col=amount_col, id_col=id_col,
    payment_date=pay_date, invoice_date=inv_date, start_invoice_no=start_inv,
    name_map=nm, split=split,
)
s = res["summary"]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Flex payments", f"{s['flex_count']}  (${s['flex_total']:,.2f})")
m2.metric("Scan payments", f"{s['scan_count']}  (${s['scan_total']:,.2f})")
m3.metric("Remittance total", f"${s['total']:,.2f}")
m4.metric("Next invoice no", s["next_invoice_no"])

if res["unmapped"]:
    st.warning(f"{len(res['unmapped'])} names not in the QB name map — verify the Customer values: "
               + ", ".join(res["unmapped"][:10]))

st.divider()
st.subheader("1. Flex receive payments")
if not res["flex_payments"].empty:
    st.dataframe(res["flex_payments"], use_container_width=True, height=240)
    st.download_button("Download flex payments (xlsx)", saasant.to_xlsx_bytes(res["flex_payments"], "FlexPayments"),
                       file_name=f"{company}_FlexPayments_{pay_date}.xlsx")
else:
    st.caption("No flex rows.")

if not res["scan_invoices"].empty:
    st.subheader("2. Scan-package invoices — upload BEFORE scan payments")
    st.dataframe(res["scan_invoices"], use_container_width=True, height=220)
    st.download_button("Download scan invoices (xlsx)", saasant.to_xlsx_bytes(res["scan_invoices"], "ScanInvoices"),
                       file_name=f"{company}_ScanInvoices_{inv_date}.xlsx")

    st.subheader("3. Scan-package receive payments (Invoice column matches the invoices above)")
    st.dataframe(res["scan_payments"], use_container_width=True, height=220)
    st.download_button("Download scan payments (xlsx)", saasant.to_xlsx_bytes(res["scan_payments"], "ScanPayments"),
                       file_name=f"{company}_ScanPayments_{pay_date}.xlsx")

st.divider()
st.caption("Upload order: scan invoices -> flex payments -> scan payments. Match the combined "
           "total to the bank feed deposit after all uploads. One SaasAnt job at a time.")
