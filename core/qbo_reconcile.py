"""Read-only reconciliation of a QBO "4320 Flex Discount" transaction report
against the app's computed FLEX closeout.

This module never writes anything — not to QBO, not to the ledger. It parses the
standard QBO *Transaction Report* export (xlsx) and, for a given clinic and
quarter, reports what QBO actually holds (monthly credit memos + quarter-end
unused/overage invoices) so the Review & Verify walkthrough can show the QBO
figure beside the app's figure.

Export layout (QBO "Transaction Report", grouped by account):

    row(s)   report title / company / date range
    one row  headers: Transaction date | Transaction type | Num | Name |
             Description | Account Name | Item split account | Amount | Balance
    then     account-group header rows (group name in column 0, rest blank)
             followed by the transaction rows under that group.

Credit-memo coverage month is parsed from the Description
("Flex Credits for April 2026" -> (2026, 4)); $0.00 memos and duplicate
mislabels are surfaced separately rather than silently counted, because the
QBO history has both (see the "less organized" earlier era).
"""
from __future__ import annotations

import calendar
import datetime as dt
import re

import pandas as pd

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}

_OUT_COLUMNS = ["group", "date", "ttype", "num", "name", "desc", "amount", "nname", "cov"]


def _norm(s) -> str:
    return " ".join(str(s or "").casefold().split())


def coverage_month(desc):
    """'Flex Credits for April  2026' -> (2026, 4); None if not parseable.

    Tolerant of doubled spaces and case (both appear in the real export).
    """
    if not isinstance(desc, str):
        return None
    m = re.search(r"for\s+([A-Za-z]+)\s+(\d{4})", desc)
    if not m:
        return None
    mm = _MONTHS.get(m.group(1).strip().lower())
    return (int(m.group(2)), mm) if mm else None


def parse_report(source) -> pd.DataFrame:
    """Parse a QBO Transaction Report into a tidy frame.

    `source` may be a file path, a file-like object (an uploaded file), or an
    already-loaded header-less DataFrame. Returns the columns in `_OUT_COLUMNS`.
    Empty frame (with those columns) if no transaction rows are found.
    Raises ValueError if the sheet is not a recognizable QBO Transaction Report.
    """
    raw = source if isinstance(source, pd.DataFrame) else pd.read_excel(source, header=None)
    raw = raw.reset_index(drop=True)

    hdr = None
    for i in range(min(25, len(raw))):
        cells = [str(x).strip().lower() for x in raw.iloc[i].tolist()]
        if "transaction date" in cells and "amount" in cells:
            hdr = i
            break
    if hdr is None:
        raise ValueError(
            "Not a recognizable QBO Transaction Report: no header row with "
            "'Transaction date' and 'Amount' was found.")

    labels = {}
    for j, x in enumerate(raw.iloc[hdr].tolist()):
        if isinstance(x, str) and x.strip():
            labels[x.strip().lower()] = j
    ci_date = labels.get("transaction date")
    ci_type = labels.get("transaction type")
    ci_num = labels.get("num")
    ci_name = labels.get("name")
    ci_desc = labels.get("description")
    ci_amt = labels.get("amount")

    def _cell(row, ci):
        return row.iloc[ci] if ci is not None and ci < len(row) else None

    rows, group = [], None
    for _, r in raw.iloc[hdr + 1:].iterrows():
        c0 = r.iloc[0] if len(r) else None
        date = _cell(r, ci_date)
        # Account-group header row: leftmost cell has text and there's no date.
        if isinstance(c0, str) and c0.strip() and pd.isna(date):
            group = c0.strip()
            continue
        if pd.isna(date):
            continue
        rows.append({
            "group": group,
            "date": pd.to_datetime(date, errors="coerce"),
            "ttype": _cell(r, ci_type),
            "num": _cell(r, ci_num),
            "name": _cell(r, ci_name),
            "desc": _cell(r, ci_desc),
            "amount": pd.to_numeric(_cell(r, ci_amt), errors="coerce"),
        })

    df = pd.DataFrame(rows, columns=["group", "date", "ttype", "num", "name", "desc", "amount"])
    if df.empty:
        return pd.DataFrame(columns=_OUT_COLUMNS)
    df["nname"] = df["name"].apply(_norm)
    df["cov"] = df["desc"].apply(coverage_month)
    return df


def _last_day(year, month):
    return calendar.monthrange(year, month)[1]


def _is_type(series, want):
    return series.astype(str).str.strip().str.lower() == want


def clinic_summary(df, names, quarter_months, year, end_month, grace_days=15):
    """QBO facts for one clinic (or a pooled group) over the given quarter.

    Read-only. `names` is a clinic name or an iterable of names (a group's
    members). `quarter_months` is an iterable of (year, month) tuples for the
    quarter. Returns::

        {
          "matched": bool,   # any QBO rows under these name(s)
          "cm":  {"count", "total", "months", "zero_count", "rows"[]},
          "recap": {"count", "total", "rows"[]},
        }

    cm counts only non-zero credit memos whose coverage month is in the quarter;
    `zero_count` surfaces $0.00 duplicates separately. `total` is reported as a
    positive credit (QBO stores memos negative). recap rows are Invoice rows
    dated in the quarter-end month through +grace_days (the unused/overage
    posted at close, incl. slightly-late posts).
    """
    if isinstance(names, str):
        names = [names]
    nns = {_norm(n) for n in names if n}
    empty = {
        "matched": False,
        "cm": {"count": 0, "total": 0.0, "months": [], "zero_count": 0, "rows": []},
        "recap": {"count": 0, "total": 0.0, "rows": []},
    }
    if df is None or df.empty or not nns:
        return empty

    qmonths = {tuple(m) for m in quarter_months}
    sub = df[df["nname"].isin(nns)]
    if sub.empty:
        return empty

    cms = sub[_is_type(sub["ttype"], "credit memo")]
    cms = cms[cms["cov"].apply(lambda c: c in qmonths)]
    nonzero = cms[cms["amount"] != 0]
    zero = cms[cms["amount"] == 0]
    cm_rows = []
    for _, r in nonzero.sort_values("date").iterrows():
        cov = r["cov"]
        cm_rows.append({
            "date": r["date"].strftime("%m/%d/%Y") if pd.notna(r["date"]) else "",
            "num": "" if pd.isna(r["num"]) else str(r["num"]),
            "amount": round(float(r["amount"]), 2),
            "coverage": f"{cov[1]:02d}/{cov[0]}" if cov else "",
        })

    start = dt.datetime(year, end_month, 1)
    end = dt.datetime(year, end_month, _last_day(year, end_month)) + dt.timedelta(days=grace_days)
    inv = sub[_is_type(sub["ttype"], "invoice")]
    inv = inv[(inv["date"] >= start) & (inv["date"] <= end)]
    recap_rows = []
    for _, r in inv.sort_values("date").iterrows():
        recap_rows.append({
            "date": r["date"].strftime("%m/%d/%Y") if pd.notna(r["date"]) else "",
            "num": "" if pd.isna(r["num"]) else str(r["num"]),
            "amount": round(float(r["amount"]), 2),
            "desc": "" if pd.isna(r["desc"]) else str(r["desc"]).strip(),
        })

    return {
        "matched": True,
        "cm": {
            "count": int(len(nonzero)),
            "total": round(float(-nonzero["amount"].sum()), 2),
            "months": sorted({c[1] for c in nonzero["cov"] if c}),
            "zero_count": int(len(zero)),
            "rows": cm_rows,
        },
        "recap": {
            "count": int(len(inv)),
            "total": round(float(inv["amount"].sum()), 2),
            "rows": recap_rows,
        },
    }
