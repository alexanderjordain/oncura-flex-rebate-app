"""Tests for the accounting-handoff email builders.

Focus: the body contains the operational truth (no SaasAnt mention for the
direct-bill flow since Tanya bills overages manually today) and surfaces the
per-clinic detail (threshold + activity + credit + net owed) inline so totals
are visible without opening the attachment.
"""
from __future__ import annotations

import datetime as dt

from core import accounting_handoff


# ── direct_bill_overage_email ────────────────────────────────────────────────


def _sample_direct_details():
    """Shape matches flex_overage.build_direct_billing_worksheet().to_dict('records')."""
    return [
        {
            "Clinic": "Galloway Village Veterinary",
            "QB Customer": "Galloway Village Veterinary",
            "Finance Company": "GreatAmerica",
            "Contract #": "GA-1234",
            "Quarter": "Mar 2026 quarter",
            "Quarterly Threshold": 5700.0,
            "Quarter Activity": 9200.0,
            "Gross Overage": 3500.0,
            "Pre-existing Credit Applied": 500.0,
            "Net Amount to Bill": 3000.0,
            "Suggested QBO Memo": "Telemedicine Overages — Mar 2026 quarter",
            "Route Reason": "GreatAmerica does not handle overages",
            "Escalation Flag": "",
        },
    ]


def test_direct_bill_email_no_longer_mentions_saasant():
    """SOP changed: Tanya bills overages manually in QBO; SaasAnt step removed."""
    _, body = accounting_handoff.direct_bill_overage_email(
        year=2026, month=5, invoice_count=1, invoice_total=3000.0,
        clinic_details=_sample_direct_details(),
    )
    assert "SaasAnt" not in body
    assert "Bulk Upload" not in body


def test_direct_bill_email_omits_work_order():
    """Work-order instructions were removed — Tanya runs the billing per her
    own SOP. The email surfaces the data only. Asserting the absence prevents
    well-meaning future edits from re-introducing the procedural reminder."""
    _, body = accounting_handoff.direct_bill_overage_email(
        year=2026, month=5, invoice_count=1, invoice_total=3000.0,
        clinic_details=_sample_direct_details(),
    )
    lower = body.lower()
    assert "authorize.net" not in lower
    assert "void" not in lower
    assert "sop-6" not in lower
    assert "sop-12" not in lower
    assert "work order" not in lower


def test_direct_bill_email_renders_per_clinic_detail():
    """Threshold + activity + credit + net owed must appear inline per clinic
    so Tanya can scan totals without opening the attachment."""
    _, body = accounting_handoff.direct_bill_overage_email(
        year=2026, month=5, invoice_count=1, invoice_total=3000.0,
        clinic_details=_sample_direct_details(),
    )
    assert "Galloway Village Veterinary" in body
    assert "GA-1234" in body
    assert "$5,700.00" in body         # threshold
    assert "$9,200.00" in body         # quarter activity
    assert "$500.00" in body           # credit applied
    assert "$3,000.00" in body         # NET to bill
    assert "AMOUNT TO BILL" in body    # the line that highlights what she owes


def test_direct_bill_email_marks_escalation_clinics():
    details = _sample_direct_details()
    details[0]["Escalation Flag"] = "YES"
    _, body = accounting_handoff.direct_bill_overage_email(
        year=2026, month=5, invoice_count=1, invoice_total=3000.0,
        clinic_details=details,
    )
    assert "ESCALATION" in body


def test_direct_bill_email_works_without_details_back_compat():
    """Earlier call sites that didn't pass clinic_details must still produce a
    valid email (no per-clinic block, just the headline + work order)."""
    subj, body = accounting_handoff.direct_bill_overage_email(
        year=2026, month=5, invoice_count=3, invoice_total=4200.0,
    )
    assert "May 2026" in subj
    assert "$4,200.00" in body
    assert "Galloway" not in body  # didn't fabricate detail


# ── partner_submission_email ─────────────────────────────────────────────────


def _sample_partner_details():
    """Shape matches flex_overage.build_partner_submission().to_dict('records')."""
    return [
        {
            "Finance Partner": "OnePlace",
            "Clinic": "Crossroads Animal Hospital TX",
            "QB Customer": "Crossroads Animal Hospital TX",
            "Contract ID": "OPC-40010147500",
            "Quarter": "Mar 2026 quarter",
            "Gross Overage": 5792.0,
            "Credit Applied": 0.0,
            "Net Overage to Submit": 5792.0,
        },
    ]


def test_partner_email_renders_per_clinic_detail():
    _, body = accounting_handoff.partner_submission_email(
        year=2026, month=5, clinic_count=1, total=5792.0,
        cutoff_date=dt.date(2026, 6, 5),
        clinic_details=_sample_partner_details(),
    )
    assert "Crossroads Animal Hospital TX" in body
    assert "OPC-40010147500" in body
    assert "$5,792.00" in body
    assert "NET TO SUBMIT" in body


def test_partner_email_preserves_cutoff_warning():
    _, body = accounting_handoff.partner_submission_email(
        year=2026, month=5, clinic_count=1, total=5792.0,
        cutoff_date=dt.date(2026, 6, 5),
        clinic_details=_sample_partner_details(),
    )
    assert "June 05, 2026" in body
    assert "BEFORE" in body


# ── .eml structure: signature-placement fix ──────────────────────────────────


def test_eml_body_is_multipart_with_html_part():
    """The .eml must include an HTML body so Outlook places the user's
    auto-inserted signature at the end of the body instead of welding it to
    'Hi Tanya,' at the top. Tested by checking the .eml bytes contain both
    text/plain and text/html parts."""
    eml = accounting_handoff._build_eml_bytes(
        "Subject", "Hi Tanya,\n\nLine two.", "to@example.com", attachments=None,
    )
    assert b"multipart/alternative" in eml
    assert b"text/plain" in eml
    assert b"text/html" in eml


def test_eml_html_has_wordsection_wrapper():
    """The HTML body uses Outlook's WordSection1 wrapper — that's the structure
    that signals 'this is a real composed message, place the signature at the
    natural end.' If this regresses, Outlook reverts to gluing the signature
    to the top of the body."""
    eml = accounting_handoff._build_eml_bytes(
        "Subject", "Hi Tanya,", "to@example.com", attachments=None,
    )
    # decoded for human readability — quoted-printable in raw .eml encodes special chars
    assert b"WordSection1" in eml


def test_body_to_html_preserves_indented_bullet_lines():
    """Per-clinic detail uses leading-space indentation (6 spaces for the
    threshold/credit lines). HTML collapses whitespace by default; we convert
    leading spaces to &nbsp; to preserve indentation."""
    html = accounting_handoff._body_to_html(
        "  - Riverlin Animal Hospital\n"
        "      Threshold: $5,700.00",
    )
    # 2-space and 6-space indents both preserved
    assert "&nbsp;&nbsp;- Riverlin Animal Hospital" in html
    assert "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Threshold:" in html


def test_body_to_html_escapes_special_characters():
    """A clinic name with '&' or '<' must not break the HTML structure."""
    html = accounting_handoff._body_to_html("Smith & Jones <vet>")
    assert "Smith &amp; Jones &lt;vet&gt;" in html
