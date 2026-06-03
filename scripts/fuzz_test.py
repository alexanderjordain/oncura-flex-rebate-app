"""Fuzz harness: synthetic fixtures + one-dimensional mutations across the
document formats the app actually consumes.

Goals:
- Start from a CLEAN canonical fixture for each format.
- Mutate exactly one dimension at a time (drift one column type, inject NBSP,
  add a summary row, swap a header, etc.).
- Pipe through the relevant core parser.
- Report PASS / FAIL / WARN with one-line diagnostics.

Run: python scripts/fuzz_test.py
"""
from __future__ import annotations

import datetime as dt
import io
import os
import sys
import traceback
from copy import deepcopy
from dataclasses import dataclass, field

import pandas as pd

# Make `core` importable when run from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import flex_finance, ledger, opd_adapter

NBSP = " "
ZWSP = "​"
SMART_LDQUO = "“"
SMART_RDQUO = "”"
EM_DASH = "—"


# ─────────────────────────────────────────────────────────────────────────────
# Result collector
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Case:
    name: str
    category: str
    status: str       # PASS / FAIL / WARN / EXPECTED_FAIL
    detail: str = ""


CASES: list[Case] = []


def record(name, category, status, detail=""):
    CASES.append(Case(name=name, category=category, status=status, detail=detail))


def safe(name, category, fn, expect_fail=False):
    try:
        diag = fn() or ""
        record(name, category, "PASS", diag)
    except Exception as e:
        status = "EXPECTED_FAIL" if expect_fail else "FAIL"
        record(name, category, status, f"{type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Canonical fixtures
# ─────────────────────────────────────────────────────────────────────────────


def baseline_newlane() -> pd.DataFrame:
    """NewLane-style remittance: mix of flex (odd cents) + scan (whole dollar)."""
    return pd.DataFrame([
        {"Customer": "Acme Animal Hospital, LLC", "Contract": "5001234", "Amount": 412.37},
        {"Customer": "Beta Vet Services",          "Contract": "5001235", "Amount": 595.00},
        {"Customer": "Charlie Pet Clinic",         "Contract": "5001236", "Amount": 278.91},
        {"Customer": "Delta Animal Care",          "Contract": "5001237", "Amount": 295.00},
        {"Customer": "Echo Vet",                   "Contract": "5001238", "Amount": 501.66},
    ])


def baseline_oneplace() -> pd.DataFrame:
    """OnePlace-style: leading-zero flex contracts mixed with longer scan contracts."""
    return pd.DataFrame([
        {"Customer": "Acme Animal Hospital, LLC", "Contract": "04001017",   "Amount": 412.37},  # flex
        {"Customer": "Beta Vet Services",          "Contract": "33333333",   "Amount": 595.00},  # scan
        {"Customer": "Charlie Pet Clinic",         "Contract": "04001018",   "Amount": 278.91},  # flex
        {"Customer": "Delta Animal Care",          "Contract": "33333334",   "Amount": 295.00},  # scan
    ])


def baseline_case_grid() -> pd.DataFrame:
    """Case grid OPD export."""
    return pd.DataFrame([
        {"Clinic": "Acme Animal Hospital", "Case ID": "C001",
         "Finalized Date": "2026-05-15", "Submitted": "2026-05-15",
         "Priority": "ROUTINE", "Services": "Basic Abdominal Ultrasound"},
        {"Clinic": "Acme Animal Hospital", "Case ID": "C002",
         "Finalized Date": "2026-05-16", "Submitted": "2026-05-16",
         "Priority": "STAT", "Services": "Basic Abdominal Ultrasound"},
        {"Clinic": "Beta Vet Services", "Case ID": "C003",
         "Finalized Date": "2026-05-17", "Submitted": "2026-05-17",
         "Priority": "ROUTINE", "Services": "Basic Abdominal Ultrasound, Add On Abdominal Radiographs (3)"},
    ])


def baseline_price_table() -> dict:
    return {
        "stat_fee": 125.0,
        "services": {
            "Basic Abdominal Ultrasound": {"price": 135.0, "category": "ultrasound"},
            "Add On Abdominal Radiographs (3)": {"price": 75.0, "category": "rads"},
            "STAT Sonographer Assistance Fee": {"price": 125.0, "category": "stat"},
        },
    }


def baseline_invoices() -> pd.DataFrame:
    """OPD generic invoices export."""
    return pd.DataFrame([
        {"Document Date": "2026-05-15", "Clinic": "Acme Animal Hospital",
         "Subtotal": 300.00, "ScanEligible": True},
        {"Document Date": "2026-06-30 14:00:00", "Clinic": "Acme Animal Hospital",
         "Subtotal": 250.00, "ScanEligible": False},
        {"Document Date": "2026-06-15", "Clinic": "Beta Vet Services",
         "Subtotal": 100.00, "ScanEligible": False},
    ])


def baseline_name_map() -> dict:
    return {
        "map": {
            "Acme Animal Hospital, LLC": "Acme Animal Hospital",
            "Beta Vet Services": "Beta Vet Services",
            "Charlie Pet Clinic": "Charlie Pet Clinic",
            "Delta Animal Care": "Delta Animal Care",
            "Echo Vet": "Echo Vet",
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Test runners
# ─────────────────────────────────────────────────────────────────────────────


def run_remittance(df, company, name_map=None):
    """Pipe a remittance through flex_finance.process_remittance with defaults."""
    return flex_finance.process_remittance(
        df, company,
        customer_col="Customer", amount_col="Amount", id_col="Contract",
        payment_date=dt.date(2026, 6, 15),
        invoice_date=dt.date(2026, 6, 15),
        start_invoice_no=49000,
        name_map=name_map or baseline_name_map(),
        split="by_cents",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Mutators (each returns a NEW df without touching baseline)
# ─────────────────────────────────────────────────────────────────────────────


def mut_float64_contract(df):
    """Excel-saved-as-xlsx behavior: numeric-looking strings get cast to Float64."""
    out = df.copy()
    out["Contract"] = out["Contract"].astype(float)
    return out


def mut_int_contract(df):
    out = df.copy()
    out["Contract"] = out["Contract"].astype(int)
    return out


def mut_nbsp_in_contract(df, idx=0):
    out = df.copy()
    v = str(out.loc[idx, "Contract"])
    out.loc[idx, "Contract"] = f"{NBSP}{v}{NBSP}"
    return out


def mut_zwsp_in_contract(df, idx=0):
    out = df.copy()
    v = str(out.loc[idx, "Contract"])
    out.loc[idx, "Contract"] = f"{v}{ZWSP}"
    return out


def _as_object_amount(df):
    """Force the Amount column to object dtype so we can mix strings + floats.
    This mirrors what pandas does when it reads a CSV with mixed-type cells."""
    out = df.copy()
    out["Amount"] = out["Amount"].astype(object)
    return out


def mut_currency_string_amount(df):
    """Amounts as `$1,234.56` strings."""
    out = _as_object_amount(df)
    out["Amount"] = out["Amount"].map(lambda v: f"${float(v):,.2f}")
    return out


def mut_negative_paren_one_row(df, idx=0):
    """One row with `(123.45)` accounting-style negative — a refund/clawback."""
    out = _as_object_amount(df)
    out.loc[idx, "Amount"] = f"({abs(float(out.loc[idx, 'Amount'])):.2f})"
    return out


def mut_dash_amount(df, idx=0):
    out = _as_object_amount(df)
    out.loc[idx, "Amount"] = "-"
    return out


def mut_na_amount(df, idx=0):
    out = _as_object_amount(df)
    out.loc[idx, "Amount"] = "N/A"
    return out


def mut_blank_amount(df, idx=0):
    out = _as_object_amount(df)
    out.loc[idx, "Amount"] = ""
    return out


def mut_blank_contract(df, idx=0):
    out = df.copy()
    out.loc[idx, "Contract"] = ""
    return out


def mut_smart_quotes_customer(df, idx=0):
    out = df.copy()
    v = str(out.loc[idx, "Customer"])
    out.loc[idx, "Customer"] = v.replace('"', SMART_LDQUO).replace('LLC', f"L{EM_DASH}LC")
    return out


def mut_case_variant_customer(df, idx=0):
    out = df.copy()
    out.loc[idx, "Customer"] = str(out.loc[idx, "Customer"]).upper()
    return out


def mut_extra_whitespace_customer(df, idx=0):
    out = df.copy()
    out.loc[idx, "Customer"] = f"  {out.loc[idx, 'Customer']}   "
    return out


def mut_unknown_customer(df, idx=0):
    out = df.copy()
    out.loc[idx, "Customer"] = "Brand New Hospital, Not In Map LLC"
    return out


def mut_trailing_summary(df):
    out = df.copy()
    # Add a "Total" row at the bottom with blank Customer/Contract
    summary = pd.DataFrame([{"Customer": None, "Contract": None,
                             "Amount": float(out["Amount"].astype(float).sum())}])
    return pd.concat([out, summary], ignore_index=True)


def mut_swap_columns(df):
    out = df.copy()[["Amount", "Customer", "Contract"]]
    return out


def mut_drop_column(df, col):
    out = df.copy().drop(columns=[col])
    return out


def mut_rename_column(df, old, new):
    return df.rename(columns={old: new})


def mut_duplicate_contract(df):
    """Same contract twice — a clinic paid twice in one remittance."""
    out = df.copy()
    out.loc[len(out)] = {"Customer": out.loc[0, "Customer"],
                         "Contract": out.loc[0, "Contract"],
                         "Amount": float(out.loc[0, "Amount"]) + 1}
    return out


def mut_huge_amount(df, idx=0):
    out = df.copy()
    out.loc[idx, "Amount"] = 999_999_999_999.99
    return out


def mut_zero_amount(df, idx=0):
    out = df.copy()
    out.loc[idx, "Amount"] = 0
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Case grid mutators
# ─────────────────────────────────────────────────────────────────────────────


def mut_priority_lowercase(df, idx=1):
    out = df.copy()
    out.loc[idx, "Priority"] = "stat"
    return out


def mut_priority_titlecase(df, idx=1):
    out = df.copy()
    out.loc[idx, "Priority"] = "Stat"
    return out


def mut_services_extra_whitespace(df, idx=0):
    out = df.copy()
    out.loc[idx, "Services"] = "  Basic   Abdominal\tUltrasound  "
    return out


def mut_services_empty(df, idx=0):
    out = df.copy()
    out.loc[idx, "Services"] = ""
    return out


def mut_services_unknown(df, idx=0):
    out = df.copy()
    out.loc[idx, "Services"] = "Made-Up Service, Another Made-Up Thing"
    return out


def mut_services_trailing_comma(df, idx=0):
    out = df.copy()
    v = str(out.loc[idx, "Services"])
    out.loc[idx, "Services"] = f"{v},"
    return out


def mut_clinic_with_comma(df, idx=0):
    out = df.copy()
    out.loc[idx, "Clinic"] = "Acme Animal Hospital, LLC"
    return out


def mut_clinic_with_smart_quotes(df, idx=0):
    out = df.copy()
    out.loc[idx, "Clinic"] = f"{SMART_LDQUO}Acme{SMART_RDQUO} Animal Hospital"
    return out


def _as_object_date(df):
    out = df.copy()
    out["Finalized Date"] = out["Finalized Date"].astype(object)
    return out


def mut_date_as_excel_serial(df, idx=0):
    """Excel sometimes leaves dates as serial numbers."""
    out = _as_object_date(df)
    out.loc[idx, "Finalized Date"] = 45413   # roughly mid-2024
    return out


def mut_date_us_format(df, idx=0):
    out = _as_object_date(df)
    out.loc[idx, "Finalized Date"] = "05/15/2026"
    return out


def mut_date_iso_with_timestamp(df, idx=0):
    out = _as_object_date(df)
    out.loc[idx, "Finalized Date"] = "2026-05-15T14:30:00"
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Fuzz runs — remittances
# ─────────────────────────────────────────────────────────────────────────────


def fuzz_remittances():
    cat = "remittance"

    # ── Baselines — these MUST pass cleanly
    def _check_baseline(company, builder):
        res = run_remittance(builder(), company)
        s = res["summary"]
        return f"flex={s['flex_count']} scan={s['scan_count']} total=${s['total']:.2f}"

    safe("baseline:NewLane", cat, lambda: _check_baseline("NewLane", baseline_newlane))
    safe("baseline:OnePlace", cat, lambda: _check_baseline("OnePlace", baseline_oneplace))
    safe("baseline:GreatAmerica", cat, lambda: _check_baseline(
        "GreatAmerica",
        lambda: baseline_newlane().assign(Amount=lambda d: d["Amount"].astype(float)),
    ))

    # ── Contract column type drift
    def _contract_float64():
        df = mut_float64_contract(baseline_newlane())
        res = run_remittance(df, "NewLane")
        # Contract should round-trip cleanly; ref nos should reflect string form
        flex = res["flex_payments"]
        if flex.empty:
            return "no flex rows produced"
        refs = list(flex["Ref No (Receive Payment No)"])
        # NewLane uses FlexNewLane - N which doesn't expose contract — check via ledger fingerprint instead
        return f"refs={refs[:2]}…"
    safe("newlane:contract-as-Float64", cat, _contract_float64)

    def _contract_int():
        df = mut_int_contract(baseline_newlane())
        res = run_remittance(df, "NewLane")
        return f"ok rows={len(res['flex_payments']) + len(res['scan_payments'])}"
    safe("newlane:contract-as-int", cat, _contract_int)

    def _oneplace_float64():
        # OnePlace uses contract in the Ref No directly — leading-zero handling is critical
        df = mut_float64_contract(baseline_oneplace())
        res = run_remittance(df, "OnePlace")
        flex_refs = list(res["flex_payments"]["Ref No (Receive Payment No)"])
        scan_refs = list(res["scan_payments"]["Ref No (Receive Payment No)"])
        return f"flex_refs={flex_refs} scan_refs={scan_refs}"
    safe("oneplace:contract-as-Float64", cat, _oneplace_float64)

    # ── Whitespace/Unicode pollution
    safe("newlane:nbsp-in-contract", cat,
         lambda: f"{run_remittance(mut_nbsp_in_contract(baseline_newlane()), 'NewLane')['summary']}")
    safe("newlane:zwsp-in-contract", cat,
         lambda: f"{run_remittance(mut_zwsp_in_contract(baseline_newlane()), 'NewLane')['summary']}")
    safe("newlane:smart-quotes-customer", cat,
         lambda: _smart_q_check())
    safe("newlane:case-variant-customer", cat,
         lambda: _case_variant_check())
    safe("newlane:extra-whitespace-customer", cat,
         lambda: _whitespace_check())

    # ── Amount format variants
    safe("newlane:currency-string-amounts", cat,
         lambda: f"{run_remittance(mut_currency_string_amount(baseline_newlane()), 'NewLane')['summary']}")
    safe("newlane:dash-amount", cat,
         lambda: f"{run_remittance(mut_dash_amount(baseline_newlane()), 'NewLane')['summary']}")
    safe("newlane:NA-amount", cat,
         lambda: f"{run_remittance(mut_na_amount(baseline_newlane()), 'NewLane')['summary']}")
    safe("newlane:blank-amount", cat,
         lambda: f"{run_remittance(mut_blank_amount(baseline_newlane()), 'NewLane')['summary']}")
    safe("newlane:parenthesized-negative", cat,
         lambda: f"{run_remittance(mut_negative_paren_one_row(baseline_newlane()), 'NewLane')['summary']}")
    safe("newlane:huge-amount", cat,
         lambda: f"{run_remittance(mut_huge_amount(baseline_newlane()), 'NewLane')['summary']}")
    safe("newlane:zero-amount", cat,
         lambda: f"{run_remittance(mut_zero_amount(baseline_newlane()), 'NewLane')['summary']}")

    # ── Structural drift
    safe("newlane:trailing-summary-row", cat,
         lambda: f"{run_remittance(mut_trailing_summary(baseline_newlane()), 'NewLane')['summary']}")
    safe("newlane:duplicate-contract", cat, _duplicate_contract_check)
    safe("newlane:unknown-customer", cat, _unknown_customer_check)
    safe("newlane:blank-contract-one-row", cat,
         lambda: f"{run_remittance(mut_blank_contract(baseline_newlane()), 'NewLane')['summary']}")

    # ── Column drift
    safe("newlane:swapped-columns", cat,
         lambda: f"{run_remittance(mut_swap_columns(baseline_newlane()), 'NewLane')['summary']}")
    safe("newlane:renamed-amount-column", cat,
         lambda: _renamed_amount_check())

    # ── Empty / minimal
    safe("newlane:empty-frame", cat,
         lambda: f"{run_remittance(baseline_newlane().iloc[0:0], 'NewLane')['summary']}")


def _smart_q_check():
    res = run_remittance(mut_smart_quotes_customer(baseline_newlane()), "NewLane")
    return f"unmapped={len(res['unmapped'])} total=${res['summary']['total']:.2f}"


def _case_variant_check():
    """Case-folded customer name should still resolve via translate_name."""
    res = run_remittance(mut_case_variant_customer(baseline_newlane()), "NewLane")
    return f"unmapped={len(res['unmapped'])} (expect 0 — case-insensitive fix should resolve)"


def _whitespace_check():
    res = run_remittance(mut_extra_whitespace_customer(baseline_newlane()), "NewLane")
    return f"unmapped={len(res['unmapped'])} (expect 0 — whitespace-collapse fix)"


def _duplicate_contract_check():
    df = mut_duplicate_contract(baseline_newlane())
    res = run_remittance(df, "NewLane")
    refs = list(res["flex_payments"]["Ref No (Receive Payment No)"])
    return f"rows={len(refs)} all_unique={len(set(refs))==len(refs)}"


def _unknown_customer_check():
    res = run_remittance(mut_unknown_customer(baseline_newlane()), "NewLane")
    return f"unmapped={res['unmapped']}"


def _renamed_amount_check():
    df = mut_rename_column(baseline_newlane(), "Amount", "Amt")
    return flex_finance.process_remittance(
        df, "NewLane",
        customer_col="Customer", amount_col="Amt", id_col="Contract",
        payment_date=dt.date(2026, 6, 15), invoice_date=dt.date(2026, 6, 15),
        start_invoice_no=49000, name_map=baseline_name_map(),
        split="by_cents",
    )["summary"]


# ─────────────────────────────────────────────────────────────────────────────
# Fuzz runs — case grid
# ─────────────────────────────────────────────────────────────────────────────


def fuzz_case_grid():
    cat = "case_grid"
    pt = baseline_price_table()

    def _norm(df):
        return opd_adapter._normalize_case_grid(df, item_map={}, price_table=pt)

    # Baseline
    def _baseline():
        out = _norm(baseline_case_grid())
        return f"rows={len(out)} total=${out['amount'].sum():.2f}"
    safe("baseline:case_grid", cat, _baseline)

    # Priority casing
    def _priority_lower():
        out = _norm(mut_priority_lowercase(baseline_case_grid()))
        # Was the implicit STAT $125 added or not?
        has_stat = (out["item_desc"] == "STAT Sonographer Assistance Fee").any()
        return f"stat_added={has_stat} (expect False — only 'STAT' upper triggers)"
    safe("case_grid:priority-lowercase", cat, _priority_lower)

    def _priority_title():
        out = _norm(mut_priority_titlecase(baseline_case_grid()))
        has_stat = (out["item_desc"] == "STAT Sonographer Assistance Fee").any()
        return f"stat_added={has_stat} (expect False — only 'STAT' upper triggers)"
    safe("case_grid:priority-titlecase", cat, _priority_title)

    # Services parsing
    safe("case_grid:services-extra-whitespace", cat,
         lambda: f"rows={len(_norm(mut_services_extra_whitespace(baseline_case_grid())))}")
    safe("case_grid:services-empty-string", cat,
         lambda: f"rows={len(_norm(mut_services_empty(baseline_case_grid())))}")
    safe("case_grid:services-unknown-name", cat,
         lambda: _unknown_service_check(_norm(mut_services_unknown(baseline_case_grid()))))
    safe("case_grid:services-trailing-comma", cat,
         lambda: f"rows={len(_norm(mut_services_trailing_comma(baseline_case_grid())))}")

    # Clinic name special chars
    safe("case_grid:clinic-with-comma", cat,
         lambda: f"clinics={set(_norm(mut_clinic_with_comma(baseline_case_grid()))['clinic'])}")
    safe("case_grid:clinic-with-smart-quotes", cat,
         lambda: f"clinics={set(_norm(mut_clinic_with_smart_quotes(baseline_case_grid()))['clinic'])}")

    # Date format drift
    safe("case_grid:date-excel-serial", cat,
         lambda: _date_drift_check(mut_date_as_excel_serial(baseline_case_grid())))
    safe("case_grid:date-us-format", cat,
         lambda: _date_drift_check(mut_date_us_format(baseline_case_grid())))
    safe("case_grid:date-iso-timestamp", cat,
         lambda: _date_drift_check(mut_date_iso_with_timestamp(baseline_case_grid())))


def _unknown_service_check(out):
    other = out[out["category"] == "other"]
    return f"unknown_rows={len(other)} prices_at_zero={(other['amount'] == 0).all()}"


def _date_drift_check(df):
    pt = baseline_price_table()
    out = opd_adapter._normalize_case_grid(df, item_map={}, price_table=pt)
    # The `date` column should parse to something usable downstream
    parsed_count = sum(1 for d in out["date"] if d is not None and str(d) != "NaT")
    return f"rows={len(out)} parseable_dates={parsed_count}/{len(out)}"


# ─────────────────────────────────────────────────────────────────────────────
# Fuzz runs — ledger fingerprint stability
# ─────────────────────────────────────────────────────────────────────────────


def fuzz_fingerprint():
    cat = "fingerprint"

    def _check(name, contract_a, contract_b):
        a = ledger.fingerprint("OnePlace", "flex", contract_a, dt.date(2026, 6, 15), 412.37)
        b = ledger.fingerprint("OnePlace", "flex", contract_b, dt.date(2026, 6, 15), 412.37)
        return f"a==b: {a == b}"

    # The whole point of the recent fix — these should ALL match
    safe("fingerprint:str-vs-float-suffix", cat,
         lambda: _check("eq", "40010172988", "40010172988.0"))
    safe("fingerprint:str-vs-int", cat,
         lambda: _check("eq", "40010172988", 40010172988))
    safe("fingerprint:NBSP-pollution", cat,
         lambda: _check("eq", "40010172988", f"40010172988{NBSP}"))
    safe("fingerprint:ZWSP-pollution", cat,
         lambda: _check("eq", "40010172988", f"40010172988{ZWSP}"))
    safe("fingerprint:trailing-space", cat,
         lambda: _check("eq", "40010172988", "40010172988 "))

    # These should NOT match — different actual contracts
    def _check_diff(contract_a, contract_b):
        a = ledger.fingerprint("OnePlace", "flex", contract_a, dt.date(2026, 6, 15), 412.37)
        b = ledger.fingerprint("OnePlace", "flex", contract_b, dt.date(2026, 6, 15), 412.37)
        return f"a!=b: {a != b}"
    safe("fingerprint:different-contracts-distinct", cat,
         lambda: _check_diff("40010172988", "40010172999"))
    safe("fingerprint:different-leading-zeros-distinct", cat,
         lambda: _check_diff("04001017", "4001017"))

    # Defensive amount handling
    safe("fingerprint:amount-None-ok", cat,
         lambda: f"hash_ok={bool(ledger.fingerprint('OnePlace', 'flex', 'X', dt.date(2026, 6, 15), None))}")
    safe("fingerprint:amount-NaN-ok", cat,
         lambda: f"hash_ok={bool(ledger.fingerprint('OnePlace', 'flex', 'X', dt.date(2026, 6, 15), float('nan')))}")
    safe("fingerprint:amount-blank-string-ok", cat,
         lambda: f"hash_ok={bool(ledger.fingerprint('OnePlace', 'flex', 'X', dt.date(2026, 6, 15), ''))}")
    safe("fingerprint:amount-garbage-string-ok", cat,
         lambda: f"hash_ok={bool(ledger.fingerprint('OnePlace', 'flex', 'X', dt.date(2026, 6, 15), 'garbage'))}")


# ─────────────────────────────────────────────────────────────────────────────
# Fuzz runs — coerce_amount edge cases
# ─────────────────────────────────────────────────────────────────────────────


def fuzz_invariants():
    """Behavioral invariants we don't want regressing — not edge cases,
    just things that must hold."""
    cat = "invariant"

    # OnePlace: flex Ref No strips leading zeros (the export pads them); scan keeps
    # leading zeros (significant identifier). Use a fixture with leading-zero scan
    # contracts so the second half of the invariant is actually exercised.
    def _oneplace_ref_invariant():
        df = pd.DataFrame([
            {"Customer": "Acme Animal Hospital, LLC", "Contract": "04001017", "Amount": 412.37},   # flex
            {"Customer": "Beta Vet Services",          "Contract": "00012345", "Amount": 595.00},   # scan w/ leading 0s
            {"Customer": "Charlie Pet Clinic",         "Contract": "04001018", "Amount": 278.91},   # flex
            {"Customer": "Delta Animal Care",          "Contract": "00067890", "Amount": 295.00},   # scan w/ leading 0s
        ])
        res = run_remittance(df, "OnePlace")
        flex_refs = list(res["flex_payments"]["Ref No (Receive Payment No)"])
        scan_refs = list(res["scan_payments"]["Ref No (Receive Payment No)"])
        flex_ok = all(r.startswith("OPC") and not r[3:].startswith("0") for r in flex_refs)
        scan_ok = all(r.startswith("OPC0") for r in scan_refs)
        return (f"flex_strips_leading_0={flex_ok} (refs={flex_refs})  "
                f"scan_keeps_leading_0={scan_ok} (refs={scan_refs})")
    safe("oneplace:flex-strips-scan-keeps-leading-0", cat, _oneplace_ref_invariant)

    # Reissue check: same amount + DIFFERENT date should flag. Same partial key, +1 cent
    # should NOT flag (the partial key includes amount in cents).
    def _reissue_one_cent_drift():
        seed_data = {
            "files": [],
            "payments": [{
                "fingerprint": "abc", "company": "OnePlace", "kind": "flex",
                "contract": "4001017", "qb_customer": "Acme",
                "payment_date": "2026-04-01", "amount": 412.37,
            }]
        }
        import unittest.mock as mock
        with mock.patch.object(ledger, "load", return_value=(seed_data, None)):
            same_amt = ledger.check_possible_reissues("OnePlace", [{
                "kind": "flex", "contract": "4001017",
                "payment_date": dt.date(2026, 5, 1), "amount": 412.37,
            }])
            diff_amt = ledger.check_possible_reissues("OnePlace", [{
                "kind": "flex", "contract": "4001017",
                "payment_date": dt.date(2026, 5, 1), "amount": 412.38,  # 1 cent drift
            }])
        return (f"same_amount_flagged={len(same_amt)>0}  "
                f"one_cent_off_flagged={len(diff_amt)>0} (expect True, False)")
    safe("reissue:cent-precision", cat, _reissue_one_cent_drift)

    # Empty customer cell — should be filtered out, not crash
    def _none_customer():
        df = baseline_newlane().copy()
        df["Customer"] = df["Customer"].astype(object)
        df.loc[0, "Customer"] = None
        res = run_remittance(df, "NewLane")
        return f"rows={res['summary']['flex_count'] + res['summary']['scan_count']} (expect 4 — one row dropped)"
    safe("newlane:None-customer-row", cat, _none_customer)

    # Customer cell = whitespace only
    def _whitespace_customer():
        df = baseline_newlane().copy()
        df["Customer"] = df["Customer"].astype(object)
        df.loc[0, "Customer"] = "   "
        res = run_remittance(df, "NewLane")
        # The row is kept (Customer.notna() is True for whitespace), but it shows as unmapped
        return f"rows={res['summary']['flex_count'] + res['summary']['scan_count']} unmapped={len(res['unmapped'])}"
    safe("newlane:whitespace-only-customer", cat, _whitespace_customer)

    # All-whole-dollar NewLane: ought to trigger crossover warning at the UI layer
    # (we can't check the banner here, but we can verify the by_cents split is all-scan)
    def _all_whole_dollar():
        df = baseline_newlane().copy()
        df["Amount"] = [395.0, 595.0, 295.0, 195.0, 495.0]
        res = run_remittance(df, "NewLane")
        s = res["summary"]
        return f"flex={s['flex_count']} scan={s['scan_count']} (expect flex=0, scan=5)"
    safe("newlane:all-whole-dollar-routes-all-scan", cat, _all_whole_dollar)

    # All-odd-cents NewLane: by_cents -> all flex
    def _all_odd_cents():
        df = baseline_newlane().copy()
        df["Amount"] = [395.37, 595.42, 295.99, 195.01, 495.50]
        res = run_remittance(df, "NewLane")
        s = res["summary"]
        return f"flex={s['flex_count']} scan={s['scan_count']} (expect flex=5, scan=0)"
    safe("newlane:all-odd-cents-routes-all-flex", cat, _all_odd_cents)

    # Duplicate-contract NewLane: ref nos must remain unique
    def _dup_contract_unique_refs():
        df = mut_duplicate_contract(baseline_newlane())
        res = run_remittance(df, "NewLane")
        all_refs = (list(res["flex_payments"]["Ref No (Receive Payment No)"])
                    + list(res["scan_payments"]["Ref No (Receive Payment No)"]))
        return f"refs={len(all_refs)} unique={len(set(all_refs))} (expect equal)"
    safe("newlane:duplicate-contract-refs-still-unique", cat, _dup_contract_unique_refs)


def fuzz_coerce_amount():
    cat = "coerce_amount"
    cases = [
        ("clean-float", 412.37, 412.37),
        ("clean-int", 412, 412.0),
        ("currency-prefix", "$412.37", 412.37),
        ("thousands-sep", "$1,234.56", 1234.56),
        ("paren-negative", "(412.37)", -412.37),
        ("minus-prefix", "-412.37", -412.37),
        ("trailing-cr", "412.37\r", 412.37),
        ("whitespace-pad", "  412.37  ", 412.37),
        ("dash-only", "-", 0.0),
        ("NA", "N/A", 0.0),
        ("blank", "", 0.0),
        ("None", None, 0.0),
        ("non-numeric-letters", "garbage", 0.0),
        ("nbsp-pad", f"{NBSP}412.37{NBSP}", 412.37),
        ("zwsp-suffix", f"412.37{ZWSP}", 412.37),
    ]
    for name, raw, expected in cases:
        def _check(raw=raw, expected=expected):
            got = opd_adapter.coerce_amount(raw)
            ok = abs(got - expected) < 0.001
            return f"in={raw!r} -> {got} (expect {expected}, {'OK' if ok else 'MISMATCH'})"
        safe(f"coerce:{name}", cat, _check)


# ─────────────────────────────────────────────────────────────────────────────
# Reporter
# ─────────────────────────────────────────────────────────────────────────────


def report():
    by_cat = {}
    for c in CASES:
        by_cat.setdefault(c.category, []).append(c)

    print("\n" + "=" * 78)
    print(f"FUZZ TEST REPORT  -  {len(CASES)} cases")
    print("=" * 78)

    total_pass = sum(1 for c in CASES if c.status == "PASS")
    total_fail = sum(1 for c in CASES if c.status == "FAIL")
    total_warn = sum(1 for c in CASES if c.status == "WARN")
    total_xfail = sum(1 for c in CASES if c.status == "EXPECTED_FAIL")
    print(f"PASS {total_pass}  |  FAIL {total_fail}  |  WARN {total_warn}  |  EXPECTED_FAIL {total_xfail}\n")

    for category, cases in by_cat.items():
        print(f"-- {category} --")
        for c in cases:
            mark = {"PASS": "+", "FAIL": "X", "WARN": "!", "EXPECTED_FAIL": "?"}[c.status]
            label = c.name.ljust(46)
            print(f"  [{mark}] {label}  {c.detail}")
        print()

    # FAIL summary
    fails = [c for c in CASES if c.status == "FAIL"]
    if fails:
        print("=" * 78)
        print(f"FAILURES ({len(fails)})")
        print("=" * 78)
        for c in fails:
            print(f"  - [{c.category}] {c.name}")
            print(f"      {c.detail}")
        print()


if __name__ == "__main__":
    fuzz_remittances()
    fuzz_case_grid()
    fuzz_fingerprint()
    fuzz_invariants()
    fuzz_coerce_amount()
    report()
