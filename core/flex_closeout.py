"""FLEX Stage 4 — Closeout WIZARD content module.

Stages 1-3 of the Payment Cycle produce SaasAnt files and numbers; they do NOT
touch QuickBooks Online (QBO) or OPD directly. Stage 4 is the *wizard* that
walks the operator through the manual closeout one step at a time, telling them
exactly WHICH clinics need each action. It replaces the side spreadsheet Tanya
used to keep.

Scope note (important): OPD now auto-defaults FLEX clinic invoices to PAID
(built by Lawrence). So the wizard NEVER tells the operator to mark invoices
paid, lock clinics out, or disable OPD payment. The only OPD action left is
flipping the OVERAGE clinics' invoices to PAST DUE — because those clinics still
owe the overage. Everything else is QBO + billing.

Design:
  - `build_worklist(...)` assembles a per-clinic worklist dict from the Stage-3
    recap rows. Pure — testable without a Streamlit runtime.
  - `render_step(step_key, worklist)` draws one wizard step with streamlit.
  - `STEPS` names the four steps, in order.
  - Two small pure helpers (`group_members`, `corporate_clinics`) slice the
    flex_master clinic list.

No emojis anywhere — Material Symbols only, written as ":material/<name>:".
"""
from __future__ import annotations

import streamlit as st

from . import flex_unused, ledger


# ═══════════════════════════════════════════════════════════════════════════════
# STEPS — the wizard's ordered steps. (key, label). Consumed by the caller.
# ═══════════════════════════════════════════════════════════════════════════════

STEPS = [
    ("clinics",  "Closing clinics"),
    ("tieup",    "QBO tie-up"),
    ("overages", "Overages: Past Due + bill"),
    ("groups",   "Group credit-spread"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# PURE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def group_members(flex_clinics) -> dict[str, list[str]]:
    """Map group_id -> [qb_name, ...] using each clinic's "group_id" field.

    `flex_clinics` is the list of clinic dicts from data/flex_master.json.
    Clinics with no group_id (None / empty) are skipped. Order within a group
    follows the order clinics appear in the master list.
    """
    groups: dict[str, list[str]] = {}
    for clinic in flex_clinics or []:
        gid = clinic.get("group_id")
        if not gid:
            continue
        name = clinic.get("qb_name") or clinic.get("clinic_name")
        if not name:
            continue
        groups.setdefault(gid, []).append(name)
    return groups


def corporate_clinics(flex_clinics) -> list[str]:
    """Return qb_names whose name contains "CityVet" (case-insensitive)."""
    out: list[str] = []
    for clinic in flex_clinics or []:
        name = clinic.get("qb_name") or clinic.get("clinic_name") or ""
        if "cityvet" in name.lower():
            out.append(name)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# WORKLIST — assemble the per-clinic picture from the Stage-3 recap rows.
# ═══════════════════════════════════════════════════════════════════════════════

def build_worklist(flex_clinics, recap_rows, group_spread=None) -> dict:
    """Turn Stage-3 recap rows into a per-clinic closeout worklist.

    `recap_rows` are the rows returned by `core.flex_unused.compute_recapture`
    for the clinics whose quarter closed this run. `group_spread` is a list of
    credit-move dicts, e.g. {"amount": 123.45, "from": "Clinic A", "to": "Clinic B"}.

    Each clinic entry is:
        {qb_name, group, finance_company, threshold, activity, payments,
         unused, overage, outcome, past_due, is_corporate}
    where
        outcome     = "overage" if overage>0 else "unused" if unused>0 else "zero"
        past_due    = overage > 0   (this clinic's OPD invoice must go Past Due)
        is_corporate= qb_name is a corporate / direct-pay (CityVet) clinic

    Returns:
        {
          "clinics":        [clinic dict, ...]        # every closing clinic
          "overage_clinics":[clinic dict, ...]        # rows where overage > 0
          "group_moves":    group_spread or []
          "corporate":      [clinic dict, ...]        # rows where is_corporate
          "counts":         {"total","unused","overage","zero"}
        }
    """
    corp_names = {n.lower() for n in corporate_clinics(flex_clinics)}

    clinics: list[dict] = []
    for r in recap_rows or []:
        qb_name = r.get("qb_name") or r.get("clinic_name") or ""
        unused = round(float(r.get("unused") or 0.0), 2)
        overage = round(float(r.get("overage") or 0.0), 2)
        if overage > 0:
            outcome = "overage"
        elif unused > 0:
            outcome = "unused"
        else:
            outcome = "zero"
        clinics.append({
            "qb_name": qb_name,
            "group": r.get("group_id"),
            "finance_company": r.get("finance_company"),
            "threshold": round(float(r.get("quarterly_threshold") or 0.0), 2),
            "activity": round(float(r.get("quarter_activity") or 0.0), 2),
            "payments": r.get("payments_in_quarter"),
            "unused": unused,
            "overage": overage,
            "outcome": outcome,
            "past_due": overage > 0,
            "is_corporate": qb_name.lower() in corp_names,
        })

    overage_clinics = [c for c in clinics if c["overage"] > 0]
    corporate = [c for c in clinics if c["is_corporate"]]
    counts = {
        "total": len(clinics),
        "unused": sum(1 for c in clinics if c["outcome"] == "unused"),
        "overage": sum(1 for c in clinics if c["outcome"] == "overage"),
        "zero": sum(1 for c in clinics if c["outcome"] == "zero"),
    }
    return {
        "clinics": clinics,
        "overage_clinics": overage_clinics,
        "group_moves": group_spread or [],
        "corporate": corporate,
        "counts": counts,
    }


def _norm(name) -> str:
    return " ".join(str(name or "").casefold().split())


def _ym(date_str):
    """Parse a ledger payment_date to (year, month). Tolerates ISO 'YYYY-MM-DD'
    (direct_overage rows) and US 'MM/DD/YYYY' (unused_invoice rows). None on junk."""
    s = str(date_str or "").strip()
    if not s:
        return None
    if "-" in s[:10]:
        parts = s[:10].split("-")
        try:
            if len(parts[0]) == 4:
                return int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            return None
    if "/" in s:
        parts = s.split("/")
        try:
            if len(parts) == 3:
                return int(parts[2]), int(parts[0])
        except (ValueError, IndexError):
            return None
    return None


def recap_from_ledger(flex_clinics, ledger_payments, year, end_month):
    """Rebuild a closeout recap for (year, end_month) from what Stage 3 already
    posted to the ledger, so Stage 4 can load a month with NO live OPD pull.

    Reads the recorded Stage 3 output for that month: unused_invoice rows (the
    Unused-Flex-Credits invoices) and direct_overage rows. One row per clinic
    (already at anchor level, since Stage 3 emits pooled to the anchor), enriched
    with group_id / finance_company / quarterly_threshold from flex_master.
    quarter_activity is reconstructed for display as threshold - unused + overage.
    Returns [] when the ledger holds no Stage 3 output for that month (the caller
    should then tell the operator to run Stage 3).

    Note: clinics that closed but netted to exactly zero produced no unused or
    overage invoice, so they are not in the ledger and do not appear here. The
    posted unused / overage clinics — the ones needing action — all do.
    """
    idx = {}
    for c in flex_clinics or []:
        for k in (c.get("qb_name"), c.get("clinic_name")):
            if k:
                idx.setdefault(_norm(k), c)

    by_clinic: dict[str, dict] = {}
    for p in ledger_payments or []:
        kind = p.get("kind")
        if kind not in ("unused_invoice", "direct_overage"):
            continue
        if _ym(p.get("payment_date")) != (year, end_month):
            continue
        name = (p.get("qb_customer") or "").strip()
        if not name:
            continue
        amt = round(float(p.get("amount") or 0.0), 2)
        rec = by_clinic.setdefault(name, {"unused": 0.0, "overage": 0.0})
        if kind == "unused_invoice":
            rec["unused"] = round(rec["unused"] + amt, 2)
        else:
            rec["overage"] = round(rec["overage"] + amt, 2)

    rows = []
    for name, v in sorted(by_clinic.items(), key=lambda kv: kv[0].lower()):
        c = idx.get(_norm(name))
        threshold = round(float(c.get("quarterly_threshold") or 0.0), 2) if c else 0.0
        unused, overage = v["unused"], v["overage"]
        rows.append({
            "qb_name": name,
            "clinic_name": (c.get("clinic_name") if c else name),
            "group_id": c.get("group_id") if c else None,
            "finance_company": c.get("finance_company") if c else None,
            "quarterly_threshold": threshold,
            "quarter_activity": round(threshold - unused + overage, 2),
            "unused": unused,
            "overage": overage,
            "payments_in_quarter": None,
        })
    return rows


def _quarter_months(year, end_month):
    """The three (year, month) tuples of the quarter ending in end_month."""
    win_start, _ = flex_unused.quarter_window(year, end_month)
    out, y, m = [], win_start.year, win_start.month
    for _ in range(3):
        out.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def closeout_walkthrough(flex_clinics, ledger_payments, year, end_month):
    """Per-clinic tie-up verification for the clinics whose quarter ends in
    (year, end_month) — the data behind the clinic-by-clinic Review walkthrough.

    One entry per closing anchor / independent clinic (group members pooled into
    their anchor), each carrying, for the quarter:
      - payments: the finance payments (date, amount, company) + how many to expect
      - credit_memos: (date, amount)
      - unused / overage: the recorded quarter-end recapture
      - hurdle: the pooled quarterly threshold; activity: reconstructed for context
      - exceptions: payment/credit-memo count off, or a reversal
    All from the ledger + roster; no OPD pull. Empty list if nothing closes.
    """
    qmonths = set(_quarter_months(year, end_month))
    members_by_anchor: dict = {}
    for c in flex_clinics or []:
        parent = (c.get("parent_clinic_id") or "").strip().lower()
        if parent:
            members_by_anchor.setdefault(parent, []).append(c)
    recap = {_norm(r["qb_name"]): r
             for r in recap_from_ledger(flex_clinics, ledger_payments, year, end_month)}

    def _in_quarter(p):
        if p.get("kind") == "flex":
            return ledger._attribution_ym(p) in qmonths
        return _ym(p.get("payment_date")) in qmonths

    slides = []
    for c in flex_clinics or []:
        if not c.get("active") or c.get("parent_clinic_id"):
            continue
        if not flex_unused.is_quarter_end(c.get("calendar_spread"), end_month):
            continue
        members = members_by_anchor.get((c.get("clinic_name") or "").strip().lower(), [])
        group = [c] + members
        group_qbs = {_norm(g.get("qb_name") or g.get("clinic_name")) for g in group}
        threshold = round(sum(float(g.get("quarterly_threshold") or 0.0) for g in group), 2)

        pmts, cms = [], []
        for p in ledger_payments or []:
            if _norm(p.get("qb_customer")) not in group_qbs or not _in_quarter(p):
                continue
            if p.get("kind") == "flex":
                pmts.append({"date": str(p.get("payment_date"))[:10],
                             "amount": round(float(p.get("amount") or 0.0), 2),
                             "company": p.get("company", "")})
            elif p.get("kind") == "credit_memo":
                cms.append({"date": str(p.get("payment_date"))[:10],
                            "amount": round(float(p.get("amount") or 0.0), 2)})
        pmts.sort(key=lambda x: x["date"])
        cms.sort(key=lambda x: x["date"])

        rr = recap.get(_norm(c.get("qb_name") or c.get("clinic_name")))
        unused = round(float(rr["unused"]), 2) if rr else 0.0
        overage = round(float(rr["overage"]), 2) if rr else 0.0
        clinic_count = len(group)
        expected_payments = 3 * clinic_count

        exceptions = []
        if len(pmts) != expected_payments:
            exceptions.append(
                f"{len(pmts)} finance payment(s) on the books, expected {expected_payments} "
                f"({clinic_count} clinic(s) x 3 months).")
        if len(cms) != len(pmts):
            exceptions.append(
                f"{len(cms)} credit memo(s) vs {len(pmts)} payment(s) — should be one-for-one.")
        if any(p["amount"] < 0 for p in pmts):
            exceptions.append("A finance payment is negative (reversal) — check for a manual adjustment.")

        slides.append({
            "qb_name": c.get("qb_name") or c.get("clinic_name"),
            "finance_company": c.get("finance_company"),
            "group_member_count": clinic_count if members else None,
            "hurdle": threshold,
            "activity": round(threshold - unused + overage, 2),
            "payments": pmts,
            "payment_total": round(sum(p["amount"] for p in pmts), 2),
            "expected_payments": expected_payments,
            "credit_memos": cms,
            "credit_memo_total": round(sum(x["amount"] for x in cms), 2),
            "unused": unused,
            "overage": overage,
            "exceptions": exceptions,
        })
    slides.sort(key=lambda s: (s["qb_name"] or "").lower())
    return slides


# ═══════════════════════════════════════════════════════════════════════════════
# RENDER — one wizard step at a time (streamlit).
# ═══════════════════════════════════════════════════════════════════════════════

def _money(value) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return f"${value}"


def _payments_str(value) -> str:
    """Human payment count — "-" when the ledger wasn't supplied (None)."""
    return "-" if value is None else str(value)


def _outcome_amount(clinic: dict):
    """The single dollar figure that matters for a clinic: overage or unused."""
    if clinic["outcome"] == "overage":
        return clinic["overage"]
    if clinic["outcome"] == "unused":
        return clinic["unused"]
    return 0.0


# outcome -> (label, hex color) matching Tanya's tracker: unused=blue, overage=green.
_OUTCOME_STYLE = {
    "unused": ("UNUSED", "#2563eb"),   # blue
    "overage": ("OVERAGE", "#16a34a"),  # green
    "zero": ("zero", None),             # plain
}


def _outcome_badge(outcome: str) -> str:
    """A small colored markdown badge for the outcome (no emoji)."""
    label, color = _OUTCOME_STYLE.get(outcome, (outcome, None))
    if color:
        return f"<span style='color:{color};font-weight:600'>{label}</span>"
    return f"<span style='color:#6b7280'>{label}</span>"


def render_step(step_key: str, worklist: dict) -> None:
    """Render one Stage-4 wizard step with streamlit.

    Args:
        step_key: one of the keys in STEPS ("clinics", "tieup", "overages",
            "groups").
        worklist: the dict returned by build_worklist(...).
    """
    if step_key == "clinics":
        _render_clinics(worklist)
    elif step_key == "tieup":
        _render_tieup(worklist)
    elif step_key == "overages":
        _render_overages(worklist)
    elif step_key == "groups":
        _render_groups(worklist)
    else:
        st.warning(f"Unknown closeout step: {step_key}")


def _render_clinics(worklist: dict) -> None:
    """The workbook replacement: a scannable picture of every closing clinic."""
    clinics = worklist.get("clinics", [])
    counts = worklist.get("counts", {})

    st.subheader("Closing clinics")
    if not clinics:
        st.info(
            ":material/inbox: No clinics closed their quarter this run. "
            "Nothing to work in Stage 4."
        )
        return

    st.markdown(
        f"**{counts.get('total', 0)} closing** — "
        f"{counts.get('unused', 0)} unused, "
        f"{counts.get('overage', 0)} overage, "
        f"{counts.get('zero', 0)} zero."
    )
    st.caption(
        "The picture that replaces the side tracker. Blue = unused, "
        "green = overage. No action here — just confirm the list."
    )

    # Clean color-coded list: outcome badge, clinic, and the unused/overage amount.
    # Threshold, activity, and payment counts belong to the QBO tie-up step, not this
    # confirm-the-list picture.
    for c in clinics:
        st.markdown(
            f"- {_outcome_badge(c['outcome'])} **{c['qb_name']}** · "
            f"{_money(_outcome_amount(c))}",
            unsafe_allow_html=True,
        )


def _render_tieup(worklist: dict) -> None:
    """QBO Receive-Payment tie-up, with a per-clinic checkbox tracker."""
    clinics = worklist.get("clinics", [])

    st.subheader("QBO tie-up")
    st.markdown(
        "In QBO, run a **Receive Payment** dated the **last day of the quarter "
        "month** for each closing clinic. Apply this quarter's lines so the "
        "account nets to **$0**:\n\n"
        "- the finance-company payment(s)\n"
        "- the credit memo(s)\n"
        "- the `Unused-Flex-Credits` invoice (unused clinics)\n\n"
        "**Every applied line must say \"FLEX\"** — never \"merchant services\", "
        "never blank. Overage clinics intentionally leave the overage overdue "
        "(you bill it in the next step), so they will not net to $0 — that is "
        "expected."
    )

    if not clinics:
        st.info(":material/inbox: No closing clinics to tie up this run.")
        return

    st.caption("Check each clinic off as you tie it up. Counts shown so you don't need a spreadsheet.")
    for c in clinics:
        label, _ = _OUTCOME_STYLE.get(c["outcome"], (c["outcome"], None))
        target = _money(_outcome_amount(c))
        kind = "overage (leave overdue)" if c["outcome"] == "overage" else \
               "unused invoice" if c["outcome"] == "unused" else "nets to $0"
        st.checkbox(
            f"{c['qb_name']} — {_payments_str(c['payments'])} pmts · "
            f"credit-memo/{label.lower()} target {target} · {kind}",
            key=f"closeout_tieup_{c['qb_name']}",
        )


def _render_overages(worklist: dict) -> None:
    """Only the overage clinics: flip OPD Past Due + bill outside OPD."""
    overage_clinics = worklist.get("overage_clinics", [])

    st.subheader("Overages: Past Due + bill")
    if not overage_clinics:
        st.success(
            ":material/check_circle: No overages this quarter — "
            "nothing to mark Past Due."
        )
        return

    st.markdown(
        "These clinics used **more** than their entitlement, so they still owe "
        "the overage. For each one: flip its **OPD invoice to PAST DUE** (OPD "
        "otherwise defaults FLEX invoices to Paid), then **bill the overage "
        "outside OPD**:\n\n"
        "- **Non-corporate** → charge via **authorize.net**.\n"
        "- **Corporate (CityVet)** → **send a statement; the clinic wires** the payment."
    )

    for c in overage_clinics:
        bill = ("send statement, clinic wires" if c["is_corporate"]
                else "authorize.net")
        corp = " · CORPORATE" if c["is_corporate"] else ""
        st.checkbox(
            f"{c['qb_name']} — overage {_money(c['overage'])} · "
            f"flip OPD invoice to PAST DUE · bill: {bill}{corp}",
            key=f"closeout_overage_{c['qb_name']}",
        )


def _render_groups(worklist: dict) -> None:
    """The audit-friendly group credit-spread table, with per-move checkboxes."""
    moves = worklist.get("group_moves", [])

    st.subheader("Group credit-spread")
    st.caption(
        "Spread group overages across the group's members via CREDITS ONLY "
        "(never payments), per contract."
    )

    if not moves:
        st.success(
            ":material/check_circle: No group credit-spread needed this quarter."
        )
        return

    # Group the moves by destination clinic for an audit-friendly layout.
    by_to: dict[str, list[dict]] = {}
    for m in moves:
        to = m.get("to") or m.get("to_clinic") or "?"
        by_to.setdefault(to, []).append(m)

    for i, (to, group_moves) in enumerate(by_to.items()):
        st.markdown(f"**Into {to}**")
        for j, m in enumerate(group_moves):
            amt = _money(m.get("amount"))
            frm = m.get("from") or m.get("from_clinic") or "?"
            st.checkbox(
                f"Move {amt} credit from {frm} → {to}",
                key=f"closeout_group_{i}_{j}_{frm}_{to}",
            )
