"""Tests for core.qbo_reconcile — the read-only QBO Transaction Report parser
and per-clinic comparison used by Review & Verify."""
import pandas as pd
import pytest

from core import qbo_reconcile as q

# A miniature header-less sheet shaped like the real QBO "Transaction Report":
# title rows, a header row, an account-group header, then transaction rows.
_HEADER = [None, "Transaction date", "Transaction type", "Num", "Name",
           "Description", "Account Name", "Item split account", "Amount", "Balance"]


def _tx(date, ttype, num, name, desc, amount):
    return [None, date, ttype, num, name, desc, "4320 Flex Discount",
            "Accounts Receivable", amount, 0]


def _sheet(rows):
    data = [
        ["Oncura Partners Diagnostics, LLC"] + [None] * 9,
        ["Transaction Report"] + [None] * 9,
        ["January 1-July 15, 2026"] + [None] * 9,
        [None] * 10,
        _HEADER,
        ["4320 Flex Discount"] + [None] * 9,  # account-group header row
    ] + rows
    return pd.DataFrame(data)


QUARTER = [(2026, 4), (2026, 5), (2026, 6)]


def _demo():
    return _sheet([
        _tx("01/31/2026", "Credit Memo", "1", "Cedar Veterinary Clinic",
            "Flex Credits for January 2026", -100.0),          # out of quarter
        _tx("04/30/2026", "Credit Memo", "2", "Cedar Veterinary Clinic",
            "Flex Credits for April  2026", -100.0),           # doubled space
        _tx("05/31/2026", "Credit Memo", "3", "Cedar Veterinary Clinic",
            "Flex Credits for May 2026", -100.0),
        _tx("05/31/2026", "Credit Memo", "4", "Cedar Veterinary Clinic",
            "Flex Credits for May 2026", 0.0),                 # $0 duplicate
        _tx("06/30/2026", "Credit Memo", "5", "Cedar Veterinary Clinic",
            "Flex Credits for June 2026", -100.0),
        _tx("06/30/2026", "Invoice", "6", "Cedar Veterinary Clinic",
            "Telemedicine-Unused Flex -April May June-2026", 250.0),
        _tx("03/31/2026", "Invoice", "7", "Cedar Veterinary Clinic",
            "Telemedicine-Unused Flex -Jan-Feb-Mar-2026", 999.0),  # prior quarter
        _tx("06/30/2026", "Credit Memo", "8", "Memorial Clinic",
            "Flex Credits for June 2026", -50.0),
    ])


def test_parse_finds_header_and_rows():
    df = q.parse_report(_demo())
    assert list(df.columns) == q._OUT_COLUMNS
    assert len(df) == 8
    assert set(df["group"]) == {"4320 Flex Discount"}
    assert (df["nname"] == "cedar veterinary clinic").sum() == 7


def test_parse_rejects_non_qbo_sheet():
    with pytest.raises(ValueError):
        q.parse_report(pd.DataFrame([["something", "else"], [1, 2]]))


def test_parse_empty_returns_typed_frame():
    df = q.parse_report(_sheet([]))
    assert df.empty
    assert list(df.columns) == q._OUT_COLUMNS


@pytest.mark.parametrize("desc,expected", [
    ("Flex Credits for April 2026", (2026, 4)),
    ("Flex Credits for April  2026", (2026, 4)),   # doubled space
    ("Flex Credit for JANUARY 2026", (2026, 1)),   # case-insensitive
    ("Unused Flex Credits", None),
    (None, None),
])
def test_coverage_month(desc, expected):
    assert q.coverage_month(desc) == expected


def test_clinic_summary_counts_nonzero_and_flags_zero():
    df = q.parse_report(_demo())
    s = q.clinic_summary(df, "Cedar Veterinary Clinic", QUARTER, 2026, 6)
    assert s["matched"] is True
    assert s["cm"]["count"] == 3               # Apr, May, Jun (Jan excluded, $0 excluded)
    assert s["cm"]["months"] == [4, 5, 6]
    assert s["cm"]["zero_count"] == 1
    assert s["cm"]["total"] == 300.0           # reported positive


def test_clinic_summary_recap_window():
    df = q.parse_report(_demo())
    s = q.clinic_summary(df, "Cedar Veterinary Clinic", QUARTER, 2026, 6)
    # Only the 06/30 invoice is in the quarter-end window; the 03/31 one is not.
    assert s["recap"]["count"] == 1
    assert s["recap"]["total"] == 250.0


def test_clinic_summary_group_pooling():
    df = q.parse_report(_demo())
    s = q.clinic_summary(df, ["Cedar Veterinary Clinic", "Memorial Clinic"], QUARTER, 2026, 6)
    assert s["cm"]["count"] == 4               # 3 Cedar + 1 Memorial
    assert s["cm"]["total"] == 350.0


def test_clinic_summary_unmatched_name():
    df = q.parse_report(_demo())
    s = q.clinic_summary(df, "Not A Clinic", QUARTER, 2026, 6)
    assert s["matched"] is False
    assert s["cm"]["count"] == 0
