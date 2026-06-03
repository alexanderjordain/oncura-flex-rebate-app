"""Coverage for opd_adapter.coerce_amount — the universal dollar-amount parser.

Pulled out from the fuzz harness once a real bug was found: a single ZWSP at
the end of an amount string was silently coercing to $0, dropping the row.
Email and Outlook copy-paste are common sources of invisible unicode.
"""
import math

from core.opd_adapter import coerce_amount


def test_clean_float():
    assert coerce_amount(412.37) == 412.37


def test_clean_int():
    assert coerce_amount(412) == 412.0


def test_currency_prefix_and_thousands_separator():
    assert coerce_amount("$1,234.56") == 1234.56


def test_parenthesized_negative():
    assert coerce_amount("(412.37)") == -412.37


def test_minus_prefix_negative():
    assert coerce_amount("-412.37") == -412.37


def test_blank_string_returns_zero():
    assert coerce_amount("") == 0.0


def test_none_returns_zero():
    assert coerce_amount(None) == 0.0


def test_nan_returns_zero():
    assert coerce_amount(float("nan")) == 0.0


def test_garbage_returns_zero():
    assert coerce_amount("garbage") == 0.0


def test_nbsp_padding_handled():
    """Non-breaking space wraps the value (common in Excel cells)."""
    nbsp = "\xa0"
    assert coerce_amount(f"{nbsp}412.37{nbsp}") == 412.37


def test_zwsp_suffix_handled():
    """Zero-width space at end of value (common from email copy-paste).
    Pre-fix, this silently returned 0.0 and the row was dropped downstream."""
    zwsp = "​"
    assert coerce_amount(f"412.37{zwsp}") == 412.37


def test_zwj_zwnj_handled():
    """Zero-width joiner and non-joiner — rare but possible from rich-text paste."""
    assert coerce_amount(f"412.37‌") == 412.37   # ZWNJ
    assert coerce_amount(f"412.37‍") == 412.37   # ZWJ


def test_bom_handled():
    """BOM character riding in on the very first cell of a UTF-8-BOM CSV."""
    assert coerce_amount("﻿412.37") == 412.37


def test_carriage_return_suffix():
    assert coerce_amount("412.37\r") == 412.37


def test_combined_pollution():
    """All the invisible unicode bandits at once."""
    s = f"﻿\xa0$1,234.56​\r"
    assert coerce_amount(s) == 1234.56
