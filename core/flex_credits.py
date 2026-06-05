"""FLEX monthly credit-memo import generator (SaasAnt -> QBO Credit Memos).

Columns + values mirror 'Flex Credits Import.xlsx' exactly (verified against FlexApril2026).
Item is 'Flex-credits', Class is '03-Telemedicine', each row a unique sequential Credit Memo No.
The FLEX program is CLOSED to new entrants: the active list only shrinks. This generator just
copies the master's active, credit-bearing clinics for the month.
"""
from __future__ import annotations

from . import saasant

ITEM = "Flex-credits"
CLASS = "03-Telemedicine"

COLUMNS = [
    "Credit Memo No",
    "Customer",
    "Credit Memo Date",
    "Product/Service",
    "Product/Service Description",
    "Product/Service Quantity",
    "Product/Service Rate",
    "Product/Service Amount",
    "Product/Service Class",
]

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def _clinic_lookup(flex_clinics):
    """Returns (by_qb_lower, by_contract) — maps to look up an eligible clinic from a ledger row."""
    by_qb, by_contract = {}, {}
    for c in flex_clinics:
        if not c.get("active") or not (c.get("monthly_credit") or 0) > 0:
            continue
        qbn = (c.get("qb_name") or "").strip().lower()
        if qbn:
            by_qb[qbn] = c
        cn = (c.get("clinic_name") or "").strip().lower()
        if cn and cn != qbn:
            by_qb.setdefault(cn, c)
        for k in ("contract_oneplace", "contract_greatamerica", "contract_newlane"):
            cv = c.get(k)
            if cv:
                by_contract[str(cv).strip()] = c
    return by_qb, by_contract


def build_import_from_payments(flex_clinics, payments, year, month, start_ref):
    """Payment-driven credit-memo import. One credit memo per ledger payment row.

    A clinic that received THREE payments in the target month gets THREE credit memos at
    its monthly_credit each — the quarter-end true-up (SOP-11) absorbs the over-credit if any
    of those payments were for a future month.

    Returns (DataFrame, next_ref, skipped, source_payments) where source_payments[i] is
    the ledger payment dict that produced df row i — used by Stage 2 to fingerprint each
    emitted credit memo on the SOURCE payment (stable) instead of the mutable QB customer
    name (renames silently broke dedup).
    """
    import pandas as pd

    by_qb, by_contract = _clinic_lookup(flex_clinics)
    date = saasant.last_day_of_month(year, month)
    desc = f"Flex Credits for {_MONTHS[month - 1]} {year}"

    matched, skipped = [], []
    for p in payments:
        qbn = (p.get("qb_customer") or "").strip().lower()
        contract = str(p.get("contract") or "").strip()
        clinic = by_qb.get(qbn) or by_contract.get(contract)
        if clinic is None:
            # Fallback: emit a credit memo against the ledger's qb_customer using the
            # payment amount itself. Keeps SOP-10's 'one Flex payment in, one credit
            # out' invariant even when flex_master is out of date. Still tracked in
            # `skipped` for audit purposes — just not dropped from the export.
            skipped.append({
                "reason": "no flex_master match (qb_customer / contract)",
                "qb_customer": p.get("qb_customer"),
                "contract": contract,
                "amount": p.get("amount"),
            })
            matched.append((None, p, round(float(p.get("amount") or 0), 2)))
            continue
        amt = round(float(clinic["monthly_credit"]), 2)
        matched.append((clinic, p, amt))

    # Sort for stable output. Unmatched rows (clinic=None) sort by their payment's
    # qb_customer so they group consistently with adjacent batches.
    def _sort_key(t):
        clinic, payment, _ = t
        name = (clinic.get("qb_name") if clinic else payment.get("qb_customer")) or ""
        return (name.lower(), payment.get("payment_date", ""))
    matched.sort(key=_sort_key)

    refs = saasant.sequential_refs(start_ref, len(matched))
    rows = []
    source_payments = []
    for ref, (clinic, payment, amt) in zip(refs, matched):
        if clinic is not None:
            customer = clinic.get("qb_name") or clinic.get("clinic_name")
        else:
            customer = payment.get("qb_customer") or "(unmatched)"
        rows.append({
            # SaasAnt-side Credit Memo No: CR-prefixed so QBO ties imported memos
            # to a 'CR' series distinct from invoice numbers. start_ref keeps
            # advancing as an integer so the next batch picks up seamlessly.
            "Credit Memo No": f"CR{ref}",
            "Customer": customer,
            "Credit Memo Date": date.strftime("%m/%d/%Y"),
            "Product/Service": ITEM,
            "Product/Service Description": desc,
            "Product/Service Quantity": 1,
            "Product/Service Rate": amt,
            "Product/Service Amount": amt,
            "Product/Service Class": CLASS,
        })
        source_payments.append(payment)
    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        saasant.assert_unique_refs(df["Credit Memo No"])
    next_ref = (refs[-1] + 1) if refs else start_ref
    return df, next_ref, skipped, source_payments


def build_import(flex_clinics: list[dict], year: int, month: int, start_ref: int):
    """Legacy: one credit memo per active clinic with monthly_credit > 0 (no payment check).

    Use only when the processed-payments ledger is empty for the target month (bootstrap).
    Returns (DataFrame, next_ref).
    """
    import pandas as pd

    eligible = [
        c for c in flex_clinics
        if c.get("active") and (c.get("monthly_credit") or 0) > 0
    ]
    eligible.sort(key=lambda c: (c.get("qb_name") or c.get("clinic_name") or "").lower())

    refs = saasant.sequential_refs(start_ref, len(eligible))
    date = saasant.last_day_of_month(year, month)
    desc = f"Flex Credits for {_MONTHS[month - 1]} {year}"

    rows = []
    for ref, c in zip(refs, eligible):
        amt = round(float(c["monthly_credit"]), 2)
        rows.append(
            {
                "Credit Memo No": f"CR{ref}",
                "Customer": c.get("qb_name") or c.get("clinic_name"),
                "Credit Memo Date": date.strftime("%m/%d/%Y"),
                "Product/Service": ITEM,
                "Product/Service Description": desc,
                "Product/Service Quantity": 1,
                "Product/Service Rate": amt,
                "Product/Service Amount": amt,
                "Product/Service Class": CLASS,
            }
        )
    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        saasant.assert_unique_refs(df["Credit Memo No"])
    next_ref = (refs[-1] + 1) if refs else start_ref
    return df, next_ref
