"""Tests for core/overage_ledger.py — CRUD, status transitions, lockout math."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from core import overage_ledger as ol


# ---------------------------------------------------------------------------
# Test scaffolding — swap store's load/save for in-memory dict per test
# ---------------------------------------------------------------------------

class _MemStore:
    def __init__(self):
        self.data: dict = {"version": 1, "entries": {}}

    def load(self, rel_path, default=None):
        return json.loads(json.dumps(self.data)), None  # deep copy

    def save(self, rel_path, data, message, sha=None, retries=3):
        self.data = json.loads(json.dumps(data))


@pytest.fixture
def mem(monkeypatch):
    m = _MemStore()
    monkeypatch.setattr(ol.store, "load_json", m.load)
    monkeypatch.setattr(ol.store, "save_json", m.save)
    return m


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def _base_record(**overrides):
    r = {
        "clinic": "Sun City Vet Surgery Center",
        "qb_customer": "Sun City Vet Surgery Center",
        "billing_month": "2026-05",
        "quarter_covered": "Q1 2026",
        "route": "direct",
        "gross_overage": 1250.00,
        "credit_applied": 0.00,
        "net_amount": 1250.00,
        "date_billed": "2026-05-15",
    }
    r.update(overrides)
    return r


def test_upsert_creates_new_entry(mem):
    eid = ol.upsert(_base_record())
    assert eid
    entries = ol.all_entries()
    assert len(entries) == 1
    assert entries[0]["clinic"] == "Sun City Vet Surgery Center"
    assert entries[0]["net_amount"] == 1250.00
    assert entries[0]["paid_at"] is None


def test_mark_paid_records_method_and_ref(mem):
    eid = ol.upsert(_base_record())
    ol.mark_paid(eid, paid_amount=1250.0, paid_date="2026-06-01",
                 method="authorize.net", txn_ref="60123456789")
    e = ol.get(eid)
    assert e["paid_method"] == "authorize.net"
    assert e["paid_ref"] == "60123456789"
    assert ol.status(e, dt.date(2026, 12, 1)) == ol.STATUS_PAID  # paid never locks out


def test_unmark_paid_clears_method_and_ref(mem):
    eid = ol.upsert(_base_record())
    ol.mark_paid(eid, 1250.0, "2026-06-01", method="authorize.net", txn_ref="X1")
    ol.unmark_paid(eid)
    e = ol.get(eid)
    assert not e["paid_method"] and not e["paid_ref"] and e["paid_at"] is None


def test_open_worklist_excludes_paid_and_sorts_by_urgency(mem):
    # Billed 2026-05-15 (older, more urgent) vs 2026-06-20 (newer)
    older = ol.upsert(_base_record(qb_customer="Older Clinic", billing_month="2026-05",
                                   date_billed="2026-05-15"))
    ol.upsert(_base_record(qb_customer="Newer Clinic", billing_month="2026-06",
                           date_billed="2026-06-20"))
    paid = ol.upsert(_base_record(qb_customer="Paid Clinic", billing_month="2026-04",
                                  date_billed="2026-04-10"))
    ol.mark_paid(paid, 1250.0, "2026-05-01", txn_ref="P1")
    wl = ol.open_worklist(dt.date(2026, 7, 1))
    names = [it["qb_customer"] for it in wl]
    assert "Paid Clinic" not in names          # paid excluded
    assert names == ["Older Clinic", "Newer Clinic"]  # soonest-to-lockout first
    assert all("days_until_lockout" in it and "status" in it for it in wl)


def test_upsert_updates_existing_by_natural_key(mem):
    eid1 = ol.upsert(_base_record(net_amount=1250.00))
    eid2 = ol.upsert(_base_record(net_amount=1500.00, notes="corrected"))
    assert eid1 == eid2
    assert len(ol.all_entries()) == 1
    assert ol.get(eid1)["net_amount"] == 1500.00


def test_upsert_preserves_paid_state_on_re_generation(mem):
    """If Stage 3 gets rerun after a bill was marked paid, the paid state
    must survive the re-upsert."""
    eid = ol.upsert(_base_record())
    ol.mark_paid(eid, paid_amount=1250.00, paid_date="2026-06-10", note="check 123")
    ol.upsert(_base_record(net_amount=1250.00, notes="rerun"))
    e = ol.get(eid)
    assert e["paid_at"] == "2026-06-10"
    assert e["paid_amount"] == 1250.00
    assert e["notes"] == "rerun"  # non-payment fields still update


def test_upsert_requires_qb_customer_and_billing_month(mem):
    with pytest.raises(ValueError, match="qb_customer"):
        ol.upsert(_base_record(qb_customer=""))
    with pytest.raises(ValueError, match="billing_month"):
        ol.upsert(_base_record(billing_month=""))


def test_upsert_rejects_bad_route(mem):
    with pytest.raises(ValueError, match="route"):
        ol.upsert(_base_record(route="bogus"))


def test_mark_paid_and_unmark(mem):
    eid = ol.upsert(_base_record())
    ol.mark_paid(eid, paid_amount=1250, paid_date="2026-06-01", note="ACH")
    e = ol.get(eid)
    assert e["paid_at"] == "2026-06-01"
    assert e["paid_amount"] == 1250
    assert e["paid_note"] == "ACH"
    assert ol.status(e, dt.date(2026, 9, 1)) == ol.STATUS_PAID

    ol.unmark_paid(eid)
    e = ol.get(eid)
    assert e["paid_at"] is None
    assert e["paid_amount"] is None


def test_mark_paid_rejects_bad_date(mem):
    eid = ol.upsert(_base_record())
    with pytest.raises(ValueError):
        ol.mark_paid(eid, paid_amount=100, paid_date="not-a-date")


def test_delete_removes_entry(mem):
    eid = ol.upsert(_base_record())
    assert len(ol.all_entries()) == 1
    ol.delete(eid)
    assert len(ol.all_entries()) == 0


# ---------------------------------------------------------------------------
# Status / lockout math
# ---------------------------------------------------------------------------

def test_status_open_same_month():
    e = _base_record(date_billed="2026-05-15")
    assert ol.status(e, dt.date(2026, 5, 20)) == ol.STATUS_OPEN


def test_status_warning_at_2_months(mem):
    e = _base_record(date_billed="2026-05-15")
    # 2 calendar months later = 2026-07-15
    assert ol.status(e, dt.date(2026, 7, 15)) == ol.STATUS_WARNING
    # day before 2 months = still open
    assert ol.status(e, dt.date(2026, 7, 14)) == ol.STATUS_OPEN


def test_status_locked_out_at_3_months(mem):
    e = _base_record(date_billed="2026-05-15")
    # exactly 3 months = locked out
    assert ol.status(e, dt.date(2026, 8, 15)) == ol.STATUS_LOCKED_OUT
    # day before = warning
    assert ol.status(e, dt.date(2026, 8, 14)) == ol.STATUS_WARNING


def test_status_paid_overrides_age(mem):
    e = _base_record(date_billed="2026-01-01")
    e["paid_at"] = "2026-06-15"
    e["paid_amount"] = 1250
    # 6 months old but paid -> STATUS_PAID, never locked out
    assert ol.status(e, dt.date(2026, 12, 31)) == ol.STATUS_PAID


def test_days_until_lockout_ranges(mem):
    e = _base_record(date_billed="2026-05-15")
    # today = billing + 1 day; lockout = billing + 3 months = 2026-08-15
    assert ol.days_until_lockout(e, dt.date(2026, 5, 16)) == (dt.date(2026, 8, 15) - dt.date(2026, 5, 16)).days
    # past due
    assert ol.days_until_lockout(e, dt.date(2026, 9, 1)) < 0
    # paid entry -> None
    e["paid_at"] = "2026-06-01"
    assert ol.days_until_lockout(e, dt.date(2026, 9, 1)) is None


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

def test_locked_out_clinics_dedupes(mem):
    ol.upsert(_base_record(qb_customer="Clinic A", billing_month="2026-01",
                            date_billed="2026-01-15"))
    ol.upsert(_base_record(qb_customer="Clinic A", billing_month="2026-02",
                            date_billed="2026-02-15"))
    ol.upsert(_base_record(qb_customer="Clinic B", billing_month="2026-06",
                            date_billed="2026-06-15"))
    # By Aug 1: Jan (~6.5mo) locked out, Feb (~5.5mo) locked out, Jun (~1.5mo) open.
    out = ol.locked_out_clinics(dt.date(2026, 8, 1))
    assert out == {"Clinic A"}


def test_summarize_bucket_math(mem):
    ol.upsert(_base_record(qb_customer="A", billing_month="2026-05",
                            date_billed="2026-05-01", net_amount=500))
    ol.upsert(_base_record(qb_customer="B", billing_month="2026-02",
                            date_billed="2026-02-01", net_amount=1000))
    ol.upsert(_base_record(qb_customer="C", billing_month="2026-01",
                            date_billed="2026-01-01", net_amount=300))
    eid_paid = ol.upsert(_base_record(qb_customer="D", billing_month="2026-04",
                                       date_billed="2026-04-01", net_amount=250))
    ol.mark_paid(eid_paid, 250, "2026-04-15")

    today = dt.date(2026, 6, 15)
    s = ol.summarize(today)
    # A billed 5/1: 1.5mo -> open
    # B billed 2/1: 4.5mo -> locked_out
    # C billed 1/1: 5.5mo -> locked_out
    # D paid
    assert s["counts"][ol.STATUS_OPEN] == 1
    assert s["counts"][ol.STATUS_LOCKED_OUT] == 2
    assert s["counts"][ol.STATUS_PAID] == 1
    assert s["total_open"] == 500 + 1000 + 300
    assert s["total_locked_out"] == 1000 + 300
    assert s["total_collected"] == 250


# ---------------------------------------------------------------------------
# record_from_annotation adapter
# ---------------------------------------------------------------------------

def test_record_from_annotation_maps_annotate_overages_shape(mem):
    ann_row = {
        "clinic_name": "Judd Veterinary Clinic",
        "qb_name": "Heartland Vet Partners DBA Judd Veterinary Clinic",
        "route": "direct",
        "overage": 800.00,
        "credit_applied": 100.00,
        "net_overage": 700.00,
        "escalation_flag": False,
    }
    eid = ol.record_from_annotation(
        ann_row,
        billing_month="2026-05",
        quarter_covered="Q1 2026",
        date_billed="2026-05-15",
    )
    e = ol.get(eid)
    assert e["clinic"] == "Judd Veterinary Clinic"
    assert e["qb_customer"].startswith("Heartland Vet Partners")
    assert e["gross_overage"] == 800.00
    assert e["credit_applied"] == 100.00
    assert e["net_amount"] == 700.00


def test_record_from_annotation_skips_zero_net(mem):
    ann_row = {"clinic_name": "X", "qb_name": "X", "route": "direct",
               "overage": 100, "credit_applied": 100, "net_overage": 0}
    assert ol.record_from_annotation(
        ann_row, billing_month="2026-05", quarter_covered="Q1 2026",
        date_billed="2026-05-15") is None
    assert ol.all_entries() == []


def test_record_batch_returns_only_written_ids(mem):
    rows = [
        {"clinic_name": "A", "qb_name": "A", "route": "direct",
         "overage": 500, "credit_applied": 0, "net_overage": 500},
        {"clinic_name": "B", "qb_name": "B", "route": "partner",
         "overage": 100, "credit_applied": 100, "net_overage": 0},  # skipped
        {"clinic_name": "C", "qb_name": "C", "route": "missed_cutoff",
         "overage": 200, "credit_applied": 0, "net_overage": 200},
    ]
    ids = ol.record_batch(rows, billing_month="2026-05",
                          quarter_covered="Q1 2026",
                          date_billed="2026-05-15")
    assert len(ids) == 2
    assert len(ol.all_entries()) == 2
