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


def test_ledger_payment_list_merge_by_fingerprint():
    """Two operators run Stage 1 for different remittances concurrently. Both batches
    append payments with unique `fingerprint` keys. The merge must preserve BOTH —
    NOT silently drop the loser's batch. Patch-review agent SEV-1 finding."""
    remote = [
        {"fingerprint": "alex_payment_1", "company": "OnePlace",
         "kind": "flex", "contract": "4001017", "amount": 100.0},
    ]
    user = [
        {"fingerprint": "tanya_payment_1", "company": "NewLane",
         "kind": "flex", "contract": "5001234", "amount": 200.0},
    ]
    merged = _merge_list(remote, user)
    # Critical: alex_payment_1 must survive even though it's not in the user's list.
    fps = {p["fingerprint"] for p in merged}
    assert fps == {"alex_payment_1", "tanya_payment_1"}


def test_ledger_files_list_merge_by_sha256():
    """Same as fingerprint test but for the `files` sub-list which uses sha256 as key."""
    remote = [{"sha256": "fileA_sha", "filename": "remit_a.csv", "company": "OnePlace"}]
    user = [{"sha256": "fileB_sha", "filename": "remit_b.csv", "company": "NewLane"}]
    merged = _merge_list(remote, user)
    shas = {f["sha256"] for f in merged}
    assert shas == {"fileA_sha", "fileB_sha"}


def test_top_level_ledger_merge_preserves_both_operators_batches():
    """End-to-end: a full processed_payments.json shape under concurrent Stage 1 runs."""
    remote = {
        "files": [{"sha256": "alex_file", "filename": "alex.csv", "company": "OnePlace"}],
        "payments": [{"fingerprint": "alex_fp1", "company": "OnePlace", "amount": 100.0}],
    }
    user = {
        "files": [{"sha256": "tanya_file", "filename": "tanya.csv", "company": "NewLane"}],
        "payments": [{"fingerprint": "tanya_fp1", "company": "NewLane", "amount": 200.0}],
    }
    out = _merge_smart(remote, user)
    assert {f["sha256"] for f in out["files"]} == {"alex_file", "tanya_file"}
    assert {p["fingerprint"] for p in out["payments"]} == {"alex_fp1", "tanya_fp1"}


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
