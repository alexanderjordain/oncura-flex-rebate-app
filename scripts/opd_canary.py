"""Live OPD OData canary — run after ANY change to core/opd_api.py or Stage 3.

Pulls the quarter ending May 2026 from the live OPD feed and checks frozen
historical facts. Historical invoices don't change, so any drift means the
fetch/parse/credit-math/date-projection pipeline regressed (or the feed itself
changed shape).

  1. Abell Animal Hospital — activity Mar 1 - May 31 2026 is $8,064.00 (GROSS:
     Subtotal + AdminFee, the figure QBO books), which against its $5,700.00
     quarterly threshold (data/flex_master.json) is a $2,364.00 overage. Guards
     the gross activity basis — if it ever reverts to the net TotalPrice it
     drops to $5,978.29. (Abell posts at 00:00:0X local, so it does NOT
     exercise the rollover-boundary projection.)
  2. Pine Tree Veterinary Hospital — activity is $7,982.00. This clinic's
     rollover invoices post LATE in the midnight hour (00:04-00:07 local): its
     Feb billing (Mar-01 00:05) must be EXCLUDED and its May billing (Jun-01
     00:04) INCLUDED. If the projection ever reverts to a tight minute window,
     this figure changes. Guards the date fix.

Requires live credentials: OPD_ODATA_USER / OPD_ODATA_PASS in
.streamlit/secrets.toml (local) or Streamlit Cloud secrets. NOT wired into CI
on purpose — CI has no OPD credentials and a public repo must never get them.

Exit code:
  0 = canary matches. The OPD pipeline is intact.
  1 = mismatch or fetch failure. Do not push until understood.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CANARY_CLINIC = "abell animal hospital"
CANARY_YEAR, CANARY_END_MONTH = 2026, 5
EXPECTED_ACTIVITY = 8064.00
EXPECTED_OVERAGE = 2364.00

# Rollover-boundary guard: its activity is only correct when every 1st-of-month
# midnight-hour invoice back-dates regardless of how late in that hour it posted.
BOUNDARY_CLINIC = "pine tree veterinary hospital"
BOUNDARY_EXPECTED_ACTIVITY = 7982.00


def main() -> int:
    from core import opd_api

    print(f"OPD canary :: quarter ending {CANARY_YEAR}-{CANARY_END_MONTH:02d}, "
          f"clinic={CANARY_CLINIC!r}")
    try:
        activity, df, orphans = opd_api.flex_activity_for_quarter(
            CANARY_YEAR, CANARY_END_MONTH
        )
    except Exception as e:
        print(f"  FAIL fetch: {type(e).__name__}: {e}")
        return 1
    print(f"  OK fetch ({len(df)} invoice rows, "
          f"{orphans.get('count', 0)} orphan(s))")

    actual = activity.get(CANARY_CLINIC)
    if actual != EXPECTED_ACTIVITY:
        print(f"  FAIL activity: expected {EXPECTED_ACTIVITY}, got {actual}")
        return 1
    print(f"  OK activity ${actual:,.2f}")

    master = json.loads((ROOT / "data" / "flex_master.json").read_text(encoding="utf-8"))
    rec = next(
        c for c in master["clinics"]
        if c["clinic_name"].lower() == CANARY_CLINIC
    )
    overage = round(actual - rec["quarterly_threshold"], 2)
    if overage != EXPECTED_OVERAGE:
        print(f"  FAIL overage: expected {EXPECTED_OVERAGE}, got {overage} "
              f"(threshold {rec['quarterly_threshold']})")
        return 1
    print(f"  OK overage ${overage:,.2f} against ${rec['quarterly_threshold']:,.2f} threshold")

    boundary = activity.get(BOUNDARY_CLINIC)
    if boundary != BOUNDARY_EXPECTED_ACTIVITY:
        print(f"  FAIL boundary projection: {BOUNDARY_CLINIC!r} expected "
              f"{BOUNDARY_EXPECTED_ACTIVITY}, got {boundary} — late-midnight "
              f"rollover invoices are being mis-filed (date-projection regression)")
        return 1
    print(f"  OK boundary ${boundary:,.2f} ({BOUNDARY_CLINIC})")

    print("Canary passed — OPD pipeline intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
