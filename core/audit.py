"""Per-cycle audit manifest — immutable record for every workflow that touches QBO.

Append-only log of every cycle execution. For each entry we record:
  - WHO ran it (approver = current auth role)
  - WHEN (ISO timestamp)
  - WHAT cycle type (stage1_finance_payment / stage2_credit_memo / stage3_recapture / rebate_report)
  - WHAT inputs were used (source file hash + name + size when applicable)
  - WHAT was produced (per-output hash + row count + total amount)
  - WITH WHAT PARAMETERS (year, month, finance_company, sales_class, etc.)
  - APPROVER's optional note

Each entry carries an `entry_hash` = sha256 of the entry's canonical JSON
(everything except entry_hash itself). The GitHub commit history is the
authoritative tamper trail; entry_hash is the in-app integrity check.

Persistence model is identical to core.ledger: GitHub Contents API when
GITHUB_TOKEN is set, local file otherwise. Stored at `data/audit_log.json`.

CYCLE_TYPES is the source-of-truth set — keep it in sync if new workflows are added.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import uuid
from typing import Any

from . import store

AUDIT_PATH = "audit_log.json"

CYCLE_TYPES = {
    "stage1_finance_payment",   # remittance -> SaasAnt flex/scan imports (Stage 1 of FLEX Cycle)
    "stage2_credit_memo",       # monthly credit memos (Stage 2 of FLEX Cycle)
    "stage3_recapture",         # quarter-end unused-recapture invoices (Stage 3 of FLEX Cycle)
    "stage3_overage",           # quarter-end overage direct-bill invoices (Stage 3 of FLEX Cycle)
    "rebate_report",            # multi-month rebate report (Rebate Cycle page)
    "settings_restore",         # data restored from a backup zip (Settings page)
    "settings_save",            # config.json change committed via Settings page
}


def _empty():
    return {"version": 1, "entries": []}


def _load():
    data, sha = store.load_json(AUDIT_PATH, default=_empty())
    if not isinstance(data, dict) or "entries" not in data:
        data = _empty()
    return data, sha


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def output_hash_df(df) -> str:
    """SHA256 of a DataFrame's canonical CSV serialization. Stable across pandas versions."""
    if df is None or len(df) == 0:
        return _sha256_bytes(b"")
    buf = io.StringIO()
    df.to_csv(buf, index=False, lineterminator="\n")
    return _sha256_bytes(buf.getvalue().encode("utf-8"))


def output_hash_bytes(content: bytes | None) -> str:
    """SHA256 of arbitrary byte content (e.g. an xlsx download or uploaded source file)."""
    return _sha256_bytes(content or b"")


def _entry_hash(entry: dict) -> str:
    """SHA256 of the entry's content (excluding entry_hash field itself)."""
    content = {k: v for k, v in entry.items() if k != "entry_hash"}
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return _sha256_bytes(canonical)


def record_cycle(
    *,
    cycle_type: str,
    approver: str,
    year: int | None = None,
    month: int | None = None,
    params: dict | None = None,
    source_file: dict | None = None,
    outputs: list[dict] | None = None,
    note: str = "",
) -> tuple[bool, str, str]:
    """Append a new audit entry. Returns (ok, entry_id, persist_message).

    source_file shape: {"name": str, "sha256": str, "size_bytes": int} or None.
    outputs item shape: {"name": str, "sha256": str, "row_count": int, "total": float}.
    """
    if cycle_type not in CYCLE_TYPES:
        # Don't refuse — just flag so it's visible during review. Never block an audit write.
        note = f"[unknown cycle_type {cycle_type!r}] {note}".strip()

    data, sha = _load()
    entry: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "cycle_type": cycle_type,
        "approver": approver,
        "year": year,
        "month": month,
        "params": params or {},
        "source_file": source_file,
        "outputs": outputs or [],
        "note": note,
    }
    entry["entry_hash"] = _entry_hash(entry)
    data["entries"].append(entry)
    msg = f"Audit: +{cycle_type} ({entry['timestamp'][:10]} by {approver})"
    ok, info = store.save_json(AUDIT_PATH, data, msg, sha=sha)
    return ok, entry["id"], info


def list_entries(limit: int | None = None, cycle_type: str | None = None) -> list[dict]:
    """Most-recent-first. Optional filter by cycle_type."""
    data, _ = _load()
    entries = list(data.get("entries", []))
    if cycle_type:
        entries = [e for e in entries if e.get("cycle_type") == cycle_type]
    entries.reverse()
    if limit is not None:
        return entries[:limit]
    return entries


def verify_integrity() -> tuple[bool, list[str]]:
    """Recompute each entry's hash. Returns (ok, [entry_ids_with_tampering])."""
    data, _ = _load()
    tampered = []
    for entry in data.get("entries", []):
        if "entry_hash" not in entry:
            continue
        original = entry["entry_hash"]
        if _entry_hash(entry) != original:
            tampered.append(entry["id"])
    return len(tampered) == 0, tampered


def summary() -> dict:
    """Quick stats for dashboards."""
    data, _ = _load()
    entries = data.get("entries", [])
    by_type: dict[str, int] = {}
    by_approver: dict[str, int] = {}
    for e in entries:
        by_type[e.get("cycle_type", "?")] = by_type.get(e.get("cycle_type", "?"), 0) + 1
        by_approver[e.get("approver", "?")] = by_approver.get(e.get("approver", "?"), 0) + 1
    latest = max((e.get("timestamp", "") for e in entries), default="")
    return {
        "entry_count": len(entries),
        "by_type": by_type,
        "by_approver": by_approver,
        "latest_timestamp": latest,
    }
