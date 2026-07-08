"""Coverage for flex_overage.group_overage_spread — the PROPOSED cross-clinic
overage smoothing for multi-clinic FLEX groups (SOP-13/14).

Contractual rule (Tanya meetings, esp. PR-vets): a group's overage is spread
ACROSS its members — an over-utilizing clinic is covered first by the unused
credit sitting on its under-utilizing siblings — before anything is billed.
Independent clinics (no group_id) are untouched. The output is an audit trail
for operator review, NOT an automatic QBO write, so these tests pin the exact
move schema, the greedy donor order, and residual surfacing when the group's
overage exceeds its pooled unused.
"""
from core import flex_overage


def _row(qb, group_id=None, unused=0.0, overage=0.0):
    """Minimal recap-row shape as compute_recapture would emit (only the keys
    group_overage_spread reads)."""
    return {
        "qb_name": qb,
        "clinic_name": qb,
        "group_id": group_id,
        "unused": unused,
        "overage": overage,
    }


def test_independent_clinic_produces_no_moves():
    """A clinic with no group_id is never spread, even with an overage."""
    rows = [_row("Solo Vet", group_id=None, overage=100.0)]
    moves = flex_overage.group_overage_spread(rows)
    assert moves == []


def test_group_spread_covers_overage_from_donors_in_order():
    """One group: donorA unused=500, donorB unused=300, recipientC overage=600,
    plus an independent clinic (no group_id, overage=100).

    Expected: the independent clinic produces no move; the group pulls the full
    600 from A first (500) then B (100), leaving B with 200 unused unused and a
    residual of 0."""
    rows = [
        _row("Donor A", group_id="PR-vets", unused=500.0),
        _row("Donor B", group_id="PR-vets", unused=300.0),
        _row("Recipient C", group_id="PR-vets", overage=600.0),
        _row("Solo Vet", group_id=None, overage=100.0),
    ]
    moves = flex_overage.group_overage_spread(rows)

    # No residual — total unused (800) covers total overage (600).
    residuals = [m for m in moves if m["reason"] == "residual group overage to bill"]
    assert residuals == []

    spread = [m for m in moves if m["reason"] == "spread group overage per contract"]
    # Independent clinic contributes nothing.
    assert all(m["group"] == "PR-vets" for m in spread)
    # Total moved equals the recipient's overage.
    assert round(sum(m["amount"] for m in spread), 2) == 600.0

    # Deterministic donor order: A drained fully (500), then B tops up (100).
    assert spread == [
        {"group": "PR-vets", "from_clinic": "Donor A", "to_clinic": "Recipient C",
         "amount": 500.0, "reason": "spread group overage per contract"},
        {"group": "PR-vets", "from_clinic": "Donor B", "to_clinic": "Recipient C",
         "amount": 100.0, "reason": "spread group overage per contract"},
    ]

    # Donor B pulled only 100 of its 300 -> 200 unused remains unused.
    donor_b_pulled = sum(m["amount"] for m in spread if m["from_clinic"] == "Donor B")
    assert round(300.0 - donor_b_pulled, 2) == 200.0


def test_group_overage_exceeds_unused_surfaces_residual():
    """Group overage (900) exceeds pooled unused (800): 800 is spread from the
    donors, and a residual of 100 is surfaced as a to-bill move."""
    rows = [
        _row("Donor A", group_id="PR-vets", unused=500.0),
        _row("Donor B", group_id="PR-vets", unused=300.0),
        _row("Recipient C", group_id="PR-vets", overage=900.0),
    ]
    moves = flex_overage.group_overage_spread(rows)

    spread = [m for m in moves if m["reason"] == "spread group overage per contract"]
    residuals = [m for m in moves if m["reason"] == "residual group overage to bill"]

    # Full pool (800) is spread from both donors.
    assert round(sum(m["amount"] for m in spread), 2) == 800.0

    # Residual = overage - unused = 100, attributed to the uncovered recipient.
    assert len(residuals) == 1
    assert residuals[0]["from_clinic"] is None
    assert residuals[0]["to_clinic"] == "Recipient C"
    assert residuals[0]["amount"] == 100.0
    assert residuals[0]["group"] == "PR-vets"

    # Spread + residual exactly accounts for the recipient's full overage.
    assert round(sum(m["amount"] for m in spread) + residuals[0]["amount"], 2) == 900.0
