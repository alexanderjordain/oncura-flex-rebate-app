# Oncura FLEX + Rebate — Month-Close Stress-Test Playbook

Run linearly. Each section ends with the file or area in the app it exercises. Stop and flag anything that diverges from **Expected**.

---

## 0. Pre-flight smoke (1 min)

| | |
|---|---|
| **Scenario** | Static + unit-test pre-checks pass before exercising the UI. |
| **Setup** | Open a shell in `C:\Users\AlexanderJordain\oncura-flex-rebate-app`. |
| **Action** | Run `python scripts/smoke_test.py`, then `python -m pytest tests/ -q`. Then visit https://oncura-programs.streamlit.app/ and click Home, FLEX Cycle, Rebate Cycle, Rebate Program Controls, Settings. |
| **Expected** | Smoke prints OK. Pytest reports `100 passed`. No red banners on any page; sidebar logo renders; Home shows Rebate clinics / FLEX clinics / Ledger metrics. |
| **What it tests** | CI invariants + module-import health (`pages/home.py` Module Health expander). |

---

## 1. Stage 1 — Finance Payment Imports (`pages/flex_cycle.py` tab 1)

### 1.1 Happy path, mixed flex+scan
- **Scenario:** Clean NewLane remittance, some whole-dollar (scan) rows, some odd-cents (flex) rows.
- **Setup:** Build a CSV with 6 rows: 3 with amounts like `$295.00`, `$595.00`, `$395.00` (scan), 3 with `$412.37`, `$278.91`, `$501.66` (flex). Fill contract column with valid contracts; use 6 real legal names from `data/name_map.json`.
- **Action:** Tab 1 → company `NewLane`, payment date `2026-06-15`, upload. Confirm column auto-detection. Click `Mark N payment(s) as imported`.
- **Expected:** Metric strip shows Flex `3` / Scan `3`; `Total` is sum of all 6. Scan invoices df has 3 rows; both scan and flex payment dfs download. After Mark, success toast `Recorded 6 payment(s)`.
- **What it tests:** `flex_finance.process_remittance` by-cents split, scan invoice/payment 1:1 linkage, ledger record_batch.

### 1.2 Re-upload of identical bytes
- **Scenario:** Same file again — must be blocked.
- **Setup:** Same CSV from 1.1.
- **Action:** Upload the same file. Do NOT tick the override.
- **Expected:** Red banner `This exact file was already processed on YYYY-MM-DD …`. `st.stop()` fires; no metrics or downloads render below.
- **What it tests:** `ledger.check_file_seen` (sha256 of bytes).

### 1.3 Float64 contract drift
- **Scenario:** Same logical rows, but Excel re-saves contract `40010172988` as `40010172988.0`.
- **Setup:** Copy the CSV from 1.1, open in Excel, save as `.xlsx` (Excel will auto-cast the contract column to float). Confirm one cell now reads `40010172988.0` when re-opened.
- **Action:** Upload the new file (different bytes → file-hash dedup does NOT fire; row-level dedup must).
- **Expected:** Yellow banner `Ledger already contains 6 of these payments`. All download dfs empty. `Mark` button reads `Nothing new to record`.
- **What it tests:** `ledger._normalize_contract` strip of trailing `.0`, the recent ledger normalization fix.

### 1.4 Reissue (same money, new date)
- **Scenario:** Finance company re-sends one corrected row with a fresh payment date.
- **Setup:** Take a single row from the 1.1 ledger (e.g., contract `X`, amount `$412.37`), put it alone in a new CSV. Use payment date `2026-06-22` (different from 1.1's `2026-06-15`).
- **Action:** Upload. Do NOT tick the reissue ack yet.
- **Expected:** Yellow `1 payment(s) look like possible reissues` banner. Expander shows the prior date → new date diff. Page hits `st.stop()`. Tick `I confirm these are intentional reissues — proceed.` → metrics + downloads now render. After tick, `Mark` succeeds.
- **What it tests:** `ledger.check_possible_reissues` (partial_fingerprint match on company/kind/contract/amount with date mismatch) — the new Stage 1 reissue gate.

### 1.5 Crossover sanity (>=97% one side)
- **Scenario:** A NewLane file that's effectively all-scan (whole-dollar amounts).
- **Setup:** CSV with 5 rows, all whole-dollar amounts (e.g., `$395.00`, `$595.00`, …). Different contracts than 1.1 to avoid dedup noise.
- **Action:** Upload as NewLane.
- **Expected:** Orange `Unusual flex/scan split: 0.0% flex / 100.0% scan` warning above the metrics. Processing continues.
- **What it tests:** The 97% crossover sanity check; GA path is unaffected.

### 1.6 Unexplained delta → red error
- **Scenario:** One row has a malformed amount cell.
- **Setup:** CSV with 4 normal rows + 1 row whose amount is literally `-` or `N/A` (not deduped against the ledger; use unique contracts).
- **Action:** Upload as NewLane.
- **Expected:** RED `unexplained delta > $1` error citing malformed currency cell. Previously this was a gray caption.
- **What it tests:** The `unexplained_delta > 1.00` branch elevating to `st.error`.

### 1.7 Case-drift in legal_name
- **Scenario:** `name_map.json` has `ABC Animal Hospital, LLC`; remittance prints `abc animal hospital, llc`.
- **Setup:** Confirm `data/name_map.json` has at least one mapping; pick an existing legal name and lowercase it in a one-row CSV. Use a fresh contract + amount.
- **Action:** Upload.
- **Expected:** The clinic resolves to its QB name automatically — `unmapped` list does NOT include it. No "Resolve N unmatched customer name(s)" subheader.
- **What it tests:** `flex_finance.translate_name` case-fold + whitespace-collapse fallback.

---

## 2. Stage 2 — Monthly Credit Memos

### 2.1 Happy path
- **Scenario:** Ledger has 5 flex payments for the target month from active clinics.
- **Setup:** Run 1.1 if needed so ≥5 flex rows exist with `payment_date` in `2026-06`. All clinics must be `active=true` in `flex_master.json`.
- **Action:** Tab 2 → Year `2026`, Month `June`, Starting Credit Memo `50000` → click `Mark 5 credit memo(s) as generated`.
- **Expected:** Metrics show `Credit memos 5`, `Total credits` matches sum. Multi-payment expander accurate. Download is non-empty. After Mark: success toast `Recorded 5 credit memo(s)`.
- **What it tests:** `flex_credits.build_import_from_payments`, ledger recording of `kind=credit_memo`.

### 2.2 Re-run after Mark
- **Scenario:** Same month, immediately re-run.
- **Setup:** None.
- **Action:** Tab 2, same year/month, click Mark again.
- **Expected:** Yellow `5 credit memo(s) already recorded for this month` warning above the Mark button. Download still works but flagged.
- **What it tests:** `ledger.check_payments_seen` against `credit_memo` fingerprints.

### 2.3 Customer rename — stable fingerprint
- **Scenario:** Between Stage 2 runs, a clinic's `qb_name` is edited in Rebate Program Controls. Old code would re-issue the credit (fingerprint included qb_name). New code uses source-payment hash, so dedup holds.
- **Setup:** Pick one clinic from 2.1's batch. Open **Rebate Program Controls** (`pages/rebate_master.py`), rename its `qb_name` (append ` v2`), save. Wait for the GitHub commit (or local save) to land. Hard-clear caches: hit `R` in Streamlit to rerun.
- **Action:** Re-open FLEX Cycle tab 2 for the same month. Click `Mark N credit memo(s) as generated` again.
- **Expected:** Warning `N credit memo(s) already recorded for this month` still fires for the renamed clinic — NOT a fresh ledger entry. Previously: the renamed clinic would show as new and re-issue.
- **What it tests:** The fingerprint-on-source-payment-hash fix (Stage 2 ledger contract = `src['fingerprint']`, not `row['Customer']`).
- **Note (simulating without time-travel):** if the rename can't be done on a live shared GitHub, do this on a local checkout with `FLEXREBATE_LOCAL=1` and an empty `GITHUB_TOKEN` — Stage 2 will dedup against `data/processed_payments.json` on disk.

### 2.4 Empty month
- **Scenario:** No flex payments in ledger for a faraway month.
- **Setup:** None.
- **Action:** Tab 2 → pick `January 2030`.
- **Expected:** Yellow banner `No FLEX payments recorded in the ledger for January 2030`. Generated df is empty. Download button disabled. No crash. Mark button hidden.
- **What it tests:** `flex_credits.build_import_from_payments` empty-payments path.

---

## 3. Stage 3 — Unused / Overage

### 3.1 Happy path
- **Scenario:** A closed quarter with case-grid OPD upload.
- **Setup:** Use `data/mock_opd_invoices.csv` or `scripts/make_mock_opd.py` output for the appropriate quarter. Ensure at least one active flex clinic has `calendar_spread` mapping to the target month.
- **Action:** Tab 3 wizard. Step 1 pick year/month/sales class. Step 2 upload. Step 3 review. Step 4 download recapture invoice.
- **Expected:** Recapture invoice df non-empty. `Next available invoice number` increments by row count. `Mark N recapture invoice(s) as imported` records a `unused_invoice` ledger row.
- **What it tests:** `flex_unused.compute_recapture` + `build_unused_invoice_import`.

### 3.2 Quarter-end 2pm timestamp
- **Scenario:** A case finalized at 2pm on the last calendar day of the quarter must be included.
- **Setup:** Hand-craft an OPD invoices XLSX with 1 row: a known active flex clinic, `Document Date = 2026-06-30T14:00:00`, Subtotal > $0. Other rows can be earlier-quarter normal data.
- **Action:** Run Stage 3 for `Year=2026, Month=June`. Confirm the row appears in Review → activity total includes the $X from the 2pm row.
- **Expected:** Activity for that clinic equals sum INCLUDING the 2pm row. Previously the row was dropped because `pd.Timestamp(end)` was midnight and `<= midnight` excluded 14:00 same-day.
- **What it tests:** `opd_adapter.flex_activity_from_invoices` and `flex_activity_from_case_grid` `dates.dt.normalize() <= pd.Timestamp(end)` fix.

---

## 4. Rebate Cycle (`pages/rebate_cycle.py`)

### 4.1 Happy path — auto-pass review gate
- **Scenario:** Multi-month case-grid OPD with no fuzzy/variance/unmatched.
- **Setup:** Roster-aligned export. Use `data/mock_opd_invoices.csv` if it covers a fresh window with exact name matches.
- **Action:** Step 1 pick 2 months. Step 2 upload. Step 3 review.
- **Expected:** No expanders for variance / fuzzy / rads-pending / unmatched. `cycle_review_acked` is auto-True. `Next ▶` enabled immediately.
- **What it tests:** Auto-pass branch when nothing is flagged.

### 4.2 Fuzzy disclosure gate
- **Scenario:** One OPD row has a clinic name with a typo that fuzzy-matches at ~90%.
- **Setup:** Take a known roster clinic, e.g., `Acme Animal Hospital`, and rename to `Acme Anmial Hospital` in one OPD row.
- **Action:** Step 3 review.
- **Expected:** `Fuzzy clinic matches — 1 row(s) matched non-exactly` expander listing OPD name, matched master, amount. Review-ack checkbox visible. `Next ▶` disabled until ticked. Caption reads `Tick the review acknowledgement checkbox above to continue.`
- **What it tests:** Fuzzy surfacing + gate (≥88 similarity → `match=fuzzy`).

### 4.3 Variance gate
- **Scenario:** OPD export has a `RadCash` feed column with values disagreeing with rate × `RadFin`.
- **Setup:** OPD invoices export with a clinic whose self-funded rads rate is 2% but feed populates `RadCash` at e.g., 4% of `RadFin` (or just an off-by-$5 mismatch).
- **Action:** Step 3 review.
- **Expected:** `Rate vs feed variance` expander listing month, clinic, rate-based, feed-based, variance (delta > $1). Review-ack required. `Next ▶` disabled.
- **What it tests:** `rebate_calc.calculate` variance computation + surfacing.

### 4.4 Per-bucket sum fix
- **Scenario:** Same clinic appears in OPD with two capitalizations.
- **Setup:** Two rows in the same month — `ACE Animal Hospital` and `Ace Animal Hospital` — both matching the same master record. Each with a known revenue, say $1000 and $500.
- **Action:** Step 3 → preview the bucket containing that clinic.
- **Expected:** Per-bucket row for that legal name shows total = sum of BOTH rows. Previously only one survived (overwrite).
- **What it tests:** `per_bucket[bucket][legal][label] = … + amt` (`+=`, not `=`).

### 4.5 Unmatched clinic
- **Scenario:** OPD lists a clinic not in the roster.
- **Setup:** Add a row with clinic `Made Up Hospital` (no fuzzy hit at 88 either).
- **Action:** Step 3.
- **Expected:** `Unmatched OPD clinics — 1 not in roster` expander listing `Made Up Hospital`. Revenue NOT in bucket totals. Review-ack required.
- **What it tests:** Unmatched surfacing + revenue exclusion.

---

## 5. Home dashboard (`pages/home.py`)

### 5.1 Unmarked-batch banner fires
- **Scenario:** Recent flex payment with no Stage 2 audit entry for that month.
- **Setup:** From 1.1 you have flex payments in `2026-06`. Confirm no `stage2_credit_memo` audit entry exists yet for `year=2026, month=6` (Settings → audit manifest, or skip 2.1 first).
- **Action:** Load Home.
- **Expected:** Red banner `Stage 2 not yet run for: June 2026.` Banner copy mentions opening FLEX Cycle → Monthly Credit Memos.
- **What it tests:** `recent_flex_months - stage2_months` set difference and `audit.list_entries(cycle_type='stage2_credit_memo')`.

### 5.2 Banner clears after Stage 2
- **Scenario:** Run Stage 2 for that month, then reload Home.
- **Setup:** Complete 2.1 if not done.
- **Action:** Reload Home (browser refresh).
- **Expected:** Banner gone.
- **What it tests:** Same banner, negative case.

---

## 6. Concurrency (merge-retry on `core/store.py`)

### 6.1 Rebate Program Controls — two-tab save
- **Scenario:** Tab A edits Acme ultrasound rate to 11%; Tab B edits Beta rate to 5%. Both save.
- **Setup:** Open `Rebate Program Controls` in two separate browser tabs (same login). Make different edits to different clinics. `GITHUB_TOKEN` must be set in secrets, otherwise the merge path is bypassed (local file).
- **Action:** Save Tab A first → success. Then save Tab B.
- **Expected:** Tab B succeeds. Toast or commit message includes `(merged with 1 concurrent edit)`. Reload either tab → BOTH edits present (Acme=11%, Beta=5%).
- **What it tests:** `store.save_json` retry on 409/412/422 with `_merge_smart` + `_merge_list` keyed by `clinic_name`.

### 6.2 Settings — config keys
- **Scenario:** Two tabs editing different config keys.
- **Setup:** Open Settings in two tabs. Tab A: change one rate-config value. Tab B: change a different value.
- **Action:** Save Tab A, then Tab B.
- **Expected:** Both edits land in `data/config.json`. No "concurrent edit could not be auto-merged" error.
- **What it tests:** `_merge_smart` on nested dict (config has no list-of-records, so dict branch).

---

## Pass criteria

All 22 cases match Expected. If any case fails, capture the traceback (each tab's `safe_stage` shows one), the file uploaded, and the ledger/audit state, then halt before touching the live month-close.

**Known-safe to skip if low on time:** 4.1 (covered implicitly by other rebate cases). **Never skip:** 1.2, 1.4, 2.3, 3.2 — those are the wave's headline fixes.
