"""Tests for the EMA outreach ledger's functional core (no storage)."""
from __future__ import annotations

import datetime as dt

from core import ema_ledger


def _seed():
    data = ema_ledger._empty()
    ema_ledger.append_outreach(data, {
        "clinic_id": "AH001", "clinic_name": "Abell", "mode": "expired",
        "contacted_at": "2026-07-20T09:00:00", "graph_event_id": "E1"})
    return data


def test_has_recent_blocks_within_window():
    data = _seed()
    today = dt.date(2026, 7, 22)
    assert ema_ledger.has_recent(data, "AH001", today, within_days=30) is True
    assert ema_ledger.has_recent(data, "AH001", today, within_days=1) is False  # 2 days ago
    assert ema_ledger.has_recent(data, "OTHER", today, within_days=30) is False


def test_renewed_row_does_not_block_new_cycle():
    data = _seed()
    ema_ledger.set_status(data, "AH001", "renewed")
    today = dt.date(2026, 7, 22)
    # a fresh cycle should be allowed once the prior outreach resolved
    assert ema_ledger.has_recent(data, "AH001", today, within_days=365) is False


def test_latest_open_picks_most_recent_open_row():
    data = _seed()
    ema_ledger.append_outreach(data, {
        "clinic_id": "AH001", "clinic_name": "Abell", "mode": "expired",
        "contacted_at": "2026-07-21T09:00:00", "graph_event_id": "E2"})
    row = ema_ledger.latest_open(data, "AH001")
    assert row["graph_event_id"] == "E2"


def test_latest_open_none_when_all_resolved():
    data = _seed()
    ema_ledger.set_status(data, "AH001", "cancelled")
    assert ema_ledger.latest_open(data, "AH001") is None


def test_set_status_only_open_by_default():
    data = _seed()
    ema_ledger.append_outreach(data, {
        "clinic_id": "AH001", "contacted_at": "2026-07-21T09:00:00", "status": "renewed"})
    changed = ema_ledger.set_status(data, "AH001", "cancelled")
    assert changed == 1  # only the open row flipped, the renewed one untouched
    statuses = sorted(r["status"] for r in data["outreach"] if r["clinic_id"] == "AH001")
    assert statuses == ["cancelled", "renewed"]


def test_append_defaults_status_open():
    data = ema_ledger._empty()
    ema_ledger.append_outreach(data, {"clinic_id": "X", "contacted_at": "2026-07-22T00:00:00"})
    assert data["outreach"][0]["status"] == "open"
