"""Shared visual identity — matched to the Oncura Imaging Analytics Dashboard palette.

Steel blue (primary) + leaf green (data/positive) + amber (accent) on a cool grey canvas,
white cards, dark-slate text. Distinctive typographic system kept: Fraunces display serif,
Hanken Grotesk body, IBM Plex Mono (tabular) for every financial figure. Applied via injected
CSS since Streamlit does not expose arbitrary markup styling.

Call inject() once per page (after auth), then header(...) instead of st.title/st.caption.
"""
from __future__ import annotations

import os

import streamlit as st

LOGO_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "assets", "oncura_logo.png"))

PALETTE = {
    "canvas": "#F0F2F4",
    "surface": "#FFFFFF",
    "ink": "#2A3742",
    "blue": "#3A6A9A",
    "blue_deep": "#2F567E",
    "green": "#469B68",
    "amber": "#E3A033",
    "muted": "#6B7785",
    "line": "#E2E6EA",
}

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&family=Hanken+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

:root {
  --canvas:#F0F2F4; --surface:#FFFFFF; --ink:#2A3742; --blue:#3A6A9A;
  --blue-deep:#2F567E; --green:#469B68; --amber:#E3A033; --muted:#6B7785; --line:#E2E6EA;
  --serif:'Fraunces',Georgia,serif; --sans:'Hanken Grotesk',-apple-system,sans-serif;
  --mono:'IBM Plex Mono',ui-monospace,monospace;
}

/* cool grey canvas with a faint blue/green mesh for depth */
.stApp {
  background:
    radial-gradient(900px 520px at 90% -10%, rgba(58,106,154,.06), transparent 60%),
    radial-gradient(720px 480px at -6% 6%, rgba(70,155,104,.05), transparent 55%),
    var(--canvas);
}
html, body, [class*="css"], .stApp, p, li, label, .stMarkdown { font-family: var(--sans); color: var(--ink); }

/* headings: serif, brand blue (echoes the dashboard section titles) */
h1, h2, h3, h4 { font-family: var(--serif) !important; color: var(--blue) !important; letter-spacing:-.01em; font-weight:600; }

/* page header block: amber kicker, green accent bar, blue serif title */
.oncura-head { margin:.2rem 0 1.4rem 0; padding:.1rem 0 1rem 1rem; border-bottom:1px solid var(--line); border-left:4px solid var(--green); }
.oncura-head .kicker { font-family:var(--mono); text-transform:uppercase; letter-spacing:.28em; font-size:.7rem; color:var(--amber); margin-bottom:.5rem; }
.oncura-head h1 { font-size:2.4rem; line-height:1.05; margin:0; color:var(--blue) !important; }
.oncura-head .sub { font-family:var(--sans); color:var(--muted); font-size:1rem; margin:.5rem 0 0 0; max-width:62ch; }

/* metrics -> white KPI cards with a blue left rule, figures in tabular mono */
[data-testid="stMetric"] {
  background:var(--surface); border:1px solid var(--line);
  border-left:3px solid var(--blue); border-radius:6px;
  padding:.85rem 1rem; box-shadow:0 1px 3px rgba(42,55,66,.05);
}
[data-testid="stMetricLabel"] p {
  font-family:var(--mono) !important; text-transform:uppercase;
  letter-spacing:.12em; font-size:.66rem !important; color:var(--muted) !important;
}
[data-testid="stMetricValue"] {
  font-family:var(--mono) !important; font-weight:600;
  font-variant-numeric:tabular-nums; color:var(--blue) !important; letter-spacing:-.01em;
}

/* Buttons — outlined by default (secondary). Primary buttons are filled steel blue
   so commit / "Mark as imported" actions visually pop. */
.stButton > button,
.stDownloadButton > button,
.stLinkButton > a,
.stLinkButton > a:visited {
  background:#FFFFFF !important;
  color:#1F3D5C !important;
  border:1.5px solid #1F3D5C !important;
  font-family:var(--sans) !important;
  font-weight:700 !important;
  border-radius:6px;
  text-decoration:none !important;
  transition:transform .08s ease, box-shadow .15s ease, background .15s ease;
}
.stButton > button:hover,
.stDownloadButton > button:hover,
.stLinkButton > a:hover {
  background:#EAF2FA !important;
  border-color:#1F3D5C !important;
  color:#1F3D5C !important;
  transform:translateY(-1px);
  box-shadow:0 4px 14px rgba(31,61,92,.18);
}
.stButton > button:disabled,
.stDownloadButton > button:disabled {
  background:#F3F4F6 !important; border-color:#D1D5DB !important;
  color:#9CA3AF !important; cursor:not-allowed;
}
/* PRIMARY buttons (type="primary") — same white-outlined style as default buttons,
   per Alex 2026-06-01: no filled-blue commit buttons, everything stays white. */
.stButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"],
.stFormSubmitButton > button[kind="primary"] {
  background:#FFFFFF !important;
  color:#1F3D5C !important;
  border:1.5px solid #1F3D5C !important;
  font-weight:700 !important;
  box-shadow:none !important;
}
.stButton > button[kind="primary"]:hover,
.stDownloadButton > button[kind="primary"]:hover,
.stFormSubmitButton > button[kind="primary"]:hover {
  background:#EAF2FA !important;
  color:#1F3D5C !important;
  border-color:#1F3D5C !important;
  transform:translateY(-1px);
  box-shadow:0 4px 14px rgba(31,61,92,.18) !important;
}
.stButton > button[kind="primary"]:disabled,
.stDownloadButton > button[kind="primary"]:disabled {
  background:#F3F4F6 !important;
  border-color:#D1D5DB !important;
  color:#9CA3AF !important;
  box-shadow:none !important;
}
/* Restore the file uploader's internal browse button to a neutral look */
[data-testid="stFileUploader"] button {
  background: var(--surface) !important;
  color: var(--ink) !important;
  border: 1px solid var(--line) !important;
  font-weight: 500 !important;
}
[data-testid="stFileUploader"] button:hover {
  background: #F3F4F6 !important;
  color: var(--ink) !important;
  border-color: var(--blue) !important;
}

/* sidebar (no * selector — it breaks Material icon fonts) */
section[data-testid="stSidebar"] { background:var(--surface); border-right:1px solid var(--line); }
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] a,
section[data-testid="stSidebar"] .stMarkdown { font-family:var(--sans); }

/* protect Material Symbols icons from font overrides */
[data-testid="stIconMaterial"], span.material-icons, span.material-symbols-rounded {
  font-family:'Material Symbols Rounded','Material Symbols Outlined','Material Icons' !important;
}

/* tabular figures in tables + code */
[data-testid="stDataFrame"], [data-testid="stTable"], code, pre, .stCode { font-family:var(--mono) !important; }
[data-testid="stDataFrame"] { font-variant-numeric:tabular-nums; }

/* links + callouts */
a, a:visited { color:var(--blue-deep); text-decoration-color:var(--amber); }
[data-testid="stExpander"] { border:1px solid var(--line); border-radius:6px; background:var(--surface); }

/* ── Sidebar custom navigation (rendered by app.py's hand-rolled nav block) ─── */

/* Flatten the expanders that wrap each section group — no border, no fill,
   no chunky padding. They should read as plain nav section headers. */
.oncura-nav [data-testid="stExpander"],
section[data-testid="stSidebar"] [data-testid="stExpander"] {
  border: none !important;
  background: transparent !important;
  box-shadow: none !important;
  margin: 0 !important;
  padding: 0 !important;
}
.oncura-nav [data-testid="stExpander"] > details,
section[data-testid="stSidebar"] [data-testid="stExpander"] > details {
  border: none !important;
  background: transparent !important;
}
/* Section header (summary): looks like a nav row, hover highlight, blue when open. */
.oncura-nav [data-testid="stExpander"] summary,
section[data-testid="stSidebar"] [data-testid="stExpander"] summary {
  padding: .45rem .6rem !important;
  border-radius: 8px !important;
  font-family: var(--sans) !important;
  font-weight: 600 !important;
  font-size: .92rem !important;
  letter-spacing: .01em !important;
  color: var(--ink) !important;
  list-style: none !important;
  cursor: pointer !important;
  transition: background .12s ease, color .12s ease !important;
}
.oncura-nav [data-testid="stExpander"] summary p,
section[data-testid="stSidebar"] [data-testid="stExpander"] summary p {
  font-weight: 600 !important;
  color: inherit !important;
  margin: 0 !important;
}
.oncura-nav [data-testid="stExpander"] summary:hover,
section[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {
  background: rgba(58,106,154,.07) !important;
  color: var(--blue) !important;
}
.oncura-nav [data-testid="stExpander"] details[open] > summary,
section[data-testid="stSidebar"] [data-testid="stExpander"] details[open] > summary {
  color: var(--blue) !important;
}
/* Indent the children of an open section so the hierarchy reads at a glance. */
.oncura-nav [data-testid="stExpander"] [data-testid="stExpanderDetails"],
section[data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderDetails"] {
  padding: .15rem 0 .25rem .65rem !important;
  border-left: 2px solid rgba(58,106,154,.15) !important;
  margin-left: .9rem !important;
}

/* Page-link rows (Home + child links inside each section): tighter padding,
   subtle hover, blue + light fill when active. */
section[data-testid="stSidebar"] [data-testid="stPageLink"] a,
section[data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"] {
  padding: .4rem .6rem !important;
  border-radius: 8px !important;
  font-weight: 500 !important;
  color: var(--ink) !important;
  font-size: .92rem !important;
  transition: background .12s ease, color .12s ease !important;
}
section[data-testid="stSidebar"] [data-testid="stPageLink"] a:hover,
section[data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"]:hover {
  background: rgba(58,106,154,.07) !important;
  color: var(--blue) !important;
}
section[data-testid="stSidebar"] [data-testid="stPageLink"] a[aria-current="page"],
section[data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"][aria-current="page"] {
  background: rgba(58,106,154,.12) !important;
  color: var(--blue) !important;
  font-weight: 600 !important;
}

/* Divider above the role/logout footer */
.oncura-sidebar-footer {
  border-top: 1px solid var(--line);
  margin: 1.25rem 0 .6rem 0;
}

/* top header bar -> white with a hairline (echoes the dashboard's white header) */
[data-testid="stHeader"] { background: var(--surface) !important; border-bottom:1px solid var(--line); }

/* breathing room between st.tabs labels */
[data-baseweb="tab-list"] { gap: 2.25rem; }
button[data-baseweb="tab"] { padding-left: .25rem; padding-right: .25rem; }
[data-testid="stDecoration"] { display:none; }
footer { visibility:hidden; }

/* sidebar wordmark */
.oncura-mark { font-family:var(--serif); font-weight:700; font-size:1.35rem; color:var(--blue); letter-spacing:-.02em; line-height:1; }
.oncura-mark .dot { color:var(--green); }
.oncura-mark-sub { font-family:var(--mono); text-transform:uppercase; letter-spacing:.2em; font-size:.6rem; color:var(--muted); margin-top:.3rem; }
.oncura-rule { height:1px; background:var(--line); margin:.7rem 0 1rem 0; }

/* Mark / Record buttons — light green tint to signal 'commit to the
 * audit + dedup ledger'. The button rendered by ui.record_button()
 * places an invisible sentinel <div class="oncura-record-btn-anchor">
 * immediately before itself; this :has() + sibling selector targets
 * the next element-container's button without touching any other
 * primary button in the app. */
.element-container:has(.oncura-record-btn-anchor) + .element-container button[kind] {
  background: #DFF5E1 !important;
  color: #1B6E3A !important;
  border: 1px solid #82C18C !important;
}
.element-container:has(.oncura-record-btn-anchor) + .element-container button[kind]:hover:not(:disabled) {
  background: #C6EFCE !important;
  border-color: #1B6E3A !important;
}
.element-container:has(.oncura-record-btn-anchor) + .element-container button[kind]:disabled {
  background: #F2F8F3 !important;
  color: #82C18C !important;
  border-color: #C6E8C9 !important;
  opacity: 0.7;
}
.oncura-record-btn-anchor { display: none; }
</style>
"""


def inject():
    st.markdown(_CSS, unsafe_allow_html=True)


def header(title: str, subtitle: str = "", kicker: str = "FLEX · REBATE LEDGER"):
    sub = f'<p class="sub">{subtitle}</p>' if subtitle else ""
    st.markdown(
        f'<div class="oncura-head"><div class="kicker">{kicker}</div>'
        f"<h1>{title}</h1>{sub}</div>",
        unsafe_allow_html=True,
    )


def set_logo():
    """Pin the Oncura logo to the top-left (top of sidebar + collapsed-state corner)."""
    if os.path.exists(LOGO_PATH) and hasattr(st, "logo"):
        try:
            st.logo(LOGO_PATH, size="large")
            return True
        except Exception:
            pass


def persistence_warning() -> None:
    """Render a warning ABOVE a record button if no GitHub token is configured.

    Without a token, ``store.save_json`` falls back to the local filesystem.
    On Streamlit Cloud that means the audit manifest + dedup ledger live only
    in the current session — closing the browser tab loses the dedup state,
    which can silently allow double-posting to QBO on a re-run.

    The warning surfaces this BEFORE the operator clicks the record button so
    they can abort and add ``GITHUB_TOKEN`` to Cloud Secrets. (Without this
    pre-check, the warning only appears AFTER the click, by which time the
    operator believes they're committed and may close the tab.)
    """
    from . import store  # local import to avoid circular dep at module load
    if store._github_token():
        return
    st.warning(
        ":material/warning: **No `GITHUB_TOKEN` configured** — the dedup "
        "ledger + audit manifest will be saved to the local filesystem only. "
        "On Streamlit Cloud this means closing the browser tab loses the "
        "ledger, and a re-run could double-post to QBO. Add `GITHUB_TOKEN` "
        "in App → Settings → Secrets before committing this cycle.",
        icon=":material/warning:",
    )


def record_button(label: str, *, key: str, disabled: bool = False,
                  use_container_width: bool = False, help: str | None = None) -> bool:
    """Render a 'commit-to-the-ledger' button with a light green tint.

    These buttons (Mark / Record …) are the load-bearing audit-manifest writes.
    They get a visually distinct soft-green styling so an operator scanning the
    page can find them at a glance even when the surrounding flow has lots of
    other widgets.

    The visual tint is applied by CSS in inject(): a sentinel
    ``<div class="oncura-record-btn-anchor">`` is rendered immediately before
    the button so the `:has()` + sibling selector can target this specific
    button without touching any other primary button in the app.
    """
    st.markdown('<div class="oncura-record-btn-anchor"></div>',
                unsafe_allow_html=True)
    return st.button(
        label, key=key, disabled=disabled,
        use_container_width=use_container_width, help=help,
    )


def initials_input(audit_key: str, *, disabled: bool = False) -> str:
    """Render an 'Initials sign-off card' and return the cleaned initials.

    Visually mirrors the bordered sign-off block used elsewhere on the wizard:
    a colored status header (red 'INITIALS REQUIRED' / green 'Initials
    captured'), a caption, and the text input itself — all grouped inside a
    single ``st.container(border=True)`` so it reads as one sign-off unit.

    The audit manifest works like a paper sign-off sheet — each cycle should be
    initialed by the operator who ran it. This widget collects the initials
    once per session (persisted under ``SS['user_initials']``) and auto-fills
    on subsequent cycles.

    Returns the uppercase initials, or ``""`` if none provided. Callers gate
    their record button on truthiness::

        initials = ui.initials_input("stage1_audit_initials")
        if st.button("Mark...", disabled=not initials, ...):
            audit.record_cycle(approver=initials or auth.current_role(), ...)
    """
    # Live state: SS[audit_key] holds the widget's current value (Streamlit
    # updates it BEFORE the rerun script body runs), so the header reflects
    # the click that just happened — no off-by-one.
    live_val = (
        st.session_state.get(audit_key)
        or st.session_state.get("user_initials", "")
        or ""
    ).strip().upper()

    with st.container(border=True):
        if live_val:
            st.markdown(
                f"##### :green[:material/check_circle:&nbsp; Initials captured: {live_val}]"
            )
            st.caption(
                "The **record button** below is now enabled. Initials persist for "
                "the session so subsequent cycles auto-fill."
            )
        else:
            st.markdown(
                "##### :red[:material/priority_high:&nbsp; INITIALS REQUIRED]"
            )
            st.caption(
                "Enter your initials below to enable the **record button** — "
                "like initialing a paper sign-off sheet. Persists for the session."
            )
        val = st.text_input(
            "Your initials (for the audit log)",
            value=st.session_state.get("user_initials", ""),
            max_chars=4,
            key=audit_key,
            placeholder="e.g. AJ",
            help="Recorded as the approver on the audit manifest. "
                 "Persists across cycles in this session.",
            disabled=disabled,
            label_visibility="collapsed",
        )
    cleaned = (val or "").strip().upper()
    if cleaned:
        st.session_state["user_initials"] = cleaned
    return cleaned


def scroll_top_on_step_change(wizard_key: str, current_step) -> None:
    """Scroll the page to the top whenever a wizard step changes.

    Tracks the previous step under ``__scroll_prev_<wizard_key>`` in session_state
    and compares to ``current_step`` on each render. When the values differ, injects
    a 0-height ``components.html`` block whose script targets multiple candidate
    scrollable containers in the parent document — Streamlit's actual scroll
    surface varies by version (window vs main section vs block container), and
    the browser's own scroll restoration tries to put the user back where they
    were on a rerun. We disable that and scroll repeatedly with small delays so
    the scroll lands AFTER the new DOM has rendered.

    Call this at the top of any wizard page or stage block, AFTER the step value
    has been read from session_state, e.g.::

        ui.scroll_top_on_step_change("rebate_cycle", SS.cycle_step)

    On the first render of a session there is no prior step recorded, so no
    scroll happens — the page is already at top.
    """
    import streamlit.components.v1 as components

    prev_key = f"__scroll_prev_{wizard_key}"
    prev = st.session_state.get(prev_key)
    st.session_state[prev_key] = current_step
    if prev is None or prev == current_step:
        return
    components.html(
        """
        <script>
        (function() {
            const targetWindow = (window.parent && window.parent !== window) ? window.parent : window.top;
            if (!targetWindow) return;
            // Stop the browser from restoring the prior scroll position on rerun.
            try {
                if (targetWindow.history && 'scrollRestoration' in targetWindow.history) {
                    targetWindow.history.scrollRestoration = 'manual';
                }
            } catch (e) {}
            const SELECTORS = [
                'section[data-testid="stMain"]',
                'div[data-testid="stAppViewContainer"]',
                'div[data-testid="stAppViewBlockContainer"]',
                'section.main',
                'div.main',
                'div.block-container',
                'main',
            ];
            const doScroll = function() {
                try {
                    // Window-level scroll covers cases where the body is the scroll surface.
                    targetWindow.scrollTo({top: 0, left: 0, behavior: 'auto'});
                    const doc = targetWindow.document;
                    if (doc && doc.documentElement) doc.documentElement.scrollTop = 0;
                    if (doc && doc.body) doc.body.scrollTop = 0;
                    // Streamlit's main scroll surface is usually an inner container.
                    for (let i = 0; i < SELECTORS.length; i++) {
                        const el = doc.querySelector(SELECTORS[i]);
                        if (el) {
                            if (typeof el.scrollTo === 'function') {
                                el.scrollTo({top: 0, left: 0, behavior: 'auto'});
                            }
                            el.scrollTop = 0;
                        }
                    }
                } catch (e) {}
            };
            // Fire now AND on a few delays so we catch the DOM after Streamlit's
            // re-render has settled. Cheap; total cost < 1s of harmless scroll calls.
            doScroll();
            setTimeout(doScroll, 50);
            setTimeout(doScroll, 150);
            setTimeout(doScroll, 350);
        })();
        </script>
        """,
        height=0,
    )
    return False


def sidebar_brand():
    placed = set_logo()
    if not placed and os.path.exists(LOGO_PATH):
        st.sidebar.image(LOGO_PATH, use_container_width=True)
    st.sidebar.markdown(
        '<div class="oncura-mark-sub">Pass-Through &middot; Rebate Ledger</div>'
        '<div class="oncura-rule"></div>',
        unsafe_allow_html=True,
    )
