"""Overage Tracker — billed-and-paid state for every overage direct-bill.

Purpose: give operations a single place to see which clinics owe on an
overage invoice, mark payments as they arrive, and see who's crossed the
3-month unpaid threshold that triggers service lockout.

Data source: `data/overage_ledger.json` via `core/overage_ledger`.
  - New entries are written automatically by the FLEX Cycle Stage 3 overage
    step (see `pages/flex_cycle.py`).
  - Historical / manual entries can be added on this page (admin only).
  - Mark-paid updates happen in the row action expander.

Lockout policy (operator, 2026-07-14):
  Any overage unpaid 3+ calendar months from `date_billed` locks the clinic
  out of Oncura service until paid. 2-3 months = warning band. This page
  computes lockout status live from each row's date_billed / paid_at.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from core import auth, overage_ledger as ol, ui

ui.header(
    "Overage Tracker",
    "Every overage that's been billed to a clinic, its payment state, and "
    "whether it's crossed the 3-month lockout threshold.",
    kicker="Pass-Through Payments · Overage Tracker",
)

is_admin = auth.can("admin")
actor = auth.current_role()

today = dt.date.today()
entries = ol.all_entries()
summary = ol.summarize(today)

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------

k1, k2, k3, k4 = st.columns(4)
k1.metric("Open balance",   f"${summary['total_open']:,.0f}")
k2.metric("Locked out",     summary["counts"][ol.STATUS_LOCKED_OUT],
          delta=f"${summary['total_locked_out']:,.0f} unpaid",
          delta_color="inverse")
k3.metric("At risk (2-3 mo)", summary["counts"][ol.STATUS_WARNING])
k4.metric("Paid entries",    summary["counts"][ol.STATUS_PAID],
          delta=f"${summary['total_collected']:,.0f} collected")

# ---------------------------------------------------------------------------
# Locked-out list — surface this above the table so it's unmissable
# ---------------------------------------------------------------------------

if summary["locked_out_clinics"]:
    st.error(
        "**Currently locked out (unpaid overage 3+ months old):**\n\n"
        + "\n".join(f"- {c}" for c in summary["locked_out_clinics"])
    )
else:
    st.success("No clinics currently locked out.")

st.divider()

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

if not entries:
    st.info(
        "No overage records yet. Overages will appear here automatically as "
        "the FLEX Cycle Stage 3 direct-bill step runs; you can also add a "
        "historical entry below."
    )
else:
    filt_l, filt_r, filt_search = st.columns([1, 1, 2])
    status_choice = filt_l.selectbox(
        "Status",
        options=["All", "Locked out", "Warning", "Open", "Paid"],
        index=0,
    )
    route_choice = filt_r.selectbox(
        "Route",
        options=["All", "direct", "partner", "missed_cutoff"],
        index=0,
    )
    search = filt_search.text_input("Search clinic", "", placeholder="clinic or QB customer name...")

    def _status_match(entry):
        st_ = ol.status(entry, today)
        if status_choice == "All":
            return True
        return {
            "Locked out": st_ == ol.STATUS_LOCKED_OUT,
            "Warning":    st_ == ol.STATUS_WARNING,
            "Open":       st_ == ol.STATUS_OPEN,
            "Paid":       st_ == ol.STATUS_PAID,
        }[status_choice]

    filtered = [
        e for e in entries
        if _status_match(e)
        and (route_choice == "All" or e.get("route") == route_choice)
        and (
            not search
            or search.lower() in (e.get("clinic", "") + " " + e.get("qb_customer", "")).lower()
        )
    ]

    # ---------------------------------------------------------------------------
    # Table
    # ---------------------------------------------------------------------------

    def _row(e):
        st_ = ol.status(e, today)
        days_until = ol.days_until_lockout(e, today)
        return {
            "id": e["id"],
            "Status": {
                ol.STATUS_LOCKED_OUT: "Locked out",
                ol.STATUS_WARNING:    "Warning",
                ol.STATUS_OPEN:       "Open",
                ol.STATUS_PAID:       "Paid",
            }[st_],
            "Clinic": e.get("clinic", ""),
            "QB Customer": e.get("qb_customer", ""),
            "Billing Month": e.get("billing_month", ""),
            "Quarter": e.get("quarter_covered", ""),
            "Route": e.get("route", ""),
            "Net Amount": float(e.get("net_amount") or 0),
            "Date Billed": e.get("date_billed", ""),
            "Invoice #": e.get("invoice_no", ""),
            "Days To/Past Lockout": (
                "" if days_until is None
                else f"{-days_until} past" if days_until < 0
                else f"in {days_until}"
            ),
            "Paid At": e.get("paid_at") or "",
            "Paid Amount": float(e.get("paid_amount") or 0) if e.get("paid_amount") is not None else None,
            "Notes": e.get("notes", ""),
        }

    df = pd.DataFrame([_row(e) for e in filtered])
    if not df.empty:
        # Sort: locked_out first, then warning, then open, then paid; each by oldest date_billed
        status_rank = {"Locked out": 0, "Warning": 1, "Open": 2, "Paid": 3}
        df["_rank"] = df["Status"].map(status_rank)
        df = df.sort_values(by=["_rank", "Date Billed"], ascending=[True, True]).drop(columns=["_rank"])

        st.dataframe(
            df.drop(columns=["id"]),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Net Amount": st.column_config.NumberColumn(format="$%.2f"),
                "Paid Amount": st.column_config.NumberColumn(format="$%.2f"),
            },
        )

        # ---------------------------------------------------------------------
        # Row actions — pick a row, mark paid / edit / delete
        # ---------------------------------------------------------------------

        st.markdown("#### Row actions")
        picker_labels = [
            f"{r['Clinic']} — {r['Billing Month']} — ${r['Net Amount']:,.0f} — {r['Status']}"
            for _, r in df.iterrows()
        ]
        id_by_label = {lbl: rid for lbl, rid in zip(picker_labels, df["id"].tolist())}
        selected_label = st.selectbox("Pick a row", options=[""] + picker_labels)
        if selected_label:
            selected_id = id_by_label[selected_label]
            entry = ol.get(selected_id)
            if entry:
                col_a, col_b = st.columns([2, 1])
                with col_a:
                    if entry.get("paid_at"):
                        st.info(
                            f"Already marked paid: ${entry['paid_amount']:,.2f} on {entry['paid_at']}"
                            + (f" · {entry.get('paid_note')}" if entry.get("paid_note") else "")
                        )
                        if is_admin and st.button("Unmark paid", key=f"unmark_{selected_id}"):
                            ol.unmark_paid(selected_id, actor=actor)
                            st.success("Unmarked. Refresh to reflect.")
                            st.rerun()
                    else:
                        with st.form(f"mark_paid_{selected_id}"):
                            st.markdown("**Mark this overage paid**")
                            paid_amount = st.number_input(
                                "Amount received ($)",
                                min_value=0.0,
                                value=float(entry.get("net_amount") or 0),
                                step=0.01, format="%.2f",
                            )
                            paid_date = st.date_input("Date received", value=today, max_value=today)
                            paid_note = st.text_input(
                                "Note (optional)",
                                placeholder="e.g. QBO invoice #12345 paid via check 06/21",
                            )
                            submitted = st.form_submit_button("Mark paid", type="primary")
                            if submitted:
                                ol.mark_paid(
                                    selected_id,
                                    paid_amount=paid_amount,
                                    paid_date=paid_date.isoformat(),
                                    note=paid_note,
                                    actor=actor,
                                )
                                st.success("Marked paid.")
                                st.rerun()
                with col_b:
                    if is_admin:
                        confirm = st.checkbox(
                            "Confirm delete (removes this ledger row)",
                            key=f"confirm_delete_{selected_id}",
                        )
                        if st.button("Delete row", disabled=not confirm, key=f"delete_{selected_id}"):
                            ol.delete(selected_id, actor=actor)
                            st.success("Deleted.")
                            st.rerun()

# ---------------------------------------------------------------------------
# Add historical / manual entry (admin)
# ---------------------------------------------------------------------------

if is_admin:
    st.divider()
    with st.expander("Add historical overage entry (backfill)", expanded=False):
        with st.form("add_historical"):
            c1, c2 = st.columns(2)
            clinic = c1.text_input("Clinic name")
            qb_customer = c2.text_input("QB Customer (canonical)")
            c3, c4, c5 = st.columns(3)
            billing_month = c3.text_input(
                "Billing month (YYYY-MM)",
                value=f"{today.year:04d}-{today.month:02d}",
                help="Month you sent the overage bill.",
            )
            quarter_covered = c4.text_input("Quarter covered", value="", placeholder="Q1 2026")
            route = c5.selectbox("Route", options=["direct", "partner", "missed_cutoff"], index=0)
            c6, c7, c8 = st.columns(3)
            gross = c6.number_input("Gross overage ($)", min_value=0.0, step=0.01, format="%.2f")
            credit = c7.number_input("Credit applied ($)", min_value=0.0, step=0.01, format="%.2f")
            net = c8.number_input("Net amount billed ($)", min_value=0.0, step=0.01, format="%.2f")
            c9, c10 = st.columns(2)
            date_billed = c9.date_input("Date billed", value=today, max_value=today)
            invoice_no = c10.text_input("QBO invoice # (optional)")
            notes = st.text_area("Notes", value="", placeholder="Anything you want on the record.")
            submitted = st.form_submit_button("Add entry", type="primary")
            if submitted:
                if not clinic or not qb_customer or not billing_month:
                    st.error("Clinic, QB Customer, and Billing Month are required.")
                elif net <= 0:
                    st.error("Net amount must be > 0.")
                else:
                    try:
                        eid = ol.upsert(
                            {
                                "clinic": clinic,
                                "qb_customer": qb_customer,
                                "billing_month": billing_month,
                                "quarter_covered": quarter_covered,
                                "route": route,
                                "gross_overage": gross,
                                "credit_applied": credit,
                                "net_amount": net,
                                "date_billed": date_billed.isoformat(),
                                "invoice_no": invoice_no,
                                "notes": notes,
                            },
                            actor=actor,
                        )
                        st.success(f"Added / updated entry {eid[:8]}...")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Save failed: {e}")
