# Oncura FLEX + Rebate Ledger

Streamlit app that runs the accounting workflows for Oncura's **FLEX** (telemedicine financing)
and **Rebate** programs. Produces SaaSAnt import files for QBO — humans review and upload.
Audit-friendly by design; no direct QBO writes.

## Pages

- **Home** — program-level dashboard
- **Rebates → Rebate Master** — 87 rebate-program clinics + per-clinic rates (editable, persisted)
- **Rebates → Rebate Cycle** — pick one or more months, upload OPD detail, download a multi-tab
  xlsx report (one tab per finance bucket) matching the existing Rebate Accounts workbook
- **FLEX → FLEX Cycle** — one page, three tabs walking the monthly process:
  1. **Finance Payment Imports** — upload a finance-co remittance (Great America / OnePlace /
     NewLane); produces flex receive-payments + scan invoices + scan payments. Includes an
     interactive resolver for unmatched legal-name → QB-payee mappings (saves persist).
  2. **Monthly Credit Memos** — pick year + month, generate the SaaSAnt credit-memo import.
  3. **Unused / Overage** — pick year + month, upload OPD activity (Invoices or case-grid),
     compute per-clinic recapture/overage; routes each overage to **partner submission**
     (OnePlace if before cutoff) or **direct-bill QBO invoice** (GA, NewLane, missed cutoff);
     applies pre-existing credit offsets; coaches through the QBO void-after-send step.

## Quick start (local)

```bash
git clone https://github.com/alexanderjordain/oncura-flex-rebate-app.git
cd oncura-flex-rebate-app
pip install -r requirements.txt

# Option A — bypass password for local dev
set FLEXREBATE_LOCAL=1            # Windows; or `export FLEXREBATE_LOCAL=1` on macOS/Linux
streamlit run app.py

# Option B — use the real password gate
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edit the file, set APP_PASSWORD
streamlit run app.py
```

App serves at <http://localhost:8501> by default.

## Cloud deploy (Streamlit Cloud)

1. Push to `main` — Streamlit Cloud auto-redeploys.
2. In **share.streamlit.io → app → Settings → Secrets**, paste:

```toml
APP_PASSWORD = "your-shared-password"
GITHUB_TOKEN = "ghp_xxx"   # optional but recommended: enables master-edit persistence to repo
```

3. Without `GITHUB_TOKEN`, calculations and downloads work but in-app master edits don't
   commit back to the repo (saves are session-only on Cloud).

## Data files (`data/`)

| File | What |
|---|---|
| `rebate_master.json` | 87 rebate-program clinics + per-clinic rates |
| `flex_master.json` | 82 FLEX clinics: monthly credit, threshold, finance company, contract IDs, calendar group |
| `name_map.json` | Legal/remittance name → QB payee translation |
| `service_prices.json` | 50 OPD services with `{price, category}` (flat across clinics) |
| `opd_item_map.json` | Category-classification rules per OPD export profile |
| `config.json` | Rates, calendar groups, finance-company labels, overage routing |
| `mock_opd_invoices.csv` | Synthetic export so the app runs end-to-end without real data |

Re-seed any of these from the source SharePoint workbooks via the scripts in `scripts/`.

## Updating the app

- Edit a JSON master via the UI (Rebate Master) or directly in `data/*.json` and commit.
- Add a new finance company, OPD export shape, or SOP feature — see `CLAUDE.md` for the
  extension recipes.
- Bump pinned dependencies deliberately in `requirements.txt`, retest locally, then push.

## License / ownership

Internal Oncura tool. Repo currently lives in a personal GitHub account; intended to move
to the Oncura organization once org-invite acceptance completes (see CLAUDE.md → "Known gaps").

## Architecture

See **CLAUDE.md** at the repo root for the module map, decisions log, SOP coverage matrix,
extension recipes, and known gaps. That file is the source of truth for "how this thing works."
