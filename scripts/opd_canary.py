"""Live OPD OData canary — run after ANY change to core/opd_api.py or Stage 3.

Pulls the quarter ending May 2026 from the live OPD feed and checks a frozen
historical fact: Abell Animal Hospital's activity for Mar 1 - May 31 2026 is
$5,978.29, which against its $5,700.00 quarterly threshold (data/flex_master.json)
is a $278.29 overage. Historical invoices don't change, so any drift means the
fetch/parse/credit-math pipeline regressed (or the feed itself changed shape).

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
EXPECTED_ACTIVITY = 5978.29
EXPECTED_OVERAGE = 278.29


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

    print("Canary passed — OPD pipeline intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
