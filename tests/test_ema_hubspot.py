"""Tests for the HubSpot documentation module (mocked HTTP; no live calls)."""
from __future__ import annotations

import datetime as dt
from unittest import mock

from core import ema_hubspot


class _Resp:
    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body or {}
        self.text = text

    def json(self):
        return self._json


def test_find_company_id_returns_first_match():
    hit = _Resp(200, {"results": [{"id": "555"}]})
    with mock.patch.object(ema_hubspot.requests, "post", return_value=hit) as p:
        cid = ema_hubspot.find_company_id("Abell Animal Hospital")
    assert cid == "555"
    body = p.call_args[1]["json"]
    assert body["filterGroups"][0]["filters"][0]["propertyName"] == "name"


def test_find_company_id_none_when_no_results():
    with mock.patch.object(ema_hubspot.requests, "post", return_value=_Resp(200, {"results": []})):
        assert ema_hubspot.find_company_id("Nope") is None


def test_create_note_builds_company_association_and_timestamp():
    with mock.patch.object(ema_hubspot.requests, "post", return_value=_Resp(201, {"id": "N1"})) as p:
        ok, nid = ema_hubspot.create_note("<p>hi</p>", company_id="555",
                                          when=dt.datetime(2026, 7, 22, 12, 0))
    assert ok and nid == "N1"
    body = p.call_args[1]["json"]
    assert body["properties"]["hs_note_body"] == "<p>hi</p>"
    assert isinstance(body["properties"]["hs_timestamp"], int)
    assoc = body["associations"][0]
    assert assoc["to"]["id"] == "555"
    assert assoc["types"][0]["associationTypeId"] == ema_hubspot._NOTE_TO_COMPANY


def test_create_note_reports_error():
    with mock.patch.object(ema_hubspot.requests, "post", return_value=_Resp(400, text="bad")):
        ok, info = ema_hubspot.create_note("<p>x</p>", company_id="1")
    assert not ok and "400" in info


def test_log_outreach_looks_up_company_then_notes():
    calls = {"search": 0, "note": 0}

    def fake_post(url, **kw):
        if url.endswith("/search"):
            calls["search"] += 1
            return _Resp(200, {"results": [{"id": "999"}]})
        calls["note"] += 1
        # the note body should name the caller and the call time
        assert "999" in str(kw["json"]["associations"])
        return _Resp(201, {"id": "NOTE9"})

    with mock.patch.object(ema_hubspot.requests, "post", side_effect=fake_post):
        ok, info = ema_hubspot.log_outreach(
            "Abell", "Friday, August 01, 2026", "10:00 AM ET", "2026-06-01", "expired")
    assert ok and info == "NOTE9"
    assert calls["search"] == 1 and calls["note"] == 1
