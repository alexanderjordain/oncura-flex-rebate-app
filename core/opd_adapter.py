"""OPD export adapter.

The real OPD export format is not yet known (a coworker has API access; until then we
ingest file exports). This module is the SEAM: it takes whatever columns the export has,
maps them to a normalized internal schema, and classifies each line into a rebate category.

When the real export arrives, the only thing that should need to change is the column
mapping (auto-detected here, or set explicitly) and data/opd_item_map.json.

Normalized line schema (one row per invoice line item):
    clinic       str   clinic / customer name as it appears in the export
    invoice_id   str   invoice or document number (optional)
    item_code    str   item / product / service code (optional)
    item_desc    str   line description
    category     str   one of opd_item_map categories (ultrasound/stat/.../rads/other)
    amount       float line amount (revenue)
    date         str   ISO date (YYYY-MM-DD) if parseable, else original string
"""
from __future__ import annotations

import datetime as _dt

import pandas as pd

NORM_COLUMNS = ["clinic", "invoice_id", "item_code", "item_desc", "category", "amount", "date"]

# Feed's own pre-computed per-line rebate columns (OData ConsultService). Passed through so
# the engine can reconcile its rate-based recompute against what the feed says.
FEED_COLUMNS = ["feed_us_finance", "feed_us_cash", "feed_rad_finance", "feed_rad_cash"]
_ODATA_FEED_SOURCE = {
    "feed_us_finance": "RebateUltrasoundFinance",
    "feed_us_cash": "RebateUltrasoundCash",
    "feed_rad_finance": "RebateRadFinance",
    "feed_rad_cash": "RebateRadCash",
}

# Candidate source header names for each normalized field, lowercased substrings.
# Auto-detection picks the first source column whose header contains any candidate.
_HEADER_CANDIDATES = {
    "clinic": ["clinic", "customer", "hospital", "account name", "patient account", "company"],
    "invoice_id": ["invoice", "doc number", "document", "txn", "transaction", "ref"],
    "item_code": ["item code", "product code", "sku", "item id", "service code", "code"],
    "item_desc": ["description", "item", "product", "service", "memo", "line desc"],
    "amount": ["amount", "total", "line total", "subtotal", "ext price", "revenue", "paid"],
    "date": ["date", "invoice date", "txn date", "service date"],
}

# OPD OData ConsultService feed: the real rebate source. Exact-match headers preferred.
_ODATA_FIELDS = {
    "clinic": "ClinicName",
    "invoice_id": "ConsultCaseID",
    "item_code": "TrentCode",
    "item_desc": "ServiceName",
    "amount": "FinalizedLneItemCost",
    "date": "ConsultAdjFinalizedDate",
    "scan_eligible": "ScanEligible",
}


def detect_profile(headers) -> str:
    """'odata' = ConsultService feed; 'case_grid' = OPD consult-grid export (Case ID / Services
    string per case, no per-line prices — needs a price table); else 'generic'."""
    hset = {str(h).strip() for h in headers}
    if "ServiceName" in hset and "ScanEligible" in hset:
        return "odata"
    if {"Case ID", "Services", "Clinic"}.issubset(hset):
        return "case_grid"
    return "generic"


def _parse_bool(v) -> bool:
    """ScanEligible arrives as bool True/False AND string 'True'/'False' in the same feed."""
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("true", "1", "yes", "y", "t")


def auto_detect_mapping(headers: list[str]) -> dict:
    """Best-effort map of normalized field -> source header. Missing fields are omitted."""
    lower = {h: str(h).strip().lower() for h in headers}
    mapping = {}
    used = set()
    for field, cands in _HEADER_CANDIDATES.items():
        for h in headers:
            if h in used:
                continue
            hl = lower[h]
            if any(c in hl for c in cands):
                mapping[field] = h
                used.add(h)
                break
    return mapping


def _coerce_date(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.strftime("%Y-%m-%d")
    try:
        return pd.to_datetime(v).strftime("%Y-%m-%d")
    except Exception:
        return str(v)


def _coerce_amount(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        val = float(s)
        return -val if neg else val
    except Exception:
        return 0.0


def classify(item_code, item_desc, item_map: dict) -> str:
    """Exact code_map match first, then keyword_rules against code+desc, else 'other'."""
    code = ("" if item_code is None else str(item_code)).strip()
    code_map = item_map.get("code_map", {})
    if code and code in code_map:
        return code_map[code]
    hay = f"{code} {item_desc or ''}".lower()
    for rule in item_map.get("keyword_rules", []):
        needle = str(rule.get("contains", "")).lower()
        if needle and needle in hay:
            return rule.get("category", "other")
    return "other"


def classify_odata(service_name, trent_code, scan_eligible, item_map: dict) -> str:
    """Classify a ConsultService line. ScanEligible wins (ultrasound); then ServiceName
    keywords (STAT / Non-EMA / Exam Assist / Radiograph); then TrentCode group."""
    od = item_map.get("odata", {})
    if od.get("scan_eligible_means_ultrasound", True) and _parse_bool(scan_eligible):
        return "ultrasound"
    sn = ("" if service_name is None else str(service_name)).lower()
    for rule in od.get("servicename_rules", []):
        needle = str(rule.get("contains", "")).lower()
        if needle and needle in sn:
            return rule.get("category", "other")
    tc = ("" if trent_code is None else str(trent_code))
    grp = tc.replace("Trent-", "").split("-")[0] if tc else ""
    for rule in od.get("trentcode_group_rules", []):
        if grp and grp.lower() == str(rule.get("group", "")).lower():
            return rule.get("category", "other")
    return "other"


def normalize(df: pd.DataFrame, mapping: dict | None, item_map: dict, profile: str | None = None,
              price_table: dict | None = None) -> pd.DataFrame:
    """Apply the column mapping + classification to a raw export DataFrame.

    mapping: normalized field -> source column. If None, auto-detected.
    item_map: contents of data/opd_item_map.json.
    profile: 'odata' | 'case_grid' | 'generic' | None (auto-detect).
    price_table: contents of data/service_prices.json — required for the case_grid profile.
    """
    if profile is None:
        profile = detect_profile(list(df.columns))

    if profile == "odata":
        return _normalize_odata(df, item_map)

    if profile == "case_grid":
        return _normalize_case_grid(df, item_map, price_table or {})

    if mapping is None:
        mapping = auto_detect_mapping(list(df.columns))

    out = pd.DataFrame()
    out["clinic"] = df[mapping["clinic"]].astype(str).str.strip() if "clinic" in mapping else ""
    out["invoice_id"] = (
        df[mapping["invoice_id"]].astype(str).str.strip() if "invoice_id" in mapping else ""
    )
    out["item_code"] = (
        df[mapping["item_code"]].astype(str).str.strip() if "item_code" in mapping else ""
    )
    out["item_desc"] = (
        df[mapping["item_desc"]].astype(str).str.strip() if "item_desc" in mapping else ""
    )
    out["amount"] = (
        df[mapping["amount"]].map(_coerce_amount) if "amount" in mapping else 0.0
    )
    out["date"] = df[mapping["date"]].map(_coerce_date) if "date" in mapping else None

    out["category"] = [
        classify(c, d, item_map) for c, d in zip(out["item_code"], out["item_desc"])
    ]
    for col in FEED_COLUMNS:
        out[col] = 0.0  # generic exports carry no pre-computed feed rebate
    return out[NORM_COLUMNS + FEED_COLUMNS]


def _normalize_odata(df: pd.DataFrame, item_map: dict) -> pd.DataFrame:
    f = _ODATA_FIELDS
    out = pd.DataFrame()
    out["clinic"] = df[f["clinic"]].astype(str).str.strip() if f["clinic"] in df else ""
    out["invoice_id"] = (
        df[f["invoice_id"]].astype(str).str.strip() if f["invoice_id"] in df else ""
    )
    out["item_code"] = df[f["item_code"]].astype(str).str.strip() if f["item_code"] in df else ""
    out["item_desc"] = df[f["item_desc"]].astype(str).str.strip() if f["item_desc"] in df else ""
    out["amount"] = df[f["amount"]].map(_coerce_amount) if f["amount"] in df else 0.0
    out["date"] = df[f["date"]].map(_coerce_date) if f["date"] in df else None

    scan = df[f["scan_eligible"]] if f["scan_eligible"] in df else [None] * len(df)
    out["category"] = [
        classify_odata(sn, tc, se, item_map)
        for sn, tc, se in zip(out["item_desc"], out["item_code"], scan)
    ]
    for col, src in _ODATA_FEED_SOURCE.items():
        out[col] = df[src].map(_coerce_amount) if src in df else 0.0
    return out[NORM_COLUMNS + FEED_COLUMNS]


def parse_case_grid_services(s) -> list:
    """Split the comma-joined Services string into individual, whitespace-normalized services."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return []
    out = []
    for part in str(s).split(","):
        norm = " ".join(part.split())
        if norm:
            out.append(norm)
    return out


def _normalize_case_grid(df: pd.DataFrame, item_map: dict, price_table: dict) -> pd.DataFrame:
    """Expand each case row into one normalized line per service in the Services string.

    Prices and categories come from the flat price_table (data/service_prices.json). Cases with
    Priority='STAT' get an implicit STAT line at the configured stat_fee, unless a STAT service
    is already present in the row (avoids double-counting)."""
    services_lut = price_table.get("services", {}) if isinstance(price_table, dict) else {}
    norm_lut = {" ".join(str(k).split()).lower(): v for k, v in services_lut.items()}
    stat_fee = float(price_table.get("stat_fee", 125.0) if isinstance(price_table, dict) else 125.0)

    rows = []
    for _, r in df.iterrows():
        clinic = r.get("Clinic", "")
        case_id = r.get("Case ID", "")
        date_raw = r.get("Finalized Date") if "Finalized Date" in df.columns else r.get("Submitted")
        priority = str(r.get("Priority", "")).strip().upper()
        services = parse_case_grid_services(r.get("Services", ""))
        has_stat = any("stat" in s.lower() for s in services)
        if priority == "STAT" and not has_stat:
            services = services + ["STAT Sonographer Assistance Fee"]
        for svc in services:
            entry = norm_lut.get(svc.lower(), {})
            price = float(entry.get("price", 0.0)) if isinstance(entry, dict) else 0.0
            category = entry.get("category", "other") if isinstance(entry, dict) else "other"
            # category override: if a STAT line was synthesized for STAT priority, force the fee
            if svc == "STAT Sonographer Assistance Fee" and price == 0.0:
                price = stat_fee
                category = "stat"
            rows.append({
                "clinic": str(clinic).strip(),
                "invoice_id": str(case_id) if case_id is not None else "",
                "item_code": "",
                "item_desc": svc,
                "category": category,
                "amount": price,
                "date": _coerce_date(date_raw),
                "feed_us_finance": 0.0, "feed_us_cash": 0.0,
                "feed_rad_finance": 0.0, "feed_rad_cash": 0.0,
            })
    return pd.DataFrame(rows, columns=NORM_COLUMNS + FEED_COLUMNS)


def read_upload(file) -> pd.DataFrame:
    """Read an uploaded CSV/XLSX file-like object into a raw DataFrame."""
    name = getattr(file, "name", "").lower()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(file)
    return pd.read_csv(file)


_HEADER_TOKENS = {"contract", "customer", "payment", "amount", "paid", "received", "invoice", "due", "date"}


def clean_columns(cols) -> list:
    """Collapse newlines/whitespace in header names; rename blank/nan headers to col_N."""
    out = []
    for i, c in enumerate(cols):
        s = " ".join(str(c).replace("\n", " ").replace("\r", " ").split())
        out.append(s if s and s.lower() != "nan" else f"col_{i}")
    return out


def _detect_header_row(raw: pd.DataFrame, max_scan: int = 15) -> int:
    """Find the row that looks most like a header (matches the most expected tokens).
    Handles files with title/preamble rows above the header (e.g. OnePlace)."""
    best_idx, best_score = 0, 0
    for i in range(min(max_scan, len(raw))):
        cells = [str(v).lower() for v in raw.iloc[i] if pd.notna(v)]
        score = sum(1 for cell in cells if any(tok in cell for tok in _HEADER_TOKENS))
        if score > best_score:
            best_score, best_idx = score, i
    return best_idx if best_score >= 2 else 0


def read_remittance(file) -> pd.DataFrame:
    """Read a finance remittance, locating the real header row and cleaning column names.
    Robust to preamble rows (OnePlace) and newline-laden headers."""
    name = getattr(file, "name", "").lower()
    if name.endswith((".xlsx", ".xls")):
        raw = pd.read_excel(file, header=None)
    else:
        raw = pd.read_csv(file, header=None)
    hidx = _detect_header_row(raw)
    cols = clean_columns(list(raw.iloc[hidx]))
    df = raw.iloc[hidx + 1:].copy()
    df.columns = cols
    df = df.dropna(how="all")
    df = df.dropna(axis=1, how="all")  # drop fully-empty columns (e.g. OnePlace leading blank)
    return df.reset_index(drop=True)


# OPD OData Invoices tab -> FLEX overage/unused activity (Subtotal + Admin Fee per clinic).
_INVOICE_FIELDS = {
    "clinic": "ClinicName",
    "subtotal": "SubtotalPrice",
    "admin": "AdminFee",
    "date": "InvoiceDate",
    "isflex": "isFlex",
}


def flex_activity_from_invoices(df: pd.DataFrame, start=None, end=None, flex_only=False) -> dict:
    """Total OPD telemedicine activity (Subtotal + Admin Fee) per clinic over [start, end].

    Returns {clinic_name_lower: activity}. start/end are date-like (inclusive) or None.
    """
    f = _INVOICE_FIELDS
    clinic_col = f["clinic"] if f["clinic"] in df else None
    if clinic_col is None:
        # fall back to fuzzy header detection
        m = auto_detect_mapping(list(df.columns))
        clinic_col = m.get("clinic")
    if clinic_col is None:
        return {}

    sub = df.get(f["subtotal"])
    admin = df.get(f["admin"])
    activity = pd.Series(0.0, index=df.index)
    if sub is not None:
        activity = activity.add(sub.map(_coerce_amount), fill_value=0.0)
    if admin is not None:
        activity = activity.add(admin.map(_coerce_amount), fill_value=0.0)

    mask = pd.Series(True, index=df.index)
    if start is not None or end is not None:
        dates = pd.to_datetime(df.get(f["date"]), errors="coerce")
        if start is not None:
            mask &= dates >= pd.Timestamp(start)
        if end is not None:
            mask &= dates <= pd.Timestamp(end)
    if flex_only and f["isflex"] in df:
        mask &= df[f["isflex"]].map(_parse_bool)

    work = pd.DataFrame({"clinic": df[clinic_col].astype(str).str.strip().str.lower(), "activity": activity})
    work = work[mask]
    return work.groupby("clinic")["activity"].sum().round(2).to_dict()


def flex_activity_from_case_grid(df: pd.DataFrame, price_table: dict, start=None, end=None) -> dict:
    """Per-clinic FLEX activity from the case-grid export: sum of priced services per case
    (including the STAT $125 add-on for STAT-priority cases) over [start, end].

    Returns {clinic_name_lower: activity}.
    """
    norm = _normalize_case_grid(df, item_map={}, price_table=price_table)
    if norm.empty:
        return {}
    mask = pd.Series(True, index=norm.index)
    if start is not None or end is not None:
        dates = pd.to_datetime(norm["date"], errors="coerce")
        if start is not None:
            mask &= dates >= pd.Timestamp(start)
        if end is not None:
            mask &= dates <= pd.Timestamp(end)
    sub = norm[mask]
    sub = sub.assign(clinic_lower=sub["clinic"].str.lower())
    return sub.groupby("clinic_lower")["amount"].sum().round(2).to_dict()
