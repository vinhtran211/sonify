"""Multi-stage synced & plain lyrics provider (LRCLIB -> YTM -> Lyrics.ovh -> Local LRC)."""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional

from backend import config, http_client, lrc_parser, offline_catalog
from backend.db import Database
from backend.providers import youtube_music

LRCLIB_BASE = "https://lrclib.net/api"
LYRICS_OVH_BASE = "https://api.lyrics.ovh/v1"


def _lrclib_headers() -> Dict[str, str]:
    return {
        "User-Agent": config.LRCLIB_USER_AGENT,
        "Accept": "application/json",
    }


def _build_response(
    track_id: str,
    title: str,
    artist: str,
    status: str,
    source: str,
    instrumental: bool,
    synced_lrc: str,
    plain_lyrics: str,
) -> Dict[str, Any]:
    lines = lrc_parser.parse_lrc(synced_lrc) if synced_lrc else []
    is_synced = status == "synced" and len(lines) > 0
    if is_synced and not plain_lyrics:
        plain_lyrics = lrc_parser.lrc_to_plain(synced_lrc)
    if not is_synced and status == "synced":
        status = "plain" if plain_lyrics else ("instrumental" if instrumental else "not_found")
    return {
        "trackId": track_id,
        "title": title,
        "artist": artist,
        "status": status,
        "source": source,
        "instrumental": bool(instrumental),
        "synced": is_synced,
        "lines": lines,
        "plainLyrics": plain_lyrics or "",
        "rawLrc": synced_lrc or "",
    }


def _query_lrclib_get(
    title: str, artist: str, album: str = "", duration: int = 0
) -> Optional[Dict[str, Any]]:
    params: Dict[str, str] = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration and duration > 0:
        params["duration"] = str(int(duration))
    url = f"{LRCLIB_BASE}/get?{urllib.parse.urlencode(params)}"
    try:
        data = http_client.fetch_json(url, headers=_lrclib_headers(), timeout=config.PROVIDER_TIMEOUT)
        if isinstance(data, dict) and not data.get("statusCode"):
            return data
    except Exception as exc:
        if hasattr(exc, "close"):
            try:
                exc.close()
            except Exception:
                pass
    return None


def _rank_lrclib_candidates(
    candidates: List[Dict[str, Any]],
    clean_title: str,
    primary_artist: str,
    duration: int = 0,
) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None

    best_item: Optional[Dict[str, Any]] = None
    best_score = -1.0

    for item in candidates:
        if not isinstance(item, dict):
            continue
        has_synced = bool((item.get("syncedLyrics") or "").strip())
        has_plain = bool((item.get("plainLyrics") or "").strip())
        is_inst = bool(item.get("instrumental"))
        if not (has_synced or has_plain or is_inst):
            continue

        t_sim = lrc_parser.token_similarity(str(item.get("trackName") or ""), clean_title)
        a_sim = lrc_parser.token_similarity(str(item.get("artistName") or ""), primary_artist)
        cand_dur = int(item.get("duration") or 0)
        if duration > 0 and cand_dur > 0:
            diff = abs(cand_dur - duration)
            dur_bonus = 1.0 if diff <= 4 else (0.75 if diff <= 12 else max(0.0, 1.0 - diff / 45.0))
        else:
            dur_bonus = 0.6

        if t_sim < 0.35:
            continue

        score = 0.45 * t_sim + 0.35 * a_sim + 0.20 * dur_bonus
        if has_synced:
            score += 0.35
        elif has_plain:
            score += 0.10

        if score > best_score:
            best_score = score
            best_item = item

    return best_item if best_score >= 0.45 else None


def _query_lrclib_search(
    clean_title: str, primary_artist: str, duration: int = 0
) -> Optional[Dict[str, Any]]:
    queries = []
    if clean_title and primary_artist:
        queries.append(
            f"{LRCLIB_BASE}/search?{urllib.parse.urlencode({'track_name': clean_title, 'artist_name': primary_artist})}"
        )
    q_combined = f"{clean_title} {primary_artist}".strip()
    if q_combined:
        queries.append(f"{LRCLIB_BASE}/search?{urllib.parse.urlencode({'q': q_combined})}")

    for url in queries:
        try:
            data = http_client.fetch_json(url, headers=_lrclib_headers(), timeout=config.PROVIDER_TIMEOUT)
            if isinstance(data, list) and data:
                best = _rank_lrclib_candidates(data, clean_title, primary_artist, duration)
                if best:
                    return best
        except Exception as exc:
            if hasattr(exc, "close"):
                try:
                    exc.close()
                except Exception:
                    pass
            continue
    return None


def _query_lyrics_ovh(primary_artist: str, clean_title: str) -> Optional[str]:
    if not primary_artist or not clean_title:
        return None
    url = (
        f"{LYRICS_OVH_BASE}/"
        f"{urllib.parse.quote(primary_artist, safe='')}/"
        f"{urllib.parse.quote(clean_title, safe='')}"
    )
    try:
        data = http_client.fetch_json(url, timeout=4.5)
        text = str((data or {}).get("lyrics") or "").strip()
        if text:
            return text
    except Exception as exc:
        if hasattr(exc, "close"):
            try:
                exc.close()
            except Exception:
                pass
    return None


def _extract_lrclib_payload(item: Dict[str, Any]) -> tuple[str, bool, str, str]:
    synced = str(item.get("syncedLyrics") or "").strip()
    plain = str(item.get("plainLyrics") or "").strip()
    instrumental = bool(item.get("instrumental"))
    if synced:
        return "synced", False, synced, plain or lrc_parser.lrc_to_plain(synced)
    if instrumental:
        return "instrumental", True, "", ""
    if plain:
        return "plain", False, "", plain
    return "not_found", False, "", ""


def get_lyrics(
    db: Database,
    track_id: str = "",
    title: str = "",
    artist: str = "",
    album: str = "",
    duration: int = 0,
    refresh: bool = False,
) -> Dict[str, Any]:
    """Fetch synced or plain lyrics using the 5-stage waterfall + 7-day SQLite cache."""
    video_id = ""
    if track_id:
        cached_track = db.get_track(track_id, include_extra=True)
        if cached_track:
            title = title or cached_track.get("title", "")
            artist = artist or cached_track.get("artist", "")
            album = album or cached_track.get("album", "")
            duration = duration or int(cached_track.get("duration") or 0)
            video_id = str((cached_track.get("_extra") or {}).get("videoId") or "")
        elif track_id.startswith("local:"):
            lt = offline_catalog.get_offline_track(track_id)
            if lt:
                title = title or lt["title"]
                artist = artist or lt["artist"]
                album = album or lt["album"]
                duration = duration or lt["duration"]
        if track_id.startswith("yt:") and not video_id:
            video_id = track_id.split(":", 1)[1]

    # Stage 5 shortcut for local:* demo tracks
    if track_id.startswith("local:"):
        local_lyr = offline_catalog.get_offline_lyrics(track_id)
        if local_lyr:
            if local_lyr.get("instrumental"):
                return _build_response(track_id, title, artist, "instrumental", "local", True, "", "")
            raw_lrc = local_lyr.get("syncedLrc", "")
            return _build_response(
                track_id,
                title,
                artist,
                "synced",
                "local",
                False,
                raw_lrc,
                lrc_parser.lrc_to_plain(raw_lrc),
            )

    clean_title = lrc_parser.clean_track_title(title)
    primary_artist = lrc_parser.extract_primary_artist(artist)
    if not clean_title:
        return _build_response(track_id, title, artist, "not_found", "", False, "", "")

    cache_key = f"{lrc_parser.normalize_key(primary_artist)}::{lrc_parser.normalize_key(clean_title)}"

    # Cache lookup
    if not refresh:
        cached = db.get_lyrics_cache(cache_key)
        if cached:
            return _build_response(
                track_id,
                title,
                artist,
                cached["status"],
                cached["source"],
                cached["instrumental"],
                cached["syncedLrc"],
                cached["plainLyrics"],
            )

    status = "not_found"
    source = ""
    instrumental = False
    synced_lrc = ""
    plain_lyrics = ""

    # Stage 1: LRCLIB exact /api/get
    if primary_artist:
        exact = _query_lrclib_get(clean_title, artist or primary_artist, album, duration)
        if exact:
            status, instrumental, synced_lrc, plain_lyrics = _extract_lrclib_payload(exact)
            if status != "not_found":
                source = "lrclib"

    # Stage 2: LRCLIB /api/search fallback
    if status == "not_found":
        searched = _query_lrclib_search(clean_title, primary_artist, duration)
        if searched:
            status, instrumental, synced_lrc, plain_lyrics = _extract_lrclib_payload(searched)
            if status != "not_found":
                source = "lrclib"

    # Stage 3: YouTube Music InnerTube plain lyrics (if videoId is known)
    if status == "not_found" and video_id:
        yt_plain = youtube_music.fetch_plain_lyrics(video_id)
        if yt_plain:
            status = "plain"
            source = "youtube_music"
            plain_lyrics = yt_plain

    # Stage 4: Lyrics.ovh plain lyrics fallback
    if status == "not_found" and primary_artist:
        ovh_plain = _query_lyrics_ovh(primary_artist, clean_title)
        if ovh_plain:
            status = "plain"
            source = "lyrics.ovh"
            plain_lyrics = ovh_plain

    # Cache positive results (and avoid caching transient offline failures)
    if status != "not_found" or not http_client.is_offline_mode():
        # Cache not_found only when online; if offline, don't poison 7-day cache
        if status != "not_found":
            db.set_lyrics_cache(cache_key, status, source, instrumental, synced_lrc, plain_lyrics)

    return _build_response(
        track_id, title, artist, status, source, instrumental, synced_lrc, plain_lyrics
    )
