"""FLEX Cycle — one page that walks through the monthly process.

Stage 1: Finance Company Payment Import (remittance -> SaasAnt flex/scan files)
Stage 2: Monthly Credit Memos (credit-memo SaasAnt file)
Stage 3: Unused / Overage (quarter-end recapture invoice + overage list)
"""
import datetime as dt

import streamlit as st

from core import (
    accounting_handoff, flex_credits, flex_finance, flex_overage, flex_unused,
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

    mc1, mc2 = st.columns([1, 2])
    company = mc1.selectbox("Finance company", ["NewLane", "OnePlace", "GreatAmerica"], key="remit_company")
    pay_date = mc2.date_input("Payment date", value=dt.date.today(), key="remit_pay_date")

    if company == "GreatAmerica":
        # GA is all-flex (Maintenance only) -> no scan invoices, so Invoice Date and the
        # scan Invoice-No starting ref are unused. Hide them to declutter the form.
        inv_date = pay_date
        start_inv = 49000
    else:
        c1, c2 = st.columns(2)
        inv_date = c1.date_input("Invoice date (scan packages)", value=dt.date.today(), key="remit_inv_date")
        start_inv = int(c2.number_input("Starting scan Invoice No (QBO max + 1)",
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

        if s["scan_count"] > 0:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Flex", f"{s['flex_count']}  (${s['flex_total']:,.2f})")
            m2.metric("Scan", f"{s['scan_count']}  (${s['scan_total']:,.2f})")
            m3.metric("Total", f"${s['total']:,.2f}")
            m4.metric("Next invoice", s["next_invoice_no"])
        else:
            m1, m2 = st.columns(2)
            m1.metric("Flex", f"{s['flex_count']}  (${s['flex_total']:,.2f})")
            m2.metric("Total", f"${s['total']:,.2f}")

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
        st.markdown(
            """
**Uploading to SaaSAnt**
1. Go to **[transactions.saasant.com](https://transactions.saasant.com)**.
2. Click **Bulk Upload**.
3. Pick the right import type for each file you downloaded above:
   - Scan-package **invoices** → select **Invoice**
   - Flex receive payments → select **Received Payments**
   - Scan receive payments → select **Received Payments**
4. Walk through the SaaSAnt wizard for each file.

**Order matters:** upload **scan invoices first**, then flex payments, then scan payments.
The scan payments reference the scan invoices by Invoice No, so the invoices must exist
in QBO first. Run **one SaaSAnt job at a time** — wait for each to complete before starting
the next. After all uploads, the combined total should match the bank-feed deposit.
"""
        )
        subj, body = accounting_handoff.finance_payment_email(
            company=company, pay_date=pay_date, summary=s, has_scan=s["scan_count"] > 0,
        )
        accounting_handoff.render_handoff(subj, body, key_prefix="remit_email")

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
    st.markdown(
        """
**Upload to SaaSAnt:** [transactions.saasant.com](https://transactions.saasant.com) →
**Bulk Upload** → **Credit Memo** → select the file → walk through the wizard.
"""
    )
    if not df.empty:
        subj, body = accounting_handoff.credit_memos_email(
            year=int(year), month=int(month), count=len(df),
            total=float(df["Product/Service Amount"].sum()),
            start_ref=int(start_ref), next_ref=int(next_ref),
        )
        accounting_handoff.render_handoff(subj, body, key_prefix="credits_email")

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — Unused Recapture + Overage
# ═══════════════════════════════════════════════════════════════════════════════
with tab_recap:
    st.caption("Monthly run — only clinics whose staggered quarter ENDS this month are processed. "
               "Activity vs threshold drives unused recapture (credit clawback) or overage "
               "(partner submission or direct-bill).")

    # ── Cycle inputs ──────────────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("**Cycle inputs**")
        rc1, rc2, rc3 = st.columns(3)
        rec_year = int(rc1.number_input("Recapture year", value=today.year, step=1, key="recap_year"))
        rec_month = int(rc2.selectbox("Recapture month", list(range(1, 13)), index=today.month - 1,
                                      format_func=lambda m: dt.date(2000, m, 1).strftime("%B"),
                                      key="recap_month"))
        recap_start = int(rc3.number_input("Starting Invoice No (QBO max + 1)",
                                           value=60000, step=1, key="recap_start_ref"))

        win_start, win_end = flex_unused.quarter_window(rec_year, rec_month)
        group = [c for c in flex_clinics
                 if c.get("active") and flex_unused.is_quarter_end(c.get("calendar_spread"), rec_month)]
        cfg_all_pre = loaders.config()
        cutoff_pre = flex_overage.cutoff_date(
            rec_year, rec_month,
            int((cfg_all_pre.get("flex", {}).get("overage", {}) or {}).get("finance_partner_cutoff_day", 5)),
        )
        today_d_pre = dt.date.today()
        cutoff_status = "✓ on time" if today_d_pre <= cutoff_pre else "⚠ cutoff missed — all routes to direct bill"

        bc1, bc2, bc3 = st.columns(3)
        bc1.markdown(f"**Quarter window**  \n{win_start:%b %d %Y} → {win_end:%b %d %Y}")
        bc2.markdown(f"**Clinics with quarter-end**  \n{len(group)}")
        bc3.markdown(f"**Partner cutoff** ({cutoff_pre:%b %d %Y})  \n{cutoff_status}")

        uc1, uc2 = st.columns([2, 1])
        rec_up = uc1.file_uploader(
            "OPD activity export covering the quarter (Invoices or case-grid)",
            type=["csv", "xlsx", "xls"], key="recap_file",
        )
        sales_class = uc2.text_input("Sales class", value="03-Telemedicine", key="recap_class")

    if rec_up is None:
        st.info("Upload an OPD activity export to compute unused / overage.")
    else:
        import pandas as pd

        rec_raw = opd_adapter.read_upload(rec_up)
        rec_profile = opd_adapter.detect_profile(list(rec_raw.columns))
        if rec_profile == "case_grid":
            activity = opd_adapter.flex_activity_from_case_grid(
                rec_raw, loaders.service_prices(), start=win_start, end=win_end,
            )
        else:
            activity = opd_adapter.flex_activity_from_invoices(rec_raw, start=win_start, end=win_end)

        recap = flex_unused.compute_recapture(flex_clinics, activity, rec_year, rec_month)
        rdf = pd.DataFrame(recap)

        if rdf.empty:
            st.warning("No clinics have a quarter-end this month.")
        else:
            cfg_all = loaders.config()
            cutoff = cutoff_pre
            today_d = today_d_pre

            # ── Cycle summary ─────────────────────────────────────────────────
            with st.container(border=True):
                st.markdown("**Cycle summary**")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Unused (recapture)", f"${rdf['unused'].fillna(0).sum():,.2f}")
                m2.metric("Overage (gross)", f"${rdf['overage'].fillna(0).sum():,.2f}")
                m3.metric("Clinics processed", len(rdf))
                m4.metric("Source profile", rec_profile)
                if rec_profile == "case_grid":
                    st.caption("Case-grid: activity = sum of priced services per case (no AdminFee, STAT +$125).")

                no_act = rdf[rdf["activity_match"] == "none"]
                if not no_act.empty:
                    st.warning(
                        f"{len(no_act)} quarter-end clinic(s) had no matched OPD activity: "
                        + ", ".join(no_act["clinic_name"].head(8))
                        + (" …" if len(no_act) > 8 else "")
                    )
                with st.expander(f"Per-clinic breakdown ({len(rdf)} clinics)"):
                    st.dataframe(rdf, use_container_width=True, height=320)

            # ── A. Unused recapture ───────────────────────────────────────────
            udf, next_ref = flex_unused.build_unused_invoice_import(
                recap, rec_year, rec_month, recap_start, sales_class,
            )
            with st.container(border=True):
                st.markdown("### A. Unused recapture invoices")
                if udf.empty:
                    st.caption("No clinics with unused balance this quarter — nothing to recapture.")
                else:
                    rc1, rc2 = st.columns([1, 1])
                    rc1.metric("Recapture invoices", len(udf))
                    rc2.metric("Recapture total", f"${udf['Product/Service Amount'].sum():,.2f}")
                    st.download_button(
                        "Download unused-flex invoice import (xlsx)",
                        saasant.to_xlsx_bytes(udf, "UnusedFlex"),
                        file_name=f"UnusedFlex_{dt.date(2000, rec_month, 1):%b}_{rec_year}.xlsx",
                        type="primary", key="recap_dl_unused",
                    )
                    st.caption(f"Next available invoice number: {next_ref}")
                    with st.expander("Preview invoice rows + SaaSAnt upload steps"):
                        st.dataframe(udf, use_container_width=True, height=220)
                        st.markdown(
                            "**Upload to SaaSAnt:** "
                            "[transactions.saasant.com](https://transactions.saasant.com) → "
                            "**Bulk Upload → Invoice** → select the file. Verify QBO P&L "
                            "Flex Credits line nets DOWN by the recapture total."
                        )

            # ── Overage routing (sets variables used by B and C below) ────────
            overs = flex_unused.overage_rows(recap)
            annotated = []
            flagged = []
            credit_offsets = {}
            if overs:
                with st.container(border=True):
                    st.markdown("### Overage routing — SOP-6 / SOP-12")
                    st.caption(
                        "OnePlace handles overages if submitted before the cutoff. Great America + "
                        "New Lane have opted out — bill directly. Self-Financed: direct. Missed "
                        "cutoff: direct."
                    )
                    with st.expander("Pre-existing credit offsets (optional)"):
                        st.caption(
                            "If a clinic already has an unapplied credit in QBO, enter it here — "
                            "the app applies it to the overage and only bills the remainder."
                        )
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
                        credit_offsets = {
                            r["Clinic (QB)"]: float(r["Pre-existing credit"] or 0)
                            for _, r in edited_offsets.iterrows()
                        }

                    annotated = flex_overage.annotate_overages(
                        overs, rec_year, rec_month, today_d, cfg_all, credit_offsets,
                    )
                    adf = pd.DataFrame(annotated)[[
                        "clinic_name", "finance_company", "overage",
                        "credit_applied", "net_overage", "route", "escalation_flag",
                    ]]
                    st.dataframe(adf, use_container_width=True, height=220)
                    flagged = [r for r in annotated if r.get("escalation_flag")]
                    if flagged:
                        names = ", ".join(r["clinic_name"] for r in flagged)
                        st.warning(
                            f"Escalation clinic(s): **{names}** — communication may need to "
                            "come from Marty / Accounting Manager (SOP-12)."
                        )

                # ── B. Direct-bill overages ───────────────────────────────────
                direct_count = sum(1 for r in annotated
                                   if r["route"] in ("direct", "missed_cutoff") and r["net_overage"] > 0)
                if direct_count:
                    direct_total = sum(float(r["net_overage"]) for r in annotated
                                       if r["route"] in ("direct", "missed_cutoff") and r["net_overage"] > 0)
                    with st.container(border=True):
                        st.markdown("### B. Direct-bill overage invoices")
                        bc1, bc2 = st.columns([1, 1])
                        bc1.metric("Invoices", direct_count)
                        bc2.metric("Total", f"${direct_total:,.2f}")
                        direct_start = int(st.number_input(
                            "Starting Invoice No",
                            value=recap_start + 1000, step=1, key="overage_direct_start",
                        ))
                        didf, direct_next = flex_overage.build_direct_invoice_import(
                            annotated, rec_year, rec_month, direct_start, sales_class, cfg_all,
                        )
                        st.download_button(
                            "Download direct-bill overage invoices (xlsx)",
                            saasant.to_xlsx_bytes(didf, "OverageDirect"),
                            file_name=f"OverageDirect_{dt.date(2000, rec_month, 1):%b}_{rec_year}.xlsx",
                            type="primary", key="recap_dl_overage_direct",
                        )
                        st.caption(f"Next available invoice number: {direct_next}")
                        with st.expander("Preview rows + SaaSAnt + send + void steps (SOP-6)"):
                            st.dataframe(didf, use_container_width=True, height=200)
                            st.markdown(
                                "1. **Upload to SaaSAnt** — "
                                "[transactions.saasant.com](https://transactions.saasant.com) → "
                                "**Bulk Upload → Invoice** → select the file.\n"
                                "2. **Send the clinic** an Authorize.net payment link (or email the QBO invoice PDF).\n"
                                "3. **VOID the QBO invoice immediately after sending** — revenue was "
                                "already captured by the OPD invoices, so leaving these open "
                                "overstates AR (SOP-6).\n"
                                "4. When payment arrives, apply it to zero out the clinic's flex account.\n"
                                "5. **No refunds** (SOP-12) — overpayments stay on account for future "
                                "overages. Marty must approve any exception."
                            )

                # ── C. Finance partner submission ─────────────────────────────
                partner_rows = [r for r in annotated
                                if r["route"] == "partner" and r["net_overage"] > 0]
                if partner_rows:
                    partner_total = sum(float(r["net_overage"]) for r in partner_rows)
                    with st.container(border=True):
                        st.markdown("### C. Finance partner submission (OnePlace)")
                        pc1, pc2, pc3 = st.columns(3)
                        pc1.metric("Clinics", len(partner_rows))
                        pc2.metric("Total", f"${partner_total:,.2f}")
                        pc3.metric("Submit by", f"{cutoff:%b %d %Y}")
                        pdf = flex_overage.build_partner_submission(annotated, rec_year, rec_month)
                        st.download_button(
                            "Download partner submission list (xlsx)",
                            saasant.to_xlsx_bytes(pdf, "OnePlaceSubmission"),
                            file_name=f"OnePlaceOverage_{dt.date(2000, rec_month, 1):%b}_{rec_year}.xlsx",
                            type="primary", key="recap_dl_overage_partner",
                        )
                        with st.expander("Preview rows + submission steps (SOP-12)"):
                            st.dataframe(pdf, use_container_width=True)
                            st.markdown(
                                f"- Submit to **OnePlace before {cutoff:%B %d, %Y}** (5th of next month).\n"
                                "- Confirm receipt.\n"
                                "- Track on FLEX Master with expected payment 5–6 months out."
                            )

            # ── Handoff email (covers unused + direct overage + partner) ──────
            _flagged_names = [r["clinic_name"] for r in flagged]
            _direct_total = sum(float(r["net_overage"]) for r in annotated
                                if r["route"] in ("direct", "missed_cutoff") and r["net_overage"] > 0)
            _direct_count = sum(1 for r in annotated
                                if r["route"] in ("direct", "missed_cutoff") and r["net_overage"] > 0)
            _partner_total = sum(float(r["net_overage"]) for r in annotated
                                 if r["route"] == "partner" and r["net_overage"] > 0)
            _partner_count = sum(1 for r in annotated
                                 if r["route"] == "partner" and r["net_overage"] > 0)
            _group_anchors = sorted({r["clinic_name"] for r in recap if r.get("group_id")})
            _unused_total = float(udf["Product/Service Amount"].sum()) if not udf.empty else 0.0
            _subj, _body = accounting_handoff.recapture_email(
                year=rec_year, month=rec_month,
                unused_total=_unused_total, unused_count=len(udf),
                direct_total=_direct_total, direct_count=_direct_count,
                partner_total=_partner_total, partner_count=_partner_count,
                cutoff_date=_cutoff,
                escalations=_flagged_names,
                group_anchors=_group_anchors,
            )
            accounting_handoff.render_handoff(_subj, _body, key_prefix="recap_email")
