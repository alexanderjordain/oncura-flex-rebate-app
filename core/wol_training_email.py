"""WOL training email — installed clinics that still need training, weekly draft.

Mirrors core/assist_report.py: recipients() + build_email(), returns subject/plain/
html plus .eml and .xlsx bytes for the Settings-page button. Pulls live from HubSpot
(deals search + company/call batch reads) and cross-checks OPD certifications.

A clinic lands on the list when it was SOLD a training modality (abdominal and/or
cardiac allotment on the deal) and OPD holds no finalized certification for that
modality (dated around or after install). abdominal_trainings / cardiac_trainings is the
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
import html as _htmlmod
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
HUBSPOT_PORTAL_ID = "8772207"   # for building company deep-links in the report
EXPIRY_SOON_DAYS = 30           # training window flagged "(soon)" within this many days


# ---------- Recipients / trainer roster / from-address ----------
# Hardcoded like the weekly assistance report (core/assist_report.py). These are
# internal Oncura training-team addresses. The optional [wol] secrets table still
# overrides any of them (to / cc / trainers / from_addr) if present.
DEFAULT_TO = [
    "Carla Erickson <carla@oncurapartners.com>",
    "John Paul Amberger <jpamberger@oncurapartners.com>",
    "Mariah Delgado <mariah@oncurapartners.com>",
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
    "phone",                               # clinic phone — so the row is a call sheet
    "city",
    "state",
]

# ---------- OPD certification cross-check ----------
# A "finalized certification" in OPD is a Finalized consult carrying one of these
# ConsultService ServiceNames. Abdomen -> abdominal training, Basic Echo ->
# cardiac. GlobalFAST certs are neither and are ignored for the remaining counts.
CERT_ABDOMINAL = "Certification - Abdomen"
CERT_CARDIAC = "Certification - Basic Echocardiography"
# Certifications completed up to this many days BEFORE the recorded install still
# count as "trained": training often happens on a loaner unit or just before the
# install date is stamped in HubSpot. A cert older than this window is treated as a
# prior, unrelated engagement (stale) and does NOT clear the modality.
CERT_GRACE_DAYS = 180
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
    if not v or str(v).strip() == "(No value)":
        return 0
    try:
        # tolerate "2", "2.0", and multi-value enums like "2;3" (take the first)
        return int(float(str(v).split(";")[0].strip()))
    except (TypeError, ValueError):
        return 0


# ---------- OPD clinic matching ----------
# HubSpot clinic names embed the OPD business-key code as a suffix ("- SVS38583").
# That code is a far more reliable join to OPD than the display name, which carries
# franchise prefixes (NVA-, TVC-), parentheticals ("(VetCor)"), and "Lost" tags.
_FRANCHISE_PREFIX = re.compile(r"^(nva|tvc|cvp|cah|ssa/svp|obt|aaha|svp)\s*[-/]\s*", re.I)


# Corporate legal suffixes to drop from the tail of a clinic name (LLC, Inc, PC...)
# so "The Cat Doctor LLC" matches OPD's "The Cat Doctor" and "...Clinic Pc" matches.
_CORP_SUFFIX = re.compile(r"[,\s]+(l\.?l\.?c\.?|p\.?l\.?l\.?c\.?|inc\.?|p\.?c\.?|ltd\.?|corp\.?)\s*$", re.I)


def _clean_clinic_name(nm):
    """Normalized clinic name with parentheticals, 'Lost' tags, a trailing OPD code,
    franchise prefixes, and corporate suffixes stripped, for the name fallback match."""
    n = re.sub(r"\((?:[^)]*)\)", "", nm or "")
    n = re.sub(r"\b(lost|no longer active)\b.*$", "", n, flags=re.I)
    n = re.split(r"\s-\s[A-Za-z0-9]{2,10}\s*$", n)[0]   # trailing " - CODE"
    n = _CORP_SUFFIX.sub("", n)
    return _norm(_FRANCHISE_PREFIX.sub("", n))


def _opd_clinic_lookup(auth):
    """({business_code -> [internal_id]}, {clean_name -> [internal_id]}) for every OPD
    clinic. Values are LISTS so a duplicated code/name (OPD has several) is detected
    rather than silently resolving to whichever row came back first."""
    from . import opd_api  # lazy import
    rows, total = opd_api._fetch_all(opd_api.CLINIC_PATH, auth=auth,
                                     params={"$select": "ClinicID,ClinicName"})
    if total is not None and total > len(rows):
        raise RuntimeError(f"OPD clinic index truncated: server reports {total}, "
                           f"fetched {len(rows)}. Raise PAGE_SIZE in core.opd_api.")
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
        if code and iid not in by_code.setdefault(code, []):
            by_code[code].append(iid)
        key = _clean_clinic_name(r.get("ClinicName"))
        if key and iid not in by_name.setdefault(key, []):
            by_name[key].append(iid)
    return by_code, by_name


def _extract_code_ids(name, by_code):
    """OPD internal ids for the embedded business-key code in a HubSpot name, or None.
    Scans every token (so a trailing 'Lost'/'No longer active' after the code, or the
    code after that text, is still found) and matches against the OPD code set."""
    base = re.sub(r"\((?:[^)]*)\)", "", name or "")
    for tok in reversed([t for t in re.split(r"[\s\-/]+", base) if t]):
        ids = by_code.get(tok.upper())
        if ids:
            return ids
    return None


def _match_opd_id(name, by_code, by_name):
    """(ids, verified). ids = OPD internal ids to union certs over. A business-code
    match (unique, or a same-code family) is trusted. A name match is trusted only
    when it is unique; an ambiguous name (maps to >1 OPD clinic) returns ([], False)
    so the clinic stays listed and flagged rather than mapped to a guess."""
    ids = _extract_code_ids(name, by_code)
    if ids:
        return ids, True
    named = by_name.get(_clean_clinic_name(name))
    if named and len(named) == 1:
        return named, True
    return [], False


def _needs_training(allot_a, allot_c, cert_a, cert_c):
    """(needs_abdominal, needs_cardiac): a modality is still owed when it was sold
    (allotment > 0) and OPD holds no post-install certification for it."""
    return (allot_a > 0 and cert_a == 0, allot_c > 0 and cert_c == 0)


def _count_certs(cert_list, install_date, grace_days=CERT_GRACE_DAYS):
    """(abdominal, cardiac) certs dated within grace_days before install or any time
    after. cert_list = [(types_dict, date), ...] as returned by _finalized_certs."""
    cutoff = install_date - _dt.timedelta(days=grace_days)
    a = sum(1 for t, fd in cert_list if t.get("abdominal") and fd and fd >= cutoff)
    c = sum(1 for t, fd in cert_list if t.get("cardiac") and fd and fd >= cutoff)
    return a, c


def _display_name(nm):
    """Human-facing clinic name: drop a trailing ' - <code/notes>' segment (an OPD
    code, a 'Lost'/'lead' annotation) and a trailing '(YYYY)'/'Lost' tag, while
    keeping real names like '... - SC #2' or '... - CA' intact."""
    n = (nm or "").strip()
    parts = n.rsplit(" - ", 1)
    if len(parts) == 2 and re.search(r"[A-Za-z]{2,}\d{2,}|\blost\b|no longer active|\blead\b",
                                     parts[1], re.I):
        n = parts[0]
    n = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", n)
    n = re.sub(r"\s*\b(lost|no longer active)\b.*$", "", n, flags=re.I)
    return n.strip(" -")


def _exp_status(exp_raw, today):
    """(label, urgency) for an expiration date string; urgency in
    {'expired','soon','future','none'}."""
    if not exp_raw:
        return "", "none"
    try:
        d = _dt.date.fromisoformat(exp_raw)
    except ValueError:
        return exp_raw, "none"
    if d < today:
        return f"EXPIRED {exp_raw}", "expired"
    if (d - today).days <= EXPIRY_SOON_DAYS:
        return f"{exp_raw} (soon)", "soon"
    return exp_raw, "future"


def _safe_sheet_name(name, used):
    """Excel-safe, unique worksheet name (<=31 chars, forbidden chars replaced)."""
    base = (re.sub(r"[\[\]:*?/\\]", "-", name or "").strip() or "Unassigned")[:31]
    candidate, i = base, 2
    while candidate.lower() in used:
        suffix = f" ({i})"
        candidate = base[:31 - len(suffix)] + suffix
        i += 1
    used.add(candidate.lower())
    return candidate


def _opd_cert_map(auth):
    """{consult_id (str): {'abdominal': bool, 'cardiac': bool}} for every
    Certification service line in OPD (any status), via two filtered live reads
    of ConsultService. Joined to Consults by ConsultServiceCost_Consult = Consult.ID.
    """
    from . import opd_api  # lazy import; avoids pulling opd_api at module load
    base = "https://telehealth.oncurapartners.com/odata/Consults/ConsultService"
    out: dict = {}
    for stype, key in ((CERT_ABDOMINAL, "abdominal"), (CERT_CARDIAC, "cardiac")):
        rows, total = opd_api._fetch_all(base, auth=auth,
                                         params={"$filter": f"ServiceName eq '{stype}'"})
        if total is not None and total > len(rows):
            raise RuntimeError(f"OPD cert map truncated for '{stype}': server reports "
                               f"{total}, fetched {len(rows)}. Raise PAGE_SIZE in core.opd_api.")
        for r in rows:
            cid = str(r.get("ConsultServiceCost_Consult") or "").strip()
            if cid:
                out.setdefault(cid, {"abdominal": False, "cardiac": False})[key] = True
    return out


def _finalized_certs(auth, clinic_internal_id, cert_map):
    """[(types_dict, finalized_date)] for this clinic's Finalized certification
    consults. finalized_date is the local (Eastern) billing date. Retries a couple
    times on a transient OPD connection error before giving up."""
    from . import opd_api
    rows = None
    for attempt in range(3):
        try:
            rows, _ = opd_api._fetch_all(
                "https://telehealth.oncurapartners.com/odata/Consults/Consult", auth=auth,
                params={"$filter": f"Consult_Clinic eq {clinic_internal_id} and CaseStatus eq 'Finalized'",
                        "$select": "ID,FinalizedDate"})
            break
        except Exception:  # noqa: BLE001 - transient OPD/network error; retry then raise
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
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

    # Deal -> primary Company association. Prefer the HubSpot-primary label; fall back
    # to the first association only when none is flagged primary.
    deal_to_co = {}
    for batch in _chunks(deal_ids, 100):
        r = s.post(
            "https://api.hubapi.com/crm/v4/associations/deals/companies/batch/read",
            json={"inputs": [{"id": d} for d in batch]},
            timeout=30,
        )
        r.raise_for_status()
        for row in r.json().get("results", []):
            tos = row.get("to", [])
            primary = next(
                (t for t in tos
                 if any("primary" in str(at.get("label", "")).lower()
                        for at in t.get("associationTypes", []))),
                None,
            )
            chosen = primary or (tos[0] if tos else None)
            if chosen:
                deal_to_co[row["from"]["id"]] = str(chosen["toObjectId"])
        time.sleep(0.05)

    # Company details (sonographer and install date).
    companies = {}
    for batch in _chunks(list(set(deal_to_co.values())), 100):
        r = s.post(
            "https://api.hubapi.com/crm/v3/objects/companies/batch/read",
            json={"properties": CO_PROPS, "inputs": [{"id": c} for c in batch]},
            timeout=30,
        )
        r.raise_for_status()
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

    # Collapse to one row per company: a company can carry several funded deals (an
    # original install plus an upgrade). Merge the allotment as the max sold per
    # modality and keep the most-recently-funded deal's fields for display.
    by_company: dict = {}
    for c in candidates:
        prev = by_company.get(c["company_id"])
        if prev is None:
            by_company[c["company_id"]] = c
            continue
        prev["allot_a"] = max(prev["allot_a"], c["allot_a"])
        prev["allot_c"] = max(prev["allot_c"], c["allot_c"])
        if (c["deal"].get("funding_received_date_stamp") or "") > \
           (prev["deal"].get("funding_received_date_stamp") or ""):
            prev["deal"] = c["deal"]
            prev["deal_id"] = c["deal_id"]
    candidates = list(by_company.values())

    # ---- OPD certification cross-check (source of truth for "already trained") ----
    # Match each clinic to OPD by its embedded business-key code (e.g. "- SVS38583"),
    # falling back to a cleaned name. Finalized abdominal / basic-echo certifications
    # dated within CERT_GRACE_DAYS before install (or any time after) mark that modality
    # complete. If OPD is unreachable we fail OPEN: nothing can be confirmed trained, so
    # every candidate stays and is flagged.
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
            _ids, _ok = _match_opd_id(c["company"].get("name"), _by_code, _by_name)
            verified[c["deal_id"]] = _ok
            if not _ok:
                continue
            # Union finalized certs across every matched OPD id (a duplicated business
            # code can point at sibling records). One flaky per-clinic read flips just
            # that clinic to unverified rather than aborting the whole cross-check.
            _merged, _failed = [], False
            for _oid in _ids:
                if _oid not in _fin_cache:
                    try:
                        _fin_cache[_oid] = _finalized_certs(_oauth, _oid, _cert_map)
                    except Exception:  # noqa: BLE001 - one clinic's read failed
                        _fin_cache[_oid] = None
                if _fin_cache[_oid] is None:
                    _failed = True
                    break
                _merged.extend(_fin_cache[_oid])
            if _failed:
                verified[c["deal_id"]] = False
                continue
            _ca, _cc = _count_certs(_merged, c["install_dt"])
            cert_after[c["deal_id"]] = {"abdominal": _ca, "cardiac": _cc}
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
        # Call activity is display-only enrichment; a failure here must not drop the
        # clinic or abort the report, so it degrades to "no calls" on any error.
        cd_list = []
        try:
            r = s.get(
                f"https://api.hubapi.com/crm/v4/objects/companies/{co_id}/associations/calls",
                params={"limit": 500}, timeout=30,
            )
            r.raise_for_status()
            call_ids = [str(x["toObjectId"]) for x in r.json().get("results", [])]
            for batch in _chunks(call_ids, 100):
                rr = s.post(
                    "https://api.hubapi.com/crm/v3/objects/calls/batch/read",
                    json={"properties": ["hs_timestamp", "hs_call_direction"],
                          "inputs": [{"id": ci} for ci in batch]},
                    timeout=30,
                )
                rr.raise_for_status()
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
        except Exception:  # noqa: BLE001 - call activity is optional; skip on error
            cd_list = []
        company_calls[co_id] = cd_list
        time.sleep(0.03)

    # Build report rows.
    rows = []
    for c in candidates:
        dp, co, co_id = c["deal"], c["company"], c["company_id"]
        trainer_id = co.get("test_training_sonographer")
        trainer = owner_names.get(str(trainer_id) if trainer_id else "", "") or "Unassigned"

        tes_raw = dp.get("migrated_00nus000001e6ghma0")
        try:
            tes_dt = _dt.date.fromisoformat(tes_raw[:10]) if tes_raw else None
        except (ValueError, TypeError):
            tes_dt = None

        # Funding is the WOL qualifier and the honest "training owed" clock: the equipment
        # install can be years earlier, so age + sort off funding, not the install date.
        fund_raw = (dp.get("funding_received_date_stamp") or "")[:10]
        try:
            fund_dt = _dt.date.fromisoformat(fund_raw) if fund_raw else None
        except (ValueError, TypeError):
            fund_dt = None

        calls = company_calls.get(co_id, [])
        last_call = max((cx["ts"] for cx in calls), default=None)
        needs = ("Abdominal + Cardiac" if c["needs_a"] and c["needs_c"]
                 else "Abdominal" if c["needs_a"] else "Cardiac")
        rows.append({
            "Training Sonographer": trainer,
            "Clinic": _display_name(co.get("name")),
            "Needs Training": needs,
            "Phone": co.get("phone") or "",
            "Expiration Date": (dp.get("expiration_date") or "")[:10],
            "Days Waiting": (today - fund_dt).days if fund_dt else "",
            "Training Sold": fund_raw,
            "OPD Match": "Verified" if c["verified"] else "UNVERIFIED - confirm in OPD",
            "City": (co.get("city") or "").title(),
            "State": co.get("state") or "",
            "Last Call": last_call.strftime("%Y-%m-%d") if last_call else "",
            "Days Since Last Call": (today - last_call.date()).days if last_call else "",
            f"Calls in Last {CALL_WINDOW_DAYS}d": len(calls),
            "Training Email Sent": tes_dt.isoformat() if tes_dt else "",
            "Days Since Training Email": (today - tes_dt).days if tes_dt else "",
            "US Install Date": c["install_dt"].isoformat(),
            "Days Since Install": (today - c["install_dt"]).days,
            "Abd Allotted": c["allot_a"],
            "Card Allotted": c["allot_c"],
            "OPD Certs Abd": c["certs"]["abdominal"],
            "OPD Certs Card": c["certs"]["cardiac"],
            "HubSpot": f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/company/{co_id}",
            "Deal ID": c["deal_id"],
        })

    # Most time-sensitive first: dated expirations (soonest / already expired) ahead of
    # undated ones, then longest waiting since the training was funded.
    def _row_sort_key(r):
        dw = r["Days Waiting"] if isinstance(r["Days Waiting"], int) else -1
        return (r["Training Sonographer"] == "Unassigned", r["Training Sonographer"],
                r["Expiration Date"] == "", r["Expiration Date"] or "9999-12-31", -dw)
    rows.sort(key=_row_sort_key)
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
        if df.empty:
            pd.DataFrame(columns=["Training Sonographer", "Clinic", "Needs Training"]).to_excel(
                w, sheet_name="All (by trainer)", index=False)
        else:
            df.to_excel(w, sheet_name="All (by trainer)", index=False)
            _used: set = set()
            for trainer in sorted(trainer_counts.keys(),
                                  key=lambda t: (t == "Unassigned", t)):
                sub = df[df["Training Sonographer"] == trainer]
                if sub.empty:
                    continue
                sub.to_excel(w, sheet_name=_safe_sheet_name(trainer, _used), index=False)
    xlsx_bytes = xlsx_bio.getvalue()

    subject = (
        f"WOL - Installed clinics still needing training "
        f"({len(rows)} open) - {today.isoformat()}"
    )

    # Plain body.
    plain = [
        "Training Team,", "",
        f"This week {len(rows)} installed clinics still need training. Each was sold a "
        f"modality (abdominal and/or cardiac) and OPD has no finalized certification for it "
        f"yet. Every clinic was cross-checked against OPD; already-certified clinics were "
        f"removed, and a clinic drops off automatically once OPD shows the finalized cert. If "
        f"one looks already trained, OPD has no finalized cert on file for it - flag it and "
        f"we'll check.", "",
        "Grouped by training sonographer, most time-sensitive first (soonest or already-"
        "expired training window, then longest waiting since the training was funded). Please "
        "call your clinics and schedule the outstanding session(s), prioritizing anything "
        "marked EXPIRED or (soon).", "",
    ]
    for trainer in sorted(trainer_counts.keys(),
                          key=lambda t: (t == "Unassigned", t)):
        sub = [r for r in rows if r["Training Sonographer"] == trainer]
        plain.append(f"--- {trainer} ({len(sub)}) ---")
        if not sub:
            plain.append("  (no clinics this week)")
            plain.append("")
            continue
        for r in sub:
            phone = f"  ph {r['Phone']}" if r["Phone"] else "  (no phone on file)"
            exp_lbl, _urg = _exp_status(r["Expiration Date"], today)
            exp_bit = f"  |  {exp_lbl}" if exp_lbl else ""
            wait_bit = f"  |  waiting {r['Days Waiting']}d" if r["Days Waiting"] != "" else ""
            call_bit = (f"  |  last call {r['Days Since Last Call']}d ago"
                        if r["Days Since Last Call"] != "" else "  |  no calls in 90d")
            flag = "  [UNVERIFIED - confirm in OPD]" if r["OPD Match"] != "Verified" else ""
            plain.append(
                f"  {r['Clinic']} ({r['City']}, {r['State']}) - needs {r['Needs Training']}"
                f"{phone}{exp_bit}{wait_bit}{call_bit}{flag}"
            )
        plain.append("")
    plain += ["Full detail in the attached spreadsheet, one tab per trainer "
              "(install date, training-email history, and the OPD cert counts)."]
    plain_body = "\n".join(plain)

    # HTML body.
    html = ['<html><body style="font-family:Calibri,Arial,sans-serif;font-size:13px;color:#1f2733">',
            "<p>Training Team,</p>",
            f"<p>This week <b>{len(rows)}</b> installed clinics still need training. Each was "
            f"sold a modality (abdominal and/or cardiac) and OPD has no finalized certification "
            f"for it yet. Every clinic was cross-checked against OPD; already-certified clinics "
            f"were removed, and a clinic drops off automatically once OPD shows the finalized "
            f"cert. If one looks already trained, OPD has no finalized cert on file for it, so "
            f"flag it and we'll check.</p>",
            "<p>Grouped by training sonographer, most time-sensitive first. Please call your "
            "clinics and schedule the outstanding session(s), prioritizing anything marked "
            "<b>EXPIRED</b> or <b>(soon)</b>. Clinic names link to HubSpot.</p>"]
    for trainer in sorted(trainer_counts.keys(),
                          key=lambda t: (t == "Unassigned", t)):
        sub = [r for r in rows if r["Training Sonographer"] == trainer]
        html.append(f'<h3 style="margin-bottom:4px;">{_htmlmod.escape(trainer)} '
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
            "<th align='left'>Phone</th><th align='left'>Location</th>"
            "<th align='left'>Expiration</th><th align='left'>Waiting (days)</th>"
            "<th align='left'>Last Call</th><th align='left'>Calls 90d</th></tr>"
        )
        for r in sub:
            name = _htmlmod.escape(r["Clinic"])
            clinic_cell = (f'<a href="{r["HubSpot"]}" style="color:#0b6bcb;'
                           f'text-decoration:none">{name}</a>')
            if r["OPD Match"] != "Verified":
                clinic_cell += '<span style="color:#b26a00"> (unverified - confirm in OPD)</span>'
            exp_lbl, urg = _exp_status(r["Expiration Date"], today)
            exp_color = {"expired": "#b3261e", "soon": "#b26a00"}.get(urg)
            exp_cell = (f'<span style="color:{exp_color};font-weight:700">{_htmlmod.escape(exp_lbl)}</span>'
                        if exp_color else _htmlmod.escape(exp_lbl))
            phone_esc = _htmlmod.escape(r["Phone"])
            loc_esc = _htmlmod.escape(f'{r["City"]}, {r["State"]}')
            calls90 = r[f"Calls in Last {CALL_WINDOW_DAYS}d"]
            html.append(
                '<tr style="border-top:1px solid #d9dde3;">'
                f'<td>{clinic_cell}</td>'
                f'<td>{r["Needs Training"]}</td>'
                f'<td>{phone_esc}</td>'
                f'<td>{loc_esc}</td>'
                f'<td>{exp_cell}</td>'
                f'<td>{r["Days Waiting"]}</td>'
                f'<td>{r["Last Call"]}</td>'
                f'<td>{calls90}</td>'
                "</tr>"
            )
        html.append("</table>")
    html += ["<p>Full detail in the attached spreadsheet, one tab per trainer "
             "(install date, training-email history, OPD cert counts, and a HubSpot link).</p>",
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
