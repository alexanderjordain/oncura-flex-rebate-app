import datetime as dt

import streamlit as st

from core import auth, flex_finance, loaders, opd_adapter, saasant, store, ui

st.set_page_config(page_title="Finance Payment Import", layout="wide")
auth.require_login()
ui.inject()
auth.sidebar_identity()

ui.header("Finance Company Payment Import",
          "Remittance to SaasAnt imports. Every row gets a unique Ref No so payments don't collapse onto one customer.",
          kicker="Flex · Remittances")

# Base map from the store (GitHub on Cloud, local otherwise) plus any names resolved this
# session. The session overlay makes saved names apply immediately even when the backing store
# reloads from the public GitHub copy (which wouldn't yet have a local-only save).
nm_base = loaders.name_map()
session_adds = st.session_state.setdefault("name_map_additions", {})
nm = {**nm_base, "map": {**nm_base.get("map", {}), **session_adds}}

c1, c2, c3 = st.columns(3)
company = c1.selectbox("Finance company", ["NewLane", "OnePlace", "GreatAmerica"])
pay_date = c2.date_input("Payment date", value=dt.date.today())
inv_date = c3.date_input("Invoice date (scan packages)", value=dt.date.today())
start_inv = int(st.number_input("Starting scan Invoice No (QBO max + 1)", value=49000, step=1))

meta = flex_finance.COMPANY_META.get(company, {})
st.write(f"Bank feed label: **{meta.get('bank_feed','?')}**  ·  flex label: **{meta.get('flex_label')}**"
         + (f"  ·  scan label: **{meta.get('scan_label')}**" if meta.get("scan_label") else ""))

# Split rule: GreatAmerica is all flex (Maintenance). NewLane + OnePlace both mix flex + scan,
# separated by cents (whole-dollar = scan package, non-round = flex) with matching scan invoices.
if company == "GreatAmerica":
    split = "all_flex"
else:
    split = "by_cents"
    st.info(f"{company} mixes flex + scan in one remittance. Split is by cents: whole-dollar (.00) "
            "= scan package, non-round = flex. Scan invoices upload before scan payments.")

up = st.file_uploader("Remittance file (CSV/XLSX)", type=["csv", "xlsx", "xls"])
if up is None:
    st.info("Upload the finance company's remittance export.")
    st.stop()

raw = opd_adapter.read_remittance(up)
cols = list(raw.columns)
st.write(f"{len(raw):,} rows.")

g = flex_finance.guess_columns(company, cols)
mc1, mc2, mc3 = st.columns(3)
customer_col = mc1.selectbox("Customer name column", cols, index=cols.index(g["customer"]))
amount_col = mc2.selectbox("Amount column", cols, index=cols.index(g["amount"]))
id_label = "Payment Invoice Number" if company == "GreatAmerica" else "Contract # / ID"
id_col = mc3.selectbox(f"{id_label} column (unique ref basis)", cols, index=cols.index(g["id"]))

res = flex_finance.process_remittance(
    raw, company,
    customer_col=customer_col, amount_col=amount_col, id_col=id_col,
    payment_date=pay_date, invoice_date=inv_date, start_invoice_no=start_inv,
    name_map=nm, split=split,
)
s = res["summary"]
unmapped = [u for u in res["unmapped"] if u and u.lower() != "nan"]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Flex payments", f"{s['flex_count']}  (${s['flex_total']:,.2f})")
m2.metric("Scan payments", f"{s['scan_count']}  (${s['scan_total']:,.2f})")
m3.metric("Remittance total", f"${s['total']:,.2f}")
m4.metric("Next invoice no", s["next_invoice_no"])

# --- Interactive name resolver: collect QB display names for unmatched legal names, persist ---
if unmapped:
    st.divider()
    st.subheader(f"Resolve {len(unmapped)} unmatched customer name(s)")
    st.caption("Click the copy icon on a legal name → paste it into QuickBooks (Customers → search) "
               "→ copy the Display Name → paste it on the right. Saved mappings are remembered for "
               "next time (committed to the repo on Cloud).")
    hc1, hc2 = st.columns(2)
    hc1.markdown("**Legal name** (hover → click copy)")
    hc2.markdown("**QuickBooks display name**")
    qb_inputs = {}
    for i, legal in enumerate(unmapped):
        c1, c2 = st.columns(2)
        with c1:
            st.code(legal, language=None)  # st.code shows a one-click copy button
        with c2:
            qb_inputs[legal] = st.text_input(
                "qb", key=f"qbfix_{i}", label_visibility="collapsed",
                placeholder="paste QuickBooks display name",
            )
    if st.button("Save mappings", type="primary"):
        new_pairs = {legal.strip(): str(qb).strip() for legal, qb in qb_inputs.items() if str(qb).strip()}
        if new_pairs:
            # 1) apply immediately for this session
            st.session_state["name_map_additions"] = {**session_adds, **new_pairs}
            # 2) persist for the future (commits to the repo on Cloud; writes local file otherwise)
            persist = {**nm_base, "map": {**nm_base.get("map", {}), **st.session_state["name_map_additions"]}}
            ok, _ = store.save_json("name_map.json", persist, f"Add {len(new_pairs)} QB name mapping(s)")
            loaders.name_map.clear()
            if ok:
                st.success(f"Saved {len(new_pairs)} mapping(s) — committed to the repo for everyone.")
            else:
                st.success(f"Saved {len(new_pairs)} mapping(s) — applied now and stored on this "
                           "machine. On Cloud, set GITHUB_TOKEN to share them with all users.")
            st.rerun()
        else:
            st.warning("Enter at least one QuickBooks display name first.")
    st.warning("Resolve the names above before uploading these imports to QuickBooks.")

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
