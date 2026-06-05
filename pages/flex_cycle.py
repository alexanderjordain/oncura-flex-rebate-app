"""Payment Cycle — one page that walks through the monthly process for BOTH
FLEX and scan-package (pass-through) payments together. Surfaced in the
sidebar under the "FLEX & Pass-Through" section.

Stage 1: Finance Company Payment Import (remittance -> SaasAnt flex/scan files)
Stage 2: Monthly Credit Memos (credit-memo SaasAnt file)
Stage 3: Unused / Overage (quarter-end recapture + overage; runs every month
         for whichever clinic group's staggered quarter is closing)
"""
import datetime as dt
from contextlib import contextmanager

import streamlit as st

from core import (
    audit, auth, flex_credits, flex_finance, flex_overage, flex_unused,
    ledger, loaders, opd_adapter, saasant, store, ui,
)


@contextmanager
def safe_stage(label: str):
    """Catch + render an error inside a tab so one broken stage doesn't kill the others.

    Chains with `with tab_X, safe_stage(...):` so the existing indentation stays the same.
    """
    try:
        yield
    except Exception as _e:
        import traceback as _tb

        st.error(f"**{label} failed:** `{type(_e).__name__}: {_e}`")
        st.caption(
            "The other stages are still usable. Share the traceback below if you need a fix."
        )
        with st.expander("Show full traceback"):
            st.code(_tb.format_exc(), language="text")

ui.header("Payment Cycle",
          "Handles FLEX and scan-package (pass-through) payments together. "
          "Generates SaasAnt files for QBO import — humans approve every QBO posting.",
          kicker="FLEX & Pass-Through · Cycle")

flex = loaders.flex_master()
flex_clinics = flex.get("clinics", [])

tab_overview, tab_remit, tab_credits, tab_recap = st.tabs([
    "Overview",
    "1. Finance Payment Imports",
    "2. Monthly Credit Memos",
    "3. Unused / Overage",
])

# ═══════════════════════════════════════════════════════════════════════════════
# OVERVIEW — minimal landing: stage cards + this-month + details on demand
# ═══════════════════════════════════════════════════════════════════════════════
with tab_overview, safe_stage("Overview"):
    st.markdown(
        """
        <style>
        .pc-stage-card {
            padding: .9rem 1rem; border-radius: 10px; background: var(--surface);
            border: 1px solid var(--line); border-top: 3px solid var(--blue);
            height: 100%;
        }
        .pc-stage-card.s2 { border-top-color: var(--green); }
        .pc-stage-card.s3 { border-top-color: var(--amber); }
        .pc-stage-num {
            display: inline-block; width: 1.4rem; height: 1.4rem; line-height: 1.4rem;
            text-align: center; border-radius: 50%; font-family: var(--mono);
            font-weight: 700; font-size: .75rem; color: var(--surface);
            margin-right: .35rem; background: var(--blue);
        }
        .pc-stage-card.s2 .pc-stage-num { background: var(--green); }
        .pc-stage-card.s3 .pc-stage-num { background: var(--amber); }
        .pc-stage-card .pc-title { font-family: var(--serif); font-size: 1.05rem;
            color: var(--blue); font-weight: 600; margin: 0 0 .35rem 0; line-height: 1.2; }
        .pc-stage-card .pc-cadence {
            font-family: var(--mono); font-size: .7rem; text-transform: uppercase;
            letter-spacing: .1em; color: var(--muted); margin-bottom: .4rem;
        }
        .pc-stage-card p { margin: 0 !important; font-size: .9rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        st.markdown(
            """<div class="pc-stage-card s1">
            <p class="pc-title"><span class="pc-stage-num">1</span> Finance Payment Imports</p>
            <p class="pc-cadence">As remittances arrive</p>
            <p>Process each finance-company remittance. Splits flex vs scan, builds the SaasAnt receive-payments file.</p>
            </div>""",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            """<div class="pc-stage-card s2">
            <p class="pc-title"><span class="pc-stage-num">2</span> Monthly Credit Memos</p>
            <p class="pc-cadence">Once all the month's remittances are in</p>
            <p>Build one credit memo per FLEX payment in the month. <i>One Flex payment in, one credit out.</i></p>
            </div>""",
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            """<div class="pc-stage-card s3">
            <p class="pc-title"><span class="pc-stage-num">3</span> Unused / Overage</p>
            <p class="pc-cadence">Monthly · one clinic group</p>
            <p>Three staggered quarter cycles run in parallel. Each month, one group closes — the wizard auto-filters.</p>
            </div>""",
            unsafe_allow_html=True,
        )

    # Vertical spacer between the stage cards and the this-month banner.
    st.markdown('<div style="height: 1.25rem"></div>', unsafe_allow_html=True)

    # This-month callout. Stage 3 runs the month AFTER the quarter-end, closing
    # out the group whose quarter just ended. So map current month → the group
    # whose quarter ended LAST month.
    #
    # Quarter-end months per group:
    #   Calendar:         Mar / Jun / Sep / Dec  → Stage 3 in Apr, Jul, Oct, Jan
    #   March-April-May:  Feb / May / Aug / Nov  → Stage 3 in Mar, Jun, Sep, Dec
    #   May-June-July:    Jan / Apr / Jul / Oct  → Stage 3 in Feb, May, Aug, Nov
    GROUP_BY_RUN_MONTH = {
        1:  ("Calendar",         22, "December"),
        2:  ("May-June-July",    20, "January"),
        3:  ("March-April-May",  39, "February"),
        4:  ("Calendar",         22, "March"),
        5:  ("May-June-July",    20, "April"),
        6:  ("March-April-May",  39, "May"),
        7:  ("Calendar",         22, "June"),
        8:  ("May-June-July",    20, "July"),
        9:  ("March-April-May",  39, "August"),
        10: ("Calendar",         22, "September"),
        11: ("May-June-July",    20, "October"),
        12: ("March-April-May",  39, "November"),
    }
    today = dt.date.today()
    group, n, qend_month = GROUP_BY_RUN_MONTH[today.month]
    st.info(
        f"**This month ({today.strftime('%B %Y')}):** closing the **{group}** "
        f"group ({n} clinics) — their quarter ended **{qend_month} {today.year if today.month > 1 else today.year - 1}**. "
        f"In Stage 3, pick year + {qend_month} as the month you just closed.",
        icon=":material/event_available:",
    )

    with st.expander("Full procedure for each stage"):
        st.markdown(
            """
**Stage 1 — Finance Payment Imports** *(run per remittance)*
1. Download the remittance file from OnePlace / NewLane / GreatAmerica.
2. Pick the company + payment date, upload the file.
3. Resolve any unmapped clinic names (mappings persist automatically).
4. Sanity-check the total and download the SaasAnt file(s).

**Stage 2 — Monthly Credit Memos** *(run after all of that month's Stage 1 remittances are in — typically the following month)*
1. Pick the year and month you're closing.
2. Review the credit-memo total — one credit per ledger payment for that month.
3. Download the SaasAnt file.
4. Late remittance? Re-run safely — ledger dedup prevents double-issuing.

**Stage 3 — Unused / Overage** *(run every month for the closing group)*
1. Pick the year and the month you just closed; the wizard auto-filters to the
   group whose quarter is ending.
2. Review recapture totals (internal entries; not mailed) and the overage list.
3. For each overage: submit to finance partner or direct-bill per SOP-6.
            """
        )

    with st.expander("Common pitfalls"):
        st.warning(
            "**Forgetting Stage 3.** Runs every month for whichever group's "
            "quarter is ending — not just at calendar quarter-end. Each clinic "
            "is recapped 4×/year, but you run Stage 3 12×/year.",
            icon=":material/event_repeat:",
        )
        st.info(
            "**Wrong month in Stage 2.** Pick the month payments arrived FOR, "
            "not the month they were *received in*.",
            icon=":material/calendar_month:",
        )

    st.caption(
        "Click a tab above to start. Deeper docs: `docs/FLEX_PROGRAM_EXPLAINED.md`, "
        "`docs/ACCOUNTING_HANDOFF.md`, `docs/RECOVERY.md`."
    )

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — Finance Company Payment Imports
# ═══════════════════════════════════════════════════════════════════════════════
with tab_remit, safe_stage("Stage 1 — Finance Payment Imports"):
    SS = st.session_state

    # Two-step wizard: setup metadata first (company, dates, invoice no) so it's
    # mandatory and intentional, then upload the file. Prevents the failure
    # mode where an operator uploads first and then realizes they had the wrong
    # company/date selected.
    REMIT_STEPS = [("setup", "Cycle setup"), ("upload", "Upload & process")]
    SS.setdefault("remit_step", 0)
    SS["remit_step"] = max(0, min(SS["remit_step"], len(REMIT_STEPS) - 1))
    step_key, step_label = REMIT_STEPS[SS["remit_step"]]
    ui.scroll_top_on_step_change("flex_stage1_remit", SS["remit_step"])

    st.markdown(f"**Step {SS['remit_step'] + 1} of {len(REMIT_STEPS)} — {step_label}**")
    st.progress((SS['remit_step'] + 1) / len(REMIT_STEPS))
    st.caption(
        "  ·  ".join(
            (f"**{lbl}**" if i == SS["remit_step"] else f":gray[{lbl}]")
            for i, (_, lbl) in enumerate(REMIT_STEPS)
        )
    )

    # PERSISTENT keys (no _w suffix) — survive across step transitions because
    # they're not tied to a widget. The widgets in step 1 use _w-suffixed keys;
    # we mirror their values into the persistent keys on each render so step 2
    # can read them after the step 1 widgets unmount. Streamlit clears widget-keyed
    # SS values when the widget isn't rendered — that was making step 2 fall back
    # to the NewLane default regardless of what step 1 selected.
    SS.setdefault("remit_company", "NewLane")
    SS.setdefault("remit_pay_date", dt.date.today())
    SS.setdefault("remit_start_inv", 50000)

    nm_base = loaders.name_map()
    session_adds = st.session_state.setdefault("name_map_additions", {})
    nm = {**nm_base, "map": {**nm_base.get("map", {}), **session_adds}}

    if step_key == "setup":
        st.caption(
            "Lock in the company and dates for this batch. The file uploader "
            "appears on the next step once these are set."
        )
        mc1, mc2 = st.columns([1, 2])
        company_options = ["NewLane", "OnePlace", "GreatAmerica"]
        company = mc1.selectbox(
            "Finance company", company_options,
            index=company_options.index(SS["remit_company"]) if SS["remit_company"] in company_options else 0,
            key="remit_company_w",
        )
        pay_date = mc2.date_input(
            "Payment date",
            value=SS["remit_pay_date"],
            key="remit_pay_date_w",
        )

        if company == "GreatAmerica":
            # GA is all-flex (Maintenance only) -> no scan invoices, so the starting
            # scan Invoice No is unused. Hide it to declutter the form.
            st.caption("GreatAmerica is all-flex — no starting scan invoice number needed.")
            start_inv = 50000
        else:
            # Invoice date for scan packages is always the same as the payment date in
            # the live workflow, so we mirror payment date into invoice date instead of
            # asking twice. Only the starting scan invoice number needs its own input.
            start_inv = int(st.number_input(
                "Starting scan Invoice No (QBO max + 1)",
                value=int(SS["remit_start_inv"]),
                step=1,
                key="remit_start_inv_w",
            ))

        # MIRROR widget -> persistent so step 2 sees the right values.
        SS["remit_company"] = company
        SS["remit_pay_date"] = pay_date
        SS["remit_start_inv"] = start_inv

        meta = flex_finance.COMPANY_META.get(company, {})
        st.write(f"Bank feed: **{meta.get('bank_feed','?')}**  ·  flex label: **{meta.get('flex_label')}**"
                 + (f"  ·  scan label: **{meta.get('scan_label')}**" if meta.get("scan_label") else ""))

        if company != "GreatAmerica":
            # OnePlace + NewLane: whole-dollar = scan, odd-cents = flex
            # (Confirmed against May OPC pass-through file: Easthaven $595.00 + Innovative
            # Animal Care $295.00 both have "04..." contracts but are scan packages —
            # SOP-9's "04 = FLEX" prefix rule has exceptions, cents is the reliable signal.)
            st.caption(f"{company} splits flex vs scan by cents: whole-dollar = scan, odd-cents = flex.")

        st.divider()
        if st.button("Next ▶  Upload remittance", type="primary", key="remit_setup_next"):
            # Belt-and-suspenders re-mirror right before the transition.
            SS["remit_company"] = company
            SS["remit_pay_date"] = pay_date
            SS["remit_start_inv"] = start_inv
            SS["remit_step"] = 1
            st.rerun()

    elif step_key == "upload":
        # Read setup values from session_state (set in step 1). Invoice date
        # mirrors payment date — they're always the same in the live workflow.
        # Defensive: re-read SS at every render so any drift surfaces immediately.
        company = SS.get("remit_company", "NewLane")
        pay_date = SS.get("remit_pay_date", dt.date.today())
        inv_date = pay_date
        if company == "GreatAmerica":
            start_inv = 50000
            split = "all_flex"
        else:
            start_inv = int(SS.get("remit_start_inv", 50000))
            split = "by_cents"

        # Diagnostic: prominently display the values being used so a mismatch with
        # what the operator picked is immediately visible. (Bug guard — the user
        # reported files coming out labeled NewLane regardless of selection.)
        st.info(
            f":material/info: **Processing as:** company=**{company}**, "
            f"payment date=**{pay_date}**, starting invoice #=**{start_inv if company != 'GreatAmerica' else '—'}**. "
            "If any of these are wrong, click **◀ Back to setup** below.",
            icon=":material/checklist:",
        )

        # Setup recap
        with st.container(border=True):
            r1, r2, r3 = st.columns(3)
            r1.markdown(f"**Company**<br>{company}", unsafe_allow_html=True)
            r2.markdown(f"**Payment & invoice date**<br>{pay_date}", unsafe_allow_html=True)
            r3.markdown(
                f"**Start invoice #**<br>{start_inv if company != 'GreatAmerica' else '—'}",
                unsafe_allow_html=True,
            )
            b1, b2 = st.columns(2)
            if b1.button("◀ Back to setup", key="remit_upload_back", use_container_width=True):
                SS["remit_step"] = 0
                st.rerun()
            if b2.button("Set up new import", key="remit_upload_reset",
                         use_container_width=True,
                         help="Clear the uploaded file and start fresh — use this between back-to-back remittances."):
                # Reset everything for a fresh import
                for k in ("remit_file", "remit_file_override",
                          "remit_cust_col", "remit_amt_col", "remit_id_col",
                          "remit_reissue_ack"):
                    SS.pop(k, None)
                SS["remit_step"] = 0
                st.rerun()

        up = st.file_uploader("Remittance file (CSV/XLSX)", type=["csv", "xlsx", "xls"], key="remit_file")
        if up is None:
            st.info("Upload the finance company's remittance.")
        else:
            file_bytes = up.getvalue()
            prior_file = ledger.check_file_seen(file_bytes)
            if prior_file:
                st.error(
                    f"**This exact file was already processed on "
                    f"{prior_file.get('uploaded_at', '?')[:10]}** "
                    f"({prior_file.get('row_count', '?')} rows, company "
                    f"{prior_file.get('company', '?')}, filename "
                    f"`{prior_file.get('filename', '?')}`). Re-uploading would risk "
                    f"double-posting payments to QBO. Row-level dedup will still skip already-imported "
                    f"payments below — but verify before downloading."
                )
                if not st.checkbox(
                    "I've verified this is intentional (e.g., recovering from a partial earlier import). "
                    "Proceed with row-level dedup.",
                    key="remit_file_override",
                ):
                    st.stop()

            raw = opd_adapter.read_remittance(up)
            cols = list(raw.columns)
            st.write(f"{len(raw):,} rows.")
            g = flex_finance.guess_columns(company, cols)
            id_label = "Payment Invoice Number" if company == "GreatAmerica" else "Contract # / ID"
            # Reset any stale widget state if a previous upload's columns aren't in this file
            # (e.g., switched from OnePlace to NewLane between uploads).
            for k, fallback in [("remit_cust_col", g["customer"]),
                                ("remit_amt_col", g["amount"]),
                                ("remit_id_col", g["id"])]:
                if SS.get(k) not in cols:
                    SS[k] = fallback
            # Tuck the override controls into a collapsed expander so non-tech-savvy users
            # aren't overwhelmed by three extra dropdowns in the typical case where
            # guess_columns picked the right ones.
            with st.expander(
                f"Column mapping (auto-detected: Customer = `{SS['remit_cust_col']}`, "
                f"Amount = `{SS['remit_amt_col']}`, {id_label} = `{SS['remit_id_col']}`) — "
                "open if a column looks wrong",
                expanded=False,
            ):
                mc1, mc2, mc3 = st.columns(3)
                mc1.selectbox("Customer name column", cols, key="remit_cust_col")
                mc2.selectbox("Amount column", cols, key="remit_amt_col")
                mc3.selectbox(f"{id_label} column", cols, key="remit_id_col")
            customer_col = SS["remit_cust_col"]
            amount_col = SS["remit_amt_col"]
            id_col = SS["remit_id_col"]

            res = flex_finance.process_remittance(
                raw, company,
                customer_col=customer_col, amount_col=amount_col, id_col=id_col,
                payment_date=pay_date, invoice_date=inv_date, start_invoice_no=start_inv,
                name_map=nm, split=split,
            )

            # ── Row-level dedup against the processed-payments ledger ──────────────
            def _row_payment_dicts(df, kind):
                if df is None or df.empty or id_col not in df.columns:
                    return []
                out = []
                for i in range(len(df)):
                    amt_val = df["Amount"].iloc[i] if "Amount" in df.columns else df[amount_col].iloc[i]
                    out.append({
                        "kind": kind,
                        "contract": df[id_col].iloc[i],
                        "qb_customer": df["Customer"].iloc[i] if "Customer" in df.columns else "",
                        "payment_date": pay_date,
                        "amount": amt_val,
                    })
                return out

            flex_rows = _row_payment_dicts(res["flex_payments"], "flex")
            scan_rows = _row_payment_dicts(res["scan_payments"], "scan")
            all_rows = flex_rows + scan_rows
            all_fps = [ledger.fingerprint(company, r["kind"], r["contract"], r["payment_date"], r["amount"])
                       for r in all_rows]
            seen_fps = ledger.check_payments_seen(all_fps)

            # ── Reissue check: rows that weren't exact-duplicates but match an existing
            #    ledger row on (company, kind, contract, amount) with a DIFFERENT payment_date.
            #    These look like reissues — same money, different date — and shouldn't be
            #    silently treated as net-new. Surface for confirm-and-proceed.
            novel_rows = [r for r, fp in zip(all_rows, all_fps) if fp not in seen_fps]
            possible_reissues = ledger.check_possible_reissues(company, novel_rows) if novel_rows else []
            if possible_reissues:
                st.warning(
                    f":material/warning: **{len(possible_reissues)} payment(s) look like possible reissues** — "
                    "same company / contract / amount as a prior ledger row but a different payment date. "
                    "Confirm these are intentional reissues, not accidental re-imports of the same money "
                    "with a re-typed date.",
                )
                with st.expander("Show possible reissues", expanded=False):
                    for r in possible_reissues:
                        inc = r["incoming"]
                        ex = r["existing"][0]
                        st.markdown(
                            f"- **{inc.get('qb_customer') or '(unmapped)'}** · "
                            f"contract `{inc.get('contract')}` · "
                            f"${float(inc['amount']):,.2f} · "
                            f"prior payment date **`{ex['payment_date']}`** → "
                            f"new payment date **`{ledger._date_iso(inc['payment_date'])}`**"
                        )
                SS["remit_reissue_ack"] = st.checkbox(
                    "I confirm these are intentional reissues — proceed.",
                    value=SS.get("remit_reissue_ack", False),
                    key="remit_reissue_ack_widget",
                )
                if not SS["remit_reissue_ack"]:
                    st.info("Tick the box above once you've verified the reissues to enable the downloads.")
                    st.stop()
            else:
                SS["remit_reissue_ack"] = True

            if seen_fps:
                skipped_flex = sum(1 for fp, r in zip(all_fps[:len(flex_rows)], flex_rows) if fp in seen_fps)
                skipped_scan = sum(1 for fp, r in zip(all_fps[len(flex_rows):], scan_rows) if fp in seen_fps)
                st.warning(
                    f"**Ledger already contains {len(seen_fps)} of these payments** "
                    f"(flex: {skipped_flex}, scan: {skipped_scan}). They've been removed from the "
                    f"downloads below so you don't double-post."
                )
                # Filter the output dataframes in-place
                keep_flex = [i for i, fp in enumerate(all_fps[:len(flex_rows)]) if fp not in seen_fps]
                keep_scan = [i for i, fp in enumerate(all_fps[len(flex_rows):]) if fp not in seen_fps]
                if not res["flex_payments"].empty:
                    res["flex_payments"] = res["flex_payments"].iloc[keep_flex].reset_index(drop=True)
                if not res["scan_payments"].empty:
                    res["scan_payments"] = res["scan_payments"].iloc[keep_scan].reset_index(drop=True)
                    # Scan invoices are 1:1 with scan payments by position — filter together
                    if not res["scan_invoices"].empty:
                        res["scan_invoices"] = res["scan_invoices"].iloc[keep_scan].reset_index(drop=True)
                # Rebuild summary metrics from the filtered frames
                res["summary"]["flex_count"] = len(res["flex_payments"])
                res["summary"]["scan_count"] = len(res["scan_payments"])
                res["summary"]["flex_total"] = round(float(res["flex_payments"]["Amount"].sum()), 2) if not res["flex_payments"].empty else 0.0
                res["summary"]["scan_total"] = round(float(res["scan_payments"]["Amount"].sum()), 2) if not res["scan_payments"].empty else 0.0
                res["summary"]["total"] = round(res["summary"]["flex_total"] + res["summary"]["scan_total"], 2)

            s = res["summary"]
            unmapped = [u for u in res["unmapped"] if u and u.lower() != "nan"]

            # ── Flex/scan crossover sanity check: NewLane and OnePlace split by cents
            #    (whole-dollar = scan, odd-cents = flex). A 100/0 or 0/100 ratio is
            #    almost always a sign of bad data or wrong split rule. GreatAmerica is
            #    intentionally all-flex (Maintenance only) — skip the check there.
            if company in ("OnePlace", "NewLane") and s["total"] > 0:
                flex_pct = s["flex_total"] / s["total"] if s["total"] else 0
                scan_pct = s["scan_total"] / s["total"] if s["total"] else 0
                if flex_pct >= 0.97 or scan_pct >= 0.97:
                    dominant = "flex" if flex_pct >= 0.97 else "scan"
                    st.warning(
                        f":material/warning: **Unusual flex/scan split**: {flex_pct*100:.1f}% flex / "
                        f"{scan_pct*100:.1f}% scan for this {company} remittance. "
                        f"OnePlace and NewLane usually run a mix; an all-{dominant} file may mean "
                        "the wrong split rule was applied or the file is mis-classified. "
                        "Cross-check against the source remittance before proceeding."
                    )

            # ── Intra-file total check: raw file rows/amounts vs. what the app is importing
            # Exclude rows where the contract/ID column is blank — those are summary/total/footnote
            # rows in the source file (e.g., OnePlace's "Pass-Thru received: $X" line at the
            # bottom). Real payment rows always have a contract #.
            raw_payment_mask = raw[id_col].astype(str).str.strip().replace({'nan': '', 'None': ''}).ne('')
            raw_amounts = raw.loc[raw_payment_mask, amount_col].map(opd_adapter.coerce_amount)
            raw_total = round(float(raw_amounts.sum()), 2)
            raw_nonzero = int((raw_amounts != 0).sum())
            diff = round(raw_total - s["total"], 2)
            deduped_amount = sum(float(r["amount"]) for r, fp in zip(all_rows, all_fps) if fp in seen_fps)
            if abs(diff) > 0.01 or raw_nonzero != (s["flex_count"] + s["scan_count"]):
                # Quiet diagnostic only — operator can sanity-check what the app counted
                # against what the file says. The dollar signs are escaped so Streamlit's
                # markdown doesn't interpret $...$ as inline LaTeX and mangle the numbers.
                st.caption(
                    f"File: {raw_nonzero} non-zero rows totalling **\\${raw_total:,.2f}**  ·  "
                    f"App importing: {s['flex_count'] + s['scan_count']} rows totalling **\\${s['total']:,.2f}**  ·  "
                    f"Δ **\\${diff:,.2f}** (of which ledger dedup accounts for \\${deduped_amount:,.2f})"
                )

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
            st.markdown("### Downloads")
            st.caption(
                "Three SaasAnt imports below. Click a preview expander on the left "
                "to inspect the rows; the download button on the right is the primary "
                "action — that's the file you upload to SaasAnt."
            )

            # Each row: bold download button on the LEFT (primary action), muted
            # preview expander on the RIGHT (optional inspection). The button
            # column is wider to telegraph priority — that's the file Stage 1
            # produced, hitting download is the whole point.
            def _download_row(*, title: str, df, fname_stem: str, fname_date,
                              sheet_name: str, dl_key: str, height: int = 240):
                col_dl, col_prev = st.columns([3, 2], gap="medium")
                with col_dl:
                    st.download_button(
                        f":material/download:  Download {sheet_name} (xlsx)",
                        saasant.to_xlsx_bytes(df, sheet_name),
                        file_name=f"{company}_{fname_stem}_{fname_date}.xlsx",
                        key=dl_key,
                        type="primary",
                        use_container_width=True,
                    )
                with col_prev:
                    with st.expander(f":gray[{title}  ·  {len(df)} rows  ·  preview]",
                                     expanded=False):
                        st.dataframe(df, use_container_width=True, height=height)

            if not res["flex_payments"].empty:
                _download_row(
                    title="Flex receive payments",
                    df=res["flex_payments"],
                    fname_stem="FlexPayments", fname_date=pay_date,
                    sheet_name="FlexPayments", dl_key="remit_dl_flex",
                )
            else:
                st.caption("No flex rows.")

            if not res["scan_invoices"].empty:
                _download_row(
                    title="Scan-package invoices  ·  upload BEFORE scan payments",
                    df=res["scan_invoices"],
                    fname_stem="ScanInvoices", fname_date=inv_date,
                    sheet_name="ScanInvoices", dl_key="remit_dl_inv",
                    height=220,
                )
                _download_row(
                    title="Scan-package receive payments",
                    df=res["scan_payments"],
                    fname_stem="ScanPayments", fname_date=pay_date,
                    sheet_name="ScanPayments", dl_key="remit_dl_scan",
                    height=220,
                )
            st.markdown(
                """
    **Uploading to SaasAnt**
    1. Go to **[transactions.saasant.com](https://transactions.saasant.com)**.
    2. Click **Bulk Upload**.
    3. Pick the right import type for each file you downloaded above:
       - Scan-package **invoices** → select **Invoice**
       - Flex receive payments → select **Received Payments**
       - Scan receive payments → select **Received Payments**
    4. Walk through the SaasAnt wizard for each file.

    **Order matters:** upload **scan invoices first**, then flex payments, then scan payments.
    The scan payments reference the scan invoices by Invoice No, so the invoices must exist
    in QBO first. Run **one SaasAnt job at a time** — wait for each to complete before starting
    the next. After all uploads, the combined total should match the bank-feed deposit.
    """
            )

            # ── Mark batch processed: write to ledger so future re-uploads are caught ────
            st.divider()
            rows_to_record = [
                r for r, fp in zip(all_rows, all_fps) if fp not in seen_fps
            ]
            ack_disabled = len(rows_to_record) == 0
            stage1_initials = ui.initials_input(
                "stage1_audit_initials",
                disabled=ack_disabled,
            )
            if ack_disabled:
                st.info("Nothing new to record (all rows were already in the ledger).")
            if not ack_disabled and st.button(
                f"Mark {len(rows_to_record)} payment(s) as imported",
                key="remit_mark_processed", type="primary",
                disabled=not stage1_initials,
            ):
                stage1_approver = stage1_initials or auth.current_role()
                ok, added, msg = ledger.record_batch(
                    file_content=file_bytes,
                    filename=up.name,
                    company=company,
                    payments=rows_to_record,
                    note=f"Stage 1 / {company} / pay_date={pay_date}",
                )
                # Append to immutable audit manifest alongside the ledger record
                audit_outputs = []
                for label, df_out in (
                    ("flex_payments", res["flex_payments"]),
                    ("scan_invoices", res["scan_invoices"]),
                    ("scan_payments", res["scan_payments"]),
                ):
                    if df_out is not None and not df_out.empty:
                        audit_outputs.append({
                            "name": label,
                            "sha256": audit.output_hash_df(df_out),
                            "row_count": len(df_out),
                            "total": round(float(df_out["Amount"].sum()), 2) if "Amount" in df_out.columns else None,
                        })
                audit.record_cycle(
                    cycle_type="stage1_finance_payment",
                    approver=stage1_approver,
                    year=pay_date.year, month=pay_date.month,
                    params={
                        "company": company,
                        "payment_date": pay_date.isoformat(),
                        "invoice_date": inv_date.isoformat(),
                        "start_invoice_no": start_inv,
                        "skipped_already_seen": len(seen_fps),
                    },
                    source_file={
                        "name": up.name,
                        "sha256": ledger.file_hash(file_bytes),
                        "size_bytes": len(file_bytes),
                    },
                    outputs=audit_outputs,
                    note=f"{added} new payment(s) recorded; {len(seen_fps)} already in ledger",
                )
                if ok:
                    st.success(f"Recorded {added} payment(s) in the ledger + audit manifest. {msg}")
                else:
                    st.warning(
                        f"Recorded {added} locally (no GitHub commit). On Cloud, this means "
                        "the ledger won't persist past the session — set GITHUB_TOKEN in secrets."
                    )

            # Next-step reminder for Acct SOP-2: bank-feed matching in QBO.
            # NOTE: `meta` is defined earlier in the upload-step branch but not here,
            # so look it up locally from the company in scope on the review step.
            sop2_meta = flex_finance.COMPANY_META.get(company, {})
            st.divider()
            _bank = sop2_meta.get("bank_feed", "the bank feed")
            st.info(
                f":material/account_balance: **Next step — Acct SOP-2: {_bank} in QBO.**  \n"
                f"After the SaasAnt files above are imported to QBO, open **{_bank}** in QBO and "
                f"match the deposit against the receive payments you just created. "
                f"Confirm the deposit amount equals the **Total** shown on this page; if there's a "
                f"mismatch, stop and reconcile before posting the next remittance.",
                icon=":material/account_balance:",
            )

            # Bottom-of-page "Set up new import" — same handler as the top-card button,
            # for operators who've scrolled all the way down and don't want to scroll back.
            st.divider()
            if st.button("Set up new import", key="remit_upload_reset_bottom",
                         use_container_width=True,
                         help="Clear the uploaded file and start fresh — use this between back-to-back remittances."):
                for k in ("remit_file", "remit_file_override",
                          "remit_cust_col", "remit_amt_col", "remit_id_col",
                          "remit_reissue_ack"):
                    SS.pop(k, None)
                SS["remit_step"] = 0
                st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — Monthly Credit Memos
# ═══════════════════════════════════════════════════════════════════════════════
with tab_credits, safe_stage("Stage 2 — Monthly Credit Memos"):
    SS = st.session_state

    # Two-step wizard: setup (year/month/start ref) first so the operator can't
    # accidentally generate a batch for the wrong month after seeing the numbers.
    CRED_STEPS = [("setup", "Cycle setup"), ("review", "Review & mark imported")]
    SS.setdefault("cred_step", 0)
    SS["cred_step"] = max(0, min(SS["cred_step"], len(CRED_STEPS) - 1))
    step_key, step_label = CRED_STEPS[SS["cred_step"]]
    ui.scroll_top_on_step_change("flex_stage2_cred", SS["cred_step"])

    st.markdown(f"**Step {SS['cred_step'] + 1} of {len(CRED_STEPS)} — {step_label}**")
    st.progress((SS['cred_step'] + 1) / len(CRED_STEPS))
    st.caption(
        "  ·  ".join(
            (f"**{lbl}**" if i == SS["cred_step"] else f":gray[{lbl}]")
            for i, (_, lbl) in enumerate(CRED_STEPS)
        )
    )

    today = dt.date.today()
    # Default to the previous month — FLEX credits are dated to the prior month's last day
    default_month = today.month - 1 or 12
    default_year = today.year if today.month > 1 else today.year - 1
    SS.setdefault("cred_year", default_year)
    SS.setdefault("cred_month", default_month)
    SS.setdefault("cred_start_ref", 50000)

    if step_key == "setup":
        st.caption(
            "Lock in the month and starting credit memo number for this batch. Review numbers "
            "and the import button appear on the next step."
        )
        cc1, cc2, cc3 = st.columns(3)
        # Use _w-suffixed widget keys; mirror to persistent keys after render so
        # step 2 (where these widgets aren't shown) can still read them.
        year_w = int(cc1.number_input(
            "Year", value=int(SS["cred_year"]), step=1, key="cred_year_w"))
        month_w = int(cc2.selectbox(
            "Month", list(range(1, 13)),
            index=int(SS["cred_month"]) - 1,
            format_func=lambda m: dt.date(2000, m, 1).strftime("%B"),
            key="cred_month_w",
        ))
        start_ref_w = int(cc3.number_input(
            "Starting Credit Memo No (from QBO max + 1)",
            value=int(SS["cred_start_ref"]), step=1, key="cred_start_ref_w"))
        SS["cred_year"] = year_w
        SS["cred_month"] = month_w
        SS["cred_start_ref"] = start_ref_w
        st.info(
            "Generates one credit memo per FLEX payment received that month (SaasAnt format: "
            "item Flex-credits, class 03-Telemedicine). Multi-payment months produce multi-credit "
            "batches; clinics that didn't pay get nothing. Quarter-end reconciliation (Stage 3) "
            "trues up against actual usage."
        )
        st.divider()
        if st.button("Next ▶  Review credit memos", type="primary", key="cred_setup_next"):
            SS["cred_year"] = year_w
            SS["cred_month"] = month_w
            SS["cred_start_ref"] = start_ref_w
            SS["cred_step"] = 1
            st.rerun()

    elif step_key == "review":
        year = int(SS.get("cred_year", default_year))
        month = int(SS.get("cred_month", default_month))
        start_ref = int(SS.get("cred_start_ref", 50000))
        mname = dt.date(2000, month, 1).strftime("%B")

        # Diagnostic: prominently display the values being used so a mismatch is visible.
        st.info(
            f":material/info: **Generating credit memos for:** **{mname} {year}**, "
            f"starting Credit Memo # **{start_ref}**. "
            "If any of these are wrong, click **◀ Back to setup** below.",
            icon=":material/checklist:",
        )

        # Setup recap with back / set-up-new-month buttons
        with st.container(border=True):
            r1, r2, r3 = st.columns(3)
            r1.markdown(f"**Year**<br>{year}", unsafe_allow_html=True)
            r2.markdown(f"**Month**<br>{mname}", unsafe_allow_html=True)
            r3.markdown(f"**Start credit memo #**<br>{start_ref}", unsafe_allow_html=True)
            b1, b2 = st.columns(2)
            if b1.button("◀ Back to setup", key="cred_review_back", use_container_width=True):
                SS["cred_step"] = 0
                st.rerun()
            if b2.button("Set up new month", key="cred_review_reset",
                         use_container_width=True,
                         help="Reset year/month/start-ref and start fresh — use this when moving on to the next month."):
                for k in ("cred_year", "cred_month", "cred_start_ref", "cred_legacy_show"):
                    SS.pop(k, None)
                SS["cred_step"] = 0
                st.rerun()

        # ── Pull ledger rows for the target month ──────────────────────────────────
        payments = ledger.flex_payments_for_month(year, month)
        df, next_ref, skipped, source_payments = flex_credits.build_import_from_payments(
            flex_clinics, payments, year, month, start_ref,
        )

        # ── Payments Remitted in {Month} panel ─────────────────────────────────────
        head_l, head_r = st.columns([5, 1])
        head_l.markdown(f"### Payments Remitted in {mname} {year}")
        if head_r.button("↻ Refresh ledger", key="cred_refresh",
                         help="Re-read the ledger from GitHub. Use this if you just recorded "
                              "payments in Stage 1 and they don't appear below."):
            try:
                st.cache_data.clear()
                loaders.clear_caches()
            except Exception:
                pass
            st.rerun()
        if not payments:
            st.warning(
                f"**No FLEX payments recorded in the ledger for {mname} {year}.** "
                f"Either Stage 1 hasn't been run for this month yet, or no remittances landed "
                f"with kind=flex for that month. Hit ↻ Refresh ledger above if you just "
                f"recorded payments. You can also run the **legacy active-list** mode below to bootstrap."
            )
        else:
            # Per-clinic payment count (so multi-payment clinics show up clearly)
            from collections import Counter
            by_qb = Counter()
            amount_by_qb = {}
            for p in payments:
                k = p.get("qb_customer") or p.get("contract") or "?"
                by_qb[k] += 1
                amount_by_qb[k] = amount_by_qb.get(k, 0.0) + float(p.get("amount") or 0)
            multi = {k: n for k, n in by_qb.items() if n > 1}
            pm1, pm2, pm3 = st.columns(3)
            pm1.metric("Payments received", len(payments))
            pm2.metric("Distinct clinics", len(by_qb))
            pm3.metric("Multi-payment clinics", len(multi))
            if multi:
                st.caption(
                    "**Multi-payment clinics this month** (will receive one credit memo per payment): "
                    + ", ".join(f"{k} ({n}×)" for k, n in sorted(multi.items()))
                )

            # Clinics that ARE active but received NO payment this month
            paid_keys = {(p.get("qb_customer") or "").strip().lower() for p in payments} | \
                        {str(p.get("contract") or "").strip() for p in payments}
            unpaid = []
            for c in flex_clinics:
                if not c.get("active") or not (c.get("monthly_credit") or 0) > 0:
                    continue
                qbn = (c.get("qb_name") or "").strip().lower()
                contracts = [str(c.get(k) or "").strip() for k in
                             ("contract_oneplace", "contract_greatamerica", "contract_newlane")]
                if qbn in paid_keys or any(cv and cv in paid_keys for cv in contracts):
                    continue
                unpaid.append(c.get("qb_name") or c.get("clinic_name"))
            if unpaid:
                with st.expander(f"Active clinics with NO payment in {mname} ({len(unpaid)}) — no credit memo generated"):
                    st.caption("Either they paid ahead in a prior month, are between cycles, or their FLEX program ended.")
                    st.write(unpaid)

        # ── Generated credit memos preview ─────────────────────────────────────────
        st.markdown("### Credit memos to be generated")
        m1, m2 = st.columns(2)
        m1.metric("Credit memos", len(df))
        m2.metric(
            "Total credits",
            f"${df['Product/Service Amount'].sum():,.2f}" if not df.empty else "$0.00",
        )
        if not df.empty:
            with st.expander(f"Preview rows  ·  {len(df)} credit memo(s)", expanded=False):
                st.dataframe(df, use_container_width=True, height=380)

        st.download_button(
            "Download credit-memo import (xlsx)",
            saasant.to_xlsx_bytes(df, f"FlexCredits{mname}{year}"),
            file_name=f"FlexCredits_{mname}_{year}.xlsx",
            disabled=df.empty,
            type="primary",
            key="cred_dl",
        )
        st.caption(
            f"Next available reference number after this batch: {next_ref}  ·  "
            f":gray[*Quarter-end true-up: unused balance and overage are reconciled in Stage 3 — "
            f"pre-paid credits don't carry past the quarter.*]"
        )

        # ── Mark batch as generated: records to audit + ledger so re-runs are caught ──
        if not df.empty:
            st.divider()
            # Use the SOURCE payment's fingerprint (immutable in the ledger) as the
            # contract-equivalent for credit-memo dedup. Previously we used the QB
            # Customer name, which is mutable — a typo fix or rename silently broke
            # dedup and let Stage 2 re-issue the same credit on the next run.
            emitted_payments_for_ledger = []
            for src, (_, row) in zip(source_payments, df.iterrows()):
                emitted_payments_for_ledger.append({
                    "kind": "credit_memo",
                    "contract": src.get("fingerprint") or row["Customer"],
                    "qb_customer": row["Customer"],
                    "payment_date": row["Credit Memo Date"],
                    "amount": float(row["Product/Service Amount"]),
                })
            emitted_fps = [
                ledger.fingerprint(
                    "INTERNAL", "credit_memo", r["contract"], r["payment_date"], r["amount"]
                )
                for r in emitted_payments_for_ledger
            ]
            already_emitted = ledger.check_payments_seen(emitted_fps)
            if already_emitted:
                st.warning(
                    f"**{len(already_emitted)} credit memo(s) already recorded for this month.** "
                    f"The download above still contains them with NEW Credit Memo Nos — DO NOT upload "
                    f"those rows to QBO again. (Future enhancement: filter them out of the download.)"
                )
            stage2_initials = ui.initials_input("stage2_audit_initials")
            if st.button(
                f"Mark {len(df)} credit memo(s) as generated",
                key="cred_mark_processed", type="primary",
                disabled=not stage2_initials,
            ):
                ok_ledger, added, _ = ledger.record_batch(
                    file_content=None,
                    filename=f"FlexCredits_{mname}_{year}.xlsx",
                    company="INTERNAL",
                    payments=emitted_payments_for_ledger,
                    note=f"Stage 2 / {mname} {year}",
                )
                audit.record_cycle(
                    cycle_type="stage2_credit_memo",
                    approver=stage2_initials or auth.current_role(),
                    year=year, month=month,
                    params={
                        "start_ref": start_ref, "next_ref": next_ref,
                        "payment_count": len(payments),
                        "skipped_unmapped": len(skipped),
                    },
                    source_file=None,
                    outputs=[{
                        "name": "credit_memo_import",
                        "sha256": audit.output_hash_df(df),
                        "row_count": len(df),
                        "total": round(float(df["Product/Service Amount"].sum()), 2),
                    }],
                    note=f"{added} new credit memo(s) recorded; {len(already_emitted)} were already in ledger",
                )
                if ok_ledger:
                    st.success(f"Recorded {added} credit memo(s) in ledger + audit manifest.")
                else:
                    st.warning(
                        f"Recorded {added} locally (no GitHub commit). "
                        "Set GITHUB_TOKEN in secrets for persistent dedup on Cloud."
                    )

        # ── Legacy bootstrap mode ──────────────────────────────────────────────────
        with st.expander(":gray[Legacy mode — active-list credit memos (bootstrap only)]"):
            st.caption(
                "Use only when the processed-payments ledger is empty for the target month "
                "(e.g., first run after migration). This generates one credit memo per active clinic "
                "regardless of payment status — the legacy behavior."
            )
            if st.checkbox("Show legacy active-list import", key="cred_legacy_show"):
                df_legacy, next_ref_legacy = flex_credits.build_import(
                    flex_clinics, year, month, start_ref,
                )
                l1, l2 = st.columns(2)
                l1.metric("Legacy credit memos", len(df_legacy))
                l2.metric(
                    "Legacy total",
                    f"${df_legacy['Product/Service Amount'].sum():,.2f}" if not df_legacy.empty else "$0.00",
                )
                st.dataframe(df_legacy, use_container_width=True, height=300)
                st.download_button(
                    "Download LEGACY credit-memo import",
                    saasant.to_xlsx_bytes(df_legacy, f"FlexCreditsLEGACY{mname}{year}"),
                    file_name=f"FlexCredits_LEGACY_{mname}_{year}.xlsx",
                    disabled=df_legacy.empty,
                    key="cred_dl_legacy",
                )

        st.markdown(
            """
    **Upload to SaasAnt:** [transactions.saasant.com](https://transactions.saasant.com) →
    **Bulk Upload** → **Credit Memo** → select the file → walk through the wizard.
    """
        )

        # Bottom-of-page "Set up new month" — mirror of the top-card button so an
        # operator who's scrolled down doesn't have to scroll back up.
        st.divider()
        if st.button("Set up new month", key="cred_review_reset_bottom",
                     use_container_width=True,
                     help="Reset year/month/start-ref and start fresh — use this when moving on to the next month."):
            for k in ("cred_year", "cred_month", "cred_start_ref", "cred_legacy_show"):
                SS.pop(k, None)
            SS["cred_step"] = 0
            st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — Unused Recapture + Overage  (step-by-step wizard)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_recap, safe_stage("Stage 3 — Unused / Overage"):
    import io as _io
    import pandas as pd

    SS = st.session_state
    SS.setdefault("recap_step", 0)
    SS.setdefault("recap_year", today.year)
    SS.setdefault("recap_month", today.month)
    SS.setdefault("recap_sales_class", "03-Telemedicine")
    SS.setdefault("recap_start_ref", 50000)
    SS.setdefault("recap_direct_start", 50000)
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
    STEPS = [("setup", "Cycle setup"), ("upload", "Upload OPD activity")]  # handoff step removed — accountant runs the import directly
    if pipe and not rdf.empty:
        STEPS.append(("review", "Review activity"))
        if not udf.empty:
            STEPS.append(("recapture", "Unused recapture"))
        if overs:
            STEPS.append(("overage", "Overage routing & bills"))
    total = len(STEPS)
    SS.recap_step = max(0, min(SS.recap_step, total - 1))
    step_key, step_label = STEPS[SS.recap_step]
    ui.scroll_top_on_step_change("flex_stage3_recap", SS.recap_step)

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
            with st.expander(":material/help: **How to pull the OPD export**", expanded=False):
                st.markdown(
                    """
1. Go to **[telehealth.oncurapartners.com](https://telehealth.oncurapartners.com)**.
2. Open **Consults → Completed**.
3. Filter **Department**: select **Cardiology**, **Ultrasound**, **General Radiology**, **Point of Care (GlobalFAST)**, and **Internal Medicine**.
4. Adjust the date range to match the chosen month.
5. Click **Search**, then **Export to Excel**.
6. Upload the exported file below.
                    """
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
            display_cols = [
                "clinic_name", "qb_name", "finance_company", "contract_number",
                "calendar_spread", "quarterly_threshold", "quarter_activity",
                "unused", "overage", "activity_match",
            ]
            with st.expander(f"Per-clinic breakdown ({len(rdf)} clinics)"):
                st.dataframe(
                    rdf[[c for c in display_cols if c in rdf.columns]],
                    use_container_width=True, height=320,
                )
            fuzzy = rdf[rdf["activity_match"] == "fuzzy"]
            if not fuzzy.empty:
                with st.expander(f"Fuzzy name matches ({len(fuzzy)}) — eyeball these"):
                    st.caption(
                        "These clinic names didn't match exactly between OPD and FLEX master, but were "
                        "similar enough (rapidfuzz ≥ 88) to pair up. Verify each pair looks right; if "
                        "any are wrong, fix the name in flex_master / name_map and re-run."
                    )
                    fz = fuzzy[["clinic_name", "qb_name", "matched_opd_name", "fuzzy_score"]].copy()
                    fz.columns = ["FLEX master clinic_name", "QB name", "OPD clinic name (matched)", "Similarity"]
                    fz["Similarity"] = fz["Similarity"].map(lambda v: f"{int(v)}%" if v is not None else "")
                    st.dataframe(fz, use_container_width=True, hide_index=True)

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
                "**Upload to SaasAnt:** "
                "[transactions.saasant.com](https://transactions.saasant.com) → "
                "**Bulk Upload → Invoice** → select the file."
            )
            with st.expander("Preview the invoice rows"):
                st.dataframe(udf, use_container_width=True, height=220)

            # Mark batch as imported -> ledger + audit
            if not udf.empty:
                recap_ledger_rows = [
                    {
                        "kind": "unused_invoice",
                        "contract": row["Customer"],
                        "qb_customer": row["Customer"],
                        "payment_date": row["Invoice Date"],
                        "amount": float(row["Product/Service Amount"]),
                    }
                    for _, row in udf.iterrows()
                ]
                recap_fps = [
                    ledger.fingerprint("INTERNAL", "unused_invoice", r["contract"],
                                       r["payment_date"], r["amount"])
                    for r in recap_ledger_rows
                ]
                already = ledger.check_payments_seen(recap_fps)
                if already:
                    st.warning(
                        f"{len(already)} recapture invoice(s) already recorded for this quarter. "
                        "Re-uploading those rows to QBO would duplicate them."
                    )
                st.divider()
                recap_initials = ui.initials_input("stage3_recap_audit_initials")
                if st.button(
                    f"Mark {len(udf)} recapture invoice(s) as imported",
                    key="w_recap_mark_unused", type="primary",
                    disabled=not recap_initials,
                ):
                    ok_l, added, _ = ledger.record_batch(
                        file_content=None,
                        filename=f"UnusedFlex_{dt.date(2000, rec_month, 1):%b}_{rec_year}.xlsx",
                        company="INTERNAL",
                        payments=recap_ledger_rows,
                        note=f"Stage 3 recapture / {rec_year}-{rec_month:02d}",
                    )
                    audit.record_cycle(
                        cycle_type="stage3_recapture",
                        approver=recap_initials or auth.current_role(),
                        year=rec_year, month=rec_month,
                        params={
                            "sales_class": sales_class,
                            "start_ref": recap_start,
                            "next_ref": next_ref,
                            "quarter_window": f"{win_start:%Y-%m-%d}..{win_end:%Y-%m-%d}",
                        },
                        source_file={
                            "name": SS.recap_uploaded_name,
                            "sha256": ledger.file_hash(SS.recap_uploaded_bytes),
                            "size_bytes": len(SS.recap_uploaded_bytes),
                        } if SS.recap_uploaded_bytes else None,
                        outputs=[{
                            "name": "unused_recapture_invoices",
                            "sha256": audit.output_hash_df(udf),
                            "row_count": len(udf),
                            "total": round(float(udf["Product/Service Amount"].sum()), 2),
                        }],
                        note=f"{added} new recapture invoice(s); {len(already)} already in ledger",
                    )
                    (st.success if ok_l else st.warning)(
                        f"Recorded {added} recapture invoice(s) in ledger + audit manifest."
                    )

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
                            "1. **Upload to SaasAnt** → "
                            "[transactions.saasant.com](https://transactions.saasant.com) → "
                            "**Bulk Upload → Invoice** → select the file.\n"
                            "2. **Send each clinic** an Authorize.net payment link (or QBO invoice PDF).\n"
                            "3. **VOID each QBO invoice immediately after sending** — revenue was already "
                            "captured by OPD invoices, so leaving them open overstates AR (SOP-6).\n"
                            "4. Apply payment to zero out the clinic's account when received.\n"
                            "5. **No refunds** (SOP-12) — overpayments stay on account for future overages."
                        )
                    # Dedup against ledger — flag rows already emitted in prior runs
                    if not didf.empty:
                        direct_payments_for_ledger = [
                            {
                                "kind": "direct_overage",
                                "contract": row["Customer"],
                                "qb_customer": row["Customer"],
                                "payment_date": row.get("Invoice Date") or f"{rec_year:04d}-{rec_month:02d}-01",
                                "amount": float(row.get("Product/Service Amount") or row.get("Amount") or 0),
                            }
                            for _, row in didf.iterrows()
                        ]
                        direct_fps = [
                            ledger.fingerprint("INTERNAL", "direct_overage", r["contract"],
                                               r["payment_date"], r["amount"])
                            for r in direct_payments_for_ledger
                        ]
                        already_direct = ledger.check_payments_seen(direct_fps)
                        if already_direct:
                            st.warning(
                                f"**{len(already_direct)} direct-bill invoice(s) already recorded for this period.** "
                                f"Re-uploading them to QBO will double-bill — review the download before importing."
                            )
                    if not didf.empty:
                        st.divider()
                        st.warning(
                            ":material/edit_off:  **SOP-6 reminder:** after uploading to QBO via "
                            "SaasAnt, **void each invoice in QBO** before initialing below.",
                            icon=":material/edit_off:",
                        )
                        direct_initials = ui.initials_input("stage3_direct_audit_initials")
                    else:
                        direct_initials = ""
                    if not didf.empty and st.button(
                        f"Mark {len(didf)} direct-bill invoice(s) as imported",
                        key="w_recap_mark_direct", type="primary",
                        disabled=not direct_initials,
                    ):
                        ok_l, added_l, _ = ledger.record_batch(
                            file_content=None,
                            filename=f"OverageDirect_{dt.date(2000, rec_month, 1):%b}_{rec_year}.xlsx",
                            company="INTERNAL",
                            payments=direct_payments_for_ledger,
                            note=f"Stage 3 direct-bill / {dt.date(2000, rec_month, 1):%B} {rec_year}",
                        )
                        audit.record_cycle(
                            cycle_type="stage3_overage",
                            approver=direct_initials or auth.current_role(),
                            year=rec_year, month=rec_month,
                            params={
                                "route": "direct_bill",
                                "start_ref": int(SS.recap_direct_start),
                                "next_ref": direct_next,
                                "clinic_count": direct_count,
                            },
                            outputs=[{
                                "name": "overage_direct_invoices",
                                "sha256": audit.output_hash_df(didf),
                                "row_count": len(didf),
                                "total": round(float(direct_total), 2),
                            }],
                            note=f"Direct-bill overages for {dt.date(2000, rec_month, 1):%B %Y}",
                        )
                        st.success(
                            f"Recorded {len(didf)} direct-bill invoice(s) in audit manifest "
                            f"and {added_l} fingerprint(s) in the dedup ledger."
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
                    # Dedup against ledger — flag rows already submitted in prior runs
                    if not pdf.empty:
                        cust_col = "Customer" if "Customer" in pdf.columns else pdf.columns[0]
                        amt_col = "Amount" if "Amount" in pdf.columns else next(
                            (c for c in pdf.columns if "amount" in str(c).lower() or "net" in str(c).lower()), None
                        )
                        partner_payments_for_ledger = [
                            {
                                "kind": "partner_overage",
                                "contract": str(row[cust_col]),
                                "qb_customer": str(row[cust_col]),
                                "payment_date": f"{rec_year:04d}-{rec_month:02d}-01",
                                "amount": float(row[amt_col]) if amt_col else 0.0,
                            }
                            for _, row in pdf.iterrows()
                        ]
                        partner_fps = [
                            ledger.fingerprint("INTERNAL", "partner_overage", r["contract"],
                                               r["payment_date"], r["amount"])
                            for r in partner_payments_for_ledger
                        ]
                        already_partner = ledger.check_payments_seen(partner_fps)
                        if already_partner:
                            st.warning(
                                f"**{len(already_partner)} partner submission(s) already recorded for this period.** "
                                f"Re-submitting will create a duplicate at OnePlace — review before sending."
                            )
                    if not pdf.empty:
                        st.divider()
                        st.warning(
                            ":material/schedule:  **Reminder:** email the partner-submission file "
                            "to OnePlace **before the cutoff date**, then initial below.",
                            icon=":material/schedule:",
                        )
                        partner_initials = ui.initials_input("stage3_partner_audit_initials")
                    else:
                        partner_initials = ""
                    if not pdf.empty and st.button(
                        f"Mark {len(pdf)} partner-submission row(s) as submitted",
                        key="w_recap_mark_partner", type="primary",
                        disabled=not partner_initials,
                    ):
                        ok_l, added_l, _ = ledger.record_batch(
                            file_content=None,
                            filename=f"OnePlaceOverage_{dt.date(2000, rec_month, 1):%b}_{rec_year}.xlsx",
                            company="INTERNAL",
                            payments=partner_payments_for_ledger,
                            note=f"Stage 3 partner submission / {dt.date(2000, rec_month, 1):%B} {rec_year}",
                        )
                        audit.record_cycle(
                            cycle_type="stage3_overage",
                            approver=partner_initials or auth.current_role(),
                            year=rec_year, month=rec_month,
                            params={
                                "route": "partner_submission",
                                "partner": "OnePlace",
                                "cutoff": cutoff.isoformat(),
                                "clinic_count": partner_count,
                            },
                            outputs=[{
                                "name": "oneplace_overage_submission",
                                "sha256": audit.output_hash_df(pdf),
                                "row_count": len(pdf),
                                "total": round(float(partner_total), 2),
                            }],
                            note=f"OnePlace overage submission for {dt.date(2000, rec_month, 1):%B %Y}",
                        )
                        st.success(
                            f"Recorded OnePlace submission ({len(pdf)} clinics) in audit manifest "
                            f"and {added_l} fingerprint(s) in the dedup ledger."
                        )

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

    # Bottom-of-page "Set up new cycle" — clears the file, credit offsets, and
    # returns to the first wizard step. Use this between back-to-back monthly runs.
    st.divider()
    if st.button("Set up new cycle", key="w_recap_reset_bottom",
                 use_container_width=True,
                 help="Clear the uploaded file + credit offsets and start fresh — use this between monthly Stage 3 runs."):
        for k in ("recap_uploaded_bytes", "recap_uploaded_name", "recap_credit_offsets",
                  "recap_pipe_error", "recap_pipe_traceback",
                  "w_recap_file", "w_recap_offsets_editor"):
            SS.pop(k, None)
        SS.recap_step = 0
        st.rerun()
