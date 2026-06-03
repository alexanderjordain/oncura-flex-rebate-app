"""Coverage for store._merge_smart — the concurrent-save merge-retry helper.

The merge runs on a 409 from GitHub's Contents API: the user's payload is
re-applied onto the current remote state so other users' simultaneous edits
survive instead of being clobbered.

Strategy: user wins on overlapping fields. Other users' additions survive.
Deletions are not preserved (acceptable — this app marks `active: false`).
"""
from core.store import _merge_smart, _merge_list


def test_dict_disjoint_keys_union():
    remote = {"a": 1}
    user = {"b": 2}
    assert _merge_smart(remote, user) == {"a": 1, "b": 2}


def test_dict_overlapping_keys_user_wins():
    remote = {"a": 1, "b": 2}
    user = {"a": 10}
    # User's value for 'a' wins; remote's 'b' (untouched by user) survives.
    assert _merge_smart(remote, user) == {"a": 10, "b": 2}


def test_nested_dict_deep_merge():
    remote = {"settings": {"x": 1, "y": 2}, "version": 1}
    user = {"settings": {"x": 99}, "version": 2}
    assert _merge_smart(remote, user) == {"settings": {"x": 99, "y": 2}, "version": 2}


def test_clinic_list_merge_by_clinic_name():
    # Alex edits Acme's rate; Tanya adds a brand-new Beta clinic in parallel.
    remote = [
        {"clinic_name": "Acme", "rate": 0.10},
        {"clinic_name": "Beta", "rate": 0.04},   # Tanya's add
    ]
    user = [
        {"clinic_name": "Acme", "rate": 0.12},   # Alex's edit
    ]
    merged = _merge_list(remote, user)
    by_name = {c["clinic_name"]: c for c in merged}
    assert by_name["Acme"]["rate"] == 0.12   # Alex's edit wins
    assert by_name["Beta"]["rate"] == 0.04   # Tanya's add preserved


def test_clinic_list_user_addition_preserved():
    remote = [{"clinic_name": "Acme", "rate": 0.10}]
    user = [
        {"clinic_name": "Acme", "rate": 0.10},
        {"clinic_name": "Charlie", "rate": 0.08},
    ]
    merged = _merge_list(remote, user)
    by_name = {c["clinic_name"]: c for c in merged}
    assert set(by_name.keys()) == {"Acme", "Charlie"}


def test_list_with_no_stable_key_returns_none():
    # A list of plain values has no key field — caller falls back to user's list
    assert _merge_list([1, 2, 3], [4, 5]) is None


def test_top_level_dict_with_clinic_list_nested():
    remote = {
        "clinics": [
            {"clinic_name": "Acme", "rate": 0.10},
            {"clinic_name": "Beta", "rate": 0.04},
        ],
        "rate_defaults": {"ultrasound_finance": 0.10},
    }
    user = {
        "clinics": [
            {"clinic_name": "Acme", "rate": 0.12},  # edit
        ],
        "rate_defaults": {"ultrasound_finance": 0.11},  # also edited the default
    }
    out = _merge_smart(remote, user)
    by_name = {c["clinic_name"]: c for c in out["clinics"]}
    assert by_name["Acme"]["rate"] == 0.12
    assert by_name["Beta"]["rate"] == 0.04         # Beta survived
    assert out["rate_defaults"]["ultrasound_finance"] == 0.11


def test_service_prices_dict_merge():
    # Two users adding different services to service_prices.json
    remote = {
        "services": {
            "Ultrasound": {"price": 300.0, "category": "ultrasound"},
            "Radiograph": {"price": 80.0, "category": "rads"},   # other user's add
        },
        "stat_fee": 125.0,
    }
    user = {
        "services": {
            "Ultrasound": {"price": 325.0, "category": "ultrasound"},  # this user's edit
        },
        "stat_fee": 125.0,
    }
    out = _merge_smart(remote, user)
    assert out["services"]["Ultrasound"]["price"] == 325.0     # user wins
    assert out["services"]["Radiograph"]["price"] == 80.0      # other user's add preserved
