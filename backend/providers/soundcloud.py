"""SoundCloud Public API v2 full-track provider with dynamic client_id discovery.

Strictly filters out 30-second Go+ 'SNIP' previews while matching full-length 'ALLOW'
uploads across hyphenated/transliterated artists, optional-space separators, and
lazy on-demand HLS fMP4/MP3 concurrent segment assembly.
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
_SEP_SPLIT_RE = re.compile(r"\s*[-–—|/:]\s*")

_UPLOAD_JUNK_RE = re.compile(
    r"(?:\b(?:official\s*(?:music\s*)?(?:video|audio|mv|lyric\s*video|visualizer)|"
    r"bản\s*chuẩn|ban\s*chuan|full\s*hd|full\s*audio|full\s*track|free\s*download|"
    r"download|original\s*mix|visualizer|mv|audio|lyrics?)\b)",
    re.IGNORECASE,
)

_NOISE_KEYWORDS = (
    "remix",
    "cover",
    "speed up",
    "sped up",
    "slowed",
    "house",
    "vinahouse",
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
    (kw, re.compile(rf"(?<!\w){re.escape(lrc_parser.normalize_key(kw))}(?!\w)", re.IGNORECASE))
    for kw in _NOISE_KEYWORDS
]

_CID_LOCK = threading.Lock()
_CACHED_CLIENT_ID: str = ""
_CACHED_CLIENT_ID_AT: float = 0.0

_HLS_LOCK = threading.Lock()
_HLS_CACHE: "OrderedDict[str, Tuple[bytes, str]]" = OrderedDict()
_MAX_HLS_CACHE_ITEMS = 8


def _noise_penalty(cand_text: str, target_text: str) -> float:
    """Compute noise penalty on Unicode-normalized text (blocks Fraktur/fancy font bypasses)."""
    norm_c = lrc_parser.normalize_key(cand_text)
    norm_t = lrc_parser.normalize_key(target_text)
    penalty = 0.0
    for _, pat in _NOISE_PATTERNS:
        if pat.search(norm_c) and not pat.search(norm_t):
            penalty += 0.22
    return penalty


def get_client_id(force_refresh: bool = False) -> Optional[str]:
    """Discover a working SoundCloud public client_id from web asset bundles."""
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


def search_raw_tracks(query: str, limit: int = 25) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    cid = get_client_id(force_refresh=False)
    if not cid:
        return []

    for attempt in range(2):
        params = urllib.parse.urlencode(
            {"q": q, "client_id": cid, "limit": str(limit), "app_locale": "en"}
        )
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
        max_diff = max(140, int(target_duration * 0.75))
        if abs(dur_sec - target_duration) > max_diff:
            return False
    return True


def _strip_upload_junk(text: str) -> str:
    cleaned = _UPLOAD_JUNK_RE.sub("", text or "")
    cleaned = re.sub(r"[\[\]()]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" -–—|/:")


def _strip_artist_tokens(text: str, norm_pa: str, norm_pa_compact: str) -> str:
    """Remove artist name tokens from a combined title string like 'Come My Way Son Tung MTP'."""
    norm_text = lrc_parser.normalize_key(text)
    if not norm_text or not norm_pa:
        return text
    if norm_pa in norm_text:
        reduced = norm_text.replace(norm_pa, " ").strip()
        return re.sub(r"\s+", " ", reduced)
    if len(norm_pa_compact) >= 4:
        # Handle hyphen-collapsed forms like "son tung mtp" vs "son tung m tp"
        tokens = norm_text.split()
        artist_toks = set(norm_pa.split())
        # Also add merged adjacent pairs of artist tokens (e.g. "m" + "tp" -> "mtp")
        pa_list = norm_pa.split()
        for i in range(len(pa_list) - 1):
            artist_toks.add(pa_list[i] + pa_list[i + 1])
        remaining = [tok for tok in tokens if tok not in artist_toks]
        if remaining and len(remaining) < len(tokens):
            return " ".join(remaining)
    return text


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

    norm_pa = lrc_parser.normalize_key(primary_artist)
    norm_pa_compact = norm_pa.replace(" ", "")
    norm_full_compact = lrc_parser.normalize_key(full_artist or primary_artist).replace(" ", "")

    cleaned_raw = _strip_upload_junk(lrc_parser.clean_track_title(raw_title))
    stripped_title = cleaned_raw
    title_artist_sim = 0.0

    # Split on optional-space separators (handles 'Come My Way-Sơn Tùng MTP', 'Artist | Song | Official MV')
    parts = [_strip_upload_junk(p) for p in _SEP_SPLIT_RE.split(cleaned_raw) if _strip_upload_junk(p)]
    if len(parts) >= 2:
        kept_parts: List[str] = []
        stripped_count = 0
        for part in parts:
            norm_part = lrc_parser.normalize_key(part)
            norm_part_compact = norm_part.replace(" ", "")
            art_s = max(
                lrc_parser.token_similarity(part, primary_artist),
                lrc_parser.token_similarity(part, full_artist or primary_artist),
            )
            if len(norm_pa_compact) >= 3 and (
                norm_pa_compact in norm_part_compact or norm_part_compact in norm_pa_compact
            ):
                art_s = max(art_s, 0.88)
            if art_s >= 0.50 and stripped_count + 1 < len(parts):
                title_artist_sim = max(title_artist_sim, art_s)
                stripped_count += 1
            else:
                kept_parts.append(part)
        if kept_parts:
            stripped_title = " - ".join(kept_parts)

    de_artisted = _strip_artist_tokens(stripped_title, norm_pa, norm_pa_compact)

    title_sim = max(
        lrc_parser.token_similarity(lrc_parser.clean_track_title(stripped_title), clean_title),
        lrc_parser.token_similarity(de_artisted, clean_title),
        lrc_parser.token_similarity(cleaned_raw, clean_title),
        lrc_parser.token_similarity(lrc_parser.clean_track_title(raw_title), clean_title),
    )
    if title_sim < 0.58:
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
    norm_cand_art = lrc_parser.normalize_key(cand_artist)
    norm_raw_title = lrc_parser.normalize_key(raw_title)
    norm_user = lrc_parser.normalize_key(username)

    if norm_pa and len(norm_pa) >= 2:
        if norm_pa in norm_cand_art or norm_pa in norm_raw_title or norm_pa in norm_user:
            artist_sim = max(artist_sim, 0.88)
    if len(norm_pa_compact) >= 3:
        if (
            norm_pa_compact in norm_raw_title.replace(" ", "")
            or norm_pa_compact in norm_cand_art.replace(" ", "")
            or norm_pa_compact in norm_user.replace(" ", "")
        ):
            artist_sim = max(artist_sim, 0.88)
    if len(norm_full_compact) >= 4:
        if (
            norm_full_compact in norm_raw_title.replace(" ", "")
            or norm_full_compact in norm_cand_art.replace(" ", "")
        ):
            artist_sim = max(artist_sim, 0.88)

    # Studio master / reupload match when uploader username is a label/fan channel but title & duration match closely
    if (
        artist_sim < 0.40
        and title_sim >= 0.88
        and (
            target_duration <= 30
            or (dur_sec > 0 and abs(dur_sec - target_duration) <= 45)
        )
    ):
        artist_sim = 0.55

    if target_has_artist and artist_sim < 0.40:
        return -1.0

    if target_duration > 0 and dur_sec > 0:
        dur_prox = max(0.0, 1.0 - abs(dur_sec - target_duration) / 60.0)
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


def assemble_hls_segments(
    m3u8_url: str, cache_key: str = "", mime_hint: str = "audio/mp4"
) -> Optional[Tuple[bytes, str]]:
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


def _resolve_item_stream(
    item: Dict[str, Any], client_id: str, assemble_now: bool = True
) -> Optional[Dict[str, Any]]:
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

    # 1. Prefer progressive direct HTTP stream (audio/mpeg) when available (never snipped/preview)
    for tc in transcodings:
        if tc.get("snipped") is True:
            continue
        fmt = tc.get("format") or {}
        if fmt.get("protocol") == "progressive":
            try:
                stream_url = _fetch_transcoding_stream_url(tc)
                if not stream_url or "/preview/" in stream_url.lower():
                    continue
                ok, _, resp_hdrs, final_url = http_client.probe_url(
                    stream_url, timeout=config.STREAM_PROBE_TIMEOUT, min_total_bytes=400_000
                )
                if ok and "/preview/" not in final_url.lower():
                    ctype = resp_hdrs.get("content-type", "audio/mpeg").split(";")[0].strip().lower()
                    if not ctype.startswith("audio/"):
                        ctype = "audio/mpeg"
                    return {"kind": "remote", "url": final_url, "content_type": ctype}
            except Exception:
                continue

    # 2. Fall back to HLS MP3 / fMP4 (lazy m3u8 resolution during /info, assembled on playback)
    hls_candidates: List[Tuple[int, Dict[str, Any]]] = []
    for tc in transcodings:
        if tc.get("snipped") is True:
            continue
        fmt = tc.get("format") or {}
        if fmt.get("protocol") != "hls":
            continue
        mime = str(fmt.get("mime_type") or "").lower()
        preset = str(tc.get("preset") or "").lower()
        # Skip adaptive bitrate master playlists (abr_sq / audio/mpegurl) which return 404 or multi-variant manifests
        if "mpegurl" in mime or preset.startswith("abr"):
            continue
        if mime.startswith("audio/mpeg") or "mp3" in mime:
            pref = 4
        elif "mp4" in mime and "160" in preset:
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
            if not m3u8_url or not m3u8_url.startswith("http") or "/preview/" in m3u8_url.lower():
                continue
            raw_mime = str((tc.get("format") or {}).get("mime_type") or "audio/mp4").split(";")[0].strip().lower()
            cache_key = f"sc:{sc_id}" if sc_id else ""
            if not assemble_now:
                return {
                    "kind": "hls",
                    "m3u8_url": m3u8_url,
                    "cache_key": cache_key,
                    "content_type": raw_mime,
                }
            assembled = assemble_hls_segments(
                m3u8_url, cache_key=cache_key, mime_hint=raw_mime
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
    min_score: float = 0.60,
    assemble_now: bool = True,
    stats: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Find and resolve a full-length SoundCloud stream.

    Returns one of:
      - {"kind": "remote", "url": str, "content_type": str}
      - {"kind": "local", "bytes": bytes, "content_type": str}
      - {"kind": "hls", "m3u8_url": str, "cache_key": str, "content_type": str} (when assemble_now=False)
      or None.
    """
    clean_title = lrc_parser.clean_track_title(title)
    primary_artist = lrc_parser.extract_primary_artist(artist)
    ascii_artist = lrc_parser.normalize_key(primary_artist).strip()
    ascii_title = lrc_parser.normalize_key(clean_title).strip()

    primary_queries: List[str] = []
    for candidate_q in (
        f"{primary_artist} {clean_title}".strip(),
        f"{artist} {clean_title}".strip(),
        f"{ascii_artist} {ascii_title}".strip(),
    ):
        if candidate_q and all(candidate_q.lower() != q.lower() for q in primary_queries):
            primary_queries.append(candidate_q)

    fallback_query = (
        clean_title.strip()
        if len(clean_title.strip()) >= 4
        else f"{clean_title} {primary_artist} full".strip()
    )

    if not primary_queries and not fallback_query:
        return None

    scored: List[Tuple[float, Dict[str, Any]]] = []
    seen_ids: set[str] = set()
    total_items = 0
    snip_count = 0
    allow_count = 0
    queries_tried = 0
    transcode_failures = 0

    def _run_query(q: str) -> None:
        nonlocal total_items, snip_count, allow_count, queries_tried
        queries_tried += 1
        items = search_raw_tracks(q, limit=25)
        for item in items:
            iid = str(item.get("id") or "")
            if iid and iid in seen_ids:
                continue
            if iid:
                seen_ids.add(iid)
            total_items += 1
            policy = str(item.get("policy") or "").upper()
            if policy == "SNIP":
                snip_count += 1
            elif policy == "ALLOW":
                allow_count += 1
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

    for q in primary_queries:
        if queries_tried >= 3 or len(scored) >= 4:
            break
        _run_query(q)

    if (
        len(scored) < 3
        and queries_tried < 3
        and fallback_query
        and all(fallback_query.lower() != q.lower() for q in primary_queries)
    ):
        _run_query(fallback_query)

    if stats is not None:
        stats.update(
            {
                "total_items": total_items,
                "snip_count": snip_count,
                "allow_count": allow_count,
                "scored_count": len(scored),
                "queries_tried": queries_tried,
                "transcode_failures": 0,
            }
        )

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    cid = get_client_id(force_refresh=False) or ""
    if not cid:
        return None

    for _, item in scored[:6]:
        resolved = _resolve_item_stream(item, cid, assemble_now=assemble_now)
        if resolved:
            if stats is not None:
                stats["transcode_failures"] = transcode_failures
            return resolved
        transcode_failures += 1

    if stats is not None:
        stats["transcode_failures"] = transcode_failures
    return None
