# -*- coding: utf-8 -*-
"""多來源歌詞抓取與評分（塞壬官方 + LRCLIB），自動選較佳結果。"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

import msr_api
from text_zh import to_simplified

LRCLIB_API_BASE = "https://lrclib.net/api"

_LRC_LINE_RE = re.compile(r"\[\d{1,2}:\d{2}(?:\.\d{1,3})?\]")
_ELRC_WORD_RE = re.compile(r"<\d{1,2}:\d{2}(?:\.\d{1,3})?>")
_INSTRUMENTAL_RE = re.compile(
    r"(instrumental|\(inst\.?\)|（\s*inst(?:rumental)?\s*）|纯音乐|純音樂)",
    re.IGNORECASE,
)


def normalize_lyrics_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    # Enhanced LRC 逐字標記轉成一般 LRC，相容性較好
    text = _ELRC_WORD_RE.sub("", text)
    lines = [re.sub(r"[ \t]{2,}", " ", line).rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip() + "\n"


def looks_instrumental(title: str = "", filename: str = "") -> bool:
    return bool(_INSTRUMENTAL_RE.search(f"{title} {filename}"))


def count_synced_lines(text: str) -> int:
    return len(_LRC_LINE_RE.findall(text or ""))


def score_lyrics_candidate(
    text: str, *, source: str, title_query: str = "", result_title: str = ""
) -> int:
    """分數越高越好；沒有可用歌詞回傳 -1。"""
    text = normalize_lyrics_text(text)
    if not text.strip():
        return -1

    synced = count_synced_lines(text)
    plain_lines = sum(
        1 for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("[")
    )
    score = 0
    if synced:
        score += 1000 + synced * 3
    else:
        score += min(plain_lines, 80)
    score += min(len(text), 4000) // 50

    q = (title_query or "").strip().lower()
    rt = (result_title or "").strip().lower()
    if q and rt:
        if q == rt:
            score += 120
        elif q in rt or rt in q:
            score += 60

    if source == "msr":
        score += 40
    return score


def _http_get_bytes(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers=msr_api.HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_get_json(url: str, timeout: int = 15):
    return json.loads(_http_get_bytes(url, timeout=timeout).decode("utf-8"))


def decode_lyrics_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "utf-16", "gb18030"):
        try:
            return normalize_lyrics_text(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    return normalize_lyrics_text(raw.decode("utf-8", errors="replace"))


def fetch_msr_lyrics_by_url(lyric_url: str) -> str | None:
    if not lyric_url:
        return None
    return decode_lyrics_bytes(_http_get_bytes(lyric_url, timeout=15))


def fetch_msr_lyrics_for_song(song: dict, detail: dict | None = None) -> str | None:
    detail = detail or {}
    lyric_url = detail.get("lyricUrl") or song.get("lyricUrl") or ""
    if not lyric_url:
        return None
    return fetch_msr_lyrics_by_url(lyric_url)


def search_lrclib(
    track_name: str,
    artist_name: str = "",
    album_name: str = "",
    duration: float | None = None,
):
    params = {"track_name": track_name}
    if artist_name:
        params["artist_name"] = artist_name
    if album_name:
        params["album_name"] = album_name
    url = f"{LRCLIB_API_BASE}/search?{urllib.parse.urlencode(params)}"
    try:
        results = _http_get_json(url, timeout=15)
    except Exception:
        url = f"{LRCLIB_API_BASE}/search?{urllib.parse.urlencode({'track_name': track_name})}"
        results = _http_get_json(url, timeout=15)
    if not isinstance(results, list):
        return []

    scored = []
    for r in results:
        if r.get("instrumental"):
            continue
        text = r.get("syncedLyrics") or r.get("plainLyrics") or ""
        if not text:
            continue
        bonus = 0
        if duration is not None and r.get("duration") is not None:
            try:
                diff = abs(float(r["duration"]) - float(duration))
                if diff <= 2:
                    bonus += 80
                elif diff <= 5:
                    bonus += 30
                elif diff > 20:
                    bonus -= 40
            except (TypeError, ValueError):
                pass
        base = score_lyrics_candidate(
            text,
            source="lrclib",
            title_query=track_name,
            result_title=r.get("trackName") or r.get("name") or "",
        )
        if base < 0:
            continue
        scored.append((base + bonus, normalize_lyrics_text(text), r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def pick_best_lyrics(
    *,
    title: str,
    artist: str = "",
    album: str = "",
    duration: float | None = None,
    msr_song: dict | None = None,
    msr_detail: dict | None = None,
) -> tuple[str | None, str | None, int]:
    """回傳 (lyrics_text, source_name, score)。"""
    title = to_simplified(title)
    artist = to_simplified(artist)
    album = to_simplified(album)

    if looks_instrumental(title=title):
        return None, "instrumental", -1

    candidates: list[tuple[int, str, str]] = []

    try:
        msr_text = fetch_msr_lyrics_for_song(msr_song or {}, msr_detail)
        if msr_text:
            msr_text = to_simplified(normalize_lyrics_text(msr_text))
            sc = score_lyrics_candidate(msr_text, source="msr", title_query=title)
            if sc >= 0:
                candidates.append((sc, msr_text, "msr"))
    except Exception:
        pass

    artist_variants = []
    for a in (artist, "塞壬唱片-MSR"):
        a = (a or "").strip()
        if a and a not in artist_variants:
            artist_variants.append(a)
    if artist and "/" in artist:
        for part in artist.split("/"):
            part = part.strip()
            if part and part not in artist_variants:
                artist_variants.append(part)

    seen_texts = set()
    for a in artist_variants:
        try:
            for sc, text, _meta in search_lrclib(title, a, album, duration)[:5]:
                text = to_simplified(text)
                key = text.strip()
                if key in seen_texts:
                    continue
                seen_texts.add(key)
                candidates.append((sc, text, "lrclib"))
        except Exception:
            continue
        if candidates and max(c[0] for c in candidates) >= 1100:
            break

    if not candidates:
        return None, None, -1
    candidates.sort(key=lambda x: x[0], reverse=True)
    score, text, source = candidates[0]
    return text, source, score
