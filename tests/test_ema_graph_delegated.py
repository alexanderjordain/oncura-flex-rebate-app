"""Tests for delegated Graph (/me) send/create/cancel + the DelegatedBackend.

Token acquisition and HTTP are mocked, so these assert payload shape and the
attendee wiring without a Streamlit runtime or live sign-in.
"""
from __future__ import annotations

import datetime as dt
from unittest import mock

from core import ema_graph_delegated as deleg


class _Resp:
    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body or {}
        self.text = text

    def json(self):
        return self._json


def test_send_mail_posts_me_sendmail(monkeypatch):
    monkeypatch.setattr(deleg, "_token", lambda: "T")
    with mock.patch("requests.post", return_value=_Resp(202)) as p:
        ok, info = deleg.send_mail("Subj", "<p>hi</p>", "Clinic <c@x.com>")
    assert ok and info == "sent"
    url, kw = p.call_args[0][0], p.call_args[1]
    assert url.endswith("/me/sendMail")
    body = kw["json"]
    assert body["saveToSentItems"] is True
    assert body["message"]["body"]["contentType"] == "HTML"
    assert body["message"]["toRecipients"] == [
        {"emailAddress": {"address": "c@x.com", "name": "Clinic"}}]


def test_send_mail_not_connected(monkeypatch):
    monkeypatch.setattr(deleg, "_token", lambda: None)
    ok, info = deleg.send_mail("s", "<p>b</p>", "c@x.com")
    assert not ok and "not connected" in info.lower()


def test_create_event_posts_me_events_with_attendees(monkeypatch):
    monkeypatch.setattr(deleg, "_token", lambda: "T")
    resp = _Resp(201, {"id": "EVT9", "webLink": "https://outlook/EVT9"})
    with mock.patch("requests.post", return_value=resp) as p:
        ok, eid, link = deleg.create_event(
            "Call", "<p>call</p>", dt.datetime(2026, 7, 30, 10, 0),
            dt.datetime(2026, 7, 30, 10, 30), ["mark@oncurapartners.com", "c@x.com"])
    assert ok and eid == "EVT9"
    url, kw = p.call_args[0][0], p.call_args[1]
    assert url.endswith("/me/events")
    addrs = [a["emailAddress"]["address"] for a in kw["json"]["attendees"]]
    assert addrs == ["mark@oncurapartners.com", "c@x.com"]
    assert all(a["type"] == "required" for a in kw["json"]["attendees"])


def test_cancel_event_posts_me_cancel(monkeypatch):
    monkeypatch.setattr(deleg, "_token", lambda: "T")
    with mock.patch("requests.post", return_value=_Resp(202)) as p:
        ok, info = deleg.cancel_event("EVT9", comment="renewed")
    assert ok and info == "cancelled"
    assert p.call_args[0][0].endswith("/me/events/EVT9/cancel")
    assert p.call_args[1]["json"] == {"comment": "renewed"}


def test_delegated_backend_create_call_invites_organizer_and_clinic(monkeypatch):
    captured = {}

    def _fake_create_event(subject, html, start, end, attendees, **kw):
        captured["attendees"] = attendees
        return True, "EVT", "link"
    monkeypatch.setattr(deleg, "create_event", _fake_create_event)
    monkeypatch.setenv("EMA_ORGANIZER", "mark@oncurapartners.com")

    backend = deleg.DelegatedBackend()
    ok, eid, link = backend.create_call(
        "s", "<p>b</p>", dt.datetime(2026, 7, 30, 10, 0),
        dt.datetime(2026, 7, 30, 10, 30), "clinic@x.com")
    assert ok and eid == "EVT"
    assert captured["attendees"] == ["mark@oncurapartners.com", "clinic@x.com"]
