"""Coverage for ledger contract normalization + reissue detection.

Pre-fix, the ledger fingerprint used `str(contract).strip()` directly. Pandas
re-parses identical source data as either a string or a Float64 column
depending on neighbors, so the same physical row could fingerprint to two
different hashes across re-uploads — silently dodging dedup.

Reissue detection: incoming payment matches an existing ledger row on
(company, kind, contract, amount) but has a different payment_date. These
look like reissues, not net-new — Stage 1 should surface them for confirm.
"""
import datetime as dt
from core import ledger


def test_normalize_strips_float_artifact():
    assert ledger._normalize_contract("40010172988.0") == "40010172988"


def test_normalize_preserves_leading_zeros():
    # OnePlace flex contracts have leading zeros that downstream Ref-No logic
    # strips intentionally — but at the ledger level, we keep them for stability.
    assert ledger._normalize_contract("04001017") == "04001017"


def test_normalize_strips_unicode_whitespace():
    nbsp = " "
    zwsp = "​"
    assert ledger._normalize_contract(f" 4001017{nbsp}{zwsp}") == "4001017"


def test_fingerprint_stable_across_float_string_drift():
    # Same physical row arriving as string then Float64 must hash the same.
    fp_str = ledger.fingerprint("OnePlace", "flex", "40010172988", dt.date(2026, 5, 1), 1234.56)
    fp_flt = ledger.fingerprint("OnePlace", "flex", "40010172988.0", dt.date(2026, 5, 1), 1234.56)
    assert fp_str == fp_flt


def test_partial_fingerprint_ignores_date():
    a = ledger.partial_fingerprint("OnePlace", "flex", "40010172988", 1234.56)
    b = ledger.partial_fingerprint("OnePlace", "flex", "40010172988", 1234.56)
    assert a == b


def test_partial_fingerprint_distinguishes_amount():
    a = ledger.partial_fingerprint("OnePlace", "flex", "40010172988", 1234.56)
    b = ledger.partial_fingerprint("OnePlace", "flex", "40010172988", 9999.99)
    assert a != b


def test_check_possible_reissues_empty_when_ledger_empty(monkeypatch):
    monkeypatch.setattr(ledger, "load", lambda: ({"files": [], "payments": []}, None))
    incoming = [{"kind": "flex", "contract": "4001017",
                 "payment_date": dt.date(2026, 5, 1), "amount": 100.0}]
    assert ledger.check_possible_reissues("OnePlace", incoming) == []


def test_check_possible_reissues_flags_same_amount_different_date(monkeypatch):
    existing_pay = {
        "fingerprint": "abc",
        "company": "OnePlace",
        "kind": "flex",
        "contract": "4001017",
        "qb_customer": "Acme Vet",
        "payment_date": "2026-04-01",
        "amount": 100.0,
    }
    monkeypatch.setattr(ledger, "load", lambda: ({"files": [], "payments": [existing_pay]}, None))
    incoming = [{"kind": "flex", "contract": "4001017",
                 "payment_date": dt.date(2026, 5, 1), "amount": 100.0}]
    out = ledger.check_possible_reissues("OnePlace", incoming)
    assert len(out) == 1
    assert out[0]["existing"][0]["payment_date"] == "2026-04-01"


def test_check_possible_reissues_skips_exact_duplicates(monkeypatch):
    # If the date matches too, it's an exact dup — the regular dedup handles it.
    # check_possible_reissues should only surface DATE-DIFFERENT matches.
    existing_pay = {
        "fingerprint": "abc", "company": "OnePlace", "kind": "flex",
        "contract": "4001017", "qb_customer": "Acme Vet",
        "payment_date": "2026-05-01", "amount": 100.0,
    }
    monkeypatch.setattr(ledger, "load", lambda: ({"files": [], "payments": [existing_pay]}, None))
    incoming = [{"kind": "flex", "contract": "4001017",
                 "payment_date": dt.date(2026, 5, 1), "amount": 100.0}]
    out = ledger.check_possible_reissues("OnePlace", incoming)
    assert out == []


def test_check_possible_reissues_normalizes_contract(monkeypatch):
    # Existing row stored with float artifact; incoming as clean string. Must match.
    existing_pay = {
        "fingerprint": "abc", "company": "OnePlace", "kind": "flex",
        "contract": "40010172988.0", "qb_customer": "Acme Vet",
        "payment_date": "2026-04-01", "amount": 100.0,
    }
    monkeypatch.setattr(ledger, "load", lambda: ({"files": [], "payments": [existing_pay]}, None))
    incoming = [{"kind": "flex", "contract": "40010172988",
                 "payment_date": dt.date(2026, 5, 1), "amount": 100.0}]
    out = ledger.check_possible_reissues("OnePlace", incoming)
    assert len(out) == 1
