"""EMA Renewals — Phase 1 review (read-only).

Surfaces clinics with an active hardware EMA due for renewal outreach (expiring
within the selected window), pulled live from OPD, with a business-day reach-out
date. No outreach, no writes — the review surface before the HubSpot-quote
(e-sign + payment link) and Calendly-invite phase.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from core import ema_renewals, ui

ui.header(
    "EMA Renewals",
    "Hardware-warranty (EMA) renewals due soon, live from OPD. Read-only review "
    "before outreach.",
    kicker="Admin · EMA Renewals",
)


@st.cache_data(ttl=1800, show_spinner="Pulling EMA data from OPD…")
def _load_ema():
    return ema_renewals.fetch_active_ema()


c1, c2, _c3 = st.columns([1, 1, 3])
window = int(c1.number_input(
    "Days before expiry (window)", min_value=1, max_value=120,
    value=ema_renewals.OUTREACH_LEAD_DAYS, step=1, key="ema_window"))
if c2.button("Refresh from OPD", key="ema_refresh"):
    _load_ema.clear()

try:
    _all = _load_ema()
except Exception as e:  # noqa: BLE001 - surface any OPD/auth error plainly
    st.error(f"Could not pull EMA data from OPD: {type(e).__name__}: {e}")
    st.stop()

today = dt.date.today()
batch = ema_renewals.renewal_batch(_all, today, window_days=window)

st.markdown(
    f"**{len(_all)}** clinics with an active hardware EMA  ·  **{len(batch)}** expiring within "
    f"{window} days  ·  renewal **${ema_renewals.RENEWAL_PRICE:,.0f}**  ·  1-year term from payment."
)

if not batch:
    st.info(f"No active hardware EMAs expiring within {window} days of {today:%b %d, %Y}.")
else:
    due = sum(1 for b in batch if b["due_today"])
    if due:
        st.success(f"{due} clinic(s) hit their business-day reach-out date today.",
                   icon=":material/event_available:")
    st.dataframe(
        pd.DataFrame([{
            "Clinic": b["clinic"],
            "OPD ID": b["clinic_id"],
            "State": b["state"],
            "EMA expires": b["hardware_end"].isoformat(),
            "Days left": b["days_to_expiry"],
            "Reach out (biz day)": b["reach_out_date"].isoformat(),
            "Due today": "yes" if b["due_today"] else "",
            "Contact email": b["email"] or "—",
            "Renewal": b["renewal_price"],
        } for b in batch]),
        hide_index=True, use_container_width=True,
        column_config={"Renewal": st.column_config.NumberColumn(format="$%.0f")},
    )
    st.caption("Read-only. Next phase generates, for each clinic and with your approval, a HubSpot "
               "quote (e-signature + payment link) and a Calendly invite with Mark — nothing sends "
               "automatically.")

st.divider()
st.caption("EMA status is maintained by accounting; this tool never writes it. On payment the "
           "renewal runs 1 year from the payment date and accounting is notified to update the record.")
