"""SoundCloud Public API v2 full-track provider with dynamic client_id discovery.

Strictly filters out 30-second Go+ 'SNIP' previews and unofficial remixes/speed-ups,
supporting both progressive MP3 streams and concurrent HLS fMP4 segment assembly.
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import threading
import time
import urllib.error
import urllib.parse
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from backend import config, http_client, lrc_parser

SC_HOME_URL = "https://soundcloud.com"
SC_API_BASE = "https://api-v2.soundcloud.com"
CLIENT_ID_TTL = 12 * 3600  # 12 hours

_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', re.IGNORECASE)
_CLIENT_ID_RE = re.compile(r'client_id["\']?\s*[:=]\s*["\']([a-zA-Z0-9_-]{30,36})["\']')
_EXT_MAP_RE = re.compile(r'#EXT-X-MAP:URI="([^"]+)"')

_NOISE_KEYWORDS = (
    "remix",
    "cover",
    "speed up",
    "sped up",
    "slowed",
    "house",
    "lak",
    "dj",
    "mashup",
    "bootleg",
    "tiktok",
    "tik tok",
    "karaoke",
    "beat",
    "hợp xướng",
    "hop xuong",
    "solo version",
    "piano",
    "live",
    "8d",
    "nightcore",
    "instrumental",
    "lofi",
    "lo-fi",
    "acoustic",
    "reverb",
)
_NOISE_PATTERNS = [
    (kw, re.compile(rf"(?<!\w){re.escape(kw)}(?!\w)", re.IGNORECASE))
    for kw in _NOISE_KEYWORDS
]

_CID_LOCK = threading.Lock()
_CACHED_CLIENT_ID: str = ""
_CACHED_CLIENT_ID_AT: float = 0.0

_HLS_LOCK = threading.Lock()
_HLS_CACHE: "OrderedDict[str, Tuple[bytes, str]]" = OrderedDict()
_MAX_HLS_CACHE_ITEMS = 8


def _noise_penalty(cand_text: str, target_text: str) -> float:
    penalty = 0.0
    for _, pat in _NOISE_PATTERNS:
        if pat.search(cand_text) and not pat.search(target_text):
            penalty += 0.45
    return penalty


def get_client_id(force_refresh: bool = False) -> Optional[str]:
    """Discover a working 32-char SoundCloud public client_id from web asset bundles."""
    global _CACHED_CLIENT_ID, _CACHED_CLIENT_ID_AT

    env_cid = os.environ.get("SOUNDCLOUD_CLIENT_ID", "").strip()
    if env_cid:
        return env_cid

    now = time.time()
    with _CID_LOCK:
        if not force_refresh and _CACHED_CLIENT_ID and (now - _CACHED_CLIENT_ID_AT) < CLIENT_ID_TTL:
            return _CACHED_CLIENT_ID

    try:
        html_text = http_client.fetch_text(SC_HOME_URL, timeout=config.PROVIDER_TIMEOUT)
        script_urls = _SCRIPT_SRC_RE.findall(html_text)
        for bundle_url in reversed(script_urls[-6:]):
            try:
                js_text = http_client.fetch_text(bundle_url, timeout=config.PROVIDER_TIMEOUT)
                m = _CLIENT_ID_RE.search(js_text)
                if m:
                    cid = m.group(1)
                    with _CID_LOCK:
                        _CACHED_CLIENT_ID = cid
                        _CACHED_CLIENT_ID_AT = time.time()
                    return cid
            except Exception:
                continue
    except Exception:
        pass
    return None


def search_raw_tracks(query: str, limit: int = 15) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    cid = get_client_id(force_refresh=False)
    if not cid:
        return []

    for attempt in range(2):
        params = urllib.parse.urlencode({"q": q, "client_id": cid, "limit": str(limit)})
        url = f"{SC_API_BASE}/search/tracks?{params}"
        try:
            data = http_client.fetch_json(url, timeout=config.PROVIDER_TIMEOUT)
            collection = data.get("collection") if isinstance(data, dict) else None
            return [x for x in (collection or []) if isinstance(x, dict)]
        except urllib.error.HTTPError as exc:
            try:
                exc.close()
            except Exception:
                pass
            if exc.code in (401, 403) and attempt == 0:
                cid = get_client_id(force_refresh=True) or ""
                if cid:
                    continue
            return []
        except Exception:
            return []
    return []


def is_full_allow_track(item: Dict[str, Any], target_duration: int = 0) -> bool:
    """Return True ONLY if the SoundCloud item is a full-length playable track (never a 30s Go+ SNIP preview)."""
    if str(item.get("policy") or "").upper() != "ALLOW":
        return False
    dur_ms = int(item.get("duration") or 0)
    full_dur_ms = int(item.get("full_duration") or dur_ms)
    if dur_ms < 90_000 or full_dur_ms < 90_000:
        return False
    if abs(dur_ms - full_dur_ms) > 5000:
        return False
    if target_duration > 30:
        dur_sec = dur_ms // 1000
        if abs(dur_sec - target_duration) > 20:
            return False
    return True


def score_candidate(
    item: Dict[str, Any],
    clean_title: str,
    primary_artist: str,
    full_artist: str,
    target_duration: int = 0,
    raw_target_title: str = "",
) -> float:
    """Score a SoundCloud track item; returns -1.0 if rejected by full-track or quality gates."""
    if not is_full_allow_track(item, target_duration=target_duration):
        return -1.0

    raw_title = str(item.get("title") or "").strip()
    user = item.get("user") or {}
    username = str(user.get("username") or user.get("full_name") or "").strip()
    pub_artist = str((item.get("publisher_metadata") or {}).get("artist") or "").strip()
    cand_artist = pub_artist or username

    # Many SoundCloud uploads format title as "Artist - Song Title", "Song Title - Artist", or "Artist | Song Title | Official MV"
    cleaned_raw = lrc_parser.clean_track_title(raw_title)
    stripped_title = cleaned_raw
    title_artist_sim = 0.0
    parts = [p.strip() for p in re.split(r"\s+(?:[-–—|])\s+", cleaned_raw) if p.strip()]
    if len(parts) >= 2:
        kept_parts: List[str] = []
        stripped_count = 0
        for part in parts:
            if re.match(
                r"^(?:official\s*(?:music\s*)?(?:video|audio|mv|lyric\s*video|visualizer)|mv|audio|lyrics?)$",
                part,
                re.IGNORECASE,
            ):
                stripped_count += 1
                continue
            art_s = max(
                lrc_parser.token_similarity(part, primary_artist),
                lrc_parser.token_similarity(part, full_artist or primary_artist),
            )
            if art_s >= 0.50 and stripped_count + 1 < len(parts):
                title_artist_sim = max(title_artist_sim, art_s)
                stripped_count += 1
            else:
                kept_parts.append(part)
        if kept_parts:
            stripped_title = " - ".join(kept_parts)

    title_sim = max(
        lrc_parser.token_similarity(lrc_parser.clean_track_title(stripped_title), clean_title),
        lrc_parser.token_similarity(lrc_parser.clean_track_title(raw_title), clean_title),
    )
    if title_sim < 0.65:
        return -1.0

    dur_sec = int(item.get("duration") or 0) // 1000
    target_context = f"{raw_target_title or clean_title} {full_artist or primary_artist}"
    noise_pen = _noise_penalty(f"{raw_title} {username}", target_context)

    target_has_artist = bool(
        primary_artist and primary_artist.lower() not in ("unknown artist", "various artists")
    )
    artist_sim = max(
        title_artist_sim,
        lrc_parser.token_similarity(cand_artist, primary_artist),
        lrc_parser.token_similarity(cand_artist, full_artist or primary_artist),
        lrc_parser.token_similarity(username, primary_artist),
    )
    norm_pa = lrc_parser.normalize_key(primary_artist)
    if norm_pa and len(norm_pa) >= 2:
        if norm_pa in lrc_parser.normalize_key(cand_artist) or norm_pa in lrc_parser.normalize_key(raw_title):
            artist_sim = max(artist_sim, 0.85)

    # Studio master match on SoundCloud when uploader username is a label/channel but title & duration match
    if (
        artist_sim < 0.45
        and title_sim >= 0.90
        and target_duration > 60
        and dur_sec > 0
        and abs(dur_sec - target_duration) <= 5
        and noise_pen == 0.0
    ):
        artist_sim = 0.55

    if target_has_artist and artist_sim < 0.45:
        return -1.0

    if target_duration > 0 and dur_sec > 0:
        dur_prox = max(0.0, 1.0 - abs(dur_sec - target_duration) / 20.0)
    else:
        dur_prox = 0.65

    score = 0.55 * title_sim + 0.30 * artist_sim + 0.15 * dur_prox - noise_pen
    return score


def parse_hls_m3u8_urls(m3u8_text: str, base_url: str = "") -> List[str]:
    """Extract ordered init (#EXT-X-MAP) + media segment URLs from an HLS playlist."""
    urls: List[str] = []
    for raw_line in (m3u8_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m_map = _EXT_MAP_RE.search(line)
        if m_map:
            u = urllib.parse.urljoin(base_url, m_map.group(1))
            urls.append(u)
        elif not line.startswith("#"):
            u = urllib.parse.urljoin(base_url, line)
            urls.append(u)
    return urls


def assemble_hls_segments(m3u8_url: str, cache_key: str = "", mime_hint: str = "audio/mp4") -> Optional[Tuple[bytes, str]]:
    """Download an HLS audio playlist's init map + segments concurrently into a single seekable buffer."""
    if cache_key:
        with _HLS_LOCK:
            cached = _HLS_CACHE.get(cache_key)
            if cached is not None:
                _HLS_CACHE.move_to_end(cache_key)
                return cached

    m3u8_text = http_client.fetch_text(m3u8_url, timeout=config.PROVIDER_TIMEOUT)
    seg_urls = parse_hls_m3u8_urls(m3u8_text, base_url=m3u8_url)
    if not seg_urls or len(seg_urls) > 400:
        return None

    def _fetch_seg(idx_and_url: Tuple[int, str]) -> Tuple[int, bytes]:
        idx, u = idx_and_url
        _, _, raw = http_client.fetch_bytes(u, timeout=config.PROVIDER_TIMEOUT)
        return idx, raw

    ordered: List[Optional[bytes]] = [None] * len(seg_urls)
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(_fetch_seg, (i, u)) for i, u in enumerate(seg_urls)]
        for fut in concurrent.futures.as_completed(futures):
            idx, chunk = fut.result()
            ordered[idx] = chunk

    if any(c is None or len(c) == 0 for c in ordered):
        return None

    full_bytes = b"".join(c for c in ordered if c)
    if len(full_bytes) < 200_000:
        return None

    sniffed = http_client.sniff_media_mime(full_bytes[:16], mime_hint)
    ctype = sniffed or mime_hint or "audio/mp4"

    if cache_key:
        with _HLS_LOCK:
            _HLS_CACHE[cache_key] = (full_bytes, ctype)
            while len(_HLS_CACHE) > _MAX_HLS_CACHE_ITEMS:
                _HLS_CACHE.popitem(last=False)

    return full_bytes, ctype


def _resolve_item_stream(item: Dict[str, Any], client_id: str) -> Optional[Dict[str, Any]]:
    transcodings = ((item.get("media") or {}).get("transcodings")) or []
    if not transcodings:
        return None

    track_auth = str(item.get("track_authorization") or "")
    sc_id = str(item.get("id") or "")

    def _fetch_transcoding_stream_url(tc: Dict[str, Any]) -> Optional[str]:
        t_url = str(tc.get("url") or "")
        if not t_url:
            return None
        sep = "&" if "?" in t_url else "?"
        params: Dict[str, str] = {"client_id": client_id}
        if track_auth:
            params["track_authorization"] = track_auth
        full_u = f"{t_url}{sep}{urllib.parse.urlencode(params)}"
        data = http_client.fetch_json(full_u, timeout=config.PROVIDER_TIMEOUT)
        return str((data or {}).get("url") or "") or None

    # 1. Prefer progressive direct HTTP stream (audio/mpeg) when available
    for tc in transcodings:
        fmt = tc.get("format") or {}
        if fmt.get("protocol") == "progressive":
            try:
                stream_url = _fetch_transcoding_stream_url(tc)
                if not stream_url:
                    continue
                ok, _, resp_hdrs, final_url = http_client.probe_url(
                    stream_url, timeout=config.STREAM_PROBE_TIMEOUT, min_total_bytes=400_000
                )
                if ok:
                    ctype = resp_hdrs.get("content-type", "audio/mpeg").split(";")[0].strip().lower()
                    if not ctype.startswith("audio/"):
                        ctype = "audio/mpeg"
                    return {"kind": "remote", "url": final_url, "content_type": ctype}
            except Exception:
                continue

    # 2. Fall back to HLS MP3 / fMP4 concurrent segment assembly
    hls_candidates: List[Tuple[int, Dict[str, Any]]] = []
    for tc in transcodings:
        fmt = tc.get("format") or {}
        if fmt.get("protocol") == "hls":
            mime = str(fmt.get("mime_type") or "").lower()
            if "mpeg" in mime or "mp3" in mime:
                pref = 3
            elif "mp4" in mime:
                pref = 2
            else:
                pref = 1
            hls_candidates.append((pref, tc))

    hls_candidates.sort(key=lambda x: x[0], reverse=True)
    for _, tc in hls_candidates:
        try:
            m3u8_url = _fetch_transcoding_stream_url(tc)
            if not m3u8_url:
                continue
            raw_mime = str((tc.get("format") or {}).get("mime_type") or "audio/mp4").split(";")[0].strip().lower()
            assembled = assemble_hls_segments(
                m3u8_url, cache_key=f"sc:{sc_id}", mime_hint=raw_mime
            )
            if assembled:
                return {
                    "kind": "local",
                    "bytes": assembled[0],
                    "content_type": assembled[1],
                }
        except Exception:
            continue

    return None


def find_best_stream(
    title: str,
    artist: str,
    duration: int = 0,
    min_score: float = 0.70,
) -> Optional[Dict[str, Any]]:
    """Find and resolve a full-length SoundCloud stream.

    Returns either:
      - {"kind": "remote", "url": str, "content_type": str}
      - {"kind": "local", "bytes": bytes, "content_type": str}
      or None.
    """
    clean_title = lrc_parser.clean_track_title(title)
    primary_artist = lrc_parser.extract_primary_artist(artist)
    query = f"{primary_artist} {clean_title}".strip()
    if not query:
        return None

    items = search_raw_tracks(query, limit=15)
    if not items:
        return None

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for item in items:
        sc = score_candidate(
            item,
            clean_title=clean_title,
            primary_artist=primary_artist,
            full_artist=artist,
            target_duration=duration,
            raw_target_title=title,
        )
        if sc >= min_score:
            scored.append((sc, item))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    cid = get_client_id(force_refresh=False) or ""
    if not cid:
        return None

    for _, item in scored[:2]:
        resolved = _resolve_item_stream(item, cid)
        if resolved:
            return resolved
    return None
