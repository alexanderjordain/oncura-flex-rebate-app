"""FLEX Cycle — one page that walks through the monthly process.

Stage 1: Finance Company Payment Import (remittance -> SaasAnt flex/scan files)
Stage 2: Monthly Credit Memos (credit-memo SaasAnt file)
Stage 3: Unused / Overage (quarter-end recapture invoice + overage list)
"""
import datetime as dt

import streamlit as st

from core import (
    flex_credits, flex_finance, flex_overage, flex_unused,
    loaders, opd_adapter, saasant, store, ui,
)

ui.header("FLEX Cycle",
          "Walks the monthly process end-to-end: remittances → credits → unused / overage.",
          kicker="FLEX · Cycle")

flex = loaders.flex_master()
flex_clinics = flex.get("clinics", [])

tab_remit, tab_credits, tab_recap = st.tabs([
    "1. Finance Payment Imports",
    "2. Monthly Credit Memos",
    "3. Unused / Overage",
])

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — Finance Company Payment Imports
# ═══════════════════════════════════════════════════════════════════════════════
with tab_remit:
    st.caption("Upload a finance-company remittance — produces the SaasAnt receive-payments "
               "(and scan invoices + scan payments for OnePlace / NewLane).")

    nm_base = loaders.name_map()
    session_adds = st.session_state.setdefault("name_map_additions", {})
    nm = {**nm_base, "map": {**nm_base.get("map", {}), **session_adds}}

    c1, c2, c3 = st.columns(3)
    company = c1.selectbox("Finance company", ["NewLane", "OnePlace", "GreatAmerica"], key="remit_company")
    pay_date = c2.date_input("Payment date", value=dt.date.today(), key="remit_pay_date")
    inv_date = c3.date_input("Invoice date (scan packages)", value=dt.date.today(), key="remit_inv_date")
    start_inv = int(st.number_input("Starting scan Invoice No (QBO max + 1)",
                                    value=49000, step=1, key="remit_start_inv"))

    meta = flex_finance.COMPANY_META.get(company, {})
    st.write(f"Bank feed: **{meta.get('bank_feed','?')}**  ·  flex label: **{meta.get('flex_label')}**"
             + (f"  ·  scan label: **{meta.get('scan_label')}**" if meta.get("scan_label") else ""))

    split = "all_flex" if company == "GreatAmerica" else "by_cents"
    if company != "GreatAmerica":
        st.caption(f"{company} splits flex vs scan by cents: whole-dollar = scan, odd-cents = flex.")

    up = st.file_uploader("Remittance file (CSV/XLSX)", type=["csv", "xlsx", "xls"], key="remit_file")
    if up is None:
        st.info("Upload the finance company's remittance.")
    else:
        raw = opd_adapter.read_remittance(up)
        cols = list(raw.columns)
        st.write(f"{len(raw):,} rows.")
        g = flex_finance.guess_columns(company, cols)
        mc1, mc2, mc3 = st.columns(3)
        customer_col = mc1.selectbox("Customer name column", cols, index=cols.index(g["customer"]), key="remit_cust_col")
        amount_col = mc2.selectbox("Amount column", cols, index=cols.index(g["amount"]), key="remit_amt_col")
        id_label = "Payment Invoice Number" if company == "GreatAmerica" else "Contract # / ID"
        id_col = mc3.selectbox(f"{id_label} column", cols, index=cols.index(g["id"]), key="remit_id_col")

        res = flex_finance.process_remittance(
            raw, company,
            customer_col=customer_col, amount_col=amount_col, id_col=id_col,
            payment_date=pay_date, invoice_date=inv_date, start_invoice_no=start_inv,
            name_map=nm, split=split,
        )
        s = res["summary"]
        unmapped = [u for u in res["unmapped"] if u and u.lower() != "nan"]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Flex", f"{s['flex_count']}  (${s['flex_total']:,.2f})")
        m2.metric("Scan", f"{s['scan_count']}  (${s['scan_total']:,.2f})")
        m3.metric("Total", f"${s['total']:,.2f}")
        m4.metric("Next invoice", s["next_invoice_no"])

        if unmapped:
            st.divider()
            st.subheader(f"Resolve {len(unmapped)} unmatched customer name(s)")
            st.caption("Copy the legal name (click the copy icon) → paste into QuickBooks → "
                       "copy the Display Name → paste it on the right. Saved mappings persist.")
            qb_inputs = {}
            hc1, hc2 = st.columns(2)
            hc1.markdown("**Legal name** (hover → click copy)")
            hc2.markdown("**QuickBooks display name**")
            for i, legal in enumerate(unmapped):
                cc1, cc2 = st.columns(2)
                with cc1:
                    st.code(legal, language=None)
                with cc2:
                    qb_inputs[legal] = st.text_input(
                        "qb", key=f"qbfix_{i}", label_visibility="collapsed",
                        placeholder="paste QuickBooks display name",
                    )
            if st.button("Save mappings", type="primary", key="remit_save_map"):
                new_pairs = {legal.strip(): str(qb).strip() for legal, qb in qb_inputs.items() if str(qb).strip()}
                if new_pairs:
                    st.session_state["name_map_additions"] = {**session_adds, **new_pairs}
                    persist = {**nm_base, "map": {**nm_base.get("map", {}), **st.session_state["name_map_additions"]}}
                    ok, _ = store.save_json("name_map.json", persist, f"Add {len(new_pairs)} QB name mapping(s)")
                    loaders.name_map.clear()
                    st.success(
                        f"Saved {len(new_pairs)} mapping(s) " +
                        ("— committed to the repo for everyone." if ok else "— applied now and stored locally. Set GITHUB_TOKEN on Cloud to share.")
                    )
                    st.rerun()
                else:
                    st.warning("Enter at least one QuickBooks display name first.")
            st.warning("Resolve the names above before uploading these imports.")

        st.divider()
        st.subheader("Flex receive payments")
        if not res["flex_payments"].empty:
            st.dataframe(res["flex_payments"], use_container_width=True, height=240)
            st.download_button("Download flex payments (xlsx)",
                               saasant.to_xlsx_bytes(res["flex_payments"], "FlexPayments"),
                               file_name=f"{company}_FlexPayments_{pay_date}.xlsx",
                               key="remit_dl_flex")
        else:
            st.caption("No flex rows.")

        if not res["scan_invoices"].empty:
            st.subheader("Scan-package invoices (upload BEFORE scan payments)")
            st.dataframe(res["scan_invoices"], use_container_width=True, height=220)
            st.download_button("Download scan invoices (xlsx)",
                               saasant.to_xlsx_bytes(res["scan_invoices"], "ScanInvoices"),
                               file_name=f"{company}_ScanInvoices_{inv_date}.xlsx",
                               key="remit_dl_inv")
            st.subheader("Scan-package receive payments")
            st.dataframe(res["scan_payments"], use_container_width=True, height=220)
            st.download_button("Download scan payments (xlsx)",
                               saasant.to_xlsx_bytes(res["scan_payments"], "ScanPayments"),
                               file_name=f"{company}_ScanPayments_{pay_date}.xlsx",
                               key="remit_dl_scan")
        st.caption("Upload order: scan invoices → flex payments → scan payments. Match the combined "
                   "total to the bank feed after all uploads. One SaasAnt job at a time.")

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — Monthly Credit Memos
# ═══════════════════════════════════════════════════════════════════════════════
with tab_credits:
    st.caption("Generates the SaasAnt credit-memo import (item Flex-credits, class 03-Telemedicine). "
               "FLEX is closed to new entrants — the active list only shrinks.")

    today = dt.date.today()
    cc1, cc2, cc3 = st.columns(3)
    year = cc1.number_input("Year", value=today.year, step=1, key="cred_year")
    month = cc2.selectbox("Month", list(range(1, 13)), index=today.month - 1,
                          format_func=lambda m: dt.date(2000, m, 1).strftime("%B"),
                          key="cred_month")
    start_ref = cc3.number_input("Starting Credit Memo No (from QBO max + 1)",
                                 value=50000, step=1, key="cred_start_ref")

    df, next_ref = flex_credits.build_import(flex_clinics, int(year), int(month), int(start_ref))
    m1, m2 = st.columns(2)
    m1.metric("Credit memos", len(df))
    m2.metric("Total credits", f"${df['Product/Service Amount'].sum():,.2f}" if not df.empty else "$0.00")
    st.dataframe(df, use_container_width=True, height=380)

    mname = dt.date(2000, int(month), 1).strftime("%B")
    st.download_button(
        "Download credit-memo import (xlsx)",
        saasant.to_xlsx_bytes(df, f"FlexCredits{mname}{year}"),
        file_name=f"FlexCredits_{mname}_{year}.xlsx",
        disabled=df.empty,
        type="primary",
        key="cred_dl",
    )
    st.caption(f"Next available reference number after this batch: {next_ref}")

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — Unused Recapture + Overage
# ═══════════════════════════════════════════════════════════════════════════════
with tab_recap:
    st.caption("Monthly run — only clinics whose staggered quarter ENDS this month are processed. "
               "Activity vs threshold drives unused (recapture invoice) or overage (Tanya bills).")

    rc1, rc2, rc3 = st.columns(3)
    rec_year = int(rc1.number_input("Recapture year", value=today.year, step=1, key="recap_year"))
    rec_month = int(rc2.selectbox("Recapture month", list(range(1, 13)), index=today.month - 1,
                                  format_func=lambda m: dt.date(2000, m, 1).strftime("%B"),
                                  key="recap_month"))
    recap_start = int(rc3.number_input("Starting Invoice No (QBO max + 1)",
                                       value=60000, step=1, key="recap_start_ref"))

    win_start, win_end = flex_unused.quarter_window(rec_year, rec_month)
    group = [c for c in flex_clinics if c.get("active") and flex_unused.is_quarter_end(c.get("calendar_spread"), rec_month)]
    st.write(f"Quarter window: **{win_start:%b %d %Y} – {win_end:%b %d %Y}**  ·  "
             f"clinics with quarter-end this month: **{len(group)}**")

    rec_up = st.file_uploader("OPD activity export covering the quarter — Invoices OR case-grid",
                              type=["csv", "xlsx", "xls"], key="recap_file")
    sales_class = st.text_input("Sales class for the unused invoice", value="03-Telemedicine", key="recap_class")

    if rec_up is None:
        st.info("Upload an OPD activity export to compute unused / overage.")
    else:
        rec_raw = opd_adapter.read_upload(rec_up)
        rec_profile = opd_adapter.detect_profile(list(rec_raw.columns))
        if rec_profile == "case_grid":
            st.info("Case-grid profile: activity = sum of priced services per case (no AdminFee, STAT +$125).")
            activity = opd_adapter.flex_activity_from_case_grid(
                rec_raw, loaders.service_prices(), start=win_start, end=win_end,
            )
        else:
            activity = opd_adapter.flex_activity_from_invoices(rec_raw, start=win_start, end=win_end)
        st.caption(f"Parsed activity for {len(activity)} clinics from the {rec_profile} export.")

        import pandas as pd

        recap = flex_unused.compute_recapture(flex_clinics, activity, rec_year, rec_month)
        rdf = pd.DataFrame(recap)

        if rdf.empty:
            st.warning("No clinics have a quarter-end this month.")
        else:
            no_act = rdf[rdf["activity_match"] == "none"]
            if not no_act.empty:
                st.warning(f"{len(no_act)} quarter-end clinics had no matched OPD activity: "
                           + ", ".join(no_act["clinic_name"].head(8)))

            m1, m2, m3 = st.columns(3)
            m1.metric("Unused (recapture)", f"${rdf['unused'].fillna(0).sum():,.2f}")
            m2.metric("Overage", f"${rdf['overage'].fillna(0).sum():,.2f}")
            m3.metric("Clinics processed", len(rdf))

            st.subheader("Per-clinic")
            st.dataframe(rdf, use_container_width=True, height=320)

            udf, next_ref = flex_unused.build_unused_invoice_import(recap, rec_year, rec_month, recap_start, sales_class)
            st.subheader(f"Unused recapture invoice import — {len(udf)} rows")
            if not udf.empty:
                st.dataframe(udf, use_container_width=True)
                st.download_button("Download unused-flex invoice import (xlsx)",
                                   saasant.to_xlsx_bytes(udf, "UnusedFlex"),
                                   file_name=f"UnusedFlex_{dt.date(2000,rec_month,1):%b}_{rec_year}.xlsx",
                                   type="primary", key="recap_dl_unused")
                st.caption(f"Next available invoice number: {next_ref}")

            # ── Overage billing (Accounting SOP-6 + SOP-12) ───────────────────
            overs = flex_unused.overage_rows(recap)
            if overs:
                st.divider()
                st.subheader(f"Overage billing — {len(overs)} clinic(s) over threshold")
                st.caption("SOP-6 + SOP-12. Per-overage routing: One Place handles them if "
                           "submitted before their cutoff (typically the 5th of the following "
                           "month). Great America and New Lane have opted out — bill directly. "
                           "Self-Financed clinics: bill directly. Missed cutoff: bill directly.")

                cfg_all = loaders.config()
                cutoff = flex_overage.cutoff_date(rec_year, rec_month,
                    int((cfg_all.get("flex", {}).get("overage", {}) or {}).get("finance_partner_cutoff_day", 5)))
                today_d = dt.date.today()
                st.write(f"Finance partner cutoff: **{cutoff:%b %d %Y}**  ·  today: **{today_d:%b %d %Y}**  "
                         + (":green[on time]" if today_d <= cutoff else ":red[CUTOFF MISSED — all routes to direct bill]"))

                # Pre-existing credit offsets per clinic (operator-entered)
                with st.expander("Pre-existing credit offsets (SOP-12) — optional"):
                    st.caption("If a clinic has an unapplied credit balance in QBO, enter it here; "
                               "the app applies it to the overage and only bills the remainder.")
                    offset_df = pd.DataFrame([
                        {"Clinic (QB)": (o.get("qb_name") or o.get("clinic_name")),
                         "Gross overage": round(float(o["overage"]), 2),
                         "Pre-existing credit": 0.0}
                        for o in overs
                    ])
                    edited_offsets = st.data_editor(
                        offset_df, hide_index=True, use_container_width=True,
                        disabled=["Clinic (QB)", "Gross overage"],
                        key="overage_offsets",
                    )
                    credit_offsets = {r["Clinic (QB)"]: float(r["Pre-existing credit"] or 0)
                                      for _, r in edited_offsets.iterrows()}

                # Route + annotate
                annotated = flex_overage.annotate_overages(
                    overs, rec_year, rec_month, today_d, cfg_all, credit_offsets,
                )
                adf = pd.DataFrame(annotated)[[
                    "clinic_name", "qb_name", "finance_company", "quarterly_threshold",
                    "quarter_activity", "overage", "credit_applied", "net_overage",
                    "route", "escalation_flag",
                ]]
                st.dataframe(adf, use_container_width=True, height=260)

                # Escalation flags
                flagged = [r for r in annotated if r.get("escalation_flag")]
                if flagged:
                    names = ", ".join(r["clinic_name"] for r in flagged)
                    st.warning(f"⚠ Escalation clinic(s) in this batch: **{names}** — "
                               "communication may need to come from Marty / Accounting Manager (SOP-12).")

                # Direct-bill SaaSAnt invoice import (route = direct or missed_cutoff)
                direct_count = sum(1 for r in annotated if r["route"] in ("direct", "missed_cutoff") and r["net_overage"] > 0)
                if direct_count:
                    direct_start = int(st.number_input(
                        "Starting Invoice No for direct-bill overage invoices",
                        value=recap_start + 1000, step=1, key="overage_direct_start"))
                    didf, direct_next = flex_overage.build_direct_invoice_import(
                        annotated, rec_year, rec_month, direct_start, sales_class, cfg_all,
                    )
                    st.markdown("**Direct-bill overage invoices (SaaSAnt import)**")
                    st.markdown("""
- Each row will be a QBO invoice. **Void each invoice in QBO immediately after sending** — the
  revenue was already captured by the OPD invoices, so leaving these open overstates AR (SOP-6).
- Send the clinic an **Authorize.net payment link** for the amount, or email the QBO invoice PDF
  if they require a formal invoice.
- When payment arrives, apply it to zero out the clinic's flex account balance.
- **No refunds** per SOP-12 — even large overpayments stay on account for future overages
  (Marty's explicit approval required for exceptions).
""")
                    st.dataframe(didf, use_container_width=True, height=200)
                    st.download_button(
                        "Download direct-bill overage invoices (xlsx)",
                        saasant.to_xlsx_bytes(didf, "OverageDirect"),
                        file_name=f"OverageDirect_{dt.date(2000,rec_month,1):%b}_{rec_year}.xlsx",
                        type="primary", key="recap_dl_overage_direct",
                    )
                    st.caption(f"Next available invoice number after this batch: {direct_next}")

                # Finance partner submission list (route = partner)
                partner_rows = [r for r in annotated if r["route"] == "partner" and r["net_overage"] > 0]
                if partner_rows:
                    pdf = flex_overage.build_partner_submission(annotated, rec_year, rec_month)
                    st.markdown("**Finance partner submission (One Place)**")
                    st.markdown(f"""
- Submit this list to the finance partner **before {cutoff:%B %d, %Y}** (5th of next month).
- Confirm receipt. Track on FLEX Master with expected payment 5–6 months out.
""")
                    st.dataframe(pdf, use_container_width=True)
                    st.download_button(
                        "Download partner submission list (xlsx)",
                        saasant.to_xlsx_bytes(pdf, "OnePlaceSubmission"),
                        file_name=f"OnePlaceOverage_{dt.date(2000,rec_month,1):%b}_{rec_year}.xlsx",
                        key="recap_dl_overage_partner",
                    )
