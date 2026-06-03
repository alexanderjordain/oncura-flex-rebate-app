"""Settings — edit operational config without touching code.

UI front-end for `data/config.json`: rebate rates, FLEX overage routing, finance partner
metadata, escalation clinics. Also: full backup (download everything as a zip) + restore.

Admin-role gated. Persists via core.store (GitHub Contents API when token set, local file
otherwise).
"""
from __future__ import annotations

import datetime as dt
import io
import json
import zipfile
from pathlib import Path

import streamlit as st

from core import audit, auth, ledger, loaders, store, ui

ui.header(
    "Settings",
    "Operational configuration, backup, and module health. Admin-only.",
    kicker="App · Settings",
)

# ── Admin gate ────────────────────────────────────────────────────────────────
if not auth.can("admin"):
    st.warning("Settings are admin-only. Your current role is read-only here.")
    auth.require("admin")
    st.stop()

# ── Second password gate ──────────────────────────────────────────────────────
# Settings can rewrite rates, restore from backup, etc. — irreversible changes.
# Even though the user is already authenticated as admin, re-prompt for the same
# APP_PASSWORD before exposing the controls. Unlocked for the rest of the session;
# a "Lock Settings" button at the bottom restores the gate without affecting the
# main session login.
SETTINGS_UNLOCK_KEY = "settings_unlocked"
_app_pw = auth._secret(["APP_PASSWORD"])

if _app_pw and not st.session_state.get(SETTINGS_UNLOCK_KEY):
    st.markdown("### Confirm password to unlock Settings")
    st.caption(
        "Settings allow editing rates, restoring from backup, and other irreversible "
        "changes. Re-enter the app password — same one you used to log in."
    )
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        pw = st.text_input("Password", type="password", key="settings_unlock_pw")
        if st.button("Unlock Settings", use_container_width=True):
            if pw == _app_pw:
                st.session_state[SETTINGS_UNLOCK_KEY] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.stop()

cfg = dict(loaders.config())  # deep-ish copy via dict(); we'll re-nest when saving


# ═════════════════════════════════════════════════════════════════════════════
# Persistence health
# ═════════════════════════════════════════════════════════════════════════════
gh_token_set = bool(store._github_token())
ph1, ph2, ph3 = st.columns(3)
ph1.metric(
    "GitHub persistence",
    "configured" if gh_token_set else "NOT set",
    help="When set, edits commit to the repo and survive restarts. When NOT set, "
         "edits are session-only on Cloud (the container's filesystem doesn't persist).",
)
ph2.metric("Repo", store.GITHUB_REPO.split("/")[-1])
ph3.metric("Branch", store.GITHUB_BRANCH)
if not gh_token_set:
    st.warning(
        "**GITHUB_TOKEN is not set.** Changes you make here will only last for this "
        "session on Cloud. Set the token in **Manage app → Settings → Secrets** to "
        "make settings persistent."
    )

st.divider()


# ═════════════════════════════════════════════════════════════════════════════
# Rebate rates
# ═════════════════════════════════════════════════════════════════════════════
st.subheader("Rebate rates")
st.caption(
    "Default percentages applied to OPD scan activity. Per-clinic overrides live in "
    "Rebate Program Controls; these are the program defaults."
)
rebate = cfg.setdefault("rebate", {})
rates = rebate.setdefault("rates", {})

rc1, rc2, rc3, rc4 = st.columns(4)
new_us_fin = rc1.number_input(
    "Ultrasound — finance", value=float(rates.get("ultrasound_finance", 0.10)),
    min_value=0.0, max_value=1.0, step=0.005, format="%.4f", key="cfg_us_fin",
)
new_us_sf = rc2.number_input(
    "Ultrasound — self-funded", value=float(rates.get("ultrasound_self_funded", 0.05)),
    min_value=0.0, max_value=1.0, step=0.005, format="%.4f", key="cfg_us_sf",
)
new_rads_fin = rc3.number_input(
    "Rads — finance", value=float(rates.get("rads_finance", 0.04)),
    min_value=0.0, max_value=1.0, step=0.005, format="%.4f", key="cfg_rads_fin",
)
new_rads_sf = rc4.number_input(
    "Rads — self-funded", value=float(rates.get("rads_self_funded", 0.02)),
    min_value=0.0, max_value=1.0, step=0.005, format="%.4f", key="cfg_rads_sf",
)

st.divider()


# ═════════════════════════════════════════════════════════════════════════════
# FLEX overage routing
# ═════════════════════════════════════════════════════════════════════════════
st.subheader("FLEX overage routing (SOP-12)")
st.caption(
    "Whether each finance partner handles overage billing for us, or whether we direct-bill "
    "the clinic. Per SOP-12: OnePlace handles overages by the cutoff (typically the 5th); "
    "Great America and NewLane decline and we invoice the clinic directly."
)

flex_cfg = cfg.setdefault("flex", {})
overage = flex_cfg.setdefault("overage", {})
handles = overage.setdefault("finance_partner_handles", {})

oc1, oc2 = st.columns(2)
new_handles = {}
finance_cos = sorted(set(list(handles.keys()) + ["OnePlace", "GreatAmerica", "NewLane", "SelfFinanced"]))
for i, co in enumerate(finance_cos):
    col = oc1 if i % 2 == 0 else oc2
    new_handles[co] = col.checkbox(
        f"{co} handles overages",
        value=bool(handles.get(co, False)),
        key=f"cfg_handles_{co}",
        help="When checked: we submit the overage to them by their cutoff. Unchecked: we invoice the clinic directly.",
    )

cc1, cc2 = st.columns(2)
new_cutoff = cc1.number_input(
    "Finance-partner cutoff day of month",
    min_value=1, max_value=28,
    value=int(overage.get("finance_partner_cutoff_day", 5)),
    key="cfg_cutoff_day",
    help="The day each month by which we must submit overages to a partner that handles them.",
)
new_direct_item = cc2.text_input(
    "Direct invoice item (QBO product/service)",
    value=overage.get("direct_invoice_item", "Telemedicine Overage"),
    key="cfg_direct_item",
)

new_memo_template = st.text_input(
    "Direct invoice memo template (use {quarter} for the quarter label)",
    value=overage.get("direct_invoice_memo_template", "Telemedicine Overages — {quarter}"),
    key="cfg_memo_tmpl",
)

new_no_refund = st.text_area(
    "No-refund policy text (surfaced in Stage 3 handoff emails)",
    value=overage.get("no_refund_policy", ""),
    key="cfg_no_refund",
    height=80,
)

new_escalations = st.text_area(
    "Escalation clinics (one per line) — flagged automatically when an overage hits these",
    value="\n".join(overage.get("escalation_clinics", [])),
    key="cfg_escalation",
    height=80,
)

st.divider()


# ═════════════════════════════════════════════════════════════════════════════
# Save settings
# ═════════════════════════════════════════════════════════════════════════════
st.subheader("Save")
commit_msg = st.text_input(
    "Commit message", value=f"Settings update — {dt.date.today().isoformat()}",
    key="cfg_commit_msg",
)
if st.button("Save settings", key="cfg_save"):
    # Reassemble the config dict
    rates["ultrasound_finance"] = round(new_us_fin, 6)
    rates["ultrasound_self_funded"] = round(new_us_sf, 6)
    rates["rads_finance"] = round(new_rads_fin, 6)
    rates["rads_self_funded"] = round(new_rads_sf, 6)
    overage["finance_partner_handles"] = new_handles
    overage["finance_partner_cutoff_day"] = int(new_cutoff)
    overage["direct_invoice_item"] = new_direct_item.strip()
    overage["direct_invoice_memo_template"] = new_memo_template.strip()
    overage["no_refund_policy"] = new_no_refund.strip()
    overage["escalation_clinics"] = [s.strip() for s in new_escalations.splitlines() if s.strip()]

    ok, info = store.save_json("config.json", cfg, commit_msg)
    loaders.config.clear()
    audit.record_cycle(
        cycle_type="settings_save",
        approver=auth.current_role(),
        params={"commit_message": commit_msg},
        note="config.json updated via Settings page",
    )
    (st.success if ok else st.warning)(info)

st.divider()


# ═════════════════════════════════════════════════════════════════════════════
# Backup / restore
# ═════════════════════════════════════════════════════════════════════════════
st.subheader("Backup")
st.caption(
    "Download every JSON in `data/` (masters, config, ledger, name map) as one zip. "
    "Restore later by uploading that same zip below. Use this before any risky settings "
    "change or operator handoff."
)

DATA_DIR = Path(store.DATA_DIR).resolve()

def _build_backup_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Snapshot every json under data/. Prefer the GitHub copy via store.load_json
        # so the backup reflects the live source-of-truth, not stale local files.
        for path in sorted(DATA_DIR.glob("*.json")):
            rel = path.name
            data, _ = store.load_json(rel, default=None)
            if data is None:
                continue
            zf.writestr(
                f"data/{rel}",
                json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"),
            )
        # Include a small manifest for human readability + restore validation
        manifest = {
            "exported_at": dt.datetime.now().isoformat(timespec="seconds"),
            "repo": store.GITHUB_REPO,
            "branch": store.GITHUB_BRANCH,
            "github_persistence": gh_token_set,
            "files": sorted(p.name for p in DATA_DIR.glob("*.json")),
        }
        zf.writestr(
            "manifest.json",
            json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
        )
    return buf.getvalue()


bcol1, bcol2 = st.columns([2, 1])
bcol1.caption(
    "Backup includes: rebate_master, flex_master, config, name_map, opd_item_map, "
    "service_prices, processed_payments (the ledger), plus a manifest.json with timestamp + repo info."
)
bcol2.download_button(
    "Download backup (.zip)",
    data=_build_backup_zip(),
    file_name=f"flexrebate_backup_{dt.date.today().isoformat()}.zip",
    mime="application/zip",
    key="cfg_backup_dl",
)

st.divider()


st.subheader("Restore")
st.caption(
    "Replace data/ with the contents of a previously-downloaded backup zip. **This is "
    "destructive** — every file in the zip overwrites its counterpart. Double-confirm "
    "before applying."
)

restore_up = st.file_uploader("Backup zip", type=["zip"], key="cfg_restore_file")
if restore_up is not None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(restore_up.getvalue()))
        files = [n for n in zf.namelist() if n.startswith("data/") and n.endswith(".json")]
        try:
            manifest = json.loads(zf.read("manifest.json"))
        except KeyError:
            manifest = None

        st.write(f"Zip contains {len(files)} data file(s):")
        for f in files:
            st.write(f"- `{f}`")
        if manifest:
            st.caption(
                f"Exported {manifest.get('exported_at', '?')} from "
                f"{manifest.get('repo', '?')}@{manifest.get('branch', '?')}."
            )

        confirm_text = st.text_input(
            'Type **RESTORE** (all caps) to confirm — this overwrites every data file in the zip.',
            key="cfg_restore_confirm",
        )
        if confirm_text == "RESTORE":
            if st.button("Apply restore", key="cfg_restore_apply"):
                applied, errors = 0, []
                msg = f"Restore from backup ({dt.date.today().isoformat()})"
                for member in files:
                    rel = member.removeprefix("data/")
                    try:
                        data = json.loads(zf.read(member))
                        ok, info = store.save_json(rel, data, msg)
                        if ok:
                            applied += 1
                        else:
                            errors.append(f"{rel}: {info}")
                    except Exception as e:
                        errors.append(f"{rel}: {type(e).__name__}: {e}")
                loaders.clear_caches()
                audit.record_cycle(
                    cycle_type="settings_restore",
                    approver=auth.current_role(),
                    source_file={
                        "name": restore_up.name,
                        "sha256": ledger.file_hash(restore_up.getvalue()),
                        "size_bytes": len(restore_up.getvalue()),
                    },
                    params={"applied_count": applied, "error_count": len(errors)},
                    note=f"Restored {applied}/{len(files)} files from backup zip",
                )
                if errors:
                    st.warning(
                        f"Restored {applied}/{len(files)} files. Errors:\n" + "\n".join(errors)
                    )
                else:
                    st.success(f"Restored {applied} file(s). Reload pages to see updated data.")
    except zipfile.BadZipFile:
        st.error("That doesn't look like a valid zip file.")

st.divider()


# ═════════════════════════════════════════════════════════════════════════════
# Ledger summary
# ═════════════════════════════════════════════════════════════════════════════
st.subheader("Processed-payments ledger")
summary = ledger.summary()
lc1, lc2, lc3, lc4 = st.columns(4)
lc1.metric("Files processed", summary["file_count"])
lc2.metric("Payments recorded", summary["payment_count"])
lc3.metric("Companies", len(summary["by_company"]))
lc4.metric(
    "Latest file",
    (summary["latest_uploaded_at"] or "—")[:10] if summary["latest_uploaded_at"] else "—",
)
if summary["by_company"]:
    by_co_str = " · ".join(f"**{k}**: {v}" for k, v in sorted(summary["by_company"].items()))
    st.caption(f"By company — {by_co_str}")

st.divider()


# ═════════════════════════════════════════════════════════════════════════════
# Audit manifest
# ═════════════════════════════════════════════════════════════════════════════
st.subheader("Audit manifest")
st.caption(
    "Immutable record of every cycle that produced QBO-bound files. Each entry is "
    "hash-anchored so post-hoc tampering is detectable. The GitHub commit history of "
    "`data/audit_log.json` is the authoritative tamper trail; the entry_hash check below "
    "is the in-app validation."
)

audit_summary = audit.summary()
a1, a2, a3, a4 = st.columns(4)
a1.metric("Total cycles", audit_summary["entry_count"])
a2.metric("Distinct types", len(audit_summary["by_type"]))
a3.metric("Distinct approvers", len(audit_summary["by_approver"]))
a4.metric(
    "Latest cycle",
    (audit_summary["latest_timestamp"] or "—")[:10] if audit_summary["latest_timestamp"] else "—",
)

if audit_summary["entry_count"]:
    ok_integ, tampered = audit.verify_integrity()
    if ok_integ:
        st.success(f"Integrity check: all {audit_summary['entry_count']} entries verified.")
    else:
        st.error(
            f"**Integrity check FAILED** — {len(tampered)} entry/entries have hash mismatches. "
            f"Entries: {tampered[:10]}. Investigate via the GitHub history of audit_log.json."
        )

    cycle_filter = st.selectbox(
        "Filter by cycle type",
        options=["(all)"] + sorted(audit_summary["by_type"].keys()),
        key="audit_filter",
    )
    limit = st.number_input("Show last N entries", min_value=5, max_value=500, value=25, step=5, key="audit_limit")
    entries = audit.list_entries(
        limit=int(limit),
        cycle_type=None if cycle_filter == "(all)" else cycle_filter,
    )
    # Flatten for table view
    rows = []
    for e in entries:
        outs = e.get("outputs") or []
        out_total = sum(o.get("total") or 0 for o in outs)
        out_rows = sum(o.get("row_count") or 0 for o in outs)
        out_names = ", ".join(o.get("name", "") for o in outs if o.get("name"))
        rows.append({
            "timestamp": e.get("timestamp", "")[:19],
            "cycle_type": e.get("cycle_type"),
            "approver": e.get("approver"),
            "year": e.get("year"),
            "month": e.get("month"),
            "output_rows": out_rows,
            "output_total": f"${out_total:,.2f}" if out_total else "",
            "output_files": out_names,
            "source_file": (e.get("source_file") or {}).get("name", ""),
            "note": e.get("note", ""),
            "entry_id": e.get("id", "")[:8],
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    if entries:
        with st.expander("Show full JSON for the most recent entry"):
            st.json(entries[0])
else:
    st.info("No audit entries yet. They start appearing once you 'Mark batch as imported' in Payment Cycle stages.")

st.divider()

# ── Lock Settings (require re-auth on next visit, without logging out) ────────
if _app_pw:
    lc1, lc2 = st.columns([3, 1])
    lc1.caption(
        "Done editing? Lock Settings again — the next visit will require the password "
        "without affecting your main session login on the other pages."
    )
    if lc2.button("Lock Settings", key="settings_lock", use_container_width=True):
        st.session_state.pop(SETTINGS_UNLOCK_KEY, None)
        st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
# DANGER ZONE — destructive ledger reset gated behind re-typed password
# ──────────────────────────────────────────────────────────────────────────────
st.divider()
st.markdown("### :red[Danger zone]")
st.error(
    ":material/dangerous: **Clear the processed-payments ledger.** "
    "This removes the full dedup history — every file hash and every per-payment "
    "fingerprint we've recorded. After clearing, re-uploads of remittances will "
    "**not** be caught and could be double-posted to QBO. Use only when migrating to "
    "a fresh environment or recovering from corruption you intentionally want to wipe.",
    icon=":material/warning:",
)
if _app_pw:
    cl_pw = st.text_input(
        "Re-enter the app password to confirm",
        type="password",
        key="clear_ledger_pw",
        help="Required even though Settings is already unlocked — destructive action.",
    )
    cl1, cl2 = st.columns([3, 1])
    cl1.caption(
        "Ledger lives at `data/processed_payments.json`. The clear writes an empty "
        "ledger and logs the action in the audit manifest."
    )
    if cl2.button(":red[Clear ledger]", key="clear_ledger_btn", use_container_width=True):
        if not cl_pw:
            st.warning("Type the app password above first.")
        elif cl_pw != _app_pw:
            st.error("Password didn't match. Ledger NOT cleared.")
        else:
            # Snapshot the current ledger size for the audit record before wiping.
            cur_data, cur_sha = ledger.load()
            prior_payments = len(cur_data.get("payments", []))
            prior_files = len(cur_data.get("files", []))
            ok, info = store.save_json(
                ledger.LEDGER_PATH, ledger._empty(),
                "Clear processed-payments ledger via Settings danger zone",
                sha=cur_sha,
            )
            audit.record_cycle(
                cycle_type="settings_clear_ledger",
                approver=auth.current_role(),
                params={"prior_payments": prior_payments, "prior_files": prior_files},
                source_file=None,
                outputs=[],
                note=(f"Wiped {prior_payments} payment fingerprint(s) and "
                      f"{prior_files} file hash(es) from processed_payments.json."),
            )
            if ok:
                st.success(
                    f"Ledger cleared. {prior_payments} payment fingerprint(s) "
                    f"and {prior_files} file hash(es) removed. Action logged in "
                    f"the audit manifest."
                )
            else:
                st.warning(
                    f"Cleared locally but GitHub commit failed: {info}. "
                    "Set GITHUB_TOKEN in secrets for persistent clears on Cloud."
                )
else:
    st.caption(":gray[Password not configured — clear-ledger action is unavailable.]")
