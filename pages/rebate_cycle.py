import datetime as dt
from contextlib import contextmanager
import traceback

import pandas as pd
import streamlit as st

from core import accounting_handoff, loaders, opd_adapter, rebate_calc, rebate_report, ui


@contextmanager
def safe_stage(label: str):
    """Trap exceptions inside a step so a broken step doesn't break the rest of the wizard."""
    try:
        yield
    except Exception as e:
        st.error(f"**{label}** failed: {e}")
        with st.expander("Traceback"):
            st.code(traceback.format_exc())


ui.header("Rebate Cycle",
          "Select the month(s), upload OPD detail, get a multi-tab rebate report.",
          kicker="Rebates · Cycle")

master = loaders.rebate_master()
imap = loaders.item_map()
prices = loaders.service_prices()
cfg = loaders.config()
clinics_master = master.get("clinics", [])

# ── shared helpers ────────────────────────────────────────────────────────────

today = dt.date.today()
SS = st.session_state

def _months_relative(n: int) -> dt.date:
    y, m = today.year, today.month + n
    while m < 1:   m += 12; y -= 1
    while m > 12:  m -= 12; y += 1
    return dt.date(y, m, 1)

default_options = [_months_relative(i) for i in range(-23, 13)]
extras = SS.setdefault("cycle_extra_months", [])

# Default empty — user fills it explicitly
SS.setdefault("cycle_months", [])
SS.setdefault("cycle_step", 0)
SS.setdefault("cycle_uploaded_bytes", None)
SS.setdefault("cycle_uploaded_name", None)
SS.setdefault("cycle_results", None)

# ── wizard structure ──────────────────────────────────────────────────────────

STEPS = [
    ("setup",   "Cycle setup"),
    ("upload",  "Upload OPD detail"),
    ("review",  "Review the numbers"),
    ("export",  "Export & hand off"),
]
total = len(STEPS)
SS.cycle_step = max(0, min(SS.cycle_step, total - 1))
step_key, step_label = STEPS[SS.cycle_step]

# Header strip
st.markdown(f"**Step {SS.cycle_step + 1} of {total} — {step_label}**")
st.progress((SS.cycle_step + 1) / total)
st.caption(" · ".join(
    (f"**{lbl}**" if i == SS.cycle_step else f":gray[{lbl}]")
    for i, (_, lbl) in enumerate(STEPS)
))
st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Cycle setup (pick months)
# ══════════════════════════════════════════════════════════════════════════════
if step_key == "setup":
    with safe_stage("Cycle setup"):
        st.markdown("**Cycle period** — months included in this report (chronological).")

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
            current = list(SS.get("cycle_months", []))
            if new_month not in current:
                current.append(new_month)
                SS["cycle_months"] = sorted(current)
                st.rerun()

        # Selected months display
        st.multiselect(
            "Selected months (click ✕ to remove)",
            options=sorted(set(default_options + extras + list(SS.get("cycle_months", []))), reverse=True),
            format_func=lambda d: d.strftime("%B %Y"),
            key="cycle_months",
        )

        if not SS["cycle_months"]:
            st.info("Add at least one month above to continue.")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Upload OPD detail
# ══════════════════════════════════════════════════════════════════════════════
elif step_key == "upload":
    with safe_stage("Upload OPD detail"):
        months = sorted(SS["cycle_months"])
        st.caption(
            "Cycle: "
            + ", ".join(m.strftime("%B %Y") for m in months)
            + f"  ·  {len(months)} month(s)"
        )

        up = st.file_uploader(
            "OPD detail export covering the selected months (CSV / XLSX / XLS)",
            type=["csv", "xlsx", "xls"],
            key="cycle_uploader",
        )
        if up is not None:
            SS["cycle_uploaded_bytes"] = up.getvalue()
            SS["cycle_uploaded_name"]  = up.name
            # Invalidate any prior computed results — they were for a different file
            SS["cycle_results"] = None

        if SS.get("cycle_uploaded_name"):
            st.success(f"Uploaded: **{SS['cycle_uploaded_name']}** "
                       f"({len(SS['cycle_uploaded_bytes']):,} bytes)")
        else:
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

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Review the numbers
# ══════════════════════════════════════════════════════════════════════════════
elif step_key == "review":
    with safe_stage("Review the numbers"):
        months = sorted(SS["cycle_months"])
        month_labels = [m.strftime("%B") for m in months]

        # (Re)compute if we don't have cached results for this upload yet
        if SS.get("cycle_results") is None:
            import io as _io
            class _FakeUpload:
                def __init__(self, name, blob): self.name = name; self._blob = blob
                def read(self): return self._blob
                def getvalue(self): return self._blob
                def seek(self, *a, **k): pass
            blob = SS["cycle_uploaded_bytes"]
            name = SS["cycle_uploaded_name"]
            raw = opd_adapter.read_upload(_FakeUpload(name, blob))
            profile = opd_adapter.detect_profile(list(raw.columns))
            norm = opd_adapter.normalize(
                raw, None, imap,
                profile=profile,
                price_table=prices if profile == "case_grid" else None,
            )
            norm = norm.copy()
            norm["_date"] = pd.to_datetime(norm["date"], errors="coerce")

            # Seed per-bucket structure with every ACTIVE program clinic
            clinic_to_legal = {}
            for c in clinics_master:
                cname = (c.get("clinic_name") or "").strip()
                lname = (c.get("legal_name") or c.get("clinic_name") or "").strip()
                if cname: clinic_to_legal[cname] = lname

            per_bucket: dict = {}
            for c in clinics_master:
                if not c.get("active", True):
                    continue
                bucket = c.get("finance_company")
                legal = (c.get("legal_name") or c.get("clinic_name") or "").strip()
                if not bucket or not legal:
                    continue
                per_bucket.setdefault(bucket, {}).setdefault(legal, {lbl: 0.0 for lbl in month_labels})

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

            grand = sum(sum(d.values()) for clinics in per_bucket.values() for d in clinics.values())
            SS["cycle_results"] = {
                "per_bucket": per_bucket,
                "grand": grand,
                "profile": profile,
                "row_count": len(raw),
                "month_labels": month_labels,
            }

        results = SS["cycle_results"]
        per_bucket   = results["per_bucket"]
        grand        = results["grand"]
        profile      = results["profile"]
        month_labels = results["month_labels"]
        row_count    = results["row_count"]

        st.caption(f"Profile: **{profile}**  ·  {row_count:,} OPD rows processed")
        if profile == "case_grid":
            st.info("Case-grid profile: each case priced from the flat service price list "
                    "(STAT priority adds $125, no admin fees).")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Months", len(months))
        m2.metric("Buckets", len(per_bucket))
        m3.metric("Clinics in report", sum(len(b) for b in per_bucket.values()))
        m4.metric("Grand total", f"${grand:,.2f}")

        st.subheader("Per-bucket totals")
        for bucket in ["OnePlace Capital", "NewLane Financed", "Self-Financed"]:
            if bucket not in per_bucket: continue
            bsum = sum(sum(d.values()) for d in per_bucket[bucket].values())
            nz = sum(1 for d in per_bucket[bucket].values() if any(v > 0 for v in d.values()))
            st.write(f"- **{bucket}** — {len(per_bucket[bucket])} clinics ({nz} with activity), **${bsum:,.2f}**")

        with st.expander("Preview a bucket"):
            if per_bucket:
                choice = st.selectbox("Bucket", list(per_bucket.keys()), key="cycle_preview_bucket")
                rows = []
                for legal, by_month in sorted(per_bucket[choice].items(), key=lambda kv: kv[0].lower()):
                    r = {"Clinic/Hospital Name": legal}
                    for lbl in month_labels:
                        r[lbl] = round(by_month.get(lbl, 0.0), 2)
                    r["Amount"] = round(sum(by_month.values()), 2)
                    rows.append(r)
                st.dataframe(pd.DataFrame(rows), use_container_width=True, height=420)

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Export & hand off
# ══════════════════════════════════════════════════════════════════════════════
elif step_key == "export":
    with safe_stage("Export & hand off"):
        if not SS.get("cycle_results"):
            st.warning("Go back to Step 3 to compute the report first.")
        else:
            months = sorted(SS["cycle_months"])
            results = SS["cycle_results"]
            per_bucket = results["per_bucket"]
            grand      = results["grand"]

            xlsx_bytes = rebate_report.build(per_bucket, months)
            fname = f"Rebates_{rebate_report.short_period(months).replace(' ', '_').replace('&','and')}.xlsx"
            st.download_button(
                "Download multi-tab rebate report (xlsx)",
                xlsx_bytes,
                file_name=fname,
            )
            st.caption(f"Grand total: **${grand:,.2f}** across {len(per_bucket)} bucket(s).")

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

# ══════════════════════════════════════════════════════════════════════════════
# Wizard navigation
# ══════════════════════════════════════════════════════════════════════════════
can_back = SS.cycle_step > 0
can_next = SS.cycle_step < total - 1
next_blocked_reason = ""

if step_key == "setup" and not SS["cycle_months"]:
    can_next = False
    next_blocked_reason = "Add at least one month to continue."
elif step_key == "upload" and not SS.get("cycle_uploaded_bytes"):
    can_next = False
    next_blocked_reason = "Upload an OPD detail file to continue."

st.divider()
nav_b, nav_msg, nav_n = st.columns([1, 4, 1])
if can_back:
    if nav_b.button("◀ Back", key="cycle_back", use_container_width=True):
        SS.cycle_step -= 1
        st.rerun()
if next_blocked_reason:
    nav_msg.caption(next_blocked_reason)
if can_next and nav_n.button("Next ▶", key="cycle_next", type="primary",
                              disabled=not can_next, use_container_width=True):
    SS.cycle_step += 1
    st.rerun()
