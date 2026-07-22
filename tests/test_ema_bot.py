"""Tests for the shared EMA bot orchestration (CLI + app button use this)."""
from __future__ import annotations

import datetime as dt

from core import ema_bot, ema_ledger


def _plan(cid, email="c@x.com"):
    return {
        "clinic": f"Clinic {cid}", "clinic_id": cid, "state": "TX", "email": email,
        "expiry": "2026-06-01", "status": "expired", "call_date": "2026-07-23",
        "call_time": "10:00 AM ET", "call_start": "2026-07-23T10:00:00",
        "call_end": "2026-07-23T10:30:00", "payment_link": "https://pay/x",
        "subject": "subj", "html": "<p>hi</p>", "event_subject": "evt",
        "event_html": "<p>evt</p>",
    }


def test_plan_batch_filters_cooldown_no_email_then_caps(monkeypatch):
    plans = [_plan("A"), _plan("B", email=""), _plan("C"), _plan("D")]
    monkeypatch.setattr(ema_bot.ema_outreach, "build_plan",
                        lambda **kw: plans)
    # ledger says A was contacted today -> cooldown skip
    data = ema_ledger._empty()
    ema_ledger.append_outreach(data, {"clinic_id": "A", "contacted_at": "2026-07-22T09:00:00"})
    monkeypatch.setattr(ema_bot.ema_ledger, "load", lambda: (data, "SHA"))

    b = ema_bot.plan_batch(mode="expired", limit=1, today=dt.date(2026, 7, 22))
    assert b["candidates"] == 4
    assert [p["clinic_id"] for p in b["eligible"]] == ["C", "D"]      # A cooldown, B no email
    assert [p["clinic_id"] for p in b["capped"]] == ["C"]            # cap=1
    reasons = {p["clinic_id"]: p["skip_reason"] for p in b["skipped"]}
    assert reasons == {"A": "recent contact (cooldown)", "B": "no email on file"}
    assert b["sha"] == "SHA"


def test_send_batch_creates_event_emails_and_records(monkeypatch):
    monkeypatch.delenv("HUBSPOT_TOKEN", raising=False)
    monkeypatch.setattr(ema_bot.ema_graph, "create_event",
                        lambda *a, **k: (True, "EVT1", "https://outlook/EVT1"))
    monkeypatch.setattr(ema_bot.ema_graph, "send_mail", lambda *a, **k: (True, "sent"))

    data = ema_ledger._empty()
    results = ema_bot.send_batch([_plan("C")], data)
    assert len(results) == 1 and results[0]["event_ok"] and results[0]["mail_ok"]
    row = data["outreach"][0]
    assert row["clinic_id"] == "C" and row["graph_event_id"] == "EVT1"
    assert row["status"] == "open"


def test_reconcile_cancels_call_and_marks_renewed(monkeypatch):
    data = ema_ledger._empty()
    ema_ledger.append_outreach(data, {
        "clinic_id": "C", "clinic_name": "Clinic C", "contacted_at": "2026-07-20T09:00:00",
        "graph_event_id": "EVT1"})
    saved = {}
    monkeypatch.setattr(ema_bot.ema_ledger, "load", lambda: (data, "SHA"))
    monkeypatch.setattr(ema_bot.ema_ledger, "save",
                        lambda d, sha, message: saved.update(sha=sha, message=message))
    monkeypatch.setattr(ema_bot.ema_graph, "is_configured", lambda: True)
    monkeypatch.setattr(ema_bot.ema_graph, "cancel_event", lambda *a, **k: (True, "cancelled"))
    monkeypatch.delenv("HUBSPOT_TOKEN", raising=False)

    res = ema_bot.reconcile(["C", "UNKNOWN"])
    assert res[0]["status"] == "renewed" and res[0]["cancel"] == "cancelled"
    assert res[1]["status"] == "no open outreach"
    assert data["outreach"][0]["status"] == "renewed"
    assert saved["sha"] == "SHA"
