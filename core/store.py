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
    """
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


def save_json(rel_path: str, data, message: str, sha: str | None = None):
    """Commit data to GitHub data/<rel_path>. Always writes the local copy too.

    Returns (ok, message). ok=False with an explanatory message when no token is
    configured (changes are then session/local only).
    """
    _save_local(rel_path, data)

    token = _github_token()
    if not token:
        return False, "No GITHUB_TOKEN configured — saved locally only (session-only on Cloud)."
    if requests is None:
        return False, "requests not available — saved locally only."

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/data/{rel_path}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    if sha is None:
        try:
            r = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=10)
            sha = r.json().get("sha") if r.status_code == 200 else None
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
            return True, "Committed to GitHub. Streamlit Cloud redeploys in ~1 minute."
        return False, f"GitHub API error {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"Request failed: {e}"


def _save_local(rel_path: str, data):
    path = _local_path(rel_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False
