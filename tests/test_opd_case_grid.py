"""Coverage for opd_adapter._normalize_case_grid + STAT $125 implicit add-on.

The implicit-$125 rule is the most-litigated logic per CLAUDE.md ("Key
decisions"); silent double-count would inflate every STAT case's rebate base.
"""
import pandas as pd

from core import opd_adapter


PRICE_TABLE = {
    "stat_fee": 125.0,
    "services": {
        "Basic Abdominal Ultrasound": {"price": 135.0, "category": "ultrasound"},
        "Radiograph - Abdominal Thoracic & Orthopedic": {"price": 52.0, "category": "rads"},
        "STAT Sonographer Assistance Fee": {"price": 125.0, "category": "stat"},
        "Comprehensive Abdominal Consult": {"price": 210.0, "category": "ultrasound"},
    },
}


def _case_row(clinic, services, priority="ROUTINE", date="2026-05-15"):
    return {
        "Clinic": clinic,
        "Case ID": "C001",
        "Submitted": date,
        "Finalized Date": date,
        "Priority": priority,
        "Services": services,
    }


def test_simple_case_explodes_to_priced_lines():
    df = pd.DataFrame([_case_row("Acme Vet", "Basic Abdominal Ultrasound, Radiograph - Abdominal Thoracic & Orthopedic")])
    out = opd_adapter._normalize_case_grid(df, item_map={}, price_table=PRICE_TABLE)
    assert len(out) == 2
    by_svc = {r["item_desc"]: r for _, r in out.iterrows()}
    assert by_svc["Basic Abdominal Ultrasound"]["amount"] == 135.0
    assert by_svc["Basic Abdominal Ultrasound"]["category"] == "ultrasound"
    assert by_svc["Radiograph - Abdominal Thoracic & Orthopedic"]["amount"] == 52.0
    assert by_svc["Radiograph - Abdominal Thoracic & Orthopedic"]["category"] == "rads"


def test_stat_priority_adds_implicit_125_when_no_stat_line():
    df = pd.DataFrame([_case_row(
        "Acme Vet",
        "Basic Abdominal Ultrasound",
        priority="STAT",
    )])
    out = opd_adapter._normalize_case_grid(df, item_map={}, price_table=PRICE_TABLE)
    by_svc = {r["item_desc"]: r for _, r in out.iterrows()}
    assert "STAT Sonographer Assistance Fee" in by_svc
    assert by_svc["STAT Sonographer Assistance Fee"]["amount"] == 125.0
    assert by_svc["STAT Sonographer Assistance Fee"]["category"] == "stat"
    # Total: ultrasound 135 + STAT 125 = 260
    assert round(out["amount"].sum(), 2) == 260.00


def test_stat_priority_does_not_double_count_when_stat_line_present():
    """If the OPD case-grid Services string ALREADY includes a STAT service, don't synthesize another."""
    df = pd.DataFrame([_case_row(
        "Acme Vet",
        "Basic Abdominal Ultrasound, STAT Sonographer Assistance Fee",
        priority="STAT",
    )])
    out = opd_adapter._normalize_case_grid(df, item_map={}, price_table=PRICE_TABLE)
    stat_rows = out[out["item_desc"] == "STAT Sonographer Assistance Fee"]
    assert len(stat_rows) == 1                              # not duplicated
    assert round(stat_rows.iloc[0]["amount"], 2) == 125.0
    # Total: 135 + 125 = 260, NOT 135 + 125 + 125 = 385
    assert round(out["amount"].sum(), 2) == 260.00


def test_routine_priority_no_stat_addon():
    df = pd.DataFrame([_case_row(
        "Acme Vet",
        "Basic Abdominal Ultrasound",
        priority="ROUTINE",
    )])
    out = opd_adapter._normalize_case_grid(df, item_map={}, price_table=PRICE_TABLE)
    assert "STAT Sonographer Assistance Fee" not in set(out["item_desc"])
    assert round(out["amount"].sum(), 2) == 135.00


def test_unknown_service_falls_to_zero_other():
    """A service name not in the price table — capture the row but $0 and category=other."""
    df = pd.DataFrame([_case_row(
        "Acme Vet",
        "Basic Abdominal Ultrasound, Made-Up Service",
    )])
    out = opd_adapter._normalize_case_grid(df, item_map={}, price_table=PRICE_TABLE)
    by_svc = {r["item_desc"]: r for _, r in out.iterrows()}
    assert by_svc["Made-Up Service"]["amount"] == 0.0
    assert by_svc["Made-Up Service"]["category"] == "other"


def test_whitespace_normalization_in_services_string():
    """Extra spaces / tabs / line wraps in the comma-separated Services string
    shouldn't prevent a match against the price table."""
    df = pd.DataFrame([_case_row(
        "Acme Vet",
        "  Basic   Abdominal\tUltrasound  ,  Radiograph - Abdominal Thoracic & Orthopedic  ",
    )])
    out = opd_adapter._normalize_case_grid(df, item_map={}, price_table=PRICE_TABLE)
    # Both should match and be priced normally.
    by_svc = {r["item_desc"]: r for _, r in out.iterrows()}
    assert by_svc["Basic Abdominal Ultrasound"]["amount"] == 135.0
    assert by_svc["Radiograph - Abdominal Thoracic & Orthopedic"]["amount"] == 52.0


def test_empty_services_string_returns_empty():
    df = pd.DataFrame([_case_row("Acme Vet", "")])
    out = opd_adapter._normalize_case_grid(df, item_map={}, price_table=PRICE_TABLE)
    assert len(out) == 0


def test_clinic_name_preserved_in_output():
    df = pd.DataFrame([_case_row(
        "Acme Vet",
        "Basic Abdominal Ultrasound",
    )])
    out = opd_adapter._normalize_case_grid(df, item_map={}, price_table=PRICE_TABLE)
    assert out.iloc[0]["clinic"] == "Acme Vet"


def test_multi_case_aggregation_with_mixed_priorities():
    """Two cases at the same clinic: one ROUTINE, one STAT. Stat fee added only to the STAT case."""
    df = pd.DataFrame([
        _case_row("Acme Vet", "Basic Abdominal Ultrasound", priority="ROUTINE"),
        _case_row("Acme Vet", "Basic Abdominal Ultrasound", priority="STAT"),
    ])
    out = opd_adapter._normalize_case_grid(df, item_map={}, price_table=PRICE_TABLE)
    # 2 ultrasound rows + 1 STAT-fee row = 3 rows total
    assert len(out) == 3
    assert round(out["amount"].sum(), 2) == 135.0 + 135.0 + 125.0   # 395


# ── detect_upload_date_coverage (Guardrail A) ────────────────────────────────


def test_coverage_returns_none_when_no_date_column():
    df = pd.DataFrame([{"Clinic": "X", "Amount": 100}])
    out = opd_adapter.detect_upload_date_coverage(df, profile="odata")
    assert out["date_col"] is None


def test_coverage_full_quarter_invoice_export():
    """A proper full-quarter OData Invoice export spans 3 calendar months."""
    df = pd.DataFrame({
        "InvoiceDate": ["2026-04-01T04:00:00Z",  # March bill (rollover)
                        "2026-05-01T04:00:00Z",  # April bill
                        "2026-06-01T04:00:00Z"], # May bill
        "ClinicName": ["A", "B", "C"],
    })
    out = opd_adapter.detect_upload_date_coverage(df, profile="odata")
    assert out["date_col"] == "InvoiceDate"
    assert len(out["months_covered"]) == 3


def test_coverage_may_only_upload_is_partial():
    """The exact failure mode that caused JW's 2026-06-09 incident:
    file contains only the May rollover (UTC dates land in June), so the
    months_covered set has just one element instead of three."""
    df = pd.DataFrame({
        "InvoiceDate": ["2026-06-01T04:00:00Z", "2026-06-01T04:00:05Z"],
        "ClinicName": ["A", "B"],
    })
    out = opd_adapter.detect_upload_date_coverage(df, profile="odata")
    assert len(out["months_covered"]) == 1
    assert out["span_days"] <= 1  # all on same day


def test_coverage_case_grid_uses_finalized_date():
    df = pd.DataFrame({
        "Finalized Date": ["2026-03-15", "2026-04-20", "2026-05-30"],
        "Clinic": ["A", "B", "C"],
    })
    out = opd_adapter.detect_upload_date_coverage(df, profile="case_grid")
    assert out["date_col"] == "Finalized Date"
    assert len(out["months_covered"]) == 3


def test_coverage_handles_empty_dates_gracefully():
    """All-NaN date column shouldn't crash — return an empty coverage report."""
    df = pd.DataFrame({"InvoiceDate": [None, None, None], "ClinicName": ["A", "B", "C"]})
    out = opd_adapter.detect_upload_date_coverage(df, profile="odata")
    assert out["months_covered"] == set()
    assert out["span_days"] == 0
