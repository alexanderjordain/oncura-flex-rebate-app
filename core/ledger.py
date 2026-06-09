"""Processed-payments ledger — dedup file imports and per-row payments.

Persistent record of every payment we've imported plus the source-file hashes that
produced them. Prevents double-counting when a remittance gets re-uploaded
(identical bytes or overlapping rows from a different export).

The ledger captures both FLEX and scan-package payments. Stage 2 (monthly credit
memos) consumes only the FLEX rows via flex_payments_for_month(). Stage 1 dedups
both kinds so SaasAnt imports never double-post against QBO.

Schema (data/processed_payments.json):
  {
    "files":    [{sha256, filename, company, uploaded_at, row_count, note}, ...],
    "payments": [{fingerprint, company, kind, contract, qb_customer,
                  payment_date, amount, recorded_at}, ...]
  }

Fingerprint = sha256("{company_lower}|{kind}|{contract}|{payment_date_iso}|{amount_cents}").
"""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Iterable

from . import store

LEDGER_PATH = "processed_payments.json"


def _empty():
    return {"files": [], "payments": []}


def load():
    """Returns (data, sha) tuple. Used by record_batch to pass sha back to GitHub."""
    data, sha = store.load_json(LEDGER_PATH, default=_empty())
    if not isinstance(data, dict) or "payments" not in data:
        data = _empty()
    return data, sha


def _date_iso(d) -> str:
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return str(d)[:10]


def _normalize_contract(c) -> str:
    """Make the contract identifier stable across re-uploads.

    Pandas can re-parse the same source column as either string or Float64
    depending on neighbors — a contract typed as `40010172988` in the sheet
    might land in memory as `'40010172988'` once and `'40010172988.0'` next time.
    Excel pasting also introduces non-breaking and zero-width spaces.
    Without normalization, the same physical row produces different ledger
    fingerprints across re-uploads and slips past dedup.
    """
    s = str(c).strip()
    if s.endswith(".0"):
        s = s[:-2]
    s = s.replace(" ", "").replace("​", "")
    return s


def _safe_cents(amount) -> int:
    """Round dollar-amount to cents, tolerating None / NaN / formatted strings.
    Bad inputs become 0 (the downstream code already filters $0 rows) — never raises."""
    if amount is None:
        return 0
    try:
        v = float(amount)
        if v != v:  # NaN check without importing math
            return 0
        return int(round(v * 100))
    except (TypeError, ValueError):
        return 0


def fingerprint(company: str, kind: str, contract, payment_date, amount) -> str:
    """Stable hash of a payment's identifying fields."""
    cents = _safe_cents(amount)
    key = f"{(company or '').strip().lower()}|{kind}|{_normalize_contract(contract)}|{_date_iso(payment_date)}|{cents}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def partial_fingerprint(company: str, kind: str, contract, amount) -> str:
    """Fingerprint excluding payment_date — for detecting possible reissues."""
    cents = _safe_cents(amount)
    key = f"{(company or '').strip().lower()}|{kind}|{_normalize_contract(contract)}|{cents}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def check_file_seen(content: bytes):
    """Returns the matching file record dict if these exact bytes were processed before, else None."""
    h = file_hash(content)
    data, _ = load()
    for f in data.get("files", []):
        if f.get("sha256") == h:
            return f
    return None


def check_payments_seen(fingerprints: Iterable[str]) -> set:
    """Return the subset of fingerprints already in the ledger."""
    fps = set(fingerprints)
    if not fps:
        return set()
    data, _ = load()
    return {p["fingerprint"] for p in data.get("payments", []) if p.get("fingerprint") in fps}


def check_payment_months_seen(company: str, year_months: Iterable) -> dict:
    """For each (year, month) in ``year_months``, count how many ledger payments
    for ``company`` already have a payment_date in that month.

    Used by Stage 1 to flag re-uploads more precisely than file-hash matching:
    OnePlace remittances look structurally similar every month, so 'same file'
    is too weak a duplicate signal. 'Same MONTH already in the ledger' is the
    real safety check — that's what blocks double-posting to QBO.

    Returns ``{(year, month): count_of_payments_for_that_month}`` for months
    that have any matches. Months with zero matches are omitted.
    """
    wanted = {(int(y), int(m)) for y, m in year_months}
    if not wanted:
        return {}
    data, _ = load()
    cmp_co = (company or "").lower()
    out: dict[tuple[int, int], int] = {}
    for p in data.get("payments", []):
        if (p.get("company") or "").lower() != cmp_co:
            continue
        pd_str = str(p.get("payment_date", ""))
        try:
            y, m, *_ = pd_str.split("-")
            key = (int(y), int(m))
        except (ValueError, AttributeError):
            continue
        if key in wanted:
            out[key] = out.get(key, 0) + 1
    return out


def check_possible_reissues(company: str, payments: list[dict]) -> list[dict]:
    """Find incoming payments that match an existing ledger row on
    (company, kind, contract, amount) but with a DIFFERENT payment_date.

    These look like reissues — same money, different date — and shouldn't be
    silently posted as net-new. Stage 1 should surface them for confirm-and-proceed.

    Returns a list of {incoming, existing[]} dicts. Empty if nothing matched.
    """
    data, _ = load()
    by_partial = {}
    for p in data.get("payments", []):
        if (p.get("company") or "").strip().lower() != (company or "").strip().lower():
            continue
        key = partial_fingerprint(company, p.get("kind", ""),
                                  p.get("contract", ""), p.get("amount", 0))
        by_partial.setdefault(key, []).append(p)

    out = []
    for p in payments:
        pk = partial_fingerprint(company, p["kind"], p.get("contract", ""), p["amount"])
        matches = by_partial.get(pk, [])
        incoming_iso = _date_iso(p["payment_date"])
        # Filter to ledger rows that share both a different date AND the same
        # (year, month) as the incoming row. A reissue is, by definition, a
        # small date correction within the same billing period — same contract
        # + same amount + a date in an EARLIER MONTH is just the prior cycle's
        # payment, not a reissue, and shouldn't surface here. Recurring monthly
        # subscriptions (OnePlace) used to false-fire this check on every
        # second-month upload because every (contract, amount) matched a prior
        # row from a different month.
        try:
            inc_ym = tuple(int(x) for x in incoming_iso.split("-")[:2])
        except (ValueError, AttributeError):
            inc_ym = None
        def _same_month(prior_iso: str) -> bool:
            try:
                return tuple(int(x) for x in str(prior_iso).split("-")[:2]) == inc_ym
            except (ValueError, AttributeError):
                return False
        date_diff = [m for m in matches
                     if m.get("payment_date") != incoming_iso and _same_month(m.get("payment_date", ""))]
        if date_diff:
            out.append({"incoming": p, "existing": date_diff})
    return out


def record_batch(
    *,
    file_content: bytes | None,
    filename: str,
    company: str,
    payments: list[dict],
    note: str = "",
):
    """Append a file record + payment rows to the ledger and persist.

    Each payment dict must include: kind, contract, qb_customer, payment_date, amount.
    Fingerprints are computed here; duplicates are skipped silently (caller's check_payments_seen
    is for UX; this is the safety net).

    file_content may be None for non-file-driven batches (e.g., recording credit memos generated
    in Stage 2 — those reference the source ledger rows already, not a file).

    Returns (ok, added_count, message).
    """
    data, sha = load()
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    if file_content is not None:
        fh = file_hash(file_content)
        if not any(f.get("sha256") == fh for f in data["files"]):
            data["files"].append({
                "sha256": fh,
                "filename": filename,
                "company": company,
                "uploaded_at": now_iso,
                "row_count": len(payments),
                "note": note,
            })
    existing = {p["fingerprint"] for p in data["payments"]}
    added = 0
    for p in payments:
        fp = fingerprint(company, p["kind"], p.get("contract", ""), p["payment_date"], p["amount"])
        if fp in existing:
            continue
        existing.add(fp)
        data["payments"].append({
            "fingerprint": fp,
            "company": company,
            "kind": p["kind"],
            "contract": str(p.get("contract", "")),
            "qb_customer": p.get("qb_customer", ""),
            "payment_date": _date_iso(p["payment_date"]),
            "amount": round(float(p["amount"]), 2),
            "recorded_at": now_iso,
        })
        added += 1
    msg = f"Ledger: +{added} {company} payments ({filename})"
    ok, _ = store.save_json(LEDGER_PATH, data, msg, sha=sha)
    return ok, added, msg


def flex_payments_for_month(year: int, month: int) -> list[dict]:
    """All ledger rows with kind='flex' and payment_date in (year, month)."""
    data, _ = load()
    out = []
    for p in data.get("payments", []):
        if p.get("kind") != "flex":
            continue
        pd_str = str(p.get("payment_date", ""))
        try:
            y, m, *_ = pd_str.split("-")
            if int(y) == year and int(m) == month:
                out.append(p)
        except (ValueError, AttributeError):
            continue
    return out


def flex_payments_in_window(start_date, end_date) -> list[dict]:
    """All ledger rows with kind='flex' and payment_date in [start_date, end_date].

    Date-range version of `flex_payments_for_month`. Used by Stage 3 to fetch
    every FLEX payment in the closing quarter window so the ledger-aware
    inclusion filter can decide which clinics are actually on the program
    this quarter (vs. stale `active=true` entries that nobody updated).

    `start_date` / `end_date` accept anything with .isoformat() or a "YYYY-MM-DD"
    string — same shape as ledger payment_date values.
    """
    def _norm(d):
        if hasattr(d, "isoformat"):
            return d.isoformat()[:10]
        return str(d)[:10]
    start_iso = _norm(start_date)
    end_iso = _norm(end_date)
    data, _ = load()
    out = []
    for p in data.get("payments", []):
        if p.get("kind") != "flex":
            continue
        pd_str = str(p.get("payment_date", ""))[:10]
        if not pd_str:
            continue
        if start_iso <= pd_str <= end_iso:
            out.append(p)
    return out


def summary():
    """Quick-stats {file_count, payment_count, by_company, latest_uploaded_at}."""
    data, _ = load()
    files = data.get("files", [])
    pays = data.get("payments", [])
    by_co = {}
    for p in pays:
        co = p.get("company", "?")
        by_co[co] = by_co.get(co, 0) + 1
    latest = max((f.get("uploaded_at", "") for f in files), default="")
    return {
        "file_count": len(files),
        "payment_count": len(pays),
        "by_company": by_co,
        "latest_uploaded_at": latest,
    }
