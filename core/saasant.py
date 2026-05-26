"""SaasAnt import helpers shared by the FLEX generators.

Hard rule from the SOPs: every row in a SaasAnt import must have a UNIQUE reference number.
A shared/constant reference makes SaasAnt collapse all rows into a single record booked
against the first customer (the Great America bug). All generators here enforce uniqueness.
"""
from __future__ import annotations

import calendar
import datetime as dt
import io

import pandas as pd


def last_day_of_month(year: int, month: int) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def sequential_refs(start: int, count: int) -> list[int]:
    """Unique sequential reference numbers starting at `start` (continue from QBO max)."""
    return list(range(int(start), int(start) + int(count)))


def assert_unique_refs(values) -> None:
    vals = list(values)
    if len(set(vals)) != len(vals):
        dupes = {v for v in vals if vals.count(v) > 1}
        raise ValueError(f"Reference numbers are not unique (SaasAnt will collapse rows): {sorted(dupes)[:10]}")


def to_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "Import") -> bytes:
    """Render a DataFrame to .xlsx bytes for a Streamlit download button.
    SaasAnt cannot import from an open file, so the app always hands back a fresh file."""
    buf = io.BytesIO()
    safe = sheet_name[:31]  # Excel sheet-name limit
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name=safe)
    return buf.getvalue()
