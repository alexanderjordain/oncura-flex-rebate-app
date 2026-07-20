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
def email_copy(clinic: str, expiry: dt.date, payment_link: str, calendly_link: str) -> tuple[str, str, str]:
    exp = expiry.strftime("%B %d, %Y")
    price = f"${RENEWAL_PRICE:,.0f}"
    subject = "Your Oncura equipment warranty (EMA) is up for renewal"
    plain = (
        f"Hello {clinic},\n\n"
        f"Your Oncura Extended Maintenance Agreement (EMA) expires on {exp}. Renewing keeps your "
        f"ultrasound fully covered for another year — Oncura repairs or replaces any failed system "
        f"and provides a loaner while it's serviced.\n\n"
        f"Renewal is {price} for a 12-month term. Two ways to handle it:\n\n"
        f"  1. Renew now (fastest) — pay securely and you're covered immediately:\n     {payment_link}\n\n"
        f"  2. Prefer to talk it through first? Book a quick call with Mark:\n     {calendly_link}\n\n"
        f"Per your agreement, the EMA renews automatically for successive 12-month terms unless you "
        f"cancel in writing — please treat this as your renewal notice. Questions? Just reply here.\n\n"
        f"Thank you,\n{SENDER}\n{COMPANY_ADDR}\n"
    )
    html = (
        f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#2A3742;">'
        f"<p>Hello {clinic},</p>"
        f"<p>Your Oncura <b>Extended Maintenance Agreement (EMA)</b> expires on <b>{exp}</b>. "
        f"Renewing keeps your ultrasound fully covered for another year — Oncura repairs or replaces "
        f"any failed system and provides a loaner while it's serviced.</p>"
        f"<p>Renewal is <b>{price}</b> for a 12-month term. Two ways to handle it:</p>"
        f'<p><a href="{payment_link}" style="background:#2F567E;color:#fff;padding:10px 18px;'
        f'border-radius:6px;text-decoration:none;font-weight:600;">Renew now — pay securely</a>'
        f"<br><span style=\"color:#6B7785;font-size:12px;\">Coverage continues immediately on payment.</span></p>"
        f'<p>Prefer to talk it through first? <a href="{calendly_link}">Book a quick call with Mark</a>.</p>'
        f'<p style="color:#6B7785;font-size:12px;">Per your agreement, the EMA renews automatically for '
        f"successive 12-month terms unless you cancel in writing — please treat this as your renewal notice.</p>"
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
def build_plan(today: dt.date | None = None, window_days: int = OUTREACH_LEAD_DAYS,
               limit: int | None = None) -> list[dict]:
    """The outreach batch as plan dicts — clinic, recipient, links, and the email
    copy that would be sent. No side effects (Calendly link shown as a placeholder)."""
    today = today or dt.date.today()
    clinics = ema_renewals.fetch_active_ema(auth=_opd_auth())
    batch = ema_renewals.renewal_batch(clinics, today, window_days=window_days)
    if limit:
        batch = batch[:limit]
    payment_link = _cfg("EMA_PAYMENT_LINK")
    plans = []
    for c in batch:
        subj, plain, html = email_copy(c["clinic"], c["hardware_end"], payment_link,
                                       "{{ per-clinic Calendly link generated at send }}")
        plans.append({
            "clinic": c["clinic"], "clinic_id": c["clinic_id"], "state": c["state"],
            "email": c["email"], "expiry": c["hardware_end"].isoformat(),
            "days_to_expiry": c["days_to_expiry"],
            "reach_out_date": c["reach_out_date"].isoformat(),
            "renewal_price": RENEWAL_PRICE, "payment_link": payment_link,
            "subject": subj, "plain": plain, "html": html,
        })
    return plans
