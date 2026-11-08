# -*- coding: utf-8 -*-
"""塞壬唱片（Monster Siren Records）公開 API 小封裝。"""
from __future__ import annotations

import json
import re
import urllib.request

API_BASE = "https://monster-siren.hypergryph.com/api"
HEADERS = {"User-Agent": "SirenDownloader/1.1"}

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def get_json(url: str, timeout: int = 10):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_song_list():
    return get_json(f"{API_BASE}/songs")["data"]["list"]


def fetch_song_detail(song_cid: str):
    return get_json(f"{API_BASE}/song/{song_cid}")["data"]


def fetch_album_detail(album_cid: str):
    return get_json(f"{API_BASE}/album/{album_cid}/detail")["data"]


def search_songs(query: str, song_list: list) -> list:
    q = (query or "").strip().lower()
    if not q:
        return []
    return [s for s in song_list if q in s["name"].lower()]


def format_artists(artists) -> str:
    """把 API 回傳的 artists / artistes 列表整理成單一字串。"""
    if not artists:
        return "塞壬唱片-MSR"
    parts = []
    for a in artists:
        if isinstance(a, str):
            name = a.strip()
        elif isinstance(a, dict):
            name = (a.get("name") or "").strip()
        else:
            name = str(a).strip()
        if name and name not in parts:
            parts.append(name)
    return " / ".join(parts) if parts else "塞壬唱片-MSR"


def guess_date_from_cover_url(cover_url: str) -> str:
    # 封面路徑常含 .../pic/20220826/...，取年份當近似發行年
    m = re.search(r"/pic/(\d{4})\d{4}/", cover_url or "")
    return m.group(1) if m else ""


def sanitize_filename(name: str) -> str:
    return _INVALID_FILENAME_CHARS.sub("", (name or "")).strip()
