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

# ---------- OPD certification cross-check ----------
# A "finalized certification" in OPD is a Finalized consult carrying one of these
# ConsultService ServiceNames. Abdomen -> abdominal training, Basic Echo ->
# cardiac. GlobalFAST certs are neither and are ignored for the remaining counts.
CERT_ABDOMINAL = "Certification - Abdomen"
CERT_CARDIAC = "Certification - Basic Echocardiography"
# Internal Oncura entities dropped from the WOL list (not customer clinics).
EXCLUDE_CLINICS = {"oncura partners fort worth", "oncura partners - fort worth"}
# The two Training-Remaining deal properties the OPD certs reduce.
REMAIN_ABDOMINAL = "migrated_00nus000001e6htmak"
REMAIN_CARDIAC = "migrated_00nus000001e6jvma0"


def recipients(kind: str) -> list[str]:
    return list(_TO) if kind == "to" else list(_CC)


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _norm(s):
    return " ".join(str(s or "").casefold().split())


def _num(v):
    try:
        return int(v) if v and str(v) != "(No value)" else 0
    except (TypeError, ValueError):
        return 0


def _num_or_none(v):
    """Parse a Training-Remaining value to int, or None if blank / not a number.
    None means 'unknown' — we keep the clinic on the list but compute no target."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "(No value)":
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _opd_cert_map(auth):
    """{consult_id (str): {'abdominal': bool, 'cardiac': bool}} for every
    Certification service line in OPD (any status), via two filtered live reads
    of ConsultService. Joined to Consults by ConsultServiceCost_Consult = Consult.ID.
    """
    from . import opd_api  # lazy import; avoids pulling opd_api at module load
    base = "https://telehealth.oncurapartners.com/odata/Consults/ConsultService"
    out: dict = {}
    for stype, key in ((CERT_ABDOMINAL, "abdominal"), (CERT_CARDIAC, "cardiac")):
        rows, _ = opd_api._fetch_all(base, auth=auth,
                                     params={"$filter": f"ServiceName eq '{stype}'"})
        for r in rows:
            cid = str(r.get("ConsultServiceCost_Consult") or "").strip()
            if cid:
                out.setdefault(cid, {"abdominal": False, "cardiac": False})[key] = True
    return out


def _finalized_certs(auth, clinic_internal_id, cert_map):
    """[(types_dict, finalized_date)] for this clinic's Finalized certification
    consults. finalized_date is the local (Eastern) billing date."""
    from . import opd_api
    rows, _ = opd_api._fetch_all(
        "https://telehealth.oncurapartners.com/odata/Consults/Consult", auth=auth,
        params={"$filter": f"Consult_Clinic eq {clinic_internal_id} and CaseStatus eq 'Finalized'",
                "$select": "ID,FinalizedDate"})
    out = []
    for r in rows:
        types = cert_map.get(str(r.get("ID") or "").strip())
        if not types:
            continue
        fd = opd_api._utc_to_billing_date(opd_api._parse_dt(r.get("FinalizedDate")))
        out.append((types, fd))
    return out


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
        # Drop internal Oncura entities (e.g. Oncura Partners - Fort Worth). A null
        # Training-Remaining value is NOT grounds for removal — those clinics stay.
        if _norm(co.get("name")) in EXCLUDE_CLINICS:
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

    # OPD certification cross-check (live). Best-effort: if OPD is unreachable the
    # report still builds with cert columns blank. Finalized OPD certifications
    # (abdominal / basic-echo) dated AFTER the install date reduce Training Remaining.
    opd_error = None
    cert_after: dict = {}
    try:
        from . import opd_api  # lazy import
        _oauth = opd_api.auth_from_secrets()
        _cert_map = _opd_cert_map(_oauth)
        _by_oname: dict = {}
        for _iid, _nm in opd_api.fetch_clinic_index(_oauth).items():
            _by_oname.setdefault(_norm(_nm), _iid)
        _fin_cache: dict = {}
        for c in candidates:
            _oid = _by_oname.get(_norm(c["company"].get("name")))
            if not _oid:
                continue
            if _oid not in _fin_cache:
                _fin_cache[_oid] = _finalized_certs(_oauth, _oid, _cert_map)
            _inst = c["install_dt"]
            cert_after[c["deal_id"]] = {
                "abdominal": sum(1 for t, fd in _fin_cache[_oid]
                                 if t["abdominal"] and fd and fd > _inst),
                "cardiac": sum(1 for t, fd in _fin_cache[_oid]
                               if t["cardiac"] and fd and fd > _inst),
            }
    except Exception as e:  # noqa: BLE001 - OPD is best-effort enrichment
        opd_error = f"{type(e).__name__}: {e}"

    def _targets(dp, did):
        """(rem_abd, tgt_abd, rem_car, tgt_car) for a deal — target = remaining
        minus post-install certs, floored at 0; target is None when remaining is
        blank (unknown) so the clinic stays listed but gets no computed change."""
        certs = cert_after.get(did, {"abdominal": 0, "cardiac": 0})
        ra = _num_or_none(dp.get(REMAIN_ABDOMINAL))
        rc = _num_or_none(dp.get(REMAIN_CARDIAC))
        ta = max(0, ra - certs["abdominal"]) if ra is not None else None
        tc = max(0, rc - certs["cardiac"]) if rc is not None else None
        return certs, ra, ta, rc, tc

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

        _certs, _ra, _ta, _rc, _tc = _targets(dp, c["deal_id"])
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
            "OPD Certs Abd (post-install)": _certs["abdominal"],
            "OPD Certs Card (post-install)": _certs["cardiac"],
            "Remaining Abd -> target": ("" if _ta is None else _ta),
            "Remaining Card -> target": ("" if _tc is None else _tc),
            "City": co.get("city") or "",
            "State": co.get("state") or "",
        })

    rows.sort(key=lambda r: (r["Training Sonographer"] == "Unassigned",
                             r["Training Sonographer"], r["US Install Date"]))
    df = pd.DataFrame(rows)

    trainer_counts = Counter(r["Training Sonographer"] for r in rows)
    for kt in KNOWN_TRAINERS:
        trainer_counts.setdefault(kt, 0)

    # Review-then-apply worklist: deals where post-install OPD certs actually
    # change a numeric Training-Remaining value. Null remaining -> no adjustment
    # (the clinic still appears on the list, just with nothing to apply).
    adjustments = []
    for c in candidates:
        certs, ra, ta, rc, tc = _targets(c["deal"], c["deal_id"])
        chg_a = certs["abdominal"] > 0 and ta is not None and ta != ra
        chg_c = certs["cardiac"] > 0 and tc is not None and tc != rc
        if not (chg_a or chg_c):
            continue
        adjustments.append({
            "deal_id": c["deal_id"],
            "clinic": c["company"].get("name") or "",
            "abd_current": ra, "abd_certs": certs["abdominal"],
            "abd_target": ta if chg_a else None,
            "car_current": rc, "car_certs": certs["cardiac"],
            "car_target": tc if chg_c else None,
        })

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
        "adjustments": adjustments,
        "opd_error": opd_error,
    }


def apply_remaining(deal_id, abd_target=None, car_target=None):
    """WRITE the computed Training-Remaining targets back to a HubSpot deal.

    This is the only write in the module. Sets the fields to a target computed
    from OPD (remaining minus post-install certs), so it is idempotent — running
    it twice does not double-decrement. Requires the HUBSPOT_TOKEN to carry the
    crm.objects.deals.write scope; read-only tokens get a 403 surfaced to the UI.
    """
    if not TOKEN:
        raise RuntimeError("HUBSPOT_TOKEN is not set in Streamlit secrets or env.")
    props = {}
    if abd_target is not None:
        props[REMAIN_ABDOMINAL] = str(abd_target)
    if car_target is not None:
        props[REMAIN_CARDIAC] = str(car_target)
    if not props:
        return None
    r = requests.patch(f"https://api.hubapi.com/crm/v3/objects/deals/{deal_id}",
                       headers=H, json={"properties": props}, timeout=30)
    r.raise_for_status()
    return r.json()
