"""Per-workflow email drafts to the accounting team.

Each builder returns (subject, body). The page calls
`render_handoff(subject, body, attachments=[...])` which lets the user either:

  1. **Send directly** via SMTP (if SMTP_HOST is configured in secrets) — with a
     two-click preview-then-confirm flow so nothing leaves the app on a single
     misclick. Attachments are included.
  2. **Download a .eml draft** — universal fallback. Works on every machine
     (Windows, macOS, Linux). Double-click opens in Outlook / Apple Mail / etc.
     with body and attachments pre-loaded.
  3. **Open mailto** — kept as a final fallback for environments where neither
     SMTP nor a .eml handler is available. Mailto cannot carry attachments;
     the file is referenced by name only.

Canonical reference for the manual steps lives in docs/ACCOUNTING_HANDOFF.md.
"""
from __future__ import annotations

import datetime as dt
import io
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote

import streamlit as st

TO = "accounting@oncurapartners.com"


# ── SMTP helpers ──────────────────────────────────────────────────────────────


def _smtp_config():
    """Read SMTP creds from Streamlit secrets. Returns dict or None."""
    try:
        host = st.secrets.get("SMTP_HOST")
        if not host:
            return None
        return {
            "host":       host,
            "port":       int(st.secrets.get("SMTP_PORT", 587)),
            "user":       st.secrets.get("SMTP_USER", ""),
            "password":   st.secrets.get("SMTP_PASSWORD", ""),
            "from":       st.secrets.get("SMTP_FROM", st.secrets.get("SMTP_USER", "")),
            "use_tls":    bool(st.secrets.get("SMTP_USE_TLS", True)),
        }
    except Exception:
        return None


def _build_message(subject: str, body: str, to: str, sender: str,
                   attachments: list[tuple[str, bytes]] | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = to
    msg.set_content(body)
    for filename, blob in attachments or []:
        ctype, _ = mimetypes.guess_type(filename)
        maintype, subtype = (ctype.split("/", 1) if ctype else ("application", "octet-stream"))
        msg.add_attachment(blob, maintype=maintype, subtype=subtype, filename=filename)
    return msg


def _send_smtp(subject: str, body: str, to: str,
               attachments: list[tuple[str, bytes]] | None,
               cfg: dict) -> tuple[bool, str]:
    """Send via SMTP. Returns (ok, info)."""
    msg = _build_message(subject, body, to, cfg["from"], attachments)
    try:
        if cfg["port"] == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=ctx, timeout=30) as s:
                if cfg["user"]: s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as s:
                if cfg["use_tls"]:
                    s.starttls(context=ssl.create_default_context())
                if cfg["user"]:
                    s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        return True, f"Sent to {to} via {cfg['host']}."
    except Exception as e:
        return False, f"SMTP send failed: {e}"


def _body_to_html(body: str) -> str:
    """Convert the plain-text body to an Outlook-friendly HTML structure.

    The body is wrapped in a single <div class="WordSection1"> with each line
    as its own <div>. This is the structure Outlook for Windows produces for
    new messages — when an .eml with this structure is opened in compose mode
    (via X-Unsent:1), Outlook's auto-inserted signature lands at the END of
    the WordSection1 container instead of being welded to the start of the
    body. Plain-text .eml bodies don't get this treatment — Outlook inserts
    the signature at position 0 with no separator, producing the well-known
    'Best,\\nAlexander JordainHi Tanya,' glue.

    Defensive: also lead with an empty <div> so if a given Outlook build still
    inserts at the top, there's visual separation from the greeting.
    """
    import html
    lines = body.split("\n")
    rendered = []
    for line in lines:
        if line.strip() == "":
            rendered.append("<div>&nbsp;</div>")
        else:
            # Preserve leading spaces (used for indented bullet lines) by
            # converting them to &nbsp; sequences — HTML otherwise collapses
            # runs of whitespace.
            leading = len(line) - len(line.lstrip(" "))
            text = html.escape(line.lstrip(" "))
            prefix = "&nbsp;" * leading
            rendered.append(f"<div>{prefix}{text}</div>")
    inner = "\n".join(rendered)
    return (
        '<html><body>'
        '<div class="WordSection1">'
        '<div>&nbsp;</div>'  # defensive separator if Outlook still top-inserts
        f'{inner}'
        '<div>&nbsp;</div>'  # cursor-landing slot where signature ideally inserts
        '</div></body></html>'
    )


def _build_eml_bytes(subject: str, body: str, to: str,
                     attachments: list[tuple[str, bytes]] | None) -> bytes:
    """Build an .eml file. Omits From/Date/Message-ID so the user's mail client
    fills From from their own account on open. Adds X-Unsent:1 — classic Outlook
    recognizes this and opens the file in compose mode rather than read mode.
    OWA / new Outlook still tends to open in reader (Microsoft limitation);
    Microsoft Graph integration is the proper path for those clients.

    Body is sent as multipart/alternative with both text/plain and text/html
    parts. Outlook prefers the HTML part, which is structured to make the
    user's auto-inserted signature land at the END of the body rather than
    glued to "Hi Tanya," — see _body_to_html()."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"]      = to
    msg["X-Unsent"] = "1"
    msg.set_content(body)                                   # text/plain part
    msg.add_alternative(_body_to_html(body), subtype="html")  # text/html part
    for filename, blob in attachments or []:
        ctype, _ = mimetypes.guess_type(filename)
        maintype, subtype = (ctype.split("/", 1) if ctype else ("application", "octet-stream"))
        msg.add_attachment(blob, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


def mailto_link(subject: str, body: str, to: str = TO) -> str:
    return f"mailto:{to}?subject={quote(subject)}&body={quote(body)}"


# ── UI: handoff card ──────────────────────────────────────────────────────────


def render_handoff(
    subject: str,
    body: str,
    key_prefix: str = "handoff",
    attachments: list[tuple[str, bytes]] | None = None,
):
    """Render the email-draft card. Priority order:
        1. Microsoft Graph (draft in user's own Outlook — user clicks Send there)
        2. SMTP send (auto-send from configured account, with confirm step)
        3. .eml download (universal fallback)
        4. mailto: link (last resort, no attachment)
    """
    from core import graph_email  # local import keeps module load-time small
    with st.container(border=True):
        st.markdown("### Hand off to accounting")
        st.caption(f"Sends to **{TO}** with this cycle's numbers + action items pre-filled.")

        used_path = False
        if graph_email.is_configured():
            _render_graph_path(subject, body, attachments, key_prefix, graph_email)
            used_path = True

        cfg = _smtp_config()
        if cfg and not used_path:
            _render_smtp_path(subject, body, attachments, key_prefix, cfg)
            used_path = True

        if not used_path:
            _render_eml_path(subject, body, attachments, key_prefix)

        # Preview is always available
        with st.expander(":gray[Preview / copy the full email body]"):
            st.caption(f"To: {TO}  ·  Subject: {subject}")
            if attachments:
                st.caption("Attached: " + ", ".join(f"**{n}** ({len(b):,} bytes)" for n, b in attachments))
            st.code(body, language="text")


def _render_graph_path(subject, body, attachments, key_prefix, graph_email):
    """Create a draft in the user's own Outlook. User clicks Send in Outlook."""
    if not graph_email.is_connected():
        with st.expander("**First time? Read this before clicking the button below.**", expanded=False):
            st.markdown(
                "**What this does**\n"
                "- Creates an email **draft** in *your* Outlook Drafts folder, with the body and "
                "all attachments already in place.\n"
                "- You open Outlook (web or desktop), review the draft, edit anything you want, "
                "then click **Send** there. The email goes **from your address**, with your "
                "signature — exactly like an email you composed yourself.\n\n"
                "**Step-by-step (first time only — ~30 seconds)**\n"
                "1. Click **Connect Outlook** below.\n"
                "2. You'll be redirected to Microsoft's sign-in page. **Don't worry — your work in "
                "the app is saved.** Sign in with your Oncura email (`@oncurapartners.com`).\n"
                "3. Microsoft will ask you to grant the app permission to read/write email. "
                "Click **Accept**. (This is required so the app can create the draft.)\n"
                "4. You'll be redirected back to the app. A green **'Outlook connected'** banner "
                "appears at the top.\n"
                "5. A new button appears: **Create draft in my Outlook**. Click it.\n"
                "6. Open Outlook in another tab/window. Look in **Drafts** — the email is there "
                "with the attachment(s). Review, edit if needed, click **Send** in Outlook.\n\n"
                "**Every other time:** if you've already signed in this browser session, the app "
                "remembers you. Just click **Create draft in my Outlook**, then open Outlook → "
                "Drafts → Send. (If you closed and reopened the browser, you'll sign in once more.)\n\n"
                "**Permission granted is narrow:** the app can only create drafts in your mailbox. "
                "It cannot read your inbox, send without you, or access anything else."
            )
        auth_url = graph_email.get_auth_url()
        st.link_button("Connect Outlook", auth_url)
        return

    user = graph_email.get_user_info() or {}
    cols = st.columns([3, 1])
    cols[0].markdown(
        f"Signed in as **{user.get('name') or user.get('email') or 'Outlook user'}**"
        + (f" ({user['email']})" if user.get('email') and user.get('name') else "")
    )
    if cols[1].button("Disconnect", key=f"{key_prefix}_disc", use_container_width=True):
        graph_email.disconnect()
        st.rerun()

    created_key = f"{key_prefix}_graph_created"
    if st.session_state.get(created_key):
        link = st.session_state[created_key]
        st.success("Draft created in your Outlook → Drafts folder.")
        st.markdown(
            "**Next steps:**\n"
            "1. Open Outlook (web at `outlook.office.com`, or desktop).\n"
            "2. Click **Drafts** in the left sidebar.\n"
            "3. Open the draft titled with this cycle's subject line.\n"
            "4. Verify the attachment is there and the body is correct.\n"
            "5. Click **Send** in Outlook — that's it."
        )
        if link and link.startswith("http"):
            st.link_button("Or open the draft directly in Outlook web", link)
        if st.button("Create another draft for this cycle", key=f"{key_prefix}_graph_again"):
            del st.session_state[created_key]
            st.rerun()
        return

    st.markdown(
        "**What happens next:** clicking the button below creates a draft in your "
        "Outlook → Drafts folder with the body and attachments. The app does **not** "
        "send the email — you open Outlook and click Send yourself."
    )
    if st.button("Create draft in my Outlook", key=f"{key_prefix}_graph_create"):
        with st.spinner("Creating draft via Microsoft Graph…"):
            ok, info = graph_email.create_draft(subject, body, TO, attachments)
        if ok:
            st.session_state[created_key] = info
            st.rerun()
        else:
            st.error(info)


def _render_smtp_path(subject, body, attachments, key_prefix, cfg):
    """Two-click send: 'Prepare to send' → 'Confirm send'."""
    confirm_key = f"{key_prefix}_confirm"
    sent_key    = f"{key_prefix}_sent"

    if st.session_state.get(sent_key):
        st.success(st.session_state[sent_key])
        if st.button("Compose another", key=f"{key_prefix}_reset"):
            del st.session_state[sent_key]
            st.session_state.pop(confirm_key, None)
            st.rerun()
        return

    if not st.session_state.get(confirm_key):
        if st.button("Prepare to send", key=f"{key_prefix}_prep"):
            st.session_state[confirm_key] = True
            st.rerun()
        st.caption(
            f"Will send from `{cfg['from']}` via `{cfg['host']}:{cfg['port']}`. "
            "Click once to review, click again to send."
        )
    else:
        st.warning(
            f"**Ready to send** to {TO}"
            + (f" with {len(attachments)} attachment(s)" if attachments else " (no attachments)")
            + ". This will leave your outbox. Confirm below."
        )
        col_cancel, col_send = st.columns([1, 2])
        with col_cancel:
            if st.button("Cancel", key=f"{key_prefix}_cancel", use_container_width=True):
                del st.session_state[confirm_key]
                st.rerun()
        with col_send:
            if st.button("Confirm — Send now", key=f"{key_prefix}_send",
                         type="primary", use_container_width=True):
                ok, info = _send_smtp(subject, body, TO, attachments, cfg)
                if ok:
                    st.session_state[sent_key] = info
                    st.session_state.pop(confirm_key, None)
                    st.rerun()
                else:
                    st.error(info)


def _render_eml_path(subject, body, attachments, key_prefix):
    """Universal fallback: download a .eml file that opens in any mail client.
    Note: in new Outlook / OWA, .eml typically opens in read-only viewer (Microsoft
    limitation). Set up Microsoft Graph (see docs/AZURE_AD_SETUP.md) for a real
    editable draft in the user's Drafts folder."""
    eml_bytes = _build_eml_bytes(subject, body, TO, attachments)
    safe_subj = "".join(c if c.isalnum() or c in "-_" else "_" for c in subject)[:60]
    st.download_button(
        "Download email draft (.eml)",
        eml_bytes,
        file_name=f"{safe_subj}.eml",
        mime="message/rfc822",
        key=f"{key_prefix}_eml",
    )
    with st.expander(":gray[How to use the `.eml` file (first-time read)]", expanded=False):
        st.markdown(
            "1. Click **Download email draft (.eml)** above. Your browser saves it to your "
            "**Downloads** folder.\n"
            "2. Open Windows File Explorer, go to **Downloads**, find the `.eml` file you "
            "just downloaded.\n"
            "3. **Double-click** it. What happens next depends on how you read your email:\n\n"
            "**Outlook desktop app (Classic or New)** — easiest path  \n"
            "The `.eml` opens in an editable compose window with the To address, Subject, body, "
            "and attachments **already filled in**. Just glance over it and click **Send**. Done.\n\n"
            "**Outlook on the web (browser tab)** — needs a quick workaround  \n"
            "Microsoft only lets `.eml` files open in read-only view inside the browser, so you "
            "can't send directly from the preview. About 30 seconds of copy-paste fixes it:\n\n"
            "1. **Keep the `.eml` file you just downloaded** — you'll attach it to a new email "
            "in a moment, so don't delete it.\n"
            "2. In Outlook, click **New mail** (top-left, blue **+** icon).\n"
            "3. In the **To** field, paste: `accounting@oncurapartners.com`\n"
            "4. Open the **Preview / copy the full email body** expander on this page (below). "
            "Copy the **Subject** and paste it into the Subject line of your new email; copy "
            "the **Body** and paste it into the email body.\n"
            "5. Attach the `.eml` file — either way works:\n"
            "    - **Drag and drop:** open your Downloads folder in a separate window, drag the "
            "`.eml` file straight into the body of the new Outlook email.\n"
            "    - **Or click the paperclip icon** in Outlook's toolbar → **Browse this computer** "
            "→ Downloads → select the `.eml`.\n"
            "6. Double-check the **To** address is correct and the attachment appears at the bottom.\n"
            "7. Click **Send**.\n\n"
            "*Want to skip the copy-paste every time?* Ask IT to set the Outlook desktop app as "
            "your default `.eml` handler. Right-click any `.eml` file → **Open with** → **Choose "
            "another app** → pick **Outlook** (the classic blue-envelope desktop one) → tick "
            "**Always use this app to open .eml files**. From then on, double-clicking goes "
            "straight to the editable compose window."
        )
    with st.expander(":gray[Last-resort: mailto link (no attachment)]"):
        st.link_button("Open mailto link", mailto_link(subject, body))
        st.caption(
            "Opens a fresh email in your default mail client with To/Subject/Body filled in. "
            "**Does not carry attachments** — you'd have to attach the file yourself. "
            "Use only if the .eml download isn't working."
        )


# ── Workflow-specific builders (unchanged signatures) ─────────────────────────


def finance_payment_email(*, company: str, pay_date, summary: dict, has_scan: bool) -> tuple[str, str]:
    """Build email for Finance Payment Import handoff."""
    subj = f"[Action Required] {company} FLEX Payment Import — {pay_date}"
    parts = [
        f"Hi accounting,",
        "",
        f"Ran the {company} payment import for the {pay_date} remittance.",
        "",
        "Summary:",
        f"  - Flex receive-payments: {summary['flex_count']}  (${summary['flex_total']:,.2f})",
    ]
    if has_scan:
        parts += [
            f"  - Scan invoices:        {summary['scan_count']}  (${summary['scan_total']:,.2f})",
            f"  - Scan receive-payments: {summary['scan_count']}  (${summary['scan_total']:,.2f})",
        ]
    parts += [
        f"  - Total:                ${summary['total']:,.2f}",
        "",
        "Files attached for SaasAnt upload:",
    ]
    if has_scan:
        parts += [
            "  1. Scan invoices (upload first — payments need invoices to apply against)",
            "  2. Flex payments",
            "  3. Scan payments (apply to the invoices uploaded in step 1)",
        ]
    else:
        parts += ["  1. Flex payments (all rows, Maintenance-only remittance)"]
    parts += [
        "",
        "After upload:",
        "  - Confirm no failed rows in SaasAnt.",
        "  - Verify the deposit shows on the correct bank feed.",
        "",
        "Reply with any issues.",
    ]
    return subj, "\n".join(parts)


def credit_memos_email(*, year: int, month: int, count: int, total: float,
                       start_ref: int, next_ref: int) -> tuple[str, str]:
    mname = dt.date(year, month, 1).strftime("%B")
    subj = f"[Action Required] FLEX Credit Memos — {mname} {year} ({count} memos)"
    body = "\n".join([
        "Hi accounting,",
        "",
        f"Generated the FLEX credit-memo batch for {mname} {year}.",
        "",
        "Summary:",
        f"  - Credit memos: {count}",
        f"  - Total: ${total:,.2f}",
        f"  - Credit Memo No range: {start_ref}–{next_ref - 1}",
        "",
        "File attached for SaasAnt upload (Credit Memo import).",
        "",
        "After upload, confirm the credit memos appear on each clinic's QBO account",
        "and that the totals reconcile against the finance-co payments imported earlier.",
    ])
    return subj, body


def recapture_email(*, year: int, month: int,
                    unused_count: int, unused_total: float,
                    direct_count: int, direct_total: float,
                    partner_count: int, partner_total: float,
                    cutoff_date) -> tuple[str, str]:
    """Quarter-end recapture handoff. Listed verbatim so accounting can work down it."""
    month_name = dt.date(year, month, 1).strftime("%B")
    next_month = dt.date(cutoff_date.year, cutoff_date.month, 1).strftime("%B %Y")
    subj = f"[Action Required] FLEX Quarter-End Recapture — {month_name} {year}"
    parts = [
        "Hi accounting,",
        "",
        f"Quarter-end FLEX recapture for {month_name} {year}. Three files may be attached:",
        "",
    ]
    if unused_count:
        parts.append(f"  A. Unused-credit invoices: {unused_count}  (${unused_total:,.2f})")
    if direct_count:
        parts.append(f"  B. Direct-bill overage invoices: {direct_count}  (${direct_total:,.2f})")
    if partner_count:
        parts.append(f"  C. OnePlace partner submission: {partner_count}  (${partner_total:,.2f})")
    parts += [
        "",
        "Work order (SOP-11 / SOP-12):",
        "  1. Upload unused-credit invoices to SaasAnt — these are INTERNAL only (do NOT mail).",
        "  2. Upload direct-bill overage invoices to SaasAnt → send pay links → VOID after sending (SOP-6).",
        f"  3. Submit OnePlace overage list before {cutoff_date:%B %d, %Y}.",
        "  4. After all three are in QBO, run reconciliation: un-apply auto-matches, "
        "re-apply payment→credit→payment→credit against the quarter's scan invoices.",
        "",
        "No refunds on FLEX overpayments (SOP-12) — overpayment stays as credit for future overages.",
    ]
    return subj, "\n".join(parts)


def direct_bill_overage_email(*, year: int, month: int,
                              invoice_count: int, invoice_total: float,
                              clinic_details: list[dict] | None = None,  # noqa: ARG001
                              ) -> tuple[str, str]:
    """FLEX direct-bill overage handoff to accounting (Tanya).

    Minimal body — just the headline summary; all per-clinic detail lives in
    the attached worksheet (threshold / activity / credit / amount to bill /
    escalation flag). Outlook adds the signature on open.

    `clinic_details` is accepted for back-compat with existing call sites but
    is no longer rendered into the body. Safe to drop from new callers.
    """
    month_name = dt.date(year, month, 1).strftime("%B")
    subj = f"[Action Required] FLEX Direct-Bill Overage — {month_name} {year}"
    parts = [
        "Hi Tanya,",
        "",
        f"Please see the attached direct-bill overage billing file for "
        f"{month_name} {year} — {invoice_count} clinic(s) to bill, "
        f"totaling ${invoice_total:,.2f}.",
        "File attached (xlsx — your working reference for manual QBO entry).",
    ]
    return subj, "\n".join(parts)


def partner_submission_email(*, year: int, month: int,
                             clinic_count: int, total: float,
                             cutoff_date,
                             clinic_details: list[dict] | None = None
                             ) -> tuple[str, str]:
    """OnePlace partner-overage submission handoff. Tanya forwards the file to
    OnePlace before the cutoff. `clinic_details` is the rendered partner
    submission as a list of dicts (output of
    flex_overage.build_partner_submission().to_dict('records')); used to
    render the inline per-clinic table.
    """
    month_name = dt.date(year, month, 1).strftime("%B")
    subj = f"[Action Required] OnePlace Partner Submission — {month_name} {year}"
    parts = [
        "Hi Tanya,",
        "",
        f"OnePlace partner-overage submission for {month_name} {year} — "
        f"{clinic_count} clinic(s) totaling ${total:,.2f}.",
        "File attached.",
    ]

    if clinic_details:
        parts += ["", "Per-clinic breakdown:"]
        for d in clinic_details:
            parts += [
                "",
                f"  - {d.get('Clinic') or d.get('QB Customer')}"
                f"   (Contract {d.get('Contract ID') or '—'})",
                f"      Gross overage: ${float(d.get('Gross Overage') or 0):,.2f}"
                f"   |   Credit applied: ${float(d.get('Credit Applied') or 0):,.2f}"
                f"   |   NET TO SUBMIT: ${float(d.get('Net Overage to Submit') or 0):,.2f}",
            ]

    parts += [
        "",
        f"Work order — must reach OnePlace BEFORE {cutoff_date:%B %d, %Y}:",
        "  1. Forward the attached file to OnePlace (their submissions inbox).",
        "  2. Confirm OnePlace receipt — keep their reply for the audit trail.",
        "  3. Track expected payment ~5–6 months out on the FLEX Master.",
        "",
        "If the cutoff is missed, OnePlace won't process these and we have to",
        "direct-bill the clinics ourselves. The app will catch that on the next",
        "Stage 3 run and route a separate direct-bill email if needed.",
        "",
        "Please reply if any adjustments need to be made.",
    ]
    return subj, "\n".join(parts)


def rebate_email(*, period_label: str, per_bucket_totals: dict,
                 grand_total: float) -> tuple[str, str]:
    subj = f"[Action Required] Rebate Period Report — {period_label}"
    parts = [
        "Hi accounting,",
        "",
        f"Rebate report for {period_label}. Multi-tab xlsx attached.",
        "",
        "Per-bucket totals:",
    ]
    for k, v in per_bucket_totals.items():
        parts.append(f"  - {k}: ${v:,.2f}")
    parts += [
        f"  - Grand total: ${grand_total:,.2f}",
        "",
        "Each clinic tab in the workbook lists the eligible consults, the applied rate, ",
        "and the remittance amount. Review the variance column for any deltas worth investigating ",
        "before sending checks.",
    ]
    return subj, "\n".join(parts)
