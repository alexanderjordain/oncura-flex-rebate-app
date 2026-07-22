"""EMA renewal outreach engine — Config A: Render compute + Graph + HubSpot.

Host-agnostic (NO Streamlit import). For each clinic whose hardware EMA is within
the outreach window (or already lapsed), it assembles the renewal outreach — the
universal HubSpot payment link, a pre-arranged renewal-call slot, and the
renewal-notice copy. The live send (in scripts/ema_run.py) then, per clinic:

  * creates the call on Mark's Outlook calendar and invites the clinic
    (core.ema_graph.create_event) — this IS the booking, no invitee click needed;
  * emails the branded renewal notice as AJordain@oncurapartners.com
    (core.ema_graph.send_mail);
  * logs a documentation Note on the clinic in HubSpot (core.ema_hubspot);
  * records it in the dedup ledger (core.ema_ledger).

Payment is a universal HubSpot payment link; a HubSpot workflow documents the
renewal downstream, and the reconcile pass cancels the call + updates the note.
Dry-run writes nothing.

Config via env vars (Render) or .streamlit/secrets.toml loaded into env (local dev):
  OPD_ODATA_USER / OPD_ODATA_PASS   OPD read (find EMAs + expiries)
  EMA_PAYMENT_LINK                  universal HubSpot payment link
  EMA_EMAIL_SENDER                  mailbox the notice sends as (default AJordain@)
  EMA_ORGANIZER                     calendar owner of the call (default mark@)
  GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET   app-only Graph
  HUBSPOT_TOKEN                     CRM documentation note (live only)
"""
from __future__ import annotations

import datetime as dt
import os

from requests.auth import HTTPBasicAuth

from . import ema_renewals

RENEWAL_PRICE = ema_renewals.RENEWAL_PRICE          # 4500
OUTREACH_LEAD_DAYS = ema_renewals.OUTREACH_LEAD_DAYS  # 14 (business day)
COMPANY_ADDR = "Oncura Partners  ·  6628 Bryant Irvin Rd, Suite 205, Fort Worth, TX 76132"
# The email is signed as the Oncura Partners brand; CALLER is the person the clinic
# speaks with on the call (and the calendar-event organizer).
SENDER = "The Oncura Partners Team"
CALLER = "Mark McIlwain"
PER_RUN_CAP = 25            # guardrail: never send more than this in one run
CALL_DURATION_MIN = 30      # length of the pre-arranged renewal call
OUTREACH_COOLDOWN_DAYS = 45  # don't re-contact a clinic within this many days


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def _opd_auth():
    u, p = _cfg("OPD_ODATA_USER"), _cfg("OPD_ODATA_PASS")
    return HTTPBasicAuth(u, p) if u and p else None


# ── Call-slot proposer ────────────────────────────────────────────────────────
# We present a specific pre-arranged time and actually create it on Mark's
# calendar (the clinic gets a real invite), spread across business-day slots so a
# batch doesn't collide. The clinic can accept or propose a new time from the
# invite itself — no separate scheduling link needed.
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
    return day, CALL_TIMES[index % len(CALL_TIMES)]


def slot_datetimes(call_date: dt.date, time_label: str) -> tuple[dt.datetime, dt.datetime]:
    """(start, end) naive US-Eastern wall-clock datetimes for a call slot. The 'ET'
    suffix (if present) is stripped; Graph pairs these with an Eastern timeZone."""
    t = time_label.replace("ET", "").strip()
    start = dt.datetime.strptime(f"{call_date.isoformat()} {t}", "%Y-%m-%d %I:%M %p")
    return start, start + dt.timedelta(minutes=CALL_DURATION_MIN)


# ── Outreach copy ─────────────────────────────────────────────────────────────
# Framing per the approved brief: the call is already ARRANGED (not "schedule a
# call") for a specific date/time; keep it — the clinic will get a calendar invite
# — or skip the conversation and renew online. Signed as the Oncura Partners brand.
def email_copy(clinic: str, expiry: dt.date, payment_link: str,
               call_date: dt.date, call_time: str, status: str = "upcoming",
               caller: str = CALLER) -> tuple[str, str, str]:
    exp = expiry.strftime("%B %d, %Y")
    cd = call_date.strftime("%A, %B %d, %Y")
    price = f"${RENEWAL_PRICE:,.0f}"
    ct = call_time if "ET" in call_time else f"{call_time} ET"
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
        f"To make this easy, we've set aside time for you to speak with {caller} about your "
        f"renewal:\n\n"
        f"     {cd} at {ct}\n\n"
        f"You'll receive a calendar invitation for this call — there's nothing you need to do to "
        f"keep it. If the time doesn't work, just accept and propose a new one right from the "
        f"invitation, or reply to this email.\n\n"
        f"If you'd rather take care of it now, you can skip the conversation and renew securely "
        f"online. Coverage {resume} immediately once payment is received.\n\n"
        f"     Renewal: {price}\n     {payment_link}\n\n"
        f"Your EMA renews for successive 12-month terms unless cancelled in writing; please treat "
        f"this as your renewal notice.\n\n"
        f"Thank you,\n{SENDER}\n\n6628 Bryant Irvin Rd, Suite 205\nFort Worth, TX 76132\n"
    )
    html = (
        f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#2A3742;">'
        f"<p>Hi {clinic},</p>"
        f"<p>{exp_html}</p>"
        f"<p>To make this easy, we've <b>set aside time for you to speak with {caller}</b> about "
        f"your renewal:</p>"
        f'<p style="font-size:16px;font-weight:600;color:#2F567E;">{cd} at {ct}</p>'
        f"<p>You'll receive a calendar invitation for this call — there's nothing you need to do "
        f"to keep it. If the time doesn't work, accept and propose a new one from the invitation, "
        f"or reply to this email.</p>"
        f"<p>Prefer to take care of it now? Skip the conversation and renew securely online — "
        f"coverage {resume} immediately once payment is received.</p>"
        f'<p><b>Renewal: {price}</b> &nbsp; '
        f'<a href="{payment_link}" style="background:#2F567E;color:#fff;padding:10px 18px;'
        f'border-radius:6px;text-decoration:none;font-weight:600;">Renew online</a></p>'
        f'<p style="color:#6B7785;font-size:12px;">Your EMA renews for successive 12-month terms '
        f"unless cancelled in writing; please treat this as your renewal notice.</p>"
        f'<p>Thank you,<br>{SENDER}</p>'
        f'<p style="color:#6B7785;font-size:12px;">6628 Bryant Irvin Rd, Suite 205<br>Fort Worth, TX 76132</p>'
        f"</div>"
    )
    return subject, plain, html


def event_subject(clinic: str) -> str:
    return f"Oncura EMA Renewal — {clinic}"


def event_body_html(clinic: str, expiry: dt.date, payment_link: str,
                    status: str = "upcoming", caller: str = CALLER) -> str:
    """Body of the calendar invitation the clinic receives. Carries the same
    skip-to-renew line so the payment path is one click away from the invite."""
    exp = expiry.strftime("%B %d, %Y")
    lapse = (f"Your EMA expired on {exp}." if status == "expired"
             else f"Your EMA is set to expire on {exp}.")
    return (
        f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#2A3742;">'
        f"<p>Hi {clinic}, this is a quick call with {caller} to walk through renewing your Oncura "
        f"EMA (hardware warranty) — questions welcome, no prep needed.</p>"
        f"<p>{lapse} Renewal is ${RENEWAL_PRICE:,.0f} for a 12-month term.</p>"
        f'<p>Prefer to skip the call and renew now? '
        f'<a href="{payment_link}">Renew securely online</a> — coverage resumes as soon as payment '
        f"is received, and we'll cancel this hold for you.</p>"
        f"</div>"
    )


# ── Plan (dry-run: no writes) ─────────────────────────────────────────────────────
def build_plan(mode: str = "upcoming", today: dt.date | None = None,
               window_days: int = OUTREACH_LEAD_DAYS, limit: int | None = None,
               max_age_days: int | None = None) -> list[dict]:
    """The outreach batch as plan dicts — clinic, recipient, the pre-arranged call
    slot (date/time + start/end datetimes), the payment link, and the rendered
    email + calendar-invite copy. No side effects.

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
        start, end = slot_datetimes(call_date, call_time)
        subj, plain, html = email_copy(
            c["clinic"], c["hardware_end"], payment_link, call_date, call_time, status=status)
        plans.append({
            "clinic": c["clinic"], "clinic_id": c["clinic_id"], "state": c["state"],
            "email": c["email"], "expiry": c["hardware_end"].isoformat(), "status": status,
            "days_to_expiry": c.get("days_to_expiry"), "days_expired": c.get("days_expired"),
            "call_date": call_date.isoformat(), "call_time": f"{call_time} ET",
            "call_start": start.isoformat(), "call_end": end.isoformat(),
            "renewal_price": RENEWAL_PRICE, "payment_link": payment_link,
            "subject": subj, "plain": plain, "html": html,
            "event_subject": event_subject(c["clinic"]),
            "event_html": event_body_html(c["clinic"], c["hardware_end"], payment_link, status=status),
        })
    return plans
