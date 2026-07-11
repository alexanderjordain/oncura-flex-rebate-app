# Recovery runbook

Quick reference for the failure modes we've actually hit on this app. Each entry: **symptom**, **what you'll see**, **fix**.

Live app: <https://oncura-programs.streamlit.app/>
Repo: <https://github.com/alexanderjordain/oncura-flex-rebate-app>

---

## 1. "AttributeError: module has no attribute X" right after a deploy

**Symptom**
A red error banner on the FLEX Cycle (or any page) with a traceback ending in `AttributeError: module 'core.X' has no attribute 'Y'`.

**Cause**
The page references a function whose definition is in another file, and the two files were deployed out of sync. Either Streamlit Cloud is still mid-rebuild, or you pushed only the page change without the supporting core/ change.

**Fix**
1. Hard-refresh the browser (Ctrl+Shift+R). If Cloud is still rebuilding, this usually clears within ~1 minute.
2. If it persists, check the GitHub Actions **smoke** workflow status — it would have failed on the offending commit if the reference is genuinely missing.
3. If the smoke test passed but Cloud still errors: click **Manage app** (lower-right of the live URL) → **Reboot app** to force a clean rebuild.

**Prevention (already in place)**
- `scripts/smoke_test.py` runs locally and in CI before merge. It statically checks every `module.attr` reference in `pages/` against the imported core module. Run it before pushing: `python scripts/smoke_test.py`.
- The `with tab_X, safe_stage(...)` wrappers in `pages/flex_cycle.py` ensure one broken tab doesn't tank the other two.

---

## 2. Sidebar shows raw filenames ("app", "home", "flex cycle", ...) instead of curated nav

**Symptom**
The sidebar shows `app`, `flex cycle`, `home`, `rebate cycle`, `rebate master` as flat list. URL is like `/flex_cycle` instead of `/?page=flex_cycle`. Custom Oncura theme (Fraunces serif headings, green left rule on the header, etc.) is missing.

**Cause**
Streamlit Cloud is **not running `app.py` as the entry file**. Without `app.py`, `st.navigation()` never runs, so Streamlit falls back to its default multipage auto-discovery (every `pages/*.py` becomes its own URL slug) and `ui.inject()` is never called.

**Fix**
1. Click **Manage app** in the lower-right of the live URL.
2. Go to the **gear icon / Settings** for the deployment.
3. Find **Main file path** (sometimes called "App file" or "Entry point"). It MUST be `app.py`.
4. If it's blank or anything else, set it to `app.py` and save. Cloud will rebuild.

**Sanity check after fix**
Visit the bare root URL <https://oncura-programs.streamlit.app/>. You should see the styled Home page with the curated sidebar (Home / Rebates section with two sub-pages / FLEX section with one sub-page). If the styling and sidebar are correct, you're back.

---

## 3. Local edits don't appear on the live app

**Symptom**
You pushed a change to `main`, but the live URL still shows the old behavior.

**Cause / Diagnosis**
- Streamlit Cloud auto-redeploys from `main` ~1 minute after push. Just wait.
- If after 5 minutes nothing changed, the GitHub Actions **smoke** workflow may have failed — check <https://github.com/alexanderjordain/oncura-flex-rebate-app/actions>. A failing smoke test does NOT block the Cloud deploy by itself (Cloud watches GitHub directly), so the deploy may still happen but with a broken build.
- Browser cache: hard-refresh (Ctrl+Shift+R).

**Fix**
1. Hard-refresh the browser.
2. Check the Actions tab for smoke-test failures and resolve them.
3. As last resort: Manage app → Reboot app.

---

## 4. Sidebar nav leaks into the login screen

**Symptom**
On the password gate, the sidebar shows a clickable list of `app`, `flex cycle`, `home`, etc. before you've entered the password.

**Cause**
This is normal Streamlit behavior — `auth.require_login()` calls `st.stop()` before `st.navigation()` runs, so Streamlit's default auto-discovery briefly shows.

**Fix**
Already mitigated in `core/auth.py` — CSS hides the sidebar entirely on the login screen. If this regresses, check that the CSS injection inside `require_login()` still has the `display: none !important` on `section[data-testid="stSidebar"]` and the `stSidebarCollapsedControl`.

---

## 5. Smoke test fails in CI but works locally

**Symptom**
`python scripts/smoke_test.py` exits 0 on your machine but the GitHub Actions workflow fails with the same script.

**Possible causes**
- Python version mismatch — CI runs 3.12 (per `.github/workflows/smoke.yml`); your local may be different.
- Missing dependency — the workflow installs from `requirements.txt` only. If you have a locally-installed dependency that's not in requirements, CI will catch it.
- Case-sensitive filesystem — Windows is case-insensitive; Linux CI is case-sensitive. A file named `Foo.py` referenced as `foo.py` will pass locally but fail in CI.

**Fix**
1. Reproduce locally with the CI Python version: `python3.12 scripts/smoke_test.py`.
2. Add any missing deps to `requirements.txt`.
3. Check file imports for case mismatches against actual file names.

---

## 6. Persistence stops working (changes don't survive page reload)

**Symptom**
You edit a clinic in Rebate Master, save, reload the page, and the edit is gone.

**Cause**
`core/store.py` writes to GitHub via the Contents API when `GITHUB_TOKEN` is set in Streamlit secrets. Without that token, changes are session-only on Cloud (since the container's local filesystem doesn't persist across restarts).

**Fix**
1. Click Manage app → Settings → **Secrets**.
2. Confirm `GITHUB_TOKEN = "ghp_..."` is present.
3. The token needs `repo` scope on `alexanderjordain/oncura-flex-rebate-app`.
4. Home page metric "GitHub persistence" should read **configured**, not **NOT set**.

---

## 7. Login password forgotten or rotated

**Symptom**
Can't log in to the live app.

**Fix**
1. Manage app → Settings → **Secrets**.
2. `APP_PASSWORD` is the shared password.
3. To change it, edit the value in secrets — Cloud will pick it up on next reload (no rebuild needed).
4. The dev bypass `FLEXREBATE_LOCAL=1` env var is NOT set on Cloud (only used for local development), so there's no backdoor — the secret is the only way in.

---

## 8. "Oh no. Error running app." after a reboot — Cloud Python version drifted

**Symptom**
The whole app shows Streamlit's generic **"Oh no. Error running app."** screen (no sidebar, no login form). Rebooting does not fix it — it comes back up broken every time. Started with no code change on our side.

**Cause**
Streamlit Community Cloud picks a **default Python version** when one isn't explicitly pinned for the deployment, and that default drifts upward over time (we were silently moved to **Python 3.14** in July 2026). `requirements.txt` pins exact versions for audit reproducibility (`pandas==3.0.1`, `lxml==6.0.2`, …), and those pins have **no pre-built wheels for the newer Python**, so the dependency install fails on every rebuild and the app never starts. Because it's an *environment* problem, not a code problem, a reboot just re-runs the same failing install — hence "keeps going down after reboot," while everything passes locally.

**How to confirm**
Manage app → open the log panel → the **build/install** log shows something like `ERROR: Could not build wheels for pandas` or `No matching distribution found` — *not* a Python traceback in our own code. (If it's a traceback in `app.py`/`core/*` instead, this isn't your problem — see mode 1.)

**Fix**
1. Manage app → **Settings** → set **Python version** explicitly to **3.13** (the version `requirements.txt` is validated against — verified locally with `streamlit.testing` AppTest across `app.py` + all pages).
2. Reboot. The rebuild installs cleanly on 3.13 and the app returns in ~1–2 min. Hard-refresh (Ctrl+Shift+R).

**Prevention**
- Keep the Python version **pinned in the deployment settings** — never leave it on "Cloud default," or it will drift again on the next platform bump.
- When you deliberately bump Python (or a pinned dependency), first re-run `python scripts/smoke_test.py` + `python -m pytest tests/` **on that Python version** locally.

---

## Quick reference — Streamlit Cloud paths

- **Manage app**: lower-right corner of the live URL
- **Reboot**: Manage app → click reboot button (forces clean rebuild without code change)
- **Settings → Main file path**: must be `app.py`
- **Settings → Python version**: pin to **3.13** — do NOT leave on Cloud default (it drifts and breaks the pinned deps; see mode 8)
- **Settings → Secrets**: `APP_PASSWORD`, `GITHUB_TOKEN`
- **Auto-redeploy**: triggered automatically on every push to `main`, ~1 minute lag
