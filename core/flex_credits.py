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


def build_import(flex_clinics: list[dict], year: int, month: int, start_ref: int):
    """Return (DataFrame, next_ref). One credit-memo row per active clinic with a credit > 0."""
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
                "Credit Memo No": ref,
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
