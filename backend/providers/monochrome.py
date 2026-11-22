"""Monochrome Tracks Streamer provider (https://tracks.monochrome.st).

Keyless, $0, 100% ad-free CD-quality Lossless FLAC full-track audio streaming that
works reliably from both cloud datacenter IPs (Render/AWS) and residential networks.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from backend import config, http_client, lrc_parser

MONOCHROME_BASE = "https://tracks.monochrome.st"

_NOISE_KEYWORDS = (
    "remix",
    "cover",
    "karaoke",
    "instrumental",
    "slowed",
    "sped up",
    "nightcore",
    "live",
    "acoustic",
    "piano",
    "lofi",
    "lo-fi",
    "tribute",
    "lullaby",
    "8d",
    "reverb",
    "mashup",
)
_NOISE_PATTERNS = [
    (kw, re.compile(rf"(?<!\w){re.escape(kw)}(?!\w)", re.IGNORECASE))
    for kw in _NOISE_KEYWORDS
]


def _noise_penalty(cand_text: str, target_text: str) -> float:
    penalty = 0.0
    for _, pat in _NOISE_PATTERNS:
        if pat.search(cand_text) and not pat.search(target_text):
            penalty += 0.35
    return penalty


def _normalize_track(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tid = raw.get("id") or raw.get("trackId")
    if not tid:
        return None
    if raw.get("playable") is False:
        return None

    artist_names = raw.get("artistNames")
    if isinstance(artist_names, list) and artist_names:
        artist = ", ".join(str(a).strip() for a in artist_names if str(a).strip())
    else:
        artist = str(raw.get("artist") or "Unknown Artist").strip()

    raw_dur = int(raw.get("duration") or 0)
    # Monochrome returns duration in milliseconds (e.g. 279208 -> 279s)
    duration = raw_dur // 1000 if raw_dur > 1500 else raw_dur

    artwork = str(raw.get("artwork") or "")
    isrc = str(raw.get("isrc") or "").strip().upper()
    raw_id = str(tid).strip()

    return {
        "id": f"mc:{raw_id}",
        "rawId": raw_id,
        "source": "monochrome",
        "title": str(raw.get("title") or "Untitled").strip(),
        "artist": artist or "Unknown Artist",
        "duration": duration,
        "coverSmall": artwork,
        "coverLarge": artwork,
        "explicit": bool(raw.get("explicit")),
        "isrc": isrc,
        "playable": True,
    }


def search_tracks(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    params = urllib.parse.urlencode({"q": q, "limit": str(limit)})
    url = f"{MONOCHROME_BASE}/search/tracks?{params}"

    for attempt in range(2):
        try:
            data = http_client.fetch_json(url, timeout=10.0)
            items = (data.get("tracks") if isinstance(data, dict) else None) or []
            return [t for t in (_normalize_track(x) for x in items if isinstance(x, dict)) if t]
        except urllib.error.HTTPError as exc:
            try:
                exc.close()
            except Exception:
                pass
            if exc.code == 429 and attempt == 0:
                time.sleep(2.0)
                continue
            return []
        except Exception:
            return []
    return []


def get_stream_url(raw_id: str) -> str:
    return f"{MONOCHROME_BASE}/track/{urllib.parse.quote(str(raw_id).strip())}"


def probe_track_stream(raw_id: str) -> Optional[Tuple[str, str]]:
    """Probe a Monochrome track stream URL with cold-cache tolerance and 1x 429 retry.

    Returns (final_url, content_type) where content_type is sniffed (e.g. 'audio/flac').
    """
    if not raw_id:
        return None
    url = get_stream_url(raw_id)
    hdrs = {"User-Agent": config.BROWSER_USER_AGENT}

    for attempt in range(2):
        ok, status, resp_hdrs, final_url = http_client.probe_url(
            url,
            headers=hdrs,
            timeout=config.MONOCHROME_PROBE_TIMEOUT,
            verify_tail=False,
            min_total_bytes=500_000,
            initial_range="bytes=0-1023",
        )
        if ok:
            ctype = resp_hdrs.get("content-type", "audio/flac").split(";")[0].strip().lower()
            if not ctype.startswith(("audio/", "video/mp4", "video/webm")):
                ctype = "audio/flac"
            return final_url, ctype
        if status == 429 and attempt == 0:
            time.sleep(2.0)
            continue
        break
    return None


def score_candidate(
    cand: Dict[str, Any],
    clean_title: str,
    primary_artist: str,
    full_artist: str,
    target_duration: int = 0,
    target_isrc: str = "",
    raw_target_title: str = "",
) -> float:
    """Score a Monochrome track candidate against target metadata; returns -1.0 if rejected."""
    if cand.get("playable") is False:
        return -1.0

    cand_title = str(cand.get("title") or "")
    cand_artist = str(cand.get("artist") or "")
    cand_dur = int(cand.get("duration") or 0)
    cand_isrc = str(cand.get("isrc") or "").strip().upper()
    norm_target_isrc = (target_isrc or "").strip().upper()

    # 1. Exact ISRC match -> studio master match
    if norm_target_isrc and cand_isrc and cand_isrc == norm_target_isrc:
        if target_duration > 30 and cand_dur > 0 and abs(cand_dur - target_duration) > 30:
            return -1.0
        return 1.0

    # 2. Hard Gate 1: Title similarity >= 0.55
    title_sim = max(
        lrc_parser.token_similarity(lrc_parser.clean_track_title(cand_title), clean_title),
        lrc_parser.token_similarity(cand_title, clean_title),
    )
    if title_sim < 0.55:
        return -1.0

    # 3. Hard Gate 2: Artist similarity >= 0.40 when target artist is known
    target_has_artist = bool(
        primary_artist and primary_artist.lower() not in ("unknown artist", "various artists")
    )
    artist_sim = max(
        lrc_parser.token_similarity(cand_artist, primary_artist),
        lrc_parser.token_similarity(cand_artist, full_artist or primary_artist),
    )
    norm_pa = lrc_parser.normalize_key(primary_artist)
    if norm_pa and len(norm_pa) >= 2:
        norm_ca = lrc_parser.normalize_key(cand_artist)
        norm_ct = lrc_parser.normalize_key(cand_title)
        if norm_pa in norm_ca or norm_pa in norm_ct:
            artist_sim = max(artist_sim, 0.55)

    if target_has_artist and artist_sim < 0.40:
        return -1.0

    # 4. Hard Gate 3: Duration within max(20s, 15% of target)
    if target_duration > 30 and cand_dur > 0:
        max_diff = max(20, int(target_duration * 0.15))
        if abs(cand_dur - target_duration) > max_diff:
            return -1.0

    if target_duration > 0 and cand_dur > 0:
        dur_prox = max(0.0, 1.0 - abs(cand_dur - target_duration) / 25.0)
    else:
        dur_prox = 0.65

    score = 0.55 * title_sim + 0.30 * artist_sim + 0.15 * dur_prox
    if target_duration > 0 and cand_dur > 0 and abs(cand_dur - target_duration) <= 4:
        score += 0.08

    target_context = f"{raw_target_title or clean_title} {full_artist or primary_artist}"
    score -= _noise_penalty(f"{cand_title} {cand_artist}", target_context)
    return score


def find_best_stream(
    title: str,
    artist: str,
    duration: int = 0,
    isrc: str = "",
    min_score: float = 0.65,
) -> Optional[Tuple[str, str, str]]:
    """Search Monochrome for a matching full-length lossless track and probe playability.

    Returns (stream_url, content_type, monochrome_track_id) or None.
    """
    clean_title = lrc_parser.clean_track_title(title)
    primary_artist = lrc_parser.extract_primary_artist(artist)
    query = f"{primary_artist} {clean_title}".strip()
    if not query:
        return None

    candidates = search_tracks(query, limit=8)
    scored: List[Tuple[float, Dict[str, Any]]] = []

    for cand in candidates:
        sc = score_candidate(
            cand,
            clean_title=clean_title,
            primary_artist=primary_artist,
            full_artist=artist,
            target_duration=duration,
            target_isrc=isrc,
            raw_target_title=title,
        )
        if sc >= min_score:
            scored.append((sc, cand))

    # Secondary title-only query if combined query yielded no match (e.g. transliterated artist credit)
    if not scored and clean_title and primary_artist:
        for cand in search_tracks(clean_title, limit=8):
            sc = score_candidate(
                cand,
                clean_title=clean_title,
                primary_artist=primary_artist,
                full_artist=artist,
                target_duration=duration,
                target_isrc=isrc,
                raw_target_title=title,
            )
            if sc >= min_score:
                scored.append((sc, cand))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    for _, cand in scored[:2]:
        raw_id = str(cand.get("rawId") or "")
        if not raw_id:
            continue
        probed = probe_track_stream(raw_id)
        if probed:
            return probed[0], probed[1], raw_id
    return None
