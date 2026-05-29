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
FUZZY_THRESHOLD = 88

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
    """Look up a clinic's quarterly activity by exact then fuzzy name."""
    for k in (clinic_rec.get("qb_name"), clinic_rec.get("clinic_name")):
        if k and str(k).strip().lower() in activity_by_name:
            return activity_by_name[str(k).strip().lower()], "exact"
    if _HAVE_FUZZ and activity_by_name:
        name = (clinic_rec.get("qb_name") or clinic_rec.get("clinic_name") or "").lower()
        hit = process.extractOne(name, list(activity_by_name.keys()), scorer=fuzz.token_sort_ratio)
        if hit and hit[1] >= FUZZY_THRESHOLD:
            return activity_by_name[hit[0]], "fuzzy"
    return None, "none"


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


def compute_recapture(flex_clinics, activity_by_name, year, month):
    """Per-clinic unused/overage for clinics whose quarter ends in (year, month).

    Multi-clinic groups (Mohnacky, River Trail, PR-vets) are pooled at the anchor:
    member activity rolls up, member thresholds sum, child clinics are not emitted as
    independent rows. Recapture / overage invoice (downstream) is emitted on the anchor.

    activity_by_name: {clinic_name_lower: total OPD activity over the quarter}.
    """
    members_by_anchor, member_to_anchor = _build_group_index(flex_clinics)
    pooled_activity = _pool_activity_by_group(activity_by_name, member_to_anchor)

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

        activity, q = match_activity(c, pooled_activity)
        activity_val = float(activity) if activity is not None else None
        unused = overage = None
        if activity_val is not None:
            unused = round(max(threshold - activity_val, 0.0), 2)
            overage = round(max(activity_val - threshold, 0.0), 2)
        rows.append(
            {
                "clinic_name": c.get("clinic_name"),
                "qb_name": c.get("qb_name"),
                "finance_company": c.get("finance_company"),
                "calendar_spread": spread,
                "quarterly_threshold": round(threshold, 2),
                "quarter_activity": (round(activity_val, 2) if activity_val is not None else None),
                "unused": unused,
                "overage": overage,
                "activity_match": q,
                # contract IDs needed for overage routing / partner submission (SOP-6, SOP-12)
                "contract_greatamerica": c.get("contract_greatamerica"),
                "contract_oneplace": c.get("contract_oneplace"),
                "contract_newlane": c.get("contract_newlane"),
                # multi-clinic group context (pooled rows show how many locations rolled up)
                "group_id": c.get("group_id"),
                "group_member_count": (1 + len(members)) if members else None,
            }
        )
    return rows


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
