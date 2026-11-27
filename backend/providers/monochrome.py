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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend import config, http_client, lrc_parser

MONOCHROME_BASE = "https://tracks.monochrome.st"
CACHE_DIR = Path(tempfile.gettempdir()) / "sonify_mc_cache"
MAX_CACHE_FILES = 12
MAX_CACHE_BYTES = 300 * 1024 * 1024  # 300 MiB disk cap on Render /tmp
MIN_TRACK_BYTES = 350_000
MAX_CONCURRENT_PRODUCERS = 2
READY_THRESHOLD_BYTES = 65_536       # Stream headers+bytes immediately after first 64 KiB
PRODUCER_BULK_CHUNK_SIZE = 262_144   # 256 KiB reads after initial 64 KiB ready chunk
PRODUCER_FLUSH_INTERVAL = 524_288    # Flush every 512 KiB for high throughput
PACE_INTERVAL_SEC = 0.6

is_valid_full_duration = lrc_parser.is_valid_full_duration

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

_ACTIVE_LOCK = threading.Lock()
_ACTIVE_DOWNLOADS: Dict[str, "ProgressiveCacheEntry"] = {}
RATE_LIMIT_COOLDOWN_SEC = 4.0
_RATE_LIMITED_UNTIL: float = 0.0


def is_rate_limited() -> bool:
    """Return True during the brief cooldown immediately following an upstream HTTP 429."""
    return time.monotonic() < _RATE_LIMITED_UNTIL


def _mark_rate_limited(cooldown: float = RATE_LIMIT_COOLDOWN_SEC) -> None:
    global _RATE_LIMITED_UNTIL
    _RATE_LIMITED_UNTIL = max(_RATE_LIMITED_UNTIL, time.monotonic() + max(0.0, cooldown))


def is_cached_on_disk(raw_id: str) -> bool:
    """Return True if a completed full-size Monochrome track is already cached on disk."""
    clean_id = str(raw_id or "").strip()
    if not clean_id:
        return False
    try:
        p = _safe_cache_path(clean_id)
        return p.is_file() and p.stat().st_size >= MIN_TRACK_BYTES
    except OSError:
        return False


@dataclass
class ProgressiveCacheEntry:
    """Single-flight progressive background download handle backed by /tmp/sonify_mc_cache."""

    clean_id: str
    part_path: Path
    final_path: Path
    cond: threading.Condition = field(default_factory=threading.Condition)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    started_at: float = field(default_factory=time.monotonic)
    bytes_written: int = 0
    total_size: int = 0          # upstream Content-Length if known (> 0)
    content_type: str = "audio/flac"
    done: bool = False
    failed: bool = False
    ready: bool = False          # True once bytes_written >= 65536 (or done & valid)

    @property
    def is_complete(self) -> bool:
        return self.done and not self.failed and self.final_path.is_file()

    def wait_until_ready(self, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + max(0.1, timeout)
        with self.cond:
            while not (self.ready or self.done):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.cond.wait(timeout=remaining)
            return self.ready and not self.failed

    def wait_until_done(self, timeout: float = 120.0) -> bool:
        deadline = time.monotonic() + max(0.1, timeout)
        with self.cond:
            while not self.done:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.cond.wait(timeout=remaining)
            return self.done and not self.failed


# Alias for external callers / spec naming
ProgressiveHandle = ProgressiveCacheEntry


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
    """Enforce both MAX_CACHE_FILES and MAX_CACHE_BYTES on completed *.bin files (never active *.part)."""
    try:
        if not CACHE_DIR.is_dir():
            return
        files: List[Tuple[float, int, Path]] = []
        for p in CACHE_DIR.glob("*.bin"):
            try:
                if p.is_file():
                    st = p.stat()
                    files.append((st.st_mtime, st.st_size, p))
            except OSError:
                continue
        files.sort(key=lambda item: item[0], reverse=True)

        kept_bytes = 0
        kept_count = 0
        for _, size, path in files:
            # Always keep at least the newest completed track even if a Hi-Res FLAC is large
            if kept_count == 0 or (
                kept_count < MAX_CACHE_FILES and (kept_bytes + size) <= MAX_CACHE_BYTES
            ):
                kept_count += 1
                kept_bytes += size
                continue
            try:
                path.unlink(missing_ok=True)
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
            data = http_client.fetch_json(url, timeout=5.0)
            items = (data.get("tracks") if isinstance(data, dict) else None) or []
            return [t for t in (_normalize_track(x) for x in items if isinstance(x, dict)) if t]
        except urllib.error.HTTPError as exc:
            try:
                exc.close()
            except Exception:
                pass
            if exc.code == 429 and attempt == 0:
                time.sleep(0.6)
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


def _read_cached_mime(clean_id: str, bin_path: Path) -> str:
    ctype = _MIME_CACHE.get(clean_id)
    if ctype:
        return ctype
    try:
        with bin_path.open("rb") as fh:
            head = fh.read(16)
        ctype = http_client.sniff_media_mime(head, "audio/flac") or "audio/flac"
    except OSError:
        ctype = "audio/flac"
    _MIME_CACHE[clean_id] = ctype
    return ctype


MAX_RESUME_ATTEMPTS = 8


def _parse_content_range_start(content_range: str) -> Optional[int]:
    """Extract start byte from 'bytes START-END/TOTAL', or None if absent/invalid."""
    raw = str(content_range or "").strip()
    if not raw.lower().startswith("bytes "):
        return None
    spec = raw[6:].split("/", 1)[0].strip()
    if "-" not in spec:
        return None
    start_str = spec.split("-", 1)[0].strip()
    return int(start_str) if start_str.isdigit() else None


def _run_producer(entry: ProgressiveCacheEntry) -> None:
    """Background producer thread: downloads tracks.monochrome.st/track/{id} once into .part -> .bin.

    Automatically resumes with HTTP Range: bytes={total_written}- if upstream closes early
    (e.g. edge transfer cap on large 30-80 MiB Hi-Res FLAC files) and NEVER promotes a
    truncated .part file to .bin when Content-Length is known.
    """
    url = get_stream_url(entry.clean_id)
    hdrs = {"User-Agent": config.BROWSER_USER_AGENT}
    backoffs = (1.0, 2.0, 3.5)
    succeeded = False

    try:
        entry.final_path.parent.mkdir(parents=True, exist_ok=True)
        total_written = 0
        known_total = 0
        header_ct = "audio/flac"
        head_bytes = bytearray()
        sniffed_ct = "audio/flac"
        sniffed_done = False
        is_ready_signaled = False

        initial_attempt = 0
        resume_attempt = 0
        zero_progress_resumes = 0

        while True:
            if entry.cancel_event.is_set():
                break
            resp = None
            is_resume = total_written > 0
            range_hdr = f"bytes={total_written}-" if is_resume else None
            written_this_leg = 0
            leg_error = False

            try:
                _pace_upstream_request()
                if entry.cancel_event.is_set():
                    break
                status, up_headers, resp = http_client.open_stream(
                    url,
                    headers=hdrs,
                    range_header=range_hdr,
                    method="GET",
                    timeout=max(45.0, float(config.STREAM_READ_TIMEOUT)),
                )
                if entry.cancel_event.is_set():
                    break
                if status not in (200, 206):
                    if status == 429:
                        if not is_resume and initial_attempt < 1:
                            time.sleep(0.75)
                            initial_attempt += 1
                            continue
                        if not is_resume:
                            _mark_rate_limited()
                        if is_resume and resume_attempt < MAX_RESUME_ATTEMPTS:
                            time.sleep(backoffs[min(resume_attempt, len(backoffs) - 1)])
                            resume_attempt += 1
                            continue
                    break

                skip_bytes = 0
                if not is_resume:
                    raw_cl = str(up_headers.get("content-length") or "").strip()
                    known_total = int(raw_cl) if raw_cl.isdigit() and int(raw_cl) > 0 else 0
                    header_ct = up_headers.get("content-type", "audio/flac")
                    with entry.cond:
                        entry.total_size = known_total
                        entry.bytes_written = 0
                        entry.ready = False
                else:
                    if status == 206:
                        cr_start = _parse_content_range_start(up_headers.get("content-range", ""))
                        if cr_start is not None:
                            if cr_start > total_written:
                                break
                            skip_bytes = total_written - cr_start
                    elif status == 200:
                        skip_bytes = total_written

                file_mode = "r+b" if (is_resume and entry.part_path.is_file()) else "wb"
                with entry.part_path.open(file_mode) as out_f:
                    if file_mode == "r+b":
                        out_f.seek(total_written)
                        out_f.truncate(total_written)

                    while True:
                        if entry.cancel_event.is_set():
                            break
                        read_size = (
                            PRODUCER_BULK_CHUNK_SIZE
                            if is_ready_signaled
                            else config.STREAM_CHUNK_SIZE
                        )
                        chunk = resp.read(read_size)
                        if not chunk or entry.cancel_event.is_set():
                            break
                        if skip_bytes > 0:
                            if len(chunk) <= skip_bytes:
                                skip_bytes -= len(chunk)
                                continue
                            chunk = chunk[skip_bytes:]
                            skip_bytes = 0
                        if len(head_bytes) < 16:
                            head_bytes.extend(chunk[: 16 - len(head_bytes)])
                        if not sniffed_done and (len(head_bytes) >= 12 or total_written == 0):
                            sniffed_ct = (
                                http_client.sniff_media_mime(bytes(head_bytes), header_ct)
                                or "audio/flac"
                            )
                            if len(head_bytes) >= 12:
                                sniffed_done = True
                        out_f.write(chunk)
                        out_f.flush()
                        total_written += len(chunk)
                        written_this_leg += len(chunk)

                        if not is_ready_signaled and total_written >= READY_THRESHOLD_BYTES:
                            is_ready_signaled = True

                        with entry.cond:
                            entry.bytes_written = total_written
                            entry.content_type = sniffed_ct
                            if is_ready_signaled:
                                entry.ready = True
                            entry.cond.notify_all()
            except urllib.error.HTTPError as exc:
                code = exc.code
                try:
                    exc.close()
                except Exception:
                    pass
                leg_error = True
                if not is_resume:
                    entry.part_path.unlink(missing_ok=True)
                    if code == 429 and initial_attempt < 1:
                        time.sleep(0.75)
                        initial_attempt += 1
                        continue
                    if code == 429:
                        _mark_rate_limited()
                    break
                if code == 429 and resume_attempt < MAX_RESUME_ATTEMPTS:
                    time.sleep(backoffs[min(resume_attempt, len(backoffs) - 1)])
                    resume_attempt += 1
                    continue
                break
            except Exception:
                leg_error = True
                if not is_resume and total_written == 0:
                    entry.part_path.unlink(missing_ok=True)
                    if initial_attempt < 1:
                        time.sleep(0.75)
                        initial_attempt += 1
                        continue
                    break
                # Mid-stream read error after partial data: try Range resume below
            finally:
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass

            # Check if download is complete or explicitly canceled
            if entry.cancel_event.is_set():
                break
            if known_total > 0 and total_written >= known_total:
                break
            if known_total == 0 and not leg_error:
                break

            # Partial transfer (early EOF or mid-stream socket error): resume with Range header
            if total_written > 0 and known_total > 0 and total_written < known_total:
                if written_this_leg == 0:
                    zero_progress_resumes += 1
                    if zero_progress_resumes >= 2:
                        break
                else:
                    zero_progress_resumes = 0
                if resume_attempt < MAX_RESUME_ATTEMPTS:
                    resume_attempt += 1
                    time.sleep(0.35 if not leg_error else 0.75)
                    continue
            break

        # Strict completeness check: NEVER cache a truncated or canceled track
        is_complete_size = (
            not entry.cancel_event.is_set()
            and total_written >= MIN_TRACK_BYTES
            and (known_total == 0 or total_written >= known_total)
            and entry.part_path.is_file()
        )
        if not is_complete_size:
            entry.part_path.unlink(missing_ok=True)
        else:
            final_ct = http_client.sniff_media_mime(bytes(head_bytes), header_ct) or sniffed_ct
            entry.part_path.replace(entry.final_path)
            _MIME_CACHE[entry.clean_id] = final_ct
            with entry.cond:
                entry.content_type = final_ct
                entry.bytes_written = total_written
                entry.total_size = total_written
                entry.ready = True
                entry.failed = False
                entry.done = True
                entry.cond.notify_all()
            succeeded = True
            _evict_lru_cache_files()
    finally:
        if not succeeded:
            try:
                entry.part_path.unlink(missing_ok=True)
            except OSError:
                pass
            with entry.cond:
                entry.failed = True
                entry.done = True
                entry.cond.notify_all()
        with _ACTIVE_LOCK:
            if _ACTIVE_DOWNLOADS.get(entry.clean_id) is entry:
                _ACTIVE_DOWNLOADS.pop(entry.clean_id, None)


def get_or_start_download(raw_id: str) -> Optional[ProgressiveCacheEntry]:
    """Return a completed or in-progress single-flight ProgressiveCacheEntry without blocking.

    - If already cached on disk: returns a completed handle immediately (<1ms).
    - If already downloading: returns the shared handle (guaranteed 1 upstream hit total).
    - Otherwise spawns a detached daemon producer thread so browser pauses/cancels never
      abort or duplicate the upstream Monochrome request.
    """
    clean_id = str(raw_id or "").strip()
    if not clean_id:
        return None

    final_path = _safe_cache_path(clean_id)

    # Lock-free fast path for already completed files on disk
    try:
        if final_path.is_file():
            st = final_path.stat()
            if st.st_size >= MIN_TRACK_BYTES:
                try:
                    os.utime(final_path, None)
                except OSError:
                    pass
                ctype = _read_cached_mime(clean_id, final_path)
                return ProgressiveCacheEntry(
                    clean_id=clean_id,
                    part_path=final_path,
                    final_path=final_path,
                    bytes_written=st.st_size,
                    total_size=st.st_size,
                    content_type=ctype,
                    done=True,
                    failed=False,
                    ready=True,
                )
    except OSError:
        pass

    with _ACTIVE_LOCK:
        # Re-check disk under lock in case a producer just finished and popped itself
        try:
            if final_path.is_file():
                st = final_path.stat()
                if st.st_size >= MIN_TRACK_BYTES:
                    try:
                        os.utime(final_path, None)
                    except OSError:
                        pass
                    ctype = _read_cached_mime(clean_id, final_path)
                    return ProgressiveCacheEntry(
                        clean_id=clean_id,
                        part_path=final_path,
                        final_path=final_path,
                        bytes_written=st.st_size,
                        total_size=st.st_size,
                        content_type=ctype,
                        done=True,
                        failed=False,
                        ready=True,
                    )
        except OSError:
            pass

        existing = _ACTIVE_DOWNLOADS.get(clean_id)
        if existing is not None and not existing.failed and not existing.cancel_event.is_set():
            return existing

        # Cap concurrent upstream .part downloads (MAX_CONCURRENT_PRODUCERS = 2):
        # abort oldest active download immediately so rapid clicking never saturates upstream!
        while len(_ACTIVE_DOWNLOADS) >= MAX_CONCURRENT_PRODUCERS:
            oldest_id, oldest_entry = min(
                _ACTIVE_DOWNLOADS.items(), key=lambda kv: kv[1].started_at
            )
            oldest_entry.cancel_event.set()
            _ACTIVE_DOWNLOADS.pop(oldest_id, None)

        part_name = f"{final_path.stem}.part.{os.getpid()}.{int(time.time() * 1000)}.{threading.get_ident()}"
        part_path = final_path.with_name(part_name)
        entry = ProgressiveCacheEntry(
            clean_id=clean_id,
            part_path=part_path,
            final_path=final_path,
        )
        _ACTIVE_DOWNLOADS[clean_id] = entry

    t = threading.Thread(
        target=_run_producer,
        args=(entry,),
        name=f"mc-dl-{clean_id[:16]}",
        daemon=True,
    )
    t.start()
    return entry


def start_background_cache(raw_id: str) -> Optional[ProgressiveCacheEntry]:
    """Non-blocking pre-warm helper invoked by /api/stream/{id}/info for Monochrome tracks."""
    return get_or_start_download(raw_id)


def ensure_track_cached(raw_id: str) -> Optional[Tuple[Path, str]]:
    """Backwards-compatible blocking wrapper around get_or_start_download()."""
    handle = get_or_start_download(raw_id)
    if not handle:
        return None
    handle.wait_until_done(timeout=120.0)
    if handle.failed or not handle.done or not handle.final_path.is_file():
        return None
    return handle.final_path, handle.content_type


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
    relaxed: bool = False,
) -> float:
    """Score a Monochrome track candidate against target metadata; returns -1.0 if rejected."""
    if cand.get("playable") is False:
        return -1.0

    cand_title = str(cand.get("title") or "")
    cand_artist = str(cand.get("artist") or "")
    cand_dur = int(cand.get("duration") or 0)
    cand_isrc = str(cand.get("isrc") or "").strip().upper()
    norm_target_isrc = (target_isrc or "").strip().upper()

    # Asymmetric duration & short-clip gate (blocks chopped/sped-up edits)
    if not lrc_parser.is_duration_compatible(
        cand_dur, target_duration, relaxed=relaxed, mv_capable=False
    ):
        return -1.0

    target_context = f"{raw_target_title or clean_title} {full_artist or primary_artist}"
    ver_class = lrc_parser.classify_unwanted_version(f"{cand_title} {cand_artist}", target_context)
    if ver_class == "hard_reject":
        return -1.0
    if not relaxed and ver_class != "ok":
        return -1.0

    # 1. Exact ISRC match -> studio master match
    if norm_target_isrc and cand_isrc and cand_isrc == norm_target_isrc:
        return 1.0

    # 2. Hard Gate 1: Title similarity (>= 0.55 strict, >= 0.60 relaxed)
    title_sim = max(
        lrc_parser.token_similarity(lrc_parser.clean_track_title(cand_title), clean_title),
        lrc_parser.token_similarity(cand_title, clean_title),
    )
    if title_sim < (0.60 if relaxed else 0.55):
        return -1.0

    # 3. Hard Gate 2: Artist similarity when target artist is known
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
        pad_pa = f" {norm_pa} "
        if (
            pad_pa in f" {norm_ca} "
            or pad_pa in f" {norm_ct} "
            or (lrc_parser._has_cjk(norm_pa) and (norm_pa in norm_ca or norm_pa in norm_ct))
            or (
                len(norm_pa_compact) >= 5
                and (norm_pa_compact in norm_ca.replace(" ", "") or norm_pa_compact in norm_ct.replace(" ", ""))
            )
        ):
            artist_sim = max(artist_sim, 0.55)

    if relaxed:
        if lrc_parser.passes_relaxed_artist_gate(
            artist_sim=artist_sim,
            primary_artist=primary_artist,
            haystack=f"{cand_title} {cand_artist}",
            clean_title=clean_title,
            title_sim=title_sim,
            cand_dur=cand_dur,
            target_duration=target_duration,
        ):
            artist_sim = max(artist_sim, 0.50)
        elif target_has_artist:
            return -1.0
    else:
        if target_has_artist and artist_sim < 0.40:
            return -1.0

    if target_duration > 0 and cand_dur > 0:
        denom = 90.0 if relaxed else 25.0
        floor_prox = 0.25 if relaxed else 0.0
        dur_prox = max(floor_prox, 1.0 - abs(cand_dur - target_duration) / denom)
    else:
        dur_prox = 0.65

    score = 0.55 * title_sim + 0.30 * artist_sim + 0.15 * dur_prox
    if target_duration > 0 and cand_dur > 0 and abs(cand_dur - target_duration) <= 4:
        score += 0.08

    if ver_class == "derivative":
        score -= 0.35
    elif ver_class == "live_acoustic":
        score -= 0.12
    return score


_SEARCH_SUFFIX_RE = re.compile(
    r"\s*[\(\[]\s*(?:with\s+[^)\]]+|from\s+[\"']?[^)\]]+)[\)\]]",
    re.IGNORECASE,
)


def _clean_search_title(title: str) -> str:
    stripped = _SEARCH_SUFFIX_RE.sub("", title or "")
    return lrc_parser.clean_track_title(stripped) or lrc_parser.clean_track_title(title)


def find_best_stream(
    title: str,
    artist: str,
    duration: int = 0,
    isrc: str = "",
    min_score: float = 0.65,
    relaxed: bool = False,
) -> Optional[Tuple[str, str, str]]:
    """Search Monochrome for a matching full-length lossless track WITHOUT probing /track/{id}.

    Zero-probe resolution prevents /info + <video> burst 429s; actual audio download
    happens once via single-flight progressive background cache when playback begins.
    Returns (stream_url, "audio/flac", monochrome_track_id) or None.
    """
    if not relaxed and is_rate_limited():
        return None
    clean_title = lrc_parser.clean_track_title(title)
    search_title = _clean_search_title(title)
    primary_artist = lrc_parser.extract_primary_artist(artist)
    primary_query = f"{primary_artist} {search_title}".strip()
    if not primary_query:
        return None

    eff_min_score = 0.48 if (relaxed and min_score == 0.65) else min_score

    queries: List[str] = [primary_query]
    ascii_q = lrc_parser.normalize_key(primary_query).strip()
    if ascii_q and ascii_q.lower() != primary_query.lower():
        queries.append(ascii_q)
    if relaxed:
        rq = f"{clean_title} {primary_artist}".strip()
        if rq and all(rq.lower() != q.lower() for q in queries):
            queries.append(rq)
    if search_title and len(search_title.strip()) >= (4 if relaxed else 4) and primary_artist:
        ct_q = search_title.strip()
        if all(ct_q.lower() != q.lower() for q in queries):
            queries.append(ct_q)

    scored: List[Tuple[float, Dict[str, Any]]] = []
    seen_ids: set[str] = set()

    for q in queries:
        for cand in search_tracks(q, limit=10):
            rid = str(cand.get("rawId") or "").strip()
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            sc = score_candidate(
                cand,
                clean_title=clean_title,
                primary_artist=primary_artist,
                full_artist=artist,
                target_duration=duration,
                target_isrc=isrc,
                raw_target_title=title,
                relaxed=relaxed,
            )
            if sc >= eff_min_score:
                scored.append((sc, cand))
        if scored:
            break

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    for _, cand in scored:
        raw_id = str(cand.get("rawId") or "").strip()
        if raw_id:
            return get_stream_url(raw_id), "audio/flac", raw_id
    return None
