"""User-safe error capture — reference IDs + traceback snapshot shape."""
from __future__ import annotations

from core import errors


def _make_exc():
    try:
        raise ValueError("boom: bad invoice row")
    except ValueError as e:
        return e


def test_capture_shape():
    err = errors.capture(_make_exc())
    assert set(err) == {"summary", "ref", "traceback"}
    assert err["summary"] == "ValueError: boom: bad invoice row"
    assert err["ref"].startswith("ERR-")
    assert "Traceback" in err["traceback"]
    assert "ValueError" in err["traceback"]


def test_reference_id_deterministic_and_distinct():
    a = errors.reference_id("tb-text-one")
    b = errors.reference_id("tb-text-one")
    c = errors.reference_id("tb-text-two")
    assert a == b
    assert a != c
    assert a.startswith("ERR-") and len(a) == 14  # ERR- + 10 hex chars


def test_capture_works_outside_except_block():
    # Session-state replay: the exception object may be rendered on a later
    # rerun, long after the except block exited.
    exc = _make_exc()
    err = errors.capture(exc)
    assert "boom: bad invoice row" in err["traceback"]


def test_capture_logs_server_side(caplog):
    import logging

    with caplog.at_level(logging.ERROR, logger="oncura.errors"):
        err = errors.capture(_make_exc())
    assert any(err["ref"] in rec.getMessage() for rec in caplog.records)
