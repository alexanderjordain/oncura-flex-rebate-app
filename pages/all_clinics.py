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
    loaders.flex_master(), loaders.name_map(), loaders.processed_payments(),
    loaders.clinic_overrides())
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
    # Editable: Clinic (QBO), Legal name(s), Finance Co, Contracts, Type, Active.
    # Read-only: the ledger-derived metrics (they're facts, not settings).
    _readonly = ["Payments", "Total Paid", "Last Payment", "Review"]
    _orig_records = df.to_dict("records")
    _orig_qb = list(df["Clinic (QBO)"]) if len(df) else []
    _colcfg_edit = {
        **_colcfg,
        "Legal name(s)": st.column_config.TextColumn(
            "Legal name(s)",
            help=("Legal / remittance names that resolve to this QBO customer (the name_map). "
                  "Edit to fix a mis-typed legal name; separate multiple names with '; '. "
                  "Adds/removes are written to the name_map so future Stage 1 matches resolve correctly."),
        ),
        "Type": st.column_config.SelectboxColumn("Type", options=["FLEX", "Scan / other"]),
        "Active": st.column_config.SelectboxColumn("Active", options=["", "yes", "no"]),
        "Finance Co": st.column_config.TextColumn("Finance Co"),
        "Contracts": st.column_config.TextColumn("Contracts"),
    }
    _edited = st.data_editor(
        df, hide_index=True, use_container_width=True, num_rows="fixed",
        disabled=_readonly, column_config=_colcfg_edit, key="all_clinics_editor",
    )
    if st.button("Save changes", type="primary"):
        _edited_records = _edited.to_dict("records")
        # 1) Field edits (legal names / finance co / contracts / type / active), keyed on
        #    each row's ORIGINAL QBO name — must run before the QBO-name repoint below.
        _nm, _fm, _ov, _fs = clinic_roster.apply_roster_edits(
            _orig_records, _edited_records,
            loaders.name_map(), loaders.flex_master(), loaders.clinic_overrides())
        # 2) QBO-name edits: repoint legal mappings (scan) / reassign orphan payments;
        #    FLEX QBO names still live in flex_master (edited on the FLEX Clinic Roster).
        _new_qb = list(_edited["Clinic (QBO)"])
        _changes = [(o, n) for o, n in zip(_orig_qb, _new_qb) if str(n).strip() != str(o).strip()]
        _updated_nm, _n_clinics, _n_legals, _skipped = clinic_roster.apply_qb_edits(_nm, _changes)
        _flex_names = {" ".join(str(c.get("qb_name") or c.get("clinic_name") or "").lower().split())
                       for c in _fm.get("clinics", [])}
        _orphan_changes = [(o, n) for o, n in _skipped
                           if " ".join(str(o).lower().split()) not in _flex_names]
        _flex_changes = [(o, n) for o, n in _skipped
                         if " ".join(str(o).lower().split()) in _flex_names]
        _updated_pp, _n_pay, _reassigned = clinic_roster.reassign_payments(
            loaders.processed_payments(), _orphan_changes)

        _did = False
        _msgs = []
        if _fs["legal_added"] or _fs["legal_removed"] or _n_clinics:
            _ok, _info = store.save_json(
                "name_map.json", _updated_nm,
                f"All Clinic Roster: legal/QBO name edits (+{_fs['legal_added']}/-{_fs['legal_removed']} "
                f"legal(s), {_n_clinics} QBO repointed)")
            _did = True
            _msgs.append((_ok, f"name_map — +{_fs['legal_added']} / -{_fs['legal_removed']} legal name(s); "
                               f"{_n_clinics} QBO repoint ({_n_legals} moved). {_info}"))
        if _fs["flex_fields"]:
            _ok, _info = store.save_json(
                "flex_master.json", _fm, f"All Clinic Roster: {_fs['flex_fields']} FLEX field edit(s)")
            _did = True
            _msgs.append((_ok, f"flex_master — {_fs['flex_fields']} field edit(s). {_info}"))
        if _fs["override_fields"]:
            _ok, _info = store.save_json(
                "clinic_overrides.json", _ov,
                f"All Clinic Roster: {_fs['override_fields']} clinic override field(s)")
            _did = True
            _msgs.append((_ok, f"clinic_overrides — {_fs['override_fields']} field(s). {_info}"))
        if _n_pay:
            _ok, _info = store.save_json(
                "processed_payments.json", _updated_pp,
                f"All Clinic Roster: reassign {_n_pay} orphan payment(s) to corrected customer")
            _did = True
            _msgs.append((_ok, f"reassigned {_n_pay} orphan payment(s) across {len(_reassigned)} clinic(s) "
                               f"({', '.join(_reassigned)}). Still reclass these in QuickBooks. {_info}"))
        if _flex_changes:
            st.warning("FLEX clinic QBO names live in flex_master — change them on the FLEX Clinic "
                       "Roster, not here: " + ", ".join(f"{o} → {n}" for o, n in _flex_changes),
                       icon=":material/info:")
        if _did:
            loaders.clear_caches()
            for _ok, _m in _msgs:
                (st.success if _ok else st.warning)(_m)
            st.rerun()
        elif not _flex_changes:
            st.info("No changes to save.")
else:
    st.dataframe(df, hide_index=True, use_container_width=True, column_config=_colcfg)

st.caption("Sources: FLEX roster (flex_master), Stage 1 name matches (name_map), and the "
           "processed-payments ledger. Editable here: Clinic (QBO), Legal name(s), Finance Co, "
           "Contracts, Type, Active. Legal/QBO name edits write to the name_map (so future remittances "
           "resolve correctly); Finance Co / Active on a FLEX clinic write to flex_master; everything "
           "else (and all scan/other-clinic fields) writes to clinic_overrides.json, a display/correction "
           "layer. Ledger metrics (Payments, Total Paid, Last Payment, Review) are computed and read-only. "
           "NOTE: none of this renames the customer or moves postings in QuickBooks — reclass those there "
           "so QBO matches. Changing Type here only relabels the roster; it does NOT add a clinic to the "
           "FLEX credit-memo / recapture math (that requires a flex_master record on the FLEX Clinic Roster).")
