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


def test_oneplace_scan_package_is_not_labeled_flex():
    # Reported bug: a OnePlace scan-package row (whole-dollar) was tagged
    # "FlexOnePlace" in the Reference No because scan_label == flex_label.
    # Scan packages must carry a scan label and get an invoice number; flex rows
    # keep the flex label.
    df = pd.DataFrame({
        "Customer": ["Risius & Associates Veterinary Service", "Flex Clinic"],
        "Amount": [395.00, 912.68],          # whole-dollar = scan, cents = flex
        "Contract": ["000000020405", "040010172988"],
    })
    out = flex_finance.process_remittance(
        df, "OnePlace",
        customer_col="Customer", amount_col="Amount", id_col="Contract",
        payment_date=dt.date(2026, 5, 5), invoice_date=dt.date(2026, 5, 5),
        start_invoice_no=50000, name_map={}, split="by_cents",
    )
    scan, flex = out["scan_payments"], out["flex_payments"]
    assert len(scan) == 1 and len(flex) == 1

    # Scan package: scan label (NOT flex), and it gets a generated invoice number.
    assert list(scan["Reference No"]) == ["OnePlaceScan"]
    assert "Flex" not in scan["Reference No"].iloc[0]
    assert scan["Invoice No"].iloc[0] == 50000
    assert scan["Ref No (Receive Payment No)"].iloc[0] == "OPC000000020405"
    assert not out["scan_invoices"].empty

    # Flex row still carries the flex label.
    assert list(flex["Reference No"]) == ["FlexOnePlace"]


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


# ── FP Leasing: strip the alphabetic invoice-# prefix; keep the FPL- ref prefix ─
# The invoice number itself (Invoice # column AND inside the Ref No) must be the
# bare numeric value: EQ42901 -> 42901. The Ref No keeps its 'FPL-' system
# prefix, so the Ref No reads 'FPL-42901'.

def test_strip_invoice_prefix_drops_alpha_prefix():
    assert flex_finance.strip_invoice_prefix("EQ42901") == "42901"
    assert flex_finance.strip_invoice_prefix("EQM43234") == "43234"
    assert flex_finance.strip_invoice_prefix("43612") == "43612"  # already bare
    assert flex_finance.strip_invoice_prefix(42901.0) == "42901"  # xlsx float read
    assert flex_finance.strip_invoice_prefix(None) == ""
    assert flex_finance.strip_invoice_prefix("") == ""


def test_fpleasing_ref_keeps_fpl_prefix_strips_alpha_invoice_prefix():
    assert flex_finance.make_ref_no("FPLeasing", "scan", invoice_number="EQ42901") == "FPL-42901"
    assert flex_finance.make_ref_no("FPLeasing", "scan", invoice_number="EQM43234") == "FPL-43234"


def test_fpleasing_ref_no_falls_back_to_seq_when_invoice_missing():
    assert flex_finance.make_ref_no("FPLeasing", "scan", invoice_number=None, seq=3) == "FPL-3"


def test_multi_remittance_companies():
    # GreatAmerica sends multiple remittances per month (month-overlap is
    # expected, not a re-upload signal). The single-remittance partners must NOT
    # be in this set, or their genuine re-upload warning would be downgraded.
    assert "GreatAmerica" in flex_finance.MULTI_REMITTANCE_COMPANIES
    for c in ("OnePlace", "NewLane", "FPLeasing"):
        assert c not in flex_finance.MULTI_REMITTANCE_COMPANIES


def test_bank_feed_labels_match_qbo():
    # Must match the QBO bank-feed strings exactly so the operator can reconcile.
    assert flex_finance.COMPANY_META["FPLeasing"]["bank_feed"] == "Fp Leasing Group"
    assert flex_finance.COMPANY_META["GreatAmerica"]["bank_feed"] == "Account Services"


def test_fpleasing_dedup_key_recovers_invoice_number_from_ref():
    # Stage 1 dedup keys FP Leasing on its own invoice #, recovered from the
    # Ref No (FPL-<n>) via strip_invoice_prefix. Lock that derivation.
    assert flex_finance.strip_invoice_prefix("FPL-42901") == "42901"
    assert flex_finance.strip_invoice_prefix("FPL-43234") == "43234"


def test_fpleasing_remittance_single_invoice_column():
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

    # PAYMENT: exactly one invoice-number column — the remittance's own
    # 'Invoice #' header, now carrying the GENERATED SaasAnt invoice number.
    # No duplicate 'Invoice No' column.
    assert list(pay["Invoice #"]) == [50088, 50089]
    assert "Invoice No" not in pay.columns

    # INVOICE: single invoice-number column under the canonical 'Invoice No';
    # FP's own Invoice # passthrough is dropped.
    assert list(inv["Invoice No"]) == [50088, 50089]
    assert "Invoice #" not in inv.columns

    # FP's own invoice # is preserved only in the Ref No, prefix-free + FPL-.
    refs = list(pay["Ref No (Receive Payment No)"])
    assert refs == ["FPL-42901", "FPL-43234"]
