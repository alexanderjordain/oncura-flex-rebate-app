"""Tests for the refactored EMA outreach copy + plan builder.

The Calendly booking link is gone (the Graph calendar invite is the booking);
the email must instead reference the invite and keep the skip-to-renew payment
path prominent. build_plan must emit the call start/end datetimes and the
calendar-invite body the live runner needs.
"""
from __future__ import annotations

import datetime as dt

from core import ema_outreach


def test_email_copy_upcoming_has_call_and_payment_no_calendly():
    subj, plain, html = ema_outreach.email_copy(
        "Test Clinic", dt.date(2026, 8, 15), "https://pay.link/abc",
        dt.date(2026, 8, 1), "10:00 AM", status="upcoming")
    assert "renewal" in subj.lower()
    assert "calendly" not in (plain + html).lower()
    assert "https://pay.link/abc" in plain and "https://pay.link/abc" in html
    assert "August 01, 2026 at 10:00 AM ET" in plain
    assert "renewal notice" in plain.lower()
    assert "calendar invitation" in plain.lower()


def test_email_copy_expired_variant():
    subj, plain, _ = ema_outreach.email_copy(
        "Lapsed Clinic", dt.date(2026, 6, 1), "https://pay.link/x",
        dt.date(2026, 8, 1), "1:00 PM", status="expired")
    assert "lapsed" in subj.lower()
    assert "expired on June 01, 2026" in plain
    assert "resumes immediately" in plain


def test_slot_datetimes_parses_et_suffix_and_duration():
    start, end = ema_outreach.slot_datetimes(dt.date(2026, 7, 30), "1:30 PM ET")
    assert start == dt.datetime(2026, 7, 30, 13, 30)
    assert end == start + dt.timedelta(minutes=ema_outreach.CALL_DURATION_MIN)


def test_event_body_carries_skip_to_renew():
    html = ema_outreach.event_body_html(
        "Test Clinic", dt.date(2026, 8, 15), "https://pay.link/abc", status="upcoming")
    assert "https://pay.link/abc" in html
    assert ema_outreach.CALLER in html


def _fake_clinics():
    return [
        {"clinic": "Alpha AH", "clinic_id": "AL001", "state": "TX", "city": "X",
         "hardware_end": dt.date(2026, 6, 1), "hardware_active": False,
         "support_end": None, "admin_email": "alpha@x.com", "billing_email": ""},
        {"clinic": "Beta VH", "clinic_id": "BE002", "state": "CA", "city": "Y",
         "hardware_end": dt.date(2026, 6, 20), "hardware_active": False,
         "support_end": None, "admin_email": "", "billing_email": "beta@y.com"},
    ]


def test_build_plan_expired_emits_runner_fields(monkeypatch):
    monkeypatch.setattr(ema_outreach.ema_renewals, "fetch_all_ema", lambda auth=None: _fake_clinics())
    monkeypatch.setenv("EMA_PAYMENT_LINK", "https://pay.link/universal")
    plans = ema_outreach.build_plan(mode="expired", today=dt.date(2026, 7, 22))
    assert len(plans) == 2
    p = plans[0]
    for key in ("call_start", "call_end", "event_subject", "event_html", "subject",
                "html", "email", "payment_link", "clinic_id"):
        assert key in p
    # call_start/end parse as datetimes and the payment link propagates
    dt.datetime.fromisoformat(p["call_start"])
    dt.datetime.fromisoformat(p["call_end"])
    assert p["payment_link"] == "https://pay.link/universal"
    assert p["email"] in ("alpha@x.com", "beta@y.com")
