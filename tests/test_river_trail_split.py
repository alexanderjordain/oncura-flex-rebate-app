"""River Trail payment split (SOP-13/14 multi-clinic contract).

GreatAmerica wires ONE combined payment of $1,843.67 under contract
022-2006959-000, which resolves (via contract_qb_map) to the single QB customer
"River Trail Animal Hospital - Tulsa". That payment actually covers TWO clinics —
Tulsa and Memorial — on the shared contract. The config-driven payment_splits
fan the combined row out into one payment (and thus one credit memo) per clinic.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from core import flex_finance

# Mirrors data/config.json -> flex.payment_splits.
RIVER_TRAIL_SPLITS = {
    "River Trail Animal Hospital - Tulsa": [
        {"qb_customer": "River Trail Animal Hospital - Tulsa", "amount": 921.84},
        {"qb_customer": "River Trail Animal Hospital - Memorial", "amount": 921.83},
    ]
}

CONTRACT = "022-2006959-000"
CONTRACT_QB_MAP = {CONTRACT: "River Trail Animal Hospital - Tulsa"}


def _process(amount):
    """Minimal GreatAmerica remittance: one combined River Trail row."""
    df = pd.DataFrame({
        "Customer Name": ["River Trail Animal Hospital"],
        "ContractID": [CONTRACT],
        "Payment Invoice Number": ["41983392"],
        "Paid": [amount],
    })
    return flex_finance.process_remittance(
        df, "GreatAmerica",
        customer_col="Customer Name", amount_col="Paid",
        id_col="Payment Invoice Number", contract_id_col="ContractID",
        payment_date=dt.date(2026, 5, 8), invoice_date=dt.date(2026, 5, 8),
        start_invoice_no=50000, name_map={},
        contract_qb_map=CONTRACT_QB_MAP,
        split="all_flex",
        payment_splits=RIVER_TRAIL_SPLITS,
    )


def test_combined_payment_splits_into_two_clinics():
    out = _process(1843.67)
    flex = out["flex_payments"]

    # One combined row fanned out into one payment per clinic.
    assert len(flex) == 2

    by_customer = dict(zip(flex["Customer"], flex["Amount"]))
    assert set(by_customer) == {
        "River Trail Animal Hospital - Tulsa",
        "River Trail Animal Hospital - Memorial",
    }
    assert round(by_customer["River Trail Animal Hospital - Tulsa"], 2) == 921.84
    assert round(by_customer["River Trail Animal Hospital - Memorial"], 2) == 921.83

    # Split parts sum back to the original combined payment.
    assert round(float(flex["Amount"].sum()), 2) == 1843.67

    # Each clinic must get a DISTINCT Ref No or SaasAnt collapses them onto one.
    refs = list(flex["Ref No (Receive Payment No)"])
    assert len(set(refs)) == 2


def test_off_amount_does_not_split():
    # An amount that doesn't equal the split total (within 1 cent) must NOT split:
    # it's a different payment and should pass through as a single row.
    out = _process(1900.00)
    flex = out["flex_payments"]
    assert len(flex) == 1
    assert flex["Customer"].iloc[0] == "River Trail Animal Hospital - Tulsa"
    assert round(float(flex["Amount"].iloc[0]), 2) == 1900.00
