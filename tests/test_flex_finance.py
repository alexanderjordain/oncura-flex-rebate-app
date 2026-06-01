"""Finance-co remittance classification — prefix + cents split rules."""
from __future__ import annotations

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
