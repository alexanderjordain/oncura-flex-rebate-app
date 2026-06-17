"""Finance-co remittance classification — prefix + cents split rules."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from core import flex_finance


# ── OnePlace: classify by contract prefix (Cash SOP-9) ───────────────────────

def test_oneplace_flex_contract_prefix_04():
    assert flex_finance.is_oneplace_flex_contract("04001234567")
    assert flex_finance.is_oneplace_flex_contract("04123")
    # Padded form sometimes seen in the export
    assert flex_finance.is_oneplace_flex_contract("00400123456")


def test_oneplace_non_flex_prefixes_are_scan():
    assert not flex_finance.is_oneplace_flex_contract("12345")
    assert not flex_finance.is_oneplace_flex_contract("99999")
    assert not flex_finance.is_oneplace_flex_contract("05000")
    assert not flex_finance.is_oneplace_flex_contract("40010172988")  # starts with 4, not 04


def test_oneplace_handles_float_artifact():
    """Excel sometimes coerces contract IDs to float ('40010172988.0')."""
    assert flex_finance.is_oneplace_flex_contract("04010172988.0")
    assert flex_finance.is_oneplace_flex_contract("004010172988.00")


def test_oneplace_handles_whitespace_and_none():
    assert flex_finance.is_oneplace_flex_contract("  04001234  ")
    assert not flex_finance.is_oneplace_flex_contract(None)
    assert not flex_finance.is_oneplace_flex_contract("")


def test_oneplace_classification_independent_of_cents():
    """The regression we just fixed: a whole-dollar flex payment must still classify as flex."""
    # Whole-dollar amount on a flex contract -> flex
    assert flex_finance.is_oneplace_flex_contract("04001234567")
    # Fractional-cents amount on a scan contract -> scan (it's the contract that matters)
    assert not flex_finance.is_oneplace_flex_contract("33333")


# ── NewLane: classify by cents (Cash SOP-10) ─────────────────────────────────

def test_is_whole_dollar_true_cases():
    assert flex_finance.is_whole_dollar(595)
    assert flex_finance.is_whole_dollar(595.00)
    assert flex_finance.is_whole_dollar("595")
    assert flex_finance.is_whole_dollar(0)


def test_is_whole_dollar_false_cases():
    assert not flex_finance.is_whole_dollar(912.68)
    assert not flex_finance.is_whole_dollar(595.01)
    assert not flex_finance.is_whole_dollar("912.68")


def test_is_whole_dollar_handles_bad_input():
    assert not flex_finance.is_whole_dollar(None)
    assert not flex_finance.is_whole_dollar("abc")


# ── FP Leasing: Ref No is the bare invoice #, NO prefix ──────────────────────
# Accounting requires the SaasAnt Ref No to match the remittance's Invoice #
# column exactly — no 'FPL-' (or any) prefix.

def test_fpleasing_ref_no_has_no_prefix():
    ref = flex_finance.make_ref_no("FPLeasing", "scan", invoice_number="EQ42901")
    assert ref == "EQ42901"
    assert "FPL" not in ref


def test_fpleasing_ref_no_strips_float_artifact():
    # A purely numeric invoice # read from xlsx may arrive as 42901.0
    assert flex_finance.make_ref_no("FPLeasing", "scan", invoice_number=42901.0) == "42901"


def test_fpleasing_ref_no_falls_back_to_seq_when_invoice_missing():
    ref = flex_finance.make_ref_no("FPLeasing", "scan", invoice_number=None, seq=3)
    assert ref == "3"
    assert "FPL" not in ref


def test_fpleasing_remittance_ref_matches_invoice_column_no_prefix():
    df = pd.DataFrame({
        "Customer Name": ["Abell Animal Hospital", "Banfield Pet Hospital"],
        "Due to Oncura": [100.00, 250.50],
        "Invoice #": ["EQ42901", "EQM43234"],
    })
    out = flex_finance.process_remittance(
        df, "FPLeasing",
        customer_col="Customer Name",
        amount_col="Due to Oncura",
        id_col="Invoice #",
        payment_date=dt.date(2026, 6, 9),
        invoice_date=dt.date(2026, 6, 9),
        start_invoice_no=50088,
        name_map={},
        split="all_scan",
    )
    # Scan-only: no flex payments, one scan invoice + one payment per row.
    assert out["flex_payments"].empty
    inv = out["scan_invoices"]
    pay = out["scan_payments"]
    assert len(inv) == 2 and len(pay) == 2

    # Generated Invoice No is the sequential number from the UI start, no prefix.
    assert list(inv["Invoice No"]) == [50088, 50089]
    assert list(pay["Invoice No"]) == [50088, 50089]

    # Ref No is the bare FP Leasing invoice #, equal to the passthrough Invoice #
    # column, with no FPL- prefix anywhere.
    refs = list(pay["Ref No (Receive Payment No)"])
    assert refs == ["EQ42901", "EQM43234"]
    assert list(pay["Invoice #"]) == refs
    assert not any(str(r).startswith("FPL") for r in refs)
