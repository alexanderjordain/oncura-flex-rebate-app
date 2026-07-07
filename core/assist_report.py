"""Sonographer "Finalized Assistance" report — weekly + daily assist counts from OPD.

Pulls finalized Consults from the live OPD OData feed, groups by the assisting
sonographer (Consult.AssistedBy), and renders the HTML tables emailed to the team.
Counts are bucketed by FinalizedDate converted to US Eastern. Backs the
"Open assistance email" button on the Settings page (admin-only).

Nothing here writes to OPD or QBO — it's a read-only pull plus an email body.
"""
from __future__ import annotations

import collections
import datetime as dt
import html as _html

import streamlit as st

from . import opd_api

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - tzdata missing
    _ET = None

# ── Report configuration ──────────────────────────────────────────────────────
# The 11 tracked sonographers, in report-column order.
SONOGRAPHERS = [
    "Becky Tiner", "Chelsea Parsons", "Denice Rodriguez", "Elyce Thomas",
    "Francisco Zuniga", "Katie Heuer", "Lanis Davis", "Liza Gonzalez",
    "Luis Romero", "Lyannette Curiel", "Megan DuCasse",
]
_SSET = {s.lower(): s for s in SONOGRAPHERS}

# Recipients (as "Name <email>"). Note: Elyce Thomas is a report column but not
# on the distribution list — mirrors the current process.
TO_RECIPIENTS = [
    ("Melissa Colpitts", "mcolpitts@oncurapartners.com"),
    ("Sandra Paris", "sparis@oncurapartners.com"),
    ("Becky Tiner", "btiner@oncurapartners.com"),
    ("Chelsea Parsons", "cparsons@oncurapartners.com"),
    ("Denice Rodriguez", "DeniceRodriguez@oncurapartners.com"),
    ("Francisco Zuniga", "francisco@oncurapartners.com"),
    ("Katie Heuer", "kheuer@oncurapartners.com"),
    ("Lanis Davis", "ldavis@oncurapartners.com"),
    ("Liza Gonzalez", "lgonzalez@oncurapartners.com"),
    ("Luis Romero", "lromero@oncurapartners.com"),
    ("Lyannette Curiel", "lyannette@oncurapartners.com"),
    ("Megan DuCasse", "mducasse@oncurapartners.com"),
]
CC_RECIPIENTS = [
    ("Marty McCutchen", "marty@oncurapartners.com"),
    ("Tanya White", "tanya@oncurapartners.com"),
    ("Craig Presnall", "craig@oncurapartners.com"),
]
SUBJECT = "Re: Weekly Assistance Update"
WEEKLY_GOAL = 50   # per week
DAILY_GOAL = 10    # per day
# Trailing windows shown in the email (both end at the last COMPLETE period).
WEEKLY_WEEKS = 15  # last N complete Mon-Sun weeks
DAILY_DAYS = 15    # last N complete days


def recipients(kind: str = "to") -> list[str]:
    """Recipient strings ('Name <email>') for the To (default) or Cc list."""
    src = TO_RECIPIENTS if kind == "to" else CC_RECIPIENTS
    return [f"{n} <{e}>" for n, e in src]


def _mdY(d: dt.date) -> str:
    return f"{d.month}/{d.day}/{d.year}"


def _eastern_date(iso_utc: str) -> dt.date:
    """FinalizedDate (UTC 'Z' string) -> US Eastern calendar date."""
    d = dt.datetime.strptime(iso_utc[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    if _ET is not None:
        return d.astimezone(_ET).date()
    # Manual US-Eastern DST fallback: EDT (-4) 2nd Sun Mar .. 1st Sun Nov, else EST (-5).
    y = d.year
    mar = dt.date(y, 3, 1); dst0 = mar + dt.timedelta(days=(6 - mar.weekday()) % 7 + 7)
    nov = dt.date(y, 11, 1); dst1 = nov + dt.timedelta(days=(6 - nov.weekday()) % 7)
    off = -4 if dst0 <= d.date() < dst1 else -5
    return (d + dt.timedelta(hours=off)).date()


def eastern_today() -> dt.date:
    now = dt.datetime.now(dt.timezone.utc)
    return now.astimezone(_ET).date() if _ET is not None else _eastern_date(now.isoformat())


def _month_chunks(start: dt.date, end_exclusive: dt.date):
    """Non-overlapping [a, b) month-aligned chunks so no consult is double-counted."""
    out, cur = [], start
    while cur < end_exclusive:
        nxt = dt.date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
        out.append((cur, min(nxt, end_exclusive)))
        cur = nxt
    return out


@st.cache_data(ttl=1800, show_spinner="Pulling assist activity from OPD…")
def build_counts(pull_start_iso: str, pull_end_iso: str) -> dict:
    """Tally finalized-consult assist counts by Eastern FinalizedDate over
    [pull_start, pull_end). Returns
    {'weekly': {monday_iso: {sonographer: n}}, 'daily': {day_iso: {sonographer: n}}}.
    Cached 30 min, keyed by the date range."""
    auth = opd_api.auth_from_secrets()
    start = dt.date.fromisoformat(pull_start_iso)
    end_excl = dt.date.fromisoformat(pull_end_iso)
    weekly = collections.defaultdict(collections.Counter)
    daily = collections.defaultdict(collections.Counter)
    for a, b in _month_chunks(start, end_excl):
        flt = (f"FinalizedDate ge datetime'{a.isoformat()}T00:00:00' "
               f"and FinalizedDate lt datetime'{b.isoformat()}T00:00:00'")
        rows, _ = opd_api._fetch_all(f"{opd_api.BASE_URL}/Consult", auth=auth,
                                     params={"$filter": flt})
        for r in rows:
            son = _SSET.get((r.get("AssistedBy") or "").strip().lower())
            fd = r.get("FinalizedDate")
            if not (son and fd):
                continue
            d = _eastern_date(fd)
            mon = d - dt.timedelta(days=d.weekday())
            weekly[mon.isoformat()][son] += 1
            daily[d.isoformat()][son] += 1
    return {"weekly": {k: dict(v) for k, v in weekly.items()},
            "daily": {k: dict(v) for k, v in daily.items()}}


def _rows(counts: dict, today: dt.date):
    """(weekly_rows, daily_rows) as [(label, {son: n}), ...] — the trailing
    WEEKLY_WEEKS complete weeks and DAILY_DAYS complete days ending `today`."""
    weekly, daily = counts["weekly"], counts["daily"]
    this_monday = today - dt.timedelta(days=today.weekday())
    last_week_monday = this_monday - dt.timedelta(days=7)   # last complete week
    wk = []
    for i in range(WEEKLY_WEEKS - 1, -1, -1):
        m = last_week_monday - dt.timedelta(days=7 * i)
        wk.append((f"WO: {_mdY(m)}", weekly.get(m.isoformat(), {})))
    last_day = today - dt.timedelta(days=1)                 # last complete day
    dy = []
    for i in range(DAILY_DAYS - 1, -1, -1):
        d = last_day - dt.timedelta(days=i)
        dy.append((_mdY(d), daily.get(d.isoformat(), {})))
    return wk, dy


# Dark-theme palette (inline styles + bgcolor attrs for Outlook's Word engine):
# charcoal body + light text, teal header, olive goal-met highlight.
_DARK_BG = "#1f1f1f"
_LIGHT_TX = "#f0f0f0"
_TEAL_BG = "#5f93a3"
_TEAL_TX = "#0e2a33"
_OLIVE_BG = "#74771e"
_OLIVE_TX = "#141400"
_BORDER = "#333333"
_FONT = "font-family:Calibri,Arial,sans-serif"


def _cell(content, bg: str, tx: str, align: str = "center", bold: bool = False) -> str:
    return (f'<td bgcolor="{bg}" style="background:{bg};color:{tx};'
            f'font-weight:{"700" if bold else "400"};padding:6px 11px;text-align:{align};'
            f'white-space:nowrap;border:1px solid {_BORDER};{_FONT};font-size:11px">{content}</td>')


def _bar(text: str, size: int, ncol: int) -> str:
    return (f'<tr><td colspan="{ncol}" bgcolor="{_DARK_BG}" style="background:{_DARK_BG};'
            f'color:#ffffff;font-weight:700;text-align:center;padding:7px 11px;'
            f'border:1px solid {_BORDER};{_FONT};font-size:{size}px">{text}</td></tr>')


def _table_html(subtitle: str, rows, goal: int | None = None) -> str:
    ncol = len(SONOGRAPHERS) + 1
    header = ("<tr>" + _cell("Assist Count", _TEAL_BG, _TEAL_TX, "left", True)
              + "".join(_cell(_html.escape(s), _TEAL_BG, _TEAL_TX, "center", True) for s in SONOGRAPHERS)
              + "</tr>")
    body = ""
    for label, counts in rows:
        cells = ""
        for s in SONOGRAPHERS:
            v = counts.get(s)
            if goal is not None and isinstance(v, int) and v >= goal:
                cells += _cell(v, _OLIVE_BG, _OLIVE_TX, "center", True)
            else:
                cells += _cell(v or "", _DARK_BG, _LIGHT_TX)
        body += "<tr>" + _cell(_html.escape(label), _DARK_BG, _LIGHT_TX, "left", True) + cells + "</tr>"
    return (
        '<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
        f'{_FONT};font-size:11px;margin:16px 0 0">'
        f'{_bar("Finalized Assistance", 15, ncol)}'
        f'{_bar(_html.escape(subtitle), 12, ncol)}'
        f'{header}{body}</table>'
    )


def build_email(today: dt.date | None = None) -> tuple[str, str, str]:
    """Pull the data and render the email. Returns (subject, plain_body, html_body)."""
    if today is None:
        today = eastern_today()
    this_monday = today - dt.timedelta(days=today.weekday())
    last_week_monday = this_monday - dt.timedelta(days=7)
    weekly_start = last_week_monday - dt.timedelta(days=7 * (WEEKLY_WEEKS - 1))
    daily_start = (today - dt.timedelta(days=1)) - dt.timedelta(days=DAILY_DAYS - 1)
    pull_start = min(weekly_start, daily_start)
    counts = build_counts(pull_start.isoformat(), (today + dt.timedelta(days=1)).isoformat())
    wk_rows, dy_rows = _rows(counts, today)
    html = (
        "<div style='font-family:Calibri,Arial,sans-serif;font-size:14px;color:#1f2733'>"
        "<p>Hello all,</p>"
        "<p>Please see the following assisting sonographer activity reports.</p>"
        f"{_table_html(f'Weekly (Goal: {WEEKLY_GOAL}/week)', wk_rows, goal=WEEKLY_GOAL)}<br>"
        f"{_table_html(f'Daily (Goal: {DAILY_GOAL}/day)', dy_rows, goal=DAILY_GOAL)}"
        "</div>"
    )
    plain = (
        "Hello all,\n\n"
        "Please see the following assisting sonographer activity reports "
        "(formatted tables in the HTML version of this email)."
    )
    return SUBJECT, plain, html
