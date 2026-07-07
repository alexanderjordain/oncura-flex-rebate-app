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
    assert len(ar.SONOGRAPHERS) == 11
    # Elyce Thomas is a report column but intentionally not on the To list.
    assert "Elyce Thomas" in ar.SONOGRAPHERS
    assert not any("Elyce" in r for r in to)


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
    html = ar._table_html(
        "Weekly (Goal: 50/week)",
        [("WO: 6/22/2026", {"Becky Tiner": 30, "Denice Rodriguez": 60, "Katie Heuer": 0})],
        goal=50,
    )
    assert "Becky Tiner" in html and "WO: 6/22/2026" in html and "Goal: 50/week" in html
    assert ">30<" in html          # below goal, plain
    assert ">60<" in html          # at/above goal, present
    assert ">0<" not in html       # zero renders as an empty cell
    assert ar._GOAL_BG in html     # goal-met highlight fill present


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
