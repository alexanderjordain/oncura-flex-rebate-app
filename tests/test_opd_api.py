"""Tests for core.opd_api — Atom parsing, DST-aware billing-date projection,
and the components-reconciliation formula. No live network calls — XML fixtures
are inlined.
"""
from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET

import pytest

from core import opd_api


# ── _coerce_decimal ──────────────────────────────────────────────────────────
def test_coerce_decimal_none_and_blank():
    assert opd_api._coerce_decimal(None) == 0.0
    assert opd_api._coerce_decimal("") == 0.0


def test_coerce_decimal_int_and_float_strings():
    assert opd_api._coerce_decimal("1480") == 1480.0
    assert opd_api._coerce_decimal("2985.50") == 2985.5
    assert opd_api._coerce_decimal("0.01") == pytest.approx(0.01)


def test_coerce_decimal_garbage_returns_zero():
    assert opd_api._coerce_decimal("garbage") == 0.0


# ── _parse_dt ────────────────────────────────────────────────────────────────
def test_parse_dt_z_suffix():
    assert opd_api._parse_dt("2026-06-01T04:00:03Z") == dt.datetime(2026, 6, 1, 4, 0, 3)


def test_parse_dt_with_fractional_seconds():
    # .939 fractional should be stripped (fromisoformat varies across Python versions)
    assert opd_api._parse_dt("2026-06-01T04:00:03.939Z") == dt.datetime(2026, 6, 1, 4, 0, 3)


def test_parse_dt_none_and_blank():
    assert opd_api._parse_dt(None) is None
    assert opd_api._parse_dt("") is None


# ── _utc_to_billing_date — the DST-aware projection that matches OPD UI ──────
def test_billing_date_march_rollover_edt():
    """Mar 31 billing -> Mendix writes at midnight EDT = 04:00 UTC on Apr 1.
    The OPD UI labels this 'Mar 31'."""
    utc = dt.datetime(2026, 4, 1, 4, 0, 7)
    assert opd_api._utc_to_billing_date(utc) == dt.date(2026, 3, 31)


def test_billing_date_may_rollover_edt():
    utc = dt.datetime(2026, 6, 1, 4, 0, 3)
    assert opd_api._utc_to_billing_date(utc) == dt.date(2026, 5, 31)


def test_billing_date_february_rollover_est():
    """Feb 28 billing -> midnight EST = 05:00 UTC on Mar 1.
    Pre-DST timestamps need to backshift to Feb 28 too."""
    utc = dt.datetime(2026, 3, 1, 5, 0, 5)
    assert opd_api._utc_to_billing_date(utc) == dt.date(2026, 2, 28)


def test_billing_date_mid_month_no_backshift():
    """Mid-month manual invoices keep their own local date."""
    utc = dt.datetime(2026, 4, 15, 18, 0, 0)  # 2pm EDT
    assert opd_api._utc_to_billing_date(utc) == dt.date(2026, 4, 15)


def test_billing_date_late_night_no_backshift():
    """An invoice created at 11:30pm EDT on Apr 14 (UTC 03:30 Apr 15) is still
    Apr 14 locally — but NOT a rollover boundary (day != 1)."""
    utc = dt.datetime(2026, 4, 15, 3, 30, 0)
    assert opd_api._utc_to_billing_date(utc) == dt.date(2026, 4, 14)


def test_billing_date_returns_none_for_none():
    assert opd_api._utc_to_billing_date(None) is None


# ── _parse_atom_entries ──────────────────────────────────────────────────────
_ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
  <m:count>2</m:count>
  <link rel="self" href="Invoices"/>
  <entry>
    <id>https://telehealth.oncurapartners.com/odata/Consults/Invoices(11258999068426241)</id>
    <content type="application/xml">
      <m:properties>
        <d:InvoiceDate>2026-04-01T04:00:07Z</d:InvoiceDate>
        <d:InvoiceStatus>Paid</d:InvoiceStatus>
        <d:SubtotalPrice>2985.00</d:SubtotalPrice>
        <d:Credit>0.00</d:Credit>
        <d:OldCredit>1900.00</d:OldCredit>
        <d:MiscCredit>1900.00</d:MiscCredit>
        <d:AdminFee>4.00</d:AdminFee>
        <d:TotalPrice>1089.00</d:TotalPrice>
        <d:ConsultCount>25</d:ConsultCount>
        <d:Invoice_Clinic>19703248371795003</d:Invoice_Clinic>
      </m:properties>
    </content>
  </entry>
  <entry>
    <id>https://telehealth.oncurapartners.com/odata/Consults/Invoices(11258999068426242)</id>
    <content type="application/xml">
      <m:properties>
        <d:InvoiceDate>2026-03-01T05:00:00Z</d:InvoiceDate>
        <d:InvoiceStatus>Paid</d:InvoiceStatus>
        <d:SubtotalPrice>1480.00</d:SubtotalPrice>
        <d:Credit>0.00</d:Credit>
        <d:OldCredit>0.00</d:OldCredit>
        <d:MiscCredit>0.00</d:MiscCredit>
        <d:AdminFee>4.00</d:AdminFee>
        <d:TotalPrice>1484.00</d:TotalPrice>
        <d:ConsultCount>10</d:ConsultCount>
        <d:Invoice_Clinic>19703248371795003</d:Invoice_Clinic>
      </m:properties>
    </content>
  </entry>
</feed>
"""


def test_parse_atom_entries_extracts_all_rows():
    rows = opd_api._parse_atom_entries(_ATOM_SAMPLE)
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["InvoiceStatus"] == "Paid"
    assert r0["SubtotalPrice"] == "2985.00"
    assert r0["TotalPrice"] == "1089.00"
    assert r0["Invoice_Clinic"] == "19703248371795003"
    assert "Invoices(11258999068426241)" in r0["_entry_id"]


def test_parse_atom_entries_empty_feed():
    empty = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"/>'
    assert opd_api._parse_atom_entries(empty) == []


def test_inlinecount_extracts_total():
    assert opd_api._inlinecount(_ATOM_SAMPLE) == 2


def test_inlinecount_returns_none_when_absent():
    no_count = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"/>'
    assert opd_api._inlinecount(no_count) is None


# ── Components-match formula: Sub − Credit − max(OldCredit, MiscCredit) + AdminFee
def test_components_match_simple_no_credit():
    # Mar invoice from Abell: no credit applied, just $4 admin
    sub, cr, oc, mc, ad, tot = 1480.0, 0.0, 0.0, 0.0, 4.0, 1484.0
    delta = round(tot - (sub - cr - max(oc, mc) + ad), 2)
    assert abs(delta) < 1.00


def test_components_match_with_doubled_credit_columns():
    # Apr invoice from Abell: Mendix wrote $1900 to BOTH OldCredit and MiscCredit.
    # Using max() (not sum) is what reconciles to TotalPrice.
    sub, cr, oc, mc, ad, tot = 2985.0, 0.0, 1900.0, 1900.0, 4.0, 1089.0
    delta = round(tot - (sub - cr - max(oc, mc) + ad), 2)
    assert abs(delta) < 1.00


def test_components_match_with_asymmetric_credit_columns():
    # Real Santa Clara row: OldCredit 2608.62, MiscCredit 2810.00 — the LARGER
    # of the two is authoritative.
    sub, cr, oc, mc, ad, tot = 3440.0, 0.0, 2608.62, 2810.0, 0.0, 630.0
    delta = round(tot - (sub - cr - max(oc, mc) + ad), 2)
    assert abs(delta) < 1.00


def test_components_match_fails_on_void_shell():
    # Empty void rows (Sub=0, Total=0, only $4 admin) — formula computes 4, total 0.
    # These are the ~3% known-mismatch tail; the gap is exactly the AdminFee.
    sub, cr, oc, mc, ad, tot = 0.0, 0.0, 0.0, 0.0, 4.0, 0.0
    delta = round(tot - (sub - cr - max(oc, mc) + ad), 2)
    assert delta == -4.0


# ── INVOICE_COLUMNS schema stability ────────────────────────────────────────
# ── Namespace drift guard ──────────────────────────────────────────────────
def test_namespace_validation_accepts_real_atom_feed():
    """The canonical Mendix Atom shape must pass validation."""
    opd_api._parse_atom_entries(_ATOM_SAMPLE)  # no raise


def test_namespace_validation_rejects_unknown_root():
    """If Mendix changes the namespace URI in a major upgrade, findall returns
    zero entries and Stage 3 would silently bill every clinic the full
    threshold as unused. Loud failure is much safer than silent zero."""
    drifted = _ATOM_SAMPLE.replace(
        'http://www.w3.org/2005/Atom',
        'http://example.com/some-future-namespace',
    )
    with pytest.raises(RuntimeError, match="Unexpected OData response root"):
        opd_api._parse_atom_entries(drifted)


def test_namespace_validation_rejects_non_atom_response():
    """A response that isn't an Atom feed at all (e.g. an HTML error page from
    a misconfigured Mendix proxy) should fail loudly, not return zero rows."""
    html_error = '<html><body>Service Unavailable</body></html>'
    with pytest.raises((RuntimeError, ET.ParseError)):
        opd_api._parse_atom_entries(html_error)


# ── Orphan invoice tracking ──────────────────────────────────────────────────
def _df_from(rows):
    import pandas as pd
    return pd.DataFrame(rows, columns=opd_api.INVOICE_COLUMNS)


def test_split_activity_no_orphans():
    df = _df_from([
        {"clinic_name": "Abell", "invoice_clinic_fk": 1, "total_price": 100.0},
        {"clinic_name": "Beta",  "invoice_clinic_fk": 2, "total_price": 200.0},
    ])
    activity, _, orphans = opd_api._split_activity_and_orphans(df)
    assert activity == {"abell": 100.0, "beta": 200.0}
    assert orphans == {"count": 0, "total": 0.0, "fk_list": []}


def test_split_activity_with_orphans_surfaces_them():
    """Rows whose Invoice_Clinic FK didn't resolve to a clinic_name should be
    EXCLUDED from the activity dict (otherwise the unknown clinic key would
    confuse downstream matching) AND reported in the orphans summary."""
    df = _df_from([
        {"clinic_name": "Abell", "invoice_clinic_fk": 1, "total_price": 100.0},
        {"clinic_name": None,    "invoice_clinic_fk": 999, "total_price": 500.0},
        {"clinic_name": None,    "invoice_clinic_fk": 888, "total_price": 50.0},
        {"clinic_name": None,    "invoice_clinic_fk": 999, "total_price": 25.0},  # same FK twice
    ])
    activity, _, orphans = opd_api._split_activity_and_orphans(df)
    assert activity == {"abell": 100.0}
    assert orphans["count"] == 3
    assert orphans["total"] == 575.0
    assert orphans["fk_list"] == [888, 999]  # deduped + sorted


def test_split_activity_empty_input():
    import pandas as pd
    df = pd.DataFrame(columns=opd_api.INVOICE_COLUMNS)
    activity, _, orphans = opd_api._split_activity_and_orphans(df)
    assert activity == {}
    assert orphans == {"count": 0, "total": 0.0, "fk_list": []}


# ── INVOICE_COLUMNS schema stability ────────────────────────────────────────
def test_invoice_columns_includes_all_critical_fields():
    """If columns are renamed or removed, downstream code breaks silently —
    this test pins the schema."""
    required = {
        "invoice_internal_id", "clinic_name",
        "invoice_date_utc", "invoice_date_local",
        "status", "subtotal", "credit", "old_credit", "misc_credit",
        "admin_fee", "total_price", "consult_count",
        "components_match", "components_delta",
    }
    assert required.issubset(set(opd_api.INVOICE_COLUMNS))
