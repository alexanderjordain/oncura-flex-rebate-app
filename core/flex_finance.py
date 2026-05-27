"""FLEX finance-company remittance -> SaasAnt imports.

Produces up to three imports from one remittance (SOP-6 upload order: scan invoices, then flex
payments, then scan payments). The original remittance columns are preserved and the SaasAnt
columns appended, so each import is human-auditable next to its source.

CRITICAL: 'Ref No (Receive Payment No)' must be UNIQUE per row, or SaasAnt collapses all rows
into one payment booked against the first customer. 'Reference No' is a constant label.

Per-company rules:
  GreatAmerica : all flex (Maintenance charges). Ref 'GA-{Payment Invoice Number}', label 'FlexGreat America'.
  OnePlace     : Ref 'OPC{Contract #}', label 'FlexOnePlace'.
  NewLane      : ONE remittance mixes flex + scan. Split by cents -> whole-dollar (.00) = scan
                 package, non-round = flex. Flex Ref 'FlexNewLane - n' / label 'FlexNewLane';
                 scan Ref 'NewLaneScan - n' / label 'NewLaneScan'. Scan needs invoices uploaded
                 BEFORE payments, with matching Invoice numbers.
"""
from __future__ import annotations

import pandas as pd

from .opd_adapter import _coerce_amount

PAYMENT_METHOD = "Wire"
DEPOSIT_TO = "Undeposited Funds"
SCAN_ITEM = "Telemedicine-ScanPackage"
SCAN_CLASS = "03-Telemedicine"
SCAN_TERMS = "SCAN"

COMPANY_META = {
    "GreatAmerica": {"flex_label": "FlexGreat America", "scan_label": None, "bank_feed": "Accounting Services"},
    "OnePlace": {"flex_label": "FlexOnePlace", "scan_label": "FlexOnePlace", "bank_feed": "Origin Bank Midwest"},
    "NewLane": {"flex_label": "FlexNewLane", "scan_label": "NewLaneScan", "bank_feed": "New Lane"},
}

RECEIVE_PAYMENT_COLS = [
    "PaymentDate", "Customer", "Payment Method", "Deposit To Account Name",
    "Ref No (Receive Payment No)", "Amount", "Reference No",
]
SCAN_INVOICE_COLS = [
    "Invoice No", "Customer", "Invoice Date", "Product/Service Description",
    "Product/Service Quantity", "Product/Service Rate", "Product/Service Amount",
    "Product/Service Class", "Terms",
]


def is_whole_dollar(amount) -> bool:
    """True when the amount has no cents (NewLane scan-package signature)."""
    try:
        return round(float(amount) * 100) % 100 == 0
    except (TypeError, ValueError):
        return False


def translate_name(name, name_map: dict):
    """Finance/legal name -> QB payee. Returns (qb_name, found)."""
    m = (name_map or {}).get("map", name_map or {})
    key = str(name).strip()
    if key in m:
        return m[key], True
    return key, False


def make_ref_no(company: str, kind: str, *, invoice_number=None, contract=None, seq=None) -> str:
    if company == "GreatAmerica":
        return f"GA-{invoice_number}"
    if company == "OnePlace":
        return f"OPC{contract}"
    if company == "NewLane":
        return f"NewLaneScan - {seq}" if kind == "scan" else f"FlexNewLane - {seq}"
    return f"{company}-{kind}-{invoice_number or contract or seq}"


def _date_str(d):
    try:
        return d.strftime("%m/%d/%Y")
    except AttributeError:
        return str(d)


def _assert_unique(values, where):
    vals = list(values)
    if len(set(vals)) != len(vals):
        dupes = {v for v in vals if vals.count(v) > 1}
        raise ValueError(f"Non-unique Ref No in {where} (SaasAnt will collapse rows): {sorted(map(str, dupes))[:10]}")


def process_remittance(
    df: pd.DataFrame,
    company: str,
    *,
    customer_col: str,
    amount_col: str,
    id_col: str | None,
    payment_date,
    invoice_date,
    start_invoice_no: int,
    name_map: dict,
    split: str = "by_cents",
):
    """Turn a remittance into SaasAnt imports.

    split: 'by_cents' (NewLane: whole-dollar=scan, else flex), 'all_flex', or 'all_scan'.
    Returns dict: flex_payments, scan_invoices, scan_payments (DataFrames), plus summary + unmapped.
    Original columns are preserved; SaasAnt columns are appended.
    """
    work = df.copy().reset_index(drop=True)
    amounts = work[amount_col].map(_coerce_amount)
    work = work[amounts != 0].reset_index(drop=True)
    amounts = work[amount_col].map(_coerce_amount)

    qb_pairs = [translate_name(n, name_map) for n in work[customer_col]]
    work["_qb_customer"] = [q for q, _ in qb_pairs]
    unmapped = sorted({str(work[customer_col].iloc[i]).strip()
                       for i, (_, found) in enumerate(qb_pairs) if not found})

    if split == "all_flex":
        kinds = ["flex"] * len(work)
    elif split == "all_scan":
        kinds = ["scan"] * len(work)
    else:  # by_cents
        kinds = ["scan" if is_whole_dollar(a) else "flex" for a in amounts]
    work["_kind"] = kinds
    work["_amount"] = amounts.round(2)

    meta = COMPANY_META.get(company, {"flex_label": f"Flex{company}", "scan_label": f"{company}Scan"})
    flex = work[work["_kind"] == "flex"].reset_index(drop=True)
    scan = work[work["_kind"] == "scan"].reset_index(drop=True)

    flex_payments = _build_payments(flex, company, "flex", meta["flex_label"], payment_date, id_col)

    scan_invoices = pd.DataFrame()
    scan_payments = pd.DataFrame()
    if len(scan):
        invoice_nos = list(range(int(start_invoice_no), int(start_invoice_no) + len(scan)))
        scan_invoices = _build_scan_invoices(scan, invoice_nos, invoice_date)
        scan_payments = _build_payments(
            scan, company, "scan", meta["scan_label"], payment_date, id_col, invoice_nos=invoice_nos
        )

    return {
        "flex_payments": flex_payments,
        "scan_invoices": scan_invoices,
        "scan_payments": scan_payments,
        "unmapped": unmapped,
        "summary": {
            "flex_count": len(flex), "scan_count": len(scan),
            "flex_total": round(float(flex["_amount"].sum()), 2) if len(flex) else 0.0,
            "scan_total": round(float(scan["_amount"].sum()), 2) if len(scan) else 0.0,
            "total": round(float(work["_amount"].sum()), 2),
            "next_invoice_no": (int(start_invoice_no) + len(scan)) if len(scan) else int(start_invoice_no),
        },
    }


def _passthrough(src: pd.DataFrame) -> pd.DataFrame:
    return src.drop(columns=[c for c in src.columns if c.startswith("_")]).reset_index(drop=True)


def _build_payments(src, company, kind, label, payment_date, id_col, invoice_nos=None):
    if not len(src):
        return pd.DataFrame()
    out = _passthrough(src)
    refs = []
    for i in range(len(src)):
        ident = src[id_col].iloc[i] if id_col and id_col in src else None
        refs.append(make_ref_no(company, kind, invoice_number=ident, contract=ident, seq=i + 1))
    out["PaymentDate"] = _date_str(payment_date)
    out["Customer"] = src["_qb_customer"].values
    out["Payment Method"] = PAYMENT_METHOD
    out["Deposit To Account Name"] = DEPOSIT_TO
    out["Ref No (Receive Payment No)"] = refs
    out["Amount"] = src["_amount"].values
    out["Reference No"] = label
    if invoice_nos is not None:
        out["Invoice"] = invoice_nos
    _assert_unique(out["Ref No (Receive Payment No)"], f"{company} {kind} payments")
    return out


def _build_scan_invoices(src, invoice_nos, invoice_date):
    out = _passthrough(src)
    out["Invoice No"] = invoice_nos
    out["Customer"] = src["_qb_customer"].values
    out["Invoice Date"] = _date_str(invoice_date)
    out["Product/Service Description"] = SCAN_ITEM
    out["Product/Service Quantity"] = 1
    out["Product/Service Rate"] = src["_amount"].values
    out["Product/Service Amount"] = src["_amount"].values
    out["Product/Service Class"] = SCAN_CLASS
    out["Terms"] = SCAN_TERMS
    _assert_unique(out["Invoice No"], "scan invoices")
    return out
