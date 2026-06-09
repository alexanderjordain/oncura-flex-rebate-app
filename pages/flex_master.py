"""Pass-Through Clinic Roster — the 82 FLEX/pass-through clinics, their finance
company + contract IDs, calendar group, threshold + credit amounts, active flag.

Sibling of pages/rebate_master.py — same UX pattern (st.data_editor with
column configs), parallel for the FLEX side. Edits persist to
data/flex_master.json via the GitHub Contents API.

Key invariant: clinic_id is the stable join key used by Stage 3 group pooling
(parent_clinic_id refers to it). The editor shows it as a read-only column
and the save logic merges edits back onto the original records by clinic_id
so unedited fields (ema_end, support_ema_end, hardware_ema_end, group_id,
parent_clinic_id) are preserved untouched.
"""
import pandas as pd
import streamlit as st

from core import auth, loaders, store, ui

ui.header(
    "Pass-Through Clinic Roster",
    "The FLEX / pass-through clinics, their finance partner + contract IDs, calendar "
    "group, threshold + credit amounts, and active flag. Edits persist to the repo.",
    kicker="Pass-Through Payments · Reference",
)

editable = auth.can("admin")

master = loaders.flex_master()
clinics = master.get("clinics", [])
df_full = pd.DataFrame(clinics)

# Editable columns (in display order). clinic_id is shown as a read-only column
# so the operator can see it; the merge-back keys on it.
EDIT_COLS = [
    "clinic_id",
    "clinic_name",
    "qb_name",
    "finance_company",
    "calendar_spread",
    "monthly_threshold",
    "quarterly_threshold",
    "monthly_credit",
    "monthly_finance_payment",
    "contract_oneplace",
    "contract_greatamerica",
    "contract_newlane",
    "active",
    "notes",
]
EDIT_COLS = [c for c in EDIT_COLS if c in df_full.columns]
DISABLED_COLS = ["clinic_id"]

# Summary metrics on top
active_count = int(df_full["active"].fillna(False).astype(bool).sum()) if "active" in df_full.columns else 0
by_partner = (
    df_full[df_full["active"].fillna(False).astype(bool)]["finance_company"].value_counts().to_dict()
    if "finance_company" in df_full.columns and "active" in df_full.columns
    else {}
)
m1, m2, m3 = st.columns(3)
m1.metric("Active clinics", active_count)
m2.metric("In roster", len(df_full))
m3.metric("Distinct finance partners", len(by_partner))
if by_partner:
    st.caption(
        ":gray[Active by partner: "
        + " · ".join(f"**{k}**: {v}" for k, v in sorted(by_partner.items(), key=lambda kv: -kv[1]))
        + "]"
    )


st.subheader("Roster")
st.caption(
    "Read-only column: **clinic_id** (stable join key for Stage 3 group pooling — "
    "`parent_clinic_id` on group members points at this). EMA dates and multi-clinic "
    "group fields (`group_id`, `parent_clinic_id`) aren't exposed here — edit those "
    "directly in `data/flex_master.json` for now."
)

if editable:
    edited = st.data_editor(
        df_full[EDIT_COLS],
        use_container_width=True,
        num_rows="dynamic",
        disabled=DISABLED_COLS,
        column_config={
            "clinic_id": st.column_config.TextColumn(
                "Clinic ID (read-only)",
                help="Stable join key — never change. `parent_clinic_id` on member clinics "
                     "points at this. Leave blank when adding a new clinic and a value will be "
                     "assigned on save.",
            ),
            "clinic_name": st.column_config.TextColumn(required=True),
            "qb_name": st.column_config.TextColumn(
                "QB Customer name",
                help="Must match the QuickBooks Display Name exactly — drives the Customer "
                     "column on every SaasAnt import.",
            ),
            "finance_company": st.column_config.SelectboxColumn(
                "Finance partner",
                options=["OnePlace", "GreatAmerica", "NewLane", "FPLeasing", "Self-Financed"],
            ),
            "calendar_spread": st.column_config.SelectboxColumn(
                "Calendar group",
                options=["Calendar", "March-April-May", "May-June-July"],
                help="Determines which month the clinic's quarter ends in — Stage 3 only "
                     "processes clinics whose quarter ends in the run month.",
            ),
            "monthly_threshold": st.column_config.NumberColumn(
                "Monthly threshold", format="$%.2f", min_value=0.0, step=10.0,
            ),
            "quarterly_threshold": st.column_config.NumberColumn(
                "Quarterly threshold", format="$%.2f", min_value=0.0, step=10.0,
                help="3 × monthly_threshold in most contracts; not auto-computed because a few "
                     "contracts have asymmetry.",
            ),
            "monthly_credit": st.column_config.NumberColumn(
                "Monthly credit", format="$%.2f", min_value=0.0, step=1.0,
                help="The Flex-credits amount Stage 2 generates against this clinic each month.",
            ),
            "monthly_finance_payment": st.column_config.NumberColumn(
                "Monthly finance payment", format="$%.2f", min_value=0.0, step=1.0,
                help="What the finance company wires to Oncura per month for this clinic.",
            ),
            "contract_oneplace": st.column_config.TextColumn("OnePlace contract"),
            "contract_greatamerica": st.column_config.TextColumn("GreatAmerica contract"),
            "contract_newlane": st.column_config.TextColumn("NewLane contract"),
            "active": st.column_config.CheckboxColumn(
                help="Inactive clinics are excluded from every cycle. Prefer marking inactive "
                     "over deleting — preserves the audit trail.",
            ),
            "notes": st.column_config.TextColumn(width="large"),
        },
        key="flex_roster_editor",
    )

    if st.button("Save roster", key="save_flex_roster_btn", type="primary"):
        # Merge edits back into the original records keyed by clinic_id so that
        # unedited fields (ema_end, support_ema_end, hardware_ema_end, group_id,
        # parent_clinic_id) survive the save. data_editor returns ONLY the visible
        # columns, so a naive to_dict('records') would silently drop them.
        original_by_id = {
            c.get("clinic_id"): c for c in clinics
            if c.get("clinic_id")
        }
        new_clinics: list[dict] = []
        for _, row in edited.iterrows():
            edited_record = {k: (None if pd.isna(v) else v) for k, v in row.to_dict().items()}
            cid = edited_record.get("clinic_id")
            base = dict(original_by_id.get(cid, {})) if cid else {}
            # Edits win over the original; original supplies the unedited fields.
            merged = {**base, **{k: v for k, v in edited_record.items() if k in EDIT_COLS}}
            # New rows without a clinic_id get a placeholder so the next save
            # cycle can match them — operator can edit the value if a better
            # one (the standard 3-letter-plus-numeric ID format) is known.
            if not merged.get("clinic_id"):
                base_id = "".join(ch for ch in str(merged.get("clinic_name") or "NEW").upper() if ch.isalnum())[:6] or "NEW"
                suffix = str(len(new_clinics) + 1).zfill(3)
                merged["clinic_id"] = f"{base_id}{suffix}"
            new_clinics.append(merged)

        payload = dict(master)
        payload["clinics"] = new_clinics
        ok, info = store.save_json(
            "flex_master.json", payload,
            f"Update flex clinic roster ({len(new_clinics)} clinic(s))",
        )
        loaders.clear_caches()
        if ok:
            st.success(info)
        else:
            st.warning(info)
else:
    st.dataframe(df_full[EDIT_COLS], use_container_width=True, hide_index=True)
    auth.require("admin")
