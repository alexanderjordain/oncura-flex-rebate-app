"""WOL training email — "installed but training NOT scheduled" weekly draft.

Mirrors core/assist_report.py: read-only, recipients() + build_email(), returns
subject/plain/html plus .eml and .xlsx bytes for the Settings-page button. Pulls
live from HubSpot (deals search + company/call batch reads). Nothing writes back
to HubSpot or any other system.

Secret: HUBSPOT_TOKEN (Streamlit secrets, falling back to the same env var for
local dev). If the deployment names the token differently, change the two lines
at the top that read it — that is the only environment-specific edit.
"""
from __future__ import annotations
import io
import os
import time
import datetime as _dt
from collections import Counter
from email.message import EmailMessage

import pandas as pd
import requests

try:
    import streamlit as st
    TOKEN = st.secrets.get("HUBSPOT_TOKEN") or os.environ.get("HUBSPOT_TOKEN")
except Exception:
    TOKEN = os.environ.get("HUBSPOT_TOKEN")

H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
CALL_WINDOW_DAYS = 90


# ---------- Recipients / trainer roster / from-address ----------
# Kept OUT of this (public) repo: read from the [wol] table in Streamlit secrets
# so no employee names or emails live in version control. See secrets.toml.example
# for the block to paste. Missing config -> empty lists (the draft still builds;
# the operator fills recipients in when sending).
def _wol_secret(key, default):
    try:
        import streamlit as st  # noqa: PLC0415
        return st.secrets["wol"].get(key, default)
    except Exception:
        return default


_TO = list(_wol_secret("to", []))
_CC = list(_wol_secret("cc", []))
# Trainers who should always appear in the breakdown even when their count is 0.
KNOWN_TRAINERS = list(_wol_secret("trainers", []))
_FROM = _wol_secret("from_addr", "")

# ---------- HubSpot property names (verified against portal 8772207 as of 2026-07-16) ----------
DEAL_PROPS = [
    "dealname",
    "funding_received_date_stamp",         # date — the WOL qualifier
    "migrated_00nus000001e6ghma0",         # date — Training Email Sent
    "expiration_date",                     # date — training expiration
    "abdominal_trainings",                 # enum — number scheduled ("0", "1", "2"...)
    "cardiac_trainings",                   # enum — same
    "migrated_00nus000001e6htmak",         # string — Training Remaining from Order Abdominal
    "migrated_00nus000001e6jvma0",         # string — Training Remaining from Order Cardiac
]
CO_PROPS = [
    "name",
    "test_training_sonographer",           # enum(OWNER reference) — the trainer
    "us_install_date__c",                  # date — install date
    "city",
    "state",
]


def recipients(kind: str) -> list[str]:
    return list(_TO) if kind == "to" else list(_CC)


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _num(v):
    try:
        return int(v) if v and str(v) != "(No value)" else 0
    except (TypeError, ValueError):
        return 0


def _days_color(d):
    if not isinstance(d, (int, float)) or d == "":
        return ""
    if d > 90:
        return "background:#F8CBAD;font-weight:600;"
    if d > 30:
        return "background:#FCE4D6;"
    return "background:#FFF2CC;"


def build_email() -> dict:
    if not TOKEN:
        raise RuntimeError("HUBSPOT_TOKEN is not set in Streamlit secrets or env.")

    s = requests.Session()
    s.headers.update(H)
    today = _dt.datetime.now().date()

    # Pull deals with funding_received_date_stamp populated (the WOL qualifier).
    deals = []
    after = None
    while True:
        body = {
            "filterGroups": [{"filters": [{"propertyName": "funding_received_date_stamp",
                                           "operator": "HAS_PROPERTY"}]}],
            "properties": DEAL_PROPS,
            "sorts": [{"propertyName": "funding_received_date_stamp", "direction": "DESCENDING"}],
            "limit": 200,
        }
        if after:
            body["after"] = after
        r = s.post("https://api.hubapi.com/crm/v3/objects/deals/search", json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        deals.extend(data.get("results", []))
        nxt = data.get("paging", {}).get("next", {}).get("after")
        if not nxt:
            break
        after = nxt
        time.sleep(0.05)

    deal_ids = [d["id"] for d in deals]
    deal_by_id = {d["id"]: d.get("properties", {}) for d in deals}

    # Deal -> primary Company association.
    deal_to_co = {}
    for batch in _chunks(deal_ids, 100):
        r = s.post(
            "https://api.hubapi.com/crm/v4/associations/deals/companies/batch/read",
            json={"inputs": [{"id": d} for d in batch]},
            timeout=30,
        )
        for row in r.json().get("results", []):
            for t in row.get("to", []):
                deal_to_co[row["from"]["id"]] = str(t["toObjectId"])
                break
        time.sleep(0.05)

    # Company details (sonographer and install date).
    companies = {}
    for batch in _chunks(list(set(deal_to_co.values())), 100):
        r = s.post(
            "https://api.hubapi.com/crm/v3/objects/companies/batch/read",
            json={"properties": CO_PROPS, "inputs": [{"id": c} for c in batch]},
            timeout=30,
        )
        for row in r.json().get("results", []):
            companies[row["id"]] = row.get("properties", {})
        time.sleep(0.05)

    # Filter to installed + no training.
    candidates = []
    for did in deal_ids:
        dp = deal_by_id.get(did, {})
        co_id = deal_to_co.get(did)
        if not co_id:
            continue
        co = companies.get(co_id, {})
        install_str = co.get("us_install_date__c")
        if not install_str:
            continue
        try:
            install_dt = _dt.date.fromisoformat(install_str[:10])
        except (ValueError, TypeError):
            continue
        if _num(dp.get("abdominal_trainings")) > 0 or _num(dp.get("cardiac_trainings")) > 0:
            continue
        candidates.append({"deal_id": did, "company_id": co_id, "company": co,
                           "deal": dp, "install_dt": install_dt})

    # Resolve sonographer owner IDs to names.
    owner_ids = {c["company"].get("test_training_sonographer")
                 for c in candidates
                 if c["company"].get("test_training_sonographer")}
    owner_names = {}
    for oid in owner_ids:
        if not oid:
            continue
        rr = s.get(f"https://api.hubapi.com/crm/v3/owners/{oid}", timeout=15)
        if rr.status_code == 200:
            p = rr.json()
            owner_names[str(oid)] = (
                f"{p.get('firstName','')} {p.get('lastName','')}".strip()
                or p.get("email", "")
            )
        time.sleep(0.03)

    # Call activity per company (last 90 days).
    company_calls = {}
    window_ms = int((_dt.datetime.now() - _dt.timedelta(days=CALL_WINDOW_DAYS)).timestamp() * 1000)
    for c in candidates:
        co_id = c["company_id"]
        if co_id in company_calls:
            continue
        r = s.get(
            f"https://api.hubapi.com/crm/v4/objects/companies/{co_id}/associations/calls",
            params={"limit": 500}, timeout=30,
        )
        call_ids = [str(x["toObjectId"]) for x in r.json().get("results", [])]
        cd_list = []
        if call_ids:
            for batch in _chunks(call_ids, 100):
                rr = s.post(
                    "https://api.hubapi.com/crm/v3/objects/calls/batch/read",
                    json={"properties": ["hs_timestamp", "hs_call_direction"],
                          "inputs": [{"id": ci} for ci in batch]},
                    timeout=30,
                )
                for row in rr.json().get("results", []):
                    p = row.get("properties", {})
                    ts_raw = p.get("hs_timestamp")
                    if not ts_raw:
                        continue
                    try:
                        ts = _dt.datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        continue
                    if ts.timestamp() * 1000 < window_ms:
                        continue
                    cd_list.append({"ts": ts, "direction": p.get("hs_call_direction")})
        company_calls[co_id] = cd_list
        time.sleep(0.03)

    # Build report rows.
    rows = []
    for c in candidates:
        dp, co, co_id = c["deal"], c["company"], c["company_id"]
        trainer_id = co.get("test_training_sonographer")
        trainer = owner_names.get(str(trainer_id) if trainer_id else "", "Unassigned")

        tes_raw = dp.get("migrated_00nus000001e6ghma0")
        try:
            tes_dt = _dt.date.fromisoformat(tes_raw[:10]) if tes_raw else None
        except (ValueError, TypeError):
            tes_dt = None

        calls = company_calls.get(co_id, [])
        last_call = max((cx["ts"] for cx in calls), default=None)
        rows.append({
            "Training Sonographer": trainer,
            "Clinic": co.get("name") or "",
            "Deal ID": c["deal_id"],
            "Funding Received": (dp.get("funding_received_date_stamp") or "")[:10],
            "US Install Date": c["install_dt"].isoformat(),
            "Days Since Install": (today - c["install_dt"]).days,
            "Training Email Sent": tes_dt.isoformat() if tes_dt else "",
            "Days on Training List": (today - tes_dt).days if tes_dt else "",
            "Last Call": last_call.strftime("%Y-%m-%d") if last_call else "",
            "Days Since Last Call": (today - last_call.date()).days if last_call else "",
            f"Calls in Last {CALL_WINDOW_DAYS}d": len(calls),
            "Expiration Date": (dp.get("expiration_date") or "")[:10],
            "Remaining Abdominal": dp.get("migrated_00nus000001e6htmak") or "",
            "Remaining Cardiac": dp.get("migrated_00nus000001e6jvma0") or "",
            "City": co.get("city") or "",
            "State": co.get("state") or "",
        })

    rows.sort(key=lambda r: (r["Training Sonographer"] == "Unassigned",
                             r["Training Sonographer"], r["US Install Date"]))
    df = pd.DataFrame(rows)

    trainer_counts = Counter(r["Training Sonographer"] for r in rows)
    for kt in KNOWN_TRAINERS:
        trainer_counts.setdefault(kt, 0)

    # xlsx bytes.
    xlsx_bio = io.BytesIO()
    with pd.ExcelWriter(xlsx_bio, engine="openpyxl") as w:
        summary_df = pd.DataFrame([
            {"Training Sonographer": t, "Clinics": n}
            for t, n in sorted(trainer_counts.items(),
                               key=lambda x: (x[0] == "Unassigned", -x[1]))
        ])
        summary_df.to_excel(w, sheet_name="Summary", index=False)
        df.to_excel(w, sheet_name="All (by trainer, install)", index=False)
        for trainer in sorted(trainer_counts.keys(),
                              key=lambda t: (t == "Unassigned", t)):
            sub = df[df["Training Sonographer"] == trainer]
            sheet_name = trainer[:31].replace("/", "-")
            sub.to_excel(w, sheet_name=sheet_name, index=False)
    xlsx_bytes = xlsx_bio.getvalue()

    subject = (
        f"WOL - Installed clinics with no training scheduled "
        f"({len(rows)} open) - {today.isoformat()}"
    )

    # Plain body.
    plain = ["Team,", "",
             f'This week the WOL "installed but no training scheduled" list is '
             f"{len(rows)} clinics.",
             "Sorted by install date, oldest first, per trainer.", ""]
    for trainer in sorted(trainer_counts.keys(),
                          key=lambda t: (t == "Unassigned", t)):
        sub = [r for r in rows if r["Training Sonographer"] == trainer]
        plain.append(f"--- {trainer} ({len(sub)}) ---")
        if not sub:
            plain.append("  (no clinics this week)")
            plain.append("")
            continue
        for r in sub:
            call_bit = (f", last call {r['Days Since Last Call']}d ago"
                        if r["Days Since Last Call"] != "" else ", no calls in 90d")
            tes_bit = (f", email sent {r['Days on Training List']}d ago"
                       if r["Days on Training List"] != "" else ", no training email")
            plain.append(
                f"  {r['Clinic']}  ({r['City']}, {r['State']})  "
                f"installed {r['US Install Date']} ({r['Days Since Install']}d ago)"
                f"{tes_bit}{call_bit}"
            )
        plain.append("")
    plain += ["Full detail in the attached spreadsheet, one tab per trainer.", "", "- Alexander"]
    plain_body = "\n".join(plain)

    # HTML body.
    html = ['<html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:13px;">',
            "<p>Team,</p>",
            f'<p>This week the WOL "installed but no training scheduled" list is '
            f"<b>{len(rows)}</b> clinics. "
            "Sorted by install date, oldest first, per trainer. "
            "Days columns colored amber (0-30) &rarr; orange (31-90) &rarr; red (90+).</p>"]
    for trainer in sorted(trainer_counts.keys(),
                          key=lambda t: (t == "Unassigned", t)):
        sub = [r for r in rows if r["Training Sonographer"] == trainer]
        html.append(f'<h3 style="margin-bottom:4px;">{trainer} '
                    f'<span style="color:#666;font-weight:normal;">({len(sub)})</span></h3>')
        if not sub:
            html.append('<p style="color:#666;margin:0 0 12px 0;">No clinics this week.</p>')
            continue
        html.append('<table cellspacing="0" cellpadding="4" '
                    'style="border-collapse:collapse;border:1px solid #ccc;font-size:12px;">')
        html.append(
            '<tr style="background:#1F4E78;color:white;">'
            "<th align='left'>Clinic</th><th align='left'>Location</th>"
            "<th align='left'>Installed</th><th align='left'>Days Installed</th>"
            "<th align='left'>Training Email</th><th align='left'>Days On List</th>"
            "<th align='left'>Last Call</th><th align='left'>Days Since Call</th>"
            "<th align='left'>Calls 90d</th></tr>"
        )
        for r in sub:
            html.append(
                f'<tr style="border-top:1px solid #eee;">'
                f'<td>{r["Clinic"]}</td>'
                f'<td>{r["City"]}, {r["State"]}</td>'
                f'<td>{r["US Install Date"]}</td>'
                f'<td style="{_days_color(r["Days Since Install"])}">{r["Days Since Install"]}</td>'
                f'<td>{r["Training Email Sent"]}</td>'
                f'<td style="{_days_color(r["Days on Training List"]) if r["Days on Training List"] != "" else ""}">'
                f'{r["Days on Training List"]}</td>'
                f'<td>{r["Last Call"]}</td>'
                f'<td style="{_days_color(r["Days Since Last Call"]) if r["Days Since Last Call"] != "" else ""}">'
                f'{r["Days Since Last Call"]}</td>'
                f'<td>{r[f"Calls in Last {CALL_WINDOW_DAYS}d"]}</td>'
                f"</tr>"
            )
        html.append("</table>")
    html += ["<p>Full detail in the attached spreadsheet, one tab per trainer.</p>",
             "<p>&mdash; Alexander</p></body></html>"]
    html_body = "\n".join(html)

    # .eml with xlsx attached.
    xlsx_filename = f"WOL_Installed_No_Training_{today.isoformat()}.xlsx"
    msg = EmailMessage()
    msg["Subject"] = subject
    if _FROM:
        msg["From"] = _FROM
    msg["To"] = ", ".join(_TO)
    msg["Cc"] = ", ".join(_CC)
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")
    msg.add_attachment(
        xlsx_bytes,
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=xlsx_filename,
    )
    eml_bytes = bytes(msg)
    eml_filename = f"WOL_No_Training_Email_Draft_{today.isoformat()}.eml"

    return {
        "subject": subject,
        "to": list(_TO),
        "cc": list(_CC),
        "plain": plain_body,
        "html": html_body,
        "xlsx_bytes": xlsx_bytes,
        "xlsx_filename": xlsx_filename,
        "eml_bytes": eml_bytes,
        "eml_filename": eml_filename,
        "row_count": len(rows),
        "trainer_count": sum(1 for t, n in trainer_counts.items() if n > 0),
    }
