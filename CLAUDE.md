# CLAUDE.md — assistant context for the FLEX + Rebate Ledger

## What this app is

A Streamlit app that runs the accounting workflows for Oncura's **FLEX** (telemedicine
financing) and **Rebate** programs. Generates SaasAnt import files for QBO — no direct QBO
writes. The app's design driver is **audit-friendliness** (Marty's stated requirement) and
**works without the original author** (single-point-of-failure mitigation).

## Architecture (where things live)

```
app.py                        # entry: auth + theme + st.navigation
core/
  auth.py                     # password gate (single shared) + optional roles; hides sidebar on login screen
  ui.py                       # theme/CSS, page header, sidebar logo, record_button, initials_input, persistence_warning
  loaders.py                  # cached loaders for the JSON masters
  store.py                    # JSON persistence (GitHub Contents API + local file)
  ledger.py                   # processed-payments ledger: fingerprint + dedup + persist
  audit.py                    # per-cycle immutable audit manifest (entry_hash chain, GitHub-backed)
  opd_adapter.py              # OPD file ingest: 3 profiles (odata, case_grid, generic) + invoices
  opd_api.py                  # live OPD Mendix OData v3 client (Atom XML, DST-aware billing date)
  rebate_calc.py              # rate-based vs feed-based rebate calc w/ variance
  rebate_report.py            # multi-tab xlsx report builder for the cycle
  flex_credits.py             # payment-driven credit-memo import + legacy active-list fallback (SOP-5)
  flex_unused.py              # quarter-end recapture (SOP-5) + overage detection
  flex_overage.py             # overage routing + direct-bill worksheet + partner submission (SOP-6, SOP-12)
  flex_finance.py             # finance-co remittance -> SaasAnt imports (SOP-9, SOP-10)
  overage_ledger.py           # overage billed/paid tracking + 3-month lockout aging (SOP-15)
  monthly_audit.py            # month-end per-clinic audit workbook (activity vs QBO entries)
  saasant.py                  # shared SaasAnt helpers (unique refs, last-day, xlsx bytes)
  accounting_handoff.py       # per-workflow email-draft builders + render helpers
  graph_email.py              # Microsoft Graph draft-creation (preferred over .eml when configured)
data/
  rebate_master.json          # 87 rebate-program clinics + rates
  flex_master.json            # 82 FLEX clinics + thresholds + contract IDs + calendar group
  name_map.json               # legal name -> QB payee (used for remittance Customer mapping)
  service_prices.json         # 50 services with {price, category} from comp-app STD_PRICES
  opd_item_map.json           # category classification rules per OPD profile
  config.json                 # rates, calendar groups, overage routing, finance co labels
  processed_payments.json     # ledger: file hashes + per-payment fingerprints (created on first Stage 1 commit)
pages/
  home.py                     # status dashboard + module-health panel
  rebate_master.py            # edit clinics + rates
  rebate_cycle.py             # multi-month cycle -> multi-tab xlsx report
  flex_cycle.py               # 3-tab wizard wrapped in safe_stage() guards; live-OPD Stage 3
  overage_tracker.py          # overage billed/paid + 3-month lockout watch (Pass-Through nav)
  flex_tutorial.py            # operator-facing walkthrough of the FLEX program model
  review_verify.py            # Admin: read-only month/quarter verification (recorded vs recompute)
  audit_log.py                # browse + verify the audit manifest (password-gated admin view)
  settings.py                 # config.json editor + backup/restore + ledger summary (admin-only)
tests/                        # pytest suite — `python -m pytest tests/` (165 tests, ~1s)
  test_ledger.py              # fingerprint stability + dedup
  test_flex_unused.py         # quarter math + multi-clinic group pooling
  test_flex_credits.py        # payment-driven builder + legacy fallback
  test_flex_overage.py        # SOP-6/SOP-12 routing + worksheet schema (partner ledger contract)
  test_opd_api.py             # Atom parsing, DST-aware billing-date, namespace drift, orphans
  test_accounting_handoff.py  # minimal direct-bill email, multipart .eml, no SaasAnt step
scripts/
  smoke_test.py               # pre-deploy static checker (syntax + cross-module references)
  build_rebate_master.py      # seed rebate_master.json from Rebate Accounts Copy.xlsx
  build_flex_master.py        # seed flex_master.json from Flex Master List.xlsx
  build_name_map.py           # seed name_map from ScanPackage + FlexMaster
  make_mock_opd.py            # generate mock OPD CSV for demo
  merge_name_map.py           # bulk-add legal->QB pairs (rerunnable)
  merge_flex_names.py         # bulk-add OnePlace flex clinic mappings
.github/workflows/
  smoke.yml                   # runs smoke_test.py + pytest on every push/PR
docs/
  ACCOUNTING_HANDOFF.md       # manual-step catalog for accounting after files are generated
  FLEX_PROGRAM_EXPLAINED.md   # explainer + brand-system spec for the tutorial deck
  RECOVERY.md                 # runbook for the 7 failure modes we've seen
assets/
  oncura_logo.png             # shown via st.logo() in the sidebar
.streamlit/
  config.toml                 # theme (committed)
  secrets.toml.example        # template (real secrets.toml is gitignored)
```

## SOPs implemented

Source: `OneDrive\...\Oncura_Accounting_Master_Reference-5-28-26.docx` (CFO Marty McCutchen).

| SOP | Title | Status |
|---|---|---|
| Accounting SOP-5 | FLEX Credit Memo Import | ✓ `flex_credits.py` + FLEX Cycle stage 2 |
| Accounting SOP-6 | FLEX Overage Billing | ✓ `flex_overage.py` direct-bill flow |
| Accounting SOP-12 | FLEX Overage — Internal Process | ✓ `flex_overage.py` routing + cutoff + credit offset |
| Cash SOP-9 | FLEX Finance Co Payment Import | ✓ `flex_finance.py` GA path |
| Cash SOP-10 | NewLane Pass-Through | ✓ `flex_finance.py` by-cents split + scan linkage |
| Accounting SOP-10 | Catch-up Credit Memo Application | ✓ via FLEX Cycle stage 2 picking past months |
| Accounting SOP-11 | Reconciliation | output computed (recapture). QBO un-apply/re-apply is manual. |
| Accounting SOP-13/14 | Multi-clinic contracts | **GAP** — flex_master doesn't model parent contracts. See "Known gaps" below. |
| Accounting SOP-15 | Account Locking | In-app tracking via `core/overage_ledger.py` + the Overage Tracker page (billed/paid/lockout aging; 3-months-unpaid = lockout). OPD/QBO enforcement of the lock is still manual. |

## Key decisions (don't re-litigate without reading the rationale)

- **Rebate rate scheme (authoritative per Tanya, email 2026-06-09)**: ultrasound 10% finance / 5% self-funded; rads 8% finance / 4% self-funded. Per-clinic overrides editable in Rebate Master. History: 2026-05-26 set 10/5 + 4/2 (with "self-funded rads = half of finance per OPD feed" rationale); 2026-06-09 morning Alexander set 10/8 + 5/4 based on initial verbal; 2026-06-09 afternoon Tanya corrected in writing to the current 10/5 + 8/4 — the rads ratio (finance > self-funded by 4 points) is the load-bearing pattern, not the ultrasound ratio.
- **STAT priority adds an implicit $125** when no STAT service is in the case row. No admin fees.
- **OPD prices are flat across all clinics** (no per-clinic discounts modeled). From comp-app's `STD_PRICES`.
- **OnePlace flex contracts strip the leading zero** in the Ref No / OPDAdd, but scan contracts keep all leading zeros — matches the SaasAnt templates.
- **NewLane + OnePlace remittances split by cents** (whole-dollar = scan, non-round = flex). GA = all flex.
- **Unique `Ref No (Receive Payment No)` per row is mandatory** — duplicate refs collapse all rows onto the first customer in SaasAnt (the GA bug). Every builder enforces this via `saasant.assert_unique_refs`.
- **Direct-bill overage invoices get VOIDED after sending** (SOP-6). The app generates the invoice; voiding is a manual QBO step. The page surfaces this as coaching. **Why we void is open**: OPD invoices do NOT push to QBO for FLEX clinics (confirmed by Alexander 2026-06-08), so the prior rationale "revenue already captured by the OPD invoices, leaving them open overstates AR" was incorrect. The rule itself stands per Marty's SOP doc; the operational reason is to be confirmed with Tanya/Marty and the rationale field updated.
- **No refunds on FLEX overpayments** (SOP-12, Marty policy). Apply to future overages.
- **The app generates files; humans approve and upload.** Third-party isolation. No direct QBO writes.

## Persistence model (important for handoff)

`core/store.py` implements a dual-path JSON store:
- **GitHub Contents API** when `GITHUB_TOKEN` is set in `st.secrets` (Cloud) — committed back to the repo, durable for everyone.
- **Local file** otherwise — survives restarts on the same machine.

Rule: token present → GitHub is the writable source of truth; no token → local file is. This prevents the "saved locally but reload pulls public GitHub copy" shadow bug.

All masters live in `data/*.json` and are versioned with the code. Edit via the UI (Rebate Master) or by rerunning the seed scripts. The interactive name-resolver in FLEX Cycle stage 1 commits new `legal -> QB` mappings into `name_map.json` automatically.

## Adding a new finance company

1. Add to `flex_finance.COMPANY_META` with `flex_label`, `scan_label`, `bank_feed`.
2. Update `make_ref_no` if its Ref No format differs.
3. Update `guess_columns` if its remittance header names differ.
4. Update `route_overage` config in `data/config.json` `flex.overage.finance_partner_handles` — `true` if they handle overages, `false` if they decline.
5. The split logic in `process_remittance` is already by_cents — confirm that matches.

## Adding a new OPD export shape

1. Make `opd_adapter.detect_profile` return a new profile name when its headers are recognized.
2. Implement `_normalize_<profile>(df, ...)` returning the standard schema (`NORM_COLUMNS + FEED_COLUMNS`).
3. Route in `opd_adapter.normalize`.
4. If it's used for FLEX activity, add `flex_activity_from_<profile>`.

## Auth

Single shared password (`APP_PASSWORD` in secrets) → full access. Optional `[roles]` block can split permissions (alex=admin, tanya=operator, etc.) but we don't use it currently.

No-secrets fallback grants admin ONLY when `FLEXREBATE_LOCAL=1` env var is set — so a misconfigured public Cloud URL never opens admin to the world.

## Bulletproofing layers (don't remove without replacing)

The app survives an Alex-leaves scenario via four layers:

1. **`scripts/smoke_test.py` + `.github/workflows/smoke.yml`** — static checker that ASTs every `module.attr` reference in `pages/` and verifies the imported core module has that attribute. Runs in CI on every push. Negative-tested to catch the exact AttributeError class we hit.
2. **`tests/` pytest suite** — frozen tests for the calculation modules (recapture math, credit-memo builder, ledger fingerprints, group pooling). 32 tests, ~1s runtime. Wired into CI.
3. **`safe_stage()` context manager in `pages/flex_cycle.py`** — `with tab_X, safe_stage(...):` traps exceptions inside each tab so one broken stage doesn't kill the others. Chains with the tab context manager so existing indentation is untouched.
4. **`docs/RECOVERY.md`** — runbook for the 7 failure modes we've actually hit. Anyone (not just Alex) can recover from a broken Cloud deploy by reading it.

## FLEX accounting model — the part nobody figures out on their own

Each FLEX clinic gets **TWO credit entries per month**, not one:

- **Finance-co payment** (cash wired to Oncura by OnePlace/GA/NewLane) — recorded as an unapplied Receive Payment on the clinic's QBO account. Per Cash SOP-9 these are *intentionally* unapplied; they sit as a credit balance.
- **Monthly credit memo** (`Flex-credits` item, class `03-Telemedicine`) — generated by Stage 2 of the wizard, one per ledger payment row. Per Accounting SOP-10 description: "one Flex payment in, one credit out."

The two together fund the clinic's quarterly entitlement: `payment + credit memo` per month × 3 ≈ `quarterly_threshold` ≈ 6 × `monthly_credit`. The ratio isn't exactly 2:1 (e.g., Chenango 1.93×, Alum Rock 2.23×) — that's contract-term asymmetry between wholesale (what the finance co pays) and retail (what the threshold is denominated in).

At quarter end, the Accounting Manager (SOP-11) un-applies auto-matches and re-applies in payment→credit→payment→credit pattern against the quarter's scan invoices. Whatever's left determines the outcome:
- **Zero balance** → reconciled, done.
- **Positive remaining** (clinic owes) → overage → SOP-12 (partner or direct bill).
- **Negative remaining** (credit) → unused → `Unused-Flex-Credits` invoice **on the clinic's account but NOT mailed** — it's an internal accounting entry that converts the credit balance into recognized revenue.

Full explainer in `docs/FLEX_PROGRAM_EXPLAINED.md`.

## Known gaps / next priorities

1. **Audit manifest (task #7, deferred):** per-cycle immutable record (timestamp, source file hash, params, totals, output hash, approver). The audit-friendly feature Marty cited as decisive. Should be wired into `flex_overage`, `flex_credits`, `flex_unused`, `rebate_calc` outputs and persisted via `store`.
2. **Multi-clinic contract modeling (SOP-13/14):** River Trail, PR-vets, Mohnacky etc. are treated as independent clinics. Need `parent_contract_id` on flex_master clinics + cross-clinic reallocation logic.
3. ~~**OPD live API:**~~ **DONE (2026-06-08).** Stage 3 pulls live from Mendix OData at `telehealth.oncurapartners.com/odata/Consults/Invoices` via `core/opd_api.py`. **The manual file-upload fallback was REMOVED 2026-06-09 after an incident (partial-quarter file produced 35 inflated recapture invoices) — do NOT re-add it without explicit operator approval.** Two DST-aware quirks captured in code: (a) Mendix double-writes `OldCredit` and `MiscCredit` — apply `max()` not sum; (b) month-end rollovers fire at midnight LOCAL Eastern so a UTC timestamp `2026-06-01T04:00:03Z` represents the May 31 billing — `opd_api._utc_to_billing_date()` handles the backshift. Credentials live in `st.secrets` as `OPD_ODATA_USER` / `OPD_ODATA_PASS`. Recommend switching to a dedicated service-account credential rather than a personal login so audit logs distinguish app pulls from human pulls.
4. **`*Discounted*` service variants** in OPD case-grid fall to `$0/other` — should either be added to `service_prices.json` or have a derivation rule (base × discount %).
5. **Rancho Pet Cure conflict** in `name_map.json`: currently mapped to "PetVet Care Centers, LLC DBA Rancho Regional Veterinary Hospital" (user's live resolver entry), but the earlier flex template mapped it to "Baseline Animal Hospital". Confirm.
6. **Extend ledger dedup to Stages 2 & 3 (task #21):** Stage 1 dedups payment imports already. Stages 2 (credit memos) and 3 (recapture invoices) don't record what they emit, so re-running them could double-post to QBO via different SaasAnt ref numbers. Same fingerprint pattern, applied to two more emission points.

## Running + pre-push checks

```bash
# Local dev
streamlit run app.py
# bypass password: set env FLEXREBATE_LOCAL=1 first

# Pre-push checks (also run by CI on every push)
python scripts/smoke_test.py          # static cross-module reference check
python -m pytest tests/ -v            # calculation correctness suite

# Live OPD canary — REQUIRED after any change to core/opd_api.py or Stage 3.
# Needs OPD_ODATA_USER/PASS in secrets; not in CI (public repo, no creds there).
python scripts/opd_canary.py          # Abell May-2026 quarter: 5978.29 / 278.29

# Cloud
# Push to main -> Streamlit Cloud auto-redeploys from github.com/alexanderjordain/oncura-flex-rebate-app
# Required secrets on Cloud: APP_PASSWORD; optional GITHUB_TOKEN for master-edit persistence.
```

## Repo + deploy

- **Repo:** `github.com/alexanderjordain/oncura-flex-rebate-app` (public)
- **Live app:** **https://oncura-programs.streamlit.app/** — Streamlit Cloud, auto-redeploys from `main` (~1 min)
- **Local:** `streamlit run app.py` after `pip install -r requirements.txt`. Set `FLEXREBATE_LOCAL=1` to bypass the password gate during dev.
- **Personal account** owns the repo and the Cloud workspace; intended future move to Oncura GitHub org once org-invite acceptance completes.
