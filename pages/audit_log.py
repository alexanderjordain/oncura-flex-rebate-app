"""Audit & Tracking — read-only manifest viewer + ledger metrics + ledger reset.

Promoted out of Settings so everything audit/tracking-related lives in one
password-gated place, separate from operational config edits:

  - Audit manifest (immutable per-cycle record)
  - Processed-payments ledger summary (dedup metrics)
  - Danger zone: clear the processed-payments ledger

Same re-auth pattern as Settings: even though the user is logged in, re-prompt
for APP_PASSWORD before exposing audit data. The unlock is page-scoped — it
doesn't carry over to Settings or other pages.
"""
from __future__ import annotations

import streamlit as st

from core import audit, auth, ledger, loaders, store, ui

ui.header(
    "Audit & Tracking",
    "Audit manifest, dedup ledger metrics, and the destructive ledger-reset action. "
    "Read-only outside the danger zone.",
    kicker="Admin · Audit",
)

# ── Second password gate ──────────────────────────────────────────────────────
# Re-auth gate so the manifest doesn't sit visible on a shared screen and so the
# Clear-Ledger danger button can't be hit by someone who just walked up to an
# unattended session. The unlock key is page-scoped (independent of Settings)
# so unlocking one doesn't auto-unlock the other.
AUDIT_UNLOCK_KEY = "audit_log_unlocked"
_app_pw = auth._secret(["APP_PASSWORD"])

if _app_pw and not st.session_state.get(AUDIT_UNLOCK_KEY):
    st.markdown("### Confirm password to view the audit log")
    st.caption(
        "The audit log is a privileged record of every cycle's source files, output "
        "hashes, totals, and approvers — and the danger-zone clear-ledger action lives "
        "here too. Re-enter the app password to unlock — same one you used to log in."
    )
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        pw = st.text_input("Password", type="password", key="audit_unlock_pw")
        if st.button("Unlock Audit & Tracking", use_container_width=True):
            if pw == _app_pw:
                st.session_state[AUDIT_UNLOCK_KEY] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.stop()


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
    limit = st.number_input(
        "Show last N entries",
        min_value=5, max_value=500, value=25, step=5,
        key="audit_limit",
    )
    entries = audit.list_entries(
        limit=int(limit),
        cycle_type=None if cycle_filter == "(all)" else cycle_filter,
    )
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
        with st.expander(":gray[Show full JSON for the most recent entry]"):
            st.json(entries[0])
else:
    st.info(
        "No audit entries yet. They start appearing once you click 'Mark X as imported' "
        "(or the equivalent record button) inside a Payment Cycle or Rebate Cycle run."
    )

st.divider()


# ═════════════════════════════════════════════════════════════════════════════
# Processed-payments ledger summary
# ═════════════════════════════════════════════════════════════════════════════
st.subheader("Processed-payments ledger")
st.caption(
    "Dedup ledger that protects against re-importing the same finance payment or "
    "credit memo. Read-only here — entries are added automatically by the Mark-as-imported "
    "step at the end of each Payment Cycle stage."
)
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
# Lock button — restores the gate without affecting the main session login
# ═════════════════════════════════════════════════════════════════════════════
if _app_pw:
    lc1, lc2 = st.columns([3, 1])
    lc1.caption(
        "Done reviewing? Lock the page — the next visit will require the password "
        "again without affecting your main session login on the other pages."
    )
    if lc2.button("Lock Audit & Tracking", key="audit_lock", use_container_width=True):
        st.session_state.pop(AUDIT_UNLOCK_KEY, None)
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# DANGER ZONE — destructive ledger reset gated behind re-typed password
# ─────────────────────────────────────────────────────────────────────────────
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
        help="Required even though the page is already unlocked — destructive action.",
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
                "Clear processed-payments ledger via Audit & Tracking danger zone",
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
            # Invalidate ALL Streamlit data caches so Stage 1 / Stage 2 / Home don't
            # serve stale ledger or master data on the next render.
            try:
                st.cache_data.clear()
                loaders.clear_caches()
            except Exception:
                pass
            # Also clear Stage 1 file-related session state so a re-upload after the
            # clear doesn't keep the prior file's "already seen" override sticky.
            for k in ("remit_file", "remit_file_override", "remit_reissue_ack"):
                st.session_state.pop(k, None)

            if ok:
                # Verify the clear actually landed by re-reading the ledger immediately.
                verify_data, _ = ledger.load()
                verify_payments = len(verify_data.get("payments", []))
                verify_files = len(verify_data.get("files", []))
                if verify_payments == 0 and verify_files == 0:
                    st.success(
                        f"Ledger cleared. {prior_payments} payment fingerprint(s) "
                        f"and {prior_files} file hash(es) removed. Verified empty "
                        "on re-read. Action logged in the audit manifest."
                    )
                else:
                    st.error(
                        f"Save reported success but ledger re-read STILL shows "
                        f"{verify_payments} payment(s) / {verify_files} file(s). "
                        "GitHub may not have committed — check the data/processed_payments.json "
                        "file on the repo. Try clearing again, or hard-refresh the app."
                    )
            else:
                st.warning(
                    f"Cleared locally but GitHub commit failed: {info}. "
                    "Set GITHUB_TOKEN in secrets for persistent clears on Cloud."
                )
else:
    st.caption(":gray[Password not configured — clear-ledger action is unavailable.]")
