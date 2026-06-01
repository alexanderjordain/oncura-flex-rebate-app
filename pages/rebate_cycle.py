import datetime as dt

import pandas as pd
import streamlit as st

from core import accounting_handoff, loaders, opd_adapter, rebate_calc, rebate_report, ui

ui.header("Rebate Cycle",
          "Select the month(s), upload OPD detail, get a multi-tab rebate report.",
          kicker="Rebates · Cycle")

master = loaders.rebate_master()
imap = loaders.item_map()
prices = loaders.service_prices()
cfg = loaders.config()
clinics_master = master.get("clinics", [])

# ── month picker ──────────────────────────────────────────────────────────────
today = dt.date.today()

def _months_relative(n: int) -> dt.date:
    """First-of-month, n months from today (negative = past, positive = future)."""
    y, m = today.year, today.month + n
    while m < 1:
        m += 12; y -= 1
    while m > 12:
        m -= 12; y += 1
    return dt.date(y, m, 1)

# Default range: 24 months back through 12 months forward
default_options = [_months_relative(i) for i in range(-23, 13)]
extras = st.session_state.setdefault("cycle_extra_months", [])
all_options = sorted(set(default_options + extras), reverse=True)  # newest first

prev_month = _months_relative(-1)
if "cycle_months" not in st.session_state:
    st.session_state["cycle_months"] = [prev_month]

st.markdown("**Cycle period** — months included in this report (chronological).")

# Month-only picker: Year + Month dropdowns side-by-side, no day grid
prev = _months_relative(-1)
year_options  = list(range(today.year - 5, today.year + 3))
month_options = list(range(1, 13))
yc, mc, bc = st.columns([1, 2, 1])
with yc:
    pick_year = st.selectbox(
        "Year", year_options,
        index=year_options.index(prev.year), key="cycle_add_year",
    )
with mc:
    pick_month = st.selectbox(
        "Month", month_options,
        index=month_options.index(prev.month),
        format_func=lambda m: dt.date(2000, m, 1).strftime("%B"),
        key="cycle_add_month",
    )
with bc:
    st.markdown("&nbsp;", unsafe_allow_html=True)
    add_clicked = st.button("Add month", key="cycle_add_btn", use_container_width=True)

if add_clicked:
    new_month = dt.date(int(pick_year), int(pick_month), 1)
    if new_month not in extras and new_month not in default_options:
        extras.append(new_month)
    current = list(st.session_state.get("cycle_months", []))
    if new_month not in current:
        current.append(new_month)
        st.session_state["cycle_months"] = sorted(current)
        st.rerun()

# Multiselect retains pill display + click-X-to-remove behavior
selected = st.multiselect(
    "Selected months (click ✕ to remove)",
    options=sorted(set(default_options + extras + list(st.session_state.get("cycle_months", []))), reverse=True),
    format_func=lambda d: d.strftime("%B %Y"),
    key="cycle_months",
)

if not selected:
    st.info("Select at least one month.")
    st.stop()
months = sorted(selected)
month_labels = [m.strftime("%B") for m in months]

# ── upload ────────────────────────────────────────────────────────────────────
up = st.file_uploader("OPD detail export covering the selected months (CSV/XLSX)",
                      type=["csv", "xlsx", "xls"])
if up is None:
    st.info(
        "**How to pull the OPD export:**\n\n"
        "1. OPD → **Consults** → **Completed**\n"
        "2. **Department** dropdown — select **Cardiology**, **General Radiology**, "
        "**Internal Medicine**, and **Ultrasound** (titles capitalized exactly as shown)\n"
        "3. Set **Finalized From** and **Finalized To** dates to cover the selected months above\n"
        "4. Click **Search**\n"
        "5. Click **Export to Excel**\n"
        "6. Upload the file here\n\n"
        "_Accepts the consult-grid export or an OData ConsultService export._"
    )
    st.stop()

raw = opd_adapter.read_upload(up)
profile = opd_adapter.detect_profile(list(raw.columns))
st.caption(f"Profile: **{profile}**  ·  {len(raw):,} rows uploaded")
if profile == "case_grid":
    st.info("Case-grid profile: each case priced from the flat service price list "
            "(STAT priority adds $125, no admin fees).")

norm = opd_adapter.normalize(
    raw, None, imap,
    profile=profile,
    price_table=prices if profile == "case_grid" else None,
)
norm = norm.copy()
norm["_date"] = pd.to_datetime(norm["date"], errors="coerce")

# ── seed per-bucket structure with every ACTIVE program clinic ────────────────
# Mirrors the existing workbook layout where every program clinic appears each cycle,
# with $0.00 if there was no activity in the selected months.
clinic_to_legal = {}
for c in clinics_master:
    cname = (c.get("clinic_name") or "").strip()
    lname = (c.get("legal_name") or c.get("clinic_name") or "").strip()
    if cname:
        clinic_to_legal[cname] = lname

per_bucket: dict = {}
for c in clinics_master:
    if not c.get("active", True):
        continue
    bucket = c.get("finance_company")
    legal = (c.get("legal_name") or c.get("clinic_name") or "").strip()
    if not bucket or not legal:
        continue
    per_bucket.setdefault(bucket, {}).setdefault(legal, {lbl: 0.0 for lbl in month_labels})

# ── per-month calc ────────────────────────────────────────────────────────────
unmatched_total = set()
for m, label in zip(months, month_labels):
    start = pd.Timestamp(m)
    next_y, next_m = (m.year + 1, 1) if m.month == 12 else (m.year, m.month + 1)
    end = pd.Timestamp(dt.date(next_y, next_m, 1))
    sub = norm[(norm["_date"] >= start) & (norm["_date"] < end)]
    if sub.empty:
        continue
    res = rebate_calc.calculate(sub, master, cfg)
    pc = res["per_clinic"]
    if not res["unmatched"].empty:
        unmatched_total.update(res["unmatched"]["opd_clinic"].astype(str).tolist())
    for _, r in pc.iterrows():
        bucket = r["finance_company"]
        clinic_name = (r.get("clinic_name") or "").strip()
        legal = clinic_to_legal.get(clinic_name, r.get("legal_name") or clinic_name)
        amt = float(r.get("total_rebate", 0))
        per_bucket.setdefault(bucket, {}).setdefault(legal, {lbl: 0.0 for lbl in month_labels})
        per_bucket[bucket][legal][label] = amt

# ── summary ───────────────────────────────────────────────────────────────────
grand = sum(sum(d.values()) for clinics in per_bucket.values() for d in clinics.values())
m1, m2, m3, m4 = st.columns(4)
m1.metric("Months", len(months))
m2.metric("Buckets", len(per_bucket))
m3.metric("Clinics in report", sum(len(b) for b in per_bucket.values()))
m4.metric("Grand total", f"${grand:,.2f}")

st.subheader("Per-bucket totals")
for bucket in ["OnePlace Capital", "NewLane Financed", "Self-Financed"]:
    if bucket not in per_bucket:
        continue
    bsum = sum(sum(d.values()) for d in per_bucket[bucket].values())
    nz = sum(1 for d in per_bucket[bucket].values() if any(v > 0 for v in d.values()))
    st.write(f"- **{bucket}** — {len(per_bucket[bucket])} clinics ({nz} with activity), **${bsum:,.2f}**")

with st.expander("Preview a bucket"):
    if per_bucket:
        choice = st.selectbox("Bucket", list(per_bucket.keys()))
        rows = []
        for legal, by_month in sorted(per_bucket[choice].items(), key=lambda kv: kv[0].lower()):
            r = {"Clinic/Hospital Name": legal}
            for lbl in month_labels:
                r[lbl] = round(by_month.get(lbl, 0.0), 2)
            r["Amount"] = round(sum(by_month.values()), 2)
            rows.append(r)
        st.dataframe(pd.DataFrame(rows), use_container_width=True, height=420)

# ── export ────────────────────────────────────────────────────────────────────
xlsx_bytes = rebate_report.build(per_bucket, months)
fname = f"Rebates_{rebate_report.short_period(months).replace(' ', '_').replace('&','and')}.xlsx"
st.download_button(
    "Download multi-tab rebate report (xlsx)",
    xlsx_bytes,
    file_name=fname,
    type="primary",
)

# Accounting handoff
_period_label = rebate_report.long_period(months)
_per_bucket_totals = {
    bucket: round(sum(sum(d.values()) for d in clinics.values()), 2)
    for bucket, clinics in per_bucket.items()
}
_subj, _body = accounting_handoff.rebate_email(
    period_label=_period_label,
    per_bucket_totals=_per_bucket_totals,
    grand_total=round(grand, 2),
)
accounting_handoff.render_handoff(_subj, _body, key_prefix="rebate_email",
                                  attachments=[(fname, xlsx_bytes)])
