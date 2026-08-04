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

from core import auth, clinic_roster, loaders, store, ui

ui.header(
    "All Clinic Roster",
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

_editable = auth.can("admin")
_colcfg = {
    "Total Paid": st.column_config.NumberColumn(format="$%.2f"),
    "Clinic (QBO)": st.column_config.TextColumn(
        "Clinic (QBO)",
        help=("The QuickBooks customer name this clinic resolves to. Editing it repoints "
              "every legal name that maps to it (the name_map), so future finance remittances "
              "book to the corrected customer. Must match the QuickBooks Display Name exactly."),
    ),
}

if _editable:
    # Only the QBO name is editable; everything else is derived/read-only. Capture the
    # pre-edit QBO name per row so save can diff old -> new and repoint the mappings.
    _orig_qb = list(df["Clinic (QBO)"]) if len(df) else []
    _edited = st.data_editor(
        df, hide_index=True, use_container_width=True, num_rows="fixed",
        disabled=[c for c in df.columns if c != "Clinic (QBO)"],
        column_config=_colcfg, key="all_clinics_editor",
    )
    if st.button("Save QBO name changes", type="primary"):
        _new_qb = list(_edited["Clinic (QBO)"])
        _changes = [(o, n) for o, n in zip(_orig_qb, _new_qb) if str(n).strip() != str(o).strip()]
        if not _changes:
            st.info("No QBO name changes to save.")
        else:
            _nm = loaders.name_map()
            # 1) Repoint any legal-name mappings (scan clinics matched via name_map).
            _updated_nm, _n_clinics, _n_legals, _skipped = clinic_roster.apply_qb_edits(_nm, _changes)
            # 2) The rest have no legal mapping. Split them: payment-only orphans get
            #    their ledger payments reassigned; FLEX clinics are edited on the FLEX
            #    Clinic Roster (their QBO name lives in flex_master, not here).
            _flex_names = {" ".join(str(c.get("qb_name") or c.get("clinic_name") or "").lower().split())
                           for c in loaders.flex_master().get("clinics", [])}
            _orphan_changes = [(o, n) for o, n in _skipped
                               if " ".join(str(o).lower().split()) not in _flex_names]
            _flex_changes = [(o, n) for o, n in _skipped
                             if " ".join(str(o).lower().split()) in _flex_names]
            _pp = loaders.processed_payments()
            _updated_pp, _n_pay, _reassigned = clinic_roster.reassign_payments(_pp, _orphan_changes)

            _did = False
            if _n_clinics:
                _ok, _info = store.save_json(
                    "name_map.json", _updated_nm,
                    f"All Clinic Roster: repoint {_n_clinics} QBO name(s) ({_n_legals} mapping(s))")
                loaders.clear_caches()
                (st.success if _ok else st.warning)(
                    f"Repointed {_n_clinics} clinic mapping(s), {_n_legals} legal name(s). {_info}")
                _did = True
            if _n_pay:
                _ok2, _info2 = store.save_json(
                    "processed_payments.json", _updated_pp,
                    f"All Clinic Roster: reassign {_n_pay} orphan payment(s) to corrected customer")
                loaders.clear_caches()
                (st.success if _ok2 else st.warning)(
                    f"Reassigned {_n_pay} orphan payment(s) across {len(_reassigned)} clinic(s) "
                    f"({', '.join(_reassigned)}). Note: still reclass these in QuickBooks so QBO "
                    f"matches. {_info2}")
                _did = True
            if _flex_changes:
                st.warning(
                    "These are FLEX clinics — change the QBO name on the FLEX Clinic Roster, not "
                    "here (it lives in flex_master): "
                    + ", ".join(f"{o} → {n}" for o, n in _flex_changes),
                    icon=":material/info:")
            if _did:
                st.rerun()
else:
    st.dataframe(df, hide_index=True, use_container_width=True, column_config=_colcfg)

st.caption("Sources: FLEX roster (FLEX Clinic Roster page), Stage 1 name matches (name_map), and the "
           "processed-payments ledger. Editing a clinic's QBO name repoints its legal-name mappings "
           "(name_map) so future remittances resolve correctly; for a payment-only orphan (no mapping, "
           "flagged 'needs review') it instead reassigns that clinic's ledger payments to the corrected "
           "customer. Either way it does NOT rename the customer or move postings in QuickBooks — "
           "reclass those there so QBO matches. FLEX names are edited on the FLEX Clinic Roster.")
