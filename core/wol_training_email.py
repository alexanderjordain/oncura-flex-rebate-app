"""WOL training email — installed clinics that still need training, weekly draft.

Mirrors core/assist_report.py: recipients() + build_email(), returns subject/plain/
html plus .eml and .xlsx bytes for the Settings-page button. Pulls live from HubSpot
(deals search + company/call batch reads) and cross-checks OPD certifications.

A clinic lands on the list when it was SOLD a training modality (abdominal and/or
cardiac allotment on the deal) and OPD holds no finalized certification for that
modality dated after install. abdominal_trainings / cardiac_trainings is the
allotment sold (2/2 is the standard package), not a scheduled or remaining counter,
so completion is decided against OPD, the only reliable record. Clinics that finish
drop off automatically. The module is read-only; nothing writes back to HubSpot.

Secret: HUBSPOT_TOKEN (Streamlit secrets, falling back to the same env var for
local dev). If the deployment names the token differently, change the two lines
at the top that read it. That is the only environment-specific edit.
"""
from __future__ import annotations
import io
import os
import re
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
# Hardcoded like the weekly assistance report (core/assist_report.py). These are
# internal Oncura training-team addresses. The optional [wol] secrets table still
# overrides any of them (to / cc / trainers / from_addr) if present.
DEFAULT_TO = [
    "Carla Erickson <carla@oncurapartners.com>",
    "John Paul Amberger <jpamberger@oncurapartners.com>",
    "Mariah Hernandez <mariah@oncurapartners.com>",
    "Rosie Haro <rharo@oncurapartners.com>",
    "Sarah Ervin <servin@oncurapartners.com>",
]
DEFAULT_CC = ["Melissa Colpitts <mcolpitts@oncurapartners.com>"]
DEFAULT_FROM = "Alexander Jordain <ajordain@oncurapartners.com>"


def _wol_secret(key, default):
    try:
        import streamlit as st  # noqa: PLC0415
        return st.secrets["wol"].get(key, default)
    except Exception:
        return default


_TO = list(_wol_secret("to", DEFAULT_TO))
_CC = list(_wol_secret("cc", DEFAULT_CC))
# Trainers who should always appear in the breakdown even when their count is 0.
KNOWN_TRAINERS = list(_wol_secret("trainers", []))
_FROM = _wol_secret("from_addr", DEFAULT_FROM)

# ---------- HubSpot property names (verified against portal 8772207 as of 2026-07-16) ----------
DEAL_PROPS = [
    "dealname",
    "funding_received_date_stamp",         # date — the WOL qualifier
    "migrated_00nus000001e6ghma0",         # date — Training Email Sent
    "expiration_date",                     # date — training expiration
    "abdominal_trainings",                 # enum - abdominal training ALLOTMENT sold (2 = standard)
    "cardiac_trainings",                   # enum - cardiac training allotment sold (not a to-do count)
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
# Internal Oncura entities (Oncura Partners - Fort Worth, - ATX, etc.) are not customer
# clinics; any company whose name starts with this prefix is dropped from the list.
EXCLUDE_PREFIX = "oncura partners"


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


# ---------- OPD clinic matching ----------
# HubSpot clinic names embed the OPD business-key code as a suffix ("- SVS38583").
# That code is a far more reliable join to OPD than the display name, which carries
# franchise prefixes (NVA-, TVC-), parentheticals ("(VetCor)"), and "Lost" tags.
_FRANCHISE_PREFIX = re.compile(r"^(nva|tvc|cvp|cah|ssa/svp|obt|aaha|svp)\s*[-/]\s*", re.I)


def _clean_clinic_name(nm):
    """Normalized clinic name with parentheticals, 'Lost' tags, a trailing OPD code,
    and franchise prefixes stripped, for the name-based fallback match."""
    n = re.sub(r"\((?:[^)]*)\)", "", nm or "")
    n = re.sub(r"\b(lost|no longer active)\b.*$", "", n, flags=re.I)
    n = re.split(r"\s-\s[A-Za-z0-9]{4,10}\s*$", n)[0]
    return _norm(_FRANCHISE_PREFIX.sub("", n))


def _opd_clinic_lookup(auth):
    """({business_code -> internal_id}, {clean_name -> internal_id}) for every OPD
    clinic. The business code (Clinic.ClinicID, e.g. 'SVS38583') is the primary key
    HubSpot names are matched against; the cleaned name is the fallback."""
    from . import opd_api  # lazy import
    rows, _ = opd_api._fetch_all(opd_api.CLINIC_PATH, auth=auth,
                                 params={"$select": "ClinicID,ClinicName"})
    by_code: dict = {}
    by_name: dict = {}
    for r in rows:
        m = opd_api._CLINIC_ID_RE.search(r.get("_entry_id") or "")
        if not m:
            continue
        try:
            iid = int(m.group(1))
        except ValueError:
            continue
        code = (r.get("ClinicID") or "").strip().upper()
        if code:
            by_code.setdefault(code, iid)
        key = _clean_clinic_name(r.get("ClinicName"))
        if key:
            by_name.setdefault(key, iid)
    return by_code, by_name


def _match_opd_id(name, by_code, by_name):
    """(internal_id, matched_bool). Try the trailing business-key code first, then a
    cleaned-name lookup. Returns (None, False) when the clinic can't be matched."""
    base = re.sub(r"\((?:[^)]*)\)", "", name or "")
    base = re.sub(r"\b(lost|no longer active)\b.*$", "", base, flags=re.I).strip()
    toks = [t for t in re.split(r"[\s\-/]+", base) if t]
    if toks:
        cand = toks[-1].upper()
        if cand in by_code:
            return by_code[cand], True
    key = _clean_clinic_name(name)
    if key and key in by_name:
        return by_name[key], True
    return None, False


def _needs_training(allot_a, allot_c, cert_a, cert_c):
    """(needs_abdominal, needs_cardiac): a modality is still owed when it was sold
    (allotment > 0) and OPD holds no post-install certification for it."""
    return (allot_a > 0 and cert_a == 0, allot_c > 0 and cert_c == 0)


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

    # ---- Candidate pool: installed + funded + at least one training modality sold ----
    # abdominal_trainings / cardiac_trainings is the training ALLOTMENT on the deal
    # (2/2 is the standard package), NOT a scheduled or remaining counter. A deal that
    # sold no training (0/0) is not a trainer task and is dropped here. Whether a sold
    # modality is DONE is decided below against OPD, the only reliable completion record.
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
        # Drop internal Oncura entities (Oncura Partners - Fort Worth, - ATX, etc.).
        if _norm(co.get("name")).startswith(EXCLUDE_PREFIX):
            continue
        allot_a = _num(dp.get("abdominal_trainings"))
        allot_c = _num(dp.get("cardiac_trainings"))
        if allot_a == 0 and allot_c == 0:
            continue
        candidates.append({"deal_id": did, "company_id": co_id, "company": co,
                           "deal": dp, "install_dt": install_dt,
                           "allot_a": allot_a, "allot_c": allot_c})

    # ---- OPD certification cross-check (source of truth for "already trained") ----
    # Match each clinic to OPD by its embedded business-key code (e.g. "- SVS38583"),
    # falling back to a cleaned name. Finalized abdominal / basic-echo certifications
    # dated after install mark that modality complete. If OPD is unreachable we fail
    # OPEN: nothing can be confirmed trained, so every candidate stays and is flagged.
    opd_error = None
    cert_after: dict = {}
    verified: dict = {}
    try:
        from . import opd_api  # lazy import
        _oauth = opd_api.auth_from_secrets()
        _cert_map = _opd_cert_map(_oauth)
        _by_code, _by_name = _opd_clinic_lookup(_oauth)
        _fin_cache: dict = {}
        for c in candidates:
            _oid, _ok = _match_opd_id(c["company"].get("name"), _by_code, _by_name)
            verified[c["deal_id"]] = _ok
            if not _ok:
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
    except Exception as e:  # noqa: BLE001 - OPD is the cross-check; fail open on error
        opd_error = f"{type(e).__name__}: {e}"

    # ---- Membership: keep clinics still owed a modality they were sold ----
    # needs_<m> = sold that modality AND OPD shows no post-install certification for it.
    members = []
    for c in candidates:
        certs = cert_after.get(c["deal_id"], {"abdominal": 0, "cardiac": 0})
        c["certs"] = certs
        c["needs_a"], c["needs_c"] = _needs_training(
            c["allot_a"], c["allot_c"], certs["abdominal"], certs["cardiac"])
        c["verified"] = verified.get(c["deal_id"], False)
        if c["needs_a"] or c["needs_c"]:
            members.append(c)
    candidates = members

    # ---- Resolve sonographer owner IDs to names (members only). ----
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

    # ---- Call activity per company, last 90 days (members only). ----
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
        needs = ("Abdominal + Cardiac" if c["needs_a"] and c["needs_c"]
                 else "Abdominal" if c["needs_a"] else "Cardiac")
        rows.append({
            "Training Sonographer": trainer,
            "Clinic": co.get("name") or "",
            "Needs Training": needs,
            "OPD Match": "Verified" if c["verified"] else "UNVERIFIED - confirm in OPD",
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
            "Abd Allotted": c["allot_a"],
            "Card Allotted": c["allot_c"],
            "OPD Certs Abd (post-install)": c["certs"]["abdominal"],
            "OPD Certs Card (post-install)": c["certs"]["cardiac"],
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
        f"WOL - Installed clinics still needing training "
        f"({len(rows)} open) - {today.isoformat()}"
    )

    # Plain body.
    plain = ["Training Team,", "",
             f"This week {len(rows)} installed clinics still need training: they were "
             f"sold a modality and OPD has no finalized certification for it yet.",
             "Sorted by install date, oldest first, per trainer. Each line notes which "
             "modality is outstanding.", ""]
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
            flag = "" if r["OPD Match"] == "Verified" else "  [UNVERIFIED in OPD]"
            plain.append(
                f"  {r['Clinic']}  ({r['City']}, {r['State']})  needs {r['Needs Training']}  -  "
                f"installed {r['US Install Date']} ({r['Days Since Install']}d ago)"
                f"{tes_bit}{call_bit}{flag}"
            )
        plain.append("")
    plain += ["Full detail in the attached spreadsheet, one tab per trainer."]
    plain_body = "\n".join(plain)

    # HTML body.
    html = ['<html><body style="font-family:Calibri,Arial,sans-serif;font-size:13px;">',
            "<p>Training Team,</p>",
            f"<p>This week <b>{len(rows)}</b> installed clinics still need training: "
            f"they were sold a modality and OPD has no finalized certification for it yet. "
            f"Sorted by install date, oldest first, per trainer.</p>"]
    for trainer in sorted(trainer_counts.keys(),
                          key=lambda t: (t == "Unassigned", t)):
        sub = [r for r in rows if r["Training Sonographer"] == trainer]
        html.append(f'<h3 style="margin-bottom:4px;">{trainer} '
                    f'<span style="color:#666;font-weight:normal;">({len(sub)})</span></h3>')
        if not sub:
            html.append('<p style="color:#666;margin:0 0 12px 0;">No clinics this week.</p>')
            continue
        html.append('<table cellspacing="0" cellpadding="4" '
                    'style="border-collapse:collapse;border:1px solid #d9dde3;'
                    'font-family:Calibri,Arial,sans-serif;font-size:12px;">')
        html.append(
            '<tr style="background:#5f93a3;color:#0e2a33;">'
            "<th align='left'>Clinic</th><th align='left'>Needs</th>"
            "<th align='left'>Location</th>"
            "<th align='left'>Installed</th><th align='left'>Days Installed</th>"
            "<th align='left'>Training Email</th><th align='left'>Days On List</th>"
            "<th align='left'>Last Call</th><th align='left'>Days Since Call</th>"
            "<th align='left'>Calls 90d</th></tr>"
        )
        for r in sub:
            unv = r["OPD Match"] != "Verified"
            clinic_cell = (f'{r["Clinic"]}<span style="color:#b26a00;"> (unverified in OPD)</span>'
                           if unv else r["Clinic"])
            html.append(
                f'<tr style="border-top:1px solid #d9dde3;">'
                f'<td>{clinic_cell}</td>'
                f'<td>{r["Needs Training"]}</td>'
                f'<td>{r["City"]}, {r["State"]}</td>'
                f'<td>{r["US Install Date"]}</td>'
                f'<td>{r["Days Since Install"]}</td>'
                f'<td>{r["Training Email Sent"]}</td>'
                f'<td>{r["Days on Training List"]}</td>'
                f'<td>{r["Last Call"]}</td>'
                f'<td>{r["Days Since Last Call"]}</td>'
                f'<td>{r[f"Calls in Last {CALL_WINDOW_DAYS}d"]}</td>'
                f"</tr>"
            )
        html.append("</table>")
    html += ["<p>Full detail in the attached spreadsheet, one tab per trainer.</p>",
             "</body></html>"]
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
        "opd_error": opd_error,
    }
