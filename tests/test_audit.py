"""Audit manifest: hash stability, integrity verification, entry filtering."""
from __future__ import annotations

import io

import pandas as pd
import pytest

from core import audit


def test_output_hash_df_stable_and_distinguishes():
    df1 = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    df2 = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    df3 = pd.DataFrame({"a": [1, 2, 4], "b": ["x", "y", "z"]})

    assert audit.output_hash_df(df1) == audit.output_hash_df(df2)
    assert audit.output_hash_df(df1) != audit.output_hash_df(df3)


def test_output_hash_empty_df_is_empty_string_hash():
    df = pd.DataFrame()
    h = audit.output_hash_df(df)
    assert len(h) == 64  # sha256 hex of empty string
    assert h == audit.output_hash_df(None)


def test_entry_hash_excludes_self():
    """The entry_hash field must NOT participate in its own computation."""
    entry = {
        "id": "test-id",
        "timestamp": "2026-05-29T00:00:00",
        "cycle_type": "stage1_finance_payment",
        "approver": "alex",
        "year": 2026, "month": 5,
        "params": {"company": "OnePlace"},
        "source_file": None,
        "outputs": [],
        "note": "",
    }
    h1 = audit._entry_hash(entry)
    entry_with_hash = {**entry, "entry_hash": h1}
    h2 = audit._entry_hash(entry_with_hash)
    assert h1 == h2  # adding the hash field doesn't change the recomputed hash


def test_entry_hash_changes_on_any_field_mutation():
    base = {
        "id": "x", "timestamp": "t", "cycle_type": "stage1_finance_payment",
        "approver": "alex", "year": 2026, "month": 5, "params": {}, "source_file": None,
        "outputs": [], "note": "",
    }
    h_base = audit._entry_hash(base)
    assert h_base != audit._entry_hash({**base, "approver": "tanya"})
    assert h_base != audit._entry_hash({**base, "params": {"x": 1}})
    assert h_base != audit._entry_hash({**base, "outputs": [{"name": "f", "row_count": 1}]})
    assert h_base != audit._entry_hash({**base, "month": 6})


def test_verify_integrity_catches_tampering(monkeypatch):
    """If someone edits an entry without recomputing entry_hash, verify_integrity flags it."""
    tampered_entry = {
        "id": "tampered-1", "timestamp": "t", "cycle_type": "stage1_finance_payment",
        "approver": "alex", "year": 2026, "month": 5, "params": {}, "source_file": None,
        "outputs": [], "note": "",
    }
    tampered_entry["entry_hash"] = audit._entry_hash(tampered_entry)
    # Now tamper with the entry post-hash
    tampered_entry["approver"] = "someone_else"

    good_entry = {
        "id": "good-1", "timestamp": "t2", "cycle_type": "stage1_finance_payment",
        "approver": "alex", "year": 2026, "month": 5, "params": {}, "source_file": None,
        "outputs": [], "note": "",
    }
    good_entry["entry_hash"] = audit._entry_hash(good_entry)

    fake = {"version": 1, "entries": [tampered_entry, good_entry]}
    monkeypatch.setattr(audit, "_load", lambda: (fake, None))
    ok, tampered_ids = audit.verify_integrity()
    assert not ok
    assert tampered_ids == ["tampered-1"]


def test_list_entries_filters_by_cycle_type(monkeypatch):
    fake_entries = [
        {"id": "1", "timestamp": "2026-05-01", "cycle_type": "stage1_finance_payment", "approver": "alex"},
        {"id": "2", "timestamp": "2026-05-02", "cycle_type": "stage2_credit_memo", "approver": "tanya"},
        {"id": "3", "timestamp": "2026-05-03", "cycle_type": "stage1_finance_payment", "approver": "alex"},
    ]
    monkeypatch.setattr(audit, "_load", lambda: ({"version": 1, "entries": fake_entries}, None))

    all_entries = audit.list_entries()
    assert len(all_entries) == 3
    # Most-recent-first ordering
    assert all_entries[0]["id"] == "3"

    stage1 = audit.list_entries(cycle_type="stage1_finance_payment")
    assert [e["id"] for e in stage1] == ["3", "1"]

    limited = audit.list_entries(limit=1)
    assert len(limited) == 1
    assert limited[0]["id"] == "3"


def test_summary_aggregates(monkeypatch):
    fake_entries = [
        {"id": "1", "timestamp": "2026-05-01", "cycle_type": "stage1_finance_payment", "approver": "alex"},
        {"id": "2", "timestamp": "2026-05-10", "cycle_type": "stage1_finance_payment", "approver": "tanya"},
        {"id": "3", "timestamp": "2026-05-15", "cycle_type": "stage2_credit_memo", "approver": "alex"},
    ]
    monkeypatch.setattr(audit, "_load", lambda: ({"version": 1, "entries": fake_entries}, None))
    s = audit.summary()
    assert s["entry_count"] == 3
    assert s["by_type"] == {"stage1_finance_payment": 2, "stage2_credit_memo": 1}
    assert s["by_approver"] == {"alex": 2, "tanya": 1}
    assert s["latest_timestamp"] == "2026-05-15"
