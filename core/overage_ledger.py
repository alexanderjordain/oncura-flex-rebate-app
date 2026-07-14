"""Overage payment tracking + lockout state.

Persistent record of every FLEX/rebate overage that's been billed to a clinic.
The `flex_overage` module generates the invoice; this module tracks what happened
after — was it paid, when, and how much still open. That "still open" state is
what drives lockout eligibility.

Lockout rule (from operator, 2026-07-14):
  Any clinic with an overage that has been UNPAID for 3+ calendar months from
  the date it was billed is locked out. Warning band: 2-3 months old.

Persistence: `data/overage_ledger.json` via core.store, matching audit.py
(GitHub Contents API when GITHUB_TOKEN is set, local file otherwise).

Schema:
  {
    "version": 1,
    "entries": {
      "<uuid>": {
        "id": "<uuid>",
        "clinic": "human name",
        "qb_customer": "canonical QB name",
        "billing_month": "YYYY-MM",
        "quarter_covered": "Q1 2026" | "",
        "route": "direct" | "partner" | "missed_cutoff",
        "gross_overage": 1250.00,
        "credit_applied": 0.00,
        "net_amount": 1250.00,
        "date_billed": "YYYY-MM-DD",
        "invoice_no": "" | "12345",
        "paid_at": null | "YYYY-MM-DD",
        "paid_amount": null | 1250.00,
        "paid_note": "",
        "notes": "",
        "created_at": "ISO datetime UTC",
        "created_by": "operator email",
        "updated_at": "ISO datetime UTC",
        "updated_by": "operator email",
      }
    }
  }

Natural key for upsert: (qb_customer_lower, billing_month). If a matching entry
exists we update in place; otherwise we mint a new UUID.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Iterable

from dateutil.relativedelta import relativedelta

from . import store

LEDGER_PATH = "overage_ledger.json"

LOCKOUT_MONTHS = 3   # unpaid this many months (calendar) -> locked out
WARNING_MONTHS = 2   # unpaid 2..3 months -> warning band

STATUS_PAID       = "paid"
STATUS_OPEN       = "open"
STATUS_WARNING    = "warning"
STATUS_LOCKED_OUT = "locked_out"

VALID_ROUTES = {"direct", "partner", "missed_cutoff"}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _empty():
    return {"version": 1, "entries": {}}


def _load():
    data, sha = store.load_json(LEDGER_PATH, default=_empty())
    if not isinstance(data, dict) or "entries" not in data:
        data = _empty()
    # entries used to be a list in an earlier draft; migrate to dict
    if isinstance(data["entries"], list):
        data["entries"] = {e["id"]: e for e in data["entries"] if e.get("id")}
    return data, sha


def _save(data: dict, sha: str | None, message: str) -> None:
    store.save_json(LEDGER_PATH, data, message, sha=sha)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _natural_key(qb_customer: str, billing_month: str) -> tuple[str, str]:
    return ((qb_customer or "").strip().lower(), (billing_month or "").strip())


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def all_entries() -> list[dict]:
    data, _ = _load()
    return list(data["entries"].values())


def get(entry_id: str) -> dict | None:
    data, _ = _load()
    return data["entries"].get(entry_id)


def upsert(record: dict, actor: str = "") -> str:
    """Create or update by natural key. Returns entry id."""
    if not record.get("qb_customer"):
        raise ValueError("qb_customer is required")
    if not record.get("billing_month"):
        raise ValueError("billing_month is required")
    if record.get("route") and record["route"] not in VALID_ROUTES:
        raise ValueError(f"route must be one of {VALID_ROUTES}")

    data, sha = _load()

    # Find existing by natural key
    nk = _natural_key(record["qb_customer"], record["billing_month"])
    existing_id = None
    for eid, e in data["entries"].items():
        if _natural_key(e.get("qb_customer", ""), e.get("billing_month", "")) == nk:
            existing_id = eid
            break

    now = _now_iso()
    if existing_id:
        entry = data["entries"][existing_id]
        # Only overwrite non-payment fields on re-generation; preserve paid_at etc.
        for field in ("clinic", "quarter_covered", "route", "gross_overage",
                      "credit_applied", "net_amount", "date_billed",
                      "invoice_no", "notes"):
            if field in record and record[field] is not None:
                entry[field] = record[field]
        entry["updated_at"] = now
        entry["updated_by"] = actor
    else:
        existing_id = str(uuid.uuid4())
        entry = {
            "id": existing_id,
            "clinic": record.get("clinic", record["qb_customer"]),
            "qb_customer": record["qb_customer"],
            "billing_month": record["billing_month"],
            "quarter_covered": record.get("quarter_covered", ""),
            "route": record.get("route", "direct"),
            "gross_overage": float(record.get("gross_overage", 0.0)),
            "credit_applied": float(record.get("credit_applied", 0.0)),
            "net_amount": float(record.get("net_amount", 0.0)),
            "date_billed": record.get("date_billed", dt.date.today().isoformat()),
            "invoice_no": record.get("invoice_no", ""),
            "paid_at": None,
            "paid_amount": None,
            "paid_note": "",
            "notes": record.get("notes", ""),
            "created_at": now,
            "created_by": actor,
            "updated_at": now,
            "updated_by": actor,
        }
        data["entries"][existing_id] = entry

    _save(data, sha, f"overage_ledger: upsert {entry['clinic']} {entry['billing_month']}")
    return existing_id


def mark_paid(entry_id: str, paid_amount: float, paid_date: str,
              note: str = "", actor: str = "") -> None:
    """Mark an entry paid. paid_date is 'YYYY-MM-DD'."""
    data, sha = _load()
    entry = data["entries"].get(entry_id)
    if not entry:
        raise KeyError(f"overage ledger entry {entry_id} not found")
    # Basic validation
    dt.date.fromisoformat(paid_date)  # raises if malformed
    entry["paid_at"] = paid_date
    entry["paid_amount"] = float(paid_amount)
    entry["paid_note"] = note
    entry["updated_at"] = _now_iso()
    entry["updated_by"] = actor
    _save(data, sha, f"overage_ledger: mark paid {entry['clinic']} {entry['billing_month']}")


def unmark_paid(entry_id: str, actor: str = "") -> None:
    """Reverse a paid mark (mistake correction)."""
    data, sha = _load()
    entry = data["entries"].get(entry_id)
    if not entry:
        raise KeyError(f"overage ledger entry {entry_id} not found")
    entry["paid_at"] = None
    entry["paid_amount"] = None
    entry["paid_note"] = ""
    entry["updated_at"] = _now_iso()
    entry["updated_by"] = actor
    _save(data, sha, f"overage_ledger: unmark paid {entry['clinic']} {entry['billing_month']}")


def delete(entry_id: str, actor: str = "") -> None:
    data, sha = _load()
    if entry_id in data["entries"]:
        clinic = data["entries"][entry_id].get("clinic", "?")
        billing_month = data["entries"][entry_id].get("billing_month", "?")
        del data["entries"][entry_id]
        _save(data, sha, f"overage_ledger: delete {clinic} {billing_month}")


# ---------------------------------------------------------------------------
# Lockout logic
# ---------------------------------------------------------------------------

def status(entry: dict, today: dt.date | None = None) -> str:
    """One of STATUS_PAID | STATUS_OPEN | STATUS_WARNING | STATUS_LOCKED_OUT."""
    if entry.get("paid_at"):
        return STATUS_PAID
    if not entry.get("date_billed"):
        return STATUS_OPEN
    today = today or dt.date.today()
    try:
        billed = dt.date.fromisoformat(entry["date_billed"])
    except (ValueError, TypeError):
        return STATUS_OPEN
    lockout_at = billed + relativedelta(months=LOCKOUT_MONTHS)
    warning_at = billed + relativedelta(months=WARNING_MONTHS)
    if today >= lockout_at:
        return STATUS_LOCKED_OUT
    if today >= warning_at:
        return STATUS_WARNING
    return STATUS_OPEN


def days_until_lockout(entry: dict, today: dt.date | None = None) -> int | None:
    """Days until this entry crosses into locked_out. Negative = already past.
    Returns None if entry is already paid or has no billing date."""
    if entry.get("paid_at") or not entry.get("date_billed"):
        return None
    today = today or dt.date.today()
    try:
        billed = dt.date.fromisoformat(entry["date_billed"])
    except (ValueError, TypeError):
        return None
    lockout_at = billed + relativedelta(months=LOCKOUT_MONTHS)
    return (lockout_at - today).days


def locked_out_clinics(today: dt.date | None = None) -> set[str]:
    """Set of qb_customer names currently locked out (any unpaid overage aged out)."""
    today = today or dt.date.today()
    out = set()
    for e in all_entries():
        if status(e, today) == STATUS_LOCKED_OUT:
            out.add(e.get("qb_customer", "").strip())
    out.discard("")
    return out


def summarize(today: dt.date | None = None) -> dict:
    """Portfolio-level snapshot for the tracker header row."""
    today = today or dt.date.today()
    counts = {STATUS_PAID: 0, STATUS_OPEN: 0, STATUS_WARNING: 0, STATUS_LOCKED_OUT: 0}
    total_open = 0.0
    total_locked_out = 0.0
    total_collected = 0.0
    for e in all_entries():
        st = status(e, today)
        counts[st] += 1
        net = float(e.get("net_amount") or 0)
        if st == STATUS_PAID:
            total_collected += float(e.get("paid_amount") or 0)
        else:
            total_open += net
            if st == STATUS_LOCKED_OUT:
                total_locked_out += net
    return {
        "counts": counts,
        "total_open": total_open,
        "total_locked_out": total_locked_out,
        "total_collected": total_collected,
        "locked_out_clinics": sorted(locked_out_clinics(today)),
    }


# ---------------------------------------------------------------------------
# Bulk helpers — hook into FLEX Cycle overage generation
# ---------------------------------------------------------------------------

def record_from_annotation(row: dict, billing_month: str, quarter_covered: str,
                           date_billed: str, actor: str = "") -> str | None:
    """Write one overage-ledger row from a `flex_overage.annotate_overages` output row.

    Tolerates both the annotate_overages shape (`clinic_name`, `qb_name`,
    `overage`, `net_overage`) and the friendlier shape used by the manual
    entry form (`clinic`, `qb_customer`, `gross_overage`, `net_amount`).
    Skips rows where net <= 0 (no bill produced).
    """
    net = float(row.get("net_overage") or row.get("net_amount") or 0)
    if net <= 0:
        return None
    qb_customer = row.get("qb_customer") or row.get("qb_name") or ""
    clinic = row.get("clinic") or row.get("clinic_name") or qb_customer
    gross = float(row.get("gross_overage") or row.get("overage") or 0)
    return upsert({
        "clinic": clinic,
        "qb_customer": qb_customer,
        "billing_month": billing_month,
        "quarter_covered": quarter_covered,
        "route": row.get("route", "direct"),
        "gross_overage": gross,
        "credit_applied": float(row.get("credit_applied") or 0),
        "net_amount": net,
        "date_billed": date_billed,
        "invoice_no": row.get("invoice_no", ""),
        "notes": "",
    }, actor=actor)


def record_batch(rows: Iterable[dict], billing_month: str, quarter_covered: str,
                 date_billed: str, actor: str = "") -> list[str]:
    ids = []
    for r in rows:
        eid = record_from_annotation(r, billing_month, quarter_covered, date_billed, actor)
        if eid:
            ids.append(eid)
    return ids
