import datetime as dt
from contextlib import contextmanager

import pandas as pd
import streamlit as st

from core import accounting_handoff, audit, auth, errors, loaders, opd_adapter, rebate_calc, rebate_report, store, ui


@contextmanager
def safe_stage(label: str):
    """Trap exceptions inside a step so a broken step doesn't break the rest of the wizard."""
    try:
        yield
    except Exception as e:
        err = errors.capture(e)
        st.error(f"**{label}** failed: `{err['summary']}`")
        errors.render_details(err)


def _apply_fuzzy_decisions(per_bucket: dict, fuzzy_matches: list, decisions: dict) -> dict:
    """Return a deep copy of per_bucket with REJECTED fuzzy amounts subtracted.

    Confirmed and pending matches are left in (current behavior). Rejected ones
    have the contributing OPD-month-amount triplet pulled out of their destination
    clinic's monthly column. Anything else is untouched.

    Decisions dict: key = "{opd_name}::{matched_master}", value ∈ {"confirm","reject"}.
    """
    import copy
    if not decisions:
        return per_bucket
    eff = copy.deepcopy(per_bucket)
    for fm in fuzzy_matches:
        key = f"{fm['opd_name']}::{fm['matched_master']}"
        if decisions.get(key) != "reject":
            continue
        fc = fm["finance_company"]
        legal = fm["matched_legal"]
        mlabel = fm["month_label"]
        if fc in eff and legal in eff[fc] and mlabel in eff[fc][legal]:
            eff[fc][legal][mlabel] = max(0.0, eff[fc][legal][mlabel] - fm["amount"])
    return eff


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

# Default empty — user fills it explicitly. We deliberately use 'selected_months'
# (not 'cycle_months') because Streamlit clears widget-keyed state when the widget
# isn't on the current page; the multiselect's key 'cycle_months_widget' only
# renders on Step 1, so we mirror its value into 'selected_months' for cross-step
# persistence.
SS.setdefault("selected_months", [])
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

# Scroll the page to the top whenever the wizard step changes so the user
# starts each new step with the instructions at top, not wherever they
# happened to leave their scroll on the previous step.
ui.scroll_top_on_step_change("rebate_cycle", SS.cycle_step)

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
        yc, mc, bc = st.columns([1, 2, 1], vertical_alignment="bottom")
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
            add_clicked = st.button("Add month", key="cycle_add_btn", use_container_width=True)

        if add_clicked:
            new_month = dt.date(int(pick_year), int(pick_month), 1)
            if new_month not in extras and new_month not in default_options:
                extras.append(new_month)
            current = list(SS.get("selected_months", []))
            if new_month not in current:
                current.append(new_month)
                SS["selected_months"] = sorted(current)
                # ALSO update the widget's own state; Streamlit ignores `default`
                # once a widget has stored state, so we have to set it explicitly.
                SS["cycle_months_widget"] = sorted(current)
                st.rerun()

        # On first render after a step nav (widget had no state), seed from persistent.
        if "cycle_months_widget" not in SS:
            SS["cycle_months_widget"] = list(SS.get("selected_months", []))

        widget_value = st.multiselect(
            "Selected months (click ✕ to remove)",
            options=sorted(set(default_options + extras + list(SS.get("selected_months", []))), reverse=True),
            format_func=lambda d: d.strftime("%B %Y"),
            key="cycle_months_widget",
        )
        # Mirror widget state into the persistent key (handles ✕ removals)
        SS["selected_months"] = widget_value

        # If the user changed the month selection, the cached results + review ack
        # belong to a different cycle. Invalidate both so Step 3 recomputes and the
        # review gate fires again. Without this, a Step 3 ack from a prior month set
        # could survive a subsequent reduce-to-different-months and let the user
        # skip the review on a new computation.
        prev_months = SS.get("_prev_selected_months")
        cur_months = tuple(SS["selected_months"])
        if prev_months is not None and prev_months != cur_months:
            SS["cycle_results"] = None
            SS["cycle_review_acked"] = False
        SS["_prev_selected_months"] = cur_months

        if not SS["selected_months"]:
            st.info("Add at least one month above to continue.")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Upload OPD detail
# ══════════════════════════════════════════════════════════════════════════════
elif step_key == "upload":
    with safe_stage("Upload OPD detail"):
        months = sorted(SS["selected_months"])
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
            SS["cycle_review_acked"] = False

        if SS.get("cycle_uploaded_name"):
            st.success(f"Uploaded: **{SS['cycle_uploaded_name']}** "
                       f"({len(SS['cycle_uploaded_bytes']):,} bytes)")
        else:
            st.info(
                "**How to pull the OPD export:**\n\n"
                "1. [OPD](https://telehealth.oncurapartners.com) → **Consults** → **Completed**\n"
                "2. **Department** dropdown — select **Cardiology**, **General Radiology**, "
                "**Internal Medicine**, and **Ultrasound**\n"
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
        months = sorted(SS["selected_months"])
        month_labels = [m.strftime("%B") for m in months]

        # Invalidate stale cached results from before the fuzzy-match UI redesign.
        # Old shape: fuzzy_matches was list[tuple]; new shape: list[dict].
        # Detect once and force a recompute so the page doesn't 500 on dict-key access.
        _cached = SS.get("cycle_results")
        if _cached is not None:
            _fm = _cached.get("fuzzy_matches") or []
            if _fm and not isinstance(_fm[0], dict):
                SS["cycle_results"] = None
                SS.pop("rebate_fuzzy_decisions", None)
                st.info(
                    ":material/refresh: Cached results were in the old format and have been "
                    "cleared. The numbers will recompute below."
                )

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
            fuzzy_matches = []   # list of dicts (see schema below)
            variance_rows = []   # (month_label, clinic_name, rate_based, feed_based, variance)
            rads_pending = []    # (month_label, clinic_name) for self-funded clinics w/ unconfirmed rate
            cycle_has_feed = False  # True if ANY selected month carries OData feed columns
            for m, label in zip(months, month_labels):
                start = pd.Timestamp(m)
                next_y, next_m = (m.year + 1, 1) if m.month == 12 else (m.year, m.month + 1)
                end = pd.Timestamp(dt.date(next_y, next_m, 1))
                sub = norm[(norm["_date"] >= start) & (norm["_date"] < end)]
                if sub.empty:
                    continue
                res = rebate_calc.calculate(sub, master, cfg)
                pc = res["per_clinic"]
                if res.get("has_feed"):
                    cycle_has_feed = True
                if not res["unmatched"].empty:
                    unmatched_total.update(res["unmatched"]["opd_clinic"].astype(str).tolist())
                for _, r in pc.iterrows():
                    bucket = r["finance_company"]
                    clinic_name = (r.get("clinic_name") or "").strip()
                    legal = clinic_to_legal.get(clinic_name, r.get("legal_name") or clinic_name)
                    amt = float(r.get("total_rebate", 0))
                    per_bucket.setdefault(bucket, {}).setdefault(legal, {lbl: 0.0 for lbl in month_labels})
                    # `+=` not `=`: if two normalized clinic-name variants in OPD both
                    # match the same master record, their amounts must sum, not overwrite.
                    per_bucket[bucket][legal][label] = per_bucket[bucket][legal].get(label, 0.0) + amt
                    # Flag rows that need human review before sign-off.
                    # Capture full context so a "reject" decision can subtract this exact
                    # contribution from per_bucket downstream.
                    if r.get("match") == "fuzzy":
                        fuzzy_matches.append({
                            "month_label":     label,
                            "opd_name":        str(r.get("opd_clinic", "")),
                            "matched_master":  clinic_name,
                            "matched_legal":   legal,
                            "finance_company": bucket,
                            "amount":          round(amt, 2),
                        })
                    # Variance check only has signal when the OPD export carried feed
                    # columns. For case_grid exports (the common one) feed_total is 0
                    # for everyone, so variance degenerates into rate_total and flags
                    # every clinic with any rebate — pure noise. Skip when no feed.
                    if res.get("has_feed"):
                        var = float(r.get("variance", 0.0) or 0.0)
                        if abs(var) >= 1.00:  # ignore sub-dollar floating-point noise
                            variance_rows.append((label, clinic_name,
                                                  float(r.get("rebate_rate_based", 0.0)),
                                                  float(r.get("rebate_feed_based", 0.0)),
                                                  round(var, 2)))
                    if r.get("rads_pending_confirmation"):
                        rads_pending.append((label, clinic_name))

            grand = sum(sum(d.values()) for clinics in per_bucket.values() for d in clinics.values())
            SS["cycle_results"] = {
                "per_bucket": per_bucket,
                "grand": grand,
                "profile": profile,
                "row_count": len(raw),
                "month_labels": month_labels,
                "unmatched_total": sorted(unmatched_total),
                "fuzzy_matches": fuzzy_matches,
                "variance_rows": variance_rows,
                "rads_pending": rads_pending,
                "has_feed": cycle_has_feed,
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

        # ── Pre-export review surfaces: things the calc flagged that the operator
        #    needs to look at before signing off. Previously these were computed
        #    and discarded silently. A checkbox at the bottom gates the Next button.
        fuzzy_matches = results.get("fuzzy_matches", [])
        variance_rows = results.get("variance_rows", [])
        rads_pending = results.get("rads_pending", [])
        unmatched_total = results.get("unmatched_total", [])

        # Variance only meaningful when the cycle's OPD export carries the OData
        # feed columns (RebateUltrasoundFinance/Cash, RebateRadFinance/Cash).
        # case_grid exports — the default OPD → Consults → Completed → Export
        # path — don't carry those columns, so we suppress the variance block
        # entirely on those cycles. (Showing variance for a feed-less cycle
        # flagged every clinic with any rebate, which was pure noise.)
        if variance_rows and results.get("has_feed"):
            with st.expander(
                f":material/info: **Rate vs feed variance** — {len(variance_rows)} row(s) over $1.00 difference",
                expanded=False,
            ):
                st.caption(
                    "Informational, not a defect. The feed's `RadCash` / `UltraCash` columns "
                    "reflect the rebate rate that was in effect at the moment each case was "
                    "finalized — if rates have changed in the master since then, the recomputed "
                    "rate-based total won't match the historical feed total. The rate-based "
                    "column is what's used in the export."
                )
                vdf = pd.DataFrame(
                    variance_rows,
                    columns=["Month", "Clinic", "Rate-based ($)", "Feed-based ($)", "Variance ($)"],
                )
                st.dataframe(vdf, use_container_width=True, hide_index=True)

        if fuzzy_matches:
            # Aggregate by (opd, matched_master). Each pair appears once even if it
            # showed up across multiple months — the months are listed in the row.
            fuzzy_agg: dict[tuple[str, str], dict] = {}
            for fm in fuzzy_matches:
                key = (fm["opd_name"], fm["matched_master"])
                rec = fuzzy_agg.setdefault(key, {
                    "months": [], "total": 0.0,
                    "matched_legal": fm["matched_legal"],
                    "finance_company": fm["finance_company"],
                })
                if fm["month_label"] not in rec["months"]:
                    rec["months"].append(fm["month_label"])
                rec["total"] += float(fm["amount"])

            # Hydrate session-state decisions from the persistent
            # data/fuzzy_decisions.json on every render — that's the source
            # of truth, session_state is just the in-flight working copy.
            # Without this, a confirmed/rejected decision evaporated on the
            # next page refresh, restart, or new Streamlit Cloud container.
            decisions = SS.setdefault("rebate_fuzzy_decisions", {})
            _persisted = (loaders.fuzzy_decisions().get("decisions") or {})
            for _key, _rec in _persisted.items():
                if _key not in decisions:
                    decisions[_key] = _rec.get("decision") if isinstance(_rec, dict) else _rec
            n_confirmed = sum(
                1 for k in fuzzy_agg
                if decisions.get(f"{k[0]}::{k[1]}") == "confirm"
            )
            n_rejected = sum(
                1 for k in fuzzy_agg
                if decisions.get(f"{k[0]}::{k[1]}") == "reject"
            )
            n_pending = len(fuzzy_agg) - n_confirmed - n_rejected

            with st.expander(
                f":material/info: **Fuzzy clinic matches** — "
                f"{len(fuzzy_agg)} clinic(s) · "
                f"{n_confirmed} confirmed · {n_rejected} rejected · {n_pending} pending",
                expanded=False,
            ):
                st.caption(
                    "These OPD clinic names matched the master roster via fuzzy match — exact "
                    "match failed, but after stripping common boilerplate (animal / veterinary / "
                    "LLC / DBA) the distinguishing tokens lined up at ≥ 92% similarity AND their "
                    "first words also matched. Click **Match** to confirm; click **Not a match** "
                    "to subtract the amount from the rebate export. Pending matches stay included "
                    "by default."
                )

                # Header row
                h = st.columns([3, 3, 1.6, 1.4, 1.2, 1.5])
                h[0].caption("**OPD name**")
                h[1].caption("**Matched master**")
                h[2].caption("**Months**")
                h[3].caption("**Total ($)**")
                h[4].caption("**Decision**")
                h[5].caption("**Action**")

                for (opd, matched), rec in sorted(
                    fuzzy_agg.items(), key=lambda kv: -kv[1]["total"]
                ):
                    key = f"{opd}::{matched}"
                    decision = decisions.get(key, "pending")
                    row = st.columns([3, 3, 1.6, 1.4, 1.2, 1.5])
                    row[0].markdown(f"`{opd}`")
                    row[1].write(matched)
                    row[2].caption(", ".join(rec["months"]))
                    row[3].write(f"${rec['total']:,.2f}")
                    if decision == "confirm":
                        row[4].markdown(":green[**✓ Match**]")
                    elif decision == "reject":
                        row[4].markdown(":red[**✗ Excluded**]")
                    else:
                        row[4].markdown(":gray[Pending]")
                    btns = row[5].columns(2)
                    safe_key = abs(hash(key))

                    def _persist_decision(verdict: str, _key=key,
                                          _opd=opd, _matched=matched):
                        """Update both session_state AND the persistent
                        data/fuzzy_decisions.json so the choice survives
                        a refresh / restart / new Cloud container."""
                        decisions[_key] = verdict
                        cur = dict(loaders.fuzzy_decisions() or {})
                        cur_decisions = dict(cur.get("decisions") or {})
                        cur_decisions[_key] = {
                            "decision": verdict,
                            "decided_at": dt.datetime.now().isoformat(timespec="seconds"),
                            "opd_name": _opd,
                            "matched_master": _matched,
                        }
                        cur["decisions"] = cur_decisions
                        ok, info = store.save_json(
                            "fuzzy_decisions.json", cur,
                            f"Fuzzy match {verdict}: {_opd[:40]} -> {_matched[:40]}",
                        )
                        loaders.fuzzy_decisions.clear()
                        if not ok:
                            st.warning(
                                f":material/warning: Decision saved locally only — {info}. "
                                "It will be lost on the next Cloud restart. Set "
                                "`GITHUB_TOKEN` in secrets for persistent decisions.",
                                icon=":material/warning:",
                            )

                    if btns[0].button(
                        "✓", key=f"fm_match_{safe_key}",
                        type="primary" if decision == "confirm" else "secondary",
                        help="Confirm this is the right clinic — include in export. Persists across cycles.",
                        use_container_width=True,
                    ):
                        _persist_decision("confirm")
                        st.rerun()
                    if btns[1].button(
                        "✗", key=f"fm_reject_{safe_key}",
                        type="primary" if decision == "reject" else "secondary",
                        help="Not the right clinic — exclude from export. Persists across cycles.",
                        use_container_width=True,
                    ):
                        _persist_decision("reject")
                        st.rerun()

        if rads_pending:
            with st.expander(
                f":material/info: **Self-funded rads rate not confirmed** — {len(rads_pending)} clinic-month(s)",
                expanded=False,
            ):
                st.caption(
                    "These self-funded clinics had their rads rebate computed at the 2% default, "
                    "but the `rads_rate_confirmed` flag is false. If you want a different rate, "
                    "set it per clinic in Rebate Program Controls and tick the confirmed flag."
                )
                pdf = pd.DataFrame(rads_pending, columns=["Month", "Clinic"]).drop_duplicates()
                st.dataframe(pdf, use_container_width=True, hide_index=True)

        # Note: "Unmatched OPD clinics" dropdown intentionally removed — OPD always lists
        # several hundred clinics that aren't in the rebate program roster (rebate enrolls
        # a subset of all OPD clinics), so the count is normal noise, not a flag.

        # Sign-off gate: operator must tick the checkbox before Next enables.
        # Wrapped in a bordered "sign-off card" so the checkbox visibly groups
        # with its instruction text. Earlier versions had the checkbox above
        # and a separate warning banner below — the connection was easy to
        # miss, so operators were skipping right past the sign-off.
        if variance_rows or fuzzy_matches or rads_pending:
            st.divider()
            # Read live state from the widget's own SS key so the heading
            # reflects the click that just happened (not the prior rerun).
            live_acked = SS.get(
                "cycle_review_ack_widget",
                SS.get("cycle_review_acked", False),
            )
            with st.container(border=True):
                if live_acked:
                    st.markdown(
                        "##### :green[:material/check_circle:&nbsp; Sign-off complete]"
                    )
                    st.caption(
                        "**Next ▶** at the bottom of the page is now enabled."
                    )
                else:
                    st.markdown(
                        "##### :red[:material/priority_high:&nbsp; SIGN-OFF REQUIRED]"
                    )
                    st.caption(
                        "Tick the checkbox below to acknowledge you've reviewed the "
                        "flagged rows above. **Next ▶** at the bottom of the page "
                        "stays disabled until you do."
                    )
                acked_now = st.checkbox(
                    "**I've reviewed the flagged rows above and they're acceptable.**",
                    value=SS.get("cycle_review_acked", False),
                    key="cycle_review_ack_widget",
                )
                SS["cycle_review_acked"] = acked_now
        else:
            SS["cycle_review_acked"] = True  # nothing to flag, auto-pass

        # Apply fuzzy decisions so the preview reflects what will actually export.
        _decisions = SS.get("rebate_fuzzy_decisions", {})
        effective_per_bucket = _apply_fuzzy_decisions(per_bucket, fuzzy_matches, _decisions)
        n_rej = sum(1 for fm in fuzzy_matches
                    if _decisions.get(f"{fm['opd_name']}::{fm['matched_master']}") == "reject")
        if n_rej:
            _rej_total = sum(fm["amount"] for fm in fuzzy_matches
                             if _decisions.get(f"{fm['opd_name']}::{fm['matched_master']}") == "reject")
            st.caption(
                f":material/remove_circle: Preview reflects **{n_rej} rejected fuzzy match(es)** — "
                f"${_rej_total:,.2f} excluded from the totals below."
            )

        with st.expander("Preview a bucket"):
            if effective_per_bucket:
                choice = st.selectbox("Bucket", list(effective_per_bucket.keys()),
                                      key="cycle_preview_bucket")
                rows = []
                for legal, by_month in sorted(effective_per_bucket[choice].items(), key=lambda kv: kv[0].lower()):
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
            months = sorted(SS["selected_months"])
            results = SS["cycle_results"]
            raw_per_bucket = results["per_bucket"]
            fuzzy_matches  = results.get("fuzzy_matches", [])
            _decisions     = SS.get("rebate_fuzzy_decisions", {})
            # Apply fuzzy match decisions: rejected matches have their amount
            # subtracted from the destination clinic's monthly column before export.
            per_bucket = _apply_fuzzy_decisions(raw_per_bucket, fuzzy_matches, _decisions)
            grand = sum(sum(d.values()) for clinics in per_bucket.values()
                                            for d in clinics.values())

            xlsx_bytes = rebate_report.build(per_bucket, months)
            fname = f"Rebates_{rebate_report.short_period(months).replace(' ', '_').replace('&','and')}.xlsx"
            n_rej = sum(1 for fm in fuzzy_matches
                        if _decisions.get(f"{fm['opd_name']}::{fm['matched_master']}") == "reject")
            cap = f"Grand total: **${grand:,.2f}** across {len(per_bucket)} bucket(s)."
            if n_rej:
                cap += f"  ·  {n_rej} rejected fuzzy match(es) excluded."
            st.caption(cap)

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

            # The xlsx already rides along as the .eml attachment, so the standalone
            # download is just a backup for anyone who needs the file outside the email
            # path. Tucked into a muted expander so it doesn't compete visually with
            # the primary Download email draft button.
            with st.expander(":gray[Backup: download the xlsx directly]"):
                st.caption(
                    "The xlsx is already attached to the email draft above — only use this "
                    "if you need a standalone copy (e.g., to save to OneDrive or send manually)."
                )
                st.download_button(
                    "Download multi-tab rebate report (xlsx)",
                    xlsx_bytes,
                    file_name=fname,
                    key="rebate_xlsx_backup_dl",
                )

            # ── Record to audit manifest ────────────────────────────────────────────
            st.divider()
            n_clinics = sum(len(c) for c in per_bucket.values())
            # Two flag sources for "already recorded":
            #   1. session-state set of hashes we recorded earlier in THIS session
            #      (covers the immediate post-click render so the red banner
            #      flips to green without needing GitHub round-trip)
            #   2. the audit manifest itself (catches reruns across sessions)
            output_hash = audit.output_hash_bytes(xlsx_bytes)
            recorded_hashes = SS.setdefault("rebate_recorded_hashes", set())
            already_recorded = (
                output_hash in recorded_hashes
                or any(
                    e.get("outputs") and e["outputs"][0].get("sha256") == output_hash
                    for e in audit.list_entries(cycle_type="rebate_report", limit=50)
                )
            )
            if already_recorded:
                st.success(
                    ":material/check_circle: This rebate report is recorded in the audit manifest. "
                    "Re-recording would create a duplicate entry — skip the button below unless "
                    "something changed."
                )
            else:
                st.error(
                    ":material/priority_high: **IMPORTANT — Confirm this report has been handed off to accounting.**  "
                    "Click below **only after** you've sent the email and shared the xlsx. This appends an immutable "
                    "entry to the audit manifest capturing the source-file hash, the output hash, the months, the "
                    "totals, and who ran it — so any future question about this cycle has a paper trail.",
                    icon=":material/warning:",
                )
            initials = ui.initials_input(
                "rebate_audit_initials",
                disabled=already_recorded,
            )
            if ui.record_button(
                "Record rebate cycle to audit manifest",
                key="rebate_audit_mark",
                disabled=already_recorded or not initials,
            ):
                src_bytes = SS.get("cycle_uploaded_bytes") or b""
                approver_val = initials or auth.current_role()
                # Multi-month cycle — anchor year/month to the LATEST selected month
                # so the audit table's year/month columns aren't empty. Full month
                # list lives in params["months"] for the complete picture.
                latest = months[-1]
                ok, entry_id, info = audit.record_cycle(
                    cycle_type="rebate_report",
                    approver=approver_val,
                    year=latest.year, month=latest.month,
                    params={
                        "months": [m.strftime("%Y-%m") for m in months],
                        "period_label": _period_label,
                        "buckets": list(per_bucket.keys()),
                        "n_clinics": n_clinics,
                        "per_bucket_totals": _per_bucket_totals,
                        "grand_total": round(float(grand), 2),
                        "fuzzy_match_decisions": {
                            "confirmed": sum(1 for v in _decisions.values() if v == "confirm"),
                            "rejected":  sum(1 for v in _decisions.values() if v == "reject"),
                        },
                    },
                    source_file={
                        "name": SS.get("cycle_uploaded_name", "unknown"),
                        "sha256": audit.output_hash_bytes(src_bytes),
                        "size_bytes": len(src_bytes),
                    },
                    outputs=[{
                        "name": fname,
                        "sha256": audit.output_hash_bytes(xlsx_bytes),
                        "row_count": n_clinics,
                        "total": round(float(grand), 2),
                    }],
                    note=f"Rebate cycle for {_period_label}",
                )
                # Mark THIS hash as recorded in the session so the red banner
                # flips to green on the NEXT natural rerun (any user interaction).
                # We intentionally do NOT call st.rerun() here — a forced rerun
                # would wipe the success/warning banner below before the user
                # ever sees it (the bug that prompted this fix). Stage 1/2/3
                # record buttons in pages/flex_cycle.py follow the same pattern.
                recorded_hashes.add(output_hash)
                if ok:
                    st.success(
                        f":material/check_circle: **Recorded to audit manifest.** "
                        f"Entry ID: `{entry_id[:8]}…`  ·  {info}",
                        icon=":material/check_circle:",
                    )
                else:
                    st.warning(
                        f"Recorded locally (no GitHub commit). Entry ID: `{entry_id[:8]}…`  ·  {info}  \n"
                        "Set `GITHUB_TOKEN` in secrets for persistent audit history on Cloud.",
                        icon=":material/warning:",
                    )

# ══════════════════════════════════════════════════════════════════════════════
# Wizard navigation
# ══════════════════════════════════════════════════════════════════════════════
can_back = SS.cycle_step > 0
can_next = SS.cycle_step < total - 1
next_blocked_reason = ""

if step_key == "setup" and not SS["selected_months"]:
    can_next = False
    next_blocked_reason = "Add at least one month to continue."
elif step_key == "upload" and not SS.get("cycle_uploaded_bytes"):
    can_next = False
    next_blocked_reason = "Upload an OPD detail file to continue."
elif step_key == "review" and not SS.get("cycle_review_acked", False):
    can_next = False
    next_blocked_reason = "Tick the review acknowledgement checkbox above to continue."

st.divider()
nav_b, nav_msg, nav_n = st.columns([1, 4, 1])
if can_back:
    if nav_b.button("◀ Back", key="cycle_back", use_container_width=True):
        SS.cycle_step -= 1
        st.rerun()
if next_blocked_reason:
    # Use a visible warning (not a tiny caption) so the operator can see why
    # Next is blocked. Previously this was easy to miss and the disabled Next
    # button wasn't rendered at all — looked like the button had vanished.
    nav_msg.warning(f":material/info: {next_blocked_reason}")
# Always render the Next button so the operator can SEE it (disabled when
# can_next is False). Previously the button only rendered when enabled, which
# looked like the wizard had silently broken.
if SS.cycle_step < total - 1:
    if nav_n.button(
        "Next ▶", key="cycle_next", type="primary",
        disabled=not can_next, use_container_width=True,
    ):
        SS.cycle_step += 1
        st.rerun()
