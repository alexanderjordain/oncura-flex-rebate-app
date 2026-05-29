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


def fingerprint(company: str, kind: str, contract, payment_date, amount) -> str:
    """Stable hash of a payment's identifying fields."""
    cents = int(round(float(amount) * 100))
    key = f"{(company or '').strip().lower()}|{kind}|{str(contract).strip()}|{_date_iso(payment_date)}|{cents}"
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
