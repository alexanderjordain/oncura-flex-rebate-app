"""FLEX overage billing — Accounting SOP-6 + SOP-12.

Per-overage routing:
  - "partner"            -> finance partner handles the overage on Oncura's behalf
                            (One Place when overage is submitted before their cutoff)
  - "missed_cutoff"      -> partner WOULD handle, but cutoff has passed -> bill directly
  - "direct"             -> partner has opted out (Great America, New Lane) or no partner
                            (Self-Financed) -> bill clinic directly

Direct-bill path produces a SaasAnt INVOICE import; per SOP-6 those invoices MUST be voided
in QBO immediately after sending (revenue was already captured by the OPD invoices). The page
surfaces that step as coaching text.

Pre-existing credit offset (SOP-12): if a clinic already has an unapplied credit balance,
apply that to the overage first; only bill the remainder. The credit balance is operator-
entered (we don't connect to QBO live).

Configurable in data/config.json under flex.overage:
  - finance_partner_cutoff_day (default 5)
  - finance_partner_handles: per-company toggle
  - direct_invoice_item + direct_invoice_memo_template
  - escalation_clinics (e.g. Luv-N-Care)
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from . import saasant

# Routing values
ROUTE_PARTNER = "partner"
ROUTE_MISSED_CUTOFF = "missed_cutoff"
ROUTE_DIRECT = "direct"

DIRECT_INVOICE_COLUMNS = [
    "Invoice No", "Customer", "Invoice Date", "Product/Service",
    "Product/Service Description", "Product/Service Quantity",
    "Product/Service Rate", "Product/Service Amount", "Product/Service Class", "Terms",
]

# Human-readable billing worksheet used in the current operational workflow.
# Tanya bills overages manually (one QBO invoice per clinic, voided after
# sending per SOP-6) — this xlsx is her working document, NOT a SaasAnt import.
# Columns are ordered for readability: identifying info first, then the math
# trail (threshold -> activity -> credits -> net), then the operational hints.
DIRECT_BILLING_WORKSHEET_COLUMNS = [
    "Clinic", "QB Customer", "Finance Company", "Contract #",
    "Quarter", "Quarterly Threshold", "Quarter Activity",
    "Gross Overage", "Pre-existing Credit Applied", "Net Amount to Bill",
    "Suggested QBO Memo", "Route Reason", "Escalation Flag",
]


def _overage_cfg(cfg: dict) -> dict:
    return (cfg.get("flex") or {}).get("overage") or {}


def cutoff_date(recap_year: int, recap_month: int, cutoff_day: int = 5) -> dt.date:
    """Finance partner cutoff is the Nth day of the month AFTER the recap month
    (overages submitted in the following month)."""
    ny, nm = (recap_year + 1, 1) if recap_month == 12 else (recap_year, recap_month + 1)
    return dt.date(ny, nm, cutoff_day)


def route_overage(finance_company: str, recap_year: int, recap_month: int,
                  today: dt.date, cfg: dict) -> str:
    over = _overage_cfg(cfg)
    handles = over.get("finance_partner_handles", {})
    if not handles.get(finance_company, False):
        return ROUTE_DIRECT
    if today > cutoff_date(recap_year, recap_month, int(over.get("finance_partner_cutoff_day", 5))):
        return ROUTE_MISSED_CUTOFF
    return ROUTE_PARTNER


def annotate_overages(overage_rows: list[dict], recap_year: int, recap_month: int,
                      today: dt.date, cfg: dict, credit_offsets: dict | None = None) -> list[dict]:
    """Tag each overage row with: route, credit_applied, net_overage, escalation flag.
    Pure — does not mutate the input rows; returns a new list of dicts."""
    over = _overage_cfg(cfg)
    escalation = {str(x).lower() for x in over.get("escalation_clinics", [])}
    credit_offsets = credit_offsets or {}
    out = []
    for r in overage_rows:
        gross = float(r.get("overage", 0.0) or 0.0)
        if gross <= 0:
            continue
        bucket = r.get("finance_company")
        qb = r.get("qb_name") or r.get("clinic_name") or ""
        credit = float(credit_offsets.get(qb, 0.0) or 0.0)
        applied = min(gross, credit)
        net = round(max(gross - applied, 0.0), 2)
        route = route_overage(bucket, recap_year, recap_month, today, cfg)
        name_l = (r.get("clinic_name") or "").lower()
        flagged = any(esc in name_l for esc in escalation)
        out.append({
            **r,
            "route": route,
            "credit_applied": round(applied, 2),
            "net_overage": net,
            "escalation_flag": flagged,
        })
    return out


def build_direct_invoice_import(annotated_rows: list[dict], recap_year: int, recap_month: int,
                                 start_ref: int, sales_class: str, cfg: dict):
    """SaasAnt invoice import for direct-bill overages (and missed-cutoff). Each row a QBO
    invoice to be VOIDED immediately after sending (SOP-6). Returns (DataFrame, next_ref)."""
    over = _overage_cfg(cfg)
    item = over.get("direct_invoice_item", "Telemedicine Overage")
    memo_template = over.get("direct_invoice_memo_template", "Telemedicine Overages — {quarter}")
    quarter_label = f"{dt.date(recap_year, recap_month, 1).strftime('%b %Y')} quarter"
    memo = memo_template.format(quarter=quarter_label)

    direct = [r for r in annotated_rows
              if r["route"] in (ROUTE_DIRECT, ROUTE_MISSED_CUTOFF) and float(r["net_overage"]) > 0]
    refs = saasant.sequential_refs(start_ref, len(direct))
    inv_date = saasant.last_day_of_month(recap_year, recap_month).strftime("%m/%d/%Y")

    rows = []
    for ref, r in zip(refs, direct):
        amt = round(float(r["net_overage"]), 2)
        rows.append({
            "Invoice No": ref,
            "Customer": r.get("qb_name") or r.get("clinic_name"),
            "Invoice Date": inv_date,
            "Product/Service": item,
            "Product/Service Description": memo,
            "Product/Service Quantity": 1,
            "Product/Service Rate": amt,
            "Product/Service Amount": amt,
            "Product/Service Class": sales_class,
            "Terms": "Flex",
        })
    df = pd.DataFrame(rows, columns=DIRECT_INVOICE_COLUMNS)
    if not df.empty:
        saasant.assert_unique_refs(df["Invoice No"])
    next_ref = (refs[-1] + 1) if refs else start_ref
    return df, next_ref


def build_direct_billing_worksheet(annotated_rows: list[dict], recap_year: int,
                                   recap_month: int, cfg: dict) -> pd.DataFrame:
    """Human-readable per-clinic billing worksheet for the direct-bill flow.

    Tanya bills these overages manually — one QBO invoice per clinic, send an
    Authorize.net payment link or PDF, void each QBO invoice immediately after
    sending (SOP-6: the revenue was already captured by the OPD-side invoice).
    This worksheet is her working reference; it is NOT a SaasAnt import.

    `build_direct_invoice_import()` produces the SaasAnt-shaped version of the
    same data and remains available for the day overages are folded into the
    SaasAnt workflow.
    """
    over = _overage_cfg(cfg)
    memo_template = over.get("direct_invoice_memo_template",
                             "Telemedicine Overages — {quarter}")
    quarter_label = f"{dt.date(recap_year, recap_month, 1).strftime('%b %Y')} quarter"
    memo = memo_template.format(quarter=quarter_label)

    direct = [r for r in annotated_rows
              if r["route"] in (ROUTE_DIRECT, ROUTE_MISSED_CUTOFF)
              and float(r["net_overage"]) > 0]

    rows = []
    for r in direct:
        contract = (
            r.get("contract_oneplace")
            or r.get("contract_newlane")
            or r.get("contract_greatamerica")
        )
        route_reason = (
            "Partner missed cutoff — direct bill"
            if r["route"] == ROUTE_MISSED_CUTOFF
            else f"{r.get('finance_company') or 'No partner'} does not handle overages"
        )
        rows.append({
            "Clinic": r.get("clinic_name") or "",
            "QB Customer": r.get("qb_name") or r.get("clinic_name") or "",
            "Finance Company": r.get("finance_company") or "",
            # Empty string (not None) so DataFrame round-trip doesn't promote to NaN
            # and render as 'nan' in the email body for clinics without a contract.
            "Contract #": contract or "",
            "Quarter": quarter_label,
            "Quarterly Threshold": round(float(r.get("quarterly_threshold", 0.0) or 0.0), 2),
            "Quarter Activity": round(float(r.get("quarter_activity", 0.0) or 0.0), 2),
            "Gross Overage": round(float(r.get("overage", 0.0) or 0.0), 2),
            "Pre-existing Credit Applied": round(float(r.get("credit_applied", 0.0) or 0.0), 2),
            "Net Amount to Bill": round(float(r["net_overage"]), 2),
            "Suggested QBO Memo": memo,
            "Route Reason": route_reason,
            "Escalation Flag": "YES" if r.get("escalation_flag") else "",
        })
    return pd.DataFrame(rows, columns=DIRECT_BILLING_WORKSHEET_COLUMNS)


def build_partner_submission(annotated_rows: list[dict], recap_year: int, recap_month: int) -> pd.DataFrame:
    """List of overages routed to the finance partner (currently only OnePlace handles them).
    Send this before the cutoff day of the following month."""
    quarter_label = f"{dt.date(recap_year, recap_month, 1).strftime('%b %Y')} quarter"
    partner_rows = [r for r in annotated_rows if r["route"] == ROUTE_PARTNER and float(r["net_overage"]) > 0]
    out = []
    for r in partner_rows:
        contract = (
            r.get("contract_oneplace")
            or r.get("contract_newlane")
            or r.get("contract_greatamerica")
        )
        out.append({
            "Finance Partner": r.get("finance_company"),
            "Clinic": r.get("clinic_name"),
            "QB Customer": r.get("qb_name") or r.get("clinic_name"),
            "Contract ID": contract,
            "Quarter": quarter_label,
            "Gross Overage": round(float(r.get("overage", 0.0)), 2),
            "Credit Applied": round(float(r.get("credit_applied", 0.0)), 2),
            "Net Overage to Submit": round(float(r["net_overage"]), 2),
        })
    return pd.DataFrame(out)
