"""Admin > Review & Verify — manual verification surface for a FLEX cycle.

Read-only. Nothing here writes to QBO, OPD, or the ledger. Two tabs:

  Monthly entries: every finance payment and credit memo the app recorded for
    the chosen month, per clinic, checked against the flex_master expectation
    (each credit memo should equal the clinic's monthly_credit, one per finance
    payment; each finance payment should equal monthly_finance_payment).

  Quarter recapture: for a chosen quarter-end month, re-pull OPD live and
    recompute unused/overage with the SAME compute_recapture Stage 3 uses, then
    reconcile against what was actually posted in the ledger and flag any
    disagreement (drift, reversals since posting, roster changes).

A scannable table plus a per-clinic drill-down, with a "flagged only" filter so
you can go clinic by clinic or jump straight to what needs a look.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from core import flex_closeout, flex_unused, ledger, loaders, monthly_audit, opd_api, ui

ui.header(
    "Review & Verify",
    "Check a FLEX cycle's recorded entries and calculations against expectations "
    "and a live OPD recompute. Read-only.",
    kicker="Admin · Review",
)

SS = st.session_state
flex = loaders.flex_master()
flex_clinics = flex.get("clinics", [])

_CENT = 0.01


def _norm(name) -> str:
    return " ".join(str(name or "").casefold().split())


def _clinic_index(clinics):
    idx = {}
    for c in clinics:
        for k in (c.get("qb_name"), c.get("clinic_name")):
            if k:
                idx.setdefault(_norm(k), c)
    return idx


CLINIC_IDX = _clinic_index(flex_clinics)


def _money(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "-"


# Period picker (shared default: previous calendar month).
_prev = dt.date.today().replace(day=1) - dt.timedelta(days=1)
pc1, pc2, _pc3 = st.columns([1, 1, 3])
year = int(pc1.number_input("Year", min_value=2024, max_value=dt.date.today().year,
                            value=_prev.year, step=1, format="%d", key="rv_year"))
month = int(pc2.selectbox("Month", list(range(1, 13)), index=_prev.month - 1,
                          format_func=lambda m: dt.date(2000, m, 1).strftime("%B"),
                          key="rv_month"))
period = dt.date(year, month, 1).strftime("%B %Y")

_all_payments, _ = ledger.load()
_all_payments = _all_payments.get("payments", [])

tab_month, tab_recap = st.tabs(["Monthly entries", "Quarter recapture"])

# ═══════════════════════════════════════════════════════════════════════════════
# MONTHLY ENTRIES
# ═══════════════════════════════════════════════════════════════════════════════
with tab_month:
    rows = monthly_audit.categorize(_all_payments, year, month)
    summary, _review, totals = monthly_audit.summarize(rows, flex_clinics)

    st.markdown(
        f"**{period}** recorded entries, from the processed-payments ledger. "
        f"Finance payments {_money(totals['finance_total'])} across {totals['finance_count']}, "
        f"credit memos {_money(totals['credit_total'])} across {totals['credit_count']}."
    )

    # Per-clinic checks against flex_master expectations.
    def _month_checks(rec):
        c = CLINIC_IDX.get(_norm(rec["clinic"]))
        exp_cm = float(c.get("monthly_credit") or 0.0) if c else None
        exp_fin = float(c.get("monthly_finance_payment") or 0.0) if c else None
        flags = []
        if rec["flex"] and not rec["credit_memo"]:
            flags.append("finance payment but no credit memo")
        if rec["credit_memo"] and not rec["flex"]:
            flags.append("credit memo but no finance payment")
        if rec["flex_n"] and rec["credit_memo_n"] and rec["flex_n"] != rec["credit_memo_n"]:
            flags.append(f"{rec['credit_memo_n']} credit memos vs {rec['flex_n']} payments")
        if exp_cm and rec["credit_memo_n"]:
            if abs(rec["credit_memo"] - rec["credit_memo_n"] * exp_cm) > _CENT:
                flags.append(
                    f"credit memo {_money(rec['credit_memo'])} vs expected "
                    f"{_money(rec['credit_memo_n'] * exp_cm)} "
                    f"({rec['credit_memo_n']} x {_money(exp_cm)})")
        if exp_fin and rec["flex_n"]:
            if abs(rec["flex"] - rec["flex_n"] * exp_fin) > _CENT:
                flags.append(
                    f"finance {_money(rec['flex'])} vs expected "
                    f"{_money(rec['flex_n'] * exp_fin)} "
                    f"({rec['flex_n']} x {_money(exp_fin)})")
        if rec.get("min_amount", 0) < 0:
            flags.append("negative amount (reversal)")
        if c is None:
            flags.append("not on the FLEX roster")
        return exp_cm, exp_fin, flags

    table, detail = [], {}
    for rec in summary:
        exp_cm, exp_fin, flags = _month_checks(rec)
        status = "OK" if not flags else "CHECK"
        table.append({
            "Clinic": rec["clinic"],
            "Finance co": rec["company"],
            "Finance $": rec["flex"],
            "# pmts": rec["flex_n"],
            "Credit memo $": rec["credit_memo"],
            "# CMs": rec["credit_memo_n"],
            "Expected CM/ea": exp_cm if exp_cm else None,
            "Status": status,
        })
        detail[rec["clinic"]] = {"rec": rec, "exp_cm": exp_cm, "exp_fin": exp_fin, "flags": flags}

    if not table:
        st.info("No FLEX entries recorded for this month.")
    else:
        only_flagged = st.checkbox("Show only clinics that need a look", key="rv_month_flagged")
        view = [r for r in table if (not only_flagged or r["Status"] == "CHECK")]
        n_flag = sum(1 for r in table if r["Status"] == "CHECK")
        st.caption(f"{len(table)} clinics, {n_flag} flagged.")
        st.dataframe(
            pd.DataFrame(view), hide_index=True, use_container_width=True,
            column_config={
                "Finance $": st.column_config.NumberColumn(format="$%.2f"),
                "Credit memo $": st.column_config.NumberColumn(format="$%.2f"),
                "Expected CM/ea": st.column_config.NumberColumn(format="$%.2f"),
            },
        )

        st.divider()
        pick = st.selectbox("Inspect a clinic", [r["Clinic"] for r in table], key="rv_month_pick")
        d = detail.get(pick)
        if d:
            rec, exp_cm, exp_fin, flags = d["rec"], d["exp_cm"], d["exp_fin"], d["flags"]
            st.markdown(f"### {pick}")
            st.caption(f"Finance company: {rec['company'] or 'unknown'}")
            # Individual ledger entries for this clinic this month.
            mine = [p for p in rows if _norm(p.get("qb_customer")) == _norm(pick)]
            lines = []
            for p in sorted(mine, key=lambda x: (x.get("kind", ""), str(x.get("payment_date", "")))):
                entry, _qbo = monthly_audit.ENTRY_META.get(p.get("kind"), (p.get("kind"), ""))
                lines.append({
                    "Entry": entry,
                    "Date": str(p.get("payment_date", ""))[:10],
                    "Amount": round(float(p.get("amount") or 0), 2),
                    "Company": p.get("company", ""),
                    "Contract": str(p.get("contract", "")),
                })
            st.dataframe(pd.DataFrame(lines), hide_index=True, use_container_width=True,
                         column_config={"Amount": st.column_config.NumberColumn(format="$%.2f")})
            # The arithmetic, written out.
            st.markdown("**Checks**")
            checks = []
            if exp_fin is not None:
                checks.append(
                    f"Finance payments: {rec['flex_n']} totaling {_money(rec['flex'])}; "
                    f"expected {_money(exp_fin)} each ({_money(rec['flex_n'] * exp_fin)} total).")
            if exp_cm is not None:
                checks.append(
                    f"Credit memos: {rec['credit_memo_n']} totaling {_money(rec['credit_memo'])}; "
                    f"expected {_money(exp_cm)} each ({_money(rec['credit_memo_n'] * exp_cm)} total).")
            c = CLINIC_IDX.get(_norm(pick))
            if c and (c.get("monthly_threshold") is not None):
                per_mo = round(rec['flex'] + rec['credit_memo'], 2)
                checks.append(
                    f"Payment + credit this month = {_money(per_mo)} "
                    f"vs monthly threshold {_money(c.get('monthly_threshold'))}.")
            for line in checks:
                st.write("- " + line)
            if flags:
                for f in flags:
                    st.warning(f, icon=":material/flag:")
            else:
                st.success("No issues flagged for this clinic.", icon=":material/check_circle:")

# ═══════════════════════════════════════════════════════════════════════════════
# QUARTER RECAPTURE — recorded figures from the ledger (+ optional OPD recompute)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_recap:
    st.markdown(
        "The quarter-end unused and overage Stage 3 recorded for the selected month, straight "
        "from the ledger. Uses the month selected above as the quarter-end (for the June cycle "
        "that closed the calendar-quarter clinics, select June)."
    )
    q_year, q_month = year, month
    win_start, win_end = flex_unused.quarter_window(q_year, q_month)
    st.caption(f"Quarter ending {dt.date(q_year, q_month, 1):%B %Y}  ·  window "
               f"{win_start:%b %d, %Y} to {win_end:%b %d, %Y}. Read from the ledger, no OPD pull.")

    _rec_rows = flex_closeout.recap_from_ledger(flex_clinics, _all_payments, q_year, q_month)
    if not _rec_rows:
        st.info(f"No recorded Stage 3 output (unused / overage) for "
                f"{dt.date(q_year, q_month, 1):%B %Y}. Run Stage 3 for that month first.")
    else:
        _un = round(sum(r["unused"] for r in _rec_rows), 2)
        _ov = round(sum(r["overage"] for r in _rec_rows), 2)
        _nu = sum(1 for r in _rec_rows if r["unused"] > 0)
        _no = sum(1 for r in _rec_rows if r["overage"] > 0)
        st.markdown(f"**{len(_rec_rows)} clinics** — unused {_money(_un)} ({_nu}), "
                    f"overage {_money(_ov)} ({_no}).")
        st.dataframe(
            pd.DataFrame([{
                "Clinic": r["qb_name"],
                "Finance co": r.get("finance_company") or "",
                "Threshold": r["quarterly_threshold"],
                "Activity": r["quarter_activity"],
                "Unused": r["unused"],
                "Overage": r["overage"],
            } for r in sorted(_rec_rows, key=lambda x: (x["qb_name"] or "").lower())]),
            hide_index=True, use_container_width=True,
            column_config={c: st.column_config.NumberColumn(format="$%.2f")
                           for c in ("Threshold", "Activity", "Unused", "Overage")},
        )
        st.caption("Activity is reconstructed from threshold, unused, and overage for context.")

    # Review notes that need no OPD pull: reversal payments (ledger) + roster calendar mismatches.
    _neg = flex_unused.clinics_with_negative_payments(
        flex_clinics, ledger.flex_payments_in_window(win_start, win_end))
    if _neg:
        with st.expander(f"Clinics with reversal (negative) payments this quarter ({len(_neg)})"):
            st.dataframe(pd.DataFrame([
                {"Clinic": n["clinic"], "Reversal total": n["reversal_total"],
                 "Rows": n["reversal_count"]} for n in _neg]),
                hide_index=True, use_container_width=True,
                column_config={"Reversal total": st.column_config.NumberColumn(format="$%.2f")})
    _cal = flex_unused.group_calendar_mismatches(flex_clinics)
    if _cal:
        with st.expander(f"Group calendar mismatches ({len(_cal)})"):
            for g in _cal:
                st.write(f"- **{g['anchor']}** (spread {g['anchor_spread']}) has members on a "
                         f"different calendar: " + ", ".join(
                             f"{m['clinic_name']} ({m['calendar_spread']})" for m in g["members"]))

    st.divider()
    st.caption("Optional: independently recompute from a live OPD pull and compare to what was "
               "posted. Slower; use it to confirm the recorded figures against OPD.")
    if st.button("Run live OPD reconcile", key="rv_run_recap"):
        try:
            activity, raw_df, orphans = opd_api.flex_activity_for_quarter(q_year, q_month)
            pays = ledger.flex_payments_in_window(win_start, win_end)
            recap = flex_unused.compute_recapture(
                flex_clinics, activity, q_year, q_month, ledger_payments_for_quarter=pays)
            SS["rv_recap"] = {"key": (q_year, q_month), "recap": recap, "orphans": orphans}
        except Exception as e:  # noqa: BLE001 - surface any OPD/auth error plainly
            SS.pop("rv_recap", None)
            st.error(f"Could not run the OPD reconcile: {type(e).__name__}: {e}")

    cached = SS.get("rv_recap")
    if cached and cached.get("key") == (q_year, q_month):
        recap = cached["recap"]
        included = [r for r in recap if not r.get("excluded_no_payments")]
        excluded = [r for r in recap if r.get("excluded_no_payments")]

        # What was actually posted, from the ledger, for this quarter-end month.
        rec_unused, rec_overage = {}, {}
        for p in _all_payments:
            if monthly_audit._ym(p.get("payment_date")) != (q_year, q_month):
                continue
            k = _norm(p.get("qb_customer"))
            amt = round(float(p.get("amount") or 0), 2)
            if p.get("kind") == "unused_invoice":
                rec_unused[k] = round(rec_unused.get(k, 0.0) + amt, 2)
            elif p.get("kind") == "direct_overage":
                rec_overage[k] = round(rec_overage.get(k, 0.0) + amt, 2)

        table, detail, mism = [], {}, 0
        seen = set()
        for r in included:
            name = r.get("qb_name") or r.get("clinic_name")
            k = _norm(name)
            seen.add(k)
            ru = rec_unused.get(k, 0.0)
            ro = rec_overage.get(k, 0.0)
            du = round(r["unused"] - ru, 2)
            do = round(r["overage"] - ro, 2)
            ok = abs(du) <= _CENT and abs(do) <= _CENT
            if not ok:
                mism += 1
            table.append({
                "Clinic": name,
                "Threshold": r["quarterly_threshold"],
                "Activity (OPD)": r["quarter_activity"],
                "Unused (calc)": r["unused"],
                "Unused (posted)": ru,
                "Overage (calc)": r["overage"],
                "Overage (posted)": ro,
                "Status": "OK" if ok else "MISMATCH",
            })
            detail[name] = {"r": r, "ru": ru, "ro": ro, "du": du, "do": do}

        # Posted rows with no matching recomputed clinic (posted but no longer computed).
        posted_only = sorted(
            {**rec_unused, **rec_overage}.keys() - seen
        )

        st.markdown(
            f"**Quarter ending {dt.date(q_year, q_month, 1):%B %Y}.** "
            f"{len(included)} clinics recomputed, {mism} do not match what was posted."
            + (f" {len(excluded)} roster clinics had no payment this quarter (shown below)." if excluded else "")
        )

        only_mism = st.checkbox("Show only mismatches", key="rv_recap_flagged")
        view = [r for r in table if (not only_mism or r["Status"] == "MISMATCH")]
        st.dataframe(
            pd.DataFrame(view), hide_index=True, use_container_width=True,
            column_config={c: st.column_config.NumberColumn(format="$%.2f") for c in
                           ("Threshold", "Activity (OPD)", "Unused (calc)", "Unused (posted)",
                            "Overage (calc)", "Overage (posted)")},
        )

        if posted_only:
            st.warning(
                "Posted this quarter but not in the recompute (verify these directly): "
                + ", ".join(posted_only), icon=":material/priority_high:")

        st.divider()
        if table:
            pick = st.selectbox("Inspect a clinic", [r["Clinic"] for r in table], key="rv_recap_pick")
            d = detail.get(pick)
            if d:
                r = d["r"]
                st.markdown(f"### {pick}")
                st.caption(f"{r.get('finance_company') or 'unknown'} · spread "
                           f"{r.get('calendar_spread') or 'n/a'}"
                           + (f" · group of {r.get('group_member_count')}" if r.get("group_member_count") else ""))
                st.markdown("**Recapture math (recomputed live)**")
                st.write(f"- Quarterly threshold (pooled): {_money(r['quarterly_threshold'])}")
                st.write(f"- OPD activity this quarter: {_money(r['quarter_activity'])} "
                         f"(name match: {r.get('activity_match')}"
                         + (f", score {r.get('fuzzy_score')}" if r.get('fuzzy_score') else "") + ")")
                if r.get("payments_in_quarter") is not None:
                    st.write(f"- Positive FLEX payments on the books: {r['payments_in_quarter']} "
                             f"vs expected {r['expected_payments']} "
                             f"(pool basis: {r['pool_basis']}, pool used {_money(r['effective_pool'])})")
                st.write(f"- Unused = max(pool - activity, 0) = {_money(r['unused'])}  |  "
                         f"Overage = max(activity - pool, 0) = {_money(r['overage'])}")
                st.markdown("**Reconcile against posted**")
                st.write(f"- Unused: calc {_money(r['unused'])} vs posted {_money(d['ru'])} "
                         f"(difference {_money(d['du'])})")
                st.write(f"- Overage: calc {_money(r['overage'])} vs posted {_money(d['ro'])} "
                         f"(difference {_money(d['do'])})")
                if abs(d["du"]) <= _CENT and abs(d["do"]) <= _CENT:
                    st.success("Recompute matches what was posted.", icon=":material/check_circle:")
                else:
                    st.warning("Recompute does not match what was posted. Investigate before "
                               "trusting the figure (a reversal, roster change, or OPD data change "
                               "since posting are the usual causes).", icon=":material/flag:")
                if r.get("underfunded"):
                    st.warning("Underfunded: fewer positive payments than expected. The posted "
                               "hurdle invoice may need a verified manual reduction.",
                               icon=":material/warning:")
                if r.get("activity_match") == "none":
                    st.warning("No OPD activity matched this clinic by name. Confirm the lack of "
                               "activity is real and not a name mismatch hiding real consults.",
                               icon=":material/help:")

        # Cross-checks that do not fit the per-clinic table.
        orphans = cached.get("orphans") or {}
        if orphans.get("count"):
            st.warning(
                f"OPD reported {orphans['count']} orphan invoice(s) totaling "
                f"{_money(orphans.get('total'))} whose clinic could not be resolved. "
                "These are excluded from activity, so a clinic could look under-used.",
                icon=":material/link_off:")
        negatives = cached.get("negatives") or []
        if negatives:
            with st.expander(f"Clinics with reversal (negative) payments this quarter ({len(negatives)})"):
                st.dataframe(pd.DataFrame([
                    {"Clinic": n["clinic"], "Reversal total": n["reversal_total"],
                     "Rows": n["reversal_count"]} for n in negatives],),
                    hide_index=True, use_container_width=True,
                    column_config={"Reversal total": st.column_config.NumberColumn(format="$%.2f")})
        cal = cached.get("cal_mismatch") or []
        if cal:
            with st.expander(f"Group calendar mismatches ({len(cal)})"):
                for g in cal:
                    st.write(f"- **{g['anchor']}** (spread {g['anchor_spread']}) has members on a "
                             f"different calendar: " + ", ".join(
                                 f"{m['clinic_name']} ({m['calendar_spread']})" for m in g["members"]))
        if excluded:
            with st.expander(f"Roster clinics with no payment this quarter ({len(excluded)})"):
                st.dataframe(pd.DataFrame([
                    {"Clinic": r.get("qb_name") or r.get("clinic_name"),
                     "Threshold": r["quarterly_threshold"],
                     "Activity (OPD)": r["quarter_activity"]} for r in excluded],),
                    hide_index=True, use_container_width=True,
                    column_config={c: st.column_config.NumberColumn(format="$%.2f")
                                   for c in ("Threshold", "Activity (OPD)")})
