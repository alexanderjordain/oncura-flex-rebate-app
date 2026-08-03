"""All Clinics — a read-only, self-updating roster of every clinic the app knows.

Unions the FLEX/pass-through roster (flex_master), every legal->QBO mapping matched
in Stage 1 (name_map), and the clinics actually paid (processed_payments). Grows on
its own as Stage 1 matches new clinics, so no clinic that flows through the app is
invisible. This view only reports; scan clinics are shown but never fed into the
FLEX credit-memo / recapture math (that stays flex_master-only).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import clinic_roster, loaders, ui

ui.header(
    "All Clinics",
    "Every clinic the app has seen: the FLEX roster, every Stage 1 name match, and "
    "every clinic paid. Read-only, and it grows automatically as new clinics are matched.",
    kicker="Pass-Through · All Clinics",
)

rows = clinic_roster.build(
    loaders.flex_master(), loaders.name_map(), loaders.processed_payments())
summary = clinic_roster.summarize(rows)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total clinics", summary["total"])
m2.metric("FLEX", summary["flex"])
m3.metric("Scan / other", summary["scan"])
m4.metric("Needs review", summary["review"])

if summary["review"]:
    st.warning(
        f"{summary['review']} clinic(s) have payments but are neither a FLEX clinic nor a current "
        "name-map target — likely orphaned or stale (e.g., a name that was mis-mapped and since "
        "corrected). Filter to 'Needs review' below to see them.",
        icon=":material/report:")

f1, f2 = st.columns([1, 2])
type_filter = f1.selectbox("Type", ["All", "FLEX", "Scan / other"])
only_review = f2.checkbox("Only clinics that need review")
search = st.text_input("Search clinic or legal name", placeholder="e.g. Ark")

df = pd.DataFrame(rows)
if type_filter != "All":
    df = df[df["Type"] == type_filter]
if only_review:
    df = df[df["Review"] == "yes"]
if search:
    s = search.strip().lower()
    mask = (df["Clinic (QBO)"].str.lower().str.contains(s, na=False)
            | df["Legal name(s)"].str.lower().str.contains(s, na=False))
    df = df[mask]

st.caption(f"Showing {len(df)} of {len(rows)} clinics.")
st.dataframe(
    df, hide_index=True, use_container_width=True,
    column_config={"Total Paid": st.column_config.NumberColumn(format="$%.2f")},
)
st.caption("Sources: FLEX roster (Clinic Roster page), Stage 1 name matches (name_map), and the "
           "processed-payments ledger. To edit FLEX terms, use the Clinic Roster page; scan clinics "
           "are report-only here and are intentionally excluded from the FLEX credit/recapture math.")
