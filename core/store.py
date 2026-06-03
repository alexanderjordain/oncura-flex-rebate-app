"""Generic JSON store: reads/writes a file in the repo via the GitHub Contents API,
with a local-file fallback. Generalized from the oncura-comp-app persistence pattern
so the app survives a maintainer handoff (state lives in the repo + Streamlit secrets,
not on any one person's machine).
"""
from __future__ import annotations

import base64
import json
import os

try:
    import requests
except ImportError:  # requests is in requirements; this guard is for bare local runs
    requests = None

# Repo the app persists into. Change this single constant when transferring to the
# Oncura org GitHub account.
GITHUB_REPO = os.environ.get("FLEXREBATE_REPO", "alexanderjordain/oncura-flex-rebate-app")
GITHUB_BRANCH = os.environ.get("FLEXREBATE_BRANCH", "main")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _github_token():
    # 1. Streamlit Cloud secrets
    try:
        import streamlit as st

        tok = st.secrets.get("GITHUB_TOKEN")
        if tok:
            return tok
    except Exception:
        pass
    # 2. Environment variable (portable to any host)
    return os.environ.get("GITHUB_TOKEN")


def _local_path(rel_path: str) -> str:
    return os.path.normpath(os.path.join(DATA_DIR, rel_path))


def load_json(rel_path: str, default=None):
    """Load data/<rel_path>. Prefer the live GitHub copy; fall back to the local file.

    Returns (data, sha). sha is the GitHub blob sha needed to commit an update,
    or None when the data came from the local file.

    Token present -> the GitHub copy is the writable source of truth. No token -> use the local
    file, so local edits persist instead of being shadowed by the (public, read-only) GitHub copy.
    """
    if _github_token():
        gh = _load_github(rel_path)
        if gh is not None:
            return gh
    return _load_local(rel_path, default), None


def _load_github(rel_path: str):
    if requests is None:
        return None
    token = _github_token()
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/data/{rel_path}",
            headers=headers,
            params={"ref": GITHUB_BRANCH},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        content = base64.b64decode(r.json()["content"]).decode("utf-8")
        return json.loads(content), r.json().get("sha")
    except Exception:
        return None


def _load_local(rel_path: str, default):
    path = _local_path(rel_path)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(rel_path: str, data, message: str, sha: str | None = None, retries: int = 3):
    """Commit data to GitHub data/<rel_path>. Always writes the local copy too.

    On a 409/412/422 (concurrent edit since we fetched sha), refetch the current
    remote content + sha, structurally merge the user's payload onto it
    (`_merge_smart` — user wins on field conflicts, other users' adds are
    preserved), and retry. Up to `retries` attempts.

    Returns (ok, message). ok=False when no token is configured, or when
    retries are exhausted, or on a non-conflict error.
    """
    _save_local(rel_path, data)

    token = _github_token()
    if not token:
        return False, "No GITHUB_TOKEN configured — saved locally only (session-only on Cloud)."
    if requests is None:
        return False, "requests not available — saved locally only."

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/data/{rel_path}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

    merge_count = 0
    for attempt in range(retries):
        # Fetch current sha (and content, if we're about to merge) when we don't have one
        if sha is None:
            try:
                r_get = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=10)
                if r_get.status_code == 200:
                    sha = r_get.json().get("sha")
                    if attempt > 0:
                        # Concurrent edit — merge our payload onto current remote
                        remote_content = json.loads(base64.b64decode(r_get.json()["content"]).decode("utf-8"))
                        merged = _merge_smart(remote_content, data)
                        if merged is None:
                            return False, "Concurrent edit could not be auto-merged — reload the page and re-apply your change."
                        data = merged
                        merge_count += 1
            except Exception:
                sha = None

        body = json.dumps(data, indent=2, ensure_ascii=False)
        payload = {
            "message": message,
            "content": base64.b64encode(body.encode("utf-8")).decode(),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha
        try:
            r = requests.put(api_url, headers=headers, json=payload, timeout=15)
            if r.status_code in (200, 201):
                suffix = f" (merged with {merge_count} concurrent edit{'s' if merge_count != 1 else ''})" if merge_count else ""
                return True, f"Committed to GitHub{suffix}. Streamlit Cloud redeploys in ~1 minute."
            if r.status_code in (409, 412, 422):
                # Concurrent edit since we fetched sha — refetch + merge on next loop
                sha = None
                continue
            return False, f"GitHub API error {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return False, f"Request failed: {e}"

    return False, "GitHub kept rejecting our save due to concurrent edits after several retries. Reload the page and try again."


# ─────────────────────────────────────────────────────────────────────────────
# Structural merge for concurrent saves. The caller has already loaded a copy,
# made edits, and tried to save; the remote moved underneath them. Goal: keep
# the user's edits AND any other user's additions/edits that don't overlap.
#
# Strategy: user wins on field conflicts. Other users' new keys / new list
# items survive. Deletions can be lost in pathological cases — acceptable
# tradeoff since this app marks `active: false` rather than removing records.
# ─────────────────────────────────────────────────────────────────────────────


_KEY_FIELDS = ("clinic_name", "id", "name", "key", "fingerprint", "sha256")


def _merge_smart(remote, user):
    """Best-effort recursive merge. Returns merged value, or None if unmergeable."""
    if isinstance(remote, dict) and isinstance(user, dict):
        out = dict(remote)
        for k, v in user.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = _merge_smart(out[k], v)
            elif k in out and isinstance(out[k], list) and isinstance(v, list):
                merged_list = _merge_list(out[k], v)
                out[k] = merged_list if merged_list is not None else v
            else:
                out[k] = v
        return out
    # Top-level not a dict — caller's version wins
    return user


def _merge_list(remote, user):
    """Merge two lists of records by a stable key field (clinic_name, id, etc.).
    Returns merged list, or None if no stable key — caller falls back to user's list."""
    if not user:
        return user  # User explicitly emptied; respect that
    if not isinstance(user[0], dict) or not isinstance(remote[0] if remote else {}, dict):
        return None  # Non-record list — fall back to user's
    key = next((k for k in _KEY_FIELDS if k in user[0]), None)
    if key is None:
        return None
    remote_by_key = {r.get(key): r for r in remote if isinstance(r, dict) and r.get(key) is not None}
    user_by_key = {u.get(key): u for u in user if isinstance(u, dict) and u.get(key) is not None}
    # User wins on overlap; remote-only keys (new adds by other users) preserved
    merged = {**remote_by_key, **user_by_key}
    # Preserve user's relative order, then append remote-only adds at the end
    user_keys_in_order = [u.get(key) for u in user if isinstance(u, dict)]
    remote_only = [k for k in remote_by_key if k not in user_by_key]
    ordered = [merged[k] for k in user_keys_in_order if k in merged] + [merged[k] for k in remote_only]
    return ordered


def _save_local(rel_path: str, data):
    path = _local_path(rel_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False
