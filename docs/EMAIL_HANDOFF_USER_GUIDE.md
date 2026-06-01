# Email handoff — user guide

After running any FLEX or Rebate cycle in the app, the last card says **"Hand off
to accounting"**. This is where you email accounting@oncurapartners.com with the
results and the SaasAnt file(s) attached.

There are two scenarios depending on whether IT has finished the one-time
Microsoft Graph setup yet.

---

## Scenario A — Microsoft Graph is set up *(the clean path)*

You'll see a **Connect Outlook** button on the handoff card.

### The very first time you use it (per browser, per machine)

1. Click **Connect Outlook**.
2. You'll be redirected to Microsoft's sign-in page. Your work in the app is
   already saved — you can come back.
3. Sign in with your Oncura email (`firstname.lastname@oncurapartners.com` or
   `firstinitial.lastname@…`). Use your normal password and MFA.
4. Microsoft shows a consent screen: *"Oncura FLEX + Rebate Ledger wants to
   read and write your email."* Click **Accept**.
5. You're redirected back to the app. A green banner appears at the top:
   **"Outlook connected."**
6. The handoff card now shows a **Create draft in my Outlook** button. Click
   it.
7. The app makes a draft in your Outlook **Drafts** folder. You'll see a
   success message and an "Open the draft in Outlook (web)" button.

### Every other time (same browser session)

1. Run the cycle as usual.
2. On the handoff card, click **Create draft in my Outlook**.
3. The draft appears in your Outlook Drafts folder.

### After the draft is created — what you do in Outlook

1. Open Outlook (web at https://outlook.office.com, or the desktop app).
2. Click **Drafts** in the left sidebar.
3. Open the draft titled with this cycle's subject line (e.g. *"[Action
   Required] FLEX Credit Memos — May 2026"*).
4. Verify the attachment is there. Verify the body looks right.
5. Edit anything you want — add a personal note, tweak the subject, anything.
6. Click **Send** in Outlook.

The email goes **from your address** with your signature, lands in *your*
Sent folder, and follows all your tenant's compliance/audit policies normally.

### Security note

The permission you granted is narrow: the app can only create drafts in your
mailbox. It cannot read your inbox, send without you clicking Send yourself,
or access anything else. You can revoke the permission anytime at
<https://myaccount.microsoft.com/consent>.

---

## Scenario B — Microsoft Graph isn't set up yet *(the manual workaround)*

You'll see a **Download email draft (.eml)** button.

### Step-by-step

1. Click **Download email draft (.eml)**. The file lands in your browser's
   downloads folder.
2. Open Windows File Explorer → **Downloads** → find the `.eml` file you just
   downloaded.
3. Double-click it. **What happens next depends on which Outlook you have:**

   #### If you have classic Outlook desktop
   - The `.eml` opens as an **editable compose window** with To, Subject,
     body, and attachment pre-filled. Review, edit if needed, click **Send**.
     Done.

   #### If you have new Outlook or Outlook on the web (OWA)
   - The `.eml` opens as a **read-only message viewer** — you can read it but
     can't send. This is a Microsoft limitation, not the app's fault.
   - Workaround:
     1. The `.eml` is already downloaded. Keep that file around.
     2. In Outlook, click **New mail** (top-left).
     3. In the new compose window:
        - **To:** `accounting@oncurapartners.com`
        - **Subject:** copy from the preview at the bottom of the app's
          handoff card
        - **Body:** copy from the preview at the bottom of the app's handoff
          card
     4. Attach the file(s): drag from your Downloads folder *into* the compose
        window, or click the paperclip icon → **Browse this computer**.
     5. Click **Send**.

### Why the workaround?

Microsoft removed editable-draft support for `.eml` files in OWA / new
Outlook a few years ago — `.eml` always opens in viewer mode there. The fix
is to set up Microsoft Graph (scenario A). Ask IT to follow
`docs/AZURE_AD_SETUP.md` (about 15 minutes of work in entra.microsoft.com).

---

## Signatures

The email body intentionally ends with the last action line — no "Thanks,
FLEX/Rebate Ledger" closer. That way **your Outlook signature flows naturally
at the bottom** without two competing sign-offs.

If your signature is being inserted somewhere weird (top of the body, above
the action items), check Outlook → File → Options → Mail → Signatures, and
set the **New messages** default to your preferred signature. The `.eml`
draft opens as a new compose, so it follows that setting.

---

## Troubleshooting

### "I clicked Connect Outlook and got an error from Microsoft"

The most common cause is that admin consent hasn't been granted for the
app yet. Ask IT to walk through `docs/AZURE_AD_SETUP.md` step 6 (Grant
admin consent for the tenant).

### "I created a draft but I don't see it in my Outlook"

- The draft is in **your** Outlook for the account you signed in with during
  Connect Outlook. If you signed in with `ajordain@` but you're looking at
  Outlook for a different account, switch accounts in Outlook.
- Drafts can take up to a minute to sync from Microsoft Graph to your client.
  Pull-to-refresh in the Drafts folder.

### "The .eml shows from `draft@oncurapartners.com` — that's not my email"

You're seeing an old cached version. The current code omits the From field
entirely so your account fills it on open. Refresh the app (Ctrl+Shift+R)
and try again.

### "I want to send from a shared mailbox, not my personal address"

That's not supported by the current Graph path. Two options:
- Open the draft and change the From field in Outlook before sending (works
  if you have Send-As permission on the shared mailbox).
- Ask IT about adding a Send-As workflow — out of scope for this app.

### "The Connect Outlook button isn't showing — I only see the .eml download"

Microsoft Graph isn't configured in the app's secrets yet. Ask Alex / IT
to follow `docs/AZURE_AD_SETUP.md`. Until then, use the `.eml` workaround
above.
