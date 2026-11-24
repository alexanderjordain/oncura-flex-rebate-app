"""Weekly Submissions Goals Update - abdominal + cardiac progress vs goal.

A team-facing Monday email that shows, for abdominal and cardiac submissions:
  - where we are (actual to date), and
  - where the goal pace says we should be (target to date), with the gap,
for both the current month and the year to date.

EVERYTHING is derived from the run date - there are no baked-in "as of" dates,
working-day counts, or period numbers. Given the Monday it runs, the report:
  - sets the "as of" date to the preceding Friday (the last completed work week),
  - counts that month's working days (excluding weekends + company holidays) and
    how many are complete through the "as of" Friday,
  - prorates each modality's monthly / annual goal across those working days, and
  - renders the same two tables for whatever month and year it lands in.

Rads and Global FAST are deliberately excluded (Craig Presnall, 2026-11-23): the
team-facing push is abdominal and cardiac only.

Actuals (abd/cardiac submissions, MTD + YTD through the "as of" Friday) are passed
IN so the report is agnostic to how they are sourced - see the sender for the pull.
The company holiday calendar is shared with the assist report (one source of truth).
"""
from __future__ import annotations

import collections
import datetime as dt
import html as _html
from datetime import timezone
from zoneinfo import ZoneInfo

from . import opd_api
from .assist_report import _cell, _bar, _FONT, _HEAD_BG, _HEAD_TX, _LBL_TX, _TX, HOLIDAYS, _mdY

_EAST = ZoneInfo("America/New_York")

SUBJECT = "Weekly Submissions Goals Update"

# ── Monthly submission goals (the 2026 Push Growth schedule) ────────────────────
# Abdominal + Cardiac (Echo) only. Keyed (year, month) so a new year is a drop-in
# addition, not an edit. Full-year sums: abdominal 22,782; cardiac 13,233. UPDATE
# yearly with the new schedule (a missing month raises rather than guessing).
GOAL_SCHEDULE: dict[tuple[int, int], dict[str, int]] = {
    (2026, 1):  {"abdominal": 1575, "cardiac": 840},
    (2026, 2):  {"abdominal": 1600, "cardiac": 900},
    (2026, 3):  {"abdominal": 1870, "cardiac": 1100},
    (2026, 4):  {"abdominal": 1907, "cardiac": 1122},
    (2026, 5):  {"abdominal": 1769, "cardiac": 1040},
    (2026, 6):  {"abdominal": 1984, "cardiac": 1167},
    (2026, 7):  {"abdominal": 2024, "cardiac": 1191},
    (2026, 8):  {"abdominal": 1971, "cardiac": 1159},
    (2026, 9):  {"abdominal": 2010, "cardiac": 1182},
    (2026, 10): {"abdominal": 2148, "cardiac": 1264},
    (2026, 11): {"abdominal": 1892, "cardiac": 1113},
    (2026, 12): {"abdominal": 2032, "cardiac": 1155},
}

MODS = [("abdominal", "Abdominal"), ("cardiac", "Cardiac")]

# sign colors (match the house palette); the total row carries a light fill.
_NEG = "#b23a3a"
_POS = "#1b6e3a"
_TOTAL_BG = "#eef3f5"


# ── Date / working-day logic (all relative to the run date) ──────────────────────
def as_of_friday(run_date: dt.date) -> dt.date:
    """The most recent Friday strictly before ``run_date``. For the Monday cron this
    is the preceding Friday (the last completed work week)."""
    d = run_date - dt.timedelta(days=1)
    while d.weekday() != 4:  # 4 = Friday
        d -= dt.timedelta(days=1)
    return d


def _is_workday(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def month_workdays(year: int, month: int) -> list[dt.date]:
    """Every working day (Mon-Fri, excluding company holidays) in the month."""
    d = dt.date(year, month, 1)
    out = []
    while d.month == month:
        if _is_workday(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def working_day_progress(as_of: dt.date) -> tuple[int, int, int]:
    """(total, done, left) working days for the month containing ``as_of``, where
    ``done`` are the working days on or before ``as_of`` and ``left`` are those after."""
    wds = month_workdays(as_of.year, as_of.month)
    done = sum(1 for d in wds if d <= as_of)
    return len(wds), done, len(wds) - done


def year_workdays(year: int) -> int:
    """Total working days (Mon-Fri, excluding company holidays) in the full year."""
    return sum(len(month_workdays(year, m)) for m in range(1, 13))


def year_workdays_through(as_of: dt.date) -> int:
    """Working days in ``as_of``'s year on or before ``as_of`` (completed months in
    full plus the current month through the 'as of' Friday)."""
    done = sum(len(month_workdays(as_of.year, m)) for m in range(1, as_of.month))
    return done + working_day_progress(as_of)[1]


def _goal(year: int, month: int, mod: str) -> int:
    try:
        return GOAL_SCHEDULE[(year, month)][mod]
    except KeyError:
        raise KeyError(
            f"No submission goal for {year}-{month:02d} ({mod}). Add the "
            f"{year} schedule to GOAL_SCHEDULE before the report runs in {year}."
        )


def _ytd_goal(year: int, month: int, mod: str) -> int:
    """Full goal for January..month-1 of the same year (completed months)."""
    return sum(_goal(year, m, mod) for m in range(1, month))


def _full_year_goal(year: int, mod: str) -> int:
    return sum(_goal(year, m, mod) for m in range(1, 13))


# ── Compute the table rows (actuals passed in; source-agnostic) ──────────────────
def compute(as_of: dt.date, actuals: dict) -> dict:
    """Build the month + YTD rows for ``as_of``.

    ``actuals`` keys: abd_mtd, card_mtd, abd_ytd, card_ytd (submission counts through
    ``as_of``). Returns {"as_of", "total", "done", "left", "month_rows", "ytd_rows"},
    each row = (label, actual, target_to_date, variance, goal). Totals are summed rows.
    """
    y, m = as_of.year, as_of.month
    total, done, left = working_day_progress(as_of)

    month_rows, ytd_rows = [], []
    for key, label in MODS:
        # Month: target to date = monthly goal prorated across working days so far.
        g = _goal(y, m, key)
        mtd_target = round(g * done / total)
        mtd_act = int(actuals[f"{'abd' if key == 'abdominal' else 'card'}_mtd"])
        month_rows.append((label, mtd_act, mtd_target, mtd_act - mtd_target, g))

        # YTD: completed months in full + current month prorated to date.
        ytd_target = _ytd_goal(y, m, key) + mtd_target
        fy = _full_year_goal(y, key)
        ytd_act = int(actuals[f"{'abd' if key == 'abdominal' else 'card'}_ytd"])
        ytd_rows.append((label, ytd_act, ytd_target, ytd_act - ytd_target, fy))

    def _total(rows):
        return ("Total", *(sum(r[i] for r in rows) for i in range(1, 5)))

    month_rows.append(_total(month_rows))
    ytd_rows.append(_total(ytd_rows))
    return {"as_of": as_of, "total": total, "done": done, "left": left,
            "ydone": year_workdays_through(as_of), "ytotal": year_workdays(y),
            "month_rows": month_rows, "ytd_rows": ytd_rows}


# ── Actuals from OPD (reverse-engineered to tie to the published numbers) ────────
# Validated 2026-11-23 vs the team's figures (as of Fri 11/20): abd 1,331 / cardiac
# 626 MTD, 18,421 / 9,163 YTD -> this method lands within ~0.1% (pull-timing noise).
# Method: count a case by its SUBMITTED date (Eastern), only once it is Finalized or
# Corrected (drafts/cancellations excluded), under its primary modality where echo
# outranks abdominal. Echo = echocardiogram only - standalone ECG and echo-measurement
# fees do NOT count as cardiac (that was the gap that made a naive pull overcount).
_COUNTED_STATUSES = {"Finalized", "Corrected"}


def _svc_cat(name: str) -> str:
    """Minimal service classifier: only echo vs abdominal matter for this report."""
    s = str(name).lower().strip()
    if "echocardiogram" in s:
        return "echo"
    if ("abdominal ultrasound" in s or "abdominal consult" in s
            or "abdominal contrast" in s or "internal medicine" in s):
        return "abdominal"
    if s == "ultrasound":
        return "abdominal"
    return "other"   # ECG, radiographs, measurement fees, certs, globalfast, etc.


def _case_modality(cats: dict) -> str | None:
    """Per-case primary: echo outranks abdominal; a non-scan case counts for neither."""
    if cats.get("echo", 0) > 0:
        return "cardiac"
    if cats.get("abdominal", 0) > 0:
        return "abdominal"
    return None


def count_actuals(records, as_of: dt.date) -> dict:
    """Pure: count abd/cardiac submissions from pre-pulled records (no network).

    Each record: {"sub": date|None, "status": str, "cats": {cat: n}}. Counts cases
    submitted in ``as_of``'s year through ``as_of`` that are Finalized/Corrected, by
    primary modality. Returns {abd_mtd, card_mtd, abd_ytd, card_ytd}.
    """
    out = {"abd_mtd": 0, "card_mtd": 0, "abd_ytd": 0, "card_ytd": 0}
    for r in records:
        d = r.get("sub")
        if not d or d.year != as_of.year or d > as_of:
            continue
        if r.get("status") not in _COUNTED_STATUSES:
            continue
        mod = _case_modality(r.get("cats", {}))
        if mod == "abdominal":
            out["abd_ytd"] += 1
            if d.month == as_of.month:
                out["abd_mtd"] += 1
        elif mod == "cardiac":
            out["card_ytd"] += 1
            if d.month == as_of.month:
                out["card_mtd"] += 1
    return out


def _eastern_date(iso: str):
    d = opd_api._parse_dt(iso)
    return d.replace(tzinfo=timezone.utc).astimezone(_EAST).date() if d else None


def _month_chunks(a: dt.date, b: dt.date):
    out, cur = [], a
    while cur < b:
        nxt = dt.date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
        out.append((cur, min(nxt, b)))
        cur = nxt
    return out


def pull_actuals(as_of: dt.date) -> dict:
    """Live OPD pull of this year's abd/cardiac submissions through ``as_of``.

    Pulls consults by SubmittedDate (Jan 1..as_of) and their service lines, classifies
    per the validated method, and returns the four counts count_actuals produces.
    """
    auth = opd_api.auth_from_secrets()
    base = opd_api.BASE_URL
    y = as_of.year
    start, end = dt.date(y, 1, 1), as_of + dt.timedelta(days=1)

    consults = {}  # id -> (sub_iso, status)
    for a, b in _month_chunks(start, end):
        flt = (f"SubmittedDate ge datetime'{a.isoformat()}T00:00:00' "
               f"and SubmittedDate lt datetime'{b.isoformat()}T00:00:00'")
        rows, total = opd_api._fetch_all(f"{base}/Consult", auth=auth, params={
            "$filter": flt, "$select": "ID,SubmittedDate,CaseStatus",
            "$inlinecount": "allpages", "$top": 10000})
        if total is not None and total > len(rows):
            raise RuntimeError(f"OPD consult page truncated for {a} ({total}>{len(rows)})")
        for r in rows:
            cid = str(r.get("ID") or "")
            sub = r.get("SubmittedDate")
            if cid and sub:
                consults[cid] = (sub, str(r.get("CaseStatus")))

    svc = collections.defaultdict(list)
    for a, b in _month_chunks(start - dt.timedelta(days=20), end):
        flt = (f"createdDate ge datetime'{a.isoformat()}T00:00:00' "
               f"and createdDate lt datetime'{b.isoformat()}T00:00:00'")
        rows, total = opd_api._fetch_all(f"{base}/ConsultService", auth=auth, params={
            "$filter": flt, "$select": "ConsultServiceCost_Consult,ServiceName,createdDate",
            "$inlinecount": "allpages", "$top": 10000})
        if total is not None and total > len(rows):
            raise RuntimeError(f"OPD service page truncated for {a} ({total}>{len(rows)})")
        for r in rows:
            cid = str(r.get("ConsultServiceCost_Consult") or "")
            if cid:
                svc[cid].append(r.get("ServiceName"))

    records = []
    for cid, (sub, status) in consults.items():
        lines = svc.get(cid)
        if not lines:
            continue
        records.append({"sub": _eastern_date(sub), "status": status,
                        "cats": collections.Counter(_svc_cat(x) for x in lines)})
    return count_actuals(records, as_of)


# ── Render ──────────────────────────────────────────────────────────────────────
def _num(n) -> str:
    return f"{n:,}"


def _signed(n) -> str:
    return f"{n:,}" if n < 0 else f"+{n:,}"


def _rate(n, days) -> str:
    """Average per working day, to one decimal (dash if there are no days yet)."""
    return f"{n / days:.1f}" if days else "-"


def _table(title: str, subtitle: str, goal_header: str, rows, done: int, total: int) -> str:
    head = ("<tr>"
            + _cell("Modality", _HEAD_BG, _HEAD_TX, "left", True)
            + _cell("Actual to Date", _HEAD_BG, _HEAD_TX, "center", True)
            + _cell("Goal to Date", _HEAD_BG, _HEAD_TX, "center", True)
            + _cell("+/-", _HEAD_BG, _HEAD_TX, "center", True)
            + _cell("Actual / Day", _HEAD_BG, _HEAD_TX, "center", True)
            + _cell("Goal / Day", _HEAD_BG, _HEAD_TX, "center", True)
            + _cell(goal_header, _HEAD_BG, _HEAD_TX, "center", True)
            + "</tr>")
    body = ""
    for i, (label, act, tgt, var, goal) in enumerate(rows):
        is_total = (i == len(rows) - 1)
        rb = _TOTAL_BG if is_total else None
        vcolor = _NEG if var < 0 else _POS
        body += ("<tr>"
                 + _cell(_html.escape(label), rb, _LBL_TX, "left", True)
                 + _cell(_num(act), rb, _LBL_TX if is_total else _TX, "center", is_total)
                 + _cell(_num(tgt), rb, _LBL_TX if is_total else _TX, "center", is_total)
                 + _cell(_signed(var), rb, vcolor, "center", True)
                 + _cell(_rate(act, done), rb, _LBL_TX if is_total else _TX, "center", is_total)
                 + _cell(_rate(goal, total), rb, _LBL_TX if is_total else _TX, "center", is_total)
                 + _cell(_num(goal), rb, _LBL_TX if is_total else _TX, "center", is_total)
                 + "</tr>")
    return ('<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
            f'{_FONT};font-size:11px;margin:18px 0 0">'
            f'{_bar(title, 15, 7)}{_bar(_html.escape(subtitle), 12, 7)}{head}{body}</table>')


def render_email(as_of: dt.date, actuals: dict) -> tuple[str, str, str]:
    """Return (subject, plain_body, html_body) for the week ending ``as_of``."""
    data = compute(as_of, actuals)
    total, done, left = data["total"], data["done"], data["left"]
    asof_md = f"{as_of.month}/{as_of.day}"
    month_name = as_of.strftime("%B")

    msub = f"Through Friday {asof_md} - {done} of {total} working days done, {left} left"
    ysub = f"Abdominal and cardiac, January through {asof_md}"

    html = (
        f"<div style='{_FONT};font-size:14px;color:#1f2733'>"
        "<p>Hello all,</p>"
        f"<p>Here's where we stand on abdominal and cardiac submissions, with data through Friday {asof_md}.</p>"
        f"{_table(month_name, msub, 'Full-Month Goal', data['month_rows'], data['done'], data['total'])}"
        f"{_table(f'{as_of.year} Year to Date', ysub, 'Full-Year Goal', data['ytd_rows'], data['ydone'], data['ytotal'])}"
        '<p style="font-family:Calibri,Arial,sans-serif;font-size:11px;color:#6b7580;'
        'margin:18px 0 0;line-height:1.5">'
        "Goal to Date is where the goal pace puts us through the last completed working day: "
        "the month or year goal spread evenly across the period's working days. Actual / Day is "
        "the average submissions per working day so far; Goal / Day is the steady daily pace the "
        "full goal requires. Working days exclude weekends and company holidays. Weeks run Monday "
        "to Sunday.</p>"
        "</div>"
    )

    mr = {r[0]: r for r in data["month_rows"]}
    plain = (
        "Hello all,\n\n"
        f"Abdominal and cardiac submissions through Friday {asof_md} "
        "(formatted tables in the HTML version of this email).\n\n"
        f"{month_name}: abdominal {mr['Abdominal'][1]:,} of {mr['Abdominal'][4]:,} goal "
        f"({_rate(mr['Abdominal'][1], done)}/day vs {_rate(mr['Abdominal'][4], total)}/day goal); "
        f"cardiac {mr['Cardiac'][1]:,} of {mr['Cardiac'][4]:,} goal "
        f"({_rate(mr['Cardiac'][1], done)}/day vs {_rate(mr['Cardiac'][4], total)}/day goal)."
    )
    return SUBJECT, plain, html
