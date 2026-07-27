"""Tests for the WOL training-email logic: recipients, OPD clinic matching, and the
'still needs training' membership rule. Network-free (pure helpers only)."""
from core import wol_training_email as wol


def test_recipients_hardcoded_defaults():
    to = wol.recipients("to")
    cc = wol.recipients("cc")
    assert len(to) == 5 and len(cc) == 1
    assert to[0] == "Carla Erickson <carla@oncurapartners.com>"
    assert cc[0] == "Melissa Colpitts <mcolpitts@oncurapartners.com>"
    assert all(r.count("<") == 1 and r.endswith(">") for r in to + cc)


def test_clean_clinic_name_strips_noise():
    # trailing OPD code, franchise prefix, parenthetical, and "Lost" tag all removed.
    assert wol._clean_clinic_name("NVA- Marbletown Animal Hospital - MTAH12484") == \
        "marbletown animal hospital"
    assert wol._clean_clinic_name("Murphy Road Animal Hospital (VetCor) - MRAH75048") == \
        "murphy road animal hospital"
    assert wol._clean_clinic_name("Encanto Animal Hospital - EAH91754 Lost") == \
        "encanto animal hospital"


def test_match_opd_id_prefers_business_code():
    by_code = {"SVS38583": 100, "NCAH34102": 200, "CAC40601": 300}
    by_name = {"woodbury animal hospital": 400, "bayshore veterinary clinic": 500}
    # trailing code with the standard " - CODE" suffix
    assert wol._match_opd_id("Sparta Veterinary Services - SVS38583", by_code, by_name) == (100, True)
    # code glued to the word with no leading space ("Care- CAC40601")
    assert wol._match_opd_id("Cornerstone Animal Care- CAC40601", by_code, by_name) == (300, True)


def test_match_opd_id_name_fallback_and_prefix():
    by_code = {"SVS38583": 100}
    by_name = {"woodbury animal hospital": 400, "bayshore veterinary clinic": 500}
    # no code -> cleaned-name match
    assert wol._match_opd_id("Woodbury Animal Hospital", by_code, by_name) == (400, True)
    # franchise prefix stripped before the name lookup
    assert wol._match_opd_id("CAH-Bayshore Veterinary Clinic", by_code, by_name) == (500, True)
    # nothing matches -> flagged unverified
    assert wol._match_opd_id("Totally Unknown Clinic - ZZZ99999", by_code, by_name) == (None, False)


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
