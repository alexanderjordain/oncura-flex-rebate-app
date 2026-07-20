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
SENDER = "Mark McIlwain, Oncura Partners"
PER_RUN_CAP = 25   # guardrail: never send more than this in one run


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _opd_auth():
    u, p = _cfg("OPD_ODATA_USER"), _cfg("OPD_ODATA_PASS")
    return HTTPBasicAuth(u, p) if u and p else None


# ── Outreach copy (reference for the HubSpot email template; previewed in dry-run) ──
# Call-first framing: we've set them up with a call so they have someone to talk
# to about the EMA; if they'd rather skip the conversation, the renew link is right
# there. Wording adapts for already-lapsed vs. still-active EMAs.
def email_copy(clinic: str, expiry: dt.date, payment_link: str, calendly_link: str,
               status: str = "upcoming") -> tuple[str, str, str]:
    exp = expiry.strftime("%B %d, %Y")
    price = f"${RENEWAL_PRICE:,.0f}"
    if status == "expired":
        subject = "Your Oncura ultrasound coverage has lapsed — let's get you covered again"
        opener = (f"Our records show your Oncura Extended Maintenance Agreement (EMA) lapsed on {exp}, "
                  f"so your ultrasound isn't currently covered for repair or replacement.")
        opener_html = (f"Our records show your Oncura <b>Extended Maintenance Agreement (EMA)</b> lapsed on "
                       f"<b>{exp}</b>, so your ultrasound isn't currently covered for repair or replacement.")
        resume = "resumes"
    else:
        subject = "Your Oncura equipment warranty (EMA) is up for renewal"
        opener = f"Your Oncura Extended Maintenance Agreement (EMA) is set to expire on {exp}."
        opener_html = f"Your Oncura <b>Extended Maintenance Agreement (EMA)</b> is set to expire on <b>{exp}</b>."
        resume = "continues"
    plain = (
        f"Hello {clinic},\n\n"
        f"{opener} Renewing keeps it fully protected for another year — Oncura repairs or replaces any "
        f"failed system and provides a loaner while it's serviced.\n\n"
        f"So you have someone to walk you through it, we've set you up with a call with Mark McIlwain, "
        f"who handles EMA renewals — grab whatever time works for you here:\n     {calendly_link}\n\n"
        f"Prefer to skip the conversation and simply renew? You can do it in under a minute — renewal is "
        f"{price} for a 12-month term and coverage {resume} immediately:\n     {payment_link}\n\n"
        f"Per your agreement, the EMA renews for successive 12-month terms unless you cancel in writing — "
        f"please treat this as your renewal notice. Questions? Just reply here.\n\n"
        f"Thank you,\n{SENDER}\n{COMPANY_ADDR}\n"
    )
    html = (
        f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#2A3742;">'
        f"<p>Hello {clinic},</p>"
        f"<p>{opener_html} Renewing keeps it fully protected for another year — Oncura repairs or replaces "
        f"any failed system and provides a loaner while it's serviced.</p>"
        f"<p>So you have someone to walk you through it, we've <b>set you up with a call with Mark McIlwain</b>, "
        f"who handles EMA renewals:</p>"
        f'<p><a href="{calendly_link}" style="background:#2F567E;color:#fff;padding:10px 18px;'
        f'border-radius:6px;text-decoration:none;font-weight:600;">Book your call — pick a time</a></p>'
        f"<p>Prefer to skip the conversation and simply renew? It takes under a minute — renewal is "
        f'<b>{price}</b> for 12 months and coverage {resume} immediately: '
        f'<a href="{payment_link}">renew and pay securely here</a>.</p>'
        f'<p style="color:#6B7785;font-size:12px;">Per your agreement, the EMA renews for successive '
        f"12-month terms unless you cancel in writing — please treat this as your renewal notice.</p>"
        f"<p>Thank you,<br>{SENDER}<br><span style=\"color:#6B7785;\">{COMPANY_ADDR}</span></p></div>"
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
    for c in batch:
        subj, plain, html = email_copy(c["clinic"], c["hardware_end"], payment_link,
                                       "{{ per-clinic Calendly link generated at send }}", status=status)
        plans.append({
            "clinic": c["clinic"], "clinic_id": c["clinic_id"], "state": c["state"],
            "email": c["email"], "expiry": c["hardware_end"].isoformat(), "status": status,
            "days_to_expiry": c.get("days_to_expiry"), "days_expired": c.get("days_expired"),
            "renewal_price": RENEWAL_PRICE, "payment_link": payment_link,
            "subject": subj, "plain": plain, "html": html,
        })
    return plans
