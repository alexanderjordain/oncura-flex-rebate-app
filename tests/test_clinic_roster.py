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


def test_apply_qb_edits_repoints_all_legals():
    nm = {"version": 1, "map": {
        "Ark Veterinary Care, Inc.": "Ark Animal Hospital - CA",
        "Ark Vet Care - North": "Ark Animal Hospital - CA",   # a 2nd legal to same wrong name
        "Some Other Clinic, LLC": "Some Other Clinic",
    }}
    updated, changed, repointed, skipped = clinic_roster.apply_qb_edits(
        nm, [("Ark Animal Hospital - CA", "Ark Veterinary Care")])
    assert changed == 1 and repointed == 2 and skipped == []
    assert updated["map"]["Ark Veterinary Care, Inc."] == "Ark Veterinary Care"
    assert updated["map"]["Ark Vet Care - North"] == "Ark Veterinary Care"
    assert updated["map"]["Some Other Clinic, LLC"] == "Some Other Clinic"  # untouched
    assert updated["version"] == 1                                          # metadata preserved


def test_reassign_payments_resolves_orphan():
    pp = {"payments": [
        {"fingerprint": "x1", "company": "FPLeasing", "kind": "scan", "contract": "43234",
         "qb_customer": "Ark Animal Hospital - CA", "amount": 611.25},
        {"fingerprint": "x2", "company": "OnePlace", "kind": "scan", "contract": "018333",
         "qb_customer": "Ark Animal Hospital - CA", "amount": 355.00},
        {"fingerprint": "x3", "company": "GreatAmerica", "kind": "flex", "contract": "z",
         "qb_customer": "Some Other Clinic", "amount": 900.00},
    ]}
    updated, n, names = clinic_roster.reassign_payments(
        pp, [("Ark Animal Hospital - CA", "Ark Veterinary Care")])
    assert n == 2 and names == ["Ark Animal Hospital - CA"]
    moved = [p for p in updated["payments"] if p["qb_customer"] == "Ark Veterinary Care"]
    assert len(moved) == 2
    assert all(p["renamed_from"] == "Ark Animal Hospital - CA" for p in moved)
    # fingerprints untouched (dedup unaffected); unrelated clinic left alone
    assert {p["fingerprint"] for p in moved} == {"x1", "x2"}
    assert any(p["qb_customer"] == "Some Other Clinic" for p in updated["payments"])
    # original not mutated
    assert pp["payments"][0]["qb_customer"] == "Ark Animal Hospital - CA"


def test_build_applies_clinic_overrides():
    ov = {"overrides": {"ark veterinary care": {
        "finance_co": "NewLane (corrected)", "contracts": "ZZZ",
        "type": "Scan / other", "active": "no"}}}
    rows = _by(clinic_roster.build(FLEX, NAME_MAP, PAY, ov))
    ark = rows["Ark Veterinary Care"]
    assert ark["Finance Co"] == "NewLane (corrected)"   # override wins over derived union
    assert ark["Contracts"] == "ZZZ"
    assert ark["Active"] == "no"
    # a clinic with no override is untouched
    assert rows["A Caring Vet"]["Finance Co"] == "GreatAmerica"


def test_apply_roster_edits_legal_names():
    orig = clinic_roster.build(FLEX, NAME_MAP, PAY)
    edited = [dict(r) for r in orig]
    for r in edited:
        if r["Clinic (QBO)"] == "Ark Veterinary Care":
            r["Legal name(s)"] = "Ark Veterinary Care, LLC"   # fix typo'd legal name
    nm, fm, ov, s = clinic_roster.apply_roster_edits(orig, edited, NAME_MAP, FLEX, {})
    assert s["legal_added"] == 1 and s["legal_removed"] == 1
    assert nm["map"].get("Ark Veterinary Care, LLC") == "Ark Veterinary Care"
    assert "Ark Veterinary Care, Inc." not in nm["map"]
    # inputs not mutated
    assert "Ark Veterinary Care, Inc." in NAME_MAP["map"]


def test_apply_roster_edits_field_routing():
    orig = clinic_roster.build(FLEX, NAME_MAP, PAY)
    edited = [dict(r) for r in orig]
    for r in edited:
        if r["Clinic (QBO)"] == "Ark Veterinary Care":        # scan -> overrides
            r["Finance Co"] = "NewLane"; r["Contracts"] = "ZZZ"; r["Active"] = "no"
        if r["Clinic (QBO)"] == "A Caring Vet":               # flex -> flex_master
            r["Finance Co"] = "OnePlace"; r["Active"] = "no"
    nm, fm, ov, s = clinic_roster.apply_roster_edits(orig, edited, NAME_MAP, FLEX, {})
    o = ov["overrides"]["ark veterinary care"]
    assert o["finance_co"] == "NewLane" and o["contracts"] == "ZZZ" and o["active"] == "no"
    fc = next(c for c in fm["clinics"] if c["qb_name"] == "A Caring Vet")
    assert fc["finance_company"] == "OnePlace" and fc["active"] is False
    assert s["flex_fields"] == 2 and s["override_fields"] == 3
    # original flex_master constant untouched
    assert next(c for c in FLEX["clinics"] if c["qb_name"] == "A Caring Vet")["finance_company"] == "GreatAmerica"


def test_apply_roster_edits_noop_when_unchanged():
    orig = clinic_roster.build(FLEX, NAME_MAP, PAY)
    nm, fm, ov, s = clinic_roster.apply_roster_edits(orig, [dict(r) for r in orig], NAME_MAP, FLEX, {})
    assert s == {"legal_added": 0, "legal_removed": 0, "flex_fields": 0, "override_fields": 0}


def test_apply_qb_edits_skips_unmapped_and_noops():
    nm = {"map": {"A Legal, LLC": "A Clinic"}}
    updated, changed, repointed, skipped = clinic_roster.apply_qb_edits(nm, [
        ("A Clinic", "A Clinic"),          # no-op (same) -> ignored
        ("Phantom Clinic", "Real Clinic"),  # no legal maps to it -> skipped
    ])
    assert changed == 0 and repointed == 0
    assert skipped == [("Phantom Clinic", "Real Clinic")]
    assert updated["map"] == {"A Legal, LLC": "A Clinic"}
