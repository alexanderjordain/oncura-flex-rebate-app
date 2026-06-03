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

from .opd_adapter import coerce_amount

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


def guess_columns(company: str, cols) -> dict:
    """Best-effort {customer, amount, id} column names for a remittance.
    Candidate-priority matching so specific names win over loose substrings
    (e.g. 'Customer Name' over 'Contract Vendor Customer Number'; 'PTB Received'
    over 'Payments Received' which is a count, not dollars)."""
    low = [str(c).lower() for c in cols]

    def pick(cands, default=0):
        for k in cands:
            for i, c in enumerate(low):
                if k in c:
                    return i
        return default

    customer = pick(["customer_name", "customer name", "customer"])
    amount = pick(["payment_amount", "ptb received", "ptb", "paid", "amount"])
    if company == "GreatAmerica":
        ident = pick(["payment invoice", "invoice"])
    else:
        ident = pick(["contract_id", "contract #", "contract"])
    return {"customer": cols[customer], "amount": cols[amount], "id": cols[ident]}


def is_whole_dollar(amount) -> bool:
    """True when the amount has no cents (NewLane scan-package signature)."""
    try:
        return round(float(amount) * 100) % 100 == 0
    except (TypeError, ValueError):
        return False


def is_oneplace_flex_contract(contract) -> bool:
    """Per Cash SOP-9: OnePlace flex contracts begin with '04' (5 digits) — anything else
    on a OnePlace remittance is a scan package. The export sometimes pads with a leading
    zero ('004...'), so accept either form."""
    s = str(contract or "").strip()
    # Strip a trailing '.0' float artifact ('40010172988.0' -> '40010172988')
    if "." in s:
        head, _, tail = s.partition(".")
        if tail.strip("0") == "":
            s = head
    return s.startswith("04") or s.startswith("004")


def translate_name(name, name_map: dict):
    """Finance/legal name -> QB payee. Returns (qb_name, found).

    Match is case-insensitive AND whitespace-collapsed so a stored mapping for
    'ABC Animal Hospital, LLC' still hits when the remittance prints it as
    'Abc Animal Hospital,  LLC' (extra space) or all-caps. Other lookups in
    the app (flex_credits._clinic_lookup, flex_unused.match_activity) already
    normalize this way — translate_name is the historical odd one out.
    """
    m = (name_map or {}).get("map", name_map or {})
    raw = str(name).strip()
    if raw in m:
        return m[raw], True
    # Build a case-folded / whitespace-collapsed index on the fly.
    norm = " ".join(raw.casefold().split())
    for k, v in m.items():
        if " ".join(str(k).casefold().split()) == norm:
            return v, True
    return raw, False


def normalize_contract(c) -> str:
    """Strip a float artifact like '40010172988.00' -> '40010172988' while preserving
    leading-zero string contracts ('000000018333')."""
    s = str(c).strip()
    if "." in s:
        head, _, tail = s.partition(".")
        if tail.strip("0") == "":
            s = head
    return s


def make_ref_no(company: str, kind: str, *, invoice_number=None, contract=None, seq=None) -> str:
    if company == "GreatAmerica":
        return f"GA-{invoice_number}"
    if company == "OnePlace":
        c = normalize_contract(contract)
        # flex contracts are padded with a leading zero in the export -> strip for flex;
        # scan contracts' leading zeros are significant -> keep (matches the SaasAnt templates)
        if kind == "flex":
            c = c.lstrip("0") or c
        return f"OPC{c}"
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
    work = df.copy()
    # drop summary/total rows (e.g. trailing "Pass-Thru received" line): a real payment always
    # has a contract/invoice id, so a blank id_col is the reliable signal.
    if id_col and id_col in work.columns:
        work = work[work[id_col].notna()]
        work = work[work[id_col].astype(str).str.replace("\xa0", " ").str.strip().ne("")]
    work = work[work[customer_col].notna()]
    work = work.reset_index(drop=True)
    amounts = work[amount_col].map(coerce_amount)
    work = work[amounts != 0].reset_index(drop=True)
    amounts = work[amount_col].map(coerce_amount)

    qb_pairs = [translate_name(n, name_map) for n in work[customer_col]]
    work["_qb_customer"] = [q for q, _ in qb_pairs]
    unmapped = sorted({str(work[customer_col].iloc[i]).strip()
                       for i, (_, found) in enumerate(qb_pairs) if not found})

    if split == "all_flex":
        kinds = ["flex"] * len(work)
    elif split == "all_scan":
        kinds = ["scan"] * len(work)
    elif split == "by_contract_prefix_oneplace":
        # Per Cash SOP-9: OnePlace classifies by contract prefix, not by cents.
        # by_cents misfires when a FLEX payment happens to be a whole-dollar amount.
        contract_vals = list(work[id_col]) if id_col and id_col in work else [None] * len(work)
        kinds = ["flex" if is_oneplace_flex_contract(c) else "scan" for c in contract_vals]
    else:  # by_cents (NewLane, fallback)
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
    n = len(src)

    bases, refs, seen = [], [], {}
    for i in range(n):
        ident = src[id_col].iloc[i] if id_col and id_col in src else None
        base = make_ref_no(company, kind, invoice_number=ident, contract=ident, seq=i + 1)
        bases.append(base)
        # a clinic paying twice in one remittance would collide; suffix to keep SaasAnt-unique
        if base in seen:
            seen[base] += 1
            refs.append(f"{base}-{seen[base]}")
        else:
            seen[base] = 1
            refs.append(base)

    customer = src["_qb_customer"].values
    amount = src["_amount"].values

    if company == "OnePlace":
        out["OPDAdd"] = bases
        out["Customer"] = customer
        out["Payment Method"] = PAYMENT_METHOD
        out["Deposit To Account Name"] = DEPOSIT_TO
        out["Ref No (Receive Payment No)"] = refs
        out["Amount"] = amount
        out["Reference No"] = label
        out["PaymentDate"] = _date_str(payment_date)
        out["Invoice No"] = invoice_nos if invoice_nos is not None else ["" for _ in range(n)]
    else:
        out["PaymentDate"] = _date_str(payment_date)
        out["Customer"] = customer
        out["Payment Method"] = PAYMENT_METHOD
        out["Deposit To Account Name"] = DEPOSIT_TO
        out["Ref No (Receive Payment No)"] = refs
        out["Amount"] = amount
        out["Reference No"] = label
        if invoice_nos is not None:
            out["Invoice No"] = invoice_nos

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
