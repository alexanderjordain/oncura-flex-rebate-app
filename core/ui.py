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

/* Buttons — dark blue solid w/ bold white lettering. Scoped to top-level Streamlit
   button containers so the file uploader's internal "Browse files" button stays neutral. */
.stButton > button,
.stDownloadButton > button,
.stLinkButton > a,
.stLinkButton > a:visited {
  background:#1F3D5C !important;
  color:#FFFFFF !important;
  border:1px solid #1F3D5C !important;
  font-family:var(--sans) !important;
  font-weight:700 !important;
  border-radius:6px;
  text-decoration:none !important;
  transition:transform .08s ease, box-shadow .15s ease, background .15s ease;
}
.stButton > button:hover,
.stDownloadButton > button:hover,
.stLinkButton > a:hover {
  background:#152C47 !important;
  border-color:#152C47 !important;
  color:#FFFFFF !important;
  transform:translateY(-1px);
  box-shadow:0 4px 14px rgba(31,61,92,.30);
}
.stButton > button:disabled,
.stDownloadButton > button:disabled {
  background:#9CA3AF !important; border-color:#9CA3AF !important;
  color:#FFFFFF !important; cursor:not-allowed;
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
    return False


def sidebar_brand():
    placed = set_logo()
    if not placed and os.path.exists(LOGO_PATH):
        st.sidebar.image(LOGO_PATH, use_container_width=True)
    st.sidebar.markdown(
        '<div class="oncura-mark-sub">Flex &middot; Rebate Ledger</div>'
        '<div class="oncura-rule"></div>',
        unsafe_allow_html=True,
    )
