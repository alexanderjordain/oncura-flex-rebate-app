#!/usr/bin/env python
"""EMA renewal bot — headless runner (Render cron entrypoint).

DRY-RUN BY DEFAULT: prints the plan and writes nothing. Add --live to actually
create calendar events, send emails, log HubSpot notes, and record the ledger.

  python scripts/ema_run.py                       # dry-run, expired backlog
  python scripts/ema_run.py --mode upcoming       # dry-run, expiring soon
  python scripts/ema_run.py --live                # send (capped at PER_RUN_CAP)
  python scripts/ema_run.py --check-graph         # verify Graph token + mailbox access
  python scripts/ema_run.py --send-test you@x.com # send one test email
  python scripts/ema_run.py --reconcile --renewed AH001,VH002   # cancel calls + document

Mailboxes: the renewal email sends as EMA_EMAIL_SENDER (default AJordain@); the
call is created on EMA_ORGANIZER's calendar (default mark@). See docs/EMA_GRAPH_SETUP.md.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core import ema_bot, ema_graph, ema_ledger, ema_outreach  # noqa: E402


def _load_secrets_into_env():
    """Local dev: fold .streamlit/secrets.toml into os.environ (Render sets env
    vars directly, so this is a no-op there)."""
    p = pathlib.Path(".streamlit/secrets.toml")
    if not p.exists():
        return
    import tomllib
    try:
        data = tomllib.load(open(p, "rb"))
    except Exception:
        return
    for k, v in data.items():
        if isinstance(v, (str, int, float)) and k not in os.environ:
            os.environ[k] = str(v)


def _sender() -> str:
    return ema_bot.sender_mailbox()


def _organizer() -> str:
    return ema_bot.organizer_mailbox()


def check_graph() -> int:
    if not ema_graph.is_configured():
        print("Graph not configured — set GRAPH_TENANT_ID/CLIENT_ID/CLIENT_SECRET "
              "(see docs/EMA_GRAPH_SETUP.md).")
        return 1
    import requests
    try:
        tok = ema_graph._token()
    except RuntimeError as e:
        print(f"Token FAILED: {e}")
        return 1
    print("Token OK (app-only).")
    org = _organizer()
    r = requests.get(f"{ema_graph.GRAPH_BASE}/users/{org}/calendar",
                     headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    print(f"Calendar access for organizer {org}: "
          f"{'OK' if r.status_code == 200 else f'FAILED {r.status_code} {r.text[:200]}'}")
    print(f"Email will send as {_sender()} (Mail.Send is only verifiable by a live "
          f"send — use --send-test).")
    return 0 if r.status_code == 200 else 1


def send_test(addr: str) -> int:
    if not ema_graph.is_configured():
        print("Graph not configured.")
        return 1
    ok, info = ema_graph.send_mail(_sender(), "Oncura EMA bot — test",
                                   "<p>This is a test send from the EMA renewal bot.</p>", addr)
    print(f"send_test -> {'OK' if ok else 'FAILED'}: {info}")
    return 0 if ok else 1


def outreach(mode: str, limit: int, live: bool, window_days: int,
             max_age_days: int | None, cooldown_days: int) -> int:
    b = ema_bot.plan_batch(mode=mode, limit=limit, window_days=window_days,
                           max_age_days=max_age_days, cooldown_days=cooldown_days)
    capped, skipped = b["capped"], b["skipped"]
    print(f"=== EMA outreach  mode={mode}  {'LIVE' if live else 'DRY-RUN'}  "
          f"{b['today'].isoformat()} ===")
    print(f"candidates={b['candidates']}  eligible={len(b['eligible'])}  "
          f"sending={len(capped)} (cap {limit})  skipped={len(skipped)}")
    for p in skipped[:20]:
        print(f"   skip: {p['clinic']}  ({p['skip_reason']})")

    if not live:
        for p in capped:
            print(f"\n--- {p['clinic']}  [{p['status']}]  expiry {p['expiry']} -> {p['email']}")
            print(f"    call: {p['call_date']} {p['call_time']}  | subject: {p['subject']}")
        print(f"\nDRY-RUN: nothing sent. Re-run with --live to send {len(capped)}.")
        return 0

    if not ema_graph.is_configured():
        print("ABORT: --live but Graph not configured. See docs/EMA_GRAPH_SETUP.md.")
        return 1

    results = ema_bot.send_batch(capped, b["ledger"], today=b["today"])
    for r in results:
        print(f"   sent: {r['clinic']}  event={'ok' if r['event_ok'] else r['event']}  "
              f"mail={'ok' if r['mail_ok'] else r['mail']}  note={r['note']}")
    if results:
        ema_ledger.save(b["ledger"], b["sha"],
                        message=f"EMA outreach {b['today'].isoformat()}: {len(results)} contacted")
    print(f"\nLIVE: contacted {len(results)}, ledger updated.")
    return 0


def reconcile(renewed_ids: list[str]) -> int:
    if not renewed_ids:
        print("No --renewed clinic ids provided.")
        return 1
    for r in ema_bot.reconcile(renewed_ids):
        if r["status"] == "renewed":
            print(f"   {r['clinic_id']} ({r.get('clinic')}): cancel -> {r['cancel']}; marked renewed")
        else:
            print(f"   {r['clinic_id']}: {r['status']} — skipped")
    return 0


def main(argv=None) -> int:
    _load_secrets_into_env()
    ap = argparse.ArgumentParser(description="EMA renewal bot runner (dry-run by default).")
    ap.add_argument("--mode", choices=["expired", "upcoming"], default="expired")
    ap.add_argument("--limit", type=int, default=ema_outreach.PER_RUN_CAP)
    ap.add_argument("--window-days", type=int, default=ema_outreach.OUTREACH_LEAD_DAYS)
    ap.add_argument("--max-age-days", type=int, default=None,
                    help="expired mode: ignore lapses older than this")
    ap.add_argument("--cooldown-days", type=int, default=ema_outreach.OUTREACH_COOLDOWN_DAYS)
    ap.add_argument("--live", action="store_true", help="actually send (default is dry-run)")
    ap.add_argument("--check-graph", action="store_true")
    ap.add_argument("--send-test", metavar="EMAIL")
    ap.add_argument("--reconcile", action="store_true")
    ap.add_argument("--renewed", default="", help="comma-separated clinic ids that paid")
    a = ap.parse_args(argv)

    if a.check_graph:
        return check_graph()
    if a.send_test:
        return send_test(a.send_test)
    if a.reconcile:
        return reconcile([c.strip() for c in a.renewed.split(",") if c.strip()])
    return outreach(a.mode, a.limit, a.live, a.window_days, a.max_age_days, a.cooldown_days)


if __name__ == "__main__":
    raise SystemExit(main())
