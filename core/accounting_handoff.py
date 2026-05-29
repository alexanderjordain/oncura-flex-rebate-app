"""Per-workflow email drafts to the accounting team.

Each builder returns (subject, body). The page calls `render_handoff(subject, body)` which
shows both a "Open email draft" button (mailto: link, pre-fills the user's mail client) and
a copy-paste fallback (st.code) because long mailto URLs get truncated by some clients.

Canonical reference for the manual steps lives in docs/ACCOUNTING_HANDOFF.md.
"""
from __future__ import annotations

import datetime as dt
from urllib.parse import quote

import streamlit as st

TO = "accounting@oncurapartners.com"


def mailto_link(subject: str, body: str, to: str = TO) -> str:
    return f"mailto:{to}?subject={quote(subject)}&body={quote(body)}"


def render_handoff(subject: str, body: str, key_prefix: str = "handoff"):
    """Render the email-draft button + full text fallback in a bordered card."""
    with st.container(border=True):
        st.markdown("### Hand off to accounting")
        st.caption(f"Sends to **{TO}** with this cycle's numbers + action items pre-filled.")
        st.link_button(
            "Open email draft",
            mailto_link(subject, body),
            type="primary",
        )
        with st.expander("Preview / copy the full email body"):
            st.caption(f"To: {TO}  ·  Subject: {subject}")
            st.code(body, language="text")


# ── Workflow-specific builders ────────────────────────────────────────────────


def finance_payment_email(*, company: str, pay_date, summary: dict, has_scan: bool) -> tuple[str, str]:
    """Build email for Finance Payment Import handoff."""
    subj = f"[Action Required] {company} FLEX Payment Import — {pay_date}"
    parts = [
        f"Hi accounting,",
        "",
        f"Ran the {company} payment import for the {pay_date} remittance.",
        "",
        "Summary:",
        f"  - Flex receive-payments: {summary['flex_count']}  (${summary['flex_total']:,.2f})",
    ]
    if has_scan:
        parts += [
            f"  - Scan-package invoices: {summary['scan_count']}  (${summary['scan_total']:,.2f})",
            f"  - Scan-package receive-payments: {summary['scan_count']}  (${summary['scan_total']:,.2f})",
        ]
    parts += [
        f"  - Combined total: ${summary['total']:,.2f}  (must match the bank-feed deposit)",
        "",
        "Action items (transactions.saasant.com -> Bulk Upload):",
    ]
    if has_scan:
        parts += [
            f"  1. Upload {company}_ScanInvoices_*.xlsx  ->  Invoice",
            f"  2. Upload {company}_FlexPayments_*.xlsx  ->  Received Payments",
            f"  3. Upload {company}_ScanPayments_*.xlsx  ->  Received Payments",
        ]
    else:
        parts += [
            f"  1. Upload {company}_FlexPayments_*.xlsx  ->  Received Payments",
        ]
    parts += [
        "  4. Match the combined upload total to the bank-feed deposit in QBO.",
        "  5. Update OPD credit box for each flex clinic — ADD to existing balance (do NOT replace).",
        "",
        "Verify:",
        "  - QBO Received Payment records match the remittance file line-by-line.",
        "  - Flex payment records are unapplied (intentional; reconcile at quarter-end).",
        "  - One SaaSAnt job at a time — wait for completion before starting the next.",
        "",
        "Per Cash SOP-9 / SOP-10. Reach out if anything looks off.",
        "",
        "Thanks,",
        "Alex",
    ]
    return subj, "\n".join(parts)


def credit_memos_email(*, year: int, month: int, count: int, total: float, start_ref: int, next_ref: int) -> tuple[str, str]:
    month_name = dt.date(year, month, 1).strftime("%B")
    subj = f"[Action Required] FLEX Credit Memos — {month_name} {year}"
    body = "\n".join([
        "Hi accounting,",
        "",
        f"Generated the {month_name} {year} FLEX credit-memo import.",
        "",
        "Summary:",
        f"  - File: FlexCredits_{month_name}_{year}.xlsx",
        f"  - {count} credit memos, ${total:,.2f} total",
        f"  - Credit Memo Nos {start_ref} through {next_ref - 1}",
        "",
        "Action items:",
        "  1. transactions.saasant.com -> Bulk Upload -> Credit Memo -> upload the file.",
        f"  2. Verify in QBO: Flex Credits P&L line should be ${total:,.2f} more negative.",
        "  3. Update OPD credit box for each of the affected clinics:",
        "     - ADD the credit to the existing balance (ACCUMULATE — never replace).",
        "     - Per Accounting SOP-5.",
        "",
        "Watch-outs:",
        "  - Item is Flex-credits (not Unused-Flex-Credits).",
        "  - Every row needs a unique Reference No (already enforced in the file).",
        "",
        "Thanks,",
        "Alex",
    ])
    return subj, body


def recapture_email(
    *,
    year: int,
    month: int,
    unused_total: float,
    unused_count: int,
    direct_total: float,
    direct_count: int,
    partner_total: float,
    partner_count: int,
    cutoff_date: dt.date,
    escalations: list[str],
    group_anchors: list[str],
    unused_recapture_clinics: list[dict] | None = None,
) -> tuple[str, str]:
    """unused_recapture_clinics: [{"clinic": qb_name, "amount": float}, ...] — per-clinic
    list of recapture invoices that need their accumulated customer credit applied in QBO
    after the SaaSAnt import. Listed verbatim in the email so accounting can work down it."""
    month_name = dt.date(year, month, 1).strftime("%B")
    next_month = dt.date(cutoff_date.year, cutoff_date.month, 1).strftime("%B %Y")
    subj = f"[Action Required] FLEX Quarter-End Recapture & Overage — {month_name} {year}"
    lines = [
        "Hi accounting,",
        "",
        f"Ran the FLEX quarter-end recapture for clinics whose quarter ended in {month_name} {year}.",
        "",
        "Summary:",
        f"  - Unused recapture invoices: {unused_count}  (${unused_total:,.2f})",
        f"  - Direct-bill overage invoices: {direct_count}  (${direct_total:,.2f})",
        f"  - OnePlace partner submission: {partner_count}  (${partner_total:,.2f})",
    ]
    if group_anchors:
        lines.append(f"  - Multi-clinic groups pooled at: {', '.join(group_anchors)}")
    lines += [
        "",
        "A. Unused recapture (Accounting SOP-5 + SOP-11)",
        f"  1. SaaSAnt -> Bulk Upload -> Invoice -> UnusedFlex_{month_name}_{year}.xlsx",
        f"  2. Verify QBO P&L: Flex Credits line nets DOWN by ${unused_total:,.2f}.",
        "  3. **Apply each clinic's accumulated customer credit to its new recapture invoice**",
        "     so the account zeros out (the invoice alone doesn't consume the credit balance).",
        "     For each clinic listed below:",
        "       a. Open the customer in QBO.",
        "       b. Receive Payment -> select the Unused-Flex-Credits invoice in 'Outstanding Transactions'.",
        "       c. In the 'Credits' panel, check the unapplied payment(s) + credit memo(s) that",
        "          collectively equal the invoice amount.",
        "       d. Save. The account balance should drop to zero (or close to it).",
        "",
    ]
    # Per-clinic checklist — what the operator needs to work down line-by-line in QBO
    if unused_recapture_clinics:
        lines.append("  Clinics needing credit-application against the new recapture invoices:")
        # Stable sort by clinic name; show amount aligned for readability in monospace clients
        for entry in sorted(unused_recapture_clinics, key=lambda r: (r.get("clinic") or "").lower()):
            cname = entry.get("clinic", "")
            amt = float(entry.get("amount") or 0)
            lines.append(f"    [ ]  {cname}  —  ${amt:,.2f}")
        lines.append("")
    lines += [
        "B. Direct-bill overages (SOP-6) — clinics Oncura bills directly",
        f"  1. SaaSAnt -> Bulk Upload -> Invoice -> OverageDirect_{month_name}_{year}.xlsx",
        "  2. For each invoice: send the clinic an Authorize.net payment link (or QBO PDF).",
        "  3. VOID each QBO invoice IMMEDIATELY after sending (revenue was already captured by OPD;",
        "     leaving them open overstates AR — per SOP-6).",
        "  4. When payment arrives, apply it to zero out the clinic's flex account.",
        "  5. NO refunds policy (SOP-12) — apply overpayments to future overages. Marty must approve exceptions.",
        "",
        "C. Partner submission to OnePlace (SOP-12)",
        f"  1. Send OnePlaceOverage_{month_name}_{year}.xlsx to OnePlace BEFORE {cutoff_date:%B %d, %Y}.",
        "     Missing this cutoff pushes collection 5–6 months out.",
        "  2. Confirm receipt. Track expected payment on FLEX Master.",
        "",
        "D. Per-clinic reconciliation (SOP-11)",
        "  1. Un-apply auto-applied payments for the quarter.",
        "  2. Manually apply payment -> credit -> payment -> credit, month by month, for the 3 months.",
        "  3. Mark OPD invoices 'Paid TW TW'.",
    ]
    if escalations:
        lines += [
            "",
            "Escalation flags (per SOP-12):",
            f"  - {', '.join(escalations)} — communication may need to come from Marty / Accounting Manager.",
        ]
    lines += [
        "",
        "Thanks,",
        "Alex",
    ]
    return subj, "\n".join(lines)


def rebate_email(*, period_label: str, per_bucket_totals: dict, grand_total: float) -> tuple[str, str]:
    subj = f"[FYI / Action] Rebate Cycle Report — {period_label}"
    bucket_lines = []
    for bucket, total in per_bucket_totals.items():
        bucket_lines.append(f"  - {bucket}: ${total:,.2f}")
    body = "\n".join([
        "Hi accounting,",
        "",
        f"Ran the rebate cycle for {period_label}.",
        "",
        "Per-bucket totals:",
        *bucket_lines,
        f"  - Grand total: ${grand_total:,.2f}",
        "",
        f"Report file: Rebates_{period_label.replace(' ', '_')}.xlsx (one tab per finance bucket).",
        "",
        "Action items:",
        "  1. Self-Funded clinics: pay the rebate directly to the clinic.",
        "     (Method TBD — credit memo in QBO or ACH via Bill.com — Jennifer / Marty decision.)",
        "  2. OnePlace Capital clinics: wire-transfer the per-partner total to OnePlace;",
        "     OnePlace applies it to the clinic's financed-balance account.",
        "  3. NewLane Financed clinics: same — wire-transfer the per-partner total to NewLane.",
        "  4. Archive the report xlsx to SharePoint under Rebates/{period}/.",
        "",
        "Watch-outs:",
        "  - When reconciling with a clinic directly, reference OPD invoices only.",
        "    Do NOT expose the finance-company split or the Oncura credit structure to clinics.",
        "  - Finance partners need both legal name and DBA on remittance — already in the report.",
        "",
        "Thanks,",
        "Alex",
    ])
    return subj, body
