"""Unified "All Clinics" roster — a read-only union of every clinic the app knows.

Combines three sources keyed by the canonical QBO customer name:
  1. flex_master.json   -> the FLEX / pass-through program clinics (type = FLEX),
                           with their finance partner + contract IDs.
  2. name_map.json      -> every legal->QBO mapping committed during Stage 1
                           (so the roster grows automatically as new clinics match).
  3. processed_payments -> finance company, contracts, and payment totals actually
                           seen, which enriches scan clinics and surfaces orphans.

This is deliberately NOT flex_master: scan clinics have no FLEX thresholds/credits
and must stay out of the Stage 2/3 credit-memo and recapture math. This view only
reports; it never feeds a calculation.

A clinic is flagged `review=True` when it has payments but is neither a FLEX clinic
nor a current name-map target -- i.e., orphaned/stale payments (for example a name
that was mis-mapped and has since been corrected), which is exactly the kind of gap
worth a human look.
"""
from __future__ import annotations


def _norm(s):
    return " ".join(str(s or "").strip().lower().split())


def build(flex_master: dict, name_map: dict, processed_payments: dict) -> list[dict]:
    """Return the unified roster as a list of display-ready row dicts, sorted by
    clinic name. Pure function (no I/O) so it is unit-testable."""
    flex = {}          # norm(qb_name) -> flex_master record
    for c in (flex_master or {}).get("clinics", []):
        qb = (c.get("qb_name") or c.get("clinic_name") or "").strip()
        if qb:
            flex[_norm(qb)] = c

    # canonical qb_name -> set of legal names that map to it
    legal_by_qb: dict = {}
    for legal, qb in ((name_map or {}).get("map", {}) or {}).items():
        legal_by_qb.setdefault(_norm(qb), {"display": (qb or "").strip(), "legals": set()})
        legal_by_qb[_norm(qb)]["legals"].add(legal.strip())

    # payment aggregates per canonical qb_customer
    pay: dict = {}
    for p in (processed_payments or {}).get("payments", []):
        qb = (p.get("qb_customer") or "").strip()
        if not qb:
            continue
        k = _norm(qb)
        agg = pay.setdefault(k, {"display": qb, "companies": set(), "contracts": set(),
                                 "count": 0, "total": 0.0, "last": ""})
        if p.get("company"):
            agg["companies"].add(str(p["company"]))
        if p.get("contract"):
            agg["contracts"].add(str(p["contract"]))
        agg["count"] += 1
        try:
            agg["total"] += float(p.get("amount") or 0)
        except (TypeError, ValueError):
            pass
        pd = (p.get("payment_date") or "")[:10]
        if pd > agg["last"]:
            agg["last"] = pd

    keys = set(flex) | set(legal_by_qb) | set(pay)
    rows = []
    for k in keys:
        frec = flex.get(k)
        display = (frec.get("qb_name") if frec else None) \
            or (legal_by_qb.get(k, {}).get("display")) \
            or (pay.get(k, {}).get("display")) or k
        is_flex = frec is not None
        pinfo = pay.get(k, {})
        companies = set(pinfo.get("companies", set()))
        if frec and frec.get("finance_company"):
            companies.add(str(frec["finance_company"]))
        contracts = set(pinfo.get("contracts", set()))
        if frec:
            for fld in ("contract_greatamerica", "contract_newlane", "contract_oneplace"):
                if frec.get(fld):
                    contracts.add(str(frec[fld]))
        legals = legal_by_qb.get(k, {}).get("legals", set())
        has_payments = pinfo.get("count", 0) > 0
        # orphan/stale: paid, but not a FLEX clinic and no current legal mapping
        review = has_payments and not is_flex and not legals
        rows.append({
            "Clinic (QBO)": display,
            "Type": "FLEX" if is_flex else "Scan / other",
            "Finance Co": ", ".join(sorted(companies)),
            "Contracts": ", ".join(sorted(contracts)),
            "Legal name(s)": "; ".join(sorted(legals)),
            "Payments": pinfo.get("count", 0),
            "Total Paid": round(pinfo.get("total", 0.0), 2),
            "Last Payment": pinfo.get("last", ""),
            "Active": ("" if not is_flex else ("yes" if frec.get("active", True) else "no")),
            "Review": "yes" if review else "",
        })
    rows.sort(key=lambda r: r["Clinic (QBO)"].lower())
    return rows


def apply_qb_edits(name_map: dict, changes):
    """Repoint QBO customer names in the name-map. `changes` is [(old_qb, new_qb), ...]
    (typically the rows whose 'Clinic (QBO)' was edited on the roster). Every legal
    name currently resolving to old_qb is repointed to new_qb. Returns
    (updated_name_map, clinics_changed, legals_repointed, skipped) where `skipped` is
    [(old, new), ...] for names with no legal mapping (FLEX-only rows -> edit on the
    FLEX Clinic Roster; payment-only orphans -> reclass in QBO). Pure; no I/O."""
    mp = dict((name_map or {}).get("map", {}) or {})
    changed = repointed = 0
    skipped = []
    for old, new in changes:
        new = (new or "").strip()
        old = (old or "").strip()
        if not new or new == old:
            continue
        legals = [legal for legal, qb in mp.items() if qb == old]
        if not legals:
            skipped.append((old, new))
            continue
        for legal in legals:
            mp[legal] = new
        changed += 1
        repointed += len(legals)
    return {**(name_map or {}), "map": mp}, changed, repointed, skipped


def reassign_payments(processed_payments: dict, changes):
    """Resolve payment-only orphans: for each (old_qb, new_qb) in `changes`, reassign
    every ledger payment whose qb_customer == old_qb to new_qb, tagging the original as
    `renamed_from` for the audit trail. Used when a roster row has no legal mapping to
    repoint (a phantom created by an earlier bad mapping) — the fix is to move the
    payments to the correct customer. Fingerprints are NOT touched (they exclude
    qb_customer, so dedup is unaffected). Returns (updated_processed_payments,
    payments_reassigned, [old names actually reassigned]). Pure; caller persists + must
    still reclass the matching invoices in QuickBooks."""
    pp = dict(processed_payments or {})
    pays = [dict(p) for p in pp.get("payments", [])]
    by_old = {(o or "").strip(): (n or "").strip() for o, n in changes if (n or "").strip()}
    reassigned = set()
    n = 0
    for p in pays:
        old = (p.get("qb_customer") or "").strip()
        if old in by_old and by_old[old] != old:
            p["renamed_from"] = p.get("renamed_from") or old
            p["qb_customer"] = by_old[old]
            reassigned.add(old)
            n += 1
    pp["payments"] = pays
    return pp, n, sorted(reassigned)


def summarize(rows: list[dict]) -> dict:
    return {
        "total": len(rows),
        "flex": sum(1 for r in rows if r["Type"] == "FLEX"),
        "scan": sum(1 for r in rows if r["Type"] != "FLEX"),
        "review": sum(1 for r in rows if r["Review"] == "yes"),
    }
