# EMA Renewal Bot — Microsoft Graph setup (app-only)

The renewal bot runs headless on a schedule (Render cron). It has no browser and
no human to sign in, so it authenticates **as the application itself**
(client-credentials), using `core/ema_graph.py`. This document is the one-time
setup to grant it exactly the access it needs — and nothing more.

**Cost: $0.** An Entra (Azure AD) *app registration* is a free directory object.
It is NOT an Azure subscription and incurs no charge. Every Microsoft 365 tenant
already includes Entra ID.

---

## What the bot is allowed to do

Two **Application** permissions on Microsoft Graph:

| Permission | Why |
|---|---|
| `Mail.Send` | Send the branded renewal email as the organizer mailbox (lands in its Sent Items). |
| `Calendars.ReadWrite` | Create the renewal call on the organizer's calendar (inviting the clinic), and cancel it if the clinic pays first. |

Application permissions are **tenant-wide by default** (the app could send/read as
*any* mailbox). We lock that down in step 5 with an **Application Access Policy**
so the bot can only touch the single organizer mailbox.

---

## 1. Create the app registration

Entra admin center → **Identity → Applications → App registrations → New registration**
(<https://entra.microsoft.com>).

- **Name:** `Oncura EMA Renewal Bot`
- **Supported account types:** *Accounts in this organizational directory only* (single tenant)
- **Redirect URI:** leave blank (no interactive sign-in)
- **Register**

On the Overview page, copy:
- **Application (client) ID** → this is `GRAPH_CLIENT_ID`
- **Directory (tenant) ID** → this is `GRAPH_TENANT_ID`

## 2. Create a client secret

**Certificates & secrets → Client secrets → New client secret.**

- Description: `render-cron`
- Expires: 24 months (calendar a rotation reminder)
- **Add**, then copy the **Value** immediately (shown once) → this is `GRAPH_CLIENT_SECRET`

## 3. Add the API permissions

**API permissions → Add a permission → Microsoft Graph → Application permissions.**

- Check **Mail.Send** and **Calendars.ReadWrite** → **Add permissions**

## 4. Grant admin consent

Still on **API permissions**, click **Grant admin consent for Oncura**. The two
permissions must show **Granted** (green check). *This button requires a Global
Administrator or Privileged Role Administrator.* If you don't have that role, send
this doc to whoever administers Microsoft 365 and ask them to complete steps 1–5.

## 5. Scope the bot to one mailbox (strongly recommended)

Without this, `Mail.Send`/`Calendars.ReadWrite` apply to every mailbox in the
tenant. An **Application Access Policy** restricts the app to just the organizer
mailbox. Run in Exchange Online PowerShell (`Connect-ExchangeOnline`):

```powershell
# One mail-enabled security group holding just the organizer mailbox
New-DistributionGroup -Name "EMA Bot Mailboxes" -Type Security `
  -Members mark@oncurapartners.com -PrimarySmtpAddress ema-bot-scope@oncurapartners.com

New-ApplicationAccessPolicy -AppId <GRAPH_CLIENT_ID> `
  -PolicyScopeGroupId ema-bot-scope@oncurapartners.com `
  -AccessRight RestrictAccess `
  -Description "EMA renewal bot may only act on the organizer mailbox"

# Verify (should return Granted / AccessCheckResult = Granted)
Test-ApplicationAccessPolicy -Identity mark@oncurapartners.com -AppId <GRAPH_CLIENT_ID>
```

## 6. Give the bot the secrets

**Render** (Environment → Environment Variables), and for local testing
`.streamlit/secrets.toml` (git-ignored):

```
GRAPH_TENANT_ID      = "<Directory (tenant) ID>"
GRAPH_CLIENT_ID      = "<Application (client) ID>"
GRAPH_CLIENT_SECRET  = "<client secret value>"
EMA_ORGANIZER        = "mark@oncurapartners.com"   # calendar owner + email sender
EMA_PAYMENT_LINK     = "https://go.oncurapartners.com/hs/payments/…"
OPD_ODATA_USER / OPD_ODATA_PASS                    # already used elsewhere
HUBSPOT_TOKEN                                      # for CRM documentation
```

## 7. Confirm it works

```bash
python scripts/ema_run.py --check-graph   # acquires a token + lists the organizer's calendar
```

A green result means the bot can send and book. Until then everything runs in
**dry-run** (plans printed, nothing sent).

---

## Security notes

- The bot never writes EMA status — accounting still owns that (it flips on payment).
- The client secret is the only credential; rotate it on the expiry you set in step 2.
- Step 5 means a leaked secret still can't read or send as anyone but the organizer.
- All sends land in the organizer's Sent Items and all events on their calendar —
  full audit trail inside Microsoft 365 (Compliance / eDiscovery still applies).
