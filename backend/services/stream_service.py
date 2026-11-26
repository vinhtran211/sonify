"""Multi-tier ad-free audio resolution waterfall and HTTP Range proxy service."""

from __future__ import annotations

import base64
import concurrent.futures
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from backend import config, http_client, offline_catalog
from backend.db import Database
from backend.providers import (
    apple_itunes,
    audius,
    bilibili,
    deezer,
    jiosaavn,
    monochrome,
    soundcloud,
    youtube_music,
)

_RANGE_RE = re.compile(r"^\s*bytes=(\d*)-(\d*)\s*$", re.IGNORECASE)
_YT_SOURCES = frozenset({"youtube-innertube", "yt-dlp", "piped", "invidious", "youtube"})
_VALID_STREAM_MIME_PREFIXES = ("audio/", "video/mp4", "video/webm")
PREVIEW_CACHE_TTL = 12.0
SOUNDCLOUD_HLS_CACHE_TTL = 240.0         # 4 min max TTL for CloudFront signed m3u8 playlists
TAIL_PASSTHROUGH_GAP = 512 * 1024        # 512 KiB ahead of sequential producer
TAIL_PASSTHROUGH_MAX_LEN = 2 * 1024 * 1024  # 2 MiB cap for direct tail/seek Range pass-through
MONOCHROME_PARALLEL_GRACE_SEC = 2.8      # Wait up to 2.8s for studio master FLAC before instant SC fallback

ALLOWED_STREAM_EXTENSIONS = frozenset(
    {".flac", ".m4a", ".mp4", ".mp3", ".wav", ".aac", ".ogg", ".webm"}
)


def normalize_stream_mime(content_type: str) -> str:
    """Normalize media MIME types for broad browser + iOS AVPlayer compatibility."""
    ctype = (content_type or "audio/mpeg").split(";", 1)[0].strip().lower()
    if ctype in ("audio/x-flac", "application/flac"):
        return "audio/flac"
    if ctype in ("audio/x-m4a", "audio/x-m4p", "audio/m4a"):
        return "audio/mp4"
    if ctype in ("audio/mp3", "audio/x-mpeg"):
        return "audio/mpeg"
    if ctype in ("audio/x-wav", "audio/wave"):
        return "audio/wav"
    return ctype or "audio/mpeg"


def mime_to_extension(content_type: str) -> str:
    """Map a normalized media MIME type to an AVPlayer-friendly filename extension."""
    ctype = normalize_stream_mime(content_type)
    if "flac" in ctype:
        return ".flac"
    if ctype.startswith("video/mp4"):
        return ".mp4"
    if "mp4" in ctype or "aac" in ctype:
        return ".m4a"
    if "mpeg" in ctype or "mp3" in ctype:
        return ".mp3"
    if "wav" in ctype:
        return ".wav"
    if "webm" in ctype:
        return ".webm"
    if "ogg" in ctype:
        return ".ogg"
    return ".m4a"


def strip_known_stream_extension(track_id: str) -> str:
    """Strip a trailing known media extension (e.g. '.flac', '.m4a') if present."""
    tid = (track_id or "").strip()
    if not tid or "." not in tid:
        return tid
    dot_idx = tid.rfind(".")
    suffix = tid[dot_idx:].lower()
    if dot_idx > 0 and suffix in ALLOWED_STREAM_EXTENSIONS:
        return tid[:dot_idx]
    return tid


class StreamUnavailableError(RuntimeError):
    """Raised when no real upstream stream can be resolved in online mode."""

    def __init__(
        self,
        message: str = "Unable to stream this track right now.",
        code: str = "STREAM_UNAVAILABLE",
        status: int = 502,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class ResolvedStream:
    track_id: str
    mode: str                 # "full" | "preview" | "offline-synth"
    source: str               # "monochrome" | "youtube-innertube" | "yt-dlp" | "piped" | "invidious" | "jiosaavn" | "soundcloud" | "audius" | "deezer-preview" | "itunes-preview" | "offline-synth"
    content_type: str
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    local_bytes: Optional[bytes] = None
    resolved_at: float = field(default_factory=time.time)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)


def _cloudfront_policy_expiry(url: str) -> Optional[float]:
    """Extract AWS:EpochTime expiry from a CloudFront signed URL's Policy query param, if present."""
    if not url or "Policy=" not in url:
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        qs = urllib.parse.parse_qs(parsed.query)
        raw_pol = (qs.get("Policy") or [""])[0]
        if not raw_pol:
            return None
        # CloudFront URL-safe base64 uses '-' -> '+', '_' -> '=', '~' -> '/'
        b64 = raw_pol.replace("-", "+").replace("_", "=").replace("~", "/")
        pad = (-len(b64)) % 4
        if pad:
            b64 += "=" * pad
        doc = json.loads(base64.b64decode(b64).decode("utf-8"))
        stmts = doc.get("Statement") if isinstance(doc, dict) else None
        if isinstance(stmts, list) and stmts:
            cond = (stmts[0] or {}).get("Condition") or {}
            dl = cond.get("DateLessThan") or {}
            epoch = dl.get("AWS:EpochTime")
            if epoch is not None:
                return float(epoch)
    except Exception:
        return None
    return None


def _extract_monochrome_id(resolved: ResolvedStream) -> Optional[str]:
    mc_prefix = monochrome.MONOCHROME_BASE + "/track/"
    if resolved.source == "monochrome" and resolved.url.startswith(mc_prefix):
        mc_id = urllib.parse.unquote(resolved.url[len(mc_prefix) :].split("?", 1)[0]).strip("/")
        return mc_id or None
    return None


def parse_byte_range(range_header: Optional[str], total_size: int) -> Tuple[Optional[Tuple[int, int]], bool]:
    """Parse an HTTP Range header against total_size.

    Returns:
      - (None, True) when no Range header is present (serve full 200 OK).
      - ((start, end), True) when valid (serve 206 Partial Content).
      - (None, False) when unsatisfiable/invalid (serve 416 Range Not Satisfiable).
    """
    if not range_header:
        return None, True
    m = _RANGE_RE.match(range_header)
    if not m or total_size <= 0:
        return None, False

    start_str, end_str = m.group(1), m.group(2)
    if start_str == "" and end_str == "":
        return None, False

    if start_str == "":
        # Suffix byte-range: bytes=-500 -> last 500 bytes
        suffix_len = int(end_str)
        if suffix_len <= 0:
            return None, False
        start = max(0, total_size - suffix_len)
        end = total_size - 1
    else:
        start = int(start_str)
        if start >= total_size:
            return None, False
        if end_str == "":
            end = total_size - 1
        else:
            end = min(int(end_str), total_size - 1)
            if end < start:
                return None, False

    return (start, end), True


class StreamService:
    """Resolves ad-free audio streams via cloud-aware waterfall and proxies with HTTP Range support."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache_lock = threading.Lock()
        self._cache: Dict[str, ResolvedStream] = {}
        self._locks_guard = threading.Lock()
        self._track_locks: Dict[str, threading.Lock] = {}

    def _get_track_lock(self, track_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._track_locks.get(track_id)
            if lock is None:
                if len(self._track_locks) > 512:
                    for k in list(self._track_locks.keys()):
                        if not self._track_locks[k].locked():
                            self._track_locks.pop(k, None)
                            if len(self._track_locks) <= 256:
                                break
                lock = threading.Lock()
                self._track_locks[track_id] = lock
            return lock

    def invalidate(self, track_id: str) -> None:
        with self._cache_lock:
            self._cache.pop(track_id, None)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    def _get_valid_cached(self, track_id: str, now: float) -> Optional[ResolvedStream]:
        cached = self._cache.get(track_id)
        if not cached:
            return None
        if cached.mode == "preview":
            ttl = PREVIEW_CACHE_TTL
        elif (
            cached.source == "soundcloud"
            and cached.local_bytes is None
            and cached.headers.get("X-SC-M3U8")
        ):
            m3u8_url = str(cached.headers.get("X-SC-M3U8") or "")
            cf_exp = _cloudfront_policy_expiry(m3u8_url)
            if cf_exp is not None and now >= (cf_exp - 60.0):
                return None
            ttl = SOUNDCLOUD_HLS_CACHE_TTL
        else:
            ttl = config.STREAM_CACHE_TTL
        if (now - cached.resolved_at) < ttl:
            return cached
        return None

    def _load_track_metadata(self, track_id: str) -> Optional[Dict[str, Any]]:
        track = self.db.get_track(track_id, include_extra=True)
        if track:
            return track

        prefix, _, raw_id = track_id.partition(":")
        if prefix == "local":
            lt = offline_catalog.get_offline_track(track_id)
            if lt:
                self.db.upsert_track(lt)
                return self.db.get_track(track_id, include_extra=True)
            return None

        if http_client.is_offline_mode():
            return None

        fetched = None
        try:
            if prefix == "dz" and raw_id:
                fetched = deezer.get_track(raw_id)
            elif prefix == "it" and raw_id:
                fetched = apple_itunes.get_track(raw_id)
            elif prefix == "au" and raw_id:
                fetched = audius.get_track(raw_id)
            elif prefix == "js" and raw_id:
                fetched = jiosaavn.get_song_details(raw_id)
            elif prefix == "yt" and raw_id:
                fetched = {
                    "id": track_id,
                    "source": "youtube_music",
                    "title": raw_id,
                    "artist": "YouTube Music",
                    "duration": 0,
                    "streamUrl": f"/api/stream/{track_id}",
                    "_extra": {"videoId": raw_id},
                }
            elif prefix == "mc" and raw_id:
                fetched = {
                    "id": track_id,
                    "source": "monochrome",
                    "title": raw_id,
                    "artist": "Monochrome",
                    "duration": 0,
                    "streamUrl": f"/api/stream/{track_id}",
                    "_extra": {"monochromeTrackId": raw_id},
                }
        except Exception:
            fetched = None

        if fetched:
            self.db.upsert_track(fetched)
            return self.db.get_track(track_id, include_extra=True)
        return None

    def _tier5_offline_wav(self, track_id: str, duration: int = 45) -> ResolvedStream:
        wav_data = offline_catalog.synthesize_track_wav(
            track_id,
            duration=min(max(duration, 30), 60) if track_id.startswith("local:") else 45,
        )
        return ResolvedStream(
            track_id=track_id,
            mode="offline-synth",
            source="offline-synth",
            content_type="audio/wav",
            local_bytes=wav_data,
        )

    def resolve_stream(
        self,
        track_id: str,
        force_refresh: bool = False,
        exclude_sources: Optional[Iterable[str]] = None,
        quality: str = "auto",
    ) -> ResolvedStream:
        excluded: Set[str] = set(exclude_sources or ())
        qual = (quality or "auto").strip().lower()
        if qual not in ("auto", "lossless", "fast"):
            qual = "auto"
        cache_key = track_id if qual == "auto" else f"{track_id}::{qual}"
        now = time.time()

        if not force_refresh and not excluded:
            with self._cache_lock:
                cached = self._get_valid_cached(cache_key, now)
                if cached:
                    return cached

        # Single-flight per track_id so concurrent /info + /stream share one resolution
        track_lock = self._get_track_lock(cache_key)
        with track_lock:
            now = time.time()
            if not force_refresh and not excluded:
                with self._cache_lock:
                    cached = self._get_valid_cached(cache_key, now)
                    if cached:
                        return cached

            resolved = self._resolve_stream_unlocked(
                track_id, excluded, force_refresh=force_refresh, quality=qual
            )
            if qual != "auto":
                # Store under both quality key and default track_id so subsequent /api/stream/{id}
                # serves the exact quality-selected stream cleanly.
                self._store_cache(resolved, cache_key=cache_key)
                self._store_cache(resolved, cache_key=track_id)
            return resolved

    def _try_resolve_monochrome(
        self,
        track_id: str,
        prefix: str,
        raw_id: str,
        track: Optional[Dict[str, Any]],
        title: str,
        artist: str,
        duration: int,
        extra: Dict[str, Any],
        excluded: Set[str],
        force_refresh: bool = False,
        store_cache: bool = True,
    ) -> Optional[ResolvedStream]:
        if "monochrome" in excluded or prefix == "mc":
            return None

        # Zero-probe fast path: cached monochromeTrackId returns immediately with 0 calls to /track/{id}
        cached_mc_id = str(extra.get("monochromeTrackId") or "").strip()
        if cached_mc_id and not force_refresh:
            resolved = ResolvedStream(
                track_id=track_id,
                mode="full",
                source="monochrome",
                content_type="audio/flac",
                url=monochrome.get_stream_url(cached_mc_id),
                headers={"User-Agent": config.BROWSER_USER_AGENT},
            )
            if store_cache:
                self._store_cache(resolved)
            return resolved

        if not title:
            return None

        # Use already-cached ISRC if present in SQLite; never burn 400-600ms on a blocking deezer.get_track() call.
        isrc = str(extra.get("isrc") or "").strip().upper()

        try:
            mc_match = monochrome.find_best_stream(
                title=title, artist=artist, duration=duration, isrc=isrc
            )
            if mc_match:
                stream_url, ctype, mc_id = mc_match
                if mc_id and track:
                    updates: Dict[str, Any] = {"monochromeTrackId": mc_id}
                    if isrc:
                        updates["isrc"] = isrc
                    self.db.update_track_extra(track_id, updates)
                resolved = ResolvedStream(
                    track_id=track_id,
                    mode="full",
                    source="monochrome",
                    content_type=ctype,
                    url=stream_url,
                    headers={"User-Agent": config.BROWSER_USER_AGENT},
                )
                if store_cache:
                    self._store_cache(resolved)
                return resolved
        except Exception:
            pass
        return None

    def _try_resolve_youtube(
        self,
        track_id: str,
        prefix: str,
        track: Optional[Dict[str, Any]],
        title: str,
        artist: str,
        duration: int,
        extra: Dict[str, Any],
        excluded: Set[str],
    ) -> Optional[ResolvedStream]:
        yt_excluded = bool(excluded & _YT_SOURCES)
        if prefix == "yt" or yt_excluded:
            return None

        cached_vid = str(extra.get("videoId") or "")
        cached_ver = int(extra.get("videoIdMatchVersion") or 0)
        video_id = cached_vid if cached_ver >= 2 else ""
        searched = False
        if not video_id and title:
            try:
                video_id = youtube_music.find_best_video_id(title, artist, duration) or ""
                searched = True
                if video_id and track:
                    self.db.update_track_extra(
                        track_id, {"videoId": video_id, "videoIdMatchVersion": 2}
                    )
            except Exception:
                video_id = ""
        if video_id:
            try:
                yt_res = youtube_music.resolve_audio_stream(video_id)
                if not yt_res and cached_vid and not searched and title:
                    fresh_vid = youtube_music.find_best_video_id(title, artist, duration) or ""
                    if fresh_vid and fresh_vid != cached_vid:
                        if track:
                            self.db.update_track_extra(
                                track_id, {"videoId": fresh_vid, "videoIdMatchVersion": 2}
                            )
                        yt_res = youtube_music.resolve_audio_stream(fresh_vid)
                if yt_res and yt_res[3] not in excluded:
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="full",
                        source=yt_res[3],
                        content_type=yt_res[1],
                        url=yt_res[0],
                        headers=yt_res[2],
                    )
                    self._store_cache(resolved)
                    return resolved
            except Exception:
                pass
        return None

    def _try_resolve_jiosaavn(
        self,
        track_id: str,
        title: str,
        artist: str,
        duration: int,
        excluded: Set[str],
        attempts: List[Dict[str, Any]],
    ) -> Optional[ResolvedStream]:
        if not title or "jiosaavn" in excluded:
            return None
        t0 = time.monotonic()
        try:
            js_match = jiosaavn.find_best_stream(title, artist, duration)
            ms = int((time.monotonic() - t0) * 1000)
            if js_match:
                attempts.append({"provider": "jiosaavn", "outcome": "ok", "ms": ms})
                resolved = ResolvedStream(
                    track_id=track_id,
                    mode="full",
                    source="jiosaavn",
                    content_type=js_match[1],
                    url=js_match[0],
                    headers={"User-Agent": config.BROWSER_USER_AGENT},
                    diagnostics=list(attempts),
                )
                self._store_cache(resolved)
                return resolved
            attempts.append({"provider": "jiosaavn", "outcome": "no_match", "ms": ms})
        except Exception:
            attempts.append(
                {"provider": "jiosaavn", "outcome": "error", "ms": int((time.monotonic() - t0) * 1000)}
            )
        return None

    def _try_resolve_soundcloud(
        self,
        track_id: str,
        title: str,
        artist: str,
        duration: int,
        excluded: Set[str],
        attempts: List[Dict[str, Any]],
        store_cache: bool = True,
    ) -> Optional[ResolvedStream]:
        if not title or "soundcloud" in excluded:
            return None
        t0 = time.monotonic()
        sc_stats: Dict[str, Any] = {}
        try:
            sc_match = soundcloud.find_best_stream(
                title, artist, duration, assemble_now=False, stats=sc_stats
            )
            ms = int((time.monotonic() - t0) * 1000)
            detail_parts = []
            if sc_stats:
                detail_parts.append(f"total={sc_stats.get('total_items', 0)}")
                detail_parts.append(f"snip={sc_stats.get('snip_count', 0)}")
                detail_parts.append(f"scored={sc_stats.get('scored_count', 0)}")
                if sc_stats.get("transcode_failures"):
                    detail_parts.append(f"tc_fail={sc_stats['transcode_failures']}")
                if sc_stats.get("http_errors"):
                    detail_parts.append(f"http={'/'.join(str(c) for c in sc_stats['http_errors'])}")
            detail = ",".join(detail_parts)
            if sc_match:
                entry: Dict[str, Any] = {"provider": "soundcloud", "outcome": "ok", "ms": ms}
                if detail:
                    entry["detail"] = detail
                attempts.append(entry)
                kind = sc_match.get("kind")
                if kind == "local" and sc_match.get("bytes"):
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="full",
                        source="soundcloud",
                        content_type=str(sc_match.get("content_type") or "audio/mp4"),
                        local_bytes=sc_match["bytes"],
                        diagnostics=list(attempts),
                    )
                elif kind == "hls" and sc_match.get("m3u8_url"):
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="full",
                        source="soundcloud",
                        content_type=str(sc_match.get("content_type") or "audio/mpeg"),
                        url=str(sc_match["m3u8_url"]),
                        headers={
                            "User-Agent": config.BROWSER_USER_AGENT,
                            "X-SC-M3U8": str(sc_match["m3u8_url"]),
                            "X-SC-Cache-Key": str(sc_match.get("cache_key") or ""),
                        },
                        diagnostics=list(attempts),
                    )
                else:
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="full",
                        source="soundcloud",
                        content_type=str(sc_match.get("content_type") or "audio/mpeg"),
                        url=str(sc_match.get("url") or ""),
                        headers={"User-Agent": config.BROWSER_USER_AGENT},
                        diagnostics=list(attempts),
                    )
                if store_cache:
                    self._store_cache(resolved)
                return resolved

            outcome = (
                "no_allow"
                if sc_stats.get("total_items", 0) > 0 and sc_stats.get("allow_count", 0) == 0
                else ("transcode_failed" if sc_stats.get("scored_count", 0) > 0 else "no_match")
            )
            entry = {"provider": "soundcloud", "outcome": outcome, "ms": ms}
            if detail:
                entry["detail"] = detail
            attempts.append(entry)
        except Exception:
            attempts.append(
                {"provider": "soundcloud", "outcome": "error", "ms": int((time.monotonic() - t0) * 1000)}
            )
        return None

    def _try_resolve_bilibili(
        self,
        track_id: str,
        title: str,
        artist: str,
        duration: int,
        excluded: Set[str],
        attempts: List[Dict[str, Any]],
    ) -> Optional[ResolvedStream]:
        if not title or "bilibili" in excluded:
            return None
        t0 = time.monotonic()
        try:
            bili_match = bilibili.find_best_stream(title, artist, duration)
            ms = int((time.monotonic() - t0) * 1000)
            if bili_match:
                attempts.append({"provider": "bilibili", "outcome": "ok", "ms": ms})
                resolved = ResolvedStream(
                    track_id=track_id,
                    mode="full",
                    source="bilibili",
                    content_type=bili_match[1],
                    url=bili_match[0],
                    headers=dict(bili_match[2]),
                    diagnostics=list(attempts),
                )
                self._store_cache(resolved)
                return resolved
            attempts.append({"provider": "bilibili", "outcome": "no_match", "ms": ms})
        except Exception:
            attempts.append(
                {"provider": "bilibili", "outcome": "error", "ms": int((time.monotonic() - t0) * 1000)}
            )
        return None

    def _try_resolve_audius(
        self,
        track_id: str,
        title: str,
        artist: str,
        duration: int,
        excluded: Set[str],
        attempts: List[Dict[str, Any]],
    ) -> Optional[ResolvedStream]:
        if not title or "audius" in excluded:
            return None
        t0 = time.monotonic()
        try:
            au_match = audius.find_best_stream(title, artist, duration)
            ms = int((time.monotonic() - t0) * 1000)
            if au_match:
                attempts.append({"provider": "audius", "outcome": "ok", "ms": ms})
                resolved = ResolvedStream(
                    track_id=track_id,
                    mode="full",
                    source="audius",
                    content_type=au_match[1],
                    url=au_match[0],
                    headers={"User-Agent": config.BROWSER_USER_AGENT},
                    diagnostics=list(attempts),
                )
                self._store_cache(resolved)
                return resolved
            attempts.append({"provider": "audius", "outcome": "no_match", "ms": ms})
        except Exception:
            attempts.append(
                {"provider": "audius", "outcome": "error", "ms": int((time.monotonic() - t0) * 1000)}
            )
        return None

    @staticmethod
    def _log_preview_fallback(
        track_id: str,
        title: str,
        artist: str,
        resolved: ResolvedStream,
    ) -> None:
        parts = []
        for a in resolved.diagnostics:
            prov = a.get("provider", "?")
            out = a.get("outcome", "?")
            ms = a.get("ms", 0)
            detail = a.get("detail")
            if detail:
                parts.append(f"{prov}:{out}({detail},{ms}ms)")
            else:
                parts.append(f"{prov}:{out}({ms}ms)")
        trace = ",".join(parts)
        try:
            print(
                f'[stream-fallback] track={track_id} title="{title}" artist="{artist}" '
                f"mode={resolved.mode} source={resolved.source} trace={trace}",
                file=sys.stderr,
                flush=True,
            )
        except Exception:
            pass

    def _resolve_stream_unlocked(
        self,
        track_id: str,
        excluded: Set[str],
        force_refresh: bool = False,
        quality: str = "auto",
    ) -> ResolvedStream:
        prefix, _, raw_id = track_id.partition(":")
        track = self._load_track_metadata(track_id)
        extra = (track or {}).get("_extra") or {}
        title = (track or {}).get("title") or ""
        artist = (track or {}).get("artist") or ""
        duration = int((track or {}).get("duration") or 0)
        preview_url = (track or {}).get("previewUrl") or ""
        attempts: List[Dict[str, Any]] = []

        # Offline synthesized WAV is STRICTLY limited to local:* demo tracks or explicit offline mode
        if prefix == "local" or http_client.is_offline_mode():
            resolved = self._tier5_offline_wav(track_id, duration=duration or 45)
            resolved.diagnostics = [{"provider": "offline-synth", "outcome": "ok", "ms": 0}]
            self._store_cache(resolved)
            return resolved

        yt_excluded = bool(excluded & _YT_SOURCES)

        # ------------------------------------------------------------------
        # Tier 0: Native full-length streams (Audius / JioSaavn / YouTube / Monochrome)
        # ------------------------------------------------------------------
        if prefix == "au" and raw_id and "audius" not in excluded:
            t0 = time.monotonic()
            try:
                au_url = audius.get_stream_url(raw_id)
                ok, _, hdrs, final_url = http_client.probe_url(
                    au_url, timeout=config.STREAM_PROBE_TIMEOUT
                )
                if ok:
                    ctype = hdrs.get("content-type", "audio/mpeg").split(";")[0].strip()
                    if not ctype.startswith("audio/"):
                        ctype = "audio/mpeg"
                    attempts.append({"provider": "audius-native", "outcome": "ok", "ms": int((time.monotonic() - t0) * 1000)})
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="full",
                        source="audius",
                        content_type=ctype,
                        url=final_url,
                        headers={"User-Agent": config.BROWSER_USER_AGENT},
                        diagnostics=list(attempts),
                    )
                    self._store_cache(resolved)
                    return resolved
            except Exception:
                pass
            attempts.append({"provider": "audius-native", "outcome": "failed", "ms": int((time.monotonic() - t0) * 1000)})

        if prefix == "js" and raw_id and "jiosaavn" not in excluded:
            t0 = time.monotonic()
            try:
                if not extra.get("encrypted_media_url") and not extra.get("direct_urls"):
                    details = jiosaavn.get_song_details(raw_id)
                    if details and details.get("_extra"):
                        extra = details["_extra"]
                        self.db.update_track_extra(track_id, extra)
                cand_urls = jiosaavn.resolve_candidate_urls(extra)
                found = jiosaavn.probe_best_url(cand_urls)
                if found:
                    attempts.append({"provider": "jiosaavn-native", "outcome": "ok", "ms": int((time.monotonic() - t0) * 1000)})
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="full",
                        source="jiosaavn",
                        content_type=found[1],
                        url=found[0],
                        headers={"User-Agent": config.BROWSER_USER_AGENT},
                        diagnostics=list(attempts),
                    )
                    self._store_cache(resolved)
                    return resolved
            except Exception:
                pass
            attempts.append({"provider": "jiosaavn-native", "outcome": "failed", "ms": int((time.monotonic() - t0) * 1000)})

        if prefix == "yt" and raw_id and not yt_excluded:
            t0 = time.monotonic()
            try:
                yt_res = youtube_music.resolve_audio_stream(raw_id)
                if yt_res and yt_res[3] not in excluded:
                    attempts.append({"provider": yt_res[3], "outcome": "ok", "ms": int((time.monotonic() - t0) * 1000)})
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="full",
                        source=yt_res[3],
                        content_type=yt_res[1],
                        url=yt_res[0],
                        headers=yt_res[2],
                        diagnostics=list(attempts),
                    )
                    self._store_cache(resolved)
                    return resolved
            except Exception:
                pass
            attempts.append({"provider": "youtube-native", "outcome": "failed", "ms": int((time.monotonic() - t0) * 1000)})

        if prefix == "mc" and raw_id and "monochrome" not in excluded:
            attempts.append({"provider": "monochrome-native", "outcome": "ok", "ms": 0})
            resolved = ResolvedStream(
                track_id=track_id,
                mode="full",
                source="monochrome",
                content_type="audio/flac",
                url=monochrome.get_stream_url(raw_id),
                headers={"User-Agent": config.BROWSER_USER_AGENT},
                diagnostics=list(attempts),
            )
            self._store_cache(resolved)
            return resolved

        # ------------------------------------------------------------------
        # Environment-Aware Primary Full-Track Waterfall:
        # - On Cloud (Render/AWS datacenter IP):
        #     * Fast-path cached monochromeTrackId (0ms)
        #     * When no YouTube credentials: parallel race Monochrome + SoundCloud
        #       (Monochrome studio master gets 2.8s priority grace; instant SoundCloud win on miss!)
        #       -> JioSaavn -> Audius -> Preview
        #     * When YouTube credentials set: Monochrome -> YouTube -> SoundCloud -> JioSaavn -> Audius -> Preview
        # - On Local / Residential IP:
        #     YouTube ANDROID itag=18 -> Monochrome -> JioSaavn -> SoundCloud -> Audius -> Preview
        # ------------------------------------------------------------------
        if config.is_cloud_env():
            mc_allowed = "monochrome" not in excluded and prefix != "mc"
            sc_allowed = bool(title) and "soundcloud" not in excluded
            has_yt_creds = youtube_music.has_cloud_youtube_credentials()

            # Zero-probe cached monochromeTrackId fast-path (0 network calls, 0ms) — skip when quality=="fast"
            cached_mc_id = str(extra.get("monochromeTrackId") or "").strip()
            if mc_allowed and cached_mc_id and not force_refresh and quality != "fast":
                mc_resolved = self._try_resolve_monochrome(
                    track_id, prefix, raw_id, track, title, artist, duration, extra, excluded, force_refresh=False
                )
                if mc_resolved:
                    attempts.append({"provider": "monochrome", "outcome": "ok", "ms": 0, "detail": "cached_id"})
                    mc_resolved.diagnostics = list(attempts)
                    return mc_resolved

            if mc_allowed and sc_allowed and not has_yt_creds:
                # Parallel race: Monochrome + SoundCloud concurrently (eliminates 2.6-4.2s serial miss tax!)
                if not yt_excluded and prefix != "yt":
                    attempts.append(
                        {"provider": "youtube", "outcome": "skipped_cloud_no_credentials", "ms": 0}
                    )

                def _race_mc():
                    t_start = time.monotonic()
                    res = self._try_resolve_monochrome(
                        track_id, prefix, raw_id, track, title, artist, duration, extra, excluded,
                        force_refresh=force_refresh, store_cache=False,
                    )
                    return res, int((time.monotonic() - t_start) * 1000)

                def _race_sc():
                    t_start = time.monotonic()
                    local_attempts: List[Dict[str, Any]] = []
                    res = self._try_resolve_soundcloud(
                        track_id, title, artist, duration, excluded, local_attempts, store_cache=False
                    )
                    return res, int((time.monotonic() - t_start) * 1000), local_attempts

                pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
                f_mc = pool.submit(_race_mc)
                f_sc = pool.submit(_race_sc)
                try:
                    if quality == "fast":
                        # Fast Standard preference: take SoundCloud compact stream immediately when it matches
                        sc_res: Optional[ResolvedStream] = None
                        sc_local_attempts: List[Dict[str, Any]] = []
                        try:
                            sc_res, _, sc_local_attempts = f_sc.result(timeout=3.5)
                        except Exception:
                            sc_res = None
                        for entry in sc_local_attempts:
                            entry["parallel"] = True
                            attempts.append(entry)
                        if sc_res is not None:
                            sc_res.diagnostics = list(attempts)
                            self._store_cache(sc_res)
                            return sc_res

                        mc_res_fast: Optional[ResolvedStream] = None
                        mc_fast_ms = 0
                        try:
                            mc_res_fast, mc_fast_ms = f_mc.result(timeout=4.0)
                        except Exception:
                            mc_res_fast = None
                        if mc_res_fast is not None:
                            attempts.append(
                                {"provider": "monochrome", "outcome": "ok", "ms": mc_fast_ms, "parallel": True}
                            )
                            mc_res_fast.diagnostics = list(attempts)
                            self._store_cache(mc_res_fast)
                            return mc_res_fast
                        attempts.append(
                            {"provider": "monochrome", "outcome": "no_match", "ms": mc_fast_ms, "parallel": True}
                        )
                    else:
                        mc_grace = 4.5 if quality == "lossless" else MONOCHROME_PARALLEL_GRACE_SEC
                        mc_res: Optional[ResolvedStream] = None
                        mc_ms = 0
                        mc_timed_out = False
                        try:
                            mc_res, mc_ms = f_mc.result(timeout=mc_grace)
                        except concurrent.futures.TimeoutError:
                            mc_timed_out = True
                            mc_ms = int(mc_grace * 1000)
                        except Exception:
                            mc_res = None

                        if mc_res is not None:
                            attempts.append(
                                {"provider": "monochrome", "outcome": "ok", "ms": mc_ms, "parallel": True}
                            )
                            mc_res.diagnostics = list(attempts)
                            self._store_cache(mc_res)
                            return mc_res

                        attempts.append(
                            {
                                "provider": "monochrome",
                                "outcome": "timeout" if mc_timed_out else "no_match",
                                "ms": mc_ms,
                                "parallel": True,
                            }
                        )

                        # SoundCloud has already been running in parallel — take its result immediately!
                        sc_res = None
                        sc_local_attempts = []
                        try:
                            sc_res, _, sc_local_attempts = f_sc.result(timeout=8.0)
                        except Exception:
                            sc_res = None

                        for entry in sc_local_attempts:
                            entry["parallel"] = True
                            attempts.append(entry)

                        if sc_res is not None:
                            sc_res.diagnostics = list(attempts)
                            self._store_cache(sc_res)
                            return sc_res

                        # If Monochrome timed out (and SoundCloud missed), wait up to 3.0s for Monochrome
                        # to finish rather than prematurely dropping to a 30s preview!
                        if mc_timed_out:
                            try:
                                late_mc, late_ms = f_mc.result(timeout=3.0)
                                if late_mc is not None:
                                    attempts.append(
                                        {"provider": "monochrome", "outcome": "ok", "ms": late_ms, "parallel": True}
                                    )
                                    late_mc.diagnostics = list(attempts)
                                    self._store_cache(late_mc)
                                    return late_mc
                            except Exception:
                                pass
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)
            else:
                # Serial cloud path (when YouTube creds are enabled, or one of Monochrome/SoundCloud is excluded)
                if mc_allowed:
                    t0 = time.monotonic()
                    mc_resolved = self._try_resolve_monochrome(
                        track_id, prefix, raw_id, track, title, artist, duration, extra, excluded, force_refresh=force_refresh
                    )
                    ms = int((time.monotonic() - t0) * 1000)
                    if mc_resolved:
                        attempts.append({"provider": "monochrome", "outcome": "ok", "ms": ms})
                        mc_resolved.diagnostics = list(attempts)
                        return mc_resolved
                    attempts.append({"provider": "monochrome", "outcome": "no_match", "ms": ms})

                if not yt_excluded and prefix != "yt":
                    if has_yt_creds:
                        t0 = time.monotonic()
                        yt_resolved = self._try_resolve_youtube(
                            track_id, prefix, track, title, artist, duration, extra, excluded
                        )
                        ms = int((time.monotonic() - t0) * 1000)
                        if yt_resolved:
                            attempts.append({"provider": yt_resolved.source, "outcome": "ok", "ms": ms})
                            yt_resolved.diagnostics = list(attempts)
                            return yt_resolved
                        attempts.append({"provider": "youtube", "outcome": "no_match", "ms": ms})
                    else:
                        attempts.append(
                            {"provider": "youtube", "outcome": "skipped_cloud_no_credentials", "ms": 0}
                        )

                if sc_allowed:
                    sc_resolved = self._try_resolve_soundcloud(
                        track_id, title, artist, duration, excluded, attempts
                    )
                    if sc_resolved:
                        return sc_resolved

            # Tier 4 (Cloud): JioSaavn 320kbps
            js_resolved = self._try_resolve_jiosaavn(
                track_id, title, artist, duration, excluded, attempts
            )
            if js_resolved:
                return js_resolved

            # Tier 4.5 (Cloud): Bilibili DASH Audio (cloud-safe, proxied with Referer)
            bili_resolved = self._try_resolve_bilibili(
                track_id, title, artist, duration, excluded, attempts
            )
            if bili_resolved:
                return bili_resolved

            # Tier 5 (Cloud): Audius
            au_resolved = self._try_resolve_audius(
                track_id, title, artist, duration, excluded, attempts
            )
            if au_resolved:
                return au_resolved
        else:
            # Tier 1 (Local): Fast YouTube ANDROID itag=18
            if not yt_excluded and prefix != "yt":
                t0 = time.monotonic()
                yt_resolved = self._try_resolve_youtube(
                    track_id, prefix, track, title, artist, duration, extra, excluded
                )
                ms = int((time.monotonic() - t0) * 1000)
                if yt_resolved:
                    attempts.append({"provider": yt_resolved.source, "outcome": "ok", "ms": ms})
                    yt_resolved.diagnostics = list(attempts)
                    return yt_resolved
                attempts.append({"provider": "youtube", "outcome": "no_match", "ms": ms})

            # Tier 2 (Local): Monochrome Lossless FLAC
            if "monochrome" not in excluded and prefix != "mc":
                t0 = time.monotonic()
                mc_resolved = self._try_resolve_monochrome(
                    track_id, prefix, raw_id, track, title, artist, duration, extra, excluded, force_refresh=force_refresh
                )
                ms = int((time.monotonic() - t0) * 1000)
                if mc_resolved:
                    attempts.append({"provider": "monochrome", "outcome": "ok", "ms": ms})
                    mc_resolved.diagnostics = list(attempts)
                    return mc_resolved
                attempts.append({"provider": "monochrome", "outcome": "no_match", "ms": ms})

            # Tier 3 (Local): JioSaavn 320kbps
            js_resolved = self._try_resolve_jiosaavn(
                track_id, title, artist, duration, excluded, attempts
            )
            if js_resolved:
                return js_resolved

            # Tier 4 (Local): SoundCloud Public API v2
            sc_resolved = self._try_resolve_soundcloud(
                track_id, title, artist, duration, excluded, attempts
            )
            if sc_resolved:
                return sc_resolved

            # Tier 4.5 (Local): Bilibili DASH Audio (proxied with Referer)
            bili_resolved = self._try_resolve_bilibili(
                track_id, title, artist, duration, excluded, attempts
            )
            if bili_resolved:
                return bili_resolved

            # Tier 5 (Local): Audius
            au_resolved = self._try_resolve_audius(
                track_id, title, artist, duration, excluded, attempts
            )
            if au_resolved:
                return au_resolved

        # ------------------------------------------------------------------
        # Tier 6: Official 30-second preview of THAT exact track (Deezer / iTunes)
        # ------------------------------------------------------------------
        preview_src = "deezer-preview" if prefix == "dz" else "itunes-preview"
        if preview_src not in excluded and (preview_url or (prefix in ("dz", "it") and raw_id)):
            t0 = time.monotonic()
            candidate_preview = preview_url
            if candidate_preview:
                ok, status, hdrs, final_url = http_client.probe_url(
                    candidate_preview, timeout=config.STREAM_PROBE_TIMEOUT
                )
                if not ok and prefix == "dz" and raw_id and status in (403, 410, 0):
                    # Refresh expired Deezer signed preview URL once
                    try:
                        refreshed = deezer.get_track(raw_id)
                        if refreshed and refreshed.get("previewUrl"):
                            self.db.upsert_track(refreshed)
                            candidate_preview = refreshed["previewUrl"]
                            ok, _, hdrs, final_url = http_client.probe_url(
                                candidate_preview, timeout=config.STREAM_PROBE_TIMEOUT
                            )
                    except Exception:
                        ok = False
                if ok:
                    ctype = hdrs.get("content-type", "audio/mpeg").split(";")[0].strip()
                    if not ctype.startswith("audio/"):
                        ctype = "audio/mpeg" if ".mp3" in final_url.lower() else "audio/mp4"
                    attempts.append({"provider": preview_src, "outcome": "preview", "ms": int((time.monotonic() - t0) * 1000)})
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="preview",
                        source=preview_src,
                        content_type=ctype,
                        url=final_url,
                        headers={"User-Agent": config.BROWSER_USER_AGENT},
                        diagnostics=list(attempts),
                    )
                    self._store_cache(resolved)
                    self._log_preview_fallback(track_id, title, artist, resolved)
                    return resolved
            elif prefix in ("dz", "it") and raw_id:
                try:
                    refreshed = (
                        deezer.get_track(raw_id) if prefix == "dz" else apple_itunes.get_track(raw_id)
                    )
                    if refreshed and refreshed.get("previewUrl"):
                        self.db.upsert_track(refreshed)
                        ok, _, hdrs, final_url = http_client.probe_url(
                            refreshed["previewUrl"], timeout=config.STREAM_PROBE_TIMEOUT
                        )
                        if ok:
                            ctype = hdrs.get("content-type", "audio/mpeg").split(";")[0].strip()
                            default_ct = "audio/mpeg" if prefix == "dz" else "audio/mp4"
                            attempts.append({"provider": preview_src, "outcome": "preview", "ms": int((time.monotonic() - t0) * 1000)})
                            resolved = ResolvedStream(
                                track_id=track_id,
                                mode="preview",
                                source=preview_src,
                                content_type=ctype if ctype.startswith("audio/") else default_ct,
                                url=final_url,
                                headers={"User-Agent": config.BROWSER_USER_AGENT},
                                diagnostics=list(attempts),
                            )
                            self._store_cache(resolved)
                            self._log_preview_fallback(track_id, title, artist, resolved)
                            return resolved
                except Exception:
                    pass

        # Online mode NEVER silently plays a fake offline-synth theme song!
        raise StreamUnavailableError(
            f"Unable to stream '{title or track_id}' right now. Please try again or pick another result."
        )

    def _store_cache(self, resolved: ResolvedStream, cache_key: Optional[str] = None) -> None:
        key = cache_key or resolved.track_id
        with self._cache_lock:
            # Never poison an existing valid "full" cache entry with a "preview" fallback
            if resolved.mode == "preview":
                existing = self._cache.get(key)
                if (
                    existing
                    and existing.mode == "full"
                    and (time.time() - existing.resolved_at) < config.STREAM_CACHE_TTL
                ):
                    return
            if len(self._cache) > 256:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = resolved

    def get_stream_info(
        self, track_id: str, debug: bool = False, quality: str = "auto"
    ) -> Dict[str, Any]:
        resolved = self.resolve_stream(track_id, quality=quality)
        norm_mime = normalize_stream_mime(resolved.content_type)
        ext = mime_to_extension(norm_mime)

        # Proactive non-blocking pre-warm for Monochrome & SoundCloud HLS tracks so
        # audio is already warming on disk/memory before the browser requests /api/stream/{id}.
        mc_id = _extract_monochrome_id(resolved)
        if mc_id:
            try:
                monochrome.start_background_cache(mc_id)
            except Exception:
                pass

        sc_m3u8 = resolved.headers.get("X-SC-M3U8") if resolved.source == "soundcloud" else None
        if sc_m3u8:
            cache_key = resolved.headers.get("X-SC-Cache-Key") or f"sc:{track_id}"
            try:
                soundcloud.start_background_hls_assembly(
                    sc_m3u8, cache_key=cache_key, mime_hint=norm_mime
                )
            except Exception:
                pass

        payload: Dict[str, Any] = {
            "trackId": track_id,
            "streamUrl": f"/api/stream/{track_id}",
            "streamUrlWithExt": f"/api/stream/{track_id}{ext}",
            "streamMode": resolved.mode,
            "streamSource": resolved.source,
            "contentType": norm_mime,
            "adFree": True,
        }

        # Expose IP-agnostic direct edge CDN URLs for fast client playback (NEVER Monochrome or YouTube)
        if sc_m3u8 and sc_m3u8.startswith("https://"):
            payload["hlsUrl"] = sc_m3u8
        elif (
            resolved.source in ("jiosaavn", "audius", "deezer-preview", "itunes-preview")
            or (resolved.source == "soundcloud" and not sc_m3u8)
        ) and resolved.url.startswith("https://"):
            payload["directUrl"] = resolved.url

        if debug:
            payload["diagnostics"] = list(resolved.diagnostics)
            payload["cloudMode"] = config.is_cloud_env()
        return payload

    def _send_common_stream_headers(self, handler: Any, resolved: ResolvedStream) -> None:
        norm_mime = normalize_stream_mime(resolved.content_type)
        ext = mime_to_extension(norm_mime)
        handler.send_header("Content-Type", norm_mime)
        handler.send_header("Content-Disposition", f'inline; filename="stream{ext}"')
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Cache-Control", "private, max-age=1800")
        handler.send_header("X-Stream-Mode", resolved.mode)
        handler.send_header("X-Stream-Source", resolved.source)
        handler.send_header("X-Ad-Free", "true")
        handler.send_header(
            "Access-Control-Expose-Headers",
            "X-Stream-Mode, X-Stream-Source, X-Ad-Free, Content-Range, Content-Disposition",
        )

    def _send_stream_unavailable(self, handler: Any, message: str, head_only: bool) -> None:
        body = json.dumps(
            {"error": {"code": "STREAM_UNAVAILABLE", "message": message}},
            ensure_ascii=False,
        ).encode("utf-8")
        handler.send_response(502)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Ad-Free", "true")
        handler.end_headers()
        if not head_only:
            try:
                handler.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass

    def _serve_local_bytes(
        self, handler: Any, resolved: ResolvedStream, range_header: Optional[str], head_only: bool
    ) -> None:
        data = resolved.local_bytes or b""
        total = len(data)
        byte_range, ok = parse_byte_range(range_header, total)
        if not ok:
            handler.send_response(416)
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Content-Range", f"bytes */{total}")
            handler.send_header("X-Ad-Free", "true")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return

        if byte_range is not None:
            start, end = byte_range
            chunk = data[start : end + 1]
            handler.send_response(206)
            self._send_common_stream_headers(handler, resolved)
            handler.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            handler.send_header("Content-Length", str(len(chunk)))
            handler.end_headers()
            if not head_only:
                self._write_chunks(handler, chunk)
        else:
            handler.send_response(200)
            self._send_common_stream_headers(handler, resolved)
            handler.send_header("Content-Length", str(total))
            handler.end_headers()
            if not head_only:
                self._write_chunks(handler, data)

    def _serve_file_path(
        self,
        handler: Any,
        file_path: Path,
        resolved: ResolvedStream,
        range_header: Optional[str],
        head_only: bool,
    ) -> None:
        total = file_path.stat().st_size
        byte_range, ok = parse_byte_range(range_header, total)
        if not ok:
            handler.send_response(416)
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Content-Range", f"bytes */{total}")
            handler.send_header("X-Ad-Free", "true")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return

        if byte_range is not None:
            start, end = byte_range
            length = end - start + 1
            handler.send_response(206)
            self._send_common_stream_headers(handler, resolved)
            handler.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            handler.send_header("Content-Length", str(length))
            handler.end_headers()
        else:
            start = 0
            length = total
            handler.send_response(200)
            self._send_common_stream_headers(handler, resolved)
            handler.send_header("Content-Length", str(length))
            handler.end_headers()

        if head_only:
            return

        try:
            with file_path.open("rb") as fh:
                if start > 0:
                    fh.seek(start)
                remaining = length
                while remaining > 0:
                    buf = fh.read(min(config.STREAM_CHUNK_SIZE, remaining))
                    if not buf:
                        break
                    handler.wfile.write(buf)
                    remaining -= len(buf)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def _serve_progressive_monochrome(
        self,
        handler: Any,
        mc_id: str,
        resolved: ResolvedStream,
        range_header: Optional[str],
        head_only: bool,
    ) -> bool:
        """Stream a Monochrome track progressively as soon as the first 64 KiB lands on disk.

        Returns True once HTTP headers have been sent (or completed cleanly), or False if
        the upstream download failed before headers so the caller can cascade to SoundCloud/etc.
        """
        handle = monochrome.get_or_start_download(mc_id)
        if not handle:
            return False

        handle.wait_until_ready(timeout=20.0)
        if (
            handle.failed
            or (handle.done and handle.bytes_written < monochrome.MIN_TRACK_BYTES)
            or not (handle.ready or handle.done)
        ):
            return False

        if handle.content_type:
            resolved.content_type = handle.content_type

        if handle.is_complete:
            self._serve_file_path(handler, handle.final_path, resolved, range_header, head_only)
            return True

        known_total = handle.total_size
        if known_total <= 0:
            # Chunked upstream without Content-Length: wait for completion then serve from disk
            handle.wait_until_done(timeout=45.0)
            if handle.failed or not handle.final_path.is_file():
                return False
            if handle.content_type:
                resolved.content_type = handle.content_type
            self._serve_file_path(handler, handle.final_path, resolved, range_header, head_only)
            return True

        byte_range, ok = parse_byte_range(range_header, known_total)
        if not ok:
            handler.send_response(416)
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Content-Range", f"bytes */{known_total}")
            handler.send_header("X-Ad-Free", "true")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return True

        # Speedup 1: Direct upstream Range pass-through for small tail / far-seek metadata probes
        # (eliminates 13-35s stalls waiting for full 25-82 MB sequential FLAC download!).
        if not head_only and byte_range is not None:
            start, end = byte_range
            length = end - start + 1
            if (
                start > handle.bytes_written + TAIL_PASSTHROUGH_GAP
                and length <= TAIL_PASSTHROUGH_MAX_LEN
            ):
                tail_resp = None
                tail_headers_sent = False
                try:
                    t_status, _, tail_resp = http_client.open_stream(
                        monochrome.get_stream_url(mc_id),
                        headers={"User-Agent": config.BROWSER_USER_AGENT},
                        range_header=f"bytes={start}-{end}",
                        method="GET",
                        timeout=8.0,
                    )
                    if t_status == 206:
                        first_chunk = tail_resp.read(min(config.STREAM_CHUNK_SIZE, length))
                        if first_chunk:
                            handler.send_response(206)
                            self._send_common_stream_headers(handler, resolved)
                            handler.send_header("Content-Range", f"bytes {start}-{end}/{known_total}")
                            handler.send_header("Content-Length", str(length))
                            handler.end_headers()
                            tail_headers_sent = True
                            handler.wfile.write(first_chunk)
                            rem = length - len(first_chunk)
                            while rem > 0:
                                buf = tail_resp.read(min(config.STREAM_CHUNK_SIZE, rem))
                                if not buf:
                                    break
                                handler.wfile.write(buf)
                                rem -= len(buf)
                            return True
                except urllib.error.HTTPError as exc:
                    try:
                        exc.close()
                    except Exception:
                        pass
                    if tail_headers_sent:
                        return True
                    # Upstream 429/non-206 before headers -> fall back cleanly to cond.wait() below
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    if tail_headers_sent:
                        return True
                except Exception:
                    if tail_headers_sent:
                        return True
                    # Upstream timeout/error before headers -> fall back cleanly to cond.wait() below
                finally:
                    if tail_resp is not None:
                        try:
                            tail_resp.close()
                        except Exception:
                            pass

        if byte_range is not None:
            start, end = byte_range
            length = end - start + 1
            handler.send_response(206)
            self._send_common_stream_headers(handler, resolved)
            handler.send_header("Content-Range", f"bytes {start}-{end}/{known_total}")
            handler.send_header("Content-Length", str(length))
            handler.end_headers()
        else:
            start = 0
            end = known_total - 1
            length = known_total
            handler.send_response(200)
            self._send_common_stream_headers(handler, resolved)
            handler.send_header("Content-Length", str(length))
            handler.end_headers()

        if head_only:
            return True

        fh = None
        using_final = False
        pos = start

        def _open_active_file(prefer_final: bool = False):
            nonlocal using_final
            paths = (
                (handle.final_path, handle.part_path)
                if prefer_final
                else (handle.part_path, handle.final_path)
            )
            for candidate in paths:
                try:
                    f = candidate.open("rb")
                    using_final = candidate == handle.final_path
                    return f
                except FileNotFoundError:
                    continue
            return None

        try:
            while pos <= end:
                with handle.cond:
                    deadline = time.monotonic() + 45.0
                    while handle.bytes_written <= pos and not handle.done:
                        remaining_wait = deadline - time.monotonic()
                        if remaining_wait <= 0:
                            break
                        handle.cond.wait(timeout=remaining_wait)
                    avail = handle.bytes_written
                    is_done = handle.done

                if avail <= pos:
                    break

                to_read = min(config.STREAM_CHUNK_SIZE, avail - pos, end - pos + 1)
                if fh is None:
                    fh = _open_active_file(prefer_final=is_done)
                    if fh is None:
                        break
                fh.seek(pos)
                buf = fh.read(to_read)
                if not buf:
                    # Handle atomic rename part_path -> final_path cleanly
                    if not using_final and handle.final_path.is_file():
                        try:
                            fh.close()
                        except Exception:
                            pass
                        fh = _open_active_file(prefer_final=True)
                        if fh is None:
                            break
                        fh.seek(pos)
                        buf = fh.read(to_read)
                    if not buf:
                        if is_done:
                            break
                        time.sleep(0.01)
                        continue

                handler.wfile.write(buf)
                pos += len(buf)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            # Browser closed/seeked socket — background producer continues uninterrupted!
            pass
        finally:
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass
        return True

    def _write_chunks(self, handler: Any, payload: bytes) -> None:
        step = config.STREAM_CHUNK_SIZE
        view = memoryview(payload)
        try:
            for offset in range(0, len(payload), step):
                handler.wfile.write(view[offset : offset + step])
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def stream_to_handler(self, handler: Any, track_id: str, head_only: bool = False) -> None:
        range_header = handler.headers.get("Range")
        try:
            resolved = self.resolve_stream(track_id)
        except StreamUnavailableError as exc:
            self._send_stream_unavailable(handler, exc.message, head_only)
            return

        # Proxy/serve resolved stream; on pre-header failure, exclude failed source and cascade
        excluded: Set[str] = set()
        for attempt in range(4):
            if resolved.local_bytes is not None:
                self._serve_local_bytes(handler, resolved, range_header, head_only)
                return

            # Monochrome single-flight progressive /tmp cache: hits tracks.monochrome.st/track/{id}
            # at most ONCE and starts streaming 200/206 immediately after first 64 KiB (<1s TTFB).
            mc_id = _extract_monochrome_id(resolved)
            if mc_id:
                if self._serve_progressive_monochrome(
                    handler, mc_id, resolved, range_header, head_only
                ):
                    return
                # Monochrome failed before headers -> cascade cleanly to SoundCloud/JioSaavn/etc.
                self.invalidate(track_id)
                excluded.add("monochrome")
                try:
                    resolved = self.resolve_stream(
                        track_id, force_refresh=True, exclude_sources=excluded
                    )
                    continue
                except StreamUnavailableError as uerr:
                    self._send_stream_unavailable(handler, uerr.message, head_only)
                    return

            # Deferred SoundCloud HLS assembly: assemble once into memory LRU, then serve all Range requests locally
            sc_m3u8 = resolved.headers.get("X-SC-M3U8") if resolved.source == "soundcloud" else None
            if sc_m3u8:
                cache_key = resolved.headers.get("X-SC-Cache-Key") or ""
                try:
                    assembled = soundcloud.assemble_hls_segments(
                        sc_m3u8, cache_key=cache_key, mime_hint=resolved.content_type
                    )
                except Exception:
                    assembled = None
                if assembled:
                    resolved.local_bytes = assembled[0]
                    resolved.content_type = assembled[1]
                    self._serve_local_bytes(handler, resolved, range_header, head_only)
                    return
                is_stale = (time.time() - resolved.resolved_at) > 30.0
                self.invalidate(track_id)
                if attempt >= 1 or not is_stale:
                    excluded.add("soundcloud")
                try:
                    resolved = self.resolve_stream(
                        track_id, force_refresh=True, exclude_sources=excluded
                    )
                    continue
                except StreamUnavailableError as uerr:
                    self._send_stream_unavailable(handler, uerr.message, head_only)
                    return

            resp = None
            headers_sent = False
            try:
                try:
                    status, up_headers, resp = http_client.open_stream(
                        resolved.url,
                        headers=resolved.headers,
                        range_header=range_header,
                        method="HEAD" if head_only else "GET",
                        timeout=config.STREAM_READ_TIMEOUT,
                    )
                except urllib.error.HTTPError as head_exc:
                    if head_only and head_exc.code == 405:
                        head_exc.close()
                        status, up_headers, resp = http_client.open_stream(
                            resolved.url,
                            headers=resolved.headers,
                            range_header=range_header or "bytes=0-",
                            method="GET",
                            timeout=config.STREAM_READ_TIMEOUT,
                        )
                    else:
                        raise

                ctype = up_headers.get("content-type", resolved.content_type).split(";")[0].strip().lower()
                if ctype.startswith(_VALID_STREAM_MIME_PREFIXES):
                    resolved.content_type = ctype

                has_content_length = "content-length" in up_headers
                handler.send_response(status)
                self._send_common_stream_headers(handler, resolved)
                if "content-range" in up_headers:
                    handler.send_header("Content-Range", up_headers["content-range"])
                if has_content_length:
                    handler.send_header("Content-Length", up_headers["content-length"])
                elif not head_only:
                    handler.send_header("Connection", "close")
                    handler.close_connection = True
                handler.end_headers()
                headers_sent = True

                if not head_only:
                    while True:
                        buf = resp.read(config.STREAM_CHUNK_SIZE)
                        if not buf:
                            break
                        handler.wfile.write(buf)
                return
            except urllib.error.HTTPError as exc:
                try:
                    exc.close()
                except Exception:
                    pass
                if headers_sent:
                    return
                if exc.code == 416:
                    handler.send_response(416)
                    handler.send_header("Accept-Ranges", "bytes")
                    if exc.headers and exc.headers.get("Content-Range"):
                        handler.send_header("Content-Range", exc.headers.get("Content-Range"))
                    handler.send_header("X-Ad-Free", "true")
                    handler.send_header("Content-Length", "0")
                    handler.end_headers()
                    return
                if attempt < 3 and exc.code in (403, 404, 410, 429, 500, 502, 503):
                    self.invalidate(track_id)
                    is_fresh = (time.time() - resolved.resolved_at) < 30.0
                    if attempt >= 1 or is_fresh:
                        excluded.add(resolved.source)
                    try:
                        resolved = self.resolve_stream(
                            track_id, force_refresh=True, exclude_sources=excluded
                        )
                    except StreamUnavailableError as uerr:
                        self._send_stream_unavailable(handler, uerr.message, head_only)
                        return
                    continue
                self._send_stream_unavailable(
                    handler,
                    f"Upstream stream rejected request (HTTP {exc.code}).",
                    head_only,
                )
                return
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except Exception:
                if headers_sent:
                    return
                if attempt < 3:
                    self.invalidate(track_id)
                    excluded.add(resolved.source)
                    try:
                        resolved = self.resolve_stream(
                            track_id, force_refresh=True, exclude_sources=excluded
                        )
                    except StreamUnavailableError as uerr:
                        self._send_stream_unavailable(handler, uerr.message, head_only)
                        return
                    continue
                self._send_stream_unavailable(
                    handler,
                    "Unable to stream this track right now.",
                    head_only,
                )
                return
            finally:
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass
