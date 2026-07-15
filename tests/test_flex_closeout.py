"""Stage 4 — Closeout WIZARD: pure-helper + worklist tests.

These exercise `core.flex_closeout` without a Streamlit runtime — only the pure
helpers (`group_members`, `corporate_clinics`), the `build_worklist` assembler,
and the `STEPS` table are imported/called here. `render_step` needs a Streamlit
runtime, so it is only import-checked (via the module import), not rendered.
"""
from __future__ import annotations

from core import flex_closeout


# ── synthetic flex_clinics fixture ────────────────────────────────────────────
# Mirrors the shape of data/flex_master.json "clinics": each dict has a qb_name
# and a group_id (None when standalone).

def _clinics():
    return [
        {"qb_name": "Mohnacky Animal Hospital of Carlsbad", "group_id": "mohnacky"},
        {"qb_name": "Mohnacky Veterinary Hospital of Vista", "group_id": "mohnacky"},
        {"qb_name": "Mohnacky Veterinary Hospital of Escondido", "group_id": "mohnacky"},
        {"qb_name": "Clinica Veterinaria Gardenville", "group_id": "pr-vets"},
        {"qb_name": "Hospital Veterinario Condado", "group_id": "pr-vets"},
        {"qb_name": "River Trail Animal Hospital - Tulsa", "group_id": "river-trail"},
        {"qb_name": "River Trail Animal Hospital - Memorial", "group_id": "river-trail"},
        {"qb_name": "CityVet Preston Forest", "group_id": None},
        {"qb_name": "CityVet Whiterock", "group_id": None},
        {"qb_name": "A Caring Vet", "group_id": None},
    ]


# ── group_members ─────────────────────────────────────────────────────────────

def test_group_members_mohnacky():
    g = flex_closeout.group_members(_clinics())
    assert g["mohnacky"] == [
        "Mohnacky Animal Hospital of Carlsbad",
        "Mohnacky Veterinary Hospital of Vista",
        "Mohnacky Veterinary Hospital of Escondido",
    ]


def test_group_members_pr_vets():
    g = flex_closeout.group_members(_clinics())
    assert g["pr-vets"] == [
        "Clinica Veterinaria Gardenville",
        "Hospital Veterinario Condado",
    ]


def test_group_members_river_trail():
    g = flex_closeout.group_members(_clinics())
    assert g["river-trail"] == [
        "River Trail Animal Hospital - Tulsa",
        "River Trail Animal Hospital - Memorial",
    ]


def test_group_members_skips_ungrouped():
    g = flex_closeout.group_members(_clinics())
    # Standalone clinics (group_id None) never create a key.
    assert None not in g
    assert set(g.keys()) == {"mohnacky", "pr-vets", "river-trail"}


def test_group_members_empty_input():
    assert flex_closeout.group_members([]) == {}
    assert flex_closeout.group_members(None) == {}


# ── corporate_clinics ─────────────────────────────────────────────────────────

def test_corporate_clinics_finds_cityvet():
    corp = flex_closeout.corporate_clinics(_clinics())
    assert corp == ["CityVet Preston Forest", "CityVet Whiterock"]


def test_corporate_clinics_case_insensitive():
    corp = flex_closeout.corporate_clinics(
        [{"qb_name": "cityvet lowercase"}, {"qb_name": "Some Other Clinic"}]
    )
    assert corp == ["cityvet lowercase"]


# ── STEPS table ───────────────────────────────────────────────────────────────

def test_steps_has_four_expected_keys():
    keys = [k for k, _ in flex_closeout.STEPS]
    assert keys == ["clinics", "tieup", "overages", "groups"]
    # Each entry is (key, human label).
    for k, label in flex_closeout.STEPS:
        assert isinstance(k, str) and isinstance(label, str) and label


# ── build_worklist ────────────────────────────────────────────────────────────
# Synthetic Stage-3 recap rows (subset of core.flex_unused.compute_recapture keys).

def _recap_rows():
    return [
        # unused: threshold > activity, no overage.
        {
            "qb_name": "A Caring Vet", "group_id": None,
            "finance_company": "GreatAmerica",
            "quarterly_threshold": 3000.0, "quarter_activity": 1200.0,
            "unused": 1800.0, "overage": 0.0, "payments_in_quarter": 3,
        },
        # overage: activity > threshold.
        {
            "qb_name": "Hospital Veterinario Condado", "group_id": "pr-vets",
            "finance_company": "OnePlace",
            "quarterly_threshold": 2000.0, "quarter_activity": 2600.0,
            "unused": 0.0, "overage": 600.0, "payments_in_quarter": 4,
        },
        # zero: activity == threshold, nothing unused or over.
        {
            "qb_name": "River Trail Animal Hospital - Tulsa", "group_id": "river-trail",
            "finance_company": "NewLane",
            "quarterly_threshold": 1500.0, "quarter_activity": 1500.0,
            "unused": 0.0, "overage": 0.0, "payments_in_quarter": 3,
        },
        # corporate (CityVet) with an overage — should be flagged corporate + past_due.
        {
            "qb_name": "CityVet Preston Forest", "group_id": None,
            "finance_company": "OnePlace",
            "quarterly_threshold": 2500.0, "quarter_activity": 2900.0,
            "unused": 0.0, "overage": 400.0, "payments_in_quarter": 3,
        },
    ]


def test_build_worklist_outcomes():
    wl = flex_closeout.build_worklist(_clinics(), _recap_rows())
    by_name = {c["qb_name"]: c for c in wl["clinics"]}

    assert by_name["A Caring Vet"]["outcome"] == "unused"
    assert by_name["A Caring Vet"]["past_due"] is False

    assert by_name["Hospital Veterinario Condado"]["outcome"] == "overage"
    assert by_name["Hospital Veterinario Condado"]["past_due"] is True

    assert by_name["River Trail Animal Hospital - Tulsa"]["outcome"] == "zero"
    assert by_name["River Trail Animal Hospital - Tulsa"]["past_due"] is False


def test_build_worklist_passes_through_fields():
    wl = flex_closeout.build_worklist(_clinics(), _recap_rows())
    caring = next(c for c in wl["clinics"] if c["qb_name"] == "A Caring Vet")
    assert caring["group"] is None
    assert caring["finance_company"] == "GreatAmerica"
    assert caring["threshold"] == 3000.0
    assert caring["activity"] == 1200.0
    assert caring["payments"] == 3
    assert caring["unused"] == 1800.0
    assert caring["overage"] == 0.0

    condado = next(c for c in wl["clinics"]
                   if c["qb_name"] == "Hospital Veterinario Condado")
    assert condado["group"] == "pr-vets"


def test_build_worklist_counts():
    wl = flex_closeout.build_worklist(_clinics(), _recap_rows())
    assert wl["counts"] == {"total": 4, "unused": 1, "overage": 2, "zero": 1}


def test_build_worklist_overage_bucket():
    wl = flex_closeout.build_worklist(_clinics(), _recap_rows())
    names = {c["qb_name"] for c in wl["overage_clinics"]}
    assert names == {"Hospital Veterinario Condado", "CityVet Preston Forest"}
    # Every overage clinic is flagged past_due.
    assert all(c["past_due"] for c in wl["overage_clinics"])


def test_build_worklist_flags_cityvet_corporate():
    wl = flex_closeout.build_worklist(_clinics(), _recap_rows())
    cityvet = next(c for c in wl["clinics"]
                   if c["qb_name"] == "CityVet Preston Forest")
    assert cityvet["is_corporate"] is True
    # Non-CityVet clinics are not corporate.
    caring = next(c for c in wl["clinics"] if c["qb_name"] == "A Caring Vet")
    assert caring["is_corporate"] is False
    # The corporate bucket contains exactly the CityVet closing clinic.
    assert [c["qb_name"] for c in wl["corporate"]] == ["CityVet Preston Forest"]


def test_build_worklist_group_moves_passthrough():
    moves = [{"amount": 123.45, "from": "Clinic A", "to": "Clinic B"}]
    wl = flex_closeout.build_worklist(_clinics(), _recap_rows(), group_spread=moves)
    assert wl["group_moves"] == moves


def test_build_worklist_group_moves_default_empty():
    wl = flex_closeout.build_worklist(_clinics(), _recap_rows())
    assert wl["group_moves"] == []


def test_build_worklist_empty_recap():
    wl = flex_closeout.build_worklist(_clinics(), [])
    assert wl["clinics"] == []
    assert wl["overage_clinics"] == []
    assert wl["corporate"] == []
    assert wl["counts"] == {"total": 0, "unused": 0, "overage": 0, "zero": 0}


def test_build_worklist_handles_none_recap():
    wl = flex_closeout.build_worklist(_clinics(), None)
    assert wl["counts"]["total"] == 0


# ── recap_from_ledger (Stage 4 loads a recorded month) ────────────────────────

def test_recap_from_ledger_builds_rows_from_recorded_stage3():
    clinics = [
        {"qb_name": "Clinic A", "clinic_name": "Clinic A", "finance_company": "GreatAmerica",
         "quarterly_threshold": 6000, "group_id": None},
        {"qb_name": "Clinic B", "clinic_name": "Clinic B", "finance_company": "OnePlace",
         "quarterly_threshold": 5700, "group_id": "G1"},
    ]
    pays = [
        {"kind": "unused_invoice", "qb_customer": "Clinic A", "amount": 1200.0,
         "payment_date": "06/30/2026"},                       # US date (unused invoices)
        {"kind": "direct_overage", "qb_customer": "Clinic B", "amount": 800.0,
         "payment_date": "2026-06-01"},                        # ISO date (overages)
        {"kind": "direct_overage", "qb_customer": "Clinic C", "amount": 50.0,
         "payment_date": "2026-05-01"},                        # wrong month -> excluded
        {"kind": "flex", "qb_customer": "Clinic A", "amount": 900.0,
         "payment_date": "2026-06-15"},                        # not a Stage 3 kind -> excluded
    ]
    rows = flex_closeout.recap_from_ledger(clinics, pays, 2026, 6)
    by = {r["qb_name"]: r for r in rows}
    assert set(by) == {"Clinic A", "Clinic B"}
    assert by["Clinic A"]["unused"] == 1200.0 and by["Clinic A"]["overage"] == 0.0
    assert by["Clinic A"]["finance_company"] == "GreatAmerica"
    assert by["Clinic A"]["quarterly_threshold"] == 6000
    assert by["Clinic A"]["quarter_activity"] == 4800.0        # threshold - unused + overage
    assert by["Clinic B"]["overage"] == 800.0 and by["Clinic B"]["group_id"] == "G1"
    # A month with no recorded Stage 3 output returns empty.
    assert flex_closeout.recap_from_ledger(clinics, pays, 2026, 1) == []
