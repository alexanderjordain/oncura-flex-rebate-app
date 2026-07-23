"""iCalendar (.ics) meeting-invite generation for the EMA bot.

A consent-free way to deliver the "your call is already set — or skip it and pay
now" model without any Microsoft Graph calendar write. We generate a standards-
compliant VCALENDAR/VEVENT ourselves and attach it to the outreach email:

  * build_invite() -> a METHOD:REQUEST invite for one clinic (organizer = the
    Oncura caller, attendee = the clinic), with the pay-to-skip link in the body.
    Email clients render it as a real meeting the clinic can accept/decline.
  * build_batch() -> one METHOD:PUBLISH file bundling a whole run's calls, for
    Mark to import so his calendar is blocked for those slots.

Times are emitted in UTC ('...Z') so every client shows the correct local time
with no VTIMEZONE needed. No network, no credentials, no consent — this is just
text. Pairs with an email channel (drafts via the already-consented assistance
mailbox, or Graph once consent lands) that carries the file.
"""
from __future__ import annotations

import datetime as dt

try:
    from zoneinfo import ZoneInfo
    _UTC = ZoneInfo("UTC")
except Exception:  # pragma: no cover
    _UTC = dt.timezone.utc

DEFAULT_TZ = "America/New_York"
PRODID = "-//Oncura Partners//EMA Renewal Bot//EN"


def _esc(text: str) -> str:
    """Escape a TEXT value per RFC 5545 (backslash, semicolon, comma, newline)."""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """Fold a content line to <=75 octets with CRLF + single leading space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, chunk = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(chunk) + len(b) > 75:
            out.append(chunk.decode("utf-8"))
            chunk = b" " + b  # continuation lines start with a space
        else:
            chunk += b
    out.append(chunk.decode("utf-8"))
    return "\r\n".join(out)


def _to_utc(d: dt.datetime, tzname: str) -> dt.datetime:
    if d.tzinfo is None:
        try:
            d = d.replace(tzinfo=ZoneInfo(tzname))
        except Exception:  # pragma: no cover - tzdata missing
            d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(_UTC)


def _stamp(d: dt.datetime) -> str:
    return d.strftime("%Y%m%dT%H%M%SZ")


def _vevent(*, uid: str, dtstamp: dt.datetime, start_utc: dt.datetime, end_utc: dt.datetime,
            summary: str, description: str, location: str,
            organizer_name: str, organizer_email: str,
            attendee_email: str | None, attendee_name: str | None) -> list[str]:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_stamp(dtstamp)}",
        f"DTSTART:{_stamp(start_utc)}",
        f"DTEND:{_stamp(end_utc)}",
        f"SUMMARY:{_esc(summary)}",
        f"DESCRIPTION:{_esc(description)}",
        f"LOCATION:{_esc(location)}",
        f"ORGANIZER;CN={_esc(organizer_name)}:mailto:{organizer_email}",
    ]
    if attendee_email:
        cn = _esc(attendee_name or attendee_email)
        lines.append(
            f"ATTENDEE;CN={cn};ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:"
            f"mailto:{attendee_email}")
    lines += ["STATUS:CONFIRMED", "SEQUENCE:0", "END:VEVENT"]
    return lines


def build_invite(*, uid: str, start: dt.datetime, end: dt.datetime, summary: str,
                 description: str, organizer_email: str, attendee_email: str,
                 organizer_name: str = "Oncura Partners", attendee_name: str | None = None,
                 location: str = "Phone call", tz: str = DEFAULT_TZ,
                 dtstamp: dt.datetime | None = None) -> str:
    """A single-clinic meeting REQUEST (organizer invites the clinic)."""
    dtstamp = dtstamp or dt.datetime.now(_UTC)
    body = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}",
            "CALSCALE:GREGORIAN", "METHOD:REQUEST"]
    body += _vevent(uid=uid, dtstamp=dtstamp,
                    start_utc=_to_utc(start, tz), end_utc=_to_utc(end, tz),
                    summary=summary, description=description, location=location,
                    organizer_name=organizer_name, organizer_email=organizer_email,
                    attendee_email=attendee_email, attendee_name=attendee_name)
    body.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in body) + "\r\n"


def build_batch(events: list[dict], *, organizer_email: str,
                organizer_name: str = "Oncura Partners", tz: str = DEFAULT_TZ,
                dtstamp: dt.datetime | None = None) -> str:
    """One PUBLISH file bundling many calls, for the caller to import into their
    own calendar. Each event dict: uid, start, end, summary, description, and
    optionally location / attendee_email / attendee_name."""
    dtstamp = dtstamp or dt.datetime.now(_UTC)
    body = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}",
            "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for e in events:
        body += _vevent(
            uid=e["uid"], dtstamp=dtstamp,
            start_utc=_to_utc(e["start"], tz), end_utc=_to_utc(e["end"], tz),
            summary=e["summary"], description=e.get("description", ""),
            location=e.get("location", "Phone call"),
            organizer_name=organizer_name, organizer_email=organizer_email,
            attendee_email=e.get("attendee_email"), attendee_name=e.get("attendee_name"))
    body.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in body) + "\r\n"
