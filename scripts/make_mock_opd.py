"""Generate data/mock_opd_invoices.csv so the app runs end-to-end before the real OPD
export format is known. Uses a sample of clinics from rebate_master.json and invents
plausible line items across every rebate category. NOT real data.
"""
import csv
import json
import os
import random

random.seed(7)

HERE = os.path.dirname(__file__)
MASTER = os.path.join(HERE, "..", "data", "rebate_master.json")
OUT = os.path.join(HERE, "..", "data", "mock_opd_invoices.csv")

LINE_TYPES = [
    ("US-SCAN", "Ultrasound Scan Read", "ultrasound", (60, 240)),
    ("US-STAT", "Ultrasound STAT Read", "stat", (40, 120)),
    ("US-ASST", "Ultrasound Assistance Read", "assistance", (20, 60)),
    ("RAD-STD", "Radiograph Read", "rads", (35, 110)),
    ("RAD-STAT", "Radiograph STAT Read", "rads", (45, 130)),
    ("CANCEL", "Cancellation Fee", "cancellation", (15, 45)),
    ("OVERAGE", "Overage Charge", "overage", (50, 200)),
    ("NONEMA", "Non-EMA Charge", "non_ema", (10, 40)),
]


def main():
    with open(os.path.normpath(MASTER), encoding="utf-8") as f:
        clinics = json.load(f)["clinics"]
    sample = random.sample(clinics, min(20, len(clinics)))

    rows = []
    inv = 50000
    for c in sample:
        name = c["clinic_name"] or c["legal_name"]
        n_invoices = random.randint(1, 3)
        for _ in range(n_invoices):
            inv += 1
            n_lines = random.randint(2, 6)
            for _ in range(n_lines):
                code, desc, _cat, (lo, hi) = random.choice(LINE_TYPES)
                amt = round(random.uniform(lo, hi), 2)
                rows.append(
                    {
                        "Customer": name,
                        "Invoice No": inv,
                        "Item Code": code,
                        "Description": desc,
                        "Amount": amt,
                        "Invoice Date": f"2026-03-{random.randint(1, 28):02d}",
                    }
                )

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(os.path.normpath(OUT), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["Customer", "Invoice No", "Item Code", "Description", "Amount", "Invoice Date"]
        )
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} mock lines across {len(sample)} clinics to {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()
