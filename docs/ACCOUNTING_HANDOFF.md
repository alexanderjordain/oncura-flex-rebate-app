# Accounting Handoff — what the app doesn't do

The Streamlit app generates SaasAnt import files and routing decisions. It does **not**
write to QBO, OPD, or finance-partner portals — those steps stay with accounting. This
doc catalogs everything that still has to happen by hand after a cycle runs, organized
per workflow. The same content is embedded into the "Email accounting" buttons inside
the app so each cycle can fire off a pre-filled handoff to `accounting@oncurapartners.com`.

Authoritative source for the underlying procedures: `Oncura_Accounting_Master_Reference-5-28-26.docx`.

---

## 1. FLEX — Finance Company Payment Imports (Cash SOP-9 / Cash SOP-10)

**What the app produces (per remittance):**
- Great America: 1 file — receive-payments (all flex).
- OnePlace / NewLane: 3 files — scan invoices, flex receive-payments, scan receive-payments.

**Accounting steps after the files exist:**
1. Go to **transactions.saasant.com → Bulk Upload**.
2. Upload in this exact order; let each job complete before starting the next:
   1. **Scan invoices** → `Invoice`  *(OPC / NewLane only)*
   2. **Flex receive-payments** → `Received Payments`
   3. **Scan receive-payments** → `Received Payments`  *(OPC / NewLane only)*
3. Open QBO bank feed. Combined upload total **must equal** the bank-feed deposit. Match.
4. **Update the OPD credit box** for each clinic that received flex this cycle —
   **ADD** the amount to the existing balance; never replace (per Cash SOP-9 + SOP-5).
5. Verify Received Payment records match the remittance line-by-line.

**Watch-outs:**
- Flex payments are intentionally **unapplied** in QBO; they reconcile at quarter-end.
- One SaasAnt job at a time — wait for completion.
- New Lane bank feed shows "New Lane"; Great America shows "Accounting Services";
  OnePlace shows "Origin Bank Midwest".

---

## 2. FLEX — Monthly Credit Memos (Accounting SOP-5)

**What the app produces:** one SaasAnt credit-memo file per month, ~78–82 clinics.

**Accounting steps:**
1. **transactions.saasant.com → Bulk Upload → Credit Memo** → select the file → walk the wizard.
2. Verify in QBO: new credits appear under the **Flex Credits** line item. P&L Flex Credits line
   should be more negative by the total in the file.
3. **Update the OPD credit box for each clinic** — **ADD** the monthly credit amount to the
   existing balance (ACCUMULATE; never replace). OPD then auto-applies the credit against the
   clinic's next monthly bill.

**Watch-outs:**
- Item must be `Flex-credits` (not `Unused-Flex-Credits`). Never mix.
- Description format: `Flex Credits for {Month} {Year}`.
- Every row needs a unique Reference No — the app enforces this; just don't manually edit
  the file before uploading.
- OPD credit-box updates have fallen behind historically — leaving them stale produces
  clinic-facing confusion when next month's bill drops.

---

## 3. FLEX — Unused Recapture + Overage Billing (Accounting SOP-5, SOP-6, SOP-12)

**What the app produces** (for clinics whose quarter ENDS in the run-month):
- **Unused recapture invoices** (file `UnusedFlex_*.xlsx`) — SaasAnt invoice import.
- **Direct-bill overage billing worksheet** (file `OverageDirect_*.xlsx`) — human-readable
  per-clinic worksheet (threshold / activity / credit / net to bill). Great America, NewLane,
  Self-Financed, and any OnePlace that missed the partner cutoff. **Not a SaasAnt import** —
  Tanya bills these manually in QBO today; see §B.
- **OnePlace partner submission list** (file `OnePlaceOverage_*.xlsx`) — clinics whose
  overage gets sent to OnePlace to bill on Oncura's behalf.

The app already pools multi-clinic groups (Mohnacky / River Trail / PR-vets) at quarter-end —
those produce ONE row per group on the anchor's QB customer.

**Accounting steps:**

### A. Unused recapture
1. **SaasAnt → Bulk Upload → Invoice** → unused-flex file.
2. Verify QBO P&L: Flex Credits line **nets DOWN** by the recapture amount (e.g. from –$69k to –$30k).

### B. Direct-bill overages (SOP-6) — manual billing today

The attached `OverageDirect_*.xlsx` is Tanya's working reference, **not** a SaasAnt import. Each
row shows: Clinic / QB Customer / Finance Company / Contract # / Quarterly Threshold /
Quarter Activity / Gross Overage / Pre-existing Credit Applied / **Net Amount to Bill** /
Suggested QBO Memo / Route Reason. The same per-clinic detail is rendered inline in the email
body so totals are visible without opening the attachment.

1. For each clinic on the worksheet, **manually** create a QBO invoice for the
   `Net Amount to Bill`. Use the `Suggested QBO Memo` so the line item reads consistently.
2. Send an **Authorize.net payment link** to the clinic (preferred), or email the QBO invoice PDF.
3. **VOID the QBO invoice immediately after sending.** Revenue was already captured by the OPD
   invoices; leaving the invoice open overstates AR.
4. When payment arrives, apply it to zero out the clinic's flex account.
5. **No refunds** (SOP-12). Apply any overpayment to future overages.
   Exceptions require **Marty's explicit approval**.

#### Future enhancement: SaasAnt for overages

The `core.flex_overage.build_direct_invoice_import()` helper still exists and produces a
SaasAnt-shaped invoice import (10 QBO-mappable columns, sequential Ref Nos, deduped via
`saasant.assert_unique_refs`). It is **not** wired into the live workflow today because
Tanya bills manually. When the team is ready to fold overages into SaasAnt:

1. Swap the call in `pages/flex_cycle.py::_direct_block()` from
   `flex_overage.build_direct_billing_worksheet(...)` to `flex_overage.build_direct_invoice_import(...)`.
2. Restore the "Starting Invoice No" setup field (see `git log` for the prior wiring).
3. Update `direct_bill_overage_email` step 1 back to the SaasAnt upload instructions.
4. Keep the void-after-send step — that's a hard SOP-6 rule regardless of how the invoice
   was created.

The math, routing, and ledger-dedup behavior stay identical between the two paths; only the
xlsx shape and Tanya's import action differ.

### C. Finance-partner submission (SOP-12)
1. Send `OnePlaceOverage_*.xlsx` to OnePlace **before the 5th of the following month**.
   Missing this cutoff pushes collection 5–6 months out.
2. Confirm receipt.
3. Track expected payment on the FLEX Master spreadsheet (~5–6 months out is typical).

### D. Reconciliation (SOP-11) — per-clinic
1. In QBO, un-apply auto-applied payments for the quarter.
2. Manually apply payment → credit → payment → credit, month by month, for the three months.
3. Skip any "merchant services" line items.
4. Mark the corresponding OPD invoices "Paid TW TW".

### E. Multi-clinic groups (SOP-13 / SOP-14)
- **Mohnacky** (Carlsbad anchor, Vista + Escondido members): pooled — accounting only sees
  Carlsbad in the recapture/overage outputs.
- **River Trail** (Tulsa anchor, Memorial member): same.
- **PR-vets** (Gardenville anchor, Acuario / Diaz Umpierre / La Muda / Condado members):
  same. **Within-group reallocation** across individual QB customers is still manual:
  move credit from a member with unused to a member with overage in QBO.

**Escalation flags surfaced by the app:** Luv-N-Care — communication may need to come from
Marty / Accounting Manager directly.

---

## 4. Rebate Cycle

**What the app produces:** one multi-tab xlsx, one tab per finance bucket, per-clinic rebate
amounts for the selected month(s).

**Accounting steps:**
1. **Self-Funded clinics** — pay the rebate to the clinic directly. Method TBD (credit memo
   in QBO **or** ACH via Bill.com — Jennifer / Marty decision).
2. **NewLane Financed clinics** — wire-transfer the per-partner rebate total to NewLane.
   NewLane applies the rebate to the clinic's financed-balance account.
3. **OnePlace Capital clinics** — same pattern: wire-transfer the per-partner rebate total
   to OnePlace.
4. Archive the report xlsx to SharePoint under `Rebates/{period}/`.

**Watch-outs:**
- When reconciling with a clinic directly, **reference OPD invoices only**. Never expose
  the finance-company split or the Oncura credit structure to clinics.
- Finance partners require both **legal name** and **DBA** on the remittance — the report
  format already includes the legal name from `rebate_master`.

---

## 5. Cross-cutting accounting tasks not in the app

These belong to accounting regardless of cycle:
- **OPD credit-box updates** (credits + recapture both touch this; manual in OPD UI).
- **Bank-feed matching in QBO** after every upload.
- **Account locking / unlocking** (SOP-15) — OPD-side, monthly cycle.
- **Funds reallocation across multi-clinic group locations** (SOP-13 / SOP-14) — within-group QBO journal entries / credit re-applications.
- **Catch-up credits for missing months** (SOP-10) — re-run the Credit Memos page for the missing month and date the credit memo to that period (not today).

---

## Email handoff format

Inside the app, each workflow page has an **"Email accounting"** button that opens a
pre-filled draft to `accounting@oncurapartners.com` with that cycle's specific numbers
(counts, totals, file names, escalations) and a condensed checklist drawn from this doc.
The full reference here is the canonical version when the email is too long for some mail
clients.
