"""End-to-end app tests via Streamlit's AppTest harness.

These boot the real app.py (st.navigation + auth gate + theme) in-process and
render every registered page. They catch the class of failure the unit suite
can't: a page that imports fine but explodes on first render (bad widget
wiring, missing session-state default, loader regression).

Auth is controlled by pre-seeding `auth_role` in session state — NOT via the
FLEXREBATE_LOCAL env bypass, which is ignored whenever a local secrets.toml
provides APP_PASSWORD (the gate would render and every page assertion would
pass vacuously against the login screen).

Slower than the unit tests (seconds, not milliseconds) — deselect with
`pytest -m "not e2e"` when iterating on calculation modules.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.e2e

PAGES = [
    "pages/home.py",
    "pages/rebate_cycle.py",
    "pages/rebate_master.py",
    "pages/flex_cycle.py",
    "pages/flex_master.py",
    "pages/flex_tutorial.py",
    "pages/settings.py",
    "pages/audit_log.py",
]

TIMEOUT = 30


def _boot_authed() -> AppTest:
    """Boot the app already authenticated as admin."""
    at = AppTest.from_file("app.py", default_timeout=TIMEOUT)
    at.session_state["auth_role"] = "alex"
    at.run()
    return at


def _exceptions(at: AppTest) -> list[str]:
    return [str(e.value) for e in at.exception]


def _role(at: AppTest):
    # AppTest's session-state proxy supports [] but not .get()
    try:
        return at.session_state["auth_role"]
    except (KeyError, AttributeError):
        return None


def _rendered_something(at: AppTest) -> bool:
    """True if the run produced visible page content (guards against the
    vacuous pass where st.stop() halted the script before the page body)."""
    return len(at.markdown) > 0 or len(at.title) > 0


def test_app_boots_clean():
    at = _boot_authed()
    assert _exceptions(at) == []
    assert at.session_state["auth_role"] == "alex"
    assert _rendered_something(at)


def test_every_page_renders_without_exception():
    # One instance, sequential switches — also exercises page-to-page
    # navigation the way a real session does.
    at = _boot_authed()
    failures = {}
    for page in PAGES:
        at.switch_page(page)
        at.run()
        exc = _exceptions(at)
        if exc:
            failures[page] = exc
        elif not _rendered_something(at):
            failures[page] = ["rendered no content (st.stop()? auth gate?)"]
    assert not failures, f"pages failed to render: {failures}"


def test_login_gate_blocks_unauthenticated():
    # No pre-seeded role: the gate must render (password field) and st.stop()
    # before any page content. Works whether APP_PASSWORD comes from the
    # local secrets.toml (dev machines) or at.secrets (CI has no file).
    at = AppTest.from_file("app.py", default_timeout=TIMEOUT)
    at.secrets["APP_PASSWORD"] = "e2e-test-pw"
    at.run()
    assert _exceptions(at) == []
    assert not _role(at)
    assert len(at.text_input) == 1, "expected exactly the password field"

    at.text_input[0].input("definitely-not-the-password")
    at.button[0].set_value(True)
    at.run()
    assert not _role(at), "wrong password must not authenticate"
