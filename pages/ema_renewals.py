"""EMA Renewals — review + renewal outreach.

Top half: read-only review of clinics with an active hardware EMA expiring within
the selected window, live from OPD. Bottom half: the renewal-outreach batch —
preview who would be contacted, and (admin-only, with confirmation) send: for each
clinic a branded renewal email as the sender mailbox plus a pre-arranged call on
Mark's Outlook calendar, logged to HubSpot and the dedup ledger. Sending is the
same code path as scripts/ema_run.py (core.ema_bot).
"""
from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import streamlit as st

from core import auth, ema_bot, ema_graph, ema_ledger, ema_outreach, ema_renewals, ui


def _bridge_secrets_to_env():
    """The EMA engine reads os.environ (it's host-agnostic and runs on Render too).
    On Community Cloud the values live in st.secrets, so mirror the ones it needs
    into the environment for this process."""
    for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
              "EMA_EMAIL_SENDER", "EMA_ORGANIZER", "EMA_PAYMENT_LINK",
              "HUBSPOT_TOKEN", "OPD_ODATA_USER", "OPD_ODATA_PASS"):
        try:
            v = st.secrets.get(k)
        except Exception:
            v = None
        if v and k not in os.environ:
            os.environ[k] = str(v)


_bridge_secrets_to_env()

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


# ── Renewal outreach ──────────────────────────────────────────────────────────
st.divider()
st.subheader("Renewal outreach")

_graph_ok = ema_graph.is_configured()
if _graph_ok:
    st.caption(f"Sending is connected. Emails send as **{ema_bot.sender_mailbox()}**; each call is "
               f"created on **{ema_bot.organizer_mailbox()}**'s calendar and the clinic is invited.")
else:
    st.info("Sending isn't connected yet — add the Microsoft Graph app credentials to enable it "
            "(see `docs/EMA_GRAPH_SETUP.md`). You can still preview the batch below.",
            icon=":material/info:")

with st.expander("Verify sending — do this before the first real batch", expanded=False):
    if not auth.can("admin"):
        st.caption("Admin-only.")
    else:
        st.caption("Confirms the Graph app-only token, calendar access to the organizer, and "
                   "(via a test email) that send-as works — without contacting any clinic.")
        vc1, vc2 = st.columns([1, 2])
        if vc1.button("Test Graph connection", key="ema_test_conn"):
            with st.spinner("Checking token + calendar access…"):
                _res = ema_bot.check_connection()
            (st.success if _res["ok"] else st.error)(_res["detail"])
        _test_addr = vc2.text_input("Send a test email to", key="ema_test_addr",
                                    placeholder="you@oncurapartners.com")
        if vc2.button("Send test email", key="ema_test_send",
                      disabled=not (_test_addr and _graph_ok)):
            with st.spinner("Sending test…"):
                _ok, _info = ema_bot.send_test(_test_addr)
            (st.success if _ok else st.error)(f"Test send: {_info}")

oc1, oc2, oc3 = st.columns([1.4, 1, 2.6])
_mode_label = oc1.selectbox("Batch", ["Expired (backlog)", "Upcoming (expiring soon)"],
                            key="ema_out_mode")
_mode = "expired" if _mode_label.startswith("Expired") else "upcoming"
_cap = int(oc2.number_input("Max this run", min_value=1, max_value=100,
                            value=ema_outreach.PER_RUN_CAP, step=1, key="ema_out_cap"))
oc3.write("")
oc3.write("")
if oc3.button("Preview outreach batch", key="ema_preview"):
    with st.spinner("Building the outreach batch from OPD…"):
        st.session_state["ema_batch"] = ema_bot.plan_batch(mode=_mode, limit=_cap)

_batch = st.session_state.get("ema_batch")
if _batch:
    _capped = _batch["capped"]
    st.markdown(
        f"**{_batch['candidates']}** candidates  ·  **{len(_batch['eligible'])}** eligible  ·  "
        f"**{len(_capped)}** in this run (cap {_cap})  ·  **{len(_batch['skipped'])}** skipped")
    if _capped:
        st.dataframe(pd.DataFrame([{
            "Clinic": p["clinic"], "OPD ID": p["clinic_id"], "State": p["state"],
            "EMA": p["status"], "Expiry": p["expiry"], "Contact email": p["email"],
            "Call (pre-arranged)": f"{p['call_date']}  {p['call_time']}",
        } for p in _capped]), hide_index=True, use_container_width=True)
    if _batch["skipped"]:
        with st.expander(f"Skipped ({len(_batch['skipped'])})"):
            st.dataframe(pd.DataFrame([{"Clinic": p["clinic"], "OPD ID": p["clinic_id"],
                                        "Reason": p["skip_reason"]} for p in _batch["skipped"]]),
                         hide_index=True, use_container_width=True)

    _is_admin = auth.can("admin")
    _can_send = _is_admin and _graph_ok and bool(_capped)
    if not _is_admin:
        st.warning("Sending renewal outreach is admin-only.")
    st.markdown(f"Sending will email **{len(_capped)}** clinic(s) and place a call on "
                f"**{ema_bot.organizer_mailbox()}**'s calendar for each — this contacts real clinics.")
    _initials = ui.initials_input("ema_send", disabled=not _can_send)
    _confirm = st.checkbox(
        f"I reviewed the {len(_capped)} clinic(s) above and approve contacting them.",
        key="ema_confirm", disabled=not _can_send)
    if ui.record_button(f"Send {len(_capped)} renewal outreach", key="ema_send_btn",
                        disabled=not (_can_send and _confirm and _initials)):
        with st.spinner(f"Contacting {len(_capped)} clinic(s)…"):
            _results = ema_bot.send_batch(_capped, _batch["ledger"])
            ema_ledger.save(_batch["ledger"], _batch["sha"],
                            message=f"EMA outreach (app) by {_initials}: {len(_results)} contacted")
        _ok = sum(1 for r in _results if r["mail_ok"] and r["event_ok"])
        st.success(f"Contacted {_ok}/{len(_results)} clinic(s) cleanly. Ledger updated.",
                   icon=":material/mark_email_read:")
        _fails = [r for r in _results if not (r["mail_ok"] and r["event_ok"])]
        if _fails:
            st.warning("Some sends had issues — review and re-run for these:")
            st.dataframe(pd.DataFrame([{
                "Clinic": r["clinic"], "Email sent": "yes" if r["mail_ok"] else r["mail"],
                "Calendar": "yes" if r["event_ok"] else r["event"], "HubSpot note": r["note"],
            } for r in _fails]), hide_index=True, use_container_width=True)
        st.session_state.pop("ema_batch", None)
