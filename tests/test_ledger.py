"""Ledger fingerprint stability + dedup behavior — protects against silent regressions."""
from __future__ import annotations

import datetime as dt

import pytest

from core import ledger


def test_fingerprint_stable_across_invocations():
    a = ledger.fingerprint("OnePlace", "flex", "OPC40010172988", dt.date(2026, 5, 1), 912.68)
    b = ledger.fingerprint("OnePlace", "flex", "OPC40010172988", dt.date(2026, 5, 1), 912.68)
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_fingerprint_company_case_insensitive():
    a = ledger.fingerprint("OnePlace", "flex", "X", dt.date(2026, 5, 1), 100.00)
    b = ledger.fingerprint("oneplace", "flex", "X", dt.date(2026, 5, 1), 100.00)
    c = ledger.fingerprint("  ONEPLACE  ", "flex", "X", dt.date(2026, 5, 1), 100.00)
    assert a == b == c


def test_fingerprint_changes_on_any_field():
    base = ledger.fingerprint("OnePlace", "flex", "OPC1", dt.date(2026, 5, 1), 100.00)
    assert base != ledger.fingerprint("GreatAmerica", "flex", "OPC1", dt.date(2026, 5, 1), 100.00)
    assert base != ledger.fingerprint("OnePlace", "scan", "OPC1", dt.date(2026, 5, 1), 100.00)
    assert base != ledger.fingerprint("OnePlace", "flex", "OPC2", dt.date(2026, 5, 1), 100.00)
    assert base != ledger.fingerprint("OnePlace", "flex", "OPC1", dt.date(2026, 5, 2), 100.00)
    assert base != ledger.fingerprint("OnePlace", "flex", "OPC1", dt.date(2026, 5, 1), 100.01)


def test_fingerprint_amount_uses_cents_not_float():
    # Avoid floating point drift: 100.00 == 100.000000...01 (within cent)
    a = ledger.fingerprint("OnePlace", "flex", "X", dt.date(2026, 5, 1), 100.00)
    b = ledger.fingerprint("OnePlace", "flex", "X", dt.date(2026, 5, 1), 100.000001)
    assert a == b  # both round to 10000 cents


def test_fingerprint_amount_distinguishes_cents():
    a = ledger.fingerprint("OnePlace", "flex", "X", dt.date(2026, 5, 1), 100.00)
    b = ledger.fingerprint("OnePlace", "flex", "X", dt.date(2026, 5, 1), 100.01)
    assert a != b


def test_file_hash_distinguishes_byte_changes():
    assert ledger.file_hash(b"hello") != ledger.file_hash(b"hello\n")
    assert ledger.file_hash(b"hello") == ledger.file_hash(b"hello")


def test_flex_payments_for_month_filters_kind():
    # Direct test of the in-memory filter — doesn't touch persistence
    data = {
        "files": [],
        "payments": [
            {"fingerprint": "a", "kind": "flex", "payment_date": "2026-05-01", "amount": 100, "company": "OnePlace", "contract": "X", "qb_customer": "C", "recorded_at": ""},
            {"fingerprint": "b", "kind": "scan", "payment_date": "2026-05-01", "amount": 200, "company": "OnePlace", "contract": "Y", "qb_customer": "C", "recorded_at": ""},
            {"fingerprint": "c", "kind": "flex", "payment_date": "2026-04-15", "amount": 300, "company": "OnePlace", "contract": "Z", "qb_customer": "D", "recorded_at": ""},
        ],
    }
    # Monkeypatch load to return this snapshot
    orig_load = ledger.load
    ledger.load = lambda: (data, None)
    try:
        may = ledger.flex_payments_for_month(2026, 5)
        assert len(may) == 1
        assert may[0]["fingerprint"] == "a"  # not the scan, not the april one
        apr = ledger.flex_payments_for_month(2026, 4)
        assert len(apr) == 1
        assert apr[0]["fingerprint"] == "c"
    finally:
        ledger.load = orig_load
