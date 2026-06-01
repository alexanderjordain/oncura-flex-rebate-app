import pandas as pd
import streamlit as st

from core import auth, loaders, store, ui

ui.header("Rebate Clinic Roster", "The clinic roster, program types, and rebate rates. Edits persist to the repo.",
          kicker="Rebates · Reference")

master = loaders.rebate_master()
clinics = master.get("clinics", [])
df = pd.DataFrame(clinics)

rates = master.get("rate_defaults", {})
st.write(
    f"Defaults — ultrasound: finance **{rates.get('ultrasound_finance')}** / "
    f"self-funded **{rates.get('ultrasound_self_funded')}**  ·  rads: finance "
    f"**{rates.get('rads_finance')}** / self-funded **{rates.get('rads_self_funded')}**"
)

editable = auth.can("admin")
edit_cols = [
    "clinic_name", "legal_name", "finance_company", "program_type",
    "rate_ultrasound", "rate_rads", "rads_rate_confirmed", "active", "notes",
]
edit_cols = [c for c in edit_cols if c in df.columns]

st.subheader("Clinics")
if editable:
    edited = st.data_editor(
        df[edit_cols],
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "program_type": st.column_config.SelectboxColumn(options=["self_funded", "finance"]),
            "rate_ultrasound": st.column_config.NumberColumn(format="%.4f"),
            "rate_rads": st.column_config.NumberColumn(format="%.4f"),
            "active": st.column_config.CheckboxColumn(),
            "rads_rate_confirmed": st.column_config.CheckboxColumn(),
        },
        key="rebate_editor",
    )
    msg = st.text_input("Commit message", value="Update rebate clinic roster")
    if st.button("Save roster"):
        new_clinics = edited.to_dict(orient="records")
        payload = dict(master)
        payload["clinics"] = new_clinics
        ok, info = store.save_json("rebate_master.json", payload, msg)
        loaders.clear_caches()
        (st.success if ok else st.warning)(info)
else:
    st.dataframe(df[edit_cols], use_container_width=True)
    auth.require("admin")

st.caption(
    "Self-funded rads rate is set to 2% (half of the 4% finance rate, per the OPD feed). "
    "Adjustable per clinic above; can be revisited later."
)
