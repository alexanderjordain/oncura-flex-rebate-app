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


def summarize(rows: list[dict]) -> dict:
    return {
        "total": len(rows),
        "flex": sum(1 for r in rows if r["Type"] == "FLEX"),
        "scan": sum(1 for r in rows if r["Type"] != "FLEX"),
        "review": sum(1 for r in rows if r["Review"] == "yes"),
    }
