"""Tests for the Weekly Submissions Goals report - validates the hand-checked draft
and proves every date/period number is derived from the run date (no fixed artifacts)."""
import datetime as dt

import pytest

from core import submissions_goals as sg

# Jennifer's actuals through Friday 11/20 (the numbers the hand-built draft used).
NOV_ACTUALS = {"abd_mtd": 1331, "card_mtd": 626, "abd_ytd": 18421, "card_ytd": 9163}


def test_as_of_is_preceding_friday_for_any_weekday():
    # Monday 11/23 -> Friday 11/20; every other weekday that week -> same Friday.
    assert sg.as_of_friday(dt.date(2026, 11, 23)) == dt.date(2026, 11, 20)
    assert sg.as_of_friday(dt.date(2026, 11, 30)) == dt.date(2026, 11, 27)
    assert sg.as_of_friday(dt.date(2026, 12, 7)) == dt.date(2026, 12, 4)
    assert sg.as_of_friday(dt.date(2027, 1, 4)) == dt.date(2027, 1, 1)


def test_november_matches_hand_verified_draft():
    d = sg.compute(dt.date(2026, 11, 20), NOV_ACTUALS)
    assert (d["total"], d["done"], d["left"]) == (19, 15, 4)   # Thanksgiving 11/26-27 off
    assert d["month_rows"] == [
        ("Abdominal", 1331, 1494, -163, 1892),
        ("Cardiac",    626,  879, -253, 1113),
        ("Total",     1957, 2373, -416, 3005),
    ]
    assert d["ytd_rows"] == [
        ("Abdominal", 18421, 20352, -1931, 22782),
        ("Cardiac",    9163, 11844, -2681, 13233),
        ("Total",     27584, 32196, -4612, 36015),
    ]


def test_full_year_goal_sums():
    assert sg._full_year_goal(2026, "abdominal") == 22782
    assert sg._full_year_goal(2026, "cardiac") == 13233


def test_period_and_targets_move_with_run_date():
    # A later run date yields a different as-of, working-day progress, and target -
    # the proof there are no baked-in period numbers.
    nov = sg.compute(dt.date(2026, 11, 20), NOV_ACTUALS)
    dec = sg.compute(dt.date(2026, 12, 4), NOV_ACTUALS)
    assert (dec["total"], dec["done"]) == (21, 4)          # Dec: 23 weekdays - 12/24 & 12/25
    assert dec["month_rows"][0][2] == round(2032 * 4 / 21)  # Dec abd target to date = 387
    assert dec["month_rows"][0][2] != nov["month_rows"][0][2]
    # YTD target in December includes all of Jan-Nov in full plus the Dec proration.
    assert dec["ytd_rows"][0][2] == sg._ytd_goal(2026, 12, "abdominal") + round(2032 * 4 / 21)


def test_holidays_reduce_working_days():
    # November has 21 weekdays; Thanksgiving (11/26, 11/27) drops it to 19.
    assert len(sg.month_workdays(2026, 11)) == 19


def test_year_working_days_for_per_day_pace():
    # Per-day rates divide by working days: full year 2026 = 253 (two Dec closures);
    # through Fri 11/20 = Jan-Oct in full plus the 15 November working days done = 228.
    assert sg.year_workdays(2026) == 253
    assert sg.year_workdays_through(dt.date(2026, 11, 20)) == 228
    d = sg.compute(dt.date(2026, 11, 20), NOV_ACTUALS)
    assert (d["ydone"], d["ytotal"]) == (228, 253)


def test_missing_year_schedule_raises_not_guesses():
    with pytest.raises(KeyError):
        sg.compute(dt.date(2027, 1, 1), NOV_ACTUALS)   # 2027 schedule not loaded yet


def test_count_actuals_definition():
    # Validated method: submitted date, Finalized/Corrected only, per-case primary,
    # echo (echocardiogram) outranks abdominal, ECG/other count for neither.
    as_of = dt.date(2026, 11, 20)
    recs = [
        {"sub": dt.date(2026, 11, 10), "status": "Finalized", "cats": {"echo": 1, "abdominal": 1}},  # cardiac
        {"sub": dt.date(2026, 11, 12), "status": "Finalized", "cats": {"abdominal": 1}},             # abd
        {"sub": dt.date(2026, 11, 13), "status": "Corrected", "cats": {"abdominal": 2}},             # abd (once)
        {"sub": dt.date(2026, 11, 14), "status": "Created",   "cats": {"abdominal": 1}},             # excluded: not final
        {"sub": dt.date(2026, 11, 15), "status": "Cancelled", "cats": {"echo": 1}},                  # excluded: cancelled
        {"sub": dt.date(2026, 11, 16), "status": "Finalized", "cats": {"other": 1}},                 # neither
        {"sub": dt.date(2026, 3, 5),   "status": "Finalized", "cats": {"abdominal": 1}},             # abd YTD only
        {"sub": dt.date(2026, 11, 25), "status": "Finalized", "cats": {"abdominal": 1}},             # excluded: after as_of
        {"sub": dt.date(2025, 12, 30), "status": "Finalized", "cats": {"abdominal": 1}},             # excluded: prior year
    ]
    assert sg.count_actuals(recs, as_of) == {"abd_mtd": 2, "card_mtd": 1, "abd_ytd": 3, "card_ytd": 1}


def test_render_is_dynamic_and_well_formed():
    subj, plain, html = sg.render_email(dt.date(2026, 11, 20), NOV_ACTUALS)
    assert subj == "Weekly Submissions Goals Update"
    assert "through Friday 11/20" in html
    assert "15 of 19 working days done, 4 left" in html
    assert "November" in html and "2026 Year to Date" in html
    assert "Goal to Date" in html and "Full-Month Goal" in html and sg._HEAD_BG in html
    assert ">1,494<" in html and ">-163<" in html          # month target + variance
    # Per-day pace: month abdominal 1331/15 = 88.7 actual vs 1892/19 = 99.6 goal.
    assert "Actual / Day" in html and "Goal / Day" in html
    assert ">88.7<" in html and ">99.6<" in html
    assert ">80.8<" in html and ">90.0<" in html            # YTD abdominal per-day (÷ 228 / 253)
    assert "11/26" not in html                              # no hardcoded holiday text
    # A December render carries December's label and numbers, not November's.
    _, _, dhtml = sg.render_email(dt.date(2026, 12, 4), NOV_ACTUALS)
    assert "December" in dhtml and "through Friday 12/4" in dhtml
