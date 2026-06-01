# Azure AD setup — Microsoft Graph email handoff

Goal: let each operator (Alex, Tanya, etc.) create email drafts in **their own**
Outlook, with attachments pre-loaded, then click Send themselves. The draft lives
in their personal Drafts folder and the sent email goes from their address with
their signature.

This is a one-time setup. After it's done, every operator gets a "Connect Outlook"
button on the email handoff card.

## Steps

1. Go to **https://entra.microsoft.com** → sign in with a tenant admin
   account.
2. Left nav → **Identity** → **Applications** → **App registrations**.
3. **+ New registration**:
   - **Name**: `Oncura FLEX + Rebate Ledger`
   - **Supported account types**: *Accounts in this organizational directory only (single tenant)*
   - **Redirect URI**:
     - Platform: **Web**
     - URL: `https://oncura-programs.streamlit.app/`
   - Click **Register**.
4. On the new app's overview page, copy:
   - **Application (client) ID**
   - **Directory (tenant) ID**

5. Left nav → **API permissions** → **+ Add a permission**:
   - **Microsoft Graph** → **Delegated permissions**
   - Search and add:
     - `Mail.ReadWrite` (create/edit messages in user's mailbox)
     - `User.Read` (read signed-in user's email + display name)
     - `offline_access` (refresh token so the user doesn't re-auth constantly)
   - Click **Add permissions**.
6. Click **Grant admin consent for <tenant>**. (Without this, every user has to
   consent individually the first time they sign in.)

7. Left nav → **Authentication**:
   - Under **Web** → **Redirect URIs**, make sure
     `https://oncura-programs.streamlit.app/` is listed.
   - For local dev, also add `http://localhost:8501/` (optional).
   - Under **Implicit grant and hybrid flows** — leave everything OFF (we use auth code flow).
   - Under **Allow public client flows** — leave OFF (this app is public-client-style
     but msal handles it correctly via the auth code flow without a secret).

8. **Streamlit Cloud → Secrets** (App settings → Secrets), add:
   ```toml
   AZURE_CLIENT_ID="<Application (client) ID from step 4>"
   AZURE_TENANT_ID="<Directory (tenant) ID from step 4>"
   # Optional — only needed if Streamlit Cloud URL ever changes
   # AZURE_REDIRECT_URI="https://oncura-programs.streamlit.app/"
   ```
   Save. Streamlit Cloud auto-reboots.

9. Open the app. On any stage's email-handoff card you should now see
   **"Connect Outlook"**. Click it → sign in with your Oncura M365 account →
   you're redirected back to the app with `?code=...` in the URL → the app
   exchanges the code for a token → you can now click **"Create draft in my
   Outlook"** to create drafts with attachments.

## Per-operator flow (once setup is done)

1. Sign in to the app (password gate).
2. Run Stage 1 / 2 / 3 normally.
3. On the email handoff card:
   - First time per session: click **"Connect Outlook"** → sign in.
   - Click **"Create draft in my Outlook"**.
   - Open Outlook (web or desktop) → **Drafts** folder → the email is there
     with body and attachments pre-loaded.
   - Review and click **Send** in Outlook.

## Why this is the right path long-term

- **Audit trail:** the email is in *your* Sent folder, traceable by tenant
  compliance / eDiscovery without needing the app's logs.
- **Per-user identity:** Tanya's emails come from `tanya@`, Alex's from `ajordain@`,
  etc. No service-account shenanigans.
- **No SMTP AUTH dependency:** Microsoft is gradually deprecating SMTP basic auth
  for M365. Graph is the supported successor.
- **Permission scoped:** `Mail.ReadWrite` lets the app create drafts only —
  nothing in the inbox is read, nothing is sent without user click.

## Fallback behavior

If `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` are not in secrets, the app falls back
to SMTP (if `SMTP_HOST` is set) or to `.eml` download. Removing Graph access
won't break the app; it just downgrades to the next-best path.
