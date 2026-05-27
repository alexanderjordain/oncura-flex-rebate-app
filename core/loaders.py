"""Shared data loaders for the Streamlit pages. Wraps core.store so masters/config load from
the GitHub copy (live) with a local fallback, cached in session.
"""
from __future__ import annotations

import streamlit as st

from . import store


def _load(rel_path, default):
    data, sha = store.load_json(rel_path, default=default)
    return data if data is not None else default, sha


@st.cache_data(show_spinner=False)
def rebate_master():
    data, _ = _load("rebate_master.json", {"clinics": []})
    return data


@st.cache_data(show_spinner=False)
def flex_master():
    data, _ = _load("flex_master.json", {"clinics": []})
    return data


@st.cache_data(show_spinner=False)
def item_map():
    data, _ = _load("opd_item_map.json", {})
    return data


@st.cache_data(show_spinner=False)
def config():
    data, _ = _load("config.json", {})
    return data


@st.cache_data(show_spinner=False)
def name_map():
    data, _ = _load("name_map.json", {"map": {}})
    return data


def clear_caches():
    rebate_master.clear()
    flex_master.clear()
    item_map.clear()
    config.clear()
    name_map.clear()
