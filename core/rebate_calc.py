"""Rebate calculation engine.

Input: normalized OPD lines (see opd_adapter) + rebate master + config.
Output: per-clinic rebate amounts grouped by finance bucket, matching the layout of the
existing Rebate Accounts workbook (Self-Funded / OnePlace / NewLane tabs).

Rules (from Rebate_Automation_Plan.md):
  - Ultrasound scan reads are rebated at the clinic's ultrasound rate
    (5% self_funded / 10% finance).
  - Excluded from ultrasound: stat, assistance, non_ema, cancellation, overage.
  - Rads rebated at 4% flat (no exclusions). Self-funded rads is an OPEN QUESTION
    (Open Question 8) -> flagged per clinic via rads_pending.
"""
from __future__ import annotations

import pandas as pd

try:
    from rapidfuzz import fuzz, process

    _HAVE_FUZZ = True
except ImportError:
    _HAVE_FUZZ = False

ULTRASOUND_EXCLUDED = {"stat", "assistance", "non_ema", "cancellation", "overage"}

# Fuzzy match: we score only the DISTINGUISHING tokens (after stripping common
# clinic-name boilerplate). The old approach scored full strings via
# token_sort_ratio at 88% — but boilerplate words like "animal hospital" are
# most of the string, so unrelated clinics like "abell animal hospital" vs
# "ace animal hospital" cleared the bar. Stripping boilerplate first means we
# compare what actually distinguishes one clinic from another.
FUZZY_THRESHOLD = 92  # raised from 88; applies to stripped-boilerplate tokens

_BOILERPLATE_TOKENS = {
    # Pure descriptors of business type — meaningless without context.
    "animal", "animals", "pet", "pets",
    "veterinary", "vet", "vets", "veterinarian",
    # Legal-entity suffixes.
    "llc", "inc", "pllc", "pa", "pc", "ltd", "corp",
    "dba", "co", "company",
    # Honorific prefixes and articles.
    "dr", "drs", "doctor", "doctors",
    "the", "of", "at", "and", "for", "a", "an",
    "&", "-", "—", "/", ",", "+",
    # NOTE: 'hospital', 'clinic', 'center', 'group', 'services', 'care',
    # 'medical', 'associates' are DELIBERATELY NOT in this list. They look
    # like boilerplate, but the same prefix with a different venue word is
    # often a different business — e.g. 'Southwest Veterinary Hospital'
    # vs 'Southwest Veterinary Clinic'. Treating those words as discriminating
    # tokens prevents silent wrong-clinic matches.
}


def _normalize(name: str) -> str:
    """Lowercase + collapse whitespace. Same casefold rule used everywhere."""
    return " ".join(str(name or "").casefold().split())


def _strip_boilerplate(name: str) -> str:
    """Drop tokens that appear in many clinic names ('animal', 'hospital', etc.)
    so fuzzy matching scores the distinguishing tokens only."""
    out = []
    for tok in _normalize(name).split():
        clean = tok.strip(".,'\"")
        if not clean or clean in _BOILERPLATE_TOKENS:
            continue
        out.append(clean)
    return " ".join(out)


def _build_index(master_clinics: list[dict]) -> dict[str, dict]:
    """name (normalized) -> clinic record, for both clinic_name and legal_name."""
    idx = {}
    for c in master_clinics:
        for key in (c.get("clinic_name"), c.get("legal_name")):
            if key:
                idx.setdefault(_normalize(key), c)
    return idx


def match_clinic(name: str, index: dict[str, dict]):
    """Return (clinic_record, match_quality). quality in {'exact','fuzzy','none'}.

    Robust fuzzy matching: scores only the distinguishing tokens (after
    stripping clinic-name boilerplate) at a 92% similarity threshold, AND
    requires the first distinguishing token to match closely. Both gates are
    needed — overall similarity protects against random noise, first-token
    gate protects against 'abell' getting matched to 'ace'.
    """
    if not name:
        return None, "none"
    key = _normalize(name)
    if key in index:
        return index[key], "exact"
    if not _HAVE_FUZZ or not index:
        return None, "none"

    src_strip = _strip_boilerplate(key)
    if not src_strip:
        return None, "none"  # nothing distinctive left to match on

    # Build a stripped index — keys are distinguishing tokens, values point back
    # to the master record. If two master names strip to the same key, first one wins.
    stripped_index: dict[str, dict] = {}
    for k, rec in index.items():
        s = _strip_boilerplate(k)
        if s:
            stripped_index.setdefault(s, rec)

    hit = process.extractOne(src_strip, list(stripped_index.keys()),
                             scorer=fuzz.token_sort_ratio)
    if not hit or hit[1] < FUZZY_THRESHOLD:
        return None, "none"

    # First-distinguishing-token gate. Most legitimate fuzzy matches differ
    # only in suffix/abbreviation ('vet'/'veterinary', 'LLC' present/absent);
    # bad matches usually differ in the first word ('abell' vs 'ace'). Require
    # the first stripped token to match the candidate's first stripped token
    # at >= 85% similarity.
    src_first = src_strip.split()[0]
    cand_first = hit[0].split()[0]
    first_score = fuzz.ratio(src_first, cand_first)
    if first_score < 85:
        return None, "none"

    return stripped_index[hit[0]], "fuzzy"


def calculate(norm_df: pd.DataFrame, master: dict, config: dict) -> dict:
    """Compute rebates.

    Returns:
      per_clinic: DataFrame (one row per matched clinic with activity)
      unmatched:  DataFrame of OPD clinic names with no master match (+ their revenue)
      bucket_totals: dict finance_company -> total_rebate
      grand_total: float
    """
    clinics = master.get("clinics", [])
    index = _build_index(clinics)

    # Normalize the OPD clinic name BEFORE groupby so capitalization / whitespace
    # variants ("ACE Animal Hospital" vs "Ace  Animal Hospital") aggregate into
    # one bucket instead of two — the master index is already case-folded, so
    # two un-folded groupby keys would both match the same master record and
    # silently overwrite each other in the downstream per_bucket assignment.
    work = norm_df.copy()
    work["clinic"] = work["clinic"].astype(str).map(lambda s: " ".join(s.casefold().split()))

    # Aggregate OPD revenue per clinic-name x category
    agg = (
        work.groupby(["clinic", "category"], dropna=False)["amount"]
        .sum()
        .unstack(fill_value=0.0)
    )
    # Sum the feed's pre-computed rebate columns per clinic (0 for generic exports)
    feed_cols = [c for c in ["feed_us_finance", "feed_us_cash", "feed_rad_finance", "feed_rad_cash"] if c in work.columns]
    feed_agg = (
        work.groupby("clinic")[feed_cols].sum() if feed_cols else None
    )
    has_feed = bool(feed_cols) and float(work[feed_cols].abs().sum().sum()) > 0

    rows = []
    unmatched = []
    for opd_name, cat_amounts in agg.iterrows():
        rec, quality = match_clinic(opd_name, index)
        ultrasound_rev = float(cat_amounts.get("ultrasound", 0.0))
        rads_rev = float(cat_amounts.get("rads", 0.0))
        excluded_rev = float(sum(cat_amounts.get(c, 0.0) for c in ULTRASOUND_EXCLUDED))

        if rec is None:
            unmatched.append(
                {
                    "opd_clinic": opd_name,
                    "ultrasound_revenue": round(ultrasound_rev, 2),
                    "rads_revenue": round(rads_rev, 2),
                    "excluded_revenue": round(excluded_rev, 2),
                }
            )
            continue

        rate_us = float(rec.get("rate_ultrasound", 0.0))
        rate_rads = float(rec.get("rate_rads", 0.0))
        us_rebate = round(ultrasound_rev * rate_us, 2)
        rads_rebate = round(rads_rev * rate_rads, 2)
        rads_pending = (rec.get("program_type") == "self_funded") and not rec.get(
            "rads_rate_confirmed", False
        )
        rate_total = round(us_rebate + rads_rebate, 2)

        # Feed-based total: pick finance vs cash columns by program type
        is_finance = rec.get("program_type") == "finance"
        if feed_agg is not None and opd_name in feed_agg.index:
            fr = feed_agg.loc[opd_name]
            feed_us = float(fr.get("feed_us_finance" if is_finance else "feed_us_cash", 0.0))
            feed_rad = float(fr.get("feed_rad_finance" if is_finance else "feed_rad_cash", 0.0))
        else:
            feed_us = feed_rad = 0.0
        feed_total = round(feed_us + feed_rad, 2)

        rows.append(
            {
                "finance_company": rec.get("finance_company"),
                "program_type": rec.get("program_type"),
                "legal_name": rec.get("legal_name"),
                "clinic_name": rec.get("clinic_name"),
                "opd_clinic": str(opd_name),
                "match": quality,
                "ultrasound_revenue": round(ultrasound_rev, 2),
                "ultrasound_rate": rate_us,
                "ultrasound_rebate": us_rebate,
                "rads_revenue": round(rads_rev, 2),
                "rads_rate": rate_rads,
                "rads_rebate": rads_rebate,
                "rads_pending_confirmation": rads_pending,
                "excluded_revenue": round(excluded_rev, 2),
                "rebate_rate_based": rate_total,
                "rebate_feed_based": feed_total,
                "variance": round(rate_total - feed_total, 2),
                "total_rebate": rate_total,
            }
        )

    per_clinic = pd.DataFrame(rows)
    if not per_clinic.empty:
        per_clinic = per_clinic.sort_values(
            ["finance_company", "clinic_name"], na_position="last"
        ).reset_index(drop=True)
        bucket_totals = (
            per_clinic.groupby("finance_company")["total_rebate"].sum().round(2).to_dict()
        )
        grand_total = round(float(per_clinic["total_rebate"].sum()), 2)
        feed_grand_total = round(float(per_clinic["rebate_feed_based"].sum()), 2)
        total_variance = round(grand_total - feed_grand_total, 2)
    else:
        bucket_totals = {}
        grand_total = feed_grand_total = total_variance = 0.0

    return {
        "per_clinic": per_clinic,
        "unmatched": pd.DataFrame(unmatched),
        "bucket_totals": bucket_totals,
        "grand_total": grand_total,
        "has_feed": bool(has_feed),
        "feed_grand_total": feed_grand_total,
        "total_variance": total_variance,
    }


def remittance_frame(per_clinic: pd.DataFrame, finance_company: str, period_label: str) -> pd.DataFrame:
    """Per-finance-partner remittance file: Legal Name + DBA + Period + Amount.
    Finance partners require both legal and DBA names on remittance.
    """
    if per_clinic.empty:
        return pd.DataFrame(columns=["Finance Company", "Legal Name", "DBA", "Period", "Rebate Amount"])
    sub = per_clinic[
        (per_clinic["finance_company"] == finance_company) & (per_clinic["total_rebate"] > 0)
    ]
    return pd.DataFrame(
        {
            "Finance Company": finance_company,
            "Legal Name": sub["legal_name"],
            "DBA": sub["clinic_name"],
            "Period": period_label,
            "Rebate Amount": sub["total_rebate"],
        }
    ).reset_index(drop=True)
