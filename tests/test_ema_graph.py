"""Tests for the app-only Microsoft Graph client (EMA renewal bot).

No live calls: requests.post and the token acquisition are mocked, so these
assert the *shape* of what we'd send to Graph — the endpoint, the app-only
send-as mailbox, the HTML body, recipient parsing, and that a calendar event
carries the clinic as a required attendee (so Graph mails the invite).
"""
from __future__ import annotations

import datetime as dt
from unittest import mock

from core import ema_graph


class _Resp:
    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body or {}
        self.text = text

    def json(self):
        return self._json


# ── helpers ───────────────────────────────────────────────────────────────────


def test_recips_parses_named_bare_and_lists():
    assert ema_graph._recips("Dr. Vet <v@x.com>") == [
        {"emailAddress": {"address": "v@x.com", "name": "Dr. Vet"}}]
    assert ema_graph._recips("v@x.com") == [{"emailAddress": {"address": "v@x.com"}}]
    assert ema_graph._recips(["a@x.com", "", None]) == [{"emailAddress": {"address": "a@x.com"}}]
    assert ema_graph._recips(None) == []


def test_graph_dt_is_naive_wallclock():
    d = dt.datetime(2026, 7, 30, 10, 0, 0)
    assert ema_graph._graph_dt(d) == "2026-07-30T10:00:00"


def test_is_configured_false_without_env(monkeypatch):
    for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert ema_graph.is_configured() is False


# ── send_mail ───────────────────────────────────────────────────────────────


def test_send_mail_posts_as_sender_with_html():
    with mock.patch.object(ema_graph.requests, "post", return_value=_Resp(202)) as p:
        ok, info = ema_graph.send_mail(
            "mark@oncurapartners.com", "Subj", "<p>hi</p>",
            "Clinic <c@x.com>", cc="mgr@x.com", token="T")
    assert ok and info == "sent"
    url, kw = p.call_args[0][0], p.call_args[1]
    assert url.endswith("/users/mark@oncurapartners.com/sendMail")
    body = kw["json"]
    assert body["saveToSentItems"] is True
    assert body["message"]["body"]["contentType"] == "HTML"
    assert body["message"]["toRecipients"] == [
        {"emailAddress": {"address": "c@x.com", "name": "Clinic"}}]
    assert body["message"]["ccRecipients"] == [{"emailAddress": {"address": "mgr@x.com"}}]


def test_send_mail_reports_graph_error():
    with mock.patch.object(ema_graph.requests, "post", return_value=_Resp(403, text="Forbidden")):
        ok, info = ema_graph.send_mail("m@x.com", "s", "<p>b</p>", "c@x.com", token="T")
    assert not ok and "403" in info


# ── create_event ──────────────────────────────────────────────────────────────


def test_create_event_invites_clinic_as_required_attendee():
    resp = _Resp(201, {"id": "EVT123", "webLink": "https://outlook/EVT123"})
    with mock.patch.object(ema_graph.requests, "post", return_value=resp) as p:
        ok, eid, link = ema_graph.create_event(
            "mark@oncurapartners.com", "Oncura EMA Renewal — Clinic", "<p>call</p>",
            dt.datetime(2026, 7, 30, 10, 0), dt.datetime(2026, 7, 30, 10, 30),
            "Clinic <c@x.com>", token="T")
    assert ok and eid == "EVT123" and link.endswith("EVT123")
    url, kw = p.call_args[0][0], p.call_args[1]
    assert url.endswith("/users/mark@oncurapartners.com/events")
    body = kw["json"]
    assert body["start"] == {"dateTime": "2026-07-30T10:00:00", "timeZone": ema_graph.DEFAULT_TZ}
    assert body["attendees"] == [
        {"emailAddress": {"address": "c@x.com", "name": "Clinic"}, "type": "required"}]
    assert body["responseRequested"] is True


def test_create_event_reports_error():
    with mock.patch.object(ema_graph.requests, "post", return_value=_Resp(400, text="bad")):
        ok, eid, err = ema_graph.create_event(
            "m@x.com", "s", "<p>b</p>",
            dt.datetime(2026, 7, 30, 10, 0), dt.datetime(2026, 7, 30, 10, 30),
            "c@x.com", token="T")
    assert not ok and eid == "" and "400" in err


# ── cancel_event ──────────────────────────────────────────────────────────────


def test_cancel_event_posts_cancel_with_comment():
    with mock.patch.object(ema_graph.requests, "post", return_value=_Resp(202)) as p:
        ok, info = ema_graph.cancel_event("mark@oncurapartners.com", "EVT123",
                                          comment="renewed", token="T")
    assert ok and info == "cancelled"
    url, kw = p.call_args[0][0], p.call_args[1]
    assert url.endswith("/users/mark@oncurapartners.com/events/EVT123/cancel")
    assert kw["json"] == {"comment": "renewed"}
