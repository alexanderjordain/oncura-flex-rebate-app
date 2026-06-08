# Oncura Streamlit UI — Style & Pattern Guide

Portable reference for the visual + interaction conventions used in the
Pass-Through & Rebate Ledger. Drop this into any sibling Oncura app so the
look-and-feel stays consistent across the suite.

Built for **Streamlit** with custom CSS injected once per page. All snippets
in this document are copy-paste ready.

---

## 1. Design philosophy

Three rules drive every visual decision:

1. **Primary action is unmistakable.** On any page, exactly one button should
   look like "the thing to click." Everything else recedes.
2. **Information at the right level of effort.** Critical safety information is
   inline; nice-to-have context is one click away in a gray expander.
3. **Audit-friendly by default.** Every load-bearing action carries operator
   initials, persists to an immutable manifest, and explains itself in the email
   handoff body so accounting can work without checking back.

When in doubt: lean toward **fewer competing UI elements**, **clearer labels**,
**colored emphasis on the one thing that matters**.

---

## 2. Color palette

```python
PALETTE = {
    # Surfaces
    "canvas":    "#F0F2F4",   # page background
    "surface":   "#FFFFFF",   # cards, inputs
    "line":      "#E2E6EA",   # hairline dividers
    # Text
    "ink":       "#2A3742",   # body text
    "muted":     "#6B7785",   # captions, hints, gray labels
    # Brand
    "blue":      "#3A6A9A",   # primary action, titles
    "blue_deep": "#2F567E",   # hover / pressed
    "green":     "#469B68",   # success accent (logo dot)
    "amber":     "#E3A033",   # informational accent
    # Semantic — feedback
    "success_bg":   "#DFF5E1",   # record-button background (mint)
    "success_text": "#1B6E3A",   # record-button text + 'on time' status
    "success_brdr": "#82C18C",   # record-button border
    "success_hov":  "#C6EFCE",   # record-button hover
    "warn_bg":      "#FFEB9C",   # drifting status, soft warnings
    "warn_text":    "#9C5700",
    "danger_bg":    "#F4B6B6",   # critical / declining
    "danger_text":  "#B23A3A",
}
```

**Cohort/status palette (for tables, dashboards):**
- Thriving / on-time → `#C6EFCE` background, `#1B6E3A` text
- Drifting / partial → `#FFEB9C` background, `#9C5700` text
- Lost gains → `#FCD5B4` background, `#A0522D` text
- Declining / critical → `#F4B6B6` background, `#B23A3A` text
- Never engaged → `#D9D2E9` background, `#5F3A8C` text
- Low activity / inert → `#F2F2F2` background, `#808080` text
- Too new / pending → `#E7E6E6` background, `#6B7785` text

---

## 3. Typography

```css
:root {
    --serif: 'Fraunces', Georgia, serif;       /* display titles only */
    --sans:  'Hanken Grotesk', -apple-system, sans-serif;  /* body */
    --mono:  'IBM Plex Mono', ui-monospace, monospace;     /* tabular figures, dollar amounts, IDs */
}
```

Load from Google Fonts:
```html
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;500;600;700&family=Hanken+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
```

**Type hierarchy:**
- `h1` page title — Fraunces 14pt bold color `#1F4E79` (or Streamlit default + override via CSS)
- `h5 markdown headers` — Fraunces semibold, used inside cards for "STATUS REQUIRED" / "Sign-off complete" badges
- Section labels — gray uppercase via `:gray[**LABEL**]` markdown
- Body — Hanken Grotesk, regular weight, `#2A3742`
- Captions — Hanken Grotesk 9pt italic, `#6B7785`
- All dollar amounts, contract IDs, hashes → IBM Plex Mono so columns align

---

## 4. Visual hierarchy

Rank elements on every page from most to least important:

| Tier | Treatment | Examples |
|------|-----------|----------|
| **1. Primary action** | `type="primary"` (or `record_button` green tint), `use_container_width=True`, leading material icon | Download xlsx, Mark/Record N items, Connect Outlook |
| **2. Confirmation / sign-off** | Bordered card via `st.container(border=True)` with colored H5 header | Initials input, "Review acknowledged" card |
| **3. Secondary action** | Plain button, no `type` | ← Back, Refresh ledger |
| **4. Important info** | `st.info` / `st.warning` / `st.error` with icon and clear label | Month dedup banner, blocked-reason explainer |
| **5. Optional context** | Gray-labeled expander (`:gray[label]`) | "Preview rows", "Backup: download the xlsx directly", "How to use the .eml file" |
| **6. Footnotes** | `st.caption` in `:gray[]` italic | Next-ref hints, see-instructions-below pointers |

If two elements occupy the same tier on the same page, demote one.

---

## 5. Button patterns

### Primary action button
```python
st.download_button(
    ":material/download:  Download something (xlsx)",
    bytes_payload, file_name="thing.xlsx",
    type="primary", use_container_width=True,
    key="dl_thing",
)
```

### Commit-to-ledger / "Record" button (light-green tint)
Render with `ui.record_button()` (defined below). Visually distinct from blue
primary so audit-writes don't look like ordinary downloads.

```python
# core/ui.py
def record_button(label, *, key, disabled=False,
                  use_container_width=False, help=None) -> bool:
    st.markdown('<div class="oncura-record-btn-anchor"></div>',
                unsafe_allow_html=True)
    return st.button(label, key=key, disabled=disabled,
                     use_container_width=use_container_width, help=help)
```

CSS for the green tint (paste into `inject()`):
```css
/* :has() + sibling selector targets only the button rendered immediately
   after the sentinel marker; other primary buttons keep their blue theme. */
.element-container:has(.oncura-record-btn-anchor) + .element-container button[kind] {
    background: #DFF5E1 !important;
    color: #1B6E3A !important;
    border: 1px solid #82C18C !important;
}
.element-container:has(.oncura-record-btn-anchor) + .element-container button[kind]:hover:not(:disabled) {
    background: #C6EFCE !important;
    border-color: #1B6E3A !important;
}
.element-container:has(.oncura-record-btn-anchor) + .element-container button[kind]:disabled {
    background: #F2F8F3 !important;
    color: #82C18C !important;
    border-color: #C6E8C9 !important;
    opacity: 0.7;
}
.oncura-record-btn-anchor { display: none; }
```

### Wizard nav row (single horizontal plane)
```
[◀ Back to Setup]    [blocked-reason caption]    [← Back]   [Next →]
```

```python
nav_reset, nav_msg, nav_b, nav_n = st.columns([1.6, 3.4, 1, 1])
if nav_reset.button("◀ Back to Setup", key=..., use_container_width=True):
    ...
if not can_next and next_blocked_reason:
    nav_msg.warning(f":material/info: {next_blocked_reason}")
if can_back:
    nav_b.button("← Back", ..., use_container_width=True)
nav_n.button("Next →", ..., type="primary", disabled=not can_next, use_container_width=True)
```

**Always render the disabled Next button** so the operator can see what they
were targeting. Don't conditionally hide it — that looks like the wizard broke.

---

## 6. Container patterns

### Sign-off card (bordered, status header + actionable widget)

```python
with st.container(border=True):
    if state_complete:
        st.markdown("##### :green[:material/check_circle:&nbsp; Sign-off complete]")
        st.caption("**Next ▶** at the bottom of the page is now enabled.")
    else:
        st.markdown("##### :red[:material/priority_high:&nbsp; SIGN-OFF REQUIRED]")
        st.caption("Tick the checkbox below to acknowledge ...")
    acked = st.checkbox(
        "**I've reviewed the flagged rows above and they're acceptable.**",
        value=SS.get("acked", False), key="acked_widget",
    )
    SS["acked"] = acked
```

**Critical:** read `SS["acked_widget"]` (or render the widget first and use its
return value) to populate the header status — otherwise the header reflects
the previous rerun's state, not the current click. See section 9 for details.

### Initials input card (bordered, applies to every "Record N as imported" gate)

```python
def initials_input(audit_key, *, disabled=False) -> str:
    live_val = (SS.get(audit_key) or SS.get("user_initials", "") or "").strip().upper()
    with st.container(border=True):
        if live_val:
            st.markdown(f"##### :green[:material/check_circle:&nbsp; Initials captured: {live_val}]")
            st.caption("The **record button** below is now enabled. Initials persist for the session.")
        else:
            st.markdown("##### :red[:material/priority_high:&nbsp; INITIALS REQUIRED]")
            st.caption("Enter your initials below to enable the **record button** — like initialing a paper sign-off sheet.")
        val = st.text_input(
            "Your initials (for the audit log)",
            value=SS.get("user_initials", ""), max_chars=4,
            key=audit_key, placeholder="e.g. AJ",
            disabled=disabled, label_visibility="collapsed",
        )
    cleaned = (val or "").strip().upper()
    if cleaned:
        SS["user_initials"] = cleaned
    return cleaned
```

Usage: gate the record button on truthiness, fall back to role only when recording:
```python
initials = ui.initials_input("stage1_audit_initials")
if ui.record_button("Mark N as imported", key=..., disabled=not initials):
    audit.record_cycle(approver=initials or auth.current_role(), ...)
```

### Gray expander for low-priority controls
```python
with st.expander(":gray[Backup: download the xlsx directly]"):
    ...
```

The `:gray[...]` markdown is well-supported by Streamlit and reads as a
footnote-level affordance.

---

## 7. Form patterns

### Column-mapping disclosure (auto-detect + collapsed override)
For file uploads where columns are inferred:

```python
g = guess_columns(company, raw.columns)
mapping_summary = (
    f"Customer = `{SS['cust_col']}`, "
    f"Amount = `{SS['amt_col']}`, "
    f"ID = `{SS['id_col']}`"
)
with st.expander(
    f"Column mapping (auto-detected: {mapping_summary}) — open if a column looks wrong",
    expanded=False,
):
    c1, c2, c3 = st.columns(3)
    c1.selectbox("Customer name column", cols, key="cust_col")
    c2.selectbox("Amount column", cols, key="amt_col")
    c3.selectbox("ID column", cols, key="id_col")
```

Auto-detection runs every render; the expander is for overrides only.

### Two-column primary+preview layout
For tables with a primary download action and an optional preview:

```python
col_dl, col_prev = st.columns([1, 1], gap="medium")
with col_dl:
    st.download_button(":material/download:  Download X (xlsx)", ..., type="primary",
                       use_container_width=True)
with col_prev:
    with st.expander(f":gray[{title} · {len(df)} rows · preview]"):
        st.dataframe(df, use_container_width=True, height=240)
```

50/50 split keeps the preview header on one line. Bold colored button on the
left = primary action; muted gray-labeled expander on the right = optional.

---

## 8. Wizard / multi-step page

Top of page:
```python
STEPS = [("setup", "Step name"), ("review", "Review"), ...]
SS.setdefault("step_idx", 0)
SS["step_idx"] = max(0, min(SS["step_idx"], len(STEPS) - 1))
step_key, step_label = STEPS[SS["step_idx"]]

# Scroll to top on step change so the operator sees the instruction header, not
# wherever they happened to leave their scroll on the previous step.
ui.scroll_top_on_step_change("my_wizard", SS["step_idx"])

st.markdown(f"**Step {SS['step_idx'] + 1} of {len(STEPS)} — {step_label}**")
st.progress((SS['step_idx'] + 1) / len(STEPS))
st.caption("  ·  ".join(
    f"**{lbl}**" if i == SS["step_idx"] else f":gray[{lbl}]"
    for i, (_, lbl) in enumerate(STEPS)
))
```

Scroll-to-top helper (drop into `core/ui.py`):
```python
def scroll_top_on_step_change(wizard_key, current_step):
    import streamlit.components.v1 as components
    prev_key = f"__scroll_prev_{wizard_key}"
    prev = st.session_state.get(prev_key)
    st.session_state[prev_key] = current_step
    if prev is None or prev == current_step:
        return
    components.html("""
    <script>
    (function() {
        const w = (window.parent && window.parent !== window) ? window.parent : window.top;
        if (!w) return;
        try { if (w.history && 'scrollRestoration' in w.history)
            w.history.scrollRestoration = 'manual'; } catch (e) {}
        const SELECTORS = [
            'section[data-testid="stMain"]',
            'div[data-testid="stAppViewContainer"]',
            'div[data-testid="stAppViewBlockContainer"]',
            'section.main', 'div.main', 'div.block-container', 'main',
        ];
        const scroll = () => {
            try {
                w.scrollTo({top:0, left:0, behavior:'auto'});
                const doc = w.document;
                if (doc?.documentElement) doc.documentElement.scrollTop = 0;
                if (doc?.body) doc.body.scrollTop = 0;
                for (const sel of SELECTORS) {
                    const el = doc.querySelector(sel);
                    if (el) { if (typeof el.scrollTo === 'function')
                        el.scrollTo({top:0, behavior:'auto'}); el.scrollTop = 0; }
                }
            } catch (e) {}
        };
        scroll(); setTimeout(scroll, 50); setTimeout(scroll, 150); setTimeout(scroll, 350);
    })();
    </script>""", height=0)
```

---

## 9. Streamlit session-state gotchas

Two real bugs we hit and patterns to avoid them:

### Off-by-one banner state
**Bad** (banner reflects the PREVIOUS rerun's checkbox state):
```python
acked = SS.get("acked", False)
if acked: st.success("Done")
else:     st.error("Required")
SS["acked"] = st.checkbox("...", value=acked, key="ack_widget")
```

**Good** (render widget first, branch on its live return):
```python
acked_now = st.checkbox("...", value=SS.get("acked", False), key="ack_widget")
SS["acked"] = acked_now
if acked_now: st.success("Done")
else:         st.error("Required")
```

Or read from the widget's own SS key (set by Streamlit before script body):
```python
live = SS.get("ack_widget", SS.get("acked", False))
```

### Widget-key state cleared when widget isn't rendered
Streamlit clears `SS[widget_key]` when the widget isn't on the current page
(e.g., during a wizard step where the widget belongs to a different step).
Use **split keys**: a widget key (`*_w`) that mirrors into a persistent key
(`*`) on every render:

```python
year_w = int(st.number_input("Year", value=int(SS["year"]), key="year_w"))
SS["year"] = year_w  # mirror so other steps can read SS["year"]
```

---

## 10. Audit + persistence pattern

Every commit-to-ledger action should:

1. **Render initials input** (`ui.initials_input("audit_key")`) and gate the record button on truthiness
2. **Append to an audit manifest** with hash-chained entries:
   ```python
   audit.record_cycle(
       cycle_type="my_cycle_type",
       approver=initials or auth.current_role(),
       year=..., month=...,
       params={...},          # what choices the operator made
       source_file={"name", "sha256", "size_bytes"},  # if applicable
       outputs=[{"name", "sha256", "row_count", "total"}],
       note="human-readable summary",
   )
   ```
3. **Persist via a GitHub-backed JSON store** so the trail is durable across
   sessions and machines (the GitHub commit history is the tamper trail).
4. **Surface in a separate password-gated admin page** (not in main config),
   so an auditor can browse without granting config-edit access.

---

## 11. Email handoff pattern (handoff to accounting)

For any cycle that produces a file accounting needs:

```python
subject, body = accounting_handoff.my_cycle_email(...)  # define per-cycle
accounting_handoff.render_handoff(
    subject, body,
    key_prefix="my_cycle_email",
    attachments=[(filename, xlsx_bytes)],
)
```

`render_handoff` produces:
- A primary `[⬇ Download email draft (.eml)]` button (Outlook desktop opens it
  in compose; OWA users follow the muted instructions expander)
- Three gray-labeled expanders below: how-to-use, mailto fallback, preview body

Email body should include the work order Tanya / accounting needs to act on,
so the page doesn't need duplicate inline "send & void" instructions.

---

## 12. Anti-patterns to avoid

- **Don't use emojis** unless the user explicitly requests them. Use Streamlit's
  `:material/icon_name:` material icons for visual cues — they're consistent
  with the rest of the Material Design system.
- **Don't add redundant safety banners.** If the initials sign-off card already
  gates the action, a separate "IMPORTANT — confirm this batch" banner above
  it is noise. One signal per safety gate.
- **Don't auto-include fuzzy matches without operator confirmation.** Wrong
  fuzzy matches silently route revenue to the wrong customer; require explicit
  ✓ Match / ✗ Not a match buttons per row.
- **Don't surface metrics that include the whole world.** If the master tracks
  ~80 clinics and the data source has 700+, don't show "700 clinics with
  activity" — the operator infers scope from numbers, and that number is wrong.
  Show "of-N" ratios instead (e.g., "qualifying: 12 / 18").
- **Don't show possible-reissue warnings across different months.** Same
  contract + same amount + different date is just the next billing cycle, not
  a reissue. Scope the check to within one year-month.
- **Don't conflate file-hash dedup with content dedup.** "Same bytes uploaded
  before" is a weak signal (re-exports differ by timestamp). "Payments for this
  month already in the ledger" is the real signal.
- **Don't bury load-bearing operational steps in expanders.** SOP-6 voiding
  reminders, OnePlace cutoff dates, etc. go inline in numbered lists OR in
  the email body that accounting receives.
- **Don't write multi-line comments or docstrings to explain "what" the code
  does.** Use them only for non-obvious "why" (hidden constraint, prior
  incident, subtle invariant). Names should carry the rest.

---

## 13. Quick checklist before shipping a new page

- [ ] Page title in Fraunces / `ui.header()`
- [ ] Primary action visually unmistakable
- [ ] Secondary controls in gray expanders
- [ ] Any commit-to-ledger button uses `ui.record_button` + initials input
- [ ] Any file handoff uses `accounting_handoff.render_handoff`
- [ ] Wizard pages call `ui.scroll_top_on_step_change` at the top
- [ ] No emojis (use `:material/*:` icons instead)
- [ ] Status banners reflect LIVE checkbox state (widget rendered before banner)
- [ ] Audit-manifest entry recorded on every external write
- [ ] Help text on every non-obvious widget
- [ ] No metrics that include non-roster scope
