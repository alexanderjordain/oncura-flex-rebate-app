"""Weekly Demos + Trainings report for the training sonographers.

Trainings come from OPD: Abdomen + Basic Echo certifications, counted by the case's
SubmittedDate and attributed to the trainer in the case's AssistedBy field (GlobalFAST
excluded; finalization is a month-end supervisor step, so we count submissions).

Demos come from the trainers' Outlook calendars. This module holds only the pure demo
classification plus the OPD pull and the HTML rendering; the actual calendar reads and the
send live in the private automation repo (demos_trainings_weekly.py), so no Graph code or
credentials sit in this public repo.

Styling mirrors the Weekly Assistance Update. Sandra runs demos only, Sarah runs trainings
only, so each appears in just one table.
"""
from __future__ import annotations

import datetime as dt
import html as _html
from zoneinfo import ZoneInfo

import streamlit as st

from . import opd_api
from .assist_report import _cell, _bar, _FONT, _HEAD_BG, _HEAD_TX, _LBL_TX, _TX, _mdY

_CT = ZoneInfo("America/Chicago")

SUBJECT = "Weekly Demos + Trainings - Training Sonographers"
WEEKS = 4  # trailing complete weeks shown

# Demos: these trainers run sales demos (read from their calendars).
DEMO_TRAINERS = ["Carla", "John Paul", "Mariah", "Rosie", "Sandra"]
# Trainings: these appear in OPD certification AssistedBy.
TRAIN_TRAINERS = ["Carla", "John Paul", "Mariah", "Rosie", "Sarah"]

# Mailbox per demo trainer (the sender reads these calendars app-only).
DEMO_MAILBOX = {
    "Carla": "carla@oncurapartners.com",
    "John Paul": "jpamberger@oncurapartners.com",
    "Mariah": "mariah@oncurapartners.com",
    "Rosie": "rharo@oncurapartners.com",
    "Sandra": "sparis@oncurapartners.com",
}

# Subject tokens that mark a trainer as the sonographer on a demo ("rep/sonographer").
# Used so a demo someone was merely invited to (e.g. a Sandra demo on JP's calendar) is
# not counted against the wrong trainer.
_DEMO_TOKENS = {
    "Carla": ("carla",),
    "John Paul": ("amberger", "john paul", " jp", "/jp"),
    "Mariah": ("mariah", "delgado"),
    "Rosie": ("rosie", "haro"),
    "Sandra": ("sandra", "paris"),
}
_PLACEHOLDER_SUBJECTS = ("demo block", "demo spot")


def _central_date(iso_utc: str) -> dt.date:
    """OPD UTC 'Z' timestamp -> US Central calendar date."""
    d = dt.datetime.strptime(iso_utc[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    return d.astimezone(_CT).date()


def _week_monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def complete_weeks(today: dt.date, n: int = WEEKS) -> list[dt.date]:
    """The n most recent COMPLETE weeks (as Mondays), ending the Sunday before today's week."""
    last_monday = _week_monday(today) - dt.timedelta(days=7)
    return [last_monday - dt.timedelta(days=7 * i) for i in range(n - 1, -1, -1)]


def is_demo_for(subject: str, organizer_email: str, owner_mailbox: str, owner_label: str) -> bool:
    """True if a calendar event on ``owner_mailbox``'s calendar is a real sales demo that
    ``owner_label`` ran as the sonographer. Excludes self-booked availability holds
    ("Demo Block"/"Demo Spot"), events the owner organized themselves, and events where the
    owner is not named as the sonographer. Canceled filtering happens in the sender."""
    subj = (subject or "").strip().lower()
    if "demo" not in subj:
        return False
    if subj in _PLACEHOLDER_SUBJECTS or subj.rstrip() in _PLACEHOLDER_SUBJECTS:
        return False
    if (organizer_email or "").strip().lower() == (owner_mailbox or "").strip().lower():
        return False  # a hold the trainer put on their own calendar, not a booked demo
    return any(tok in subj for tok in _DEMO_TOKENS.get(owner_label, ()))


def _train_label(name: str | None) -> str | None:
    """Map an OPD AssistedBy full name to the short trainer label used in the table."""
    n = (name or "").lower()
    if "carla" in n:
        return "Carla"
    if "amberger" in n or "john paul" in n:
        return "John Paul"
    if "delgado" in n or "mariah" in n:
        return "Mariah"
    if "haro" in n or "rosie" in n:
        return "Rosie"
    if "ervin" in n or "sarah" in n:
        return "Sarah"
    return None


@st.cache_data(ttl=1800, show_spinner="Pulling certifications from OPD…")
def build_trainings(pull_start_iso: str, pull_end_iso: str) -> dict:
    """OPD Abdomen + Basic Echo certifications by SubmittedDate (Central) week, attributed via
    the parent Consult's AssistedBy. GlobalFAST excluded. Returns {monday_iso: {trainer: count}}.

    Certifications live as ConsultService lines whose ServiceName contains "Certification";
    the parent consult id is the scalar field ConsultServiceCost_Consult (Mendix ignores
    $expand). ID eq must be UNQUOTED on this OData feed."""
    auth = opd_api.auth_from_secrets()
    flt = ("substringof('Certification',ServiceName) and "
           f"createdDate ge datetime'{pull_start_iso}T00:00:00' and "
           f"createdDate lt datetime'{pull_end_iso}T00:00:00'")
    lines, _ = opd_api._fetch_all(f"{opd_api.BASE_URL}/ConsultService", auth=auth,
                                  params={"$filter": flt})
    cids = sorted({
        l.get("ConsultServiceCost_Consult") for l in lines
        if (l.get("ServiceName") and "Certification" in l["ServiceName"]
            and "GlobalFAST" not in l["ServiceName"] and l.get("ConsultServiceCost_Consult"))
    })
    cons: dict = {}
    for i in range(0, len(cids), 15):
        f = "(" + " or ".join(f"ID eq {c}" for c in cids[i:i + 15]) + ")"
        rows, _ = opd_api._fetch_all(f"{opd_api.BASE_URL}/Consult", auth=auth, params={"$filter": f})
        for r in rows:
            cons[r.get("ID")] = r

    out: dict = {}
    for l in lines:
        sv = l.get("ServiceName") or ""
        if "Certification" not in sv or "GlobalFAST" in sv:
            continue
        c = cons.get(l.get("ConsultServiceCost_Consult"))
        if not c or not c.get("SubmittedDate"):
            continue
        lab = _train_label(c.get("AssistedBy"))
        if lab is None:
            continue
        mon = _week_monday(_central_date(c["SubmittedDate"])).isoformat()
        out.setdefault(mon, {}).setdefault(lab, 0)
        out[mon][lab] += 1
    return out


def _tbl(cols, corner, title, subtitle, weekmap, weeks) -> str:
    ncol = len(cols) + 2
    header = ("<tr>" + _cell(corner, _HEAD_BG, _HEAD_TX, "left", True)
              + "".join(_cell(_html.escape(c), _HEAD_BG, _HEAD_TX, "center", True) for c in cols)
              + _cell("Total", _HEAD_BG, _HEAD_TX, "center", True) + "</tr>")
    body = ""
    for m in weeks:
        counts = weekmap.get(m.isoformat(), {})
        tot = sum(int(counts.get(c, 0) or 0) for c in cols)
        cells = "".join(_cell(counts.get(c) or "") for c in cols)
        body += ("<tr>" + _cell("WO: " + _mdY(m), None, _LBL_TX, "left", True)
                 + cells + _cell(tot or "", None, _TX, "center", True) + "</tr>")
    return ('<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
            f'{_FONT};font-size:11px;margin:16px 0 0">'
            f'{_bar(title, 15, ncol)}{_bar(_html.escape(subtitle), 12, ncol)}{header}{body}</table>')


def render_email(demos: dict, trainings: dict, weeks: list) -> tuple[str, str, str]:
    """Return (subject, plain_body, html_body). `demos`/`trainings` are {monday_iso: {trainer: n}}."""
    note = (
        '<p style="font-family:Calibri,Arial,sans-serif;font-size:11px;color:#6b7580;margin:16px 0 0">'
        "Trainings are Abdomen and Basic Echo certifications in OPD, counted by the case's submitted "
        "date and attributed to the trainer in the case's AssistedBy field (GlobalFAST excluded). "
        "Finalization requires supervisor approval at month-end, so we count submissions, not "
        "finalizations. Demos are sales demos from the trainers' Outlook calendars; availability holds "
        "and canceled sessions are excluded. Sandra runs demos only and Sarah runs trainings only, so "
        "each appears in one table. Weeks run Monday to Sunday.</p>")
    html = ("<div style='font-family:Calibri,Arial,sans-serif;font-size:14px;color:#1f2733'>"
            "<p>Hello all,</p>"
            "<p>Please see the following demo and training activity for the training sonographers.</p>"
            f"{_tbl(DEMO_TRAINERS, 'Demos', 'Demos', 'Sales demos delivered, count by week', demos, weeks)}<br>"
            f"{_tbl(TRAIN_TRAINERS, 'Trainings', 'Trainings', 'Certifications submitted (Abdomen + Basic Echo), count by week', trainings, weeks)}"
            f"{note}</div>")
    last = weeks[-1]
    d_tot = sum(int(v or 0) for v in demos.get(last.isoformat(), {}).values())
    t_tot = sum(int(v or 0) for v in trainings.get(last.isoformat(), {}).values())
    plain = ("Hello all,\n\nDemo and training activity for the training sonographers "
             "(formatted tables in the HTML version of this email).\n\n"
             f"Week of {_mdY(last)}: {d_tot} demos, {t_tot} certifications submitted.")
    return SUBJECT, plain, html
