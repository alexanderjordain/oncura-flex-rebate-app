"""EMA renewal outreach engine — Config A: Render compute + HubSpot email/payment.

Host-agnostic (NO Streamlit import). For each clinic whose hardware EMA is within
the outreach window, it assembles the renewal outreach — the universal HubSpot
payment link + a per-clinic Calendly booking link + the renewal-notice copy — and,
in live mode, generates the Calendly link and upserts the HubSpot contact with the
properties a HubSpot workflow watches to send the email. Payment + documentation
are handled by HubSpot workflows downstream. Dry-run writes nothing.

Config via env vars (Render) or .streamlit/secrets.toml loaded into env (local dev):
  OPD_ODATA_USER / OPD_ODATA_PASS          OPD read (find EMAs + expiries)
  EMA_PAYMENT_LINK                         universal HubSpot payment link
  CALENDLY_TOKEN / CALENDLY_EVENT_TYPE_URI Mark's EMA-renewal event type (booking link)
  HUBSPOT_TOKEN                            contact upsert + enrollment (live only)
"""
from __future__ import annotations

import datetime as dt
import os

import requests
from requests.auth import HTTPBasicAuth

from . import ema_renewals

RENEWAL_PRICE = ema_renewals.RENEWAL_PRICE          # 4500
OUTREACH_LEAD_DAYS = ema_renewals.OUTREACH_LEAD_DAYS  # 14 (business day)
COMPANY_ADDR = "Oncura Partners  ·  6628 Bryant Irvin Rd, Suite 205, Fort Worth, TX 76132"
# Sender is the Oncura Partners brand, not a named individual. Mark is referenced
# in the body only as the person the clinic would speak with on the call.
SENDER = "The Oncura Partners Team"
PER_RUN_CAP = 25   # guardrail: never send more than this in one run


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _opd_auth():
    u, p = _cfg("OPD_ODATA_USER"), _cfg("OPD_ODATA_PASS")
    return HTTPBasicAuth(u, p) if u and p else None


# ── Call-slot proposer ────────────────────────────────────────────────────────
# We present a specific pre-arranged time ("we've set aside {call_date} at
# {call_time}"), spread across business-day slots so a batch doesn't collide. The
# Calendly link lets the clinic keep/confirm or reschedule. NOTE: these are
# proposed holds — they only become real calendar events once Mark's Calendly
# calendar is connected and the clinic confirms via the link.
CALL_TIMES = ["10:00 AM", "10:30 AM", "11:00 AM", "11:30 AM",
              "1:00 PM", "1:30 PM", "2:00 PM", "2:30 PM"]  # US Eastern, per business day


def _next_business_day(d: dt.date) -> dt.date:
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def assign_call_slot(index: int, start: dt.date) -> tuple[dt.date, str]:
    """Assign clinic #index a proposed call slot: sequential business-day slots
    from `start`, CALL_TIMES per day, so a run's calls are spread out."""
    day = start
    for _ in range((index // len(CALL_TIMES)) + 1):
        day = _next_business_day(day)
    return day, CALL_TIMES[index % len(CALL_TIMES)] + " ET"


# ── Outreach copy (reference for the HubSpot email template; previewed in dry-run) ──
# Framing per the approved brief: the call is already ARRANGED (not "schedule a
# call") for a specific date/time; keep it, or skip the conversation and renew
# online. From the Oncura Partners brand, not an individual.
def email_copy(clinic: str, expiry: dt.date, payment_link: str, calendly_link: str,
               call_date: dt.date, call_time: str, status: str = "upcoming") -> tuple[str, str, str]:
    exp = expiry.strftime("%B %d, %Y")
    cd = call_date.strftime("%A, %B %d, %Y")
    price = f"${RENEWAL_PRICE:,.0f}"
    if status == "expired":
        subject = "Your Oncura EMA has lapsed — your renewal call is set"
        exp_line = f"Your EMA expired on {exp}, so your ultrasound isn't currently covered."
        exp_html = f"Your EMA expired on <b>{exp}</b>, so your ultrasound isn't currently covered."
        resume = "resumes"
    else:
        subject = "Your Oncura EMA renewal — your call is set"
        exp_line = f"Your EMA is set to expire on {exp}."
        exp_html = f"Your EMA is set to expire on <b>{exp}</b>."
        resume = "continues"
    plain = (
        f"Hi {clinic},\n\n"
        f"{exp_line}\n\n"
        f"To help keep your ultrasound coverage active, we've set aside time for you to speak with "
        f"Mark McIlwain about your renewal:\n\n"
        f"     {cd} at {call_time}\n\n"
        f"Mark can answer any questions and walk you through your options — there's nothing you need to "
        f"do to keep this call. Confirm or reschedule here:\n     {calendly_link}\n\n"
        f"If you'd rather take care of it now, you can skip the conversation and renew securely online. "
        f"Coverage {resume} immediately once payment is received.\n\n"
        f"     Renewal: {price}\n     {payment_link}\n\n"
        f"Your EMA renews for successive 12-month terms unless cancelled in writing; please treat this "
        f"as your renewal notice.\n\n"
        f"Thank you,\n{SENDER}\n\n6628 Bryant Irvin Rd, Suite 205\nFort Worth, TX 76132\n"
    )
    html = (
        f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#2A3742;">'
        f"<p>Hi {clinic},</p>"
        f"<p>{exp_html}</p>"
        f"<p>To help keep your ultrasound coverage active, we've <b>set aside time for you to speak with "
        f"Mark McIlwain</b> about your renewal:</p>"
        f'<p style="font-size:16px;font-weight:600;color:#2F567E;">{cd} at {call_time}</p>'
        f"<p>Mark can answer any questions and walk you through your options — there's nothing you need "
        f"to do to keep this call.</p>"
        f'<p><a href="{calendly_link}" style="background:#2F567E;color:#fff;padding:10px 18px;'
        f'border-radius:6px;text-decoration:none;font-weight:600;">Confirm or reschedule your call</a></p>'
        f"<p>Prefer to take care of it now? Skip the conversation and renew securely online — coverage "
        f"{resume} immediately once payment is received.</p>"
        f'<p><b>Renewal: {price}</b> &nbsp; <a href="{payment_link}">renew online</a></p>'
        f'<p style="color:#6B7785;font-size:12px;">Your EMA renews for successive 12-month terms unless '
        f"cancelled in writing; please treat this as your renewal notice.</p>"
        f'<p>Thank you,<br>{SENDER}</p>'
        f'<p style="color:#6B7785;font-size:12px;">6628 Bryant Irvin Rd, Suite 205<br>Fort Worth, TX 76132</p>'
        f"</div>"
    )
    return subject, plain, html


# ── Calendly single-use booking link (live) ──────────────────────────────────────
def calendly_link() -> str:
    """Mint a single-use scheduling link on Mark's EMA-renewal event type."""
    tok = _cfg("CALENDLY_TOKEN")
    et = _cfg("CALENDLY_EVENT_TYPE_URI")
    if not tok or not et:
        return ""
    r = requests.post(
        "https://api.calendly.com/scheduling_links",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        json={"max_event_count": 1, "owner": et, "owner_type": "EventType"}, timeout=30)
    r.raise_for_status()
    return r.json()["resource"]["booking_url"]


# ── Plan (dry-run: no writes) ─────────────────────────────────────────────────────
def build_plan(mode: str = "upcoming", today: dt.date | None = None,
               window_days: int = OUTREACH_LEAD_DAYS, limit: int | None = None,
               max_age_days: int | None = None) -> list[dict]:
    """The outreach batch as plan dicts — clinic, recipient, links, and the email
    copy that would be sent. No side effects (Calendly link is a placeholder).

    mode="upcoming": hardware EMAs expiring within `window_days` (the ongoing run).
    mode="expired":  hardware EMAs already lapsed (the backlog), most-recent first,
                     optionally bounded by `max_age_days`. A live run caps at
                     PER_RUN_CAP so the backlog drains gradually.
    """
    today = today or dt.date.today()
    clinics = ema_renewals.fetch_all_ema(auth=_opd_auth())
    if mode == "expired":
        batch = ema_renewals.expired_batch(clinics, today, max_age_days=max_age_days)
        status = "expired"
    else:
        active = [c for c in clinics if c["hardware_active"]]
        batch = ema_renewals.renewal_batch(active, today, window_days=window_days)
        status = "upcoming"
    if limit:
        batch = batch[:limit]
    payment_link = _cfg("EMA_PAYMENT_LINK")
    plans = []
    for i, c in enumerate(batch):
        call_date, call_time = assign_call_slot(i, today)
        subj, plain, html = email_copy(
            c["clinic"], c["hardware_end"], payment_link,
            "{{ per-clinic Calendly link generated at send }}", call_date, call_time, status=status)
        plans.append({
            "clinic": c["clinic"], "clinic_id": c["clinic_id"], "state": c["state"],
            "email": c["email"], "expiry": c["hardware_end"].isoformat(), "status": status,
            "days_to_expiry": c.get("days_to_expiry"), "days_expired": c.get("days_expired"),
            "call_date": call_date.isoformat(), "call_time": call_time,
            "renewal_price": RENEWAL_PRICE, "payment_link": payment_link,
            "subject": subj, "plain": plain, "html": html,
        })
    return plans
