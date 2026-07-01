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


def _norm_contract(value) -> str:
    """Canonicalize a contract id for cross-source matching.

    Roster and ledger store the same OnePlace contract three different ways —
    'OPC40010149681' (roster, OPC-prefixed), '040010149681' (ledger, padded),
    '40010149681' (bare). Comparing them raw never matches, so the inclusion
    gate's contract branch was dead. Strip a trailing Excel float artifact
    ('...988.0'), keep digits only, drop leading zeros -> all three collapse to
    '40010149681'. Returns '' when there are no significant digits.

    NOTE: this does NOT bridge GreatAmerica, whose roster contract is a dashed
    number ('022-1996782-000') while the ledger stores a different bare account
    number — those are distinct identifiers, so the GA gate stays on qb-name.
    """
    s = str(value or "").strip()
    if not s:
        return ""
    if "." in s:                      # Excel coerced the id to a float
        s = s.split(".", 1)[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits.lstrip("0")


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
        contract = _norm_contract(p.get("contract"))
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
            cv = _norm_contract(c.get(k))
            if cv and cv in payments_by_contract:
                return True
    return False


def _group_payment_count(anchor, members, ledger_payments) -> int:
    """Count POSITIVE FLEX payments in the ledger belonging to this clinic or any
    of its group members — matched by qb_name OR normalized contract, each
    payment counted once.

    Drives the recapture payment-band logic: the expected count is 3 per clinic
    (one per month of the quarter), so a single clinic with >3 — or an M-clinic
    group with >3M — has extra payments on the books whose credit the recapture
    must absorb to zero the account.
    """
    qbs: set[str] = set()
    contracts: set[str] = set()
    for c in [anchor] + list(members):
        qb = (c.get("qb_name") or "").strip().lower()
        if qb:
            qbs.add(qb)
        for k in ("contract_oneplace", "contract_greatamerica", "contract_newlane"):
            cv = _norm_contract(c.get(k))
            if cv:
                contracts.add(cv)
    n = 0
    for p in ledger_payments or []:
        if p.get("kind") != "flex":
            continue
        try:
            if float(p.get("amount") or 0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        pqb = (p.get("qb_customer") or "").strip().lower()
        pc = _norm_contract(p.get("contract"))
        if (pqb and pqb in qbs) or (pc and pc in contracts):
            n += 1
    return n


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

        # ── Payment-band recapture ───────────────────────────────────────────
        # The standard "hurdle" pool is the threshold (3 months of entitlement
        # per clinic). When a clinic/group has MORE payments on the books than
        # expected (3 per clinic), those extra payments are real credit sitting
        # on the account, so the pool flexes UP to absorb them — otherwise the
        # recapture under-recognizes and the account can't zero. We deliberately
        # do NOT flex DOWN for fewer-than-expected payments: that's ambiguous (a
        # payment may simply not be imported yet), so it's left to a verified
        # MANUAL adjustment instead, surfaced via the `underfunded` flag.
        clinic_count = 1 + len(members)
        n_expected = 3 * clinic_count
        per_payment = round(threshold / n_expected, 4) if n_expected else 0.0
        n_payments = (
            _group_payment_count(c, members, ledger_payments_for_quarter)
            if payment_filter_active else None
        )
        if payment_filter_active and n_payments is not None and n_payments > n_expected:
            effective_pool = round(n_payments * per_payment, 2)
            pool_basis = "ledger_over"
        else:
            effective_pool = threshold
            pool_basis = "hurdle"
        unused = round(max(effective_pool - activity_val, 0.0), 2)
        overage = round(max(activity_val - effective_pool, 0.0), 2)
        # Books-zeroing unused implied by the ACTUAL posted payment count (what
        # the account needs to net to zero). Equals `unused` for the hurdle/over
        # cases; for under-funded clinics it's the smaller figure the manual
        # adjustment should target.
        if payment_filter_active and n_payments is not None:
            balance_unused = round(max(n_payments * per_payment - activity_val, 0.0), 2)
        else:
            balance_unused = unused
        underfunded = bool(
            payment_filter_active and n_payments is not None
            and 0 < n_payments < n_expected and unused > 0 and not excluded_no_payments
        )
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
                # ── Payment-band fields ──────────────────────────────────────
                # n positive FLEX payments on the books this quarter (None when
                # no ledger supplied); expected count (3 per clinic); the pool
                # actually used and whether it's the flat hurdle or was flexed
                # up to absorb extra payments; the books-zeroing unused; and the
                # under-funded flag (paid fewer than expected -> the posted
                # hurdle invoice needs a verified manual reduction).
                "payments_in_quarter": n_payments,
                "expected_payments": n_expected,
                "effective_pool": round(effective_pool, 2),
                "pool_basis": pool_basis,
                "balance_unused": balance_unused,
                "underfunded": underfunded,
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
            cv = _norm_contract(c.get(k))
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
        contract = _norm_contract(p.get("contract"))
        qb = (p.get("qb_customer") or "").strip().lower()
        if contract and contract in known_contracts:
            continue
        if qb and qb in known_qb:
            continue
        orphans.append(p)
    return orphans


def clinics_with_negative_payments(flex_clinics, ledger_payments) -> list[dict]:
    """Roster clinics that have at least one NEGATIVE (reversal / clawback) FLEX
    payment row in the supplied ledger window.

    A reversal row (amount < 0) is a payment backed out WITHOUT an offsetting
    credit memo, so the paid-vs-expected recapture math (which counts positive
    payments only) can't see it — the account may need a MANUAL ADJUSTMENT.
    Pure: takes the quarter's flex payment rows, returns one dict per affected
    clinic with the summed negative amount, the row count, and the best contract
    id resolved from the roster. Match by normalized contract first, then
    qb_name / clinic_name (lowercased). Rows that resolve to no roster clinic are
    still reported under their raw qb_customer / contract so nothing is silently
    dropped.
    """
    by_contract, by_name = {}, {}
    for c in flex_clinics:
        for k in ("contract_oneplace", "contract_greatamerica", "contract_newlane"):
            cv = _norm_contract(c.get(k))
            if cv:
                by_contract[cv] = c
        for nm in (c.get("qb_name"), c.get("clinic_name")):
            if nm:
                by_name[str(nm).strip().lower()] = c

    agg: dict = {}
    for p in ledger_payments or []:
        if p.get("kind") != "flex":
            continue
        try:
            amt = float(p.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if amt >= 0:
            continue
        pc = _norm_contract(p.get("contract"))
        pqb = (p.get("qb_customer") or "").strip().lower()
        clinic = (by_contract.get(pc) if pc else None) or by_name.get(pqb)
        if clinic is not None:
            name = clinic.get("qb_name") or clinic.get("clinic_name")
            contract = (clinic.get("contract_number")
                        or clinic.get("contract_greatamerica")
                        or clinic.get("contract_oneplace")
                        or clinic.get("contract_newlane"))
            key = (name or "").strip().lower()
        else:
            name = p.get("qb_customer") or p.get("contract") or "(unknown)"
            contract = p.get("contract")
            key = str(name).strip().lower()
        rec = agg.setdefault(key, {"clinic": name, "contract": contract,
                                   "reversal_total": 0.0, "reversal_count": 0})
        rec["reversal_total"] = round(rec["reversal_total"] + amt, 2)
        rec["reversal_count"] += 1
    return sorted(agg.values(), key=lambda r: (r["clinic"] or "").lower())


def group_calendar_mismatches(flex_clinics) -> list[dict]:
    """Detect multi-clinic groups whose members don't all share the anchor's
    calendar_spread.

    compute_recapture pools every member's threshold AND activity onto the
    anchor row, gated on the ANCHOR's quarter-end month only. That is correct
    only when the whole group closes on one calendar. A member on a different
    calendar_spread is (a) swept into the anchor's quarter even though its own
    quarter hasn't closed — inflating the anchor's pooled threshold/activity —
    and (b) never emitted in its OWN quarter-end month, because children are
    never emitted as independent rows. Either way the numbers are wrong.

    Returns one dict per offending group: {anchor, anchor_spread, members:[...]}
    listing only the members whose spread differs. The Stage 3 review surfaces
    this so the operator resolves it (align the roster calendars, or split the
    off-calendar clinics out of the group) before committing.
    """
    members_by_anchor, _ = _build_group_index(flex_clinics)
    out = []
    for c in flex_clinics:
        if c.get("parent_clinic_id"):
            continue
        anchor_key = (c.get("clinic_name") or "").strip().lower()
        members = members_by_anchor.get(anchor_key, [])
        if not members:
            continue
        anchor_spread = c.get("calendar_spread") or None
        off = [
            {"clinic_name": m.get("clinic_name"),
             "calendar_spread": m.get("calendar_spread")}
            for m in members
            if (m.get("calendar_spread") or None) != anchor_spread
        ]
        if off:
            out.append({
                "anchor": c.get("clinic_name"),
                "anchor_spread": c.get("calendar_spread"),
                "members": off,
            })
    return out


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
