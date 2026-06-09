"""Coverage for rebate_calc.calculate — the dollar engine.

Every cycle's payout flows through this. Tests are parametric — they pass
explicit rates into _master() and verify the math, so they remain valid
across rate-scheme changes. The current production scheme (2026-06-09):
ultrasound 10% finance / 8% self-funded; rads 5% finance / 4% self-funded.

Exclusions (stat/assistance/non_ema/cancellation/overage) need to stay
out of the rebatable base — covered by the exclusion tests below.
"""
import pandas as pd
import pytest

from core import rebate_calc


def _master(rate_us=0.10, rate_rads=0.04, program_type="finance",
            confirmed=True, clinic_name="Acme Vet", finance_company="OnePlace Capital"):
    return {
        "rate_defaults": {
            "ultrasound_finance": 0.10, "ultrasound_self_funded": 0.05,
            "rads_finance": 0.04, "rads_self_funded": 0.02,
        },
        "clinics": [{
            "clinic_name": clinic_name,
            "legal_name": clinic_name,
            "finance_company": finance_company,
            "program_type": program_type,
            "rate_ultrasound": rate_us,
            "rate_rads": rate_rads,
            "rads_rate_confirmed": confirmed,
            "active": True,
        }],
    }


def _norm(rows):
    """Build a normalized OPD frame from (clinic, category, amount) tuples."""
    return pd.DataFrame(
        [{"clinic": c, "category": cat, "amount": a,
          "feed_us_finance": 0.0, "feed_us_cash": 0.0,
          "feed_rad_finance": 0.0, "feed_rad_cash": 0.0}
         for c, cat, a in rows]
    )


def test_finance_clinic_basic_rebate():
    """10% on ultrasound + 4% on rads for a finance-funded clinic."""
    norm = _norm([
        ("Acme Vet", "ultrasound", 1000.0),
        ("Acme Vet", "rads", 500.0),
    ])
    res = rebate_calc.calculate(norm, _master(), config={})
    pc = res["per_clinic"]
    assert len(pc) == 1
    assert pc.iloc[0]["ultrasound_rebate"] == 100.0   # 1000 * 10%
    assert pc.iloc[0]["rads_rebate"] == 20.0          # 500 * 4%
    assert pc.iloc[0]["total_rebate"] == 120.0


def test_self_funded_uses_lower_rates():
    """Self-funded clinic should default to 5% / 2%."""
    norm = _norm([
        ("Acme Vet", "ultrasound", 1000.0),
        ("Acme Vet", "rads", 500.0),
    ])
    master = _master(rate_us=0.05, rate_rads=0.02,
                     program_type="self_funded", finance_company="Self-Financed")
    res = rebate_calc.calculate(norm, master, config={})
    pc = res["per_clinic"]
    assert pc.iloc[0]["ultrasound_rebate"] == 50.0    # 1000 * 5%
    assert pc.iloc[0]["rads_rebate"] == 10.0          # 500 * 2%


def test_self_funded_rads_pending_flag_set_when_unconfirmed():
    """rads_rate_confirmed=False on a self-funded clinic should set the pending flag."""
    norm = _norm([("Acme Vet", "rads", 500.0)])
    master = _master(rate_us=0.05, rate_rads=0.02,
                     program_type="self_funded", confirmed=False,
                     finance_company="Self-Financed")
    res = rebate_calc.calculate(norm, master, config={})
    pc = res["per_clinic"]
    assert bool(pc.iloc[0]["rads_pending_confirmation"]) is True


def test_self_funded_rads_pending_flag_off_when_confirmed():
    norm = _norm([("Acme Vet", "rads", 500.0)])
    master = _master(rate_us=0.05, rate_rads=0.02,
                     program_type="self_funded", confirmed=True,
                     finance_company="Self-Financed")
    res = rebate_calc.calculate(norm, master, config={})
    pc = res["per_clinic"]
    assert bool(pc.iloc[0]["rads_pending_confirmation"]) is False


def test_finance_clinic_never_pending_even_if_unconfirmed():
    """rads_pending_confirmation only applies to self-funded clinics."""
    norm = _norm([("Acme Vet", "rads", 500.0)])
    master = _master(confirmed=False)  # finance + unconfirmed
    res = rebate_calc.calculate(norm, master, config={})
    pc = res["per_clinic"]
    assert bool(pc.iloc[0]["rads_pending_confirmation"]) is False


def test_excluded_categories_not_rebatable():
    """STAT / assistance / non_ema / cancellation / overage revenue is reported
    in excluded_revenue but doesn't earn a rebate."""
    norm = _norm([
        ("Acme Vet", "ultrasound", 1000.0),
        ("Acme Vet", "rads", 500.0),
        ("Acme Vet", "stat", 200.0),
        ("Acme Vet", "assistance", 100.0),
        ("Acme Vet", "non_ema", 50.0),
        ("Acme Vet", "cancellation", 30.0),
        ("Acme Vet", "other", 25.0),
    ])
    res = rebate_calc.calculate(norm, _master(), config={})
    pc = res["per_clinic"]
    # 1000 * 10% + 500 * 4% = 120; nothing else counts
    assert pc.iloc[0]["total_rebate"] == 120.0
    # excluded_revenue tracks stat + assistance + non_ema + cancellation + overage
    assert pc.iloc[0]["excluded_revenue"] == 380.0   # 200+100+50+30
    assert pc.iloc[0]["ultrasound_revenue"] == 1000.0
    assert pc.iloc[0]["rads_revenue"] == 500.0


def test_clinic_name_casefold_aggregation():
    """The bug we just fixed: two capitalizations of the same clinic should aggregate
    into one row in per_clinic (not two rows where only the last survives)."""
    norm = _norm([
        ("ACE Animal Hospital", "ultrasound", 500.0),
        ("ace animal hospital", "ultrasound", 500.0),  # same clinic, different case
    ])
    master = _master(clinic_name="Ace Animal Hospital")
    res = rebate_calc.calculate(norm, master, config={})
    pc = res["per_clinic"]
    assert len(pc) == 1                                # one row, not two
    assert pc.iloc[0]["ultrasound_revenue"] == 1000.0  # summed, not overwritten


def test_unmatched_clinic_reported_separately():
    """OPD lists a clinic not in the roster; it goes to unmatched and contributes nothing
    to per_clinic, but its revenue is tracked."""
    norm = _norm([
        ("Acme Vet", "ultrasound", 1000.0),
        ("Made-Up Hospital", "ultrasound", 9999.0),
    ])
    res = rebate_calc.calculate(norm, _master(), config={})
    assert len(res["per_clinic"]) == 1
    assert len(res["unmatched"]) == 1
    assert res["unmatched"].iloc[0]["opd_clinic"] == "made-up hospital"
    assert res["unmatched"].iloc[0]["ultrasound_revenue"] == 9999.0


def test_variance_zero_when_no_feed_columns():
    """Generic OPD exports (no feed_us_*/feed_rad_* columns) -> feed_total = 0 -> variance = rate_total."""
    norm = _norm([
        ("Acme Vet", "ultrasound", 1000.0),
        ("Acme Vet", "rads", 500.0),
    ])
    res = rebate_calc.calculate(norm, _master(), config={})
    pc = res["per_clinic"]
    # Without feed columns active, feed_total is 0, variance equals rate_total.
    assert pc.iloc[0]["rebate_feed_based"] == 0.0
    assert pc.iloc[0]["variance"] == pc.iloc[0]["rebate_rate_based"]


def test_variance_flags_disagreement_with_feed():
    """When the OPD feed reports its OWN pre-calculated rebate (RadCash/UltraCash) AND the
    feed_agg path is active, rebate_feed_based should reflect the feed and variance should
    show the disagreement. This is the silent-fail-open bug we fixed in the Step 3 surface."""
    norm = pd.DataFrame([
        {
            "clinic": "Acme Vet", "category": "ultrasound", "amount": 1000.0,
            # Feed says $90 ultrasound rebate; rate-based says $100 (10% of 1000). Variance = +$10.
            "feed_us_finance": 90.0, "feed_us_cash": 0.0,
            "feed_rad_finance": 0.0, "feed_rad_cash": 0.0,
        }
    ])
    res = rebate_calc.calculate(norm, _master(), config={})
    pc = res["per_clinic"]
    assert pc.iloc[0]["rebate_rate_based"] == 100.0
    assert pc.iloc[0]["rebate_feed_based"] == 90.0
    assert pc.iloc[0]["variance"] == 10.0


def test_inactive_clinic_still_calculates_if_in_master():
    """Inactivity is a master-roster flag; the calc itself doesn't filter on it
    (the downstream report filters)."""
    norm = _norm([("Acme Vet", "ultrasound", 1000.0)])
    master = _master()
    master["clinics"][0]["active"] = False
    res = rebate_calc.calculate(norm, master, config={})
    # The clinic still matches and gets a rebate row; the cycle UI is what filters by active.
    assert len(res["per_clinic"]) == 1
