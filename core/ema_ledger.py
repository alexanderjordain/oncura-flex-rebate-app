"""EMA renewal-outreach ledger — dedup + calendar-event tracking.

Persists one row per outreach so the daily cron never contacts the same clinic
twice inside a cooldown window, and so a later payment can find the exact calendar
event to cancel (pull the call back) and mark the clinic renewed.

Persistence goes through core.store (GitHub Contents API when GITHUB_TOKEN is set,
else local file). On Render the filesystem is ephemeral between cron runs, so set
GITHUB_TOKEN there to make the ledger durable in the repo.

**The repo is PUBLIC**, so this ledger deliberately carries NO clinic email or
other contact PII, and no raw Graph error text (which can echo a recipient). It
holds only what dedup and cancel need: the OPD clinic_id, the calendar event id,
booleans, and dates. The clinic email is read fresh from OPD at send time.

Schema (data/ema_outreach_ledger.json):
  {"outreach": [
    {clinic_id, clinic_name, mode ("expired"|"upcoming"), expiry ("YYYY-MM-DD"),
     contacted_at (ISO datetime), call_datetime (ISO), call_time,
     graph_event_id, email_sent (bool), event_created (bool), hs_note_id,
     status ("open"|"renewed"|"cancelled")},
    ...]}

The functional core (has_recent / append_outreach / latest_open / set_status)
takes and returns plain dicts so it is unit-testable without touching storage.
load()/save() are thin store wrappers around it.
"""
from __future__ import annotations

import datetime as dt

from . import store

LEDGER_PATH = "ema_outreach_ledger.json"


def _empty():
    return {"outreach": []}


# ── functional core (pure; operate on the data dict) ──────────────────────────
def _parse(dt_iso: str):
    try:
        return dt.datetime.fromisoformat(str(dt_iso))
    except (ValueError, TypeError):
        try:
            return dt.datetime.fromisoformat(str(dt_iso)[:10])
        except (ValueError, TypeError):
            return None


def has_recent(data: dict, clinic_id: str, today: dt.date, within_days: int) -> bool:
    """True if this clinic was contacted within the last `within_days` — the
    cooldown that stops a re-run (or a still-expired clinic on the next daily
    sweep) from being emailed again. A row already marked renewed/cancelled does
    not block a fresh cycle."""
    for row in data.get("outreach", []):
        if row.get("clinic_id") != clinic_id:
            continue
        if row.get("status") in ("renewed", "cancelled"):
            continue
        when = _parse(row.get("contacted_at", ""))
        if when and (today - when.date()).days < within_days:
            return True
    return False


def append_outreach(data: dict, entry: dict) -> dict:
    """Append an outreach row (status defaults to 'open'). Returns the same dict."""
    row = dict(entry)
    row.setdefault("status", "open")
    data.setdefault("outreach", []).append(row)
    return data


def latest_open(data: dict, clinic_id: str) -> dict | None:
    """Most-recent still-open outreach for a clinic — what a payment cancels."""
    rows = [r for r in data.get("outreach", [])
            if r.get("clinic_id") == clinic_id and r.get("status") == "open"]
    if not rows:
        return None
    return max(rows, key=lambda r: r.get("contacted_at", ""))


def set_status(data: dict, clinic_id: str, status: str, *, only_open: bool = True) -> int:
    """Flip a clinic's outreach rows to `status` (e.g. 'renewed'/'cancelled').
    Returns how many rows changed. By default only touches still-open rows."""
    n = 0
    for row in data.get("outreach", []):
        if row.get("clinic_id") != clinic_id:
            continue
        if only_open and row.get("status") != "open":
            continue
        row["status"] = status
        n += 1
    return n


# ── storage wrappers ──────────────────────────────────────────────────────────
def load():
    """Returns (data, sha). sha is passed back to save() for GitHub optimistic-lock."""
    data, sha = store.load_json(LEDGER_PATH, default=_empty())
    if not isinstance(data, dict) or "outreach" not in data:
        data = _empty()
    return data, sha


def save(data: dict, sha: str | None, message: str):
    return store.save_json(LEDGER_PATH, data, message=message, sha=sha)
