"""Rebate Program Controls — clinic roster + service price list.

Two sub-pages:
  1. Clinic roster — the 87 rebate-program clinics, their program type, per-clinic
     rate overrides, active flag, notes.
  2. Service price list — service name -> {price, category}. The category drives
     the rebate-vs-excluded classification in rebate_calc. Service NAMES must
     match OPD case-grid Service strings character-for-character — fuzzy match
     is not used here.
"""
import pandas as pd
import streamlit as st

from core import auth, loaders, store, ui

ui.header(
    "Rebate Program Controls",
    "Clinic roster, rebate rates, and the service price list that drives rebate categorization. "
    "Edits persist to the repo.",
    kicker="Rebates · Reference",
)

editable = auth.can("admin")

tab_clinics, tab_prices = st.tabs(["Clinic roster", "Service price list"])


# ─────────────────────────────────────────────────────────────────────────────
# Clinic roster
# ─────────────────────────────────────────────────────────────────────────────
with tab_clinics:
    master = loaders.rebate_master()
    clinics = master.get("clinics", [])
    df = pd.DataFrame(clinics)

    rates = master.get("rate_defaults", {})

    def _pct(v):
        try:
            return f"{float(v):.0%}"
        except (TypeError, ValueError):
            return "—"

    st.write(
        f"Defaults — ultrasound: finance **{_pct(rates.get('ultrasound_finance'))}** / "
        f"self-funded **{_pct(rates.get('ultrasound_self_funded'))}**  ·  rads: finance "
        f"**{_pct(rates.get('rads_finance'))}** / self-funded **{_pct(rates.get('rads_self_funded'))}**"
    )

    edit_cols = [
        "clinic_name", "legal_name", "finance_company", "program_type",
        "rate_ultrasound", "rate_rads", "rads_rate_confirmed", "active", "notes",
    ]
    edit_cols = [c for c in edit_cols if c in df.columns]

    st.subheader("Rebate Clinic Roster")
    if editable:
        edited = st.data_editor(
            df[edit_cols],
            use_container_width=True,
            num_rows="dynamic",
            column_config={
                "program_type": st.column_config.SelectboxColumn(options=["self_funded", "finance"]),
                "rate_ultrasound": st.column_config.NumberColumn(
                    "Rate — ultrasound",
                    format="percent", min_value=0.0, max_value=1.0, step=0.01,
                    help="Stored as a decimal (0.10 = 10%). Edit as a percent and Streamlit handles the conversion.",
                ),
                "rate_rads": st.column_config.NumberColumn(
                    "Rate — rads",
                    format="percent", min_value=0.0, max_value=1.0, step=0.01,
                ),
                "active": st.column_config.CheckboxColumn(),
                "rads_rate_confirmed": st.column_config.CheckboxColumn(),
            },
            key="rebate_editor",
        )
        if st.button("Save roster", key="save_roster_btn"):
            new_clinics = edited.to_dict(orient="records")
            payload = dict(master)
            payload["clinics"] = new_clinics
            ok, info = store.save_json("rebate_master.json", payload, "Update rebate clinic roster")
            loaders.clear_caches()
            (st.success if ok else st.warning)(info)
    else:
        st.dataframe(df[edit_cols], use_container_width=True)
        auth.require("admin")

    st.caption(
        "Current rate scheme (set 2026-06-09): ultrasound 10% finance / 8% self-funded; "
        "rads 5% finance / 4% self-funded. Per-clinic overrides editable above."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Service price list
# ─────────────────────────────────────────────────────────────────────────────
with tab_prices:
    prices_doc = loaders.service_prices()
    services = dict(prices_doc.get("services", {}))

    st.error(
        "**Service names must match OPD exactly — character-for-character, including "
        "punctuation, capitalization, and spacing.** The rebate calculator looks up "
        "service prices by exact string match against the OPD case-grid `Services` "
        "column. A typo here means that service won't be classified or counted in "
        "the rebate. If you're adding a new service, copy the name straight from an "
        "OPD export and paste it in — don't retype.",
        icon=":material/warning:",
    )

    st.markdown(
        f"**{len(services)}** services configured · "
        f"STAT-priority fee (added when no STAT line is present): "
        f"**${prices_doc.get('stat_fee', 125.0):,.2f}**"
    )

    with st.expander("What the category field means"):
        st.markdown(
            """
- **`ultrasound`** — counts toward the **ultrasound rebate** (10% finance / 8% self-funded by default).
- **`rads`** — counts toward the **rads rebate** (5% finance / 4% self-funded by default).
- **`stat`** — STAT priority fees. Not directly rebatable, but the implicit
  $125 STAT fee is added when STAT-priority cases have no STAT line.
- **`assistance`** — case-prep / assist fees (e.g., abdominal assist). Excluded
  from rebate.
- **`non_ema`** / **`other`** — non-rebatable services. Reported but not counted.

Changing a service's category re-routes its revenue between the rebate
buckets on the next cycle run.
            """
        )

    rows = [
        {"service": name, "price": float(v.get("price", 0.0)), "category": v.get("category", "other")}
        for name, v in services.items()
    ]
    rows.sort(key=lambda r: r["service"].lower())
    df_prices = pd.DataFrame(rows, columns=["service", "price", "category"])

    categories = ["ultrasound", "rads", "stat", "assistance", "non_ema", "other"]

    st.subheader("Services")
    if editable:
        edited_prices = st.data_editor(
            df_prices,
            use_container_width=True,
            num_rows="dynamic",
            column_config={
                "service": st.column_config.TextColumn(
                    "Service name (must match OPD exactly)",
                    help="Paste from OPD — don't retype. Case, spacing, and punctuation are all significant.",
                    required=True,
                ),
                "price": st.column_config.NumberColumn(
                    "Price (USD)", format="$%.2f", min_value=0.0, step=1.0, required=True,
                ),
                "category": st.column_config.SelectboxColumn(
                    "Category", options=categories, required=True,
                ),
            },
            key="prices_editor",
            hide_index=True,
        )

        # STAT fee editor (separate from the services table)
        sc1, sc2 = st.columns([1, 3])
        with sc1:
            new_stat_fee = st.number_input(
                "STAT-priority fee",
                value=float(prices_doc.get("stat_fee", 125.0)),
                min_value=0.0,
                step=1.0,
                format="%.2f",
                key="stat_fee_input",
            )
        with sc2:
            st.caption(
                "Added automatically to STAT-priority cases when the OPD `Services` "
                "string doesn't already list a STAT line. Rarely changes."
            )

        if st.button("Save price list", key="save_prices_btn"):
            # Rebuild the services dict from the editor. Skip blank-service rows
            # so accidental empty rows don't poison the file.
            new_services = {}
            dupes = []
            blanks = 0
            for _, row in edited_prices.iterrows():
                name = (row.get("service") or "").strip()
                if not name:
                    blanks += 1
                    continue
                if name in new_services:
                    dupes.append(name)
                    continue
                new_services[name] = {
                    "price": float(row.get("price") or 0.0),
                    "category": (row.get("category") or "other"),
                }

            if dupes:
                st.error(
                    f"Duplicate service name(s) — each name must be unique: "
                    + ", ".join(f"`{d}`" for d in dupes[:5])
                    + ("…" if len(dupes) > 5 else "")
                )
            else:
                payload = dict(prices_doc)
                payload["services"] = new_services
                payload["stat_fee"] = float(new_stat_fee)
                ok, info = store.save_json("service_prices.json", payload, "Update service price list")
                loaders.clear_caches()
                if ok:
                    extras = []
                    if blanks:
                        extras.append(f"skipped {blanks} blank row(s)")
                    st.success(info + (" · " + " · ".join(extras) if extras else ""))
                else:
                    st.warning(info)
    else:
        st.dataframe(df_prices, use_container_width=True, hide_index=True)
        auth.require("admin")
