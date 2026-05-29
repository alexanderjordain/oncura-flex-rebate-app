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
# STAGE 3 — Unused Recapture + Overage  (step-by-step wizard)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_recap:
    import io as _io
    import pandas as pd

    SS = st.session_state
    SS.setdefault("recap_step", 0)
    SS.setdefault("recap_year", today.year)
    SS.setdefault("recap_month", today.month)
    SS.setdefault("recap_sales_class", "03-Telemedicine")
    SS.setdefault("recap_start_ref", 60000)
    SS.setdefault("recap_direct_start", 61000)
    SS.setdefault("recap_uploaded_bytes", None)
    SS.setdefault("recap_uploaded_name", "")
    SS.setdefault("recap_credit_offsets", {})

    # Pull current state into locals
    rec_year = int(SS.recap_year)
    rec_month = int(SS.recap_month)
    sales_class = SS.recap_sales_class
    recap_start = int(SS.recap_start_ref)
    win_start, win_end = flex_unused.quarter_window(rec_year, rec_month)
    cfg_all = loaders.config()
    cutoff = flex_overage.cutoff_date(
        rec_year, rec_month,
        int((cfg_all.get("flex", {}).get("overage", {}) or {}).get("finance_partner_cutoff_day", 5)),
    )
    today_d = dt.date.today()

    # Compute the pipeline if a file is uploaded. Errors are captured into SS so the
    # upload step can render them inline (with full traceback) instead of just a banner.
    pipe = None
    SS["recap_pipe_error"] = None
    SS["recap_pipe_traceback"] = None
    if SS.recap_uploaded_bytes:
        try:
            f = _io.BytesIO(SS.recap_uploaded_bytes)
            f.name = SS.recap_uploaded_name or "upload.xls"
            rec_raw = opd_adapter.read_upload(f)
            rec_profile = opd_adapter.detect_profile(list(rec_raw.columns))
            if rec_profile == "case_grid":
                activity = opd_adapter.flex_activity_from_case_grid(
                    rec_raw, loaders.service_prices(), start=win_start, end=win_end,
                )
            else:
                activity = opd_adapter.flex_activity_from_invoices(rec_raw, start=win_start, end=win_end)
            recap = flex_unused.compute_recapture(flex_clinics, activity, rec_year, rec_month)
            pipe = {"profile": rec_profile, "activity": activity, "recap": recap}
        except Exception as e:
            import traceback as _tb
            SS["recap_pipe_error"] = f"{type(e).__name__}: {e}"
            SS["recap_pipe_traceback"] = _tb.format_exc()

    rdf = pd.DataFrame(pipe["recap"]) if pipe else pd.DataFrame()
    udf = pd.DataFrame()
    next_ref = recap_start
    if pipe and not rdf.empty:
        udf, next_ref = flex_unused.build_unused_invoice_import(
            pipe["recap"], rec_year, rec_month, recap_start, sales_class,
        )
    overs = flex_unused.overage_rows(pipe["recap"]) if pipe else []
    annotated = []
    if overs:
        annotated = flex_overage.annotate_overages(
            overs, rec_year, rec_month, today_d, cfg_all, SS.recap_credit_offsets,
        )
    direct_count = sum(1 for r in annotated if r["route"] in ("direct", "missed_cutoff") and r["net_overage"] > 0)
    partner_count = sum(1 for r in annotated if r["route"] == "partner" and r["net_overage"] > 0)
    flagged = [r for r in annotated if r.get("escalation_flag")]

    # Dynamic step list — only include steps that have something to show
    STEPS = [("setup", "Cycle setup"), ("upload", "Upload OPD activity")]
    if pipe and not rdf.empty:
        STEPS.append(("review", "Review activity"))
        if not udf.empty:
            STEPS.append(("recapture", "Unused recapture"))
        if overs:
            STEPS.append(("overage", "Overage routing & bills"))
        STEPS.append(("handoff", "Hand off to accounting"))
    total = len(STEPS)
    SS.recap_step = max(0, min(SS.recap_step, total - 1))
    step_key, step_label = STEPS[SS.recap_step]

    # Stepper
    st.markdown(f"**Step {SS.recap_step + 1} of {total} — {step_label}**")
    st.progress((SS.recap_step + 1) / total)
    breadcrumbs = "  ·  ".join(
        (f"**{lbl}**" if i == SS.recap_step else f":gray[{lbl}]")
        for i, (_, lbl) in enumerate(STEPS)
    )
    st.caption(breadcrumbs)

    # ── Step content ──────────────────────────────────────────────────────────
    with st.container(border=True):
        if step_key == "setup":
            st.markdown("### Cycle setup")
            st.caption(
                "Pick the cycle period and starting reference number. Only clinics whose staggered "
                "quarter ENDS in this month will be processed."
            )
            c1, c2 = st.columns(2)
            SS.recap_year = int(c1.number_input(
                "Recapture year", value=rec_year, step=1, key="w_recap_year"))
            SS.recap_month = int(c2.selectbox(
                "Recapture month", list(range(1, 13)), index=rec_month - 1,
                format_func=lambda m: dt.date(2000, m, 1).strftime("%B"),
                key="w_recap_month",
            ))
            c3, c4 = st.columns(2)
            SS.recap_sales_class = c3.text_input(
                "Sales class", value=sales_class, key="w_recap_class")
            SS.recap_start_ref = int(c4.number_input(
                "Starting Invoice No (QBO max + 1)", value=recap_start, step=1, key="w_recap_start_ref"))

            new_win_s, new_win_e = flex_unused.quarter_window(int(SS.recap_year), int(SS.recap_month))
            new_group = [c for c in flex_clinics
                         if c.get("active") and flex_unused.is_quarter_end(c.get("calendar_spread"), int(SS.recap_month))]
            new_cutoff = flex_overage.cutoff_date(
                int(SS.recap_year), int(SS.recap_month),
                int((cfg_all.get("flex", {}).get("overage", {}) or {}).get("finance_partner_cutoff_day", 5)),
            )
            cs = "on time" if today_d <= new_cutoff else "cutoff missed"
            st.info(
                f"**Quarter window:** {new_win_s:%b %d, %Y} → {new_win_e:%b %d, %Y}  ·  "
                f"**Qualifying clinics:** {len(new_group)}  ·  "
                f"**Partner cutoff:** {new_cutoff:%b %d, %Y} ({cs})"
            )

        elif step_key == "upload":
            st.markdown("### Upload OPD activity")
            st.caption(
                "Upload the OPD consult-grid export (or the OPD Invoices export) covering the quarter window."
            )
            rec_up = st.file_uploader(
                "OPD activity export",
                type=["csv", "xlsx", "xls"],
                key="w_recap_file",
            )
            if rec_up is not None:
                # The pipeline runs at the TOP of tab_recap, before this widget renders.
                # If we capture bytes now and DON'T rerun, the pipeline saw empty bytes for
                # this run and won't catch up until the next user interaction (which is why
                # clicking X used to make it "work" — that was the extra rerun). Force the
                # rerun immediately on a new upload so the pipeline picks it up right away.
                is_new = (rec_up.name != SS.get("recap_uploaded_name")
                          or SS.get("recap_uploaded_bytes") is None)
                SS.recap_uploaded_bytes = rec_up.getvalue()
                SS.recap_uploaded_name = rec_up.name
                if is_new:
                    st.rerun()
                st.success(f"Uploaded: **{rec_up.name}**  ({len(SS.recap_uploaded_bytes) // 1024:,} KB)")
            elif SS.recap_uploaded_bytes:
                st.info(
                    f"Previously uploaded: **{SS.recap_uploaded_name}**  — re-upload to replace, "
                    "or click Next to continue."
                )
            else:
                st.warning("Upload a file to continue.")
            if SS.get("recap_pipe_error"):
                st.error(
                    f"**Could not parse this file:**  `{SS['recap_pipe_error']}`\n\n"
                    "Try re-uploading, or upload a different export (case-grid xls or "
                    "Invoices xlsx)."
                )
                if SS.get("recap_pipe_traceback"):
                    with st.expander("Full traceback (share this if asking for help)"):
                        st.code(SS["recap_pipe_traceback"], language="text")
            if pipe:
                total_qualifying = sum(
                    1 for c in flex_clinics
                    if c.get("active") and flex_unused.is_quarter_end(c.get("calendar_spread"), rec_month)
                )
                pm1, pm2, pm3 = st.columns(3)
                pm1.metric("Source profile", pipe["profile"])
                pm2.metric("Clinics with activity", len(pipe["activity"]))
                pm3.metric(
                    "Qualifying for this month",
                    f"{len(rdf)} / {total_qualifying}",
                    help="Rows emitted (group anchors only) over all active clinics whose quarter ends this month. "
                         "Difference = group members (e.g. Mohnacky / River Trail / PR-vets non-anchors) rolled into their anchor's row.",
                )

        elif step_key == "review":
            st.markdown("### Review activity")
            st.caption("What the app found in the uploaded export. Sanity-check before generating files.")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Source", pipe["profile"])
            m2.metric("Clinics with activity", len(pipe["activity"]))
            m3.metric("Unused (recapture)", f"${rdf['unused'].fillna(0).sum():,.2f}")
            m4.metric("Overage (gross)", f"${rdf['overage'].fillna(0).sum():,.2f}")
            if pipe["profile"] == "case_grid":
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

        elif step_key == "recapture":
            st.markdown("### A. Unused recapture invoices")
            st.caption(
                "These clinics fell short of their threshold. Recapture the unused portion of the credit "
                "Oncura already issued so the P&L Flex Credits line nets down correctly."
            )
            rc1, rc2 = st.columns(2)
            rc1.metric("Invoices", len(udf))
            rc2.metric("Recapture total", f"${udf['Product/Service Amount'].sum():,.2f}")
            st.download_button(
                "Download unused-flex invoice import",
                saasant.to_xlsx_bytes(udf, "UnusedFlex"),
                file_name=f"UnusedFlex_{dt.date(2000, rec_month, 1):%b}_{rec_year}.xlsx",
                type="primary",
                key="w_recap_dl_unused",
            )
            st.caption(f"Next available invoice number: {next_ref}")
            st.markdown(
                "**Upload to SaaSAnt:** "
                "[transactions.saasant.com](https://transactions.saasant.com) → "
                "**Bulk Upload → Invoice** → select the file."
            )
            with st.expander("Preview the invoice rows"):
                st.dataframe(udf, use_container_width=True, height=220)

        elif step_key == "overage":
            st.markdown(f"### Overage routing & bills — {len(overs)} clinic(s) over threshold")
            st.caption(
                "OnePlace handles overages if submitted before the cutoff. Great America + New Lane "
                "have opted out — bill directly. Self-Financed: direct. Missed cutoff: direct."
            )
            with st.expander("Pre-existing credit offsets (optional)"):
                st.caption(
                    "If an over-threshold clinic has an unapplied credit in QBO, enter it here — the app "
                    "applies it to the overage and only bills the remainder."
                )
                offset_seed = pd.DataFrame([
                    {"Clinic (QB)": (o.get("qb_name") or o.get("clinic_name")),
                     "Gross overage": round(float(o["overage"]), 2),
                     "Pre-existing credit": float(
                         SS.recap_credit_offsets.get(o.get("qb_name") or o.get("clinic_name"), 0) or 0
                     )}
                    for o in overs
                ])
                edited = st.data_editor(
                    offset_seed, hide_index=True, use_container_width=True,
                    disabled=["Clinic (QB)", "Gross overage"],
                    key="w_recap_offsets_editor",
                )
                SS.recap_credit_offsets = {
                    r["Clinic (QB)"]: float(r["Pre-existing credit"] or 0)
                    for _, r in edited.iterrows()
                }

            adf = pd.DataFrame(annotated)[[
                "clinic_name", "finance_company", "overage",
                "credit_applied", "net_overage", "route", "escalation_flag",
            ]]
            st.dataframe(adf, use_container_width=True, height=220)
            if flagged:
                names = ", ".join(r["clinic_name"] for r in flagged)
                st.warning(
                    f"Escalation clinic(s): **{names}** — communication may need to come from "
                    "Marty / Accounting Manager (SOP-12)."
                )

            if direct_count:
                direct_total = sum(float(r["net_overage"]) for r in annotated
                                   if r["route"] in ("direct", "missed_cutoff") and r["net_overage"] > 0)
                with st.container(border=True):
                    st.markdown("#### B. Direct-bill overage invoices")
                    bc1, bc2 = st.columns(2)
                    bc1.metric("Invoices", direct_count)
                    bc2.metric("Total", f"${direct_total:,.2f}")
                    SS.recap_direct_start = int(st.number_input(
                        "Starting Invoice No (direct-bill)", value=int(SS.recap_direct_start),
                        step=1, key="w_overage_direct_start",
                    ))
                    didf, direct_next = flex_overage.build_direct_invoice_import(
                        annotated, rec_year, rec_month, int(SS.recap_direct_start), sales_class, cfg_all,
                    )
                    st.download_button(
                        "Download direct-bill overage invoices",
                        saasant.to_xlsx_bytes(didf, "OverageDirect"),
                        file_name=f"OverageDirect_{dt.date(2000, rec_month, 1):%b}_{rec_year}.xlsx",
                        type="primary",
                        key="w_recap_dl_direct",
                    )
                    st.caption(f"Next available invoice number: {direct_next}")
                    with st.expander("Send & void steps (SOP-6)"):
                        st.markdown(
                            "1. **Upload to SaaSAnt** → "
                            "[transactions.saasant.com](https://transactions.saasant.com) → "
                            "**Bulk Upload → Invoice** → select the file.\n"
                            "2. **Send each clinic** an Authorize.net payment link (or QBO invoice PDF).\n"
                            "3. **VOID each QBO invoice immediately after sending** — revenue was already "
                            "captured by OPD invoices, so leaving them open overstates AR (SOP-6).\n"
                            "4. Apply payment to zero out the clinic's account when received.\n"
                            "5. **No refunds** (SOP-12) — overpayments stay on account for future overages."
                        )

            if partner_count:
                partner_total = sum(float(r["net_overage"]) for r in annotated
                                    if r["route"] == "partner" and r["net_overage"] > 0)
                with st.container(border=True):
                    st.markdown("#### C. Partner submission (OnePlace)")
                    pc1, pc2, pc3 = st.columns(3)
                    pc1.metric("Clinics", partner_count)
                    pc2.metric("Total", f"${partner_total:,.2f}")
                    pc3.metric("Submit by", f"{cutoff:%b %d, %Y}")
                    pdf = flex_overage.build_partner_submission(annotated, rec_year, rec_month)
                    st.download_button(
                        "Download partner submission list",
                        saasant.to_xlsx_bytes(pdf, "OnePlaceSubmission"),
                        file_name=f"OnePlaceOverage_{dt.date(2000, rec_month, 1):%b}_{rec_year}.xlsx",
                        type="primary",
                        key="w_recap_dl_partner",
                    )
                    with st.expander("Submission steps"):
                        st.markdown(
                            f"- Submit to **OnePlace before {cutoff:%B %d, %Y}**.\n"
                            "- Confirm receipt.\n"
                            "- Track expected payment 5–6 months out on FLEX Master."
                        )

        elif step_key == "handoff":
            st.markdown("### Hand off to accounting")
            st.caption(
                "Email accounting with this cycle's results + next steps. The body is pre-filled with "
                "all the numbers, escalations, and the SaaSAnt + QBO action items."
            )
            unused_total = float(udf["Product/Service Amount"].sum()) if not udf.empty else 0.0
            direct_total = sum(float(r["net_overage"]) for r in annotated
                               if r["route"] in ("direct", "missed_cutoff") and r["net_overage"] > 0)
            partner_total = sum(float(r["net_overage"]) for r in annotated
                                if r["route"] == "partner" and r["net_overage"] > 0)
            group_anchors = sorted({r["clinic_name"] for r in pipe["recap"] if r.get("group_id")})
            flagged_names = [r["clinic_name"] for r in flagged]
            subj, body = accounting_handoff.recapture_email(
                year=rec_year, month=rec_month,
                unused_total=unused_total, unused_count=len(udf),
                direct_total=direct_total, direct_count=direct_count,
                partner_total=partner_total, partner_count=partner_count,
                cutoff_date=cutoff,
                escalations=flagged_names,
                group_anchors=group_anchors,
            )
            accounting_handoff.render_handoff(subj, body, key_prefix="w_recap_email")

    # ── Navigation ────────────────────────────────────────────────────────────
    can_back = SS.recap_step > 0
    can_next = SS.recap_step < total - 1
    next_blocked_reason = ""
    if step_key == "upload" and not SS.recap_uploaded_bytes:
        can_next = False
        next_blocked_reason = "Upload a file before continuing."
    elif step_key == "upload" and pipe is None:
        can_next = False
        next_blocked_reason = "Could not parse the uploaded file."

    nav_b, nav_msg, nav_n = st.columns([1, 4, 1])
    if can_back:
        if nav_b.button("← Back", key=f"w_recap_back_{SS.recap_step}"):
            SS.recap_step -= 1
            st.rerun()
    if not can_next and next_blocked_reason:
        nav_msg.caption(f":orange[{next_blocked_reason}]")
    if SS.recap_step < total - 1:
        if nav_n.button("Next →", key=f"w_recap_next_{SS.recap_step}",
                        type="primary", disabled=not can_next):
            SS.recap_step += 1
            st.rerun()
    else:
        nav_n.markdown("**Done ✓**")
