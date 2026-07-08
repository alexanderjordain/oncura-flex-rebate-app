"""Stage 4 — Closeout guide: pure helpers + content-builder smoke tests.

These exercise `core.flex_closeout` without a Streamlit runtime — only the pure
helpers and the markdown builders are imported/called here.
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


# ── content builders: key phrases present ─────────────────────────────────────

def test_intro_md_is_str():
    assert isinstance(flex_closeout.intro_md(), str)
    assert "Stage 4" in flex_closeout.intro_md()


def test_qbo_tieup_mentions_flex_and_zero():
    md = flex_closeout.qbo_tieup_md()
    assert isinstance(md, str)
    assert "FLEX" in md
    assert "$0" in md


def test_overage_md_generic_mentions_past_due_and_authorizenet():
    md = flex_closeout.overage_md()
    assert isinstance(md, str)
    assert "Past Due" in md
    assert "authorize.net" in md
    # No specific list passed -> points at Stage 3.
    assert "Stage 3" in md


def test_overage_md_lists_supplied_clinics():
    md = flex_closeout.overage_md(["Clinic A", "Clinic B"])
    assert "Clinic A" in md
    assert "Clinic B" in md
    assert "Past Due" in md


def test_groups_md_mentions_hurdle_and_credits():
    md = flex_closeout.groups_md(_clinics())
    assert isinstance(md, str)
    assert ("$6,000" in md) or ("6,000" in md)
    # Mohnacky members surface in the group section.
    assert "Carlsbad" in md
    # Manual spread method mentions credits only.
    assert "CREDITS ONLY" in md


def test_groups_md_renders_supplied_spread():
    md = flex_closeout.groups_md(
        _clinics(),
        group_spread=[{"amount": 123.45, "from": "Clinic A", "to": "Clinic B"}],
    )
    assert "Clinic A" in md
    assert "Clinic B" in md
    assert "123.45" in md


def test_corporate_md_lists_cityvet():
    md = flex_closeout.corporate_md(_clinics())
    assert "CityVet Preston Forest" in md
    assert "Preston Forest" in md


def test_policies_md_mentions_account_credit_and_no_refund():
    md = flex_closeout.policies_md()
    assert isinstance(md, str)
    assert "account-credit" in md
    assert "refund" in md.lower()


def test_get_off_opd_md_mentions_lock():
    md = flex_closeout.get_off_opd_md()
    assert isinstance(md, str)
    assert "lock" in md.lower()


# ── no emojis anywhere in the rendered prose ──────────────────────────────────

def test_no_emoji_in_content():
    blobs = [
        flex_closeout.intro_md(),
        flex_closeout.qbo_tieup_md(),
        flex_closeout.overage_md(["X"]),
        flex_closeout.groups_md(_clinics()),
        flex_closeout.corporate_md(_clinics()),
        flex_closeout.get_off_opd_md(),
        flex_closeout.policies_md(),
    ]
    # Emoji live above the BMP (U+1F000+) and in the misc-symbols / dingbats /
    # supplemental-symbols blocks. Plain typographic marks (an arrow "→" U+2192,
    # curly quotes, etc.) are fine — only pictographic emoji are disallowed.
    def _is_emoji(ch: str) -> bool:
        cp = ord(ch)
        return (
            cp >= 0x1F000
            or 0x2600 <= cp <= 0x27BF   # misc symbols + dingbats
            or 0x2B00 <= cp <= 0x2BFF   # misc symbols and arrows (emoji-style)
            or 0xFE00 <= cp <= 0xFE0F   # variation selectors
        )

    for text in blobs:
        for ch in text:
            assert not _is_emoji(ch), f"emoji char {ch!r} in content"
