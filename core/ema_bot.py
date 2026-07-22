"""EMA renewal bot — orchestration shared by the CLI runner and the app button.

Host-agnostic (no Streamlit, no argparse). One source of truth for "who gets
contacted and what happens when we contact them", so scripts/ema_run.py and the
in-app admin button behave identically.

  plan_batch(...)  -> which clinics are eligible (after cooldown + email filter),
                      capped at PER_RUN_CAP; plus the loaded ledger (+ its sha).
  send_batch(...)  -> for each capped clinic: create the call on the organizer's
                      calendar inviting the clinic, email the notice as the sender
                      mailbox, log a HubSpot note, append the ledger row. The
                      CALLER saves the ledger (so a dry-run can skip it).
  reconcile(ids)   -> clinics that paid: cancel the pending call + document renewal.
"""
from __future__ import annotations

import datetime as dt

from . import ema_graph, ema_ledger, ema_outreach

DEFAULT_SENDER = "AJordain@oncurapartners.com"
DEFAULT_ORGANIZER = "mark@oncurapartners.com"


def sender_mailbox() -> str:
    return ema_outreach._cfg("EMA_EMAIL_SENDER", DEFAULT_SENDER)


def organizer_mailbox() -> str:
    return ema_outreach._cfg("EMA_ORGANIZER", DEFAULT_ORGANIZER)


def hubspot_enabled() -> bool:
    try:
        from . import ema_hubspot
        return ema_hubspot.is_configured()
    except Exception:
        return False


def plan_batch(mode: str = "expired", limit: int | None = None,
               window_days: int = ema_outreach.OUTREACH_LEAD_DAYS,
               max_age_days: int | None = None,
               cooldown_days: int = ema_outreach.OUTREACH_COOLDOWN_DAYS,
               today: dt.date | None = None) -> dict:
    """Assemble the batch: OPD scan -> build_plan -> drop clinics contacted within
    the cooldown or with no email -> cap. Returns the plan lists plus the loaded
    ledger and its sha (for the caller to save after sending)."""
    today = today or dt.date.today()
    plans = ema_outreach.build_plan(mode=mode, today=today, window_days=window_days,
                                    limit=None, max_age_days=max_age_days)
    data, sha = ema_ledger.load()
    eligible, skipped = [], []
    for p in plans:
        if ema_ledger.has_recent(data, p["clinic_id"], today, cooldown_days):
            skipped.append({**p, "skip_reason": "recent contact (cooldown)"})
        elif not p["email"]:
            skipped.append({**p, "skip_reason": "no email on file"})
        else:
            eligible.append(p)
    cap = ema_outreach.PER_RUN_CAP if limit is None else limit
    return {"candidates": len(plans), "eligible": eligible, "capped": eligible[:cap],
            "skipped": skipped, "ledger": data, "sha": sha, "today": today}


def send_one(p: dict, data: dict, *, organizer: str, sender: str, hs_on: bool,
             now_iso: str) -> dict:
    """Contact one clinic (calendar invite + email + optional HubSpot note) and
    append its ledger row. Returns a result dict for display/logging."""
    start = dt.datetime.fromisoformat(p["call_start"])
    end = dt.datetime.fromisoformat(p["call_end"])
    ok_evt, event_id, evt_info = ema_graph.create_event(
        organizer, p["event_subject"], p["event_html"], start, end, p["email"])
    ok_mail, mail_info = ema_graph.send_mail(
        sender, p["subject"], p["html"], p["email"], reply_to=sender)
    note_info = "hubspot off"
    if hs_on:
        from . import ema_hubspot
        _, note_info = ema_hubspot.log_outreach(
            p["clinic"], p["call_date"], p["call_time"], p["expiry"], p["status"])
    ema_ledger.append_outreach(data, {
        "clinic_id": p["clinic_id"], "clinic_name": p["clinic"], "mode": p["status"],
        "expiry": p["expiry"], "contacted_at": now_iso, "call_datetime": p["call_start"],
        "call_time": p["call_time"], "organizer": organizer, "email": p["email"],
        "graph_event_id": event_id, "email_status": mail_info,
        "event_status": evt_info if ok_evt else f"FAILED {evt_info}", "hs_note": note_info,
    })
    return {"clinic": p["clinic"], "email": p["email"], "event_ok": ok_evt,
            "event": event_id if ok_evt else evt_info, "mail_ok": ok_mail,
            "mail": mail_info, "note": note_info}


def send_batch(capped: list[dict], data: dict, *, today: dt.date | None = None) -> list[dict]:
    """Send every capped plan. Mutates `data` with new ledger rows; the caller
    persists it via ema_ledger.save(data, sha, message)."""
    del today  # timestamp uses the real clock at send time
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    org, snd, hs_on = organizer_mailbox(), sender_mailbox(), hubspot_enabled()
    return [send_one(p, data, organizer=org, sender=snd, hs_on=hs_on, now_iso=now_iso)
            for p in capped]


def reconcile(renewed_ids: list[str]) -> list[dict]:
    """Clinics that have paid: cancel the pending call (notifying the clinic),
    mark the ledger renewed, and document the renewal. Loads and saves the ledger
    itself. `renewed_ids` come from the HubSpot payment workflow (or by hand)."""
    data, sha = ema_ledger.load()
    org, hs_on = organizer_mailbox(), hubspot_enabled()
    results = []
    for cid in renewed_ids:
        row = ema_ledger.latest_open(data, cid)
        if not row:
            results.append({"clinic_id": cid, "status": "no open outreach"})
            continue
        cancel = "no event on file"
        if row.get("graph_event_id") and ema_graph.is_configured():
            ok, info = ema_graph.cancel_event(org, row["graph_event_id"])
            cancel = "cancelled" if ok else info
        ema_ledger.set_status(data, cid, "renewed")
        if hs_on:
            from . import ema_hubspot
            ema_hubspot.log_renewal(row.get("clinic_name", cid))
        results.append({"clinic_id": cid, "clinic": row.get("clinic_name"),
                        "cancel": cancel, "status": "renewed"})
    changed = [r for r in results if r["status"] == "renewed"]
    if changed:
        ema_ledger.save(data, sha, message=f"EMA reconcile: {len(changed)} renewed/cancelled")
    return results
