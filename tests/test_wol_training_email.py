"""Tests for the WOL training-email logic: recipients, OPD clinic matching, the
grace-window cert count, and the 'still needs training' rule. Network-free."""
import datetime as dt

from core import wol_training_email as wol


def test_recipients_hardcoded_defaults():
    to = wol.recipients("to")
    cc = wol.recipients("cc")
    assert len(to) == 5 and len(cc) == 1
    assert to[0] == "Carla Erickson <carla@oncurapartners.com>"
    assert cc[0] == "Melissa Colpitts <mcolpitts@oncurapartners.com>"
    assert all(r.count("<") == 1 and r.endswith(">") for r in to + cc)


def test_clean_clinic_name_strips_noise():
    assert wol._clean_clinic_name("NVA- Marbletown Animal Hospital - MTAH12484") == \
        "marbletown animal hospital"
    assert wol._clean_clinic_name("Murphy Road Animal Hospital (VetCor) - MRAH75048") == \
        "murphy road animal hospital"
    assert wol._clean_clinic_name("Encanto Animal Hospital - EAH91754 Lost") == \
        "encanto animal hospital"
    # corporate suffixes stripped so HubSpot/OPD name forms line up
    assert wol._clean_clinic_name("Bayshore Veterinary Clinic Pc") == "bayshore veterinary clinic"
    assert wol._clean_clinic_name("The Cat Doctor LLC") == "the cat doctor"


def test_match_opd_id_prefers_business_code():
    by_code = {"SVS38583": [100], "CAC40601": [300]}
    by_name = {"woodbury animal hospital": [400]}
    assert wol._match_opd_id("Sparta Veterinary Services - SVS38583", by_code, by_name) == ([100], True)
    # code glued to the word with no leading space ("Care- CAC40601")
    assert wol._match_opd_id("Cornerstone Animal Care- CAC40601", by_code, by_name) == ([300], True)


def test_match_opd_id_finds_code_around_status_text():
    # code AFTER a "No longer active" tag (the Brookside bug) must still resolve
    assert wol._match_opd_id(
        "NVA- Brookside Animal Hospital - No longer active - BAH33076",
        {"BAH33076": [999]}, {}) == ([999], True)
    # code BEFORE a "Lost (2025)" tag
    assert wol._match_opd_id(
        "Woof Animal Hospital - WAH78626 Lost (2025)", {"WAH78626": [777]}, {}) == ([777], True)


def test_match_opd_id_name_fallback_and_prefix():
    by_name = {"woodbury animal hospital": [400], "bayshore veterinary clinic": [500],
               "the cat doctor": [610]}
    assert wol._match_opd_id("Woodbury Animal Hospital", {}, by_name) == ([400], True)
    # franchise prefix stripped before the name lookup
    assert wol._match_opd_id("CAH-Bayshore Veterinary Clinic", {}, by_name) == ([500], True)
    # corporate suffix + trailing short code cleaned away
    assert wol._match_opd_id("The Cat Doctor LLC - TCD", {}, by_name) == ([610], True)
    # nothing matches -> flagged unverified
    assert wol._match_opd_id("Totally Unknown Clinic - ZZZ99999", {}, by_name) == ([], False)


def test_match_opd_id_ambiguous_name_is_unverified():
    # two OPD clinics share a cleaned name -> do NOT guess; keep + flag unverified
    assert wol._match_opd_id("Cactus Pet Hospital", {}, {"cactus pet hospital": [11, 22]}) == ([], False)


def test_match_opd_id_code_collision_unions_ids():
    # a shared business code (same entity family) resolves to all its ids to union over
    assert wol._match_opd_id("Blvd Vet - BVLE60625", {"BVLE60625": [1, 2, 3]}, {}) == ([1, 2, 3], True)


def test_count_certs_grace_window():
    install = dt.date(2025, 6, 1)
    A = {"abdominal": True, "cardiac": False}
    C = {"abdominal": False, "cardiac": True}
    certs = [
        (A, dt.date(2025, 1, 1)),   # 151 days before install -> within the 180d grace
        (C, dt.date(2024, 6, 1)),   # ~365 days before -> stale, excluded
        (C, dt.date(2025, 8, 1)),   # after install -> counts
    ]
    assert wol._count_certs(certs, install) == (1, 1)
    # a tighter grace drops the 151-day-early abdominal
    assert wol._count_certs(certs, install, grace_days=90) == (0, 1)


def test_needs_training_rule():
    # sold both, none certified -> needs both
    assert wol._needs_training(2, 2, 0, 0) == (True, True)
    # sold both, both certified -> trained, drops off
    assert wol._needs_training(2, 2, 2, 1) == (False, False)
    # abdominal done, cardiac still owed (the common partial case)
    assert wol._needs_training(2, 2, 3, 0) == (False, True)
    # only abdominal sold -> never chased for cardiac it never bought
    assert wol._needs_training(4, 0, 0, 0) == (True, False)
    assert wol._needs_training(4, 0, 1, 0) == (False, False)
