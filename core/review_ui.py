"""Review & Verify — manual FLEX-cycle verification, rendered as Stage 5 of the
Payment Cycle. Read-only: nothing writes to QBO, OPD, or the ledger. Extracted
from the former standalone Admin page so the cycle can host it as a stage.
"""
from __future__ import annotations

import datetime as dt
import io

import pandas as pd
import streamlit as st

from core import (flex_closeout, flex_unused, ledger, loaders, monthly_audit,
                  opd_api, qbo_reconcile)

_CENT = 0.01


def _norm(name) -> str:
    return " ".join(str(name or "").casefold().split())


def _money(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "-"


@st.cache_data(show_spinner=False)
def _load_qbo(data: bytes):
    """Parse an uploaded QBO Transaction Report once per file (cached on bytes)."""
    return qbo_reconcile.parse_report(io.BytesIO(data))


def render() -> None:
    """Render the Review & Verify body. The caller supplies the stage/page header."""
    SS = st.session_state
    flex = loaders.flex_master()
    flex_clinics = flex.get("clinics", [])

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

    tab_walk, tab_recap = st.tabs(["Clinic walkthrough", "Recorded totals"])

    # ═══════════════════════════════════════════════════════════════════════════════
    # CLINIC WALKTHROUGH — one closing clinic at a time (tie-up verification)
    # ═══════════════════════════════════════════════════════════════════════════════
    with tab_walk:
        _qdf = None
        with st.expander("Compare against QBO (optional) — drop in a Flex Discount transaction report"):
            st.caption("Export QBO's Transaction Report for the 4320 Flex Discount account (xlsx) and "
                       "upload it here. Each clinic below then shows the QBO figure beside the app's. "
                       "Read-only — nothing is written to QBO or the ledger.")
            _up = st.file_uploader("QBO Transaction Report (.xlsx)", type=["xlsx"], key="rv_qbo_up")
            if _up is not None:
                try:
                    _qdf = _load_qbo(_up.getvalue())
                    st.success(f"Loaded {len(_qdf)} transactions across {_qdf['nname'].nunique()} "
                               "names. Comparison shows on each clinic below.",
                               icon=":material/fact_check:")
                except Exception as e:  # noqa: BLE001 - surface any parse error plainly
                    _qdf = None
                    st.error(f"Could not read that file: {type(e).__name__}: {e}")

        _slides = flex_closeout.closeout_walkthrough(flex_clinics, _all_payments, year, month)
        if not _slides:
            st.info(f"No clinics closed for {dt.date(year, month, 1):%B %Y}. Pick the month a "
                    "quarter closed (for example, June closed the calendar-quarter clinics).")
        else:
            # One piece of state drives the slideshow: rv_wt_jump (the dropdown's
            # key). Prev/Next nudge it via on_click callbacks, which run before the
            # widgets rebuild — so the buttons and the dropdown never fight. (The
            # earlier two-variable version let a stale dropdown value undo Next.)
            SS.setdefault("rv_wt_jump", 0)
            SS["rv_wt_jump"] = max(0, min(int(SS["rv_wt_jump"]), len(_slides) - 1))
            i = SS["rv_wt_jump"]
            s = _slides[i]

            st.markdown(f"**Clinic {i + 1} of {len(_slides)}**  ·  {dt.date(year, month, 1):%B %Y} closeout")
            st.progress((i + 1) / len(_slides))
            st.selectbox("Jump to clinic", range(len(_slides)),
                         format_func=lambda j: _slides[j]["qb_name"], key="rv_wt_jump")

            st.divider()
            _grp = f"  ·  group of {s['group_member_count']}" if s.get("group_member_count") else ""
            st.subheader(s["qb_name"])
            st.caption(f"{s['finance_company'] or 'unknown'}{_grp}  ·  hurdle {_money(s['hurdle'])}"
                       f"  ·  activity ~{_money(s['activity'])}")

            st.markdown("**What went in**")
            _cp, _cc = st.columns(2)
            with _cp:
                st.markdown(f"Finance payments — **{len(s['payments'])}** (expect "
                            f"{s['expected_payments']}), total {_money(s['payment_total'])}")
                if s["payments"]:
                    st.dataframe(pd.DataFrame([
                        {"Date": p["date"], "Amount": p["amount"], "Company": p["company"]}
                        for p in s["payments"]]), hide_index=True, use_container_width=True,
                        column_config={"Amount": st.column_config.NumberColumn(format="$%.2f")})
                else:
                    st.caption("none recorded")
            with _cc:
                st.markdown(f"Credit memos — **{len(s['credit_memos'])}** (expect "
                            f"{s['expected_credit_memos']}), total {_money(s['credit_memo_total'])}")
                if s["credit_memos"]:
                    st.dataframe(pd.DataFrame([
                        {"Date": x["date"], "Amount": x["amount"]} for x in s["credit_memos"]]),
                        hide_index=True, use_container_width=True,
                        column_config={"Amount": st.column_config.NumberColumn(format="$%.2f")})
                else:
                    st.caption("none recorded")
                if s.get("pre_ledger_cm_months"):
                    st.caption(f"Credit memos for {', '.join(s['pre_ledger_cm_months'])} predate the "
                               "ledger (in QBO, not shown here).")

            if s["overage"] > 0:
                st.markdown(f"Quarter-end: **overage {_money(s['overage'])}** — activity was above "
                            f"the {_money(s['hurdle'])} hurdle.")
            elif s["unused"] > 0:
                st.markdown(f"Quarter-end: **unused {_money(s['unused'])}** — activity was below the "
                            f"{_money(s['hurdle'])} hurdle; an Unused-Flex-Credits invoice was raised.")
            else:
                st.markdown("Quarter-end: **no unused or overage** — activity met the hurdle.")

            if s["exceptions"]:
                st.markdown("**Exceptions to check**")
                for _e in s["exceptions"]:
                    st.warning(_e, icon=":material/flag:")
            else:
                st.success("No exceptions flagged.", icon=":material/check_circle:")

            # ── Compared to QBO (only when a transaction report is loaded) ──────────
            if _qdf is not None and not _qdf.empty:
                st.markdown("**Compared to QBO (4320 Flex Discount)**")
                _names = s.get("member_qb_names") or [s["qb_name"]]
                _qm = flex_closeout._quarter_months(year, month)
                _summ = qbo_reconcile.clinic_summary(_qdf, _names, _qm, year, month)
                if not _summ["matched"]:
                    st.info("No QBO rows found under this clinic's name. It may be recorded under a "
                            "different name in QBO (for example, a group partner's name).",
                            icon=":material/help:")
                else:
                    _exp_cm = 3 * max(1, len(_names))
                    _qcm = _summ["cm"]
                    _delta = _qcm["count"] - _exp_cm
                    _verdict = ("matches" if _delta == 0
                                else f"QBO short {abs(_delta)}" if _delta < 0
                                else f"QBO extra {_delta}")
                    _qc, _rc = st.columns(2)
                    with _qc:
                        _zero = f"  ·  +{_qcm['zero_count']} $0 dup" if _qcm["zero_count"] else ""
                        st.markdown(
                            f"Credit memos — app recorded **{len(s['credit_memos'])}**, "
                            f"QBO holds **{_qcm['count']}** of {_exp_cm} expected "
                            f"({_verdict}), total {_money(_qcm['total'])}{_zero}")
                        if _qcm["rows"]:
                            st.dataframe(pd.DataFrame([
                                {"Date": r["date"], "Coverage": r["coverage"],
                                 "Amount": r["amount"], "QBO #": r["num"]} for r in _qcm["rows"]]),
                                hide_index=True, use_container_width=True,
                                column_config={"Amount": st.column_config.NumberColumn(format="$%.2f")})
                        if _delta == 0:
                            st.success("Credit-memo count ties to QBO.", icon=":material/check_circle:")
                        else:
                            st.warning(f"Credit-memo count differs: {_verdict}.", icon=":material/flag:")
                    with _rc:
                        _app_rc = (f"overage {_money(s['overage'])}" if s["overage"] > 0
                                   else f"unused {_money(s['unused'])}" if s["unused"] > 0 else "none")
                        _rcp = _summ["recap"]
                        st.markdown(
                            f"Quarter recapture — app: **{_app_rc}**  ·  "
                            f"QBO posted: **{'none' if not _rcp['count'] else _money(_rcp['total'])}**")
                        if _rcp["rows"]:
                            st.dataframe(pd.DataFrame([
                                {"Date": r["date"], "Amount": r["amount"], "Description": r["desc"]}
                                for r in _rcp["rows"]]),
                                hide_index=True, use_container_width=True,
                                column_config={"Amount": st.column_config.NumberColumn(format="$%.2f")})
                        else:
                            st.caption("Not yet posted in QBO for this quarter — expected, since this "
                                       "is the closeout being verified.")

            st.divider()

            def _wt_go(delta):
                SS["rv_wt_jump"] = max(0, min(SS["rv_wt_jump"] + delta, len(_slides) - 1))

            _b, _spacer, _n = st.columns([1, 4, 1])
            _b.button("← Previous", key="rv_wt_prev", use_container_width=True,
                      disabled=(i == 0), on_click=_wt_go, args=(-1,))
            if i < len(_slides) - 1:
                _n.button("Next →", key="rv_wt_next", type="primary",
                          use_container_width=True, on_click=_wt_go, args=(1,))
            else:
                _n.markdown("**Done ✓**")


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

