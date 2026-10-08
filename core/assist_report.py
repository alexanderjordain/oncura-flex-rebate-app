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
import statistics

import streamlit as st

from . import opd_api

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - tzdata missing
    _ET = None

# ── Report configuration ──────────────────────────────────────────────────────
# The 10 tracked sonographers, in report-column order.
SONOGRAPHERS = [
    "Becky Tiner", "Chelsea Parsons", "Denice Rodriguez", "Elyce Thomas",
    "Francisco Zuniga", "Katie Heuer", "Lanis Davis",
    "Luis Romero", "Lyannette Curiel", "Megan DuCasse",
]
_SSET = {s.lower(): s for s in SONOGRAPHERS}

# Recipients (as "Name <email>").
TO_RECIPIENTS = [
    ("Melissa Colpitts", "mcolpitts@oncurapartners.com"),
    ("Sandra Paris", "sparis@oncurapartners.com"),
    ("Becky Tiner", "btiner@oncurapartners.com"),
    ("Chelsea Parsons", "cparsons@oncurapartners.com"),
    ("Denice Rodriguez", "DeniceRodriguez@oncurapartners.com"),
    ("Elyce Thomas", "ethomas@oncurapartners.com"),
    ("Francisco Zuniga", "francisco@oncurapartners.com"),
    ("Katie Heuer", "kheuer@oncurapartners.com"),
    ("Lanis Davis", "ldavis@oncurapartners.com"),
    ("Luis Romero", "lromero@oncurapartners.com"),
    ("Lyannette Curiel", "lyannette@oncurapartners.com"),
    ("Megan DuCasse", "mducasse@oncurapartners.com"),
]
CC_RECIPIENTS = [
    ("Marty McCutchen", "marty@oncurapartners.com"),
    ("Tanya White", "tanya@oncurapartners.com"),
    ("Craig Presnall", "craig@oncurapartners.com"),
]
SUBJECT = "Weekly Assistance Update"
WEEKLY_GOAL = 50   # per week (full-time)
DAILY_GOAL = 10    # per day (full-time, 5-day default)
# Trailing windows shown in the email (both end at the last COMPLETE period).
WEEKLY_WEEKS = 15  # last N complete Mon-Sun weeks
DAILY_DAYS = 15    # last N complete days

# ── Schedule inference (SUBMITTED date/time; counts above stay FinalizedDate) ──
# Finalized date lags the sonographer's actual work, so who-works-when and PTO are
# derived from SubmittedDate (≈ when they scanned). Submitted timestamps also give a
# daily active span → estimated hours → FT/PT. A weekday counts as a scheduled work
# day when its 12-week average volume is >= WORKDAY_FRAC of the person's busiest day.
SCHEDULE_WEEKS = 12
WORKDAY_FRAC = 0.35
FT_WEEKLY_HOURS = 35      # >= this (span x days) => full-time
# Known/confirmed schedules override the inference (HR truth beats data). Luis works
# a 4x10; Becky and Katie work ~6-hour days (both their submit-span and their PTO,
# which is booked in 6-hour units, confirm it) — everyone else infers to 5x8.
CONFIRMED_SCHEDULES = {
    "Luis Romero": {"type": "FT", "days": [0, 1, 2, 3], "shift": "4x10", "weekly_hours": 40},
    "Becky Tiner": {"type": "FT", "days": [0, 1, 2, 3, 4], "shift": "5x6", "weekly_hours": 30},
    "Katie Heuer": {"type": "FT", "days": [0, 1, 2, 3, 4], "shift": "5x6", "weekly_hours": 30},
}

# Company-observed holidays: the office is closed, so a zero-activity day here is NOT
# PTO — without this, every holiday flags all 10 sonographers as "out" (Labor Day
# 2026 did exactly that in testing). Update this list each year. No Juneteenth.
HOLIDAYS = {
    dt.date(2026, 1, 1),    # New Year's Day
    dt.date(2026, 5, 25),   # Memorial Day
    dt.date(2026, 7, 3),    # Independence Day (observed; 7/4 is a Saturday)
    dt.date(2026, 9, 7),    # Labor Day
    dt.date(2026, 11, 26),  # Thanksgiving
    dt.date(2026, 11, 27),  # Day after Thanksgiving
    dt.date(2026, 12, 25),  # Christmas Day
    dt.date(2027, 1, 1),    # New Year's Day (covers year-end runs)
}


def _is_holiday(d: dt.date) -> bool:
    return d in HOLIDAYS


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


def _eastern_dt(iso_utc: str) -> dt.datetime:
    """SubmittedDate (UTC 'Z' string) -> US Eastern datetime (for day + time-of-day)."""
    d = dt.datetime.strptime(iso_utc[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    if _ET is not None:
        return d.astimezone(_ET)
    y = d.year
    mar = dt.date(y, 3, 1); dst0 = mar + dt.timedelta(days=(6 - mar.weekday()) % 7 + 7)
    nov = dt.date(y, 11, 1); dst1 = nov + dt.timedelta(days=(6 - nov.weekday()) % 7)
    off = -4 if dst0 <= d.date() < dst1 else -5
    return d + dt.timedelta(hours=off)


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


@st.cache_data(ttl=1800, show_spinner="Pulling submitted activity for schedules…")
def build_submitted(pull_start_iso: str, pull_end_iso: str) -> dict:
    """Per-sonographer SUBMITTED activity by Eastern submitted date over
    [pull_start, pull_end). SubmittedDate ≈ when the sonographer actually scanned,
    so it drives schedule/FT-PT inference and PTO (counts above stay FinalizedDate).
    Returns {sonographer: {date_iso: [count, first_hr, last_hr]}} where first/last
    are decimal Eastern hours (for the daily active span). Cached 30 min."""
    auth = opd_api.auth_from_secrets()
    start = dt.date.fromisoformat(pull_start_iso)
    end_excl = dt.date.fromisoformat(pull_end_iso)
    out: dict[str, dict[str, list]] = {}
    for a, b in _month_chunks(start, end_excl):
        flt = (f"SubmittedDate ge datetime'{a.isoformat()}T00:00:00' "
               f"and SubmittedDate lt datetime'{b.isoformat()}T00:00:00'")
        rows, _ = opd_api._fetch_all(f"{opd_api.BASE_URL}/Consult", auth=auth,
                                     params={"$filter": flt})
        for r in rows:
            son = _SSET.get((r.get("AssistedBy") or "").strip().lower())
            sd = r.get("SubmittedDate")
            if not (son and sd):
                continue
            edt = _eastern_dt(sd)
            key = edt.date().isoformat()
            hr = edt.hour + edt.minute / 60.0
            day = out.setdefault(son, {}).get(key)
            if day is None:
                out[son][key] = [1, hr, hr]
            else:
                day[0] += 1
                day[1] = min(day[1], hr)
                day[2] = max(day[2], hr)
    return out


def infer_schedules(sub: dict, n_weeks: int = SCHEDULE_WEEKS) -> dict:
    """Derive each sonographer's working pattern from SUBMITTED activity.

    work days  = weekdays whose avg volume ≥ WORKDAY_FRAC × the person's busiest day
    daily span = median (last − first submit) across days with ≥2 submissions
    FT/PT      = (span × #work days) ≥ FT_WEEKLY_HOURS
    Confirmed HR schedules (CONFIRMED_SCHEDULES) override the inference.
    Returns {son: {type, days:[0..6], shift, weekly_hours, daily_goal, confirmed}}."""
    _DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    scheds: dict[str, dict] = {}
    for s in SONOGRAPHERS:
        days = sub.get(s, {})
        wd_total = [0] * 7
        spans = []
        for diso, rec in days.items():
            n, first, last = rec[0], rec[1], rec[2]
            wd_total[dt.date.fromisoformat(diso).weekday()] += n
            if n >= 2 and last > first:
                spans.append(last - first)
        avg = [t / n_weeks for t in wd_total]
        peak = max(avg) if avg else 0.0
        work = [i for i in range(7) if peak > 0 and avg[i] >= WORKDAY_FRAC * peak]
        span = statistics.median(spans) if spans else 8.0
        wk_hours = span * len(work)
        ndays = len(work) or 1
        scheds[s] = {
            "type": "FT" if wk_hours >= FT_WEEKLY_HOURS else "PT",
            "days": work,
            "shift": f"{len(work)}x{round(span)}" if work else "—",
            "weekly_hours": round(wk_hours),
            "daily_goal": max(1, round(WEEKLY_GOAL / ndays)),
            "days_label": ("–".join(_DOW[i] for i in (work[0], work[-1]))
                           if _consecutive(work) else ", ".join(_DOW[i] for i in work)) or "—",
            "confirmed": False,
        }
    for s, ov in CONFIRMED_SCHEDULES.items():
        if s not in scheds:
            continue
        work = ov["days"]
        ndays = len(work) or 1
        scheds[s] = {
            "type": ov["type"], "days": work, "shift": ov["shift"],
            "weekly_hours": ov["weekly_hours"],
            "daily_goal": max(1, round(WEEKLY_GOAL / ndays)),
            "days_label": ("–".join(_DOW[i] for i in (work[0], work[-1]))
                           if _consecutive(work) else ", ".join(_DOW[i] for i in work)) or "—",
            "confirmed": True,
        }
    return scheds


def _consecutive(days: list[int]) -> bool:
    return bool(days) and days == list(range(days[0], days[-1] + 1))


def week_pto(scheds: dict, sub: dict, week_monday: dt.date) -> dict:
    """For the given Mon-Sun week, each sonographer's scheduled work days that had
    ZERO submitted activity (likely PTO / out). Submitted-based, so it reflects when
    people were actually away rather than the finalized-date lag. Company holidays are
    skipped — the office is closed, so a zero there is not PTO.
    Returns {son: [dates]} only for those with at least one such day."""
    out: dict[str, list] = {}
    for s in SONOGRAPHERS:
        act = sub.get(s, {})
        off = []
        for i in scheds.get(s, {}).get("days", []):
            d = week_monday + dt.timedelta(days=i)
            if _is_holiday(d):
                continue
            rec = act.get(d.isoformat())
            if rec is None or rec[0] == 0:
                off.append(d)
        if off:
            out[s] = off
    return out


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


# Transparent cells with dark text (renders on any email background); only the
# header names and goal-met (>= goal) cells carry a fill.
_HEAD_BG = "#5f93a3"   # teal header band (the names)
_HEAD_TX = "#0e2a33"
_GOAL_BG = "#f5e04d"   # yellow highlight for >= goal
_GOAL_TX = "#3a3300"
_TX = "#333333"        # regular numbers
_LBL_TX = "#1f2733"    # row labels (dates)
_BORDER = "#d9dde3"
_FONT = "font-family:Calibri,Arial,sans-serif"


def _cell(content, bg: str | None = None, tx: str = _TX, align: str = "center", bold: bool = False) -> str:
    fill = f'bgcolor="{bg}" ' if bg else ""
    bgs = f"background:{bg};" if bg else ""            # omit entirely -> transparent
    return (f'<td {fill}style="{bgs}color:{tx};font-weight:{"700" if bold else "400"};'
            f'padding:6px 11px;text-align:{align};white-space:nowrap;border:1px solid {_BORDER};'
            f'{_FONT};font-size:11px">{content}</td>')


def _bar(text: str, size: int, ncol: int) -> str:
    # Transparent title/subtitle band — centered dark text, no fill or border.
    return (f'<tr><td colspan="{ncol}" style="color:{_LBL_TX};font-weight:700;text-align:center;'
            f'padding:7px 11px;{_FONT};font-size:{size}px">{text}</td></tr>')


def _table_html(subtitle: str, rows, goal: int | None = None) -> str:
    ncol = len(SONOGRAPHERS) + 1
    header = ("<tr>" + _cell("Assist Count", _HEAD_BG, _HEAD_TX, "left", True)
              + "".join(_cell(_html.escape(s), _HEAD_BG, _HEAD_TX, "center", True) for s in SONOGRAPHERS)
              + "</tr>")
    body = ""
    for label, counts in rows:
        cells = ""
        for s in SONOGRAPHERS:
            v = counts.get(s)
            if goal is not None and isinstance(v, int) and v >= goal:
                cells += _cell(v, _GOAL_BG, _GOAL_TX, "center", True)   # highlighted
            else:
                cells += _cell(v or "")                                # transparent
        body += "<tr>" + _cell(_html.escape(label), None, _LBL_TX, "left", True) + cells + "</tr>"
    return (
        '<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
        f'{_FONT};font-size:11px;margin:16px 0 0">'
        f'{_bar("Finalized Assistance", 15, ncol)}'
        f'{_bar(_html.escape(subtitle), 12, ncol)}'
        f'{header}{body}</table>'
    )


_OFF_BG = "#eef1f4"    # scheduled-off cell (light grey)
_OFF_TX = "#b4bcc6"
_PTO_TX = "#9a6b00"    # PTO note text


def _legend_html(scheds: dict) -> str:
    """Small reference table: who is FT/PT, which days they work, and their shift.
    Confirmed schedules are marked; the rest are inferred from recent activity."""
    head = ("<tr>"
            + _cell("Sonographer", _HEAD_BG, _HEAD_TX, "left", True)
            + _cell("Status", _HEAD_BG, _HEAD_TX, "center", True)
            + _cell("Works", _HEAD_BG, _HEAD_TX, "center", True)
            + _cell("Shift", _HEAD_BG, _HEAD_TX, "center", True)
            + "</tr>")
    body = ""
    for s in SONOGRAPHERS:
        sc = scheds[s]
        name = _html.escape(s) + ("" if sc["confirmed"] else " *")
        body += ("<tr>"
                 + _cell(name, None, _LBL_TX, "left", True)
                 + _cell(sc["type"])
                 + _cell(_html.escape(sc["days_label"]))
                 + _cell(_html.escape(sc["shift"]))
                 + "</tr>")
    note = ('<tr><td colspan="4" style="color:#6b7480;padding:5px 11px;'
            f'{_FONT};font-size:10px">* schedule inferred from recent submitted activity; '
            'all others confirmed. Weekly goal is '
            f'{WEEKLY_GOAL}/week for full-time.</td></tr>')
    return (
        '<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
        f'{_FONT};font-size:11px;margin:16px 0 0">'
        f'{_bar("Sonographer Schedules", 15, 4)}'
        f'{head}{body}{note}</table>'
    )


def _notes_html(pto: dict, week_label: str, holidays: list | None = None) -> str:
    """Submitted-activity callout for the reported week: who had a scheduled work day
    with no activity (likely PTO / out). Empty -> a clean 'full attendance' line.
    Any company holiday in the week is called out so a lighter week reads correctly."""
    if not pto:
        inner = ('Full attendance — every sonographer had activity on each of their '
                 'scheduled work days.')
    else:
        items = ""
        for s in SONOGRAPHERS:
            if s not in pto:
                continue
            dys = ", ".join(f"{d.strftime('%a')} {d.month}/{d.day}" for d in pto[s])
            items += (f'<li style="margin:2px 0"><b>{_html.escape(s)}</b>: '
                      f'no activity {dys}</li>')
        inner = ('Scheduled work days with no submitted activity (likely PTO / out):'
                 f'<ul style="margin:6px 0 0;padding-left:20px">{items}</ul>')
    if holidays:
        hol = ", ".join(f"{d.strftime('%a')} {d.month}/{d.day}" for d in holidays)
        inner += (f'<div style="margin-top:6px">Company holiday this week ({hol}); '
                  'the office was closed, so that day is not counted as PTO.</div>')
    return (
        f'<div style="{_FONT};font-size:12px;color:{_PTO_TX};'
        f'background:#fff8e8;border:1px solid #f0e2bd;border-radius:4px;'
        f'padding:9px 13px;margin:16px 0 0">'
        f'<b>Week of {_html.escape(week_label)} — attendance notes</b><br>{inner}</div>'
    )


def _daily_table_html(subtitle: str, rows, scheds: dict) -> str:
    """Daily table (FinalizedDate counts). Cells on a sonographer's scheduled-off day
    are greyed; goal highlight uses that person's prorated daily goal."""
    ncol = len(SONOGRAPHERS) + 1
    header = ("<tr>" + _cell("Assist Count", _HEAD_BG, _HEAD_TX, "left", True)
              + "".join(_cell(_html.escape(s), _HEAD_BG, _HEAD_TX, "center", True) for s in SONOGRAPHERS)
              + "</tr>")
    body = ""
    for d, counts in rows:
        holiday = _is_holiday(d)
        cells = ""
        for s in SONOGRAPHERS:
            if holiday or d.weekday() not in scheds[s]["days"]:
                cells += _cell("", _OFF_BG, _OFF_TX)            # holiday or scheduled off
                continue
            v = counts.get(s)
            goal = scheds[s]["daily_goal"]
            if isinstance(v, int) and v >= goal:
                cells += _cell(v, _GOAL_BG, _GOAL_TX, "center", True)
            else:
                cells += _cell(v or "")
        label = _mdY(d) + (" (holiday)" if holiday else "")
        body += "<tr>" + _cell(label, None, _LBL_TX, "left", True) + cells + "</tr>"
    legend = ('<tr><td colspan="%d" style="color:#6b7480;padding:5px 11px;%s;font-size:10px">'
              'Shaded cells = scheduled day off or company holiday. Highlight = met that person\'s daily goal.'
              '</td></tr>' % (ncol, _FONT))
    return (
        '<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
        f'{_FONT};font-size:11px;margin:16px 0 0">'
        f'{_bar("Finalized Assistance", 15, ncol)}'
        f'{_bar(_html.escape(subtitle), 12, ncol)}'
        f'{header}{body}{legend}</table>'
    )


def build_email(today: dt.date | None = None) -> tuple[str, str, str]:
    """Pull the data and render the email. Returns (subject, plain_body, html_body).

    COUNT tables stay on FinalizedDate. Schedules (FT/PT, work days, shift) and the
    PTO/attendance notes are derived from SubmittedDate, which tracks when the
    sonographer actually scanned rather than the finalized-date lag."""
    if today is None:
        today = eastern_today()
    this_monday = today - dt.timedelta(days=today.weekday())
    last_week_monday = this_monday - dt.timedelta(days=7)
    weekly_start = last_week_monday - dt.timedelta(days=7 * (WEEKLY_WEEKS - 1))
    daily_start = (today - dt.timedelta(days=1)) - dt.timedelta(days=DAILY_DAYS - 1)
    pull_start = min(weekly_start, daily_start)
    counts = build_counts(pull_start.isoformat(), (today + dt.timedelta(days=1)).isoformat())
    wk_rows, _ = _rows(counts, today)

    # Daily rows carry the date (needed for per-person schedule/greying).
    last_day = today - dt.timedelta(days=1)
    dy_rows = [(last_day - dt.timedelta(days=i), counts["daily"].get(
        (last_day - dt.timedelta(days=i)).isoformat(), {}))
        for i in range(DAILY_DAYS - 1, -1, -1)]

    # Schedules + PTO from submitted activity (last SCHEDULE_WEEKS complete weeks).
    sched_start = this_monday - dt.timedelta(weeks=SCHEDULE_WEEKS)
    sub = build_submitted(sched_start.isoformat(), this_monday.isoformat())
    scheds = infer_schedules(sub)
    pto = week_pto(scheds, sub, last_week_monday)
    wk_holidays = [last_week_monday + dt.timedelta(days=i) for i in range(7)
                   if _is_holiday(last_week_monday + dt.timedelta(days=i))]

    html = (
        "<div style='font-family:Calibri,Arial,sans-serif;font-size:14px;color:#1f2733'>"
        "<p>Hello all,</p>"
        "<p>Please see the following assisting sonographer activity reports.</p>"
        f"{_legend_html(scheds)}"
        f"{_notes_html(pto, _mdY(last_week_monday), wk_holidays)}"
        f"{_table_html(f'Weekly (Goal: {WEEKLY_GOAL}/week, full-time)', wk_rows, goal=WEEKLY_GOAL)}<br>"
        f"{_daily_table_html('Daily (goal prorated to each schedule)', dy_rows, scheds)}"
        "</div>"
    )
    plain = (
        "Hello all,\n\n"
        "Please see the following assisting sonographer activity reports "
        "(formatted tables in the HTML version of this email)."
    )
    return SUBJECT, plain, html
