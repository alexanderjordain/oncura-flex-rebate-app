"""Tests for iCalendar (.ics) generation — structure, UTC times, escaping."""
from __future__ import annotations

import datetime as dt

from core import ema_ics


def test_build_invite_structure_and_utc():
    ics = ema_ics.build_invite(
        uid="EMA-AH001@oncurapartners.com",
        start=dt.datetime(2026, 7, 30, 10, 0),   # 10:00 ET
        end=dt.datetime(2026, 7, 30, 10, 30),
        summary="Oncura EMA Renewal Call - Abell AH",
        description="Your call is set. Prefer to skip? Pay: https://pay.link/x",
        organizer_email="ajordain@oncurapartners.com",
        attendee_email="clinic@x.com", attendee_name="Abell AH",
        dtstamp=dt.datetime(2026, 7, 22, 12, 0, tzinfo=dt.timezone.utc))
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.strip().endswith("END:VCALENDAR")
    assert "\r\n" in ics  # CRLF line endings
    # unfold (a parser joins "\r\n " continuations) before checking content
    flat = ics.replace("\r\n ", "")
    assert "METHOD:REQUEST" in flat
    assert "BEGIN:VEVENT" in flat and "END:VEVENT" in flat
    # 10:00 ET on 2026-07-30 is EDT (UTC-4) -> 14:00Z
    assert "DTSTART:20260730T140000Z" in flat
    assert "DTEND:20260730T143000Z" in flat
    assert "ORGANIZER;CN=Oncura Partners:mailto:ajordain@oncurapartners.com" in flat
    assert "RSVP=TRUE:mailto:clinic@x.com" in flat
    assert "https://pay.link/x" in flat


def test_description_escaping():
    ics = ema_ics.build_invite(
        uid="u1", start=dt.datetime(2026, 7, 30, 10, 0), end=dt.datetime(2026, 7, 30, 10, 30),
        summary="Call; with, punctuation", description="line1\nline2; and, more",
        organizer_email="a@x.com", attendee_email="c@x.com")
    assert "SUMMARY:Call\\; with\\, punctuation" in ics
    assert "DESCRIPTION:line1\\nline2\\; and\\, more" in ics


def test_build_batch_multiple_events_publish():
    events = [
        {"uid": "u1", "start": dt.datetime(2026, 7, 30, 10, 0),
         "end": dt.datetime(2026, 7, 30, 10, 30), "summary": "Call A", "description": "a"},
        {"uid": "u2", "start": dt.datetime(2026, 7, 30, 10, 30),
         "end": dt.datetime(2026, 7, 30, 11, 0), "summary": "Call B", "description": "b"},
    ]
    ics = ema_ics.build_batch(events, organizer_email="mark@oncurapartners.com")
    assert "METHOD:PUBLISH" in ics
    assert ics.count("BEGIN:VEVENT") == 2 and ics.count("END:VEVENT") == 2
    assert "SUMMARY:Call A" in ics and "SUMMARY:Call B" in ics


def test_long_line_is_folded():
    ics = ema_ics.build_invite(
        uid="u1", start=dt.datetime(2026, 7, 30, 10, 0), end=dt.datetime(2026, 7, 30, 10, 30),
        summary="x", description="y " * 60,  # long line forces folding
        organizer_email="a@x.com", attendee_email="c@x.com")
    # every physical line must be <= 75 octets
    assert all(len(ln.encode("utf-8")) <= 75 for ln in ics.split("\r\n"))
