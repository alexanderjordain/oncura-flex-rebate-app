"""Tests for the sonographer assistance report + the Cc/HTML email plumbing."""
import datetime as dt

from core import assist_report as ar
from core import accounting_handoff as ah


def test_recipients_shape():
    to = ar.recipients("to")
    cc = ar.recipients("cc")
    assert len(to) == 12 and len(cc) == 3
    assert to[0] == "Melissa Colpitts <mcolpitts@oncurapartners.com>"
    assert cc[0] == "Marty McCutchen <marty@oncurapartners.com>"
    assert all(r.count("<") == 1 and r.endswith(">") for r in to + cc)
    assert len(ar.SONOGRAPHERS) == 10
    # Elyce Thomas is a tracked column and now also on the To list (added per request).
    assert "Elyce Thomas" in ar.SONOGRAPHERS
    assert any("Elyce Thomas <ethomas@oncurapartners.com>" == r for r in to)
    # Liza Gonzalez was removed from both the tracked columns and the recipients.
    assert "Liza Gonzalez" not in ar.SONOGRAPHERS
    assert not any("Liza" in r for r in to)


def test_eastern_date_conversion():
    # 02:00Z in June (EDT, -4) is the previous calendar day in Eastern.
    assert ar._eastern_date("2026-06-22T02:00:00.000Z") == dt.date(2026, 6, 21)
    # Midday UTC stays the same Eastern day.
    assert ar._eastern_date("2026-06-22T18:00:00Z") == dt.date(2026, 6, 22)


def test_month_chunks_contiguous_and_bounded():
    ch = ar._month_chunks(dt.date(2026, 1, 15), dt.date(2026, 3, 3))
    assert ch[0][0] == dt.date(2026, 1, 15)
    assert ch[-1][1] == dt.date(2026, 3, 3)
    for (a, b), (c, d) in zip(ch, ch[1:]):
        assert b == c            # non-overlapping + no gaps


def test_rows_trailing_windows():
    counts = {
        "weekly": {"2026-06-22": {"Becky Tiner": 30, "Katie Heuer": 16}},
        "daily": {"2026-06-23": {"Becky Tiner": 6}},
    }
    # Tue 2026-07-07 -> last complete week Monday is 2026-06-29 (this week excluded)
    wk, dy = ar._rows(counts, dt.date(2026, 7, 7))
    assert len(wk) == ar.WEEKLY_WEEKS == 15
    assert len(dy) == ar.DAILY_DAYS == 15
    labels = [lbl for lbl, _ in wk]
    assert labels[-1] == "WO: 6/29/2026"     # last complete week present
    assert "WO: 6/22/2026" in labels
    assert "WO: 7/6/2026" not in labels       # current (incomplete) week excluded
    assert dict(wk)["WO: 6/22/2026"]["Becky Tiner"] == 30
    assert dy[-1][0] == "7/6/2026"            # daily ends yesterday
    assert dy[0][0] == "6/22/2026"            # 15 days back


def test_table_html_highlights_goal_and_blanks_zero():
    sch = ar.infer_schedules({})   # Becky goal 37.5, Denice 50, Katie 37.5
    html = ar._table_html(
        "Weekly (Goal: 50/week at full-time, prorated for part-time)",
        [("WO: 6/22/2026", {"Becky Tiner": 30, "Denice Rodriguez": 60, "Katie Heuer": 0})],
        scheds=sch,
    )
    assert "Becky Tiner" in html and "WO: 6/22/2026" in html and "50/week" in html
    assert ">30<" in html          # Becky 30 < her 37.5 goal -> plain
    assert ">60<" in html          # Denice 60 >= her 50 goal -> present (highlighted)
    assert ">0<" not in html       # zero renders as an empty cell
    assert ar._GOAL_BG in html     # goal-met highlight fill present
    assert ar._HEAD_BG in html     # teal header present


def test_prorated_goals_match_hours():
    sch = ar.infer_schedules({})
    assert sch["Becky Tiner"]["weekly_goal"] == 37.5 and not sch["Becky Tiner"]["full_time"]
    assert sch["Katie Heuer"]["weekly_goal"] == 37.5 and not sch["Katie Heuer"]["full_time"]
    assert sch["Megan DuCasse"]["weekly_goal"] == 25 and not sch["Megan DuCasse"]["full_time"]
    assert sch["Luis Romero"]["weekly_goal"] == 50 and sch["Luis Romero"]["full_time"]
    assert sch["Denice Rodriguez"]["weekly_goal"] == 50 and sch["Denice Rodriguez"]["full_time"]
    # 7 full-time x 50 + (37.5 + 37.5 + 25) part-time = 450
    assert sum(sch[s]["weekly_goal"] for s in ar.SONOGRAPHERS) == 450
    assert sum(1 for s in ar.SONOGRAPHERS if sch[s]["full_time"]) == 7


def test_goal_perf_table_group_total_and_pct():
    sch = ar.infer_schedules({})
    wc = {s: 50 for s in ar.SONOGRAPHERS if sch[s]["full_time"]}   # 7 x 50 = 350
    wc["Becky Tiner"] = 30; wc["Katie Heuer"] = 25; wc["Megan DuCasse"] = 18  # -> 423 total
    html = ar._goal_perf_html(sch, wc, "10/6/2026")
    assert "Weekly Goal Performance" in html
    assert "Group Total" in html and "% of Goal" in html
    assert "40 hours (7)" in html and "Modified (3)" in html
    assert "94%" in html           # 423 / 450


def test_regular_cells_transparent_only_goal_and_header_filled():
    # A plain (below-goal) cell carries no background fill at all.
    plain = ar._cell("30")
    assert "bgcolor" not in plain and "background:" not in plain
    # Goal + header cells DO carry a fill.
    assert f'bgcolor="{ar._GOAL_BG}"' in ar._cell(60, ar._GOAL_BG, ar._GOAL_TX, bold=True)
    assert f'bgcolor="{ar._HEAD_BG}"' in ar._cell("Becky Tiner", ar._HEAD_BG, ar._HEAD_TX, bold=True)


def test_eml_supports_cc_and_html_body():
    raw = ah._build_eml_bytes(
        "Subject", "plain fallback", "a@x.com, b@x.com", None,
        cc="c@x.com", html_body="<table><tr><td>ASSIST</td></tr></table>",
    ).decode("utf-8", "ignore")
    assert "a@x.com" in raw and "b@x.com" in raw
    assert "c@x.com" in raw                  # Cc header present
    assert "text/html" in raw                # html alternative present
    assert "X-Unsent" in raw                 # opens in compose mode


def test_mailto_link_includes_cc():
    link = ah.mailto_link("Sub", "Body", "to@x.com", "cc@x.com")
    assert link.startswith("mailto:to@x.com?")
    assert "cc=cc%40x.com" in link


def test_render_handoff_defaults_unchanged():
    # Existing callers pass no to/cc: signature still accepts the old positional args.
    import inspect
    sig = inspect.signature(ah.render_handoff)
    assert sig.parameters["to"].default is None
    assert sig.parameters["cc"].default is None
    assert sig.parameters["html_body"].default is None
