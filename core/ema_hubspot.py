"""HubSpot documentation for the EMA renewal bot.

The bot's CRM footprint is *documentation*, not scheduling — the real calendar
event lives in Outlook via Graph (HubSpot-API meetings don't sync to Outlook).
So at outreach we drop a Note on the clinic's company timeline recording that the
renewal notice went out and the call is set; when a payment cancels the call, we
add a second Note closing the loop. Deal creation happens post-payment via a
HubSpot workflow (see docs), not here.

Everything is best-effort: a CRM failure logs and returns (False, reason) but must
NOT block the clinic's email/calendar outreach, which is the load-bearing part.

Config: HUBSPOT_TOKEN (private-app token with crm.objects.companies.read +
crm.objects.notes.write).
"""
from __future__ import annotations

import datetime as dt
import os

import requests

HS_BASE = "https://api.hubapi.com"
_TIMEOUT = 30
# HubSpot-defined association type IDs for a Note:
_NOTE_TO_COMPANY = 190
_NOTE_TO_CONTACT = 202


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def is_configured() -> bool:
    return bool(_cfg("HUBSPOT_TOKEN"))


def _headers() -> dict:
    return {"Authorization": f"Bearer {_cfg('HUBSPOT_TOKEN')}", "Content-Type": "application/json"}


def _epoch_ms(when: dt.datetime | None) -> int:
    when = when or dt.datetime(1970, 1, 1)
    return int(when.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def find_company_id(name: str) -> str | None:
    """Best-effort company lookup by name: exact match first, then a token
    contains. Returns the company id or None (caller logs the miss)."""
    if not name:
        return None
    for op, val in (("EQ", name), ("CONTAINS_TOKEN", name)):
        payload = {
            "filterGroups": [{"filters": [{"propertyName": "name", "operator": op, "value": val}]}],
            "properties": ["name"], "limit": 5,
        }
        try:
            r = requests.post(f"{HS_BASE}/crm/v3/objects/companies/search",
                              headers=_headers(), json=payload, timeout=_TIMEOUT)
        except requests.RequestException:
            return None
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                return results[0].get("id")
    return None


def create_note(body_html: str, *, company_id: str | None = None,
                contact_id: str | None = None, when: dt.datetime | None = None) -> tuple[bool, str]:
    """Create a timeline Note, associated to the company (and optionally contact).
    Returns (ok, note_id_or_error)."""
    associations = []
    if company_id:
        associations.append({
            "to": {"id": company_id},
            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": _NOTE_TO_COMPANY}],
        })
    if contact_id:
        associations.append({
            "to": {"id": contact_id},
            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": _NOTE_TO_CONTACT}],
        })
    payload = {
        "properties": {"hs_note_body": body_html, "hs_timestamp": _epoch_ms(when)},
        "associations": associations,
    }
    try:
        r = requests.post(f"{HS_BASE}/crm/v3/objects/notes",
                          headers=_headers(), json=payload, timeout=_TIMEOUT)
    except requests.RequestException as e:
        return False, f"network error: {e}"
    if r.status_code in (200, 201):
        return True, r.json().get("id", "")
    return False, f"HubSpot note error {r.status_code}: {r.text[:300]}"


def log_outreach(clinic: str, call_date: str, call_time: str, expiry: str, status: str,
                 *, when: dt.datetime | None = None) -> tuple[bool, str]:
    """Documentation Note for an outreach: notice sent + call set."""
    cid = find_company_id(clinic)
    lapse = "lapsed" if status == "expired" else "expiring"
    body = (f"<p><b>EMA renewal outreach sent</b> (automated).</p>"
            f"<p>EMA {lapse} — expiry {expiry}. Renewal notice emailed to the clinic, and a "
            f"renewal call with {ov_caller()} is set for <b>{call_date} at {call_time}</b> "
            f"(invite sent from Outlook). Universal payment link included for skip-to-renew.</p>")
    ok, info = create_note(body, company_id=cid, when=when)
    return ok, (info if ok else f"{info} (company_id={cid})")


def log_renewal(clinic: str, *, when: dt.datetime | None = None) -> tuple[bool, str]:
    """Documentation Note closing the loop: clinic renewed, call cancelled."""
    cid = find_company_id(clinic)
    body = ("<p><b>EMA renewed — renewal call cancelled.</b></p>"
            "<p>Payment received via the renewal link; the scheduled call was cancelled and "
            "the clinic notified. Accounting owns updating the EMA end date.</p>")
    return create_note(body, company_id=cid, when=when)


def ov_caller() -> str:
    # Kept as a function so the caller name stays consistent with ema_outreach.CALLER
    from . import ema_outreach
    return ema_outreach.CALLER
