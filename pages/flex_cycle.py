"""Payment Cycle — one page that walks through the monthly process for BOTH
FLEX and scan-package (pass-through) payments together. Surfaced in the
sidebar under the "Pass-Through Payments" section.

Stage 1: Finance Company Payment Import (remittance -> SaasAnt flex/scan files)
Stage 2: Monthly Credit Memos (credit-memo SaasAnt file)
Stage 3: Unused / Overage (quarter-end recapture + overage; runs every month
         for whichever clinic group's staggered quarter is closing)
"""
import datetime as dt
from contextlib import contextmanager

import pandas as pd
import streamlit as st

from core import (
    accounting_handoff, audit, auth, errors, flex_credits, flex_finance,
    flex_overage, flex_unused, ledger, loaders, opd_adapter, opd_api, saasant,
    store, ui,
)


@contextmanager
def safe_stage(label: str):
    """Catch + render an error inside a tab so one broken stage doesn't kill the others.

    Chains with `with tab_X, safe_stage(...):` so the existing indentation stays the same.
    """
    try:
        yield
    except Exception as _e:
        _err = errors.capture(_e)
        st.error(f"**{label} failed:** `{_err['summary']}`")
        st.caption("The other stages are still usable.")
        errors.render_details(_err)

ui.header("Payment Cycle",
          "Handles FLEX and scan-package (pass-through) payments together. "
          "Generates SaasAnt files for QBO import — humans approve every QBO posting.",
          kicker="Pass-Through Payments · Cycle")

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
        mc1, mc2 = st.columns([1, 1])
        company_options = ["NewLane", "OnePlace", "GreatAmerica", "FPLeasing"]
        company = mc1.selectbox(
            "Finance company", company_options,
            index=company_options.index(SS["remit_company"]) if SS["remit_company"] in company_options else 0,
            key="remit_company_w",
        )
        pay_date = mc2.date_input(
            "Payment date (received)",
            value=SS["remit_pay_date"],
            key="remit_pay_date_w",
        )

        # "Applies to month" — NewLane ONLY. NewLane pass-throughs arrive on
        # irregular days covering the PRIOR month, so we attribute them by an
        # explicit coverage month (Stage 3 trues up coverage + 1). Defaults to
        # last calendar month; the operator changes it only for an off-schedule
        # remittance. Every other company is attributed by the received date, so
        # no field appears for them.
        applies_to = ""
        if ledger.uses_coverage(company):
            _cur = dt.date.today()
            _cur_idx = _cur.year * 12 + (_cur.month - 1)
            _last_month = f"{(_cur_idx-1)//12:04d}-{(_cur_idx-1)%12+1:02d}"  # current month - 1
            _opts = {f"{(_cur_idx+d)//12:04d}-{(_cur_idx+d)%12+1:02d}" for d in range(-6, 2)}
            _opts.add(_last_month)
            if SS.get("remit_applies_to_w"):
                _opts.add(SS["remit_applies_to_w"])
            _month_opts = sorted(_opts)
            _def_idx = _month_opts.index(_last_month) if _last_month in _month_opts else 0
            ac, _ = st.columns([1, 1])
            applies_to = ac.selectbox(
                "Applies to month",
                options=_month_opts,
                index=_def_idx,
                format_func=lambda s: dt.date(int(s[:4]), int(s[5:7]), 1).strftime("%B %Y"),
                key="remit_applies_to_w",
                help="Which month this NewLane remittance is for. Defaults to last "
                     "month; Stage 3 trues it up the following month. Change it only "
                     "if the remittance arrived off its usual schedule.",
            )
        SS["remit_applies_to"] = applies_to

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

        if company == "FPLeasing":
            st.caption(
                "FP Leasing is scan-only — every row becomes a scan invoice + receive payment. "
                "Amount used is **DUE TO ONCURA** (net wire, after the $5 service fee), "
                "matching what the bank feed will show."
            )
        elif company != "GreatAmerica":
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
        # Coverage month (NewLane only) set on the setup step; every other
        # company is attributed by the received date, so it carries none.
        applies_to = SS.get("remit_applies_to", "") if ledger.uses_coverage(company) else ""
        if company == "GreatAmerica":
            start_inv = 50000
            split = "all_flex"
        elif company == "FPLeasing":
            start_inv = int(SS.get("remit_start_inv", 50000))
            split = "all_scan"
        else:
            start_inv = int(SS.get("remit_start_inv", 50000))
            split = "by_cents"

        # Diagnostic: prominently display the values being used so a mismatch with
        # what the operator picked is immediately visible. (Bug guard — the user
        # reported files coming out labeled NewLane regardless of selection.)
        _covname = ""
        if applies_to:
            try:
                _covname = dt.date(int(applies_to[:4]), int(applies_to[5:7]), 1).strftime("%b %Y")
            except (ValueError, IndexError):
                _covname = ""
        st.info(
            f":material/info: **Processing as:** company=**{company}**, "
            f"payment date=**{pay_date}**" + (f" (applies to **{_covname}**)" if _covname else "") + ", "
            f"starting invoice #=**{start_inv if company != 'GreatAmerica' else '—'}**. "
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
            back_col, _ = st.columns([1, 3])
            if back_col.button("◀ Back to setup", key="remit_upload_back", use_container_width=True):
                SS["remit_step"] = 0
                st.rerun()
            # The 'Set up new import' reset is intentionally at the bottom of the
            # page only — keeps the top of the upload step focused on the action.

        up = st.file_uploader("Remittance file (CSV/XLSX)", type=["csv", "xlsx", "xls"], key="remit_file")
        if up is None:
            st.info("Upload the finance company's remittance.")
        else:
            file_bytes = up.getvalue()
            prior_file = ledger.check_file_seen(file_bytes)
            # Did we record THIS exact file earlier in this session? Tracked in
            # session state (keyed by file hash) so the "already recorded" state
            # is deterministic and does NOT depend on the just-written ledger
            # having propagated to the next read. This is what stops the "Mark N"
            # button from lingering after a successful record and inviting a
            # confusing second click that records 0. {file_hash: added_count}
            this_file_hash = ledger.file_hash(file_bytes)
            SS.setdefault("stage1_recorded_files", {})
            recorded_this_session = this_file_hash in SS["stage1_recorded_files"]
            raw = opd_adapter.read_remittance(up)

            # Month-based dedup — the real signal for protecting against
            # double-posting. OnePlace files look structurally similar each
            # month, so file-hash alone produces false positives. Find the
            # Payment Date column, extract unique (year, month) tuples, and
            # check whether ANY of those months already have payments for
            # this company in the ledger. If yes → warn + require override.
            #
            # OnePlace remittances carry a per-row payment date (detected via
            # these keywords). NewLane "Advice" and GreatAmerica files have
            # one payment date for the whole batch — it's not in the file
            # data, it's the `pay_date` the operator entered on the setup
            # step. When no payment-date column is detected we fall back to
            # that single operator-supplied date, which is the right semantic
            # anyway (the file IS for one date).
            _payment_date_candidates = [
                c for c in raw.columns
                if any(k in str(c).lower().replace("\n", " ").replace("_", " ")
                       for k in ("payment date", "paymentdate", "pay date",
                                 "date paid", "transaction date"))
            ]
            if _payment_date_candidates:
                _pd_col = _payment_date_candidates[0]
                _dates = pd.to_datetime(raw[_pd_col], errors="coerce").dropna()
                _year_months = {(d.year, d.month) for d in _dates}
            else:
                _year_months = {(pay_date.year, pay_date.month)}
            already_processed_months: dict = (
                ledger.check_payment_months_seen(company, _year_months)
                if _year_months else {}
            )

            # Whether a month overlap is a genuine re-upload signal depends on the
            # partner. Single-remittance-per-month partners (OnePlace, NewLane,
            # FP Leasing) overlapping a month → likely re-upload → hard gate.
            # GreatAmerica sends MULTIPLE remittances per month, so a month overlap
            # is expected — inform, don't gate. The exact-file-hash match is a true
            # re-upload signal for ANY partner and always gates.
            multi_remittance = company in flex_finance.MULTI_REMITTANCE_COMPANIES
            month_overlap = bool(already_processed_months)
            # A file we already recorded this session is expected to be "seen" —
            # don't throw the red re-upload gate for it; the recorded-state
            # confirmation below handles it.
            hard_dupe_risk = (not recorded_this_session) and (
                bool(prior_file) or (month_overlap and not multi_remittance)
            )

            if month_overlap:
                _human_months = ", ".join(
                    f"**{dt.date(y, m, 1):%B %Y}** ({n} payment(s))"
                    for (y, m), n in sorted(already_processed_months.items())
                )

            if hard_dupe_risk:
                _extra_hash_note = ""
                if prior_file:
                    _extra_hash_note = (
                        f"  \nThis exact file was previously processed on "
                        f"{prior_file.get('uploaded_at', '?')[:10]} "
                        f"(`{prior_file.get('filename', '?')}`)."
                    )
                _month_clause = (
                    f"contains payments for a month that's already in the ledger: {_human_months}. "
                    if month_overlap else "was uploaded before. "
                )
                st.error(
                    f"**This file {_month_clause}**Re-uploading would risk double-posting "
                    f"to QBO. Row-level dedup will still skip already-imported payments "
                    f"below — but verify before downloading.{_extra_hash_note}"
                )
                if not st.checkbox(
                    "I've verified this is intentional (e.g., recovering from a partial earlier import). "
                    "Proceed with row-level dedup.",
                    key="remit_file_override",
                ):
                    st.stop()
            elif month_overlap and multi_remittance:
                # Expected for GreatAmerica — multiple remittances per month is the
                # normal flow. A quiet caption, not a banner; no override gate.
                _overlap_months = ", ".join(
                    f"{dt.date(y, m, 1):%B %Y}" for (y, m) in sorted(already_processed_months)
                )
                st.caption(
                    f"{company} sends multiple remittances per month, so {_overlap_months} "
                    f"already in the ledger is expected; already-imported rows are skipped below."
                )
            elif prior_file:
                # Same bytes seen before but no month overlap in the ledger — this
                # means the prior import was rolled back / cleared. Note quietly;
                # row-level dedup will handle whatever the operator intends.
                st.info(
                    f":material/info: This file's bytes were uploaded before "
                    f"({prior_file.get('uploaded_at', '?')[:10]}) but no payments "
                    f"from its months are currently in the ledger — proceeding.",
                )

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
            # GA needs a SECOND id column — Payment Invoice Number is used for
            # the SaasAnt Ref No, ContractID is used for QB-customer lookup.
            if company == "GreatAmerica" and g.get("contract"):
                if SS.get("remit_contract_col") not in cols:
                    SS["remit_contract_col"] = g["contract"]
            # Tuck the override controls into a collapsed expander so non-tech-savvy users
            # aren't overwhelmed by extra dropdowns in the typical case where
            # guess_columns picked the right ones.
            mapping_summary = (
                f"Customer = `{SS['remit_cust_col']}`, "
                f"Amount = `{SS['remit_amt_col']}`, "
                f"{id_label} = `{SS['remit_id_col']}`"
            )
            if company == "GreatAmerica" and g.get("contract"):
                mapping_summary += f", ContractID = `{SS.get('remit_contract_col')}`"
            with st.expander(
                f"Column mapping (auto-detected: {mapping_summary}) — open if a column looks wrong",
                expanded=False,
            ):
                if company == "GreatAmerica" and g.get("contract"):
                    mc1, mc2, mc3, mc4 = st.columns(4)
                    mc1.selectbox("Customer name column", cols, key="remit_cust_col")
                    mc2.selectbox("Amount column", cols, key="remit_amt_col")
                    mc3.selectbox(f"{id_label} column", cols, key="remit_id_col",
                                  help="Drives the SaasAnt Ref No (e.g. GA-41983392).")
                    mc4.selectbox("ContractID column", cols, key="remit_contract_col",
                                  help="The dashed-format Contract ID used to look up QB customer "
                                       "names against flex_master and contract_qb_map.json.")
                else:
                    mc1, mc2, mc3 = st.columns(3)
                    mc1.selectbox("Customer name column", cols, key="remit_cust_col")
                    mc2.selectbox("Amount column", cols, key="remit_amt_col")
                    mc3.selectbox(f"{id_label} column", cols, key="remit_id_col")
            customer_col = SS["remit_cust_col"]
            amount_col = SS["remit_amt_col"]
            id_col = SS["remit_id_col"]

            # GreatAmerica remittances key off ContractID — the customer-name
            # column carries unrelated legal-entity names that don't match QB.
            # Merge two contract → qb_name sources:
            #   1. The contract_<company> field on each flex_master clinic
            #   2. Operator-added entries in data/contract_qb_map.json for
            #      clinics not (yet) in flex_master
            cm_base = loaders.contract_qb_map()
            extras = (cm_base.get("map") or {}).get(company, {})
            contract_qb_map = flex_finance.build_contract_qb_map(
                flex_clinics, company, extras=extras,
            )
            # GA: id_col is Payment Invoice Number (used to build the SaasAnt
            # Ref No 'GA-{n}'). ContractID (dashed) is the separate lookup
            # key for matching against flex_master.contract_greatamerica.
            # User can override via the column-mapping expander above; falls
            # back to None when the upload has no ContractID column at all.
            ga_contract_col = (
                SS.get("remit_contract_col")
                if company == "GreatAmerica" and g.get("contract")
                else None
            )
            res = flex_finance.process_remittance(
                raw, company,
                customer_col=customer_col, amount_col=amount_col, id_col=id_col,
                contract_id_col=ga_contract_col,
                payment_date=pay_date, invoice_date=inv_date, start_invoice_no=start_inv,
                name_map=nm, contract_qb_map=contract_qb_map, split=split,
            )

            # ── Row-level dedup against the processed-payments ledger ──────────────
            # Stable per-payment dedup key. For most companies that's id_col
            # (contract / payment-invoice number). FP Leasing's displayed
            # invoice-# column now carries the GENERATED SaasAnt invoice number
            # (which changes per run with the start #), so key off FP's own
            # invoice number instead — it's preserved in the Ref No as 'FPL-<n>'
            # — so re-uploads still dedup regardless of the chosen start #.
            ref_col = "Ref No (Receive Payment No)"

            def _row_payment_dicts(df, kind):
                if df is None or df.empty:
                    return []
                use_ref = company == "FPLeasing" and ref_col in df.columns
                if not use_ref and id_col not in df.columns:
                    return []
                out = []
                for i in range(len(df)):
                    amt_val = df["Amount"].iloc[i] if "Amount" in df.columns else df[amount_col].iloc[i]
                    if use_ref:
                        contract = flex_finance.strip_invoice_prefix(df[ref_col].iloc[i])
                    else:
                        contract = df[id_col].iloc[i]
                        # GA can ship a real payment with a blank Payment Invoice
                        # Number; fall back to the dashed ContractID so the ledger
                        # fingerprint key is stable and non-blank.
                        if (contract is None or str(contract).strip().lower() in ("", "nan")) \
                                and ga_contract_col and ga_contract_col in df.columns:
                            contract = df[ga_contract_col].iloc[i]
                    out.append({
                        "kind": kind,
                        "contract": contract,
                        "qb_customer": df["Customer"].iloc[i] if "Customer" in df.columns else "",
                        "payment_date": pay_date,
                        "applies_to": applies_to,
                        "amount": amt_val,
                    })
                return out

            flex_rows = _row_payment_dicts(res["flex_payments"], "flex")
            scan_rows = _row_payment_dicts(res["scan_payments"], "scan")
            all_rows = flex_rows + scan_rows
            all_fps = [ledger.fingerprint(company, r["kind"], r["contract"], r["payment_date"], r["amount"])
                       for r in all_rows]
            seen_fps = ledger.check_payments_seen(all_fps)
            # A batch recorded earlier in THIS session is definitively in the
            # ledger even if the just-written copy hasn't propagated to this
            # read. Treat all its rows as seen so the flow reflects the recorded
            # state deterministically (no lingering "Mark N" button / 0-record
            # second click).
            if recorded_this_session:
                seen_fps = set(all_fps)

            # ── Reissue check: rows that weren't exact-duplicates but match an existing
            #    ledger row on (company, kind, contract, amount) with a DIFFERENT payment_date.
            #    These look like reissues — same money, different date — and shouldn't be
            #    silently treated as net-new. Surface for confirm-and-proceed.
            novel_rows = [r for r, fp in zip(all_rows, all_fps) if fp not in seen_fps]
            possible_reissues = ledger.check_possible_reissues(company, novel_rows) if novel_rows else []
            reissue_fps: set[str] = set()
            if possible_reissues:
                st.warning(
                    f":material/warning: **{len(possible_reissues)} payment(s) look like possible reissues** — "
                    "same company / contract / amount as a prior ledger row but a different payment date. "
                    "By default they're EXCLUDED from this import (assumed to be the same money re-dated); "
                    "the genuinely-new rows still import. Tick the box only if these are intentional reissues.",
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
                    "Include these reissues — they are intentional, not the same money re-dated.",
                    value=SS.get("remit_reissue_ack", False),
                    key="remit_reissue_ack_widget",
                )
                if not SS["remit_reissue_ack"]:
                    # Exclude just the reissue rows; the clean new rows still import.
                    reissue_fps = {
                        ledger.fingerprint(company, r["incoming"].get("kind", "flex"),
                                           r["incoming"].get("contract", ""),
                                           r["incoming"]["payment_date"],
                                           r["incoming"]["amount"])
                        for r in possible_reissues
                    }
                    st.info(
                        f"{len(reissue_fps)} reissue(s) excluded from this import — the rest still "
                        "import. Tick the box above to include them."
                    )
            else:
                SS["remit_reissue_ack"] = True

            # Rows to drop from the downloads + ledger: exact dups already in the
            # ledger, plus any suspected reissues the operator hasn't confirmed.
            skip_fps = set(seen_fps) | reissue_fps

            if skip_fps:
                if seen_fps:
                    sk_flex = sum(1 for fp in all_fps[:len(flex_rows)] if fp in seen_fps)
                    sk_scan = sum(1 for fp in all_fps[len(flex_rows):] if fp in seen_fps)
                    st.warning(
                        f"**Ledger already contains {len(seen_fps)} of these payments** "
                        f"(flex: {sk_flex}, scan: {sk_scan}). They've been removed from the "
                        f"downloads below so you don't double-post."
                    )
                # Filter the output dataframes in-place
                keep_flex = [i for i, fp in enumerate(all_fps[:len(flex_rows)]) if fp not in skip_fps]
                keep_scan = [i for i, fp in enumerate(all_fps[len(flex_rows):]) if fp not in skip_fps]
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

            # If the ledger already contained every payment in this file, post-filter
            # there are zero rows left to import. The yellow banner above already
            # explains it — everything downstream (metric cards / Downloads /
            # SaasAnt instructions / Mark button) would just show zeros. Show a
            # clean 'nothing to import' message and skip to the bottom reset.
            fully_deduped = bool(seen_fps) and s["total"] == 0 and (s["flex_count"] + s["scan_count"]) == 0
            if fully_deduped:
                if recorded_this_session:
                    _rec_n = SS["stage1_recorded_files"].get(this_file_hash, 0)
                    _rec_lead = (
                        f"**Recorded.** {_rec_n} payment(s) from this file were written to the "
                        f"ledger + audit manifest this session"
                        if _rec_n else
                        "**Already in the ledger.** These payments were previously recorded — "
                        "nothing new was written"
                    )
                    st.success(
                        f":material/check_circle: {_rec_lead}. Re-uploading the same file won't "
                        "double-post. Use **◀ Back to Setup** below to process the next file."
                    )
                else:
                    st.success(
                        ":material/check_circle: **All payments in this file are already in the "
                        "ledger.** Nothing new to import — the prior batch covers this remittance. "
                        "Use **◀ Back to Setup** at the bottom of the page to upload a different "
                        "file."
                    )
                st.divider()
                reset_col, _ = st.columns([1, 4])
                if reset_col.button(
                    "◀ Back to Setup",
                    key="remit_upload_reset_fullydeduped",
                    use_container_width=True,
                    help="Clear the uploaded file and start fresh — use this between back-to-back remittances.",
                ):
                    for k in ("remit_file", "remit_file_override",
                              "remit_cust_col", "remit_amt_col", "remit_id_col",
                              "remit_reissue_ack"):
                        SS.pop(k, None)
                    SS["remit_step"] = 0
                    st.rerun()
                st.stop()

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
                    f"File: {raw_nonzero} non-zero rows totaling **\\${raw_total:,.2f}**  ·  "
                    f"App importing: {s['flex_count'] + s['scan_count']} rows totaling **\\${s['total']:,.2f}**  ·  "
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

            # GA resolves rows by ContractID — surface the unmapped CONTRACTS
            # (with the remittance legal name as a hint) so the operator can
            # map each contract → QB Display Name. Saved to
            # data/contract_qb_map.json under {company: {contract: qb_name}}.
            unmapped_contracts = res.get("unmapped_contracts") or []
            if unmapped_contracts and company in flex_finance.CONTRACT_PRIMARY_COMPANIES:
                st.divider()
                st.subheader(f"Resolve {len(unmapped_contracts)} unmatched contract(s)")
                st.caption(
                    "These ContractIDs aren't in the FLEX master or the saved contract map. "
                    "Paste the matching QuickBooks Display Name on the right — the legal name "
                    "from the remittance is shown as context. Saved mappings persist so future "
                    "cycles auto-match by contract."
                )
                qb_inputs = {}
                hc1, hc2 = st.columns(2)
                hc1.markdown("**Contract ID** (legal name from remittance as hint)")
                hc2.markdown("**QuickBooks display name**")
                for i, entry in enumerate(unmapped_contracts):
                    cid = entry["contract"]
                    hint = entry.get("remittance_name") or "—"
                    cc1, cc2 = st.columns(2)
                    with cc1:
                        st.code(cid, language=None)
                        st.caption(f":gray[from remittance: {hint}]")
                    with cc2:
                        qb_inputs[cid] = st.text_input(
                            "qb", key=f"qbfix_contract_{i}",
                            label_visibility="collapsed",
                            placeholder="paste QuickBooks display name",
                        )
                if st.button("Save contract mappings", type="primary", key="remit_save_contract_map"):
                    new_pairs = {c.strip(): str(qb).strip()
                                 for c, qb in qb_inputs.items() if str(qb).strip()}
                    if new_pairs:
                        cm = loaders.contract_qb_map()
                        cm = {**cm, "map": {**(cm.get("map") or {})}}
                        company_map = dict((cm["map"].get(company) or {}))
                        company_map.update(new_pairs)
                        cm["map"][company] = company_map
                        ok, _ = store.save_json(
                            "contract_qb_map.json", cm,
                            f"Add {len(new_pairs)} {company} contract→QB mapping(s)",
                        )
                        loaders.contract_qb_map.clear()
                        st.success(
                            f"Saved {len(new_pairs)} contract mapping(s) " +
                            ("— committed to the repo for everyone." if ok
                             else "— applied locally. Set GITHUB_TOKEN on Cloud to share.")
                        )
                        st.rerun()
                    else:
                        st.warning("Enter at least one QuickBooks display name first.")
                st.warning("Resolve the contracts above before uploading these imports.")
            elif unmapped:
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
            # preview expander on the RIGHT (optional inspection). 50/50 split
            # so the preview header isn't squeezed onto a second line.
            def _download_row(*, title: str, df, fname_stem: str, fname_date,
                              sheet_name: str, dl_key: str, height: int = 240):
                col_dl, col_prev = st.columns([1, 1], gap="medium")
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
            with st.expander(":gray[Uploading to SaasAnt — reference]", expanded=False):
                st.markdown(
                    """
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
                r for r, fp in zip(all_rows, all_fps) if fp not in skip_fps
            ]
            ack_disabled = len(rows_to_record) == 0
            if not ack_disabled:
                ui.persistence_warning()
            stage1_initials = ui.initials_input(
                "stage1_audit_initials",
                disabled=ack_disabled,
            )
            if ack_disabled:
                st.info("Nothing new to record (all rows were already in the ledger).")
            if not ack_disabled and ui.record_button(
                f"Mark {len(rows_to_record)} payment(s) as imported",
                key="remit_mark_processed",
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
                        "applies_to": applies_to,
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
                    # Mark this file recorded for the session and rerun, so the
                    # page re-renders in the deterministic recorded state (no
                    # lingering "Mark N" button) instead of relying on the
                    # just-written ledger being visible to the next read.
                    SS["stage1_recorded_files"][this_file_hash] = added
                    st.rerun()
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
            # Build the bank-feed-label crosswalk from COMPANY_META so it can't
            # drift out of sync with the labels (as it did when GreatAmerica was
            # corrected to "Account Services").
            _label_pairs = " · ".join(
                f"**{m['bank_feed']}** = {c}"
                for c, m in flex_finance.COMPANY_META.items()
                if m.get("bank_feed")
            )
            # st.warning gives the yellow call-to-action box appropriate for a
            # "you still have work to do in QBO" reminder. The bank-feed-label
            # crosswalk at the bottom uses :blue[] so the label names stand
            # out from the body copy.
            st.warning(
                f"**Next step — Acct SOP-2: match the bank feed in QBO.**  \n"
                f"After the SaasAnt files above are imported to QBO, open **the bank feed** in QBO "
                f"and match the deposit against the receive payments you just created. The deposit "
                f"shows up under the finance company's bank-feed label — for this batch: **{_bank}**. "
                f"Confirm the deposit amount equals the **Total** shown on this page; if there's a "
                f"mismatch, stop and reconcile before posting the next remittance.  \n"
                f":blue[Bank-feed labels: {_label_pairs}]",
                icon=":material/account_balance:",
            )

            # Bottom-of-page "Set up new import" — same handler as the top-card button,
            # for operators who've scrolled all the way down and don't want to scroll back.
            # Placed bottom-left in a narrow column to match the Back/Next nav pattern
            # used on the wizard pages — keeps the reset action out of the visual
            # foreground but easy to find when needed.
            st.divider()

            # Leave-guard: don't let an unrecorded batch be abandoned silently.
            # Resetting with rows still un-recorded would skip the sign-off — no
            # audit entry and no dedup protection (a re-upload wouldn't be caught).
            # So the reset opens a blocking modal: record first, or discard.
            _s1_unrecorded = len(rows_to_record) > 0

            def _do_s1_reset():
                for k in ("remit_file", "remit_file_override",
                          "remit_cust_col", "remit_amt_col", "remit_id_col",
                          "remit_reissue_ack", "remit_leave_guard"):
                    SS.pop(k, None)
                SS["remit_step"] = 0

            @st.dialog("This batch isn't recorded yet", width="large", dismissible=False)
            def _s1_leave_modal():
                st.info(
                    "**You haven't recorded this batch to the ledger.** Leave now and it "
                    "won't be signed off or audited, and a future re-upload of the same "
                    "payments won't be caught as a duplicate. Scroll up and **Mark … as "
                    "imported** to record it, or discard this batch and start fresh.",
                    icon=":material/warning:",
                )
                _gb, _ds = st.columns(2)
                if _gb.button("← Go back & record", key="s1_guard_back",
                              type="primary", use_container_width=True):
                    SS.pop("remit_leave_guard", None)
                    st.rerun()
                if _ds.button("Discard this batch & start fresh", key="s1_guard_discard",
                              use_container_width=True):
                    _do_s1_reset()
                    st.rerun()

            if SS.get("remit_leave_guard"):
                _s1_leave_modal()

            reset_col, _ = st.columns([1, 4])
            if reset_col.button(
                "◀ Back to Setup",
                key="remit_upload_reset_bottom",
                use_container_width=True,
                help="Clear the uploaded file and return to the setup step — use this between back-to-back remittances.",
            ):
                if _s1_unrecorded:
                    SS["remit_leave_guard"] = True
                else:
                    _do_s1_reset()
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
            value=int(SS["cred_start_ref"]), step=1, key="cred_start_ref_w",
            help="Numeric seed only — the SaasAnt export prepends 'CR' "
                 "(e.g. 50000 → CR50000).",
        ))
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
            back_col, _ = st.columns([1, 3])
            if back_col.button("◀ Back to setup", key="cred_review_back", use_container_width=True):
                SS["cred_step"] = 0
                st.rerun()
            # The 'Set up new month' reset lives at the bottom of the page only —
            # keeps the top of the review step focused on the action.

        # ── Pull ledger rows for the target month ──────────────────────────────────
        payments = ledger.flex_payments_for_month(year, month)
        df, next_ref, skipped, source_payments = flex_credits.build_import_from_payments(
            flex_clinics, payments, year, month, start_ref,
        )

        # Drop credit memos already recorded for this month so the download + mark
        # carry ONLY new ones (no manual row-picking, no double-post). Dedup key =
        # the SOURCE payment's immutable fingerprint — the same key the mark step
        # uses below.
        if not df.empty:
            _emit_fps = [
                ledger.fingerprint(
                    "INTERNAL", "credit_memo",
                    sp.get("fingerprint") or row["Customer"],
                    row["Credit Memo Date"], float(row["Product/Service Amount"]),
                )
                for sp, (_, row) in zip(source_payments, df.iterrows())
            ]
            _seen_cred = ledger.check_payments_seen(_emit_fps)
            if _seen_cred:
                _keep = [i for i, fp in enumerate(_emit_fps) if fp not in _seen_cred]
                df = df.iloc[_keep].reset_index(drop=True)
                source_payments = [source_payments[i] for i in _keep]
                st.info(
                    f"{len(_seen_cred)} credit memo(s) already recorded for {mname} {year} "
                    "were removed — the download below holds only new ones.",
                    icon=":material/info:",
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
            st.error(
                f":material/inventory_2: **No FLEX payments in the ledger for {mname} {year}.**  \n"
                f"Nothing to generate credit memos against. Likely causes:\n"
                f"- Stage 1 hasn't been run for this month yet — record those finance "
                f"payments first, then come back here.\n"
                f"- You selected the wrong month — click **◀ Back to setup** above and "
                f"pick a different month.\n"
                f"- You just recorded Stage 1 payments and the page hasn't refreshed — "
                f"click **↻ Refresh ledger** above.\n\n"
                f"Bootstrap-only fallback (first run after migration) is available in the "
                f"gray **Legacy mode** expander further down.",
                icon=":material/warning:",
            )
            # No payments → no credit memos → nothing else to render. Show the
            # bottom 'Set up new month' reset, then stop. Skips the empty df
            # preview, SaasAnt instructions, initials card, and Mark button — all
            # of which would otherwise render with zero rows.
            st.divider()
            reset_col, _ = st.columns([1, 4])
            if reset_col.button(
                "◀ Back to Setup",
                key="cred_review_reset_nopay",
                use_container_width=True,
                help="Reset year/month/start-ref and return to the setup step.",
            ):
                for k in ("cred_year", "cred_month", "cred_start_ref"):
                    SS.pop(k, None)
                SS["cred_step"] = 0
                st.rerun()
            st.stop()
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

            # Source-payment metrics + unpaid list tucked into a gray expander.
            # They're context for trusting the credit-memo batch below, not the
            # action itself — keep the page focused on what the operator is here
            # to do, not on stats they only sometimes need.
            src_label = (f":gray[Source payments — {len(payments)} payment(s) across "
                         f"{len(by_qb)} clinic(s) this month  ·  details]")
            with st.expander(src_label, expanded=False):
                pm1, pm2, pm3 = st.columns(3)
                pm1.metric("Payments received", len(payments))
                pm2.metric("Distinct clinics", len(by_qb))
                pm3.metric("Multi-payment clinics", len(multi))
                if multi:
                    st.caption(
                        "**Multi-payment clinics this month** (will receive one credit memo per payment): "
                        + ", ".join(f"{k} ({n}×)" for k, n in sorted(multi.items()))
                    )
                if unpaid:
                    st.divider()
                    st.markdown(f"**Active clinics with NO payment in {mname} — no credit memo generated** ({len(unpaid)})")
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
        st.caption(":gray[*See SaasAnt Import Instructions below.*]")

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
            ui.persistence_warning()
            stage2_initials = ui.initials_input("stage2_audit_initials")
            if ui.record_button(
                f"Mark {len(df)} credit memo(s) as generated",
                key="cred_mark_processed",
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

        st.divider()
        with st.expander(":gray[Uploading to SaasAnt — reference]", expanded=False):
            st.markdown(
                """
1. Go to **[transactions.saasant.com](https://transactions.saasant.com)**.
2. Click **Bulk Upload**.
3. Pick the import type: **Credit Memo**.
4. Select the **FlexCredits** xlsx you downloaded above.
5. Walk through the SaasAnt wizard. After it completes, come back here
   and click **Mark credit memos as generated** so the audit + dedup
   ledger records the batch.
                """
            )

        # Bottom-of-page "Set up new month" — mirror of the top-card button so an
        # operator who's scrolled down doesn't have to scroll back up. Bottom-left
        # narrow column to match the Back/Next nav pattern elsewhere.
        st.divider()

        # Leave-guard: don't let un-recorded credit memos be abandoned silently.
        # Resetting with credit memos still un-recorded would skip the sign-off
        # (no audit entry, and re-running the month could re-issue them). So the
        # reset opens a blocking modal: record first, or discard.
        _s2_unrecorded = not df.empty

        def _do_s2_reset():
            for k in ("cred_year", "cred_month", "cred_start_ref", "cred_leave_guard"):
                SS.pop(k, None)
            SS["cred_step"] = 0

        @st.dialog("These credit memos aren't recorded yet", width="large", dismissible=False)
        def _s2_leave_modal():
            st.info(
                "**You haven't recorded these credit memos to the ledger.** Leave now and "
                "they won't be signed off or audited, and re-running this month could "
                "re-issue them. Scroll up and **Mark … as generated** to record them, or "
                "discard and start a new month.",
                icon=":material/warning:",
            )
            _gb, _ds = st.columns(2)
            if _gb.button("← Go back & record", key="s2_guard_back",
                          type="primary", use_container_width=True):
                SS.pop("cred_leave_guard", None)
                st.rerun()
            if _ds.button("Discard & start a new month", key="s2_guard_discard",
                          use_container_width=True):
                _do_s2_reset()
                st.rerun()

        if SS.get("cred_leave_guard"):
            _s2_leave_modal()

        reset_col, _ = st.columns([1, 4])
        if reset_col.button(
            "◀ Back to Setup",
            key="cred_review_reset_bottom",
            use_container_width=True,
            help="Reset year/month/start-ref and return to the setup step — use this when moving on to the next month.",
        ):
            if _s2_unrecorded:
                SS["cred_leave_guard"] = True
            else:
                _do_s2_reset()
            st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — Unused Recapture + Overage  (step-by-step wizard)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_recap, safe_stage("Stage 3 — Unused / Overage"):
    import pandas as pd

    SS = st.session_state
    SS.setdefault("recap_step", 0)
    # Stage 3 runs the month AFTER the recap month (you're closing out the
    # month that just ended), so default to today minus one month — same
    # pattern as Stage 2.
    _recap_default_month = today.month - 1 or 12
    _recap_default_year = today.year if today.month > 1 else today.year - 1
    SS.setdefault("recap_year", _recap_default_year)
    SS.setdefault("recap_month", _recap_default_month)
    SS.setdefault("recap_sales_class", "03-Telemedicine")
    SS.setdefault("recap_start_ref", 50000)
    SS.setdefault("recap_direct_start", 50000)
    SS.setdefault("recap_credit_offsets", {})
    # Live OPD-OData source state. "opd_live" is the only source — the manual
    # upload fallback was removed 2026-06-09 (see the comment near the fetch
    # button). The key stays because the audit manifest records it.
    SS.setdefault("recap_data_source", "opd_live")
    SS.setdefault("recap_opd_activity", None)            # {clinic_lower: total_price}
    SS.setdefault("recap_opd_raw_rows", None)            # list[dict] for audit/preview
    SS.setdefault("recap_opd_fetched_at", None)          # UTC ISO timestamp
    SS.setdefault("recap_opd_invoice_count", 0)
    SS.setdefault("recap_opd_clinic_count", 0)
    SS.setdefault("recap_opd_components_mismatch", 0)
    SS.setdefault("recap_opd_orphans", {"count": 0, "total": 0.0, "fk_list": []})

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

    # Compute the pipeline. Single source of activity: the live OPD OData
    # pull. Errors are captured into SS so the fetch step can render them
    # inline (with a reference ID + admin-only traceback) instead of just
    # a banner.
    pipe = None
    SS["recap_pipe_error"] = None
    # Pull the quarter's positive FLEX payments from the ledger — drives the
    # "is this clinic actually on the program this quarter?" gate inside
    # compute_recapture, and feeds find_orphan_payments for the inverse
    # ("payments exist but no roster config") warning.
    #
    # FAIL CLOSED on a ledger read error. The old behavior fell back to None,
    # which DISABLES the payment gate and includes every active+quarter-end
    # clinic unverified — that is exactly the inflated-recapture incident
    # (a full-threshold unused invoice emitted for clinics with no payment
    # proof). Instead, pass an empty ledger: the gate stays active, so EVERY
    # clinic trips excluded_no_payments and drops into the held-out review
    # bucket, and we record the failure so the review step renders a blocking
    # warning. A glitchy ledger pauses the cycle; it never auto-posts.
    try:
        ledger_payments_for_quarter = ledger.flex_payments_in_window(win_start, win_end)
        SS["recap_ledger_read_failed"] = None
    except Exception as e:
        ledger_payments_for_quarter = []
        SS["recap_ledger_read_failed"] = errors.capture(e)
    orphan_payments = (
        flex_unused.find_orphan_payments(flex_clinics, ledger_payments_for_quarter)
        if ledger_payments_for_quarter else []
    )

    if SS.recap_data_source == "opd_live" and SS.recap_opd_activity is not None:
        try:
            activity = SS.recap_opd_activity
            recap = flex_unused.compute_recapture(
                flex_clinics, activity, rec_year, rec_month,
                ledger_payments_for_quarter=ledger_payments_for_quarter,
            )
            pipe = {"profile": "opd_live", "activity": activity, "recap": recap}
        except Exception as e:
            SS["recap_pipe_error"] = errors.capture(e)

    rdf = pd.DataFrame(pipe["recap"]) if pipe else pd.DataFrame()
    # Split the recapture rows into the ones we actually process this cycle vs
    # the ones excluded by the ledger filter (roster entry says active but no
    # FLEX payment in the quarter — likely the clinic left the program). The
    # excluded set is surfaced in the review step as a warning so the operator
    # can update the Clinic Roster, but their rows DON'T flow into the unused-
    # recapture invoice builder or the overage routing. Safety: a stale-roster
    # clinic with no real payments must NOT generate an internal recapture
    # invoice or an overage-bill.
    if pipe:
        recap_included = [r for r in pipe["recap"] if not r.get("excluded_no_payments")]
        recap_excluded = [r for r in pipe["recap"] if r.get("excluded_no_payments")]
    else:
        recap_included, recap_excluded = [], []
    udf = pd.DataFrame()
    next_ref = recap_start
    if pipe and recap_included:
        udf, next_ref = flex_unused.build_unused_invoice_import(
            recap_included, rec_year, rec_month, recap_start, sales_class,
        )
    overs = flex_unused.overage_rows(recap_included) if pipe else []
    annotated = []
    if overs:
        annotated = flex_overage.annotate_overages(
            overs, rec_year, rec_month, today_d, cfg_all, SS.recap_credit_offsets,
        )
    direct_count = sum(1 for r in annotated if r["route"] in ("direct", "missed_cutoff") and r["net_overage"] > 0)
    partner_count = sum(1 for r in annotated if r["route"] == "partner" and r["net_overage"] > 0)
    flagged = [r for r in annotated if r.get("escalation_flag")]

    # Dynamic step list — only include steps that have something to show.
    # Overage is split into two separate steps so OnePlace partner submission
    # gets its own full-width page; previously it lived in a tab alongside
    # direct-bill and was easy to miss.
    STEPS = [("setup", "Cycle setup"), ("upload", "Fetch OPD activity")]
    if pipe and not rdf.empty:
        STEPS.append(("review", "Review activity"))
        if not udf.empty:
            STEPS.append(("recapture", "Unused recapture"))
        if direct_count:
            STEPS.append(("direct_bill", "Direct-bill overages"))
        if partner_count:
            STEPS.append(("partner_submission", "OnePlace partner submission"))
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
                "Only clinics whose staggered quarter ENDS in this month will be processed."
            )
            # 2×2 layout — top row: Month + Year; bottom row: Starting Invoice # +
            # live quarter-window summary scaled to fit the cell. Sales class is no
            # longer editable (always '03-Telemedicine' — set via SS.setdefault above).
            top_l, top_r = st.columns(2)
            SS.recap_month = int(top_l.selectbox(
                "Recapture month", list(range(1, 13)), index=rec_month - 1,
                format_func=lambda m: dt.date(2000, m, 1).strftime("%B"),
                key="w_recap_month",
            ))
            SS.recap_year = int(top_r.number_input(
                "Recapture year", value=rec_year, step=1, key="w_recap_year"))

            bot_l, bot_r = st.columns(2)
            SS.recap_start_ref = int(bot_l.number_input(
                "Starting Invoice No (QBO max + 1)", value=recap_start, step=1,
                key="w_recap_start_ref"))

            new_win_s, new_win_e = flex_unused.quarter_window(int(SS.recap_year), int(SS.recap_month))
            new_group = [c for c in flex_clinics
                         if c.get("active") and flex_unused.is_quarter_end(c.get("calendar_spread"), int(SS.recap_month))]
            new_cutoff = flex_overage.cutoff_date(
                int(SS.recap_year), int(SS.recap_month),
                int((cfg_all.get("flex", {}).get("overage", {}) or {}).get("finance_partner_cutoff_day", 5)),
            )
            with bot_r:
                with st.container(border=True):
                    # Date range + qualifying-clinics count on one line (wraps to two
                    # lines if it doesn't fit the column width).
                    st.markdown(
                        f"**:blue[{new_win_s:%b %d} → {new_win_e:%b %d, %Y}]**"
                        f"&nbsp;&nbsp;·&nbsp;&nbsp;**{len(new_group)}** qualifying clinics"
                    )

        elif step_key == "upload":
            st.markdown("### Fetch OPD activity")
            st.caption(
                f"Pulls invoice-level data (net of credits) directly from OPD for "
                f"**{win_start:%B %d, %Y} – {win_end:%B %d, %Y}**. This is the only "
                f"supported source — manual exports either price per consult "
                f"(wrong totals) or ship a partial date range (missing months)."
            )

            # ── Primary action: live OData fetch from telehealth.oncurapartners.com
            def _do_opd_fetch():
                with st.spinner(
                    f"Fetching invoices for {win_start:%b %d} – {win_end:%b %d, %Y} "
                    "from telehealth.oncurapartners.com…"
                ):
                    activity, raw_df, orphans = opd_api.flex_activity_for_quarter(
                        rec_year, rec_month,
                    )
                    SS.recap_data_source = "opd_live"
                    SS.recap_opd_activity = activity
                    # to_dict('records') so the audit manifest can serialize easily
                    SS.recap_opd_raw_rows = raw_df.to_dict("records")
                    SS.recap_opd_invoice_count = len(raw_df)
                    SS.recap_opd_clinic_count = len(activity)
                    SS.recap_opd_components_mismatch = (
                        int((~raw_df["components_match"]).sum()) if not raw_df.empty else 0
                    )
                    # Orphan invoices: Invoice_Clinic FK didn't resolve to a clinic
                    # name. These are silently EXCLUDED from the activity dict —
                    # surface count + dollar total so the operator can investigate
                    # before committing the cycle (otherwise the affected clinic
                    # gets billed for full threshold as 'unused', which is wrong).
                    SS.recap_opd_orphans = orphans
                    SS.recap_opd_fetched_at = (
                        dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                    )

            live_loaded = (SS.recap_data_source == "opd_live"
                           and SS.recap_opd_activity is not None)
            btn_label = (":material/cloud_download:  Refresh from OPD"
                         if live_loaded
                         else ":material/cloud_download:  Fetch quarter from OPD")
            if st.button(btn_label, type="primary", use_container_width=True,
                         key="w_recap_fetch_opd"):
                try:
                    _do_opd_fetch()
                    st.rerun()
                except Exception as e:
                    _err = errors.capture(e)
                    st.error(
                        f"**OPD fetch failed:** `{_err['summary']}`\n\n"
                        "Check that OPD_ODATA_USER / OPD_ODATA_PASS are set in Streamlit "
                        "Cloud secrets, then click the button again. If the OPD OData "
                        "feed is unreachable, message Alexander — Stage 3 can't be run "
                        "from any other source. Per-consult exports from the OPD "
                        "Consults page produce wrong numbers (the local price table "
                        "doesn't match real OPD billing), and per-month invoice "
                        "exports almost always miss data from one or more months in "
                        "the quarter."
                    )
                    errors.render_details(_err)

            if live_loaded:
                fetched_at = (SS.recap_opd_fetched_at or "")[:19].replace("T", " ")
                st.success(
                    f":material/check_circle: **Loaded {SS.recap_opd_invoice_count:,} "
                    f"invoices across {SS.recap_opd_clinic_count} clinic(s)** — "
                    f"fetched {fetched_at} UTC."
                )
                if SS.recap_opd_components_mismatch:
                    st.caption(
                        f":gray[{SS.recap_opd_components_mismatch} invoice(s) had a "
                        "Subtotal/Credit/Admin reconciliation gap of ≥$1 — typically "
                        "$4-AdminFee voids. `TotalPrice` is still used (authoritative).]"
                    )
                # Orphan-invoice warning: rows whose Invoice_Clinic FK didn't
                # resolve. These are excluded from the activity dict, which
                # means the affected clinic would otherwise be billed for the
                # FULL threshold as unused credit (wrong). Surface loudly so
                # the operator can investigate before generating any files.
                _orphans = SS.get("recap_opd_orphans") or {"count": 0}
                if _orphans.get("count"):
                    fks = ", ".join(str(x) for x in _orphans.get("fk_list", [])[:5])
                    more = "" if len(_orphans.get("fk_list", [])) <= 5 else f" (+ {len(_orphans['fk_list']) - 5} more)"
                    st.warning(
                        f":material/warning: **{_orphans['count']} invoice(s) totaling "
                        f"${_orphans['total']:,.2f}** reference a clinic ID not in the "
                        f"current OPD clinic index — these are **excluded from the "
                        f"activity calc**. If the affected clinic should have been on "
                        f"this quarter's roster, the recapture amount will be too high "
                        f"(full threshold billed as unused) and the overage too low. "
                        f"Orphaned Mendix clinic IDs: `{fks}`{more}. Investigate before "
                        f"committing this cycle.",
                        icon=":material/warning:",
                    )

            # Manual-upload fallback intentionally removed (2026-06-09).
            # The OPD Consults export is per-CONSULT and is priced via our
            # local service_prices.json — which doesn't match real OPD billing,
            # so Stage 3 numbers computed off that source are always wrong.
            # The OPD Invoices export IS shape-compatible with what live OData
            # returns, but the operator-applied date filter on the OPD UI is a
            # reliable way to ship a partial-quarter file (that's exactly what
            # caused JW's 2026-06-09 incident — a May-only file produced 35
            # inflated unused-recapture invoices). Live OPD pull always reads
            # the full quarter window directly via OData, so it's the only
            # path Stage 3 supports. If the OData feed is unreachable, fix
            # that upstream — don't paper over with a file upload.

            # Error display when the OPD fetch failed
            if SS.get("recap_pipe_error"):
                st.error(
                    f"**Could not process the OPD response:**  "
                    f"`{SS['recap_pipe_error']['summary']}`\n\n"
                    "Click the fetch button again to retry. If the error persists, "
                    "message Alexander — the OData feed or our integration may need "
                    "attention."
                )
                errors.render_details(SS["recap_pipe_error"])

            # Soft prompt when the OPD pull hasn't been run yet
            if not pipe and not SS.get("recap_pipe_error"):
                st.warning(
                    ":material/info: Click **Fetch quarter from OPD** above to load "
                    "the full quarter window. Stage 3 only accepts data from the "
                    "live OPD feed — per-consult exports and partial-month files "
                    "produce wrong numbers and are not supported.",
                    icon=":material/info:",
                )

            if pipe:
                total_qualifying = sum(
                    1 for c in flex_clinics
                    if c.get("active") and flex_unused.is_quarter_end(c.get("calendar_spread"), rec_month)
                )
                pm1, pm2 = st.columns(2)
                pm1.metric("Source", "OPD (live)")
                pm2.metric(
                    "Qualifying for this month",
                    f"{len(rdf)} / {total_qualifying}",
                    help="Rows emitted (group anchors only) over all active clinics whose quarter ends this month. "
                         "Difference = group members (e.g. Mohnacky / River Trail / PR-vets non-anchors) rolled into their anchor's row.",
                )

        elif step_key == "review":
            st.markdown("### Review activity")
            st.caption("What the app pulled from OPD. Sanity-check before generating files.")

            # Ledger read failed → the payment gate failed CLOSED (every clinic
            # held out). Surface it above everything else: nothing is billable
            # until the ledger is readable again.
            _ledger_err = SS.get("recap_ledger_read_failed")
            if _ledger_err:
                st.error(
                    ":material/priority_high: **The processed-payments ledger could "
                    "not be read — the payment gate failed CLOSED, so every clinic is "
                    "held out and this cycle will generate NO recapture or overage.** "
                    "This is deliberate: without the ledger we can't confirm who "
                    "actually paid into FLEX this quarter, and silently including "
                    "everyone is the inflated-recapture incident. Restore the ledger "
                    "(Settings → ledger summary, or docs/RECOVERY.md) and reload "
                    "before committing.",
                    icon=":material/priority_high:",
                )
                errors.render_details(_ledger_err)

            # Roster-vs-ledger mismatch warnings — surface BEFORE the metrics
            # so the operator sees them first. Two flavors:
            #   1. excluded_no_payments: roster says active, ledger has no
            #      positive FLEX payments this quarter → likely left program
            #   2. orphan payments: ledger has payments for a clinic not in
            #      roster → almost certainly a missing roster entry
            if recap_excluded:
                names = [(r.get("qb_name") or r.get("clinic_name") or "?")
                         for r in recap_excluded]
                st.warning(
                    f":material/warning: **{len(recap_excluded)} roster clinic(s) "
                    f"excluded from this cycle — no positive FLEX payments in "
                    f"the quarter window ({win_start:%b %d}–{win_end:%b %d, %Y}).** "
                    "These clinics are `active=true` in the Clinic Roster but the "
                    "ledger has no record of them paying this quarter — likely they "
                    "left the program. They will NOT generate recapture invoices or "
                    "overage bills until either (a) a Stage 1 payment is recorded "
                    "for them this quarter, or (b) they're marked inactive in the "
                    "Clinic Roster.  \n\n"
                    "Affected: " + ", ".join(names[:8])
                    + (f" (+{len(names) - 8} more)" if len(names) > 8 else ""),
                    icon=":material/warning:",
                )
            if orphan_payments:
                # Group orphan payments by qb_customer for a readable summary
                from collections import defaultdict
                by_cust = defaultdict(list)
                for p in orphan_payments:
                    by_cust[p.get("qb_customer") or p.get("contract") or "(unknown)"].append(p)
                lines = []
                for cust, payments in sorted(by_cust.items()):
                    total = sum(float(p.get("amount") or 0) for p in payments)
                    lines.append(f"**{cust}** — {len(payments)} payment(s), ${total:,.2f}")
                st.error(
                    f":material/priority_high: **{len(orphan_payments)} ledger "
                    f"payment(s) have no Clinic Roster entry — total "
                    f"${sum(float(p.get('amount') or 0) for p in orphan_payments):,.2f}.** "
                    "The finance partner wired us money for clinic(s) the roster "
                    "doesn't know about — Stage 3 cannot compute recapture/overage "
                    "without per-clinic threshold + monthly_credit config. **Add "
                    "the missing clinic(s) to the Clinic Roster before committing "
                    "this cycle.**  \n\n"
                    + "  \n".join(f"- {line}" for line in lines[:8])
                    + (f"  \n- … and {len(lines) - 8} more" if len(lines) > 8 else ""),
                    icon=":material/priority_high:",
                )

            # Cross-calendar group members. The engine pools every member's
            # threshold + activity onto the anchor's quarter-end month; a member
            # on a different calendar_spread is swept into a quarter that isn't
            # its own AND never gets a row in its real quarter-end month. Surface
            # it so the figure isn't trusted blind.
            _cal_mismatch = flex_unused.group_calendar_mismatches(flex_clinics)
            if _cal_mismatch:
                _lines = []
                for g in _cal_mismatch:
                    offs = ", ".join(
                        f"{m['clinic_name']} ({m['calendar_spread']})" for m in g["members"]
                    )
                    _lines.append(
                        f"**{g['anchor']}** (anchor: {g['anchor_spread']}) — off-calendar "
                        f"member(s): {offs}"
                    )
                st.warning(
                    f":material/warning: **{len(_cal_mismatch)} multi-clinic group(s) "
                    "have members on a DIFFERENT billing calendar than the anchor.** "
                    "Their threshold and activity are pooled into the anchor's "
                    "quarter-end month even though their own quarter doesn't close "
                    "then, and they get no row in the month it does — so this group's "
                    "pooled figure is unreliable. Align the calendars in the Clinic "
                    "Roster, or split the off-calendar clinics out of the group, "
                    "before committing.  \n\n" + "  \n".join(f"- {ln}" for ln in _lines),
                    icon=":material/warning:",
                )

            # Split included (billable) vs excluded (active in roster but no FLEX
            # payment this quarter — held out, see the warning above). The headline
            # metrics show what will ACTUALLY be billed, so they reconcile with the
            # recapture/overage steps; the excluded amount is a caption beneath.
            if "excluded_no_payments" in rdf.columns:
                _excluded_mask = rdf["excluded_no_payments"].fillna(False)
                included_df = rdf[~_excluded_mask]
                excluded_df = rdf[_excluded_mask]
            else:
                included_df, excluded_df = rdf, rdf.iloc[0:0]

            def _col_sum(df, col):
                return float(df[col].fillna(0).sum()) if (col in df.columns and not df.empty) else 0.0

            _exc_unused = _col_sum(excluded_df, "unused")
            _exc_overage = _col_sum(excluded_df, "overage")
            _n_excl = len(excluded_df)

            m1, m2, m3 = st.columns(3)
            m1.metric("Source", pipe["profile"])
            m2.metric("Unused (recapture)", f"${_col_sum(included_df, 'unused'):,.2f}")
            if _n_excl and _exc_unused:
                m2.caption(f"+ ${_exc_unused:,.2f} across {_n_excl} excluded clinic(s) — not billed")
            m3.metric("Overage (gross)", f"${_col_sum(included_df, 'overage'):,.2f}")
            if _n_excl and _exc_overage:
                m3.caption(f"+ ${_exc_overage:,.2f} across {_n_excl} excluded clinic(s) — not billed")
            if pipe["profile"] == "case_grid":
                st.caption("Case-grid: activity = sum of priced services per case (no AdminFee, STAT +$125).")
            # Activity-match check operates on INCLUDED rows only — excluded
            # ones already have a more specific warning above.
            no_act = included_df[included_df["activity_match"] == "none"]
            if not no_act.empty:
                st.warning(
                    f"{len(no_act)} quarter-end clinic(s) had no matched OPD activity: "
                    + ", ".join(no_act["clinic_name"].head(8))
                    + (" …" if len(no_act) > 8 else "")
                )
            # Status flag per row. The breakdown lists EVERY quarter-close clinic,
            # including held-out ones; without this column a held-out clinic is
            # indistinguishable from a billed one — that is exactly why Alum Rock
            # looked like it was "on the import" when it was actually held out.
            def _row_status(r):
                if r.get("excluded_no_payments"):
                    return "Held out — no payment"
                if r.get("activity_match") == "none":
                    return "No OPD match (full unused)"
                # Payment-band annotation: over-funded clinics had the pool
                # auto-raised to absorb extra payments; under-funded ones still
                # post the hurdle invoice but need a verified manual reduction.
                n, exp = r.get("payments_in_quarter"), r.get("expected_payments")
                band = ""
                if r.get("pool_basis") == "ledger_over":
                    over = round(float(r.get("effective_pool") or 0)
                                 - float(r.get("quarterly_threshold") or 0), 2)
                    band = f" · auto-raised +${over:,.0f} ({n} pmts)"
                elif r.get("underfunded"):
                    band = f" · underfunded ({n}/{exp} pmts — verify)"
                if (r.get("overage") or 0) > 0:
                    return "Overage" + band
                if (r.get("unused") or 0) > 0:
                    return "Recapture" + band
                return "Reconciled" + band
            breakdown = rdf.copy()
            breakdown["status"] = breakdown.apply(_row_status, axis=1)
            display_cols = [
                "status", "clinic_name", "qb_name", "finance_company", "contract_number",
                "calendar_spread", "quarterly_threshold", "quarter_activity",
                "payments_in_quarter", "unused", "overage", "activity_match",
            ]
            with st.expander(f"Per-clinic breakdown ({len(rdf)} clinics)"):
                st.dataframe(
                    breakdown[[c for c in display_cols if c in breakdown.columns]],
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
            st.markdown("### Unused recapture invoices")
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
                ui.persistence_warning()
                recap_initials = ui.initials_input("stage3_recap_audit_initials")
                if ui.record_button(
                    f"Mark {len(udf)} recapture invoice(s) as imported",
                    key="w_recap_mark_unused",
                    disabled=not recap_initials,
                ):
                    ok_l, added, _ = ledger.record_batch(
                        file_content=None,
                        filename=f"UnusedFlex_{dt.date(2000, rec_month, 1):%b}_{rec_year}.xlsx",
                        company="INTERNAL",
                        payments=recap_ledger_rows,
                        note=f"Stage 3 recapture / {rec_year}-{rec_month:02d}",
                    )
                    # Audit-manifest source descriptor so future auditors can
                    # trace exactly where the activity numbers came from.
                    if SS.recap_data_source == "opd_live":
                        _live_rows_df = pd.DataFrame(SS.recap_opd_raw_rows or [])
                        _audit_source = {
                            "name": "opd_odata_live",
                            "sha256": audit.output_hash_df(_live_rows_df),
                            "size_bytes": SS.recap_opd_invoice_count,
                            "fetched_at": SS.recap_opd_fetched_at,
                        }
                    else:
                        _audit_source = None
                    audit.record_cycle(
                        cycle_type="stage3_recapture",
                        approver=recap_initials or auth.current_role(),
                        year=rec_year, month=rec_month,
                        params={
                            "sales_class": sales_class,
                            "start_ref": recap_start,
                            "next_ref": next_ref,
                            "quarter_window": f"{win_start:%Y-%m-%d}..{win_end:%Y-%m-%d}",
                            "source": SS.recap_data_source,
                            "opd_invoice_count": (
                                SS.recap_opd_invoice_count
                                if SS.recap_data_source == "opd_live" else None
                            ),
                            "opd_components_mismatch": (
                                SS.recap_opd_components_mismatch
                                if SS.recap_data_source == "opd_live" else None
                            ),
                            "opd_orphan_count": (
                                (SS.recap_opd_orphans or {}).get("count", 0)
                                if SS.recap_data_source == "opd_live" else None
                            ),
                            "opd_orphan_total": (
                                (SS.recap_opd_orphans or {}).get("total", 0.0)
                                if SS.recap_data_source == "opd_live" else None
                            ),
                        },
                        source_file=_audit_source,
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

            # ── Zero-the-account adjustments hand-off ───────────────────────
            # Under-funded clinics (paid < expected) got a posted hurdle invoice
            # that's bigger than the credit on the account, so it won't zero —
            # accounting must reduce it (after verifying no payment was simply
            # un-imported). Over-funded clinics (paid > expected) were already
            # auto-raised in the recapture, so they're FYI only.
            inv_by_customer = {}
            for _, _ir in udf.iterrows():
                inv_by_customer.setdefault(
                    str(_ir["Customer"]).strip().lower(), _ir["Invoice No"])
            underfunded_rows = []
            for r in recap_included:
                if not r.get("underfunded"):
                    continue
                cust = (r.get("qb_name") or r.get("clinic_name") or "").strip().lower()
                cur = round(float(r.get("unused") or 0.0), 2)
                sug = round(float(r.get("balance_unused") or 0.0), 2)
                underfunded_rows.append({
                    "clinic": r.get("qb_name") or r.get("clinic_name"),
                    "payments": r.get("payments_in_quarter"),
                    "expected": r.get("expected_payments"),
                    "invoice_no": inv_by_customer.get(cust, "(see import)"),
                    "current": cur, "suggested": sug, "delta": round(cur - sug, 2),
                })
            overfunded_rows = [
                {"clinic": r.get("qb_name") or r.get("clinic_name"),
                 "payments": r.get("payments_in_quarter"),
                 "true_up": round(float(r.get("unused") or 0.0), 2)}
                for r in recap_included
                if r.get("pool_basis") == "ledger_over" and float(r.get("unused") or 0) > 0
            ]
            if underfunded_rows or overfunded_rows:
                st.divider()
                st.markdown("#### Zero-out adjustments")
                if underfunded_rows:
                    st.warning(
                        f":material/warning: **{len(underfunded_rows)} clinic(s) paid fewer than "
                        "the expected payments this quarter.** The posted unused invoice is larger "
                        "than the credit on the account, so it won't zero. Verify a payment wasn't "
                        "just un-imported, then reduce the invoice per the email.",
                        icon=":material/warning:",
                    )
                if overfunded_rows:
                    st.info(
                        f":material/info: **{len(overfunded_rows)} clinic(s) paid more than "
                        "expected** — recapture was auto-raised to absorb the extra credit so the "
                        "account zeros (no action; in the email for the record).",
                        icon=":material/info:",
                    )
                _zsubj, _zbody = accounting_handoff.recapture_zeroing_adjustments_email(
                    year=rec_year, month=rec_month,
                    underfunded=underfunded_rows, overfunded=overfunded_rows,
                )
                accounting_handoff.render_handoff(
                    _zsubj, _zbody, key_prefix="recap_zeroing_email",
                )

        elif step_key in ("direct_bill", "partner_submission"):
            # Shared totals + dataframe used by both overage steps.
            direct_total = sum(float(r["net_overage"]) for r in annotated
                               if r["route"] in ("direct", "missed_cutoff") and r["net_overage"] > 0)
            partner_total = sum(float(r["net_overage"]) for r in annotated
                                if r["route"] == "partner" and r["net_overage"] > 0)
            adf = pd.DataFrame(annotated)[[
                "clinic_name", "finance_company", "overage",
                "credit_applied", "net_overage", "route", "escalation_flag",
            ]]

            def _shared_overage_context(*, offsets_key: str):
                """Render the bits common to both overage steps: routing-rules
                caption, optional credit-offsets editor, all-overages preview,
                and escalation warning."""
                st.caption(
                    "OnePlace handles overages submitted before the cutoff. Great America + "
                    "New Lane have opted out (direct-bill). Self-Funded: direct. Missed cutoff: direct."
                )
                with st.expander(":gray[Pre-existing credit offsets (optional)]"):
                    st.caption(
                        "If an over-threshold clinic has an unapplied credit in QBO, enter it here — "
                        "the app applies it to the overage and only bills the remainder."
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
                        column_config={
                            "Pre-existing credit": st.column_config.NumberColumn(
                                "Pre-existing credit",
                                min_value=0.0,
                                step=0.01,
                                format="$%.2f",
                                help="Unapplied credit balance in QBO. Applied to overage; "
                                     "only the remainder is billed.",
                            ),
                        },
                        key=offsets_key,
                    )
                    # Defensive parse: a NumberColumn keeps values numeric, but
                    # if Streamlit ever returns a string (legacy clients, paste
                    # edge cases), fall back to 0.0 + caption rather than
                    # raising a ValueError into safe_stage.
                    _bad_rows: list[str] = []
                    def _parse_offset(raw, clinic):
                        try:
                            return float(raw or 0)
                        except (TypeError, ValueError):
                            _bad_rows.append(clinic)
                            return 0.0
                    SS.recap_credit_offsets = {
                        r["Clinic (QB)"]: _parse_offset(r["Pre-existing credit"], r["Clinic (QB)"])
                        for _, r in edited.iterrows()
                    }
                    if _bad_rows:
                        st.caption(
                            f":gray[Non-numeric credit value for {', '.join(_bad_rows)} "
                            "— treated as $0.00 offset.]"
                        )
                with st.expander(f":gray[All overages · {len(adf)} clinic(s) · preview]"):
                    st.dataframe(adf, use_container_width=True, height=240)
                if flagged:
                    names = ", ".join(r["clinic_name"] for r in flagged)
                    st.warning(
                        f":material/priority_high: Escalation clinic(s): **{names}** — "
                        f"communication may need to come from Marty / Accounting Manager (SOP-12).",
                        icon=":material/priority_high:",
                    )

            def _direct_block():
                st.caption(
                    f"These overages go to **accounting@oncurapartners.com** for **manual** "
                    f"billing in QBO. Tanya creates a QBO invoice per clinic, sends an "
                    f"Authorize.net link / PDF, and voids each invoice immediately after "
                    f"sending per **SOP-6**. The attached xlsx is her working reference — "
                    f"NOT a SaasAnt import."
                )
                bc1, bc2 = st.columns(2)
                bc1.metric("Clinics to bill", direct_count)
                bc2.metric("Total", f"${direct_total:,.2f}")
                # Human-readable billing worksheet (clinic / threshold / activity /
                # credit / net). This replaces the SaasAnt-shaped invoice import
                # because Tanya bills these manually today; the SaasAnt builder
                # remains in flex_overage.py for future use.
                didf = flex_overage.build_direct_billing_worksheet(
                    annotated, rec_year, rec_month, cfg_all,
                )
                xlsx_bytes = saasant.to_xlsx_bytes(didf, "OverageBilling")
                fname = f"OverageDirect_{dt.date(2000, rec_month, 1):%b}_{rec_year}.xlsx"

                # Email handoff to Tanya — worksheet attached, SOP-6 instructions
                # AND per-clinic detail (threshold/activity/credit/net) in body so
                # she can scan totals without opening the attachment.
                if not didf.empty:
                    _subj, _body = accounting_handoff.direct_bill_overage_email(
                        year=rec_year, month=rec_month,
                        invoice_count=len(didf),
                        invoice_total=float(direct_total),
                        clinic_details=didf.to_dict("records"),
                    )
                    accounting_handoff.render_handoff(
                        _subj, _body, key_prefix="recap_direct_email",
                        attachments=[(fname, xlsx_bytes)],
                    )
                else:
                    st.info("No direct-bill rows to send.")

                # Dedup against ledger — flag rows already emitted in prior runs.
                direct_payments_for_ledger = []
                already_direct: list[str] = []
                if not didf.empty:
                    direct_payments_for_ledger = [
                        {
                            "kind": "direct_overage",
                            "contract": row["QB Customer"],
                            "qb_customer": row["QB Customer"],
                            "payment_date": f"{rec_year:04d}-{rec_month:02d}-01",
                            "amount": float(row.get("Net Amount to Bill") or 0),
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
                            f"**{len(already_direct)} direct-bill invoice(s) already sent for "
                            f"this period.** Re-sending tells Tanya to bill these twice — "
                            f"review the file before sending the email."
                        )

                if not didf.empty:
                    st.divider()
                    st.caption(
                        ":gray[Initial below **after** you've sent the email above. This logs "
                        "the send to the audit manifest + dedup ledger.]"
                    )
                    ui.persistence_warning()
                    direct_initials = ui.initials_input("stage3_direct_audit_initials")
                else:
                    direct_initials = ""
                if not didf.empty and ui.record_button(
                    f"Record {len(didf)} direct-bill invoice(s) as sent to accounting",
                    key="w_recap_mark_direct",
                    disabled=not direct_initials,
                ):
                    ok_l, added_l, _ = ledger.record_batch(
                        file_content=None,
                        filename=fname,
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
                            "clinic_count": direct_count,
                            "sent_to": "accounting@oncurapartners.com",
                        },
                        outputs=[{
                            "name": "overage_direct_invoices",
                            "sha256": audit.output_hash_df(didf),
                            "row_count": len(didf),
                            "total": round(float(direct_total), 2),
                        }],
                        note=f"Direct-bill overages emailed to accounting for {dt.date(2000, rec_month, 1):%B %Y}",
                    )
                    st.success(
                        f"Recorded {len(didf)} direct-bill invoice(s) in audit manifest "
                        f"and {added_l} fingerprint(s) in the dedup ledger."
                    )

            def _partner_block():
                st.caption(
                    f"OnePlace bills these clinics on Oncura's behalf — send them the file before "
                    f"the cutoff. Cutoff for this cycle: **{cutoff:%B %d, %Y}**."
                )
                pc1, pc2, pc3 = st.columns(3)
                pc1.metric("Clinics", partner_count)
                pc2.metric("Total", f"${partner_total:,.2f}")
                pc3.metric("Submit by", f"{cutoff:%b %d, %Y}")
                pdf = flex_overage.build_partner_submission(annotated, rec_year, rec_month)
                xlsx_bytes_partner = saasant.to_xlsx_bytes(pdf, "OnePlaceSubmission")
                fname_partner = f"OnePlaceOverage_{dt.date(2000, rec_month, 1):%b}_{rec_year}.xlsx"

                # Email handoff — accounting@oncurapartners.com (Tanya) is the
                # recipient. Tanya forwards / sends to OnePlace before cutoff.
                if not pdf.empty:
                    _subj, _body = accounting_handoff.partner_submission_email(
                        year=rec_year, month=rec_month,
                        clinic_count=partner_count,
                        total=float(partner_total),
                        cutoff_date=cutoff,
                        clinic_details=pdf.to_dict("records"),
                    )
                    accounting_handoff.render_handoff(
                        _subj, _body, key_prefix="recap_partner_email",
                        attachments=[(fname_partner, xlsx_bytes_partner)],
                    )
                else:
                    st.info("No partner-submission rows to send.")

                # Dedup against ledger. build_partner_submission() owns the
                # column schema — hardcode the column names instead of probing.
                # Earlier code did `pdf.columns[0]` as a fallback, which silently
                # resolved to "Finance Partner" (= "OnePlace" for every row),
                # collapsing every clinic onto a single qb_customer value and
                # creating fingerprint collisions whenever two clinics in the
                # batch happened to share the same net overage.
                partner_payments_for_ledger = []
                already_partner: list[str] = []
                if not pdf.empty:
                    partner_payments_for_ledger = [
                        {
                            "kind": "partner_overage",
                            "contract": str(row["QB Customer"]),
                            "qb_customer": str(row["QB Customer"]),
                            "payment_date": f"{rec_year:04d}-{rec_month:02d}-01",
                            "amount": float(row["Net Overage to Submit"]),
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
                            f"**{len(already_partner)} partner submission(s) already sent for "
                            f"this period.** Re-sending tells Tanya to forward these to OnePlace "
                            f"twice — review before sending the email."
                        )

                if not pdf.empty:
                    st.divider()
                    st.caption(
                        ":gray[Initial below **after** you've sent the email above. This logs "
                        "the send to the audit manifest + dedup ledger.]"
                    )
                    ui.persistence_warning()
                    partner_initials = ui.initials_input("stage3_partner_audit_initials")
                else:
                    partner_initials = ""
                if not pdf.empty and ui.record_button(
                    f"Record {len(pdf)} partner-submission row(s) as sent to accounting",
                    key="w_recap_mark_partner",
                    disabled=not partner_initials,
                ):
                    ok_l, added_l, _ = ledger.record_batch(
                        file_content=None,
                        filename=fname_partner,
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
                            "sent_to": "accounting@oncurapartners.com",
                        },
                        outputs=[{
                            "name": "oneplace_overage_submission",
                            "sha256": audit.output_hash_df(pdf),
                            "row_count": len(pdf),
                            "total": round(float(partner_total), 2),
                        }],
                        note=f"OnePlace partner submission emailed to accounting for {dt.date(2000, rec_month, 1):%B %Y}",
                    )
                    st.success(
                        f"Recorded OnePlace submission ({len(pdf)} clinics) in audit manifest "
                        f"and {added_l} fingerprint(s) in the dedup ledger."
                    )

            # ── Step dispatch ──────────────────────────────────────────────────
            # Each overage route lives on its own wizard step so it gets the
            # full page (and the OnePlace partner submission isn't half-hidden
            # behind a tab next to direct-bill).
            if step_key == "direct_bill":
                st.markdown(f"### Direct-bill overages — {direct_count} clinic(s)")
                _shared_overage_context(offsets_key="w_recap_offsets_editor_direct")
                _direct_block()
            elif step_key == "partner_submission":
                st.markdown(f"### OnePlace partner submission — {partner_count} clinic(s)")
                _shared_overage_context(offsets_key="w_recap_offsets_editor_partner")
                _partner_block()

    # ── Navigation ────────────────────────────────────────────────────────────
    can_back = SS.recap_step > 0
    can_next = SS.recap_step < total - 1
    next_blocked_reason = ""
    if step_key == "upload" and pipe is None:
        can_next = False
        if SS.get("recap_pipe_error"):
            next_blocked_reason = "OPD fetch failed — retry the button above."
        else:
            next_blocked_reason = "Fetch the quarter from OPD before continuing."

    # Sign-off gate. A sign-off step only exists when it has rows to record, so
    # empty initials here means the operator is about to advance WITHOUT signing
    # off / recording. Clicking Next with no initials opens a large blocking
    # modal (Go back & add initials / Continue without recording) — hard to
    # miss, and it fires on EVERY un-initialed sign-off step.
    _signoff_key = {
        "recapture": "stage3_recap_audit_initials",
        "direct_bill": "stage3_direct_audit_initials",
        "partner_submission": "stage3_partner_audit_initials",
    }.get(step_key)
    _initials_live = (
        (SS.get(_signoff_key) or SS.get("user_initials", "") or "").strip()
        if _signoff_key else "n/a"
    )

    @st.dialog("Sign-off needed before you continue", width="large", dismissible=False)
    def _signoff_modal():
        st.info(
            "**You haven't entered your initials for this step.**\n\n"
            "Without a sign-off, this step will **NOT be recorded** to the ledger "
            "or the audit manifest. Add your initials to sign off, or continue "
            "without recording this step.",
            icon=":material/draw:",
        )
        _gb, _ct = st.columns(2)
        if _gb.button("← Go back & add initials", key="recap_signoff_goback",
                      type="primary", use_container_width=True):
            SS.pop("recap_signoff_modal", None)
            st.rerun()
        if _ct.button("Continue without recording →", key="recap_signoff_continue",
                      use_container_width=True):
            SS.pop("recap_signoff_modal", None)
            SS.recap_step += 1
            st.rerun()

    if SS.get("recap_signoff_modal") == SS.recap_step:
        _signoff_modal()

    # Single nav row: [◀ Set up new cycle]  [blocked reason]  [← Back]  [Next →]
    # The reset, Back, and Next live on the same horizontal plane so the
    # operator sees all available navigation actions at once.
    st.divider()
    nav_reset, nav_msg, nav_b, nav_n = st.columns([1.6, 3.4, 1, 1])
    if nav_reset.button(
        "◀ Back to Setup",
        key="w_recap_reset_bottom",
        use_container_width=True,
        help="Clear the uploaded file + credit offsets and return to the setup step — use this between monthly Stage 3 runs.",
    ):
        for k in ("recap_signoff_modal",
                  "recap_credit_offsets",
                  "recap_pipe_error",
                  "w_recap_offsets_editor",
                  "recap_opd_activity", "recap_opd_raw_rows", "recap_opd_fetched_at",
                  "recap_opd_invoice_count", "recap_opd_clinic_count",
                  "recap_opd_components_mismatch", "recap_opd_orphans"):
            SS.pop(k, None)
        # Default the next run back to the live-fetch path
        SS.recap_data_source = "opd_live"
        SS.recap_step = 0
        st.rerun()
    if not can_next and next_blocked_reason:
        nav_msg.caption(f":orange[{next_blocked_reason}]")
    if can_back:
        if nav_b.button("← Back", key=f"w_recap_back_{SS.recap_step}",
                        use_container_width=True):
            SS.pop("recap_signoff_modal", None)
            SS.recap_step -= 1
            st.rerun()
    if SS.recap_step < total - 1:
        if nav_n.button("Next →", key=f"w_recap_next_{SS.recap_step}",
                        type="primary", disabled=not can_next,
                        use_container_width=True):
            if _signoff_key and not _initials_live:
                # Un-initialed sign-off step: open the blocking modal, don't advance.
                SS["recap_signoff_modal"] = SS.recap_step
            else:
                SS.pop("recap_signoff_modal", None)
                SS.recap_step += 1
            st.rerun()
    else:
        nav_n.markdown("**Done ✓**")
