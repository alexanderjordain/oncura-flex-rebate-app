"""FLEX unused-credit recapture + overage calculation.

Runs MONTHLY: each clinic is on one of three staggered quarter calendars; only clinics whose
quarter ENDS in the target month are processed that month.

  Total OPD activity (Subtotal + Admin Fee) over the quarter, vs the clinic's quarterly threshold:
    unused  = max(threshold - activity, 0)   -> recaptured via INVOICE, item 'Unused-Flex-Credits'
    overage = max(activity - threshold, 0)   -> billed separately by Tanya (SOP-5)

Calendar spreads (from FlexMaster):
  'Calendar'          -> quarters end Mar/Jun/Sep/Dec
  'March-April-May'   -> quarters end Feb/May/Aug/Nov
  'May-June-July'     -> quarters end Jan/Apr/Jul/Oct
"""
from __future__ import annotations

import datetime as dt

from . import saasant

try:
    from rapidfuzz import fuzz, process
    _HAVE_FUZZ = True
except ImportError:
    _HAVE_FUZZ = False

UNUSED_ITEM = "Unused-Flex-Credits"
TERMS = "Flex"
# Tightened from 88 -> 92 after Encanto/Chenango false positive (89%): two real,
# distinct flex clinics whose names happen to be phonetically close.
FUZZY_THRESHOLD = 92

_MONTH_NUM = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

UNUSED_INVOICE_COLUMNS = [
    "Invoice No",
    "Customer",
    "Invoice Date",
    "Product/Service",
    "Product/Service Description",
    "Product/Service Quantity",
    "Product/Service Rate",
    "Product/Service Amount",
    "Product/Service Class",
    "Terms",
]


def quarter_end_month(calendar_spread: str | None) -> int | None:
    """The month number a clinic's quarter ends (the anchor). None if unparseable."""
    if not calendar_spread:
        return None
    s = calendar_spread.strip().lower()
    if s == "calendar":
        return 12  # standard calendar quarters end Mar/Jun/Sep/Dec -> anchor 12 works mod 3
    # take the last month token of e.g. "march-april-may"
    tokens = [t.strip() for t in s.replace("--", "-").split("-") if t.strip()]
    for tok in reversed(tokens):
        if tok in _MONTH_NUM:
            return _MONTH_NUM[tok]
    return None


def is_quarter_end(calendar_spread: str | None, month: int) -> bool:
    anchor = quarter_end_month(calendar_spread)
    if anchor is None:
        return False
    return (month - anchor) % 3 == 0


def quarter_window(year: int, end_month: int):
    """(start_date, end_date) for the 3-month quarter ending in end_month of `year`."""
    end = saasant.last_day_of_month(year, end_month)
    # first month = end_month - 2, with year rollover
    fm = end_month - 2
    fy = year
    if fm <= 0:
        fm += 12
        fy -= 1
    start = dt.date(fy, fm, 1)
    return start, end


def _index(flex_clinics):
    idx = {}
    for c in flex_clinics:
        for k in (c.get("clinic_name"), c.get("qb_name")):
            if k:
                idx.setdefault(str(k).strip().lower(), c)
    return idx


def match_activity(clinic_rec, activity_by_name):
    """Look up a clinic's quarterly activity by exact then fuzzy name.

    Returns (amount, quality, matched_opd_name, score).
    matched_opd_name = the key in activity_by_name we ended up using (or None).
    score = fuzzy similarity 0–100 for fuzzy matches, None otherwise.
    """
    for k in (clinic_rec.get("qb_name"), clinic_rec.get("clinic_name")):
        if k and str(k).strip().lower() in activity_by_name:
            key = str(k).strip().lower()
            return activity_by_name[key], "exact", key, None
    if _HAVE_FUZZ and activity_by_name:
        name = (clinic_rec.get("qb_name") or clinic_rec.get("clinic_name") or "").lower()
        hit = process.extractOne(name, list(activity_by_name.keys()), scorer=fuzz.token_sort_ratio)
        if hit and hit[1] >= FUZZY_THRESHOLD:
            return activity_by_name[hit[0]], "fuzzy", hit[0], float(hit[1])
    return None, "none", None, None


def _build_group_index(flex_clinics):
    """Returns (members_by_anchor_lower, member_name_to_anchor_lower).

    A group is identified by parent_clinic_id (members) pointing at an anchor's clinic_name.
    Independent clinics (no group_id) are absent from both maps.
    """
    members_by_anchor = {}
    member_name_to_anchor = {}
    for c in flex_clinics:
        parent = c.get("parent_clinic_id")
        if not parent:
            continue
        a = parent.strip().lower()
        members_by_anchor.setdefault(a, []).append(c)
        for k in (c.get("clinic_name"), c.get("qb_name")):
            if k:
                member_name_to_anchor[k.strip().lower()] = a
    return members_by_anchor, member_name_to_anchor


def _pool_activity_by_group(activity_by_name: dict, member_to_anchor: dict) -> dict:
    """Sum each member clinic's activity into its anchor's bucket. Independent clinics pass through."""
    if not member_to_anchor:
        return activity_by_name
    pooled = {}
    for name, amt in activity_by_name.items():
        anchor = member_to_anchor.get(name)
        key = anchor if anchor else name
        pooled[key] = pooled.get(key, 0.0) + float(amt or 0)
    return pooled


def _index_payments_by_clinic(ledger_payments):
    """Build (contract→bool, qb_lower→bool) indexes of clinics that have at
    least one POSITIVE FLEX payment in the supplied ledger rows.

    Used by compute_recapture's optional payment filter — clawbacks (amount<=0)
    are intentionally excluded so a clinic whose net is negative still counts
    as "had a real payment" if any single row was positive (handles a clinic
    that paid in months 1-2 then got clawed back in month 3).
    """
    by_contract: set[str] = set()
    by_qb: set[str] = set()
    for p in ledger_payments or []:
        if p.get("kind") != "flex":
            continue
        try:
            if float(p.get("amount") or 0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        contract = str(p.get("contract") or "").strip()
        qb = (p.get("qb_customer") or "").strip().lower()
        if contract:
            by_contract.add(contract)
        if qb:
            by_qb.add(qb)
    return by_contract, by_qb


def _group_has_positive_payment(anchor, members, payments_by_contract, payments_by_qb):
    """Multi-clinic-group-aware payment check: True if the anchor OR any
    member has a positive FLEX payment in the indexed set. In practice the
    finance company typically wires under the anchor's contract, but member
    contracts exist too (River Trail Memorial, etc.) — checking both ensures
    we don't falsely exclude a whole group because the wires happen to be
    booked against a child.
    """
    for c in [anchor] + list(members):
        qb = (c.get("qb_name") or "").strip().lower()
        if qb and qb in payments_by_qb:
            return True
        for k in ("contract_oneplace", "contract_greatamerica", "contract_newlane"):
            cv = (c.get(k) or "").strip()
            if cv and cv in payments_by_contract:
                return True
    return False


def compute_recapture(flex_clinics, activity_by_name, year, month,
                     ledger_payments_for_quarter=None):
    """Per-clinic unused/overage for clinics whose quarter ends in (year, month).

    Multi-clinic groups (Mohnacky, River Trail, PR-vets) are pooled at the anchor:
    member activity rolls up, member thresholds sum, child clinics are not emitted as
    independent rows. Recapture / overage invoice (downstream) is emitted on the anchor.

    activity_by_name: {clinic_name_lower: total OPD activity over the quarter}.

    ledger_payments_for_quarter: optional list of ledger payment dicts (from
        `ledger.flex_payments_in_window(start, end)`) for the closing quarter.
        When provided, each returned row gets an `excluded_no_payments` flag
        set True when NEITHER the anchor NOR any group member has a positive
        FLEX payment in the supplied ledger window. The UI uses the flag to
        warn about roster entries that look stale (clinic left the program
        but `active=true` was never updated). Rows are NOT removed — caller
        decides whether to drop them or display them in a "review" bucket.
        When None: flag is set to False on every row (backward-compatible).
    """
    members_by_anchor, member_to_anchor = _build_group_index(flex_clinics)
    pooled_activity = _pool_activity_by_group(activity_by_name, member_to_anchor)
    payments_by_contract, payments_by_qb = _index_payments_by_clinic(
        ledger_payments_for_quarter,
    )
    payment_filter_active = ledger_payments_for_quarter is not None

    rows = []
    for c in flex_clinics:
        if not c.get("active"):
            continue
        # Children are aggregated into their anchor — don't emit a row for them.
        if c.get("parent_clinic_id"):
            continue
        spread = c.get("calendar_spread")
        if not is_quarter_end(spread, month):
            continue
        # Pool threshold: anchor's own + all member thresholds (for groups; identity for independents).
        anchor_key = (c.get("clinic_name") or "").strip().lower()
        threshold = float(c.get("quarterly_threshold") or 0.0)
        members = members_by_anchor.get(anchor_key, [])
        for m in members:
            threshold += float(m.get("quarterly_threshold") or 0.0)
        threshold = round(threshold, 2)

        # Ledger-aware inclusion flag. Only set meaningfully when the caller
        # passed payments; otherwise stays False (back-compat: prior behavior
        # was to include every active+quarter-end clinic unconditionally).
        excluded_no_payments = False
        if payment_filter_active:
            has_payment = _group_has_positive_payment(
                c, members, payments_by_contract, payments_by_qb,
            )
            excluded_no_payments = not has_payment

        activity, q, matched_opd, fuzzy_score = match_activity(c, pooled_activity)
        # A clinic that's active on the FLEX program and whose quarter ends this
        # month BUT has no OPD activity match still gets a full-threshold
        # unused invoice — they prepaid for the quarter and didn't use any of
        # the credit, so all of it becomes recognized revenue. activity_match
        # 'none' is still surfaced in the review-step warning so the operator
        # can sanity-check that the lack of activity is real and not a name
        # mismatch hiding genuine consults.
        activity_val = float(activity) if activity is not None else 0.0
        unused = round(max(threshold - activity_val, 0.0), 2)
        overage = round(max(activity_val - threshold, 0.0), 2)
        fc = c.get("finance_company")
        contract_number = (
            c.get("contract_greatamerica") if fc == "GreatAmerica"
            else c.get("contract_oneplace") if fc == "OnePlace"
            else c.get("contract_newlane") if fc == "NewLane"
            else None
        )
        rows.append(
            {
                "clinic_name": c.get("clinic_name"),
                "qb_name": c.get("qb_name"),
                "finance_company": c.get("finance_company"),
                "calendar_spread": spread,
                "quarterly_threshold": round(threshold, 2),
                "quarter_activity": round(activity_val, 2),
                "unused": unused,
                "overage": overage,
                "activity_match": q,
                "matched_opd_name": matched_opd,
                "fuzzy_score": fuzzy_score,
                # Consolidated single contract field for display + downstream lookups.
                "contract_number": contract_number,
                # Per-company contract IDs preserved for partner submission lookup.
                "contract_greatamerica": c.get("contract_greatamerica"),
                "contract_oneplace": c.get("contract_oneplace"),
                "contract_newlane": c.get("contract_newlane"),
                # Multi-clinic group context (kept for downstream code; not displayed by default).
                "group_id": c.get("group_id"),
                "group_member_count": (1 + len(members)) if members else None,
                # Ledger-aware filter flag — True when payments were supplied
                # AND neither the anchor nor any group member had a positive
                # FLEX payment in the quarter window. UI surfaces this as a
                # "roster vs ledger mismatch" warning. Always False when no
                # payment filter is active (backward compat).
                "excluded_no_payments": excluded_no_payments,
            }
        )
    return rows


def find_orphan_payments(flex_clinics, ledger_payments) -> list[dict]:
    """Return positive-amount FLEX payments whose contract+qb_customer don't
    resolve to any clinic in flex_master.

    Surfaces the inverse of `excluded_no_payments`: a finance partner is
    wiring us money for a clinic the roster doesn't know about. This is
    almost always a "we forgot to add the clinic" config drift — flag it
    loudly before the cycle commits.
    """
    known_contracts: set[str] = set()
    known_qb: set[str] = set()
    for c in flex_clinics:
        for k in ("contract_oneplace", "contract_greatamerica", "contract_newlane"):
            cv = (c.get(k) or "").strip()
            if cv:
                known_contracts.add(cv)
        qb = (c.get("qb_name") or "").strip().lower()
        if qb:
            known_qb.add(qb)
        cn = (c.get("clinic_name") or "").strip().lower()
        if cn:
            known_qb.add(cn)

    orphans = []
    for p in ledger_payments or []:
        if p.get("kind") != "flex":
            continue
        try:
            amt = float(p.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if amt <= 0:
            # Clawbacks are handled by the Stage 2 non-positive filter — not
            # orphans for the purposes of "do we have config for this clinic".
            continue
        contract = str(p.get("contract") or "").strip()
        qb = (p.get("qb_customer") or "").strip().lower()
        if contract and contract in known_contracts:
            continue
        if qb and qb in known_qb:
            continue
        orphans.append(p)
    return orphans


def build_unused_invoice_import(recapture_rows, year, month, start_ref, sales_class):
    """Invoice import for clinics with unused > 0. Item 'Unused-Flex-Credits', date = quarter end."""
    import pandas as pd

    eligible = [r for r in recapture_rows if (r.get("unused") or 0) > 0]
    eligible.sort(key=lambda r: (r.get("qb_name") or r.get("clinic_name") or "").lower())
    refs = saasant.sequential_refs(start_ref, len(eligible))
    date = saasant.last_day_of_month(year, month)
    win_start, _ = quarter_window(year, month)
    qlabel = f"{win_start.strftime('%b')}-{date.strftime('%b %Y')}"

    rows = []
    for ref, r in zip(refs, eligible):
        amt = round(float(r["unused"]), 2)
        rows.append(
            {
                "Invoice No": ref,
                "Customer": r.get("qb_name") or r.get("clinic_name"),
                "Invoice Date": date.strftime("%m/%d/%Y"),
                "Product/Service": UNUSED_ITEM,
                "Product/Service Description": f"Unused Flex Credits {qlabel}",
                "Product/Service Quantity": 1,
                "Product/Service Rate": amt,
                "Product/Service Amount": amt,
                "Product/Service Class": sales_class,
                "Terms": TERMS,
            }
        )
    df = pd.DataFrame(rows, columns=UNUSED_INVOICE_COLUMNS)
    if not df.empty:
        saasant.assert_unique_refs(df["Invoice No"])
    next_ref = (refs[-1] + 1) if refs else start_ref
    return df, next_ref


def overage_rows(recapture_rows):
    """Clinics that exceeded threshold -> Tanya bills these separately (SOP-5)."""
    return [r for r in recapture_rows if (r.get("overage") or 0) > 0]
