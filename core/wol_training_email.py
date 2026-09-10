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
    "migrated_00nus000001e6htmak",         # string - "Training Remaining from Order Abdominal"
    "migrated_00nus000001e6jvma0",         # string - "Training Remaining from Order Cardiac"
]
# The two "Training Remaining from Order" fields ARE the trainer-maintained to-do
# count (decremented as sessions are completed): >0 still owed, 0 done, blank = not
# yet filled in. This is the PRIMARY completion signal; OPD certs are the fallback
# used only when a trainer has left the remaining field blank. Property IDs verified
# against portal 8772207 on 2026-09-09.
REMAINING_ABD = "migrated_00nus000001e6htmak"
REMAINING_CARD = "migrated_00nus000001e6jvma0"
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


def _num_opt(v):
    """Like _num but returns None when the field is blank/absent, so a trainer's
    explicit 0 ('training complete') is distinguishable from a field never filled in."""
    if v is None or str(v).strip() in ("", "(No value)"):
        return None
    try:
        return int(float(str(v).split(";")[0].strip()))
    except (TypeError, ValueError):
        return None


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


def _need_one(allot, cert, remaining):
    """Is a single modality still owed (should we reach out to schedule training)?

    Never-sold (allot <= 0) is never chased. A modality is DONE, and drops off, as
    soon as EITHER signal says so: OPD holds a post-install certification (cert > 0,
    hard evidence the clinic was trained), or the trainer zeroed the 'Training
    Remaining from Order' count. It stays on the list only when BOTH say untrained:
    no certification AND a remaining count that is either blank or greater than zero.
    A certified clinic is off the list even if a package session is still undelivered
    (this report is 'Installed, No Training', not 'package not fully consumed')."""
    if allot <= 0:
        return False
    if cert > 0:
        return False
    if remaining == 0:
        return False
    return True


def _needs_training(allot_a, allot_c, cert_a, cert_c, rem_a=None, rem_c=None):
    """(needs_abdominal, needs_cardiac).

    A modality is cleared by EITHER an OPD certification (cert_* > 0) OR the trainer
    marking its remaining count to 0; it is chased only when both say untrained. See
    _need_one for the per-modality rule."""
    return (_need_one(allot_a, cert_a, rem_a), _need_one(allot_c, cert_c, rem_c))


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


def _mon_yr(d):
    return f"{d.strftime('%b')} {d.year}"           # "Dec 2024"


def _mon_day(d, yr=False):
    return f"{d.strftime('%b')} {d.day}" + (f", {d.year}" if yr else "")   # "Sep 4" / "Oct 31, 2026"


def _status_label(exp_raw, fund_raw, today):
    """Plain-language urgency for a non-technical reader: (text, urgency). Expiration
    drives it when present; otherwise fall back to how long since training was funded."""
    if exp_raw:
        try:
            d = _dt.date.fromisoformat(exp_raw[:10])
            if d < today:
                return f"Expired {_mon_yr(d)}", "expired"
            if (d - today).days <= EXPIRY_SOON_DAYS:
                return f"Expiring soon ({_mon_day(d)})", "soon"
            return f"Due by {_mon_day(d, yr=True)}", "future"
        except ValueError:
            pass
    try:
        f = _dt.date.fromisoformat(fund_raw[:10]) if fund_raw else None
    except (ValueError, TypeError):
        f = None
    return (f"Waiting since {_mon_yr(f)}", "wait") if f else ("", "none")


def _last_contact_label(last_call_iso):
    if not last_call_iso:
        return "Not yet"
    try:
        return _mon_day(_dt.date.fromisoformat(str(last_call_iso)[:10]))
    except ValueError:
        return str(last_call_iso)


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
    # (2/2 is the standard package), NOT a to-do count. A deal that sold no training
    # (0/0) is not a trainer task and is dropped here. Whether a sold modality is DONE
    # is decided below: primarily by the trainer's 'Training Remaining from Order' count,
    # with OPD certifications as the fallback where that count is blank.
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
        # Trainer-maintained remaining counts (None when the field is blank). Kept as
        # per-deal lists so a company with several funded deals sums correctly below.
        _ra = _num_opt(dp.get(REMAINING_ABD))
        _rc = _num_opt(dp.get(REMAINING_CARD))
        candidates.append({"deal_id": did, "company_id": co_id, "company": co,
                           "deal": dp, "install_dt": install_dt,
                           "allot_a": allot_a, "allot_c": allot_c,
                           "_rem_a_vals": [_ra] if _ra is not None else [],
                           "_rem_c_vals": [_rc] if _rc is not None else []})

    # Collapse to one row per company: a company can carry several funded deals (an
    # original install plus an upgrade). Merge the allotment as the max sold per
    # modality, sum the remaining counts across the company's deals, and keep the
    # most-recently-funded deal's fields for display.
    by_company: dict = {}
    for c in candidates:
        prev = by_company.get(c["company_id"])
        if prev is None:
            by_company[c["company_id"]] = c
            continue
        prev["allot_a"] = max(prev["allot_a"], c["allot_a"])
        prev["allot_c"] = max(prev["allot_c"], c["allot_c"])
        prev["_rem_a_vals"] += c["_rem_a_vals"]
        prev["_rem_c_vals"] += c["_rem_c_vals"]
        if (c["deal"].get("funding_received_date_stamp") or "") > \
           (prev["deal"].get("funding_received_date_stamp") or ""):
            prev["deal"] = c["deal"]
            prev["deal_id"] = c["deal_id"]
    candidates = list(by_company.values())
    # Resolve each company's remaining to a single number per modality: the sum of the
    # deals that carry a value, or None when every one of them was left blank.
    for c in candidates:
        c["rem_a"] = sum(c["_rem_a_vals"]) if c["_rem_a_vals"] else None
        c["rem_c"] = sum(c["_rem_c_vals"]) if c["_rem_c_vals"] else None

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
    # needs_<m> = sold that modality AND neither signal says trained: OPD holds no
    # post-install certification for it AND the trainer has not zeroed its remaining
    # count. Either an OPD cert or a trainer-set 0 clears the modality.
    members = []
    for c in candidates:
        certs = cert_after.get(c["deal_id"], {"abdominal": 0, "cardiac": 0})
        c["certs"] = certs
        c["needs_a"], c["needs_c"] = _needs_training(
            c["allot_a"], c["allot_c"], certs["abdominal"], certs["cardiac"],
            rem_a=c["rem_a"], rem_c=c["rem_c"])
        c["verified"] = verified.get(c["deal_id"], False)
        # Basis note: for a still-needed modality, did the trainer confirm a count
        # (>0) or leave it blank so only OPD's no-cert kept it on the list?
        _need_blank = ((c["needs_a"] and c["rem_a"] is None)
                       or (c["needs_c"] and c["rem_c"] is None))
        c["basis"] = ("No cert; trainer count blank" if _need_blank
                      else "No cert; trainer count > 0")
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
            "Abd Remaining (HubSpot)": "" if c["rem_a"] is None else c["rem_a"],
            "Card Remaining (HubSpot)": "" if c["rem_c"] is None else c["rem_c"],
            "Completion Basis": c["basis"],
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

    n_trainers = sum(1 for _t, _n in trainer_counts.items() if _n > 0)
    _needs_label = {"Abdominal + Cardiac": "Abdominal & Cardiac"}

    subject = f"Training to schedule - {len(rows)} clinics still need their session ({today.isoformat()})"

    # Plain body — friendly and jargon-free (the numbers detail lives in the spreadsheet).
    plain = [
        "Training Team,", "",
        f"Below are the {len(rows)} installed clinics that still need their training "
        f"scheduled (across {n_trainers} trainers). Each one paid for training (abdominal, "
        f"cardiac, or both) that we don't yet have on record as completed. Please call your "
        f"clinics and get the session booked, starting with anything marked Expired or "
        f"Expiring soon.", "",
        "If a clinic tells you they've already been trained, just reply and flag it and "
        "we'll double-check.", "",
    ]
    for trainer in sorted(trainer_counts.keys(),
                          key=lambda t: (t == "Unassigned", t)):
        sub = [r for r in rows if r["Training Sonographer"] == trainer]
        plain.append(f"=== {trainer} - {len(sub)} clinic{'' if len(sub) == 1 else 's'} ===")
        if not sub:
            plain += ["  (none this week)", ""]
            continue
        for r in sub:
            status, _u = _status_label(r["Expiration Date"], r["Training Sold"], today)
            needs = _needs_label.get(r["Needs Training"], r["Needs Training"])
            loc = f"{r['City']}, {r['State']}".strip(", ")
            flag = "  [confirm in OPD]" if r["OPD Match"] != "Verified" else ""
            plain.append(f"  - {r['Clinic']} ({loc}){flag}")
            plain.append(
                f"      Needs {needs}  |  {status or 'Waiting'}  |  "
                f"Call {r['Phone'] or 'no phone on file'}  |  "
                f"Last contacted {_last_contact_label(r['Last Call'])}"
            )
        plain.append("")
    plain += ["Full detail (install dates, certification counts, and call history) is in the "
              "attached spreadsheet, one tab per trainer."]
    plain_body = "\n".join(plain)

    # HTML body — the primary view for trainers. A clean call sheet: who to call, what
    # they need, how urgent (plain words), and when they were last contacted.
    html = ['<html><body style="font-family:Calibri,Arial,sans-serif;font-size:13px;color:#1f2733">',
            "<p>Training Team,</p>",
            f"<p>Below are the <b>{len(rows)}</b> installed clinics that still need their "
            f"training scheduled (across {n_trainers} trainers). Each one paid for training "
            f"(abdominal, cardiac, or both) that we don't yet have on record as completed. "
            f"Please call your clinics and get the session booked, starting with anything marked "
            f'<b style="color:#b3261e">Expired</b> or <b style="color:#b26a00">Expiring soon</b>.</p>',
            "<p>If a clinic tells you they've already been trained, just reply and flag it and "
            "we'll double-check. Clinic names link to HubSpot.</p>"]
    for trainer in sorted(trainer_counts.keys(),
                          key=lambda t: (t == "Unassigned", t)):
        sub = [r for r in rows if r["Training Sonographer"] == trainer]
        html.append(f'<h3 style="margin:18px 0 4px 0;">{_htmlmod.escape(trainer)} '
                    f'<span style="color:#666;font-weight:normal;">- {len(sub)} '
                    f'clinic{"" if len(sub) == 1 else "s"} to call</span></h3>')
        if not sub:
            html.append('<p style="color:#666;margin:0 0 12px 0;">None this week.</p>')
            continue
        html.append('<table cellspacing="0" cellpadding="7" '
                    'style="border-collapse:collapse;border:1px solid #d9dde3;'
                    'font-family:Calibri,Arial,sans-serif;font-size:13px;">')
        html.append(
            '<tr style="background:#5f93a3;color:#0e2a33;text-align:left;">'
            "<th>Clinic</th><th>Needs</th><th>Status</th>"
            "<th>Phone</th><th>Location</th><th>Last contacted</th></tr>"
        )
        for r in sub:
            clinic_cell = (f'<a href="{r["HubSpot"]}" style="color:#0b6bcb;'
                           f'text-decoration:none">{_htmlmod.escape(r["Clinic"])}</a>')
            if r["OPD Match"] != "Verified":
                clinic_cell += '<span style="color:#b26a00"> (confirm in OPD)</span>'
            status, urg = _status_label(r["Expiration Date"], r["Training Sold"], today)
            scolor = {"expired": "#b3261e", "soon": "#b26a00"}.get(urg)
            status_cell = (f'<b style="color:{scolor}">{_htmlmod.escape(status)}</b>'
                           if scolor else _htmlmod.escape(status or "Waiting"))
            needs = _htmlmod.escape(_needs_label.get(r["Needs Training"], r["Needs Training"]))
            phone = _htmlmod.escape(r["Phone"] or "-")
            loc = _htmlmod.escape(f'{r["City"]}, {r["State"]}'.strip(", "))
            last = _htmlmod.escape(_last_contact_label(r["Last Call"]))
            html.append(
                '<tr style="border-top:1px solid #d9dde3;">'
                f'<td>{clinic_cell}</td><td>{needs}</td><td>{status_cell}</td>'
                f'<td>{phone}</td><td>{loc}</td><td>{last}</td></tr>'
            )
        html.append("</table>")
    html += ["<p style='margin-top:14px'>Full detail (install dates, certification counts, and "
             "call history) is in the attached spreadsheet, one tab per trainer.</p>",
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


# ============================================================================
# Bucketed training outreach (install-date model)
# ----------------------------------------------------------------------------
# A different approach from build_email() above: no OPD cross-check. A clinic is
# owed training when any of the three trainer-maintained "Training Remaining from
# Order" fields is > 0 (Abdominal / Cardiac / Global FAST). Each such clinic is
# then bucketed by days since install:
#   - within 90 days of install -> ACTIVE: the assigned sonographer's window to
#     schedule (email to the training team).
#   - past 90 days              -> EXPIRED: Steph's to resell at a discount
#     (separate email to Steph).
# The 90-day line is measured from us_install_date__c to today.
# ============================================================================
REMAINING_GFAST = "training_remaining_from_order_global_fast"
POST_INSTALL_WINDOW_DAYS = 90
STEPH_RECIPIENT = "Stephanie Mendoza <smendoza@oncurapartners.com>"
# Both training outreach emails (active + resale) are Cc'd to the training manager
# and Alexander for oversight.
OUTREACH_CC = [
    "Melissa Colpitts <mcolpitts@oncurapartners.com>",
    "Alexander Jordain <ajordain@oncurapartners.com>",
]


def _owed_modality(allot, rem):
    """(owed_count, needs_hubspot_update) for one modality.

    rem is the 'Training Remaining from Order' value (None when the field is blank).
    - rem filled: owed = rem (0 = done); no data-quality flag.
    - rem blank but the modality WAS sold (allotment > 0): treat the full allotment
      as still owed and flag it, since the trainer has not recorded a remaining count.
    - nothing sold and rem blank: not owed."""
    if rem is not None:
        return (rem if rem > 0 else 0, False)
    if allot > 0:
        return (allot, True)
    return (0, False)


def _outreach_needs(a, c, g, nua=False, nuc=False, nug=False):
    """Human 'Needs' string from remaining counts, e.g. 'Cardiac (2), Global FAST (1)'.
    A modality inferred from a blank remaining field is tagged '(Needs HubSpot Update)'."""
    def _lab(name, n, nu):
        return f"{name} ({n}{', Needs HubSpot Update' if nu else ''})"
    parts = []
    if a > 0:
        parts.append(_lab("Abdominal", a, nua))
    if c > 0:
        parts.append(_lab("Cardiac", c, nuc))
    if g > 0:
        parts.append(_lab("Global FAST", g, nug))
    return ", ".join(parts)


def _pack_email(subject, to, cc, plain_body, html_body, xlsx_bytes, xlsx_filename, today):
    """Assemble one email payload (+ .eml) matching build_email()'s contract."""
    msg = EmailMessage()
    msg["Subject"] = subject
    if _FROM:
        msg["From"] = _FROM
    msg["To"] = ", ".join(to) if isinstance(to, (list, tuple)) else str(to)
    if cc:
        msg["Cc"] = ", ".join(cc) if isinstance(cc, (list, tuple)) else str(cc)
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")
    if xlsx_bytes:
        msg.add_attachment(
            xlsx_bytes, maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=xlsx_filename)
    return {
        "subject": subject, "to": list(to) if isinstance(to, (list, tuple)) else [to],
        "cc": list(cc) if isinstance(cc, (list, tuple)) else ([cc] if cc else []),
        "plain": plain_body, "html": html_body,
        "xlsx_bytes": xlsx_bytes, "xlsx_filename": xlsx_filename,
        "eml_bytes": bytes(msg),
        "eml_filename": xlsx_filename.replace(".xlsx", ".eml"),
    }


def build_bucketed_outreach(today=None) -> dict:
    """Build the two-bucket training outreach. Returns {'active': payload, 'expired':
    payload, 'active_count', 'expired_count'} where each payload has subject/to/cc/
    plain/html/xlsx/eml, matching build_email()'s field contract."""
    if not TOKEN:
        raise RuntimeError("HUBSPOT_TOKEN is not set in Streamlit secrets or env.")
    s = requests.Session()
    s.headers.update(H)
    today = today or _dt.datetime.now().date()

    props = ["dealname", "funding_received_date_stamp", "migrated_00nus000001e6ghma0",
             "abdominal_trainings", "cardiac_trainings", "global_fast_training",
             REMAINING_ABD, REMAINING_CARD, REMAINING_GFAST]
    deals, after = [], None
    while True:
        body = {"filterGroups": [{"filters": [{"propertyName": "funding_received_date_stamp",
                                               "operator": "HAS_PROPERTY"}]}],
                "properties": props,
                "sorts": [{"propertyName": "funding_received_date_stamp", "direction": "DESCENDING"}],
                "limit": 200}
        if after:
            body["after"] = after
        r = s.post("https://api.hubapi.com/crm/v3/objects/deals/search", json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        deals.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(0.05)

    deal_ids = [d["id"] for d in deals]
    deal_by_id = {d["id"]: d.get("properties", {}) for d in deals}

    # Deal -> primary company (prefer the HubSpot-primary label, else first).
    deal_to_co = {}
    for batch in _chunks(deal_ids, 100):
        r = s.post("https://api.hubapi.com/crm/v4/associations/deals/companies/batch/read",
                   json={"inputs": [{"id": d} for d in batch]}, timeout=30)
        r.raise_for_status()
        for row in r.json().get("results", []):
            tos = row.get("to", [])
            primary = next((t for t in tos
                            if any("primary" in str(at.get("label", "")).lower()
                                   for at in t.get("associationTypes", []))), None)
            chosen = primary or (tos[0] if tos else None)
            if chosen:
                deal_to_co[row["from"]["id"]] = str(chosen["toObjectId"])
        time.sleep(0.05)

    companies = {}
    for batch in _chunks(list(set(deal_to_co.values())), 100):
        r = s.post("https://api.hubapi.com/crm/v3/objects/companies/batch/read",
                   json={"properties": CO_PROPS, "inputs": [{"id": c} for c in batch]}, timeout=30)
        r.raise_for_status()
        for row in r.json().get("results", []):
            companies[row["id"]] = row.get("properties", {})
        time.sleep(0.05)

    # ---- Aggregate to one record per company: sum remaining across its deals. ----
    by_company: dict = {}
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
        if _norm(co.get("name")).startswith(EXCLUDE_PREFIX):
            continue
        oa, nua = _owed_modality(_num(dp.get("abdominal_trainings")), _num_opt(dp.get(REMAINING_ABD)))
        oc, nuc = _owed_modality(_num(dp.get("cardiac_trainings")), _num_opt(dp.get(REMAINING_CARD)))
        og, nug = _owed_modality(_num(dp.get("global_fast_training")), _num_opt(dp.get(REMAINING_GFAST)))
        fund_raw = (dp.get("funding_received_date_stamp") or "")[:10]
        e = by_company.setdefault(co_id, {
            "co": co, "co_id": co_id, "install_dt": install_dt,
            "rem_a": 0, "rem_c": 0, "rem_g": 0,
            "nu_a": False, "nu_c": False, "nu_g": False, "deal_id": did, "fund": fund_raw})
        e["rem_a"] += oa
        e["rem_c"] += oc
        e["rem_g"] += og
        e["nu_a"] = e["nu_a"] or nua
        e["nu_c"] = e["nu_c"] or nuc
        e["nu_g"] = e["nu_g"] or nug
        if fund_raw > e["fund"]:
            e["fund"], e["deal_id"] = fund_raw, did

    # Membership: any remaining modality > 0. Bucket by days since install.
    members = [e for e in by_company.values() if (e["rem_a"] + e["rem_c"] + e["rem_g"]) > 0]
    for e in members:
        e["days_since_install"] = (today - e["install_dt"]).days
        e["active"] = e["days_since_install"] <= POST_INSTALL_WINDOW_DAYS
        e["days_left"] = POST_INSTALL_WINDOW_DAYS - e["days_since_install"]

    # Resolve sonographer owner IDs to names.
    owner_ids = {e["co"].get("test_training_sonographer") for e in members
                 if e["co"].get("test_training_sonographer")}
    owner_names = {}
    for oid in owner_ids:
        if not oid:
            continue
        rr = s.get(f"https://api.hubapi.com/crm/v3/owners/{oid}", timeout=15)
        if rr.status_code == 200:
            p = rr.json()
            owner_names[str(oid)] = (f"{p.get('firstName','')} {p.get('lastName','')}".strip()
                                     or p.get("email", ""))
        time.sleep(0.03)

    # Last-contact enrichment (calls in the last 90 days), members only.
    company_calls = {}
    window_ms = int((_dt.datetime.now() - _dt.timedelta(days=CALL_WINDOW_DAYS)).timestamp() * 1000)
    for e in members:
        co_id = e["co_id"]
        if co_id in company_calls:
            continue
        cd = []
        try:
            r = s.get(f"https://api.hubapi.com/crm/v4/objects/companies/{co_id}/associations/calls",
                      params={"limit": 500}, timeout=30)
            r.raise_for_status()
            call_ids = [str(x["toObjectId"]) for x in r.json().get("results", [])]
            for batch in _chunks(call_ids, 100):
                rr = s.post("https://api.hubapi.com/crm/v3/objects/calls/batch/read",
                            json={"properties": ["hs_timestamp"], "inputs": [{"id": ci} for ci in batch]},
                            timeout=30)
                rr.raise_for_status()
                for row in rr.json().get("results", []):
                    ts_raw = row.get("properties", {}).get("hs_timestamp")
                    if not ts_raw:
                        continue
                    try:
                        ts = _dt.datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        continue
                    if ts.timestamp() * 1000 >= window_ms:
                        cd.append(ts)
        except Exception:  # noqa: BLE001 - call activity is optional
            cd = []
        company_calls[co_id] = cd
        time.sleep(0.03)

    def _row(e):
        co = e["co"]
        tid = co.get("test_training_sonographer")
        trainer = owner_names.get(str(tid) if tid else "", "") or "Unassigned"
        last_call = max(company_calls.get(e["co_id"], []), default=None)
        installed = e["days_since_install"] >= 0
        return {
            "Clinic": _display_name(co.get("name")),
            "Sonographer": trainer,
            "Needs": _outreach_needs(e["rem_a"], e["rem_c"], e["rem_g"],
                                     e["nu_a"], e["nu_c"], e["nu_g"]),
            "Abd Remaining": e["rem_a"], "Card Remaining": e["rem_c"], "GFAST Remaining": e["rem_g"],
            "Phone": co.get("phone") or "",
            "City": (co.get("city") or "").title(), "State": co.get("state") or "",
            "Install Date": e["install_dt"].isoformat(),
            "Installed": "Yes" if installed else "Not Installed",
            "Days Since Install": e["days_since_install"],
            "Days Left in Window": e["days_left"] if e["active"] else "",
            "Days Past Window": "" if e["active"] else -e["days_left"],
            "Last Contacted": last_call.strftime("%Y-%m-%d") if last_call else "",
            "HubSpot": f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/company/{e['co_id']}",
        }

    active_rows = [_row(e) for e in members if e["active"]]
    expired_rows = [_row(e) for e in members if not e["active"]]
    active_rows.sort(key=lambda r: (r["Sonographer"] == "Unassigned", r["Sonographer"],
                                    r["Days Left in Window"]))
    expired_rows.sort(key=lambda r: r["Days Since Install"])  # freshest expirations first

    active = _build_active_email(active_rows, today)
    expired = _build_expired_email(expired_rows, today)
    return {"active": active, "expired": expired,
            "active_count": len(active_rows), "expired_count": len(expired_rows)}


def _build_active_email(rows, today):
    """Active bucket -> training team, grouped by sonographer, with a days-left countdown."""
    by_trainer = Counter(r["Sonographer"] for r in rows)
    subject = (f"Training to schedule - {len(rows)} clinic"
               f"{'' if len(rows) == 1 else 's'} in the 90-day window ({today.isoformat()})")

    plain = ["Training Team,", "",
             f"These {len(rows)} clinics are still inside their 90-day post-install training "
             "window. Schedule the Training before the window closes. Once a clinic passes 90 "
             "days from install it moves to Stephanie for discounted resale. Days left is the "
             "countdown to that cutoff.", ""]
    for trainer in sorted(by_trainer, key=lambda t: (t == "Unassigned", t)):
        sub = [r for r in rows if r["Sonographer"] == trainer]
        plain.append(f"=== {trainer} - {len(sub)} clinic{'' if len(sub) == 1 else 's'} ===")
        for r in sub:
            loc = f"{r['City']}, {r['State']}".strip(", ")
            window = ("Not Installed" if r["Installed"] == "Not Installed"
                      else f"{r['Days Left in Window']} days left")
            plain.append(f"  - {r['Clinic']} ({loc})")
            plain.append(f"      Needs {r['Needs']}  |  {window}  |  "
                         f"Call {r['Phone'] or 'no phone on file'}  |  "
                         f"Last contacted {_last_contact_label(r['Last Contacted'])}")
        plain.append("")
    plain_body = "\n".join(plain)

    html = ['<html><body style="font-family:Calibri,Arial,sans-serif;font-size:13px;color:#1f2733">',
            "<p>Training Team,</p>",
            f"<p>These <b>{len(rows)}</b> clinics are still inside their 90-day post-install "
            "training window. Schedule the Training before the window closes. Once a clinic "
            "passes 90 days from install it moves to Stephanie for discounted resale. "
            "<b>Days left</b> is the countdown to that cutoff.</p>"]
    for trainer in sorted(by_trainer, key=lambda t: (t == "Unassigned", t)):
        sub = [r for r in rows if r["Sonographer"] == trainer]
        html.append(f'<h3 style="margin:18px 0 4px 0;">{_htmlmod.escape(trainer)} '
                    f'<span style="color:#666;font-weight:normal;">- {len(sub)} '
                    f'clinic{"" if len(sub) == 1 else "s"}</span></h3>')
        html.append('<table cellspacing="0" cellpadding="7" style="border-collapse:collapse;'
                    'border:1px solid #d9dde3;font-family:Calibri,Arial,sans-serif;font-size:13px;">')
        html.append('<tr style="background:#5f93a3;color:#0e2a33;text-align:left;">'
                    "<th>Clinic</th><th>Needs</th><th>Days left</th><th>Phone</th>"
                    "<th>Location</th><th>Last contacted</th></tr>")
        for r in sub:
            clinic = (f'<a href="{r["HubSpot"]}" style="color:#0b6bcb;text-decoration:none">'
                      f'{_htmlmod.escape(r["Clinic"])}</a>')
            if r["Installed"] == "Not Installed":
                dcell = '<span style="color:#666">Not Installed</span>'
            else:
                dl = r["Days Left in Window"]
                dcolor = "#b3261e" if dl <= 14 else ("#b26a00" if dl <= 30 else None)
                dcell = (f'<b style="color:{dcolor}">{dl} days</b>' if dcolor else f"{dl} days")
            loc = _htmlmod.escape(f'{r["City"]}, {r["State"]}'.strip(", "))
            html.append('<tr style="border-top:1px solid #d9dde3;">'
                        f'<td>{clinic}</td><td>{_htmlmod.escape(r["Needs"])}</td><td>{dcell}</td>'
                        f'<td>{_htmlmod.escape(r["Phone"] or "-")}</td><td>{loc}</td>'
                        f'<td>{_htmlmod.escape(_last_contact_label(r["Last Contacted"]))}</td></tr>')
        html.append("</table>")
    html += ["<p style='margin-top:14px'>Full detail is in the attached spreadsheet.</p>",
             "</body></html>"]
    html_body = "\n".join(html)

    xlsx_bytes = _outreach_xlsx(rows, "Active (in window)")
    return _pack_email(subject, list(_TO), list(OUTREACH_CC), plain_body, html_body,
                       xlsx_bytes, f"Training_Active_{today.isoformat()}.xlsx", today)


def _build_expired_email(rows, today):
    """Expired bucket -> Steph, flat list, freshest expirations first, resale framing."""
    subject = (f"Resale opportunities - {len(rows)} clinic"
               f"{'' if len(rows) == 1 else 's'} past the training window ({today.isoformat()})")

    plain = ["Steph,", "",
             f"These {len(rows)} clinics passed their 90-day post-install training window with "
             "training still unused, so they are yours to resell at a discount. Freshest "
             "expirations are listed first. 'Days past window' is how long ago the 90 days "
             "lapsed.", ""]
    for r in rows:
        loc = f"{r['City']}, {r['State']}".strip(", ")
        plain.append(f"  - {r['Clinic']} ({loc})")
        plain.append(f"      Available: {r['Needs']}  |  {r['Days Past Window']} days past window  |  "
                     f"Installed {r['Install Date']}  |  Trainer {r['Sonographer']}  |  "
                     f"Call {r['Phone'] or 'no phone on file'}")
    plain.append("")
    plain_body = "\n".join(plain)

    html = ['<html><body style="font-family:Calibri,Arial,sans-serif;font-size:13px;color:#1f2733">',
            "<p>Steph,</p>",
            f"<p>These <b>{len(rows)}</b> clinics passed their 90-day post-install training window "
            "with training still unused, so they are yours to resell at a discount. Freshest "
            "expirations are first; <b>Days past window</b> is how long ago the 90 days lapsed.</p>",
            '<table cellspacing="0" cellpadding="7" style="border-collapse:collapse;'
            'border:1px solid #d9dde3;font-family:Calibri,Arial,sans-serif;font-size:13px;">',
            '<tr style="background:#5f93a3;color:#0e2a33;text-align:left;">'
            "<th>Clinic</th><th>Available</th><th>Days past window</th><th>Installed</th>"
            "<th>Trainer</th><th>Phone</th><th>Location</th></tr>"]
    for r in rows:
        clinic = (f'<a href="{r["HubSpot"]}" style="color:#0b6bcb;text-decoration:none">'
                  f'{_htmlmod.escape(r["Clinic"])}</a>')
        loc = _htmlmod.escape(f'{r["City"]}, {r["State"]}'.strip(", "))
        html.append('<tr style="border-top:1px solid #d9dde3;">'
                    f'<td>{clinic}</td><td>{_htmlmod.escape(r["Needs"])}</td>'
                    f'<td>{r["Days Past Window"]}</td><td>{_htmlmod.escape(r["Install Date"])}</td>'
                    f'<td>{_htmlmod.escape(r["Sonographer"])}</td>'
                    f'<td>{_htmlmod.escape(r["Phone"] or "-")}</td><td>{loc}</td></tr>')
    html += ["</table>", "<p style='margin-top:14px'>Full detail is in the attached spreadsheet.</p>",
             "</body></html>"]
    html_body = "\n".join(html)

    xlsx_bytes = _outreach_xlsx(rows, "Expired (resale)")
    return _pack_email(subject, [STEPH_RECIPIENT], list(OUTREACH_CC), plain_body, html_body,
                       xlsx_bytes, f"Training_Resale_{today.isoformat()}.xlsx", today)


def _outreach_xlsx(rows, sheet_name):
    cols = ["Clinic", "Sonographer", "Needs", "Abd Remaining", "Card Remaining",
            "GFAST Remaining", "Install Date", "Installed", "Days Since Install",
            "Days Left in Window", "Days Past Window", "Phone", "City", "State",
            "Last Contacted", "HubSpot"]
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        df.to_excel(w, sheet_name=sheet_name[:31], index=False)
    return bio.getvalue()
