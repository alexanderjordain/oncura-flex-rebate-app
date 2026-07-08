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


def group_overage_spread(recap_rows: list[dict]) -> list[dict]:
    """Propose how to SPREAD (smooth) a multi-clinic FLEX group's overages
    across its own members using each member's unused credit (SOP-13/14).

    Contractual rule (Tanya meetings, esp. the PR-vets group): an overage is a
    GROUP obligation, not the bill of the single clinic that happened to exceed
    its own threshold. Within a `group_id`, an over-utilizing member (overage>0)
    is covered first by the unused credit sitting on its under-utilizing siblings
    (unused>0) before anything is billed. Only clinics that share a `group_id`
    pool together; independent clinics (no `group_id`) are never touched.

    Algorithm (per group):
      1. donors    = members with unused>0, sorted by qb_name (deterministic).
      2. recipients = members with overage>0, sorted by qb_name.
      3. Greedily walk recipients; for each, pull credit from donors in order,
         moving min(remaining_donor_unused, remaining_recipient_overage) at each
         step until the recipient is covered or donors are exhausted.
      4. Any recipient overage still uncovered after donors run dry is GROUP-level
         real overage that must still be billed — surfaced as a residual row.

    Returns an audit-friendly, deterministic list of move dicts. Two shapes,
    both sharing the same keys:

      inter-member credit move
        {"group": group_id, "from_clinic": donor_qb_name,
         "to_clinic": recipient_qb_name, "amount": round(x, 2),
         "reason": "spread group overage per contract"}

      residual (group's total overage exceeded its total unused)
        {"group": group_id, "from_clinic": None,
         "to_clinic": recipient_qb_name, "amount": round(residual, 2),
         "reason": "residual group overage to bill"}
        One residual row per still-uncovered recipient (in qb_name order), so
        the unmet need is attributed to the specific clinics that generated it
        and the residual amounts sum to (total overage - total unused) for the
        group. Groups whose unused fully covers overage produce no residual row.

    This is a PROPOSED spread for operator review — an audit trail of who would
    fund whom — NOT an automatic QBO write. Rows with no `group_id` are ignored.
    Amounts are rounded to cents and the output order is deterministic (groups
    by group_id, then recipients/donors by qb_name).
    """
    # Bucket rows by group_id, ignoring independents (no group_id).
    groups: dict = {}
    for r in recap_rows or []:
        gid = r.get("group_id")
        if not gid:
            continue
        groups.setdefault(gid, []).append(r)

    def _qb(row) -> str:
        return (row.get("qb_name") or row.get("clinic_name") or "")

    moves: list[dict] = []
    for gid in sorted(groups.keys(), key=lambda g: str(g)):
        members = groups[gid]
        donors = sorted(
            (m for m in members if float(m.get("unused") or 0.0) > 0),
            key=lambda m: _qb(m).lower(),
        )
        recipients = sorted(
            (m for m in members if float(m.get("overage") or 0.0) > 0),
            key=lambda m: _qb(m).lower(),
        )
        # Mutable remaining balances keyed by identity (allows dup qb_names safely).
        donor_left = {id(d): round(float(d.get("unused") or 0.0), 2) for d in donors}
        di = 0
        for rec in recipients:
            need = round(float(rec.get("overage") or 0.0), 2)
            rec_qb = _qb(rec)
            # Pull from donors in deterministic order until covered or dry.
            while need > 0 and di < len(donors):
                donor = donors[di]
                avail = donor_left[id(donor)]
                if avail <= 0:
                    di += 1
                    continue
                amt = round(min(avail, need), 2)
                if amt > 0:
                    moves.append({
                        "group": gid,
                        "from_clinic": _qb(donor),
                        "to_clinic": rec_qb,
                        "amount": amt,
                        "reason": "spread group overage per contract",
                    })
                donor_left[id(donor)] = round(avail - amt, 2)
                need = round(need - amt, 2)
                if donor_left[id(donor)] <= 0:
                    di += 1
            # Whatever this recipient still needs is real group overage to bill.
            if need > 0:
                moves.append({
                    "group": gid,
                    "from_clinic": None,
                    "to_clinic": rec_qb,
                    "amount": round(need, 2),
                    "reason": "residual group overage to bill",
                })
    return moves
