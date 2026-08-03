"""Tests for the unified All-Clinics roster builder."""
from core import clinic_roster


FLEX = {"clinics": [
    {"qb_name": "A Caring Vet", "finance_company": "GreatAmerica",
     "contract_greatamerica": "022-1", "active": True},
    {"qb_name": "Desert Ark Veterinary Hospital AAHA - Avondale",
     "finance_company": "OnePlace", "contract_oneplace": "000123", "active": True},
]}
NAME_MAP = {"map": {
    "A Caring Vet, LLC": "A Caring Vet",              # legal -> FLEX clinic
    "Ark Veterinary Care, Inc.": "Ark Veterinary Care",  # legal -> scan clinic
}}
PAY = {"payments": [
    {"company": "FPLeasing", "kind": "scan", "contract": "43234",
     "qb_customer": "Ark Veterinary Care", "amount": 611.25, "payment_date": "2026-06-09"},
    {"company": "OnePlace", "kind": "scan", "contract": "018333",
     "qb_customer": "Ark Veterinary Care", "amount": 355.00, "payment_date": "2026-04-07"},
    {"company": "GreatAmerica", "kind": "flex", "contract": "022-1",
     "qb_customer": "A Caring Vet", "amount": 900.00, "payment_date": "2026-06-01"},
    # a paid clinic that is NOT flex and has NO name-map entry -> orphan / review
    {"company": "OnePlace", "kind": "scan", "contract": "999",
     "qb_customer": "Ark Animal Hospital - CA", "amount": 100.00, "payment_date": "2026-04-07"},
]}


def _by(rows):
    return {r["Clinic (QBO)"]: r for r in rows}


def test_union_and_typing():
    rows = _by(clinic_roster.build(FLEX, NAME_MAP, PAY))
    # FLEX clinic typed FLEX, carries payments + contract + legal name
    a = rows["A Caring Vet"]
    assert a["Type"] == "FLEX" and a["Payments"] == 1 and a["Total Paid"] == 900.0
    assert "A Caring Vet, LLC" in a["Legal name(s)"]
    # scan clinic (Ark Veterinary Care) surfaces from name_map + payments, typed scan
    ark = rows["Ark Veterinary Care"]
    assert ark["Type"] == "Scan / other"
    assert ark["Payments"] == 2 and ark["Total Paid"] == 966.25
    assert "FPLeasing" in ark["Finance Co"] and "OnePlace" in ark["Finance Co"]
    assert ark["Review"] == ""      # has a legal mapping -> not an orphan


def test_orphan_flagged_for_review():
    rows = _by(clinic_roster.build(FLEX, NAME_MAP, PAY))
    # paid but no FLEX record and no name-map target -> flagged
    phantom = rows["Ark Animal Hospital - CA"]
    assert phantom["Type"] == "Scan / other"
    assert phantom["Review"] == "yes"
    assert phantom["Legal name(s)"] == ""


def test_summarize_counts():
    rows = clinic_roster.build(FLEX, NAME_MAP, PAY)
    s = clinic_roster.summarize(rows)
    assert s["total"] == 4 and s["flex"] == 2 and s["scan"] == 2 and s["review"] == 1


def test_empty_inputs_safe():
    assert clinic_roster.build({}, {}, {}) == []
    assert clinic_roster.summarize([]) == {"total": 0, "flex": 0, "scan": 0, "review": 0}
