"""FLEX Stage 4 — Closeout operator guide.

Stages 1-3 of the Payment Cycle produce SaasAnt files and numbers; they do NOT
touch QuickBooks Online (QBO) or OPD directly. Stage 4 is the *guide* that tells
the operator the manual QBO + OPD steps that finish a quarter after Stages 1-3.
Going forward we no longer hand these steps off by email — this page replaces
those instructions (source: the Tanya closeout meetings).

Design:
  - The long prose lives in module-level string builders (functions that return
    markdown) so they're testable without a Streamlit runtime.
  - `render_closeout(...)` draws the whole Stage 4 UI with streamlit.
  - Two small pure helpers (`group_members`, `corporate_clinics`) slice the
    flex_master clinic list for the group-specific and corporate sections.

No emojis anywhere — Material Symbols only, written as ":material/<name>:".
"""
from __future__ import annotations

import streamlit as st


# ═══════════════════════════════════════════════════════════════════════════════
# PURE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def group_members(flex_clinics) -> dict[str, list[str]]:
    """Map group_id -> [qb_name, ...] using each clinic's "group_id" field.

    `flex_clinics` is the list of clinic dicts from data/flex_master.json.
    Clinics with no group_id (None / empty) are skipped. Order within a group
    follows the order clinics appear in the master list.
    """
    groups: dict[str, list[str]] = {}
    for clinic in flex_clinics or []:
        gid = clinic.get("group_id")
        if not gid:
            continue
        name = clinic.get("qb_name") or clinic.get("clinic_name")
        if not name:
            continue
        groups.setdefault(gid, []).append(name)
    return groups


def corporate_clinics(flex_clinics) -> list[str]:
    """Return qb_names whose name contains "CityVet" (case-insensitive)."""
    out: list[str] = []
    for clinic in flex_clinics or []:
        name = clinic.get("qb_name") or clinic.get("clinic_name") or ""
        if "cityvet" in name.lower():
            out.append(name)
    return out


def _members_for(flex_clinics, group_id: str) -> list[str]:
    """Convenience: members of one group, empty list if the group is absent."""
    return group_members(flex_clinics).get(group_id, [])


# ═══════════════════════════════════════════════════════════════════════════════
# CONTENT BUILDERS — return markdown strings (testable without streamlit)
# ═══════════════════════════════════════════════════════════════════════════════

def intro_md() -> str:
    """Stage 4 framing: what it is, scope, and the ~2-day expectation."""
    return (
        "**Stage 4 is the manual QBO + OPD work that finishes a quarter** after "
        "Stages 1-3 have generated their files and numbers. Stages 1-3 do not "
        "perform any QBO or OPD action for you — this page is the checklist and "
        "reference for the hands-on tie-up.\n\n"
        "Work **only the clinics whose quarter closed this month** — take that "
        "list from the Stage 3 (Unused / Overage) report. Every account differs, "
        "and a full tie-up can take roughly **two days**. Nothing below is "
        "automated; the app does not touch QBO or OPD on your behalf."
    )


def qbo_tieup_md() -> str:
    """The core per-clinic QBO Receive-Payment tie-up, done at quarter-end."""
    return (
        "The core step, run **per clinic, only at the quarter-end tie-up**:\n\n"
        "1. **Roll the tracker to the new quarter.** Color the closing quarter — "
        "**blue = unused**, **green = overage** — so the state is visible at a glance.\n"
        "2. **Confirm the expected item exists in QBO** for this clinic: either the "
        "`Unused-Flex-Credits` invoice (unused quarter) or the overage.\n"
        "3. **Receive Payment** with the **date = the last day of the quarter month**.\n"
        "4. **Apply this quarter's lines:** the finance-company payment(s) + the credit "
        "memo(s) + the unused invoice. **Every applied line must have \"FLEX\" in the "
        "name** — never \"merchant services\", never blank. A blank or mis-labeled line "
        "is the usual cause of a non-zero balance; relabel it or investigate before moving on.\n"
        "5. **Verify the balance nets to $0.** QBO will pull in stray old credits and "
        "payments — prior quarters, December entries, stray journal entries. **Un-apply "
        "anything that isn't this quarter's FLEX.** If a journal entry (from Mike or "
        "Jennifer) blocks the zero, **leave it off and flag Jennifer** — do not force it.\n"
        "6. **In OPD, mark this quarter's FLEX invoices PAID** and add your initials in "
        "the memo. The initials signal \"Accounting processed this through the program; "
        "the clinic did not pay it.\""
    )


def overage_md(overage_clinics=None) -> str:
    """Overage handling: mark Past Due in OPD, bill outside OPD.

    When `overage_clinics` is provided, the returned markdown names them
    explicitly as the clinics to flip to Past Due; otherwise it points the
    operator at the Stage 3 overage list.
    """
    if overage_clinics:
        listed = "\n".join(f"- {c}" for c in overage_clinics)
        which = (
            "**Mark these clinics' OPD invoices Past Due** (this quarter's overages, "
            "from Stage 3):\n\n" + listed + "\n\n"
        )
    else:
        which = (
            "Run Stage 3 to populate the specific list — then flip **each overage "
            "clinic's** OPD invoice to Past Due. (No overage list was passed in, so use "
            "the Stage 3 overage report as the source of which clinics to mark.)\n\n"
        )
    return (
        "All FLEX OPD invoices now **default to Paid**. Your job is to flip the "
        "**overage clinics'** invoices to **Past Due** in OPD.\n\n"
        + which +
        "**Overages are NOT billed through OPD.** How to bill them:\n\n"
        "- **Non-corporate overages → authorize.net** (cleanest path).\n"
        "- **If QBO must be used**, make a dummy invoice labeled **\"Telemedicine "
        "Overage\"** and **VOID it after payment** — QBO's QR code otherwise "
        "double-counts the payment.\n"
        "- **OnePlace overages** are submitted to the partner **by the cutoff (the 5th)**. "
        "OnePlace charges no fees, which is why we still route through them.\n\n"
        "**Keep the overage invoice OPEN / Past Due in OPD until the clinic pays.** The "
        "clinic verifies its OPD balance against the invoice to the penny before paying, "
        "so change nothing on it until it's paid — then mark it paid.\n\n"
        "**Avoid double-payment:** keep **only** the overage invoice open and mark every "
        "OTHER OPD invoice paid so nothing else can be paid. If a clinic pays via OPD "
        "\"merchant services\", apply it to the **oldest open overage invoice**. For "
        "auto-pay clinics, **preemptively close the non-overage OPD invoices**.\n\n"
        "_Note: authorize.net access is currently Tanya-only._"
    )


def groups_md(flex_clinics, group_spread=None) -> str:
    """Multi-clinic group spread rule + the three known groups.

    When `group_spread` (a list of credit-move dicts) is provided, the moves are
    rendered as an explicit "move $X credit from A to B" list; otherwise the
    manual method is described.
    """
    mohnacky = _members_for(flex_clinics, "mohnacky")
    pr_vets = _members_for(flex_clinics, "pr-vets")
    river_trail = _members_for(flex_clinics, "river-trail")

    def _fmt(members: list[str]) -> str:
        return ", ".join(members) if members else "(no members found in the FLEX master)"

    if group_spread:
        rows = []
        for m in group_spread:
            amt = m.get("amount")
            frm = m.get("from") or m.get("from_clinic") or "?"
            to = m.get("to") or m.get("to_clinic") or "?"
            try:
                amt_str = f"${float(amt):,.2f}"
            except (TypeError, ValueError):
                amt_str = f"${amt}"
            rows.append(f"- Move **{amt_str}** credit from **{frm}** → **{to}**")
        spread_block = (
            "**This quarter's documented credit moves** (audit-friendly — spread via "
            "CREDITS ONLY, not payments):\n\n" + "\n".join(rows) + "\n\n"
        )
    else:
        spread_block = (
            "No spread moves were passed in — do it manually: within each group, move "
            "overage from the **over-utilizing** clinics to the **under-utilizing** ones "
            "using **credits only** (never payments), and clear the group's OPD invoices "
            "monthly so they never pay. Document each move for the audit trail.\n\n"
        )

    return (
        "**Rule:** overages must be spread **ACROSS a group's clinics using CREDITS "
        "ONLY** (not payments) — move overage from over-utilizing clinics to "
        "under-utilizing ones, and clear the group's OPD invoices monthly so they never "
        "pay.\n\n"
        + spread_block +
        "**Mohnacky group** — members: " + _fmt(mohnacky) + ".\n"
        "  Total group hurdle is **$6,000**; the contract is on **Carlsbad only** (FLEX "
        "is collected only on Carlsbad). Vista and Escondido scan packages have expired. "
        "This is the **most complicated account** — walk the contact invoice-by-invoice, "
        "**ACH only** (no checks), and **never auto-pull** from their account.\n\n"
        "**PR-vets group** — members: " + _fmt(pr_vets) + ".\n"
        "  Each clinic pays its own FLEX, but overages **MUST be spread across the group** "
        "(contractual). They are **Spanish-speaking** — send invoices to **billing**, not "
        "the admin email. **NEVER let them pay on OPD** (they are notorious for paying).\n\n"
        "**River Trail group** — members: " + _fmt(river_trail) + ".\n"
        "  Tulsa + Memorial arrive as **one GA payment**; split it **Tulsa 921.84 / "
        "Memorial 921.83**, with credit memos **978.16 / 978.17**. A **penny-exact OPD "
        "match** is demanded."
    )


def corporate_md(flex_clinics) -> str:
    """Corporate / direct-pay clinics (CityVet + Preston Forest)."""
    corp = corporate_clinics(flex_clinics)
    corp_list = ", ".join(corp) if corp else "(no CityVet clinics found in the FLEX master)"
    return (
        "**Corporate / direct-pay clinics do NOT pay through OPD.** These are the "
        "**CityVet** clinics (" + corp_list + ") and **Preston Forest**.\n\n"
        "Their billing manager emails at the **start of the month** requesting the last "
        "**~3 months of OPD statements** for all their clinics. You pull each account's "
        "**Statements**, send them, and they **wire the payments together**.\n\n"
        "_Mark: confirm CityVet's exact process with Tanya._"
    )


def get_off_opd_md() -> str:
    """The plan to make FLEX clinics view-only in OPD (Lawrence to build)."""
    return (
        "**Plan (Lawrence to build):** disable FLEX clinics' ability to pay in OPD "
        "(make them **view-only**), which also stops autopay. **Re-enable** a clinic "
        "when it leaves FLEX, and **record that as a note in the QBO customer's Notes.**\n\n"
        "An account with **3 open invoices (even $0) auto-locks** — clean up phantom $0 "
        "invoices so a clinic doesn't lock unexpectedly.\n\n"
        "If a clinic **won't pay its overage, lock it.**"
    )


def policies_md() -> str:
    """Standing policies + the recurring per-clinic exceptions."""
    return (
        "**Standing policies**\n\n"
        "- **No refunds on FLEX overpayments** — reallocate the money as a **credit**. "
        "Marty approval required for any exception.\n"
        "- **Don't issue credit memos to a clinic that dropped off** (Stage 2 already "
        "skips no-payment clinics).\n"
        "- **Late finance-partner payments** post to the **month received**.\n"
        "- **Credits must be added to the OPD account-credit field (top)**, NOT onto an "
        "existing invoice, so OPD and QBO match. Escalate any clinic that refuses to pay "
        "until the credit shows on a past invoice.\n\n"
        "**Recurring per-clinic exceptions**\n\n"
        "- **Luv-N-Care** runs a one-off OPD-managed program that Tanya continues to manage.\n"
        "- **South Central** converts to a normal non-FLEX account once its Q10 is installed.\n"
        "- **Silicon Valley** is upgrading — keep it on FLEX until installed, then remove "
        "and true up after the finance payoff.\n"
        "- **Veterinary Cancer Care** — do nothing.\n"
        "- **Animal Care Experts** — a credit was put on the OPD invoice, not the "
        "account-credit field; label it **\"OPD credit\"** and email Michelle."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# RENDER
# ═══════════════════════════════════════════════════════════════════════════════

def render_closeout(flex_clinics, config, *, overage_clinics=None, group_spread=None):
    """Draw the Stage 4 — Closeout operator guide.

    Args:
        flex_clinics: list of clinic dicts (from data/flex_master.json "clinics").
        config: the loaded config.json dict (reserved for future use; the guide
            reads its content from the module builders, not config).
        overage_clinics: optional list of qb_names with an overage this quarter —
            the clinics whose OPD invoice must be marked PAST DUE. Comes from
            Stage 3. When None, the guide gives generic guidance and points at the
            Stage 3 report.
        group_spread: optional list of credit-move dicts, e.g.
            {"amount": 123.45, "from": "Clinic A", "to": "Clinic B"}. Comes from
            Stage 3. When None, the guide describes the manual spread method.
    """
    st.subheader("Stage 4 — Closeout")
    st.caption(
        "The manual QBO + OPD steps that finish a quarter after Stages 1-3. "
        "Replaces the old email hand-off."
    )
    st.markdown(intro_md())

    # ── Closeout checklist ───────────────────────────────────────────────────
    st.subheader("Closeout checklist")
    st.caption(
        "Work only the clinics whose quarter closed this month (from the Stage 3 report)."
    )
    st.checkbox(
        "Rolled the tracker to the new quarter and colored the closing quarter "
        "(blue = unused, green = overage)",
        key="closeout_chk_tracker",
    )
    st.checkbox(
        "QBO tie-up done per clinic — Receive Payment dated the last day of the "
        "quarter month, every applied line has \"FLEX\" in the name, balance nets to $0",
        key="closeout_chk_qbo",
    )
    st.checkbox(
        "OPD FLEX invoices for the quarter marked PAID, with operator initials in the memo",
        key="closeout_chk_opd_paid",
    )
    st.checkbox(
        "Overage clinics' OPD invoices flipped to PAST DUE and billed outside OPD "
        "(authorize.net / OnePlace by the 5th)",
        key="closeout_chk_overage",
    )
    st.checkbox(
        "Multi-clinic group overages spread via CREDITS ONLY; group OPD invoices cleared",
        key="closeout_chk_groups",
    )
    st.checkbox(
        "Corporate / direct-pay statements pulled and sent (CityVet, Preston Forest)",
        key="closeout_chk_corporate",
    )

    # ── Detail sections ──────────────────────────────────────────────────────
    with st.expander(":material/account_balance: QBO tie-up — receive payment", expanded=True):
        st.markdown(qbo_tieup_md())

    with st.expander(":material/schedule: Overages — mark Past Due, bill outside OPD", expanded=True):
        st.markdown(overage_md(overage_clinics))

    with st.expander(":material/groups: Multi-clinic groups — spread overages via credits (audit-friendly)"):
        st.markdown(groups_md(flex_clinics, group_spread))

    with st.expander(":material/corporate_fare: Corporate / direct-pay clinics"):
        st.markdown(corporate_md(flex_clinics))

    with st.expander(":material/lock: Get clinics off OPD payment"):
        st.markdown(get_off_opd_md())

    with st.expander(":material/policy: Policies & recurring exceptions"):
        st.markdown(policies_md())
