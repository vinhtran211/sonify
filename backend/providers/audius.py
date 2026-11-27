"""Audius Decentralized REST API provider (keyless full-length MP3 streams)."""

from __future__ import annotations

import re
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from backend import config, http_client, lrc_parser

APP_NAME = "Sonify"
DISCOVERY_API = "https://api.audius.co"
FALLBACK_HOST = "https://discoveryprovider.audius.co"
MIN_TRACK_BYTES = 350_000

is_valid_full_duration = lrc_parser.is_valid_full_duration

_NOISE_KEYWORDS = (
    "nightcore",
    "techno",
    "ambient",
    "cover",
    "remix",
    "karaoke",
    "instrumental",
    "slowed",
    "sped up",
    "lofi",
    "lo-fi",
    "piano",
    "8d",
    "reverb",
    "tribute",
    "lullaby",
    "acoustic",
    "mashup",
)
_NOISE_PATTERNS = [
    (kw, re.compile(rf"(?<!\w){re.escape(kw)}(?!\w)", re.IGNORECASE))
    for kw in _NOISE_KEYWORDS
]

_HOST_LOCK = threading.Lock()
_CACHED_HOST: str = FALLBACK_HOST
_CACHED_HOST_AT: float = 0.0


def get_discovery_host(force_refresh: bool = False) -> str:
    global _CACHED_HOST, _CACHED_HOST_AT
    now = time.time()
    with _HOST_LOCK:
        if not force_refresh and _CACHED_HOST and (now - _CACHED_HOST_AT) < config.AUDIUS_HOST_TTL:
            return _CACHED_HOST

    chosen = FALLBACK_HOST
    try:
        data = http_client.fetch_json(DISCOVERY_API, timeout=4.0)
        hosts = data.get("data") or []
        for h in hosts:
            if isinstance(h, str) and h.startswith("https://"):
                chosen = h.rstrip("/")
                break
    except Exception:
        pass

    with _HOST_LOCK:
        _CACHED_HOST = chosen
        _CACHED_HOST_AT = now
    return chosen


def _normalize_track(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tid = raw.get("id")
    if not tid:
        return None
    user = raw.get("user") or {}
    artwork = raw.get("artwork") or {}
    if isinstance(artwork, dict):
        cover_small = str(artwork.get("480x480") or artwork.get("150x150") or "")
        cover_large = str(artwork.get("1000x1000") or cover_small)
    else:
        cover_small = cover_large = ""
    uid = user.get("id")
    return {
        "id": f"au:{tid}",
        "source": "audius",
        "title": str(raw.get("title") or "Untitled"),
        "artist": str(user.get("name") or user.get("handle") or "Unknown Artist"),
        "artistId": f"au:{uid}" if uid else "",
        "album": str(raw.get("genre") or "Audius Single"),
        "albumId": "",
        "duration": int(raw.get("duration") or 0),
        "coverSmall": cover_small,
        "coverLarge": cover_large,
        "explicit": False,
        "releaseDate": str(raw.get("release_date") or "")[:10],
        "previewUrl": "",
        "streamUrl": f"/api/stream/au:{tid}",
        "_extra": {"audius_id": str(tid)},
    }


def get_trending(limit: int = 15) -> List[Dict[str, Any]]:
    host = get_discovery_host()
    params = urllib.parse.urlencode({"time": "week", "limit": str(limit), "app_name": APP_NAME})
    data = http_client.fetch_json(f"{host}/v1/tracks/trending?{params}", timeout=config.PROVIDER_TIMEOUT)
    return [t for t in (_normalize_track(x) for x in (data.get("data") or [])) if t]


def search_tracks(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    q = query.strip()
    if not q:
        return []
    host = get_discovery_host()
    params = urllib.parse.urlencode({"query": q, "limit": str(limit), "app_name": APP_NAME})
    data = http_client.fetch_json(f"{host}/v1/tracks/search?{params}", timeout=config.PROVIDER_TIMEOUT)
    return [t for t in (_normalize_track(x) for x in (data.get("data") or [])) if t]


def get_track(raw_id: str) -> Optional[Dict[str, Any]]:
    host = get_discovery_host()
    params = urllib.parse.urlencode({"app_name": APP_NAME})
    data = http_client.fetch_json(f"{host}/v1/tracks/{raw_id}?{params}", timeout=config.PROVIDER_TIMEOUT)
    raw = data.get("data")
    if isinstance(raw, dict):
        return _normalize_track(raw)
    return None


def get_artist(raw_id: str) -> Optional[Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]]:
    host = get_discovery_host()
    params = urllib.parse.urlencode({"app_name": APP_NAME})
    user_data = http_client.fetch_json(f"{host}/v1/users/{raw_id}?{params}", timeout=config.PROVIDER_TIMEOUT)
    u = user_data.get("data") or {}
    if not u:
        return None
    pp = u.get("profile_picture") or {}
    pic_small = str(pp.get("480x480") or pp.get("150x150") or "") if isinstance(pp, dict) else ""
    pic_large = str(pp.get("1000x1000") or pic_small) if isinstance(pp, dict) else ""
    artist = {
        "id": f"au:{raw_id}",
        "source": "audius",
        "name": str(u.get("name") or u.get("handle") or "Unknown Artist"),
        "pictureSmall": pic_small,
        "pictureLarge": pic_large,
        "fans": int(u.get("follower_count") or 0),
    }
    tracks: List[Dict[str, Any]] = []
    try:
        tr_data = http_client.fetch_json(
            f"{host}/v1/users/{raw_id}/tracks?{params}&limit=25", timeout=config.PROVIDER_TIMEOUT
        )
        tracks = [t for t in (_normalize_track(x) for x in (tr_data.get("data") or [])) if t]
    except Exception:
        pass
    return artist, tracks, []


def get_stream_url(raw_id: str) -> str:
    host = get_discovery_host()
    return f"{host}/v1/tracks/{urllib.parse.quote(str(raw_id))}/stream?app_name={APP_NAME}"


def _noise_penalty(cand_text: str, target_text: str) -> float:
    penalty = 0.0
    for _, pat in _NOISE_PATTERNS:
        if pat.search(cand_text) and not pat.search(target_text):
            penalty += 0.35
    return penalty


def find_best_stream(
    title: str,
    artist: str,
    duration: int = 0,
    min_score: float = 0.78,
    relaxed: bool = False,
) -> Optional[Tuple[str, str]]:
    clean_title = lrc_parser.clean_track_title(title)
    primary_artist = lrc_parser.extract_primary_artist(artist)
    query = f"{primary_artist} {clean_title}".strip()
    if not query:
        return None

    eff_min_score = 0.48 if (relaxed and min_score == 0.78) else min_score
    queries: List[str] = [query]
    if relaxed:
        for rq in (
            f"{primary_artist} {clean_title} audio".strip(),
            f"{clean_title} {primary_artist}".strip(),
            clean_title.strip() if len(clean_title.strip()) >= 5 else "",
        ):
            if rq and all(rq.lower() != q.lower() for q in queries):
                queries.append(rq)

    target_context = f"{title or ''} {artist or ''}"
    target_has_artist = bool(
        primary_artist and primary_artist.lower() not in ("unknown artist", "various artists")
    )

    best_score = 0.0
    best_id = ""
    seen_ids: set[str] = set()

    for q in queries:
        try:
            candidates = search_tracks(q, limit=8)
        except Exception:
            candidates = []

        for cand in candidates:
            cid = str(cand.get("id") or "")
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)

            cand_title = str(cand.get("title") or "")
            cand_artist = str(cand.get("artist") or "")
            cand_dur = int(cand.get("duration") or 0)

            # Universal anti-short-clip floor (>= 60s unless genuine short interlude)
            if not is_valid_full_duration(cand_dur, duration):
                continue
            if relaxed and cand_dur > 900:
                continue

            # Hard Gate 1: Title similarity (>= 0.60 strict, >= 0.52 relaxed)
            title_sim = max(
                lrc_parser.token_similarity(lrc_parser.clean_track_title(cand_title), clean_title),
                lrc_parser.token_similarity(cand_title, clean_title),
            )
            if title_sim < (0.52 if relaxed else 0.60):
                continue

            # Hard Gate 2: Artist similarity when target artist is known
            artist_sim = max(
                lrc_parser.token_similarity(cand_artist, primary_artist),
                lrc_parser.token_similarity(cand_artist, artist or primary_artist),
            )
            if relaxed:
                if (
                    artist_sim >= 0.25
                    or lrc_parser.has_primary_artist_token(primary_artist, f"{cand_title} {cand_artist}")
                    or (
                        title_sim >= 0.85
                        and (duration <= 30 or (cand_dur > 0 and abs(cand_dur - duration) <= 45))
                    )
                ):
                    artist_sim = max(artist_sim, 0.50)
                elif target_has_artist:
                    continue
            else:
                if target_has_artist and artist_sim < 0.50:
                    continue

            # Hard Gate 3: Duration within max(25s, 20%) in strict mode; [60, 900] in relaxed mode
            if not relaxed and duration > 30 and cand_dur > 0:
                max_diff = max(25, int(duration * 0.20))
                if abs(cand_dur - duration) > max_diff:
                    continue

            if duration > 0 and cand_dur > 0:
                dur_prox = max(
                    0.25 if relaxed else 0.0,
                    1.0 - abs(cand_dur - duration) / (90.0 if relaxed else 25.0),
                )
            else:
                dur_prox = 0.65
            score = 0.55 * title_sim + 0.30 * artist_sim + 0.15 * dur_prox
            noise_pen = _noise_penalty(f"{cand_title} {cand_artist}", target_context)
            if relaxed:
                noise_pen *= 0.25
            score -= noise_pen
            if score >= eff_min_score and score > best_score:
                best_score = score
                best_id = str(cand["id"]).split(":", 1)[-1]

        if best_id:
            break

    if not best_id:
        return None

    stream_url = get_stream_url(best_id)
    ok, _, hdrs, final_url = http_client.probe_url(
        stream_url, timeout=config.STREAM_PROBE_TIMEOUT, min_total_bytes=MIN_TRACK_BYTES
    )
    if ok:
        ctype = hdrs.get("content-type", "audio/mpeg").split(";")[0].strip()
        if not ctype.startswith("audio/"):
            ctype = "audio/mpeg"
        return final_url, ctype
    return None
