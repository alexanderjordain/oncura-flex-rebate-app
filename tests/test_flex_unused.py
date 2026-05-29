"""Recapture computation — the core quarter-end math."""
from __future__ import annotations

import datetime as dt

import pytest

from core import flex_unused


# ── quarter boundaries ───────────────────────────────────────────────────────

def test_quarter_end_month_calendar():
    assert flex_unused.quarter_end_month("Calendar") == 12  # 12 mod 3 == 0 -> all calendar quarters


def test_quarter_end_month_march_april_may():
    assert flex_unused.quarter_end_month("March-April-May") == 5


def test_quarter_end_month_may_june_july():
    assert flex_unused.quarter_end_month("May-June-July") == 7


def test_quarter_end_month_unknown_returns_none():
    assert flex_unused.quarter_end_month(None) is None
    assert flex_unused.quarter_end_month("") is None
    assert flex_unused.quarter_end_month("Banana") is None


def test_is_quarter_end_calendar():
    for m in (3, 6, 9, 12):
        assert flex_unused.is_quarter_end("Calendar", m)
    for m in (1, 2, 4, 5, 7, 8, 10, 11):
        assert not flex_unused.is_quarter_end("Calendar", m)


def test_is_quarter_end_march_april_may():
    for m in (2, 5, 8, 11):
        assert flex_unused.is_quarter_end("March-April-May", m)
    assert not flex_unused.is_quarter_end("March-April-May", 3)


def test_quarter_window_covers_three_months():
    s, e = flex_unused.quarter_window(2026, 5)  # March-April-May ending May
    assert s == dt.date(2026, 3, 1)
    assert e == dt.date(2026, 5, 31)


def test_quarter_window_year_rollover():
    s, e = flex_unused.quarter_window(2026, 1)  # Nov-Dec-Jan
    assert s == dt.date(2025, 11, 1)
    assert e == dt.date(2026, 1, 31)


# ── compute_recapture: the key behavior ──────────────────────────────────────

def _clinic(name, threshold=6000.0, spread="March-April-May", active=True, **extra):
    return {
        "clinic_name": name, "qb_name": name, "active": active,
        "calendar_spread": spread, "quarterly_threshold": threshold,
        "monthly_credit": threshold / 6,
        "finance_company": extra.get("finance_company", "OnePlace"),
        "contract_oneplace": extra.get("contract_oneplace"),
        "contract_greatamerica": extra.get("contract_greatamerica"),
        "contract_newlane": extra.get("contract_newlane"),
        "parent_clinic_id": extra.get("parent_clinic_id"),
        "group_id": extra.get("group_id"),
    }


def test_recapture_unused_when_activity_below_threshold():
    clinics = [_clinic("Alpha", threshold=6000.0)]
    activity = {"alpha": 4000.0}
    rows = flex_unused.compute_recapture(clinics, activity, 2026, 5)
    assert len(rows) == 1
    r = rows[0]
    assert r["activity_match"] == "exact"
    assert r["unused"] == 2000.0
    assert r["overage"] == 0.0


def test_recapture_overage_when_activity_above_threshold():
    clinics = [_clinic("Beta", threshold=6000.0)]
    activity = {"beta": 7500.0}
    rows = flex_unused.compute_recapture(clinics, activity, 2026, 5)
    assert rows[0]["unused"] == 0.0
    assert rows[0]["overage"] == 1500.0


def test_recapture_skips_clinic_off_calendar():
    clinics = [_clinic("Gamma", spread="Calendar")]  # ends month 12, not 5
    rows = flex_unused.compute_recapture(clinics, {"gamma": 5000.0}, 2026, 5)
    assert rows == []


def test_recapture_skips_inactive_clinic():
    clinics = [_clinic("Delta", active=False)]
    rows = flex_unused.compute_recapture(clinics, {"delta": 5000.0}, 2026, 5)
    assert rows == []


def test_recapture_no_activity_yields_none_marker():
    clinics = [_clinic("Epsilon")]
    rows = flex_unused.compute_recapture(clinics, {}, 2026, 5)
    assert len(rows) == 1
    assert rows[0]["activity_match"] == "none"
    assert rows[0]["unused"] is None
    assert rows[0]["overage"] is None


def test_recapture_pools_multi_clinic_group_thresholds_and_activity():
    """Mohnacky pattern: anchor + 2 children. Pool = 3× threshold, activity sums."""
    anchor = _clinic("Mohnacky Carlsbad", threshold=6000.0)
    child_a = _clinic("Mohnacky Vista", threshold=6000.0, parent_clinic_id="Mohnacky Carlsbad")
    child_b = _clinic("Mohnacky Escondido", threshold=6000.0, parent_clinic_id="Mohnacky Carlsbad")
    activity = {
        "mohnacky carlsbad": 3000.0,
        "mohnacky vista": 2500.0,
        "mohnacky escondido": 4000.0,
    }
    rows = flex_unused.compute_recapture([anchor, child_a, child_b], activity, 2026, 5)
    # Only the anchor emits a row
    assert len(rows) == 1
    r = rows[0]
    assert r["clinic_name"] == "Mohnacky Carlsbad"
    assert r["quarterly_threshold"] == 18000.0  # 3 × 6000
    assert r["quarter_activity"] == 9500.0
    assert r["unused"] == 8500.0
    assert r["group_member_count"] == 3


def test_recapture_emits_contract_number_by_finance_company():
    c = _clinic("Iota", finance_company="OnePlace", contract_oneplace="OPC123")
    rows = flex_unused.compute_recapture([c], {"iota": 1000.0}, 2026, 5)
    assert rows[0]["contract_number"] == "OPC123"

    c2 = _clinic("Jota", finance_company="GreatAmerica", contract_greatamerica="GA999")
    rows = flex_unused.compute_recapture([c2], {"jota": 1000.0}, 2026, 5)
    assert rows[0]["contract_number"] == "GA999"


# ── unused invoice builder ───────────────────────────────────────────────────

def test_build_unused_invoice_only_includes_positive_unused():
    rows = [
        {"qb_name": "A", "clinic_name": "A", "unused": 100.0, "overage": 0.0},
        {"qb_name": "B", "clinic_name": "B", "unused": 0.0, "overage": 50.0},
        {"qb_name": "C", "clinic_name": "C", "unused": None, "overage": None},
    ]
    df, next_ref = flex_unused.build_unused_invoice_import(rows, 2026, 5, 60000, "03-Telemedicine")
    assert len(df) == 1
    assert df.iloc[0]["Customer"] == "A"
    assert df.iloc[0]["Product/Service Amount"] == 100.0
    assert next_ref == 60001


def test_build_unused_invoice_sequential_refs_unique():
    rows = [
        {"qb_name": f"Clinic{i}", "clinic_name": f"Clinic{i}", "unused": 100.0 * (i + 1), "overage": 0.0}
        for i in range(5)
    ]
    df, next_ref = flex_unused.build_unused_invoice_import(rows, 2026, 5, 70000, "03-Telemedicine")
    refs = list(df["Invoice No"])
    assert refs == [70000, 70001, 70002, 70003, 70004]
    assert next_ref == 70005
