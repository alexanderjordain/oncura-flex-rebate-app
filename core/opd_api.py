"""Live OPD Mendix OData v3 client — fetches invoice data directly so Stage 3
of the FLEX Cycle doesn't require an operator to pull files manually.

Endpoint: https://telehealth.oncurapartners.com/odata/Consults (Mendix v3).
Auth:     HTTP Basic — credentials in st.secrets (OPD_ODATA_USER / OPD_ODATA_PASS).
Format:   Mendix v3 returns Atom XML regardless of Accept header — we parse it.

Two pulls per Stage 3 run:
  fetch_clinic_index() -> {Mendix_internal_id: ClinicName}
  fetch_invoices_for_quarter(...)  -> DataFrame with one row per invoice

Then `flex_activity_for_quarter()` is the convenience that gives Stage 3 the
same {clinic_lower: total_price} dict shape that `opd_adapter.flex_activity_from_invoices`
returns from a file upload — drop-in compatible.

Two reconciliation guards baked in:

1. **Timezone:** Mendix InvoiceDate is UTC (e.g. `2026-06-01T04:00:03.939Z` for
   what the OPD UI labels May 31). We pad the server-side filter by ±24h, then
   filter precisely on the EDT/EST local-date projection client-side. Robust
   regardless of Mendix's filter interpretation.

2. **Credit math:** `TotalPrice` is the authoritative net (matches OPD UI). The
   formula `Sub − Credit − OldCredit − MiscCredit + AdminFee` reconciles in ~95%
   of rows; misses are typically $4-AdminFee-only voided invoices. We track
   reconciliation status per row and surface it in the cycle metadata; the
   activity total always uses `TotalPrice` directly.
"""
from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

# ── Configuration ──────────────────────────────────────────────────────────────
BASE_URL = "https://telehealth.oncurapartners.com/odata/Consults"
INVOICES_PATH = f"{BASE_URL}/Invoices"
CLINIC_PATH = f"{BASE_URL}/Clinic"

# Mendix v3 here doesn't emit OData server-driven pagination (`rel=next`) — only
# a self-link — but it DOES honor large `$top` values. Quarter pulls top out
# around 2.5k invoices in 2026; 10k gives plenty of headroom. If a single
# quarter ever exceeds 10k we'd see truncation, hence the assertion in
# fetch_invoices_for_quarter against the server's $inlinecount.
PAGE_SIZE = 10000
REQUEST_TIMEOUT = 60

# OPD bills at month-end via a Mendix rollover that fires at midnight LOCAL
# (US Eastern). The InvoiceDate UTC timestamp on a "May 31" invoice is
# `2026-06-01T04:00:03Z` — i.e., the moment of write, which is just after
# midnight local. The OPD UI labels that invoice "May 31" (the last day of
# the billed month). _utc_to_billing_date() projects UTC -> local with full
# DST awareness, then backshifts one day for rollover-boundary timestamps so
# the result matches what the OPD UI displays.
ZONE_EASTERN = ZoneInfo("America/New_York")
_BOUNDARY_TOLERANCE_MINUTES = 5  # write latency observed at <1s; 5min gives margin

_ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
    "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
}
_CLINIC_ID_RE = re.compile(r"/Clinic\((\d+)\)")


# ── Auth ───────────────────────────────────────────────────────────────────────
def auth_from_secrets() -> HTTPBasicAuth:
    """Build an HTTPBasicAuth from st.secrets. Raises a clear error if missing.

    Lazy import of streamlit so the module imports cleanly from pytest.
    """
    import streamlit as st  # noqa: PLC0415

    user = st.secrets.get("OPD_ODATA_USER")
    pwd = st.secrets.get("OPD_ODATA_PASS")
    if not user or not pwd:
        raise RuntimeError(
            "OPD OData credentials not configured. Set OPD_ODATA_USER and "
            "OPD_ODATA_PASS in .streamlit/secrets.toml (locally) or in the "
            "Streamlit Cloud Secrets vault."
        )
    return HTTPBasicAuth(str(user), str(pwd))


# ── Atom XML parsing ───────────────────────────────────────────────────────────
def _parse_atom_entries(xml_text: str) -> list[dict]:
    """Extract property bags from an Atom feed. Each <entry> -> dict of its
    <m:properties> children. The entry's <id> URL is also captured under
    `_entry_id` so callers can pull internal Mendix IDs.
    """
    root = ET.fromstring(xml_text)
    out: list[dict] = []
    for entry in root.findall("a:entry", _ATOM_NS):
        props = entry.find("a:content/m:properties", _ATOM_NS)
        if props is None:
            props = entry.find("m:properties", _ATOM_NS)
        if props is None:
            continue
        row: dict = {}
        for child in props:
            tag = child.tag.split("}")[-1]
            row[tag] = child.text
        id_el = entry.find("a:id", _ATOM_NS)
        if id_el is not None and id_el.text:
            row["_entry_id"] = id_el.text
        out.append(row)
    return out


def _inlinecount(xml_text: str) -> int | None:
    """Pull <m:count> from an `$inlinecount=allpages` response. None if absent."""
    root = ET.fromstring(xml_text)
    el = root.find(f".//{{{_ATOM_NS['m']}}}count")
    if el is not None and el.text:
        try:
            return int(el.text)
        except ValueError:
            return None
    return None


def _coerce_decimal(s) -> float:
    if s is None or s == "":
        return 0.0
    try:
        return float(Decimal(str(s)))
    except Exception:
        return 0.0


def _parse_dt(s) -> dt.datetime | None:
    """Parse '2026-06-01T04:00:03.939Z' (or without millis / Z) to a naive
    UTC datetime. Returns None on failure."""
    if not s:
        return None
    s = str(s).strip()
    # Strip trailing Z (we treat values as UTC explicitly)
    if s.endswith("Z"):
        s = s[:-1]
    # Drop fractional seconds — fromisoformat in pre-3.11 chokes on >6 digits
    if "." in s:
        s = s.split(".")[0]
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def _utc_to_billing_date(utc_dt: dt.datetime | None) -> dt.date | None:
    """Project a UTC datetime to the local date the OPD UI labels it.

    OPD's month-end rollover fires at midnight LOCAL on the 1st of the next
    month. Its UTC timestamp lands at the same instant (04:00Z EDT, 05:00Z
    EST). The OPD UI labels that invoice as the LAST day of the prior month —
    that's the billed date. For any rollover-boundary timestamp (local time
    within `_BOUNDARY_TOLERANCE_MINUTES` of midnight on day 1) we backshift one
    day so the date matches what the operator sees. Mid-month manual invoices
    keep their own local date.
    """
    if utc_dt is None:
        return None
    aware = utc_dt.replace(tzinfo=dt.timezone.utc)
    local = aware.astimezone(ZONE_EASTERN)
    if local.day == 1 and local.hour == 0 and local.minute < _BOUNDARY_TOLERANCE_MINUTES:
        local = local - dt.timedelta(days=1)
    return local.date()


# ── Fetch ──────────────────────────────────────────────────────────────────────
def _fetch_all(url: str, *, auth: HTTPBasicAuth, params: dict | None = None) -> tuple[list[dict], int | None]:
    """Single-shot fetch with `$top=PAGE_SIZE` and `$inlinecount=allpages`.

    Returns (rows, server_reported_total). If server_reported_total exceeds
    len(rows), the caller should treat that as a configuration error — bump
    PAGE_SIZE — rather than silently dropping invoices.
    """
    page_params = dict(params or {})
    page_params.setdefault("$top", PAGE_SIZE)
    page_params.setdefault("$inlinecount", "allpages")
    r = requests.get(url, auth=auth, params=page_params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    rows = _parse_atom_entries(r.text)
    return rows, _inlinecount(r.text)


# ── Clinic index ───────────────────────────────────────────────────────────────
def fetch_clinic_index(auth: HTTPBasicAuth | None = None) -> dict[int, str]:
    """Return {Mendix_internal_id (int): ClinicName (str)} for every clinic.

    The Invoice entity carries `Invoice_Clinic` as an Int64 FK pointing to the
    Clinic entity's internal Mendix ID (NOT the business-key `ClinicID` like
    'AAH60631'). We need this map to render invoice rows with clinic names.
    """
    auth = auth or auth_from_secrets()
    rows, total = _fetch_all(
        CLINIC_PATH, auth=auth,
        params={"$select": "ClinicName"},  # internal ID comes from entry @id
    )
    if total is not None and total > len(rows):
        raise RuntimeError(
            f"Clinic index truncated: server reports {total} clinics but only "
            f"{len(rows)} fetched. Increase PAGE_SIZE in core.opd_api."
        )
    index: dict[int, str] = {}
    for row in rows:
        eid = row.get("_entry_id") or ""
        m = _CLINIC_ID_RE.search(eid)
        if not m:
            continue
        try:
            internal = int(m.group(1))
        except ValueError:
            continue
        name = (row.get("ClinicName") or "").strip()
        if name:
            index[internal] = name
    return index


# ── Invoice fetch ──────────────────────────────────────────────────────────────
INVOICE_COLUMNS = [
    "invoice_internal_id", "invoice_clinic_fk", "clinic_name",
    "invoice_date_utc", "invoice_date_local",
    "status", "subtotal", "credit", "old_credit", "misc_credit",
    "admin_fee", "total_price", "consult_count",
    "paid_date_utc", "transaction_date_utc",
    "components_match", "components_delta",
]


def fetch_invoices_for_quarter(
    year: int, end_month: int,
    *,
    auth: HTTPBasicAuth | None = None,
    clinic_index: dict[int, str] | None = None,
    statuses: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Fetch all invoices whose local-date InvoiceDate falls in the quarter
    ending in (year, end_month). Returns a DataFrame with INVOICE_COLUMNS.

    `statuses`: optional whitelist (e.g. ('Paid', 'Invoiced')). None = all.

    The OData filter uses a ±24h UTC pad around the local quarter window so
    timezone-shifted boundary invoices don't slip out. Final filtering is done
    in Python against `invoice_date_local`.
    """
    auth = auth or auth_from_secrets()
    if clinic_index is None:
        clinic_index = fetch_clinic_index(auth)

    # Quarter window in local calendar terms.
    from . import flex_unused  # local import to avoid cycle at module load
    win_start_local, win_end_local = flex_unused.quarter_window(year, end_month)

    # UTC pad: start - 24h, end + 24h. The local date filter narrows precisely.
    pad = dt.timedelta(hours=24)
    win_start_utc = (
        dt.datetime.combine(win_start_local, dt.time(0, 0)) - pad
    )
    win_end_utc = (
        dt.datetime.combine(win_end_local, dt.time(23, 59, 59)) + pad
    )

    flt = (
        f"InvoiceDate ge datetime'{win_start_utc.isoformat(timespec='seconds')}' "
        f"and InvoiceDate le datetime'{win_end_utc.isoformat(timespec='seconds')}'"
    )

    raw_rows, server_total = _fetch_all(
        INVOICES_PATH, auth=auth,
        params={"$filter": flt},
    )
    if server_total is not None and server_total > len(raw_rows):
        raise RuntimeError(
            f"Invoice fetch truncated: server reports {server_total} matching "
            f"invoices in window but only {len(raw_rows)} fetched. Increase "
            f"PAGE_SIZE in core.opd_api."
        )

    norm_rows: list[dict] = []
    for r in raw_rows:
        utc_dt = _parse_dt(r.get("InvoiceDate"))
        local_d = _utc_to_billing_date(utc_dt)
        # Skip rows whose local date falls outside the precise quarter window —
        # the ±24h pad would otherwise admit them.
        if local_d is None or local_d < win_start_local or local_d > win_end_local:
            continue
        status = (r.get("InvoiceStatus") or "").strip()
        if statuses is not None and status not in statuses:
            continue

        sub = _coerce_decimal(r.get("SubtotalPrice"))
        cr = _coerce_decimal(r.get("Credit"))
        oc = _coerce_decimal(r.get("OldCredit"))
        mc = _coerce_decimal(r.get("MiscCredit"))
        ad = _coerce_decimal(r.get("AdminFee"))
        tot = _coerce_decimal(r.get("TotalPrice"))
        # Components formula: Mendix double-writes OldCredit and MiscCredit
        # (legacy schema refactor) — applying both as credits double-counts.
        # `max(OldCredit, MiscCredit)` is the actual credit applied, validated
        # against 935+ rows where one of the two is the larger / authoritative
        # value. TotalPrice is still authoritative; this check just surfaces
        # the rare orphan ($4-AdminFee voids etc.) for audit.
        comp_total = sub - cr - max(oc, mc) + ad
        delta = round(tot - comp_total, 2)
        components_match = abs(delta) < 1.00

        inv_clinic_fk_raw = r.get("Invoice_Clinic")
        try:
            inv_clinic_fk = int(inv_clinic_fk_raw) if inv_clinic_fk_raw else None
        except ValueError:
            inv_clinic_fk = None
        clinic_name = clinic_index.get(inv_clinic_fk) if inv_clinic_fk else None

        # Internal invoice ID from the entry URL — useful for dedup / audit.
        eid = r.get("_entry_id") or ""
        m = re.search(r"/Invoices\((\d+)\)", eid)
        invoice_internal_id = int(m.group(1)) if m else None

        norm_rows.append({
            "invoice_internal_id": invoice_internal_id,
            "invoice_clinic_fk": inv_clinic_fk,
            "clinic_name": clinic_name,
            "invoice_date_utc": utc_dt.isoformat() if utc_dt else None,
            "invoice_date_local": local_d.isoformat(),
            "status": status,
            "subtotal": round(sub, 2),
            "credit": round(cr, 2),
            "old_credit": round(oc, 2),
            "misc_credit": round(mc, 2),
            "admin_fee": round(ad, 2),
            "total_price": round(tot, 2),
            "consult_count": int(_coerce_decimal(r.get("ConsultCount"))) if r.get("ConsultCount") else 0,
            "paid_date_utc": (_parse_dt(r.get("PaidDate")) or "") and _parse_dt(r.get("PaidDate")).isoformat(),
            "transaction_date_utc": (_parse_dt(r.get("TransactionDate")) or "") and _parse_dt(r.get("TransactionDate")).isoformat(),
            "components_match": components_match,
            "components_delta": delta,
        })

    return pd.DataFrame(norm_rows, columns=INVOICE_COLUMNS)


# ── Convenience: drop-in replacement for opd_adapter.flex_activity_from_invoices
def flex_activity_for_quarter(
    year: int, end_month: int,
    *,
    auth: HTTPBasicAuth | None = None,
) -> tuple[dict[str, float], pd.DataFrame]:
    """Pull the quarter's invoices live and return:
      - {clinic_lower: TotalPrice sum} (same shape as flex_activity_from_invoices)
      - The raw DataFrame (for audit + UI display)
    """
    df = fetch_invoices_for_quarter(year, end_month, auth=auth)
    if df.empty:
        return {}, df
    by_clinic = (
        df.dropna(subset=["clinic_name"])
        .assign(_key=lambda d: d["clinic_name"].str.strip().str.lower())
        .groupby("_key")["total_price"]
        .sum()
        .round(2)
        .to_dict()
    )
    return by_clinic, df
