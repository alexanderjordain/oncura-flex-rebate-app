"""EMA (hardware-warranty) renewal engine.

Phase 1 (this module): read-only discovery — pull clinics with an active hardware
EMA from OPD and surface those due for renewal outreach (expiring within a
window), with a business-day-adjusted reach-out date. No outreach, no writes.

Later phases add the HubSpot quote (e-signature + payment link) and the Calendly
invite. Renewal is $4,500 for a 1-year term dated from the payment date. EMA
status itself is maintained by the accounting department — this tool never
writes it; on payment it notifies accounting.
"""
from __future__ import annotations

import datetime as dt

from . import opd_api

RENEWAL_PRICE = 4500.00
TERM_MONTHS = 12            # 1 year, dated from the payment date
OUTREACH_LEAD_DAYS = 14     # reach out this many days before expiry (business day)

CLINIC_PATH = "https://telehealth.oncurapartners.com/odata/Consults/Clinic"
_EMA_SELECT = ("ClinicName,ClinicID,State,City,HardwareEMA,HardwareEMAEndDate,"
               "SupportEMA,SupportEMAEndDate,AdminEmail,BillingEmail,"
               "LastInstallDate,OriginalInstallDate")


def _date(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def prev_business_day(d: dt.date) -> dt.date:
    """Move a date back to the nearest weekday (Mon-Fri)."""
    while d.weekday() >= 5:  # 5 = Sat, 6 = Sun
        d -= dt.timedelta(days=1)
    return d


def reach_out_date(expiry: dt.date) -> dt.date:
    """Business-day-adjusted outreach date: OUTREACH_LEAD_DAYS before expiry."""
    return prev_business_day(expiry - dt.timedelta(days=OUTREACH_LEAD_DAYS))


def fetch_active_ema(auth=None):
    """Every clinic with an active hardware EMA and an end date, live from OPD.
    Read-only. Returns a list of dicts sorted by hardware EMA expiry."""
    auth = auth or opd_api.auth_from_secrets()
    rows, _ = opd_api._fetch_all(
        CLINIC_PATH, auth=auth,
        params={"$filter": "HardwareEMA eq true and HardwareEMAEndDate ne null",
                "$select": _EMA_SELECT})
    out = []
    for r in rows:
        end = _date(r.get("HardwareEMAEndDate"))
        if not end:
            continue
        out.append({
            "clinic": (r.get("ClinicName") or "").strip(),
            "clinic_id": (r.get("ClinicID") or "").strip(),
            "state": (r.get("State") or "").strip(),
            "city": (r.get("City") or "").strip(),
            "hardware_end": end,
            "support_end": _date(r.get("SupportEMAEndDate")),
            "admin_email": (r.get("AdminEmail") or "").strip(),
            "billing_email": (r.get("BillingEmail") or "").strip(),
        })
    out.sort(key=lambda x: x["hardware_end"])
    return out


def renewal_batch(clinics, today: dt.date, window_days: int = OUTREACH_LEAD_DAYS):
    """Clinics whose hardware EMA expires within `window_days` of `today` — the
    outreach batch. Each row carries days-to-expiry, the business-day reach-out
    date, whether it is due today, the best contact email, and the renewal price.
    """
    batch = []
    for c in clinics:
        days = (c["hardware_end"] - today).days
        if 0 <= days <= window_days:
            ro = reach_out_date(c["hardware_end"])
            batch.append({
                **c,
                "days_to_expiry": days,
                "reach_out_date": ro,
                "due_today": ro == today,
                "email": c["admin_email"] or c["billing_email"],
                "renewal_price": RENEWAL_PRICE,
            })
    batch.sort(key=lambda x: x["hardware_end"])
    return batch
