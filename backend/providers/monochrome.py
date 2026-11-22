"""Monochrome Tracks Streamer provider (https://tracks.monochrome.st).

Keyless, $0, 100% ad-free CD-quality Lossless FLAC full-track audio streaming that
works reliably from both cloud datacenter IPs (Render/AWS) and residential networks.
Uses zero-probe /info resolution plus a single-flight /tmp LRU cache so every song
hits tracks.monochrome.st/track/{id} at most once across all browser Range requests.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend import config, http_client, lrc_parser

MONOCHROME_BASE = "https://tracks.monochrome.st"
CACHE_DIR = Path(tempfile.gettempdir()) / "sonify_mc_cache"
MAX_CACHE_FILES = 12
MIN_TRACK_BYTES = 200_000
PACE_INTERVAL_SEC = 1.0

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
    (kw, re.compile(rf"(?<!\w){re.escape(lrc_parser.normalize_key(kw))}(?!\w)", re.IGNORECASE))
    for kw in _NOISE_KEYWORDS
]

_LOCKS_GUARD = threading.Lock()
_TRACK_LOCKS: Dict[str, threading.Lock] = {}
_PACE_LOCK = threading.Lock()
_LAST_STREAM_START: float = 0.0
_MIME_CACHE: Dict[str, str] = {}


def _get_track_lock(raw_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _TRACK_LOCKS.get(raw_id)
        if lock is None:
            if len(_TRACK_LOCKS) > 256:
                for k in list(_TRACK_LOCKS.keys()):
                    if not _TRACK_LOCKS[k].locked():
                        _TRACK_LOCKS.pop(k, None)
                        if len(_TRACK_LOCKS) <= 128:
                            break
            lock = threading.Lock()
            _TRACK_LOCKS[raw_id] = lock
        return lock


def _pace_upstream_request() -> None:
    """Enforce >= 1.0s spacing between upstream /track/{id} downloads to avoid burst 429s."""
    global _LAST_STREAM_START
    with _PACE_LOCK:
        now = time.monotonic()
        wait = PACE_INTERVAL_SEC - (now - _LAST_STREAM_START)
        if wait > 0:
            time.sleep(wait)
        _LAST_STREAM_START = time.monotonic()


def _safe_cache_path(raw_id: str) -> Path:
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", str(raw_id).strip())
    return CACHE_DIR / f"{safe_name}.bin"


def _evict_lru_cache_files() -> None:
    try:
        if not CACHE_DIR.is_dir():
            return
        files = sorted(
            (p for p in CACHE_DIR.glob("*.bin") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in files[MAX_CACHE_FILES:]:
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def _noise_penalty(cand_text: str, target_text: str) -> float:
    norm_c = lrc_parser.normalize_key(cand_text)
    norm_t = lrc_parser.normalize_key(target_text)
    penalty = 0.0
    for _, pat in _NOISE_PATTERNS:
        if pat.search(norm_c) and not pat.search(norm_t):
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
                time.sleep(1.5)
                continue
            return []
        except Exception:
            return []
    return []


def get_stream_url(raw_id: str) -> str:
    return f"{MONOCHROME_BASE}/track/{urllib.parse.quote(str(raw_id).strip())}"


def probe_track_stream(raw_id: str) -> Optional[Tuple[str, str]]:
    """Probe a Monochrome track stream URL with cold-cache tolerance and 1x 429 retry.

    Note: find_best_stream() intentionally does NOT call this during /info to avoid
    triggering burst 429 rate limits; this helper remains available for explicit probes.
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


def ensure_track_cached(raw_id: str) -> Optional[Tuple[Path, str]]:
    """Download a Monochrome track at most once into bounded /tmp LRU cache (single-flight).

    All concurrent browser Range requests (bytes=0-, Chrome tail probes, seeks) share
    one single uninterrupted upstream download and then serve in <1ms from disk.
    """
    clean_id = str(raw_id or "").strip()
    if not clean_id:
        return None

    bin_path = _safe_cache_path(clean_id)

    # Fast path without waiting on download lock if already cached on disk
    try:
        if bin_path.is_file() and bin_path.stat().st_size >= MIN_TRACK_BYTES:
            try:
                os.utime(bin_path, None)
            except OSError:
                pass
            ctype = _MIME_CACHE.get(clean_id)
            if not ctype:
                with bin_path.open("rb") as fh:
                    head = fh.read(16)
                ctype = http_client.sniff_media_mime(head, "audio/flac") or "audio/flac"
                _MIME_CACHE[clean_id] = ctype
            return bin_path, ctype
    except OSError:
        pass

    track_lock = _get_track_lock(clean_id)
    with track_lock:
        # Re-check after acquiring per-track lock in case a concurrent request just finished
        try:
            if bin_path.is_file() and bin_path.stat().st_size >= MIN_TRACK_BYTES:
                try:
                    os.utime(bin_path, None)
                except OSError:
                    pass
                ctype = _MIME_CACHE.get(clean_id)
                if not ctype:
                    with bin_path.open("rb") as fh:
                        head = fh.read(16)
                    ctype = http_client.sniff_media_mime(head, "audio/flac") or "audio/flac"
                    _MIME_CACHE[clean_id] = ctype
                return bin_path, ctype
        except OSError:
            pass

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        url = get_stream_url(clean_id)
        hdrs = {"User-Agent": config.BROWSER_USER_AGENT}
        backoffs = (1.5, 3.0, 4.5)

        for attempt in range(4):
            resp = None
            tmp_path = bin_path.with_suffix(f".tmp.{os.getpid()}.{threading.get_ident()}")
            try:
                _pace_upstream_request()
                status, up_headers, resp = http_client.open_stream(
                    url,
                    headers=hdrs,
                    range_header=None,
                    method="GET",
                    timeout=max(35.0, float(config.STREAM_READ_TIMEOUT)),
                )
                if status not in (200, 206):
                    if status == 429 and attempt < len(backoffs):
                        time.sleep(backoffs[attempt])
                        continue
                    return None

                head_bytes = bytearray()
                total_written = 0
                with tmp_path.open("wb") as out_f:
                    while True:
                        chunk = resp.read(config.STREAM_CHUNK_SIZE)
                        if not chunk:
                            break
                        if len(head_bytes) < 16:
                            head_bytes.extend(chunk[: 16 - len(head_bytes)])
                        out_f.write(chunk)
                        total_written += len(chunk)

                if total_written < MIN_TRACK_BYTES:
                    tmp_path.unlink(missing_ok=True)
                    return None

                header_ct = up_headers.get("content-type", "audio/flac")
                ctype = http_client.sniff_media_mime(bytes(head_bytes), header_ct) or "audio/flac"
                tmp_path.replace(bin_path)
                _MIME_CACHE[clean_id] = ctype
                _evict_lru_cache_files()
                return bin_path, ctype
            except urllib.error.HTTPError as exc:
                code = exc.code
                try:
                    exc.close()
                except Exception:
                    pass
                tmp_path.unlink(missing_ok=True)
                if code == 429 and attempt < len(backoffs):
                    time.sleep(backoffs[attempt])
                    continue
                return None
            except Exception:
                tmp_path.unlink(missing_ok=True)
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return None
            finally:
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass

    return None


def fetch_track_bytes(raw_id: str) -> Optional[Tuple[bytes, str]]:
    """Fetch full Monochrome audio bytes via the single-flight /tmp LRU cache."""
    cached = ensure_track_cached(raw_id)
    if not cached:
        return None
    path, ctype = cached
    try:
        return path.read_bytes(), ctype
    except OSError:
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
        norm_pa_compact = norm_pa.replace(" ", "")
        if (
            norm_pa in norm_ca
            or norm_pa in norm_ct
            or (
                len(norm_pa_compact) >= 3
                and (norm_pa_compact in norm_ca.replace(" ", "") or norm_pa_compact in norm_ct.replace(" ", ""))
            )
        ):
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
    """Search Monochrome for a matching full-length lossless track WITHOUT probing /track/{id}.

    Zero-probe resolution prevents /info + <video> burst 429s; actual audio download
    happens once via single-flight ensure_track_cached() when playback begins.
    Returns (stream_url, "audio/flac", monochrome_track_id) or None.
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
    for _, cand in scored:
        raw_id = str(cand.get("rawId") or "").strip()
        if raw_id:
            return get_stream_url(raw_id), "audio/flac", raw_id
    return None
