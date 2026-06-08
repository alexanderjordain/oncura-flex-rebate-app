"""Coverage for flex_overage routing + credit-offset (SOP-6 + SOP-12).

These are the two highest-value behaviors per the test-coverage audit: getting
the routing wrong at quarter-close either submits to a partner who'll reject
the overage (revenue delay), or direct-bills a clinic the partner would have
absorbed (clinic-relations problem). Credit-offset bugs create clinic-facing
dollar errors since SOP-12 forbids refunds — under-applying or over-applying
credits ships a wrong invoice.
"""
import datetime as dt

import pytest

from core import flex_overage

# Standard config matching production data/config.json shape
CFG = {
    "flex": {
        "overage": {
            "finance_partner_cutoff_day": 5,
            "finance_partner_handles": {
                "OnePlace": True,
                "GreatAmerica": False,
                "NewLane": False,
            },
            "direct_invoice_item": "Telemedicine Overage",
            "direct_invoice_memo_template": "Telemedicine Overages — {quarter}",
            "escalation_clinics": ["Luv-N-Care"],
        }
    }
}


# ── route_overage cutoff matrix ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "company,today,recap_y,recap_m,expected",
    [
        # OnePlace handles overages; before cutoff -> partner submission
        ("OnePlace", dt.date(2026, 7, 3), 2026, 6, flex_overage.ROUTE_PARTNER),
        # OnePlace, same day as cutoff (Jul 5) -> still partner (strict >)
        ("OnePlace", dt.date(2026, 7, 5), 2026, 6, flex_overage.ROUTE_PARTNER),
        # OnePlace, ONE DAY AFTER cutoff -> missed_cutoff -> direct bill
        ("OnePlace", dt.date(2026, 7, 6), 2026, 6, flex_overage.ROUTE_MISSED_CUTOFF),
        # GreatAmerica opted out -> direct, regardless of date
        ("GreatAmerica", dt.date(2026, 7, 3), 2026, 6, flex_overage.ROUTE_DIRECT),
        ("GreatAmerica", dt.date(2026, 7, 30), 2026, 6, flex_overage.ROUTE_DIRECT),
        # NewLane opted out -> direct
        ("NewLane", dt.date(2026, 7, 3), 2026, 6, flex_overage.ROUTE_DIRECT),
        # Self-Financed (not in config) -> direct (default False on missing key)
        ("Self-Financed", dt.date(2026, 7, 3), 2026, 6, flex_overage.ROUTE_DIRECT),
    ],
)
def test_route_overage_matrix(company, today, recap_y, recap_m, expected):
    assert flex_overage.route_overage(company, recap_y, recap_m, today, CFG) == expected


def test_route_overage_year_rollover_december():
    """Dec recap -> cutoff is Jan 5 of NEXT year, not Jan 5 of same year."""
    # Today is Jan 4 2027, recapping Dec 2026. Cutoff is Jan 5 2027 -> still partner.
    assert (
        flex_overage.route_overage(
            "OnePlace", 2026, 12, dt.date(2027, 1, 4), CFG
        )
        == flex_overage.ROUTE_PARTNER
    )
    # Same recap, but today is Jan 6 2027 -> missed cutoff.
    assert (
        flex_overage.route_overage(
            "OnePlace", 2026, 12, dt.date(2027, 1, 6), CFG
        )
        == flex_overage.ROUTE_MISSED_CUTOFF
    )


def test_cutoff_date_helper_year_wrap():
    # Recap = Dec 2026 -> cutoff = Jan 5 2027
    assert flex_overage.cutoff_date(2026, 12, 5) == dt.date(2027, 1, 5)
    # Recap = Mar 2026 -> cutoff = Apr 5 2026
    assert flex_overage.cutoff_date(2026, 3, 5) == dt.date(2026, 4, 5)


# ── annotate_overages credit-offset (SOP-12) ────────────────────────────────


def test_annotate_credit_offset_partial():
    """Clinic has unapplied credit < gross overage. Net = gross - credit."""
    rows = [
        {"finance_company": "GreatAmerica", "clinic_name": "Acme Vet",
         "qb_name": "Acme Vet", "overage": 1000.0},
    ]
    out = flex_overage.annotate_overages(
        rows, 2026, 6, dt.date(2026, 7, 3), CFG, credit_offsets={"Acme Vet": 200.0}
    )
    assert len(out) == 1
    assert out[0]["credit_applied"] == 200.0
    assert out[0]["net_overage"] == 800.0


def test_annotate_credit_offset_exceeds_gross_caps_at_gross():
    """Credit balance > gross overage. SOP-12 forbids refunds — apply only up to gross."""
    rows = [
        {"finance_company": "GreatAmerica", "clinic_name": "Acme Vet",
         "qb_name": "Acme Vet", "overage": 500.0},
    ]
    out = flex_overage.annotate_overages(
        rows, 2026, 6, dt.date(2026, 7, 3), CFG, credit_offsets={"Acme Vet": 1200.0}
    )
    assert out[0]["credit_applied"] == 500.0    # capped at gross
    assert out[0]["net_overage"] == 0.0          # nothing to bill
    # The extra $700 is silently lost to the clinic per SOP-12 (apply to future overages).


def test_annotate_credit_offset_no_credit():
    rows = [
        {"finance_company": "GreatAmerica", "clinic_name": "Acme Vet",
         "qb_name": "Acme Vet", "overage": 500.0},
    ]
    out = flex_overage.annotate_overages(rows, 2026, 6, dt.date(2026, 7, 3), CFG)
    assert out[0]["credit_applied"] == 0.0
    assert out[0]["net_overage"] == 500.0


def test_annotate_drops_zero_or_negative_overage():
    """Overage == 0 or negative isn't an overage — drop the row."""
    rows = [
        {"finance_company": "GreatAmerica", "clinic_name": "Zero Vet",
         "qb_name": "Zero Vet", "overage": 0.0},
        {"finance_company": "GreatAmerica", "clinic_name": "Negative Vet",
         "qb_name": "Negative Vet", "overage": -50.0},
        {"finance_company": "GreatAmerica", "clinic_name": "Real Vet",
         "qb_name": "Real Vet", "overage": 100.0},
    ]
    out = flex_overage.annotate_overages(rows, 2026, 6, dt.date(2026, 7, 3), CFG)
    assert len(out) == 1
    assert out[0]["clinic_name"] == "Real Vet"


def test_annotate_escalation_flag_substring_match():
    """Clinic name contains an escalation substring (case-insensitive)."""
    rows = [
        {"finance_company": "GreatAmerica", "clinic_name": "Luv-N-Care Animal Hospital",
         "qb_name": "Luv-N-Care", "overage": 100.0},
        {"finance_company": "GreatAmerica", "clinic_name": "Acme Vet",
         "qb_name": "Acme Vet", "overage": 100.0},
    ]
    out = flex_overage.annotate_overages(rows, 2026, 6, dt.date(2026, 7, 3), CFG)
    by_name = {r["clinic_name"]: r for r in out}
    assert by_name["Luv-N-Care Animal Hospital"]["escalation_flag"] is True
    assert by_name["Acme Vet"]["escalation_flag"] is False


def test_build_direct_invoice_only_includes_direct_and_missed():
    rows = [
        # OnePlace before cutoff -> partner, NOT in direct invoice
        {"finance_company": "OnePlace", "clinic_name": "Partner Vet",
         "qb_name": "Partner Vet", "overage": 100.0},
        # OnePlace after cutoff -> missed_cutoff -> direct invoice
        {"finance_company": "OnePlace", "clinic_name": "Missed Vet",
         "qb_name": "Missed Vet", "overage": 200.0,
         "contract_oneplace": "04001017"},
        # GreatAmerica -> direct -> direct invoice
        {"finance_company": "GreatAmerica", "clinic_name": "Direct Vet",
         "qb_name": "Direct Vet", "overage": 300.0},
    ]
    # Two scenarios — pre-cutoff and post-cutoff
    annotated_pre = flex_overage.annotate_overages(
        rows, 2026, 6, dt.date(2026, 7, 3), CFG
    )
    df_pre, _ = flex_overage.build_direct_invoice_import(
        annotated_pre, 2026, 6, start_ref=80000, sales_class="03-Telemedicine", cfg=CFG
    )
    # Before cutoff: OnePlace -> partner (excluded); GA -> direct (included). 1 row.
    assert len(df_pre) == 1
    assert df_pre.iloc[0]["Customer"] == "Direct Vet"

    annotated_post = flex_overage.annotate_overages(
        rows, 2026, 6, dt.date(2026, 7, 10), CFG
    )
    df_post, _ = flex_overage.build_direct_invoice_import(
        annotated_post, 2026, 6, start_ref=80000, sales_class="03-Telemedicine", cfg=CFG
    )
    # After cutoff: BOTH OnePlace clinics route to missed_cutoff (included), plus GA -> direct.
    # Three rows total.
    assert len(df_post) == 3
    customers = set(df_post["Customer"])
    assert customers == {"Partner Vet", "Missed Vet", "Direct Vet"}


# ── build_direct_billing_worksheet — the human-readable Tanya-facing flow ────


def _make_annotated_for_worksheet():
    """Annotated rows with the full threshold/activity/credit context the
    worksheet surfaces. Mirrors what compute_recapture + annotate_overages
    produce in Stage 3."""
    rows = [
        {"finance_company": "GreatAmerica", "clinic_name": "Direct Vet",
         "qb_name": "Direct Vet QB", "contract_greatamerica": "GA-999",
         "quarterly_threshold": 5700.0, "quarter_activity": 8000.0,
         "overage": 2300.0},
        {"finance_company": "OnePlace", "clinic_name": "Missed Vet",
         "qb_name": "Missed Vet QB", "contract_oneplace": "OPC-12345",
         "quarterly_threshold": 5100.0, "quarter_activity": 6000.0,
         "overage": 900.0},
    ]
    # Post-cutoff: OnePlace -> missed_cutoff -> joins direct flow
    return flex_overage.annotate_overages(rows, 2026, 6, dt.date(2026, 7, 10), CFG)


def test_billing_worksheet_includes_threshold_activity_and_net_owed():
    annotated = _make_annotated_for_worksheet()
    df = flex_overage.build_direct_billing_worksheet(annotated, 2026, 6, CFG)
    assert len(df) == 2
    direct_row = df[df["Clinic"] == "Direct Vet"].iloc[0]
    assert direct_row["Quarterly Threshold"] == 5700.0
    assert direct_row["Quarter Activity"] == 8000.0
    assert direct_row["Gross Overage"] == 2300.0
    assert direct_row["Net Amount to Bill"] == 2300.0
    assert direct_row["Contract #"] == "GA-999"
    assert direct_row["QB Customer"] == "Direct Vet QB"


def test_billing_worksheet_route_reason_distinguishes_missed_cutoff():
    annotated = _make_annotated_for_worksheet()
    df = flex_overage.build_direct_billing_worksheet(annotated, 2026, 6, CFG)
    missed = df[df["Clinic"] == "Missed Vet"].iloc[0]
    direct = df[df["Clinic"] == "Direct Vet"].iloc[0]
    assert "missed cutoff" in missed["Route Reason"].lower()
    assert "missed cutoff" not in direct["Route Reason"].lower()
    assert "great" in direct["Route Reason"].lower() or "no partner" in direct["Route Reason"].lower()


def test_billing_worksheet_applies_credit_offsets():
    rows = [
        {"finance_company": "GreatAmerica", "clinic_name": "Has Credit",
         "qb_name": "Has Credit QB", "contract_greatamerica": "GA-1",
         "quarterly_threshold": 5700.0, "quarter_activity": 7500.0,
         "overage": 1800.0},
    ]
    annotated = flex_overage.annotate_overages(
        rows, 2026, 6, dt.date(2026, 7, 10), CFG,
        credit_offsets={"Has Credit QB": 500.0},
    )
    df = flex_overage.build_direct_billing_worksheet(annotated, 2026, 6, CFG)
    r = df.iloc[0]
    assert r["Gross Overage"] == 1800.0
    assert r["Pre-existing Credit Applied"] == 500.0
    assert r["Net Amount to Bill"] == 1300.0


def test_billing_worksheet_excludes_zero_net_and_partner_routes():
    rows = [
        # Net is zero after credit -> dropped
        {"finance_company": "GreatAmerica", "clinic_name": "Fully Credited",
         "qb_name": "Fully Credited", "overage": 200.0,
         "quarterly_threshold": 5700.0, "quarter_activity": 5900.0},
        # OnePlace before cutoff -> partner -> excluded from direct worksheet
        {"finance_company": "OnePlace", "clinic_name": "Partner Vet",
         "qb_name": "Partner Vet", "overage": 100.0,
         "quarterly_threshold": 5700.0, "quarter_activity": 5800.0},
        # GreatAmerica -> direct -> kept
        {"finance_company": "GreatAmerica", "clinic_name": "Real Bill",
         "qb_name": "Real Bill", "overage": 300.0,
         "quarterly_threshold": 5700.0, "quarter_activity": 6000.0},
    ]
    annotated = flex_overage.annotate_overages(
        rows, 2026, 6, dt.date(2026, 7, 3), CFG,
        credit_offsets={"Fully Credited": 200.0},  # zeros out
    )
    df = flex_overage.build_direct_billing_worksheet(annotated, 2026, 6, CFG)
    assert list(df["Clinic"]) == ["Real Bill"]


def test_billing_worksheet_columns_pinned():
    df = flex_overage.build_direct_billing_worksheet([], 2026, 6, CFG)
    assert list(df.columns) == flex_overage.DIRECT_BILLING_WORKSHEET_COLUMNS


# ── build_partner_submission schema is the contract for ledger dedup ─────────
#
# Stage 3's _partner_block reads "QB Customer" and "Net Overage to Submit"
# from this DataFrame to fingerprint partner-submission rows into the dedup
# ledger. If either column name changes, every OnePlace clinic in a cycle
# would collide on a single fingerprint — corrupting the audit trail and
# silently allowing double-submission on a re-run.


def test_partner_submission_has_qb_customer_column():
    rows = [
        {"finance_company": "OnePlace", "clinic_name": "Partner Vet",
         "qb_name": "Partner Vet QB", "contract_oneplace": "OPC-1",
         "quarterly_threshold": 5700.0, "quarter_activity": 6500.0,
         "overage": 800.0},
    ]
    annotated = flex_overage.annotate_overages(
        rows, 2026, 6, dt.date(2026, 7, 3), CFG,  # before cutoff → partner
    )
    df = flex_overage.build_partner_submission(annotated, 2026, 6)
    assert "QB Customer" in df.columns
    assert "Net Overage to Submit" in df.columns
    assert df.iloc[0]["QB Customer"] == "Partner Vet QB"
    assert df.iloc[0]["Net Overage to Submit"] == 800.0


def test_partner_submission_first_column_is_finance_partner_not_customer():
    """Regression-pin: the column ORDER matters because earlier ledger code
    used `pdf.columns[0]` as a customer-name fallback. If "Customer" was ever
    missing (which it always is — the column is "QB Customer"), the fallback
    silently picked "Finance Partner", collapsing every clinic onto the
    string "OnePlace". This test pins the order so future schema edits don't
    bring that footgun back."""
    rows = [
        {"finance_company": "OnePlace", "clinic_name": "X",
         "qb_name": "X QB", "contract_oneplace": "OPC-1",
         "quarterly_threshold": 1.0, "quarter_activity": 10.0, "overage": 9.0},
    ]
    annotated = flex_overage.annotate_overages(rows, 2026, 6, dt.date(2026, 7, 3), CFG)
    df = flex_overage.build_partner_submission(annotated, 2026, 6)
    assert df.columns[0] == "Finance Partner"
    # And "Customer" is NOT a column — so any code probing for it must fall
    # back explicitly to "QB Customer", not to columns[0].
    assert "Customer" not in df.columns
