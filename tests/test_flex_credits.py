"""Payment-driven credit memo builder + legacy active-list builder."""
from __future__ import annotations

import datetime as dt

import pytest

from core import flex_credits


def _clinic(name, monthly_credit=1000.0, active=True, **extra):
    return {
        "clinic_name": name, "qb_name": name, "active": active,
        "monthly_credit": monthly_credit,
        "finance_company": extra.get("finance_company", "OnePlace"),
        "contract_oneplace": extra.get("contract_oneplace"),
        "contract_greatamerica": extra.get("contract_greatamerica"),
        "contract_newlane": extra.get("contract_newlane"),
    }


# ── build_import_from_payments — the new payment-driven path ─────────────────

def test_empty_payments_produces_empty_df():
    df, next_ref, skipped, _src = flex_credits.build_import_from_payments(
        [_clinic("Alpha")], [], 2026, 5, 50000,
    )
    assert len(df) == 0
    assert next_ref == 50000
    assert skipped == []


# ── Non-positive payment skip (clawback safety) ──────────────────────────────
def test_negative_payment_skipped_not_credited():
    """A clawback row in the ledger (amount < 0) must NOT auto-generate a
    credit memo. Stage 2 records it to `skipped` for the operator to handle
    manually in QBO."""
    clinics = [_clinic("Alpha", monthly_credit=1000.0)]
    payments = [
        {"qb_customer": "Alpha", "contract": "X", "amount": -804.56,
         "payment_date": "2026-05-15", "kind": "flex"},
    ]
    df, next_ref, skipped, _src = flex_credits.build_import_from_payments(
        clinics, payments, 2026, 5, 50000,
    )
    assert len(df) == 0
    assert next_ref == 50000  # no refs consumed
    assert len(skipped) == 1
    assert skipped[0]["amount"] == -804.56
    assert "non-positive" in skipped[0]["reason"].lower()


def test_zero_payment_skipped():
    """Zero-amount rows skip the same way negatives do — neither should
    generate a credit memo."""
    clinics = [_clinic("Alpha", monthly_credit=1000.0)]
    payments = [
        {"qb_customer": "Alpha", "contract": "X", "amount": 0.0,
         "payment_date": "2026-05-15", "kind": "flex"},
    ]
    df, _, skipped, _src = flex_credits.build_import_from_payments(
        clinics, payments, 2026, 5, 50000,
    )
    assert len(df) == 0
    assert len(skipped) == 1


def test_mixed_payments_only_positive_produces_credit():
    """Mix of positive + negative + zero — only positive generates a credit memo."""
    clinics = [_clinic("Alpha", monthly_credit=1000.0)]
    payments = [
        {"qb_customer": "Alpha", "contract": "X", "amount": 1000.0,
         "payment_date": "2026-05-01", "kind": "flex"},
        {"qb_customer": "Alpha", "contract": "X", "amount": -500.0,
         "payment_date": "2026-05-15", "kind": "flex"},
        {"qb_customer": "Alpha", "contract": "X", "amount": 0.0,
         "payment_date": "2026-05-20", "kind": "flex"},
    ]
    df, _, skipped, _src = flex_credits.build_import_from_payments(
        clinics, payments, 2026, 5, 50000,
    )
    assert len(df) == 1
    assert df.iloc[0]["Product/Service Amount"] == 1000.0
    assert len(skipped) == 2  # one negative + one zero
    reasons = {s["reason"] for s in skipped}
    assert all("non-positive" in r.lower() for r in reasons)


def test_one_payment_yields_one_credit_memo():
    clinics = [_clinic("Alpha", monthly_credit=1000.0)]
    payments = [
        {"qb_customer": "Alpha", "contract": "X1", "payment_date": "2026-05-15",
         "amount": 950.0, "kind": "flex"},
    ]
    df, next_ref, skipped, _src = flex_credits.build_import_from_payments(
        clinics, payments, 2026, 5, 50000,
    )
    assert len(df) == 1
    assert df.iloc[0]["Customer"] == "Alpha"
    assert df.iloc[0]["Product/Service Amount"] == 1000.0  # from monthly_credit, NOT the payment amount
    assert next_ref == 50001
    assert skipped == []


def test_multi_payment_clinic_gets_multi_credit_memos():
    """A clinic that pays 3× in one month gets 3 credit memos at monthly_credit each — SOP-10 case."""
    clinics = [_clinic("Beta", monthly_credit=1500.0)]
    payments = [
        {"qb_customer": "Beta", "contract": "X", "payment_date": "2026-05-01", "amount": 1500.0, "kind": "flex"},
        {"qb_customer": "Beta", "contract": "X", "payment_date": "2026-05-10", "amount": 1500.0, "kind": "flex"},
        {"qb_customer": "Beta", "contract": "X", "payment_date": "2026-05-20", "amount": 1500.0, "kind": "flex"},
    ]
    df, _next, skipped, _src = flex_credits.build_import_from_payments(clinics, payments, 2026, 5, 50000)
    assert len(df) == 3
    assert all(df["Customer"] == "Beta")
    assert all(df["Product/Service Amount"] == 1500.0)
    assert skipped == []


def test_payment_with_no_matching_clinic_falls_back_to_payment_amount():
    """When a payment doesn't match any flex_master clinic, emit a credit memo
    against the ledger's qb_customer using the payment amount (SOP-10's
    'one Flex payment in, one credit out' invariant). Still tracked in `skipped`
    for audit visibility, just no longer dropped from the export."""
    clinics = [_clinic("Gamma")]
    payments = [
        {"qb_customer": "Mystery Clinic", "contract": "UNKNOWN", "payment_date": "2026-05-01",
         "amount": 500.0, "kind": "flex"},
    ]
    df, _next, skipped, _src = flex_credits.build_import_from_payments(clinics, payments, 2026, 5, 50000)
    assert len(df) == 1
    assert df.iloc[0]["Customer"] == "Mystery Clinic"
    assert df.iloc[0]["Product/Service Amount"] == 500.0
    # Still surfaces in `skipped` for audit recording even though the row IS emitted.
    assert len(skipped) == 1
    assert "no flex_master match" in skipped[0]["reason"]


def test_match_falls_back_to_contract_when_qb_customer_unmatched():
    """If qb_customer is the finance-co's legal name (untranslated), contract should match."""
    clinics = [_clinic("Delta", contract_oneplace="OPC555")]
    payments = [
        {"qb_customer": "Some Legal Name LLC", "contract": "OPC555", "payment_date": "2026-05-01",
         "amount": 1000.0, "kind": "flex"},
    ]
    df, _next, skipped, _src = flex_credits.build_import_from_payments(clinics, payments, 2026, 5, 50000)
    assert len(df) == 1
    assert df.iloc[0]["Customer"] == "Delta"


def test_inactive_clinic_falls_back_to_payment_amount():
    """Inactive clinics aren't in the lookup index, so a payment for one falls into
    the no-flex_master-match bucket and uses the payment amount as the credit."""
    clinics = [_clinic("Epsilon", active=False)]
    payments = [
        {"qb_customer": "Epsilon", "contract": "X", "payment_date": "2026-05-01",
         "amount": 1000.0, "kind": "flex"},
    ]
    df, _next, skipped, _src = flex_credits.build_import_from_payments(clinics, payments, 2026, 5, 50000)
    assert len(df) == 1
    assert df.iloc[0]["Customer"] == "Epsilon"
    assert df.iloc[0]["Product/Service Amount"] == 1000.0
    assert len(skipped) == 1


def test_sequential_refs_no_collisions():
    clinics = [_clinic(f"C{i}") for i in range(5)]
    payments = [
        {"qb_customer": f"C{i}", "contract": f"X{i}", "payment_date": "2026-05-01",
         "amount": 1000.0, "kind": "flex"}
        for i in range(5)
    ]
    df, next_ref, _skipped, _src = flex_credits.build_import_from_payments(clinics, payments, 2026, 5, 50000)
    refs = list(df["Credit Memo No"])
    assert len(set(refs)) == 5
    assert refs == sorted(refs)
    assert next_ref == 50005


# ── legacy build_import (active-list bootstrap) ──────────────────────────────

def test_legacy_build_import_one_memo_per_active_clinic():
    clinics = [
        _clinic("A", monthly_credit=1000.0),
        _clinic("B", monthly_credit=2000.0),
        _clinic("C", active=False),  # excluded
        _clinic("D", monthly_credit=0),  # excluded
    ]
    df, next_ref = flex_credits.build_import(clinics, 2026, 5, 50000)
    assert len(df) == 2
    customers = set(df["Customer"])
    assert customers == {"A", "B"}
    assert next_ref == 50002
