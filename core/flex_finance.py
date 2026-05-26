"""FLEX finance-company remittance -> SaasAnt import generators.

Three import types (SOP-6 order matters: scan invoices must exist before scan payments):
  1. Scan-package INVOICES   (Terms 'SCAN', item 'Telemedicine-ScanPackage')
  2. Flex RECEIVE PAYMENTS    (intentionally unapplied; reconcile at quarter end)
  3. Scan-package RECEIVE PAYMENTS (apply to the invoices from step 1)

CRITICAL: 'Ref No (Receive Payment No)' must be UNIQUE per row or SaasAnt collapses all rows
into one payment booked against the first customer. The visible 'Reference No' is a constant
label ('FlexGreat America' / 'FlexOnePlace' / 'FlexNewLane') and is NOT the unique key.

Per-company unique Ref No format (verified against Flex Master List tabs):
  GreatAmerica -> 'GA-{Payment Invoice Number}'
  OnePlace     -> 'OPC{Contract #}'
  NewLane      -> 'FlexNewLane - {n}'  (sequential)
"""
from __future__ import annotations

from . import saasant

PAYMENT_METHOD = "Wire"
DEPOSIT_TO = "Undeposited Funds"
SCAN_ITEM = "Telemedicine-ScanPackage"
SCAN_CLASS = "03-Telemedicine"
SCAN_TERMS = "SCAN"

RECEIVE_PAYMENT_COLUMNS = [
    "Customer",
    "Payment Method",
    "Deposit To Account Name",
    "Ref No (Receive Payment No)",
    "Amount",
    "Reference No",
    "Payment Date",
]

SCAN_INVOICE_COLUMNS = [
    "Invoice No",
    "Customer",
    "Invoice Date",
    "Product/Service Description",
    "Product/Service Quantity",
    "Product/Service Rate",
    "Product/Service Amount",
    "Product/Service Class",
    "Terms",
]

COMPANY_META = {
    "GreatAmerica": {"label": "FlexGreat America", "bank_feed": "Accounting Services"},
    "OnePlace": {"label": "FlexOnePlace", "bank_feed": "Origin Bank Midwest"},
    "NewLane": {"label": "FlexNewLane", "bank_feed": "New Lane"},
}


def make_ref_no(company: str, *, invoice_number=None, contract=None, seq=None) -> str:
    if company == "GreatAmerica":
        return f"GA-{invoice_number}"
    if company == "OnePlace":
        return f"OPC{contract}"
    if company == "NewLane":
        return f"FlexNewLane - {seq}"
    # generic fallback: must still be unique
    return f"{company}-{invoice_number or contract or seq}"


def classify_contract(contract, prefix="04", digit_length=5) -> str:
    """Documented default rule: 5-digit contract starting '04' = flex, other = scan.
    Company formats vary (OnePlace/NewLane use longer IDs); when the contract doesn't fit the
    5-digit shape this returns 'unknown' and the operator assigns the split."""
    s = str(contract).strip() if contract is not None else ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == digit_length:
        return "flex" if digits.startswith(prefix) else "scan"
    return "unknown"


def build_receive_payments(rows, company: str, payment_date):
    """rows: list of dicts with keys customer, amount, and one of {invoice_number, contract}.
    Returns (DataFrame, label). Sequential seq is assigned for NewLane-style refs."""
    import pandas as pd

    meta = COMPANY_META.get(company, {"label": f"Flex{company}"})
    label = meta["label"]
    pd_str = _date_str(payment_date)
    out = []
    for i, r in enumerate(rows, start=1):
        ref = make_ref_no(
            company,
            invoice_number=r.get("invoice_number"),
            contract=r.get("contract"),
            seq=i,
        )
        out.append(
            {
                "Customer": r["customer"],
                "Payment Method": PAYMENT_METHOD,
                "Deposit To Account Name": DEPOSIT_TO,
                "Ref No (Receive Payment No)": ref,
                "Amount": round(float(r["amount"]), 2),
                "Reference No": label,
                "Payment Date": pd_str,
            }
        )
    df = pd.DataFrame(out, columns=RECEIVE_PAYMENT_COLUMNS)
    if not df.empty:
        saasant.assert_unique_refs(df["Ref No (Receive Payment No)"])
    return df, label


def build_scan_invoices(rows, invoice_date, start_ref):
    """rows: list of dicts with keys customer, amount. Returns (DataFrame, next_ref)."""
    import pandas as pd

    refs = saasant.sequential_refs(start_ref, len(rows))
    d_str = _date_str(invoice_date)
    out = []
    for ref, r in zip(refs, rows):
        amt = round(float(r["amount"]), 2)
        out.append(
            {
                "Invoice No": ref,
                "Customer": r["customer"],
                "Invoice Date": d_str,
                "Product/Service Description": SCAN_ITEM,
                "Product/Service Quantity": 1,
                "Product/Service Rate": amt,
                "Product/Service Amount": amt,
                "Product/Service Class": SCAN_CLASS,
                "Terms": SCAN_TERMS,
            }
        )
    df = pd.DataFrame(out, columns=SCAN_INVOICE_COLUMNS)
    if not df.empty:
        saasant.assert_unique_refs(df["Invoice No"])
    next_ref = (refs[-1] + 1) if refs else start_ref
    return df, next_ref


def _date_str(d):
    try:
        return d.strftime("%m/%d/%Y")
    except AttributeError:
        return str(d)
