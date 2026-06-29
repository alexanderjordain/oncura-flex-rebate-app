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


def test_within_reissue_window():
    # +/- 2 days (a 5-day window) flags; a 5/13 row covers 5/11..5/15, and it
    # spans a month boundary (4/30 vs 5/02). Beyond the window and the next
    # monthly cycle do not.
    assert ledger._within_reissue_window("2026-05-13", "2026-05-11")
    assert ledger._within_reissue_window("2026-05-13", "2026-05-15")
    assert ledger._within_reissue_window("2026-05-13", "2026-05-14")
    assert ledger._within_reissue_window("2026-04-30", "2026-05-02")   # cross-month
    assert not ledger._within_reissue_window("2026-05-13", "2026-05-18")  # 5 days out
    assert not ledger._within_reissue_window("2026-05-13", "2026-06-13")  # next cycle
    assert not ledger._within_reissue_window("", "2026-05-13")            # tolerant of junk


def test_reissue_flags_near_dates_window():
    led = {"files": [], "payments": [
        {"kind": "flex", "payment_date": "2026-05-13", "amount": 921.84,
         "company": "GreatAmerica", "contract": "AAA", "qb_customer": "C", "fingerprint": "1"},
        {"kind": "flex", "payment_date": "2026-04-30", "amount": 870.14,
         "company": "GreatAmerica", "contract": "BBB", "qb_customer": "D", "fingerprint": "2"},
    ]}
    orig = ledger.load
    ledger.load = lambda: (led, None)
    try:
        def inc(contract, amount, date):
            return [{"kind": "flex", "contract": contract, "amount": amount, "payment_date": date}]
        # Same-month near-date (5/11 vs 5/13) -> flags.
        assert ledger.check_possible_reissues("GreatAmerica", inc("AAA", 921.84, "2026-05-11"))
        # Cross-month, within window (5/02 vs 4/30) -> flags (the gap the window fixes).
        assert ledger.check_possible_reissues("GreatAmerica", inc("BBB", 870.14, "2026-05-02"))
        # Next monthly cycle (6/13 vs 5/13) -> does NOT flag.
        assert not ledger.check_possible_reissues("GreatAmerica", inc("AAA", 921.84, "2026-06-13"))
        # Cross-month but beyond the window (5/07 vs 4/30) -> does NOT flag.
        assert not ledger.check_possible_reissues("GreatAmerica", inc("BBB", 870.14, "2026-05-07"))
    finally:
        ledger.load = orig


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


# ── Coverage ("applies-to") month + attribution (coverage + 1) ────────────────

def test_default_applies_to_prior_month_normal():
    # Received early/mid month -> covers the PRIOR month.
    assert ledger.default_applies_to(dt.date(2026, 3, 2)) == "2026-02"
    assert ledger.default_applies_to("2026-03-15") == "2026-02"
    # January received -> prior month is the prior YEAR.
    assert ledger.default_applies_to(dt.date(2026, 1, 5)) == "2025-12"


def test_default_applies_to_is_always_prior_month():
    # Coverage is always the month before the received date — no last-week shift.
    # (Stage 1 defaults the NewLane picker to last calendar month; this helper is
    # only the record_batch safety net.)
    assert ledger.default_applies_to(dt.date(2026, 2, 26)) == "2026-01"
    assert ledger.default_applies_to(dt.date(2026, 2, 2)) == "2026-01"
    assert ledger.default_applies_to("2026-12-03") == "2026-11"


def test_default_applies_to_junk():
    assert ledger.default_applies_to("not-a-date") == ""
    assert ledger.default_applies_to("") == ""


def test_trueup_ym_for_coverage():
    assert ledger.trueup_ym_for_coverage("2026-02") == (2026, 3)
    assert ledger.trueup_ym_for_coverage("2026-12") == (2027, 1)   # year rollover
    assert ledger.trueup_ym_for_coverage("garbage") is None


def test_coverage_month_per_company():
    # NewLane and OnePlace (pass-throughs) cover the prior month; GreatAmerica
    # and FPLeasing cover the received month.
    assert ledger.coverage_month("NewLane", "2026-04-03") == "2026-03"
    assert ledger.coverage_month("OnePlace", "2026-04-07") == "2026-03"   # Pass-Thru March file
    assert ledger.coverage_month("OnePlace", "2026-05-03") == "2026-04"
    assert ledger.coverage_month("GreatAmerica", "2026-05-26") == "2026-05"
    assert ledger.coverage_month("FPLeasing", "2026-06-09") == "2026-06"
    assert ledger.coverage_month("NewLane", "junk") == ""
    # OnePlace is labeled prior-month but still attributed by received date.
    assert ledger.uses_coverage("OnePlace") is False
    assert ledger._attribution_ym({"company": "OnePlace", "payment_date": "2026-04-07"}) == (2026, 4)


def test_attribution_newlane_uses_coverage_others_use_payment_date():
    # NewLane: coverage + 1, regardless of the received date.
    assert ledger._attribution_ym(
        {"company": "NewLane", "applies_to": "2026-02", "payment_date": "2026-02-26"}) == (2026, 3)
    # Non-NewLane: the payment_date month, even if a stray coverage value is present.
    assert ledger._attribution_ym(
        {"company": "OnePlace", "applies_to": "2026-02", "payment_date": "2026-05-03"}) == (2026, 5)
    assert ledger._attribution_ym(
        {"company": "GreatAmerica", "payment_date": "2026-05-26"}) == (2026, 5)


def test_attribution_newlane_without_coverage_uses_payment_date():
    # A NewLane row missing a coverage month falls back to the received month;
    # no last-week shift.
    assert ledger._attribution_ym({"company": "NewLane", "payment_date": "2026-02-26"}) == (2026, 2)
    assert ledger._attribution_ym({"company": "NewLane", "payment_date": "2026-03-02"}) == (2026, 3)
    assert ledger._attribution_ym({"payment_date": "junk"}) is None


def test_flex_payments_grouped_by_attribution_not_received():
    # Two payments for Feb coverage: one received 2/26 (early), one 3/02 (on time).
    # Both must land in the MARCH true-up cycle, not split across Feb/Mar.
    data = {"files": [], "payments": [
        {"fingerprint": "early", "kind": "flex", "payment_date": "2026-02-26",
         "applies_to": "2026-02", "amount": 300, "company": "NewLane", "contract": "A", "qb_customer": "C"},
        {"fingerprint": "ontime", "kind": "flex", "payment_date": "2026-03-02",
         "applies_to": "2026-02", "amount": 300, "company": "NewLane", "contract": "B", "qb_customer": "D"},
    ]}
    orig = ledger.load
    ledger.load = lambda: (data, None)
    try:
        mar = ledger.flex_payments_for_month(2026, 3)
        assert {p["fingerprint"] for p in mar} == {"early", "ontime"}
        assert ledger.flex_payments_for_month(2026, 2) == []   # neither lands in Feb
    finally:
        ledger.load = orig


def test_flex_payments_in_window_uses_attribution():
    # Coverage Feb -> attribution March -> inside a Mar-May quarter window.
    # Coverage Jan -> attribution Feb -> OUTSIDE it (the boundary the fix protects).
    data = {"files": [], "payments": [
        {"fingerprint": "in", "kind": "flex", "payment_date": "2026-02-26",
         "applies_to": "2026-02", "amount": 300, "company": "NewLane", "contract": "A", "qb_customer": "C"},
        {"fingerprint": "out", "kind": "flex", "payment_date": "2026-01-30",
         "applies_to": "2026-01", "amount": 300, "company": "NewLane", "contract": "B", "qb_customer": "D"},
    ]}
    orig = ledger.load
    ledger.load = lambda: (data, None)
    try:
        win = ledger.flex_payments_in_window("2026-03-01", "2026-05-31")
        assert {p["fingerprint"] for p in win} == {"in"}
    finally:
        ledger.load = orig


def test_record_batch_stores_applies_to(monkeypatch):
    saved: dict = {}
    monkeypatch.setattr(ledger, "load", lambda: ({"files": [], "payments": []}, None))
    monkeypatch.setattr(ledger.store, "save_json",
                        lambda path, data, msg, sha=None: (saved.update(data=data) or (True, None)))
    # Explicit applies_to is stored verbatim.
    _, added, _ = ledger.record_batch(
        file_content=None, filename="f.xlsx", company="NewLane",
        payments=[{"kind": "flex", "contract": "A", "qb_customer": "C",
                   "payment_date": dt.date(2026, 3, 2), "amount": 300, "applies_to": "2026-02"}],
    )
    assert added == 1
    assert saved["data"]["payments"][0]["applies_to"] == "2026-02"
    # Omitted applies_to is defaulted from payment_date (3/02 -> covers Feb).
    saved.clear()
    monkeypatch.setattr(ledger, "load", lambda: ({"files": [], "payments": []}, None))
    ledger.record_batch(
        file_content=None, filename="f2.xlsx", company="NewLane",
        payments=[{"kind": "flex", "contract": "Z", "qb_customer": "C",
                   "payment_date": dt.date(2026, 3, 2), "amount": 300}],
    )
    assert saved["data"]["payments"][0]["applies_to"] == "2026-02"


def test_record_batch_skips_applies_to_for_non_newlane(monkeypatch):
    saved: dict = {}
    monkeypatch.setattr(ledger, "load", lambda: ({"files": [], "payments": []}, None))
    monkeypatch.setattr(ledger.store, "save_json",
                        lambda path, data, msg, sha=None: (saved.update(data=data) or (True, None)))
    # OnePlace / GA / FP carry no coverage month, even if one is passed in.
    ledger.record_batch(
        file_content=None, filename="f.xlsx", company="OnePlace",
        payments=[{"kind": "flex", "contract": "A", "qb_customer": "C",
                   "payment_date": dt.date(2026, 5, 3), "amount": 300, "applies_to": "2026-04"}],
    )
    assert "applies_to" not in saved["data"]["payments"][0]
