"""Multi-tier ad-free audio resolution waterfall and HTTP Range proxy service."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from backend import config, http_client, offline_catalog
from backend.db import Database
from backend.providers import (
    apple_itunes,
    audius,
    deezer,
    jiosaavn,
    monochrome,
    soundcloud,
    youtube_music,
)

_RANGE_RE = re.compile(r"^\s*bytes=(\d*)-(\d*)\s*$", re.IGNORECASE)
_YT_SOURCES = frozenset({"youtube-innertube", "yt-dlp", "piped", "invidious", "youtube"})
_VALID_STREAM_MIME_PREFIXES = ("audio/", "video/mp4", "video/webm")


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
    ) -> ResolvedStream:
        excluded: Set[str] = set(exclude_sources or ())
        now = time.time()

        if not force_refresh and not excluded:
            with self._cache_lock:
                cached = self._cache.get(track_id)
                if cached and (now - cached.resolved_at) < config.STREAM_CACHE_TTL:
                    return cached

        # Single-flight per track_id so concurrent /info + /stream share one resolution
        track_lock = self._get_track_lock(track_id)
        with track_lock:
            now = time.time()
            if not force_refresh and not excluded:
                with self._cache_lock:
                    cached = self._cache.get(track_id)
                    if cached and (now - cached.resolved_at) < config.STREAM_CACHE_TTL:
                        return cached

            return self._resolve_stream_unlocked(track_id, excluded)

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
    ) -> Optional[ResolvedStream]:
        if "monochrome" in excluded or prefix == "mc":
            return None

        cached_mc_id = str(extra.get("monochromeTrackId") or "").strip()
        if cached_mc_id:
            try:
                probed = monochrome.probe_track_stream(cached_mc_id)
                if probed:
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="full",
                        source="monochrome",
                        content_type=probed[1],
                        url=probed[0],
                        headers={"User-Agent": config.BROWSER_USER_AGENT},
                    )
                    self._store_cache(resolved)
                    return resolved
            except Exception:
                pass

        if not title:
            return None

        isrc = str(extra.get("isrc") or "").strip().upper()
        if not isrc and prefix == "dz" and raw_id:
            try:
                dz_info = deezer.get_track(raw_id)
                if dz_info:
                    self.db.upsert_track(dz_info)
                    isrc = str((dz_info.get("_extra") or {}).get("isrc") or "").strip().upper()
            except Exception:
                pass

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

    def _resolve_stream_unlocked(self, track_id: str, excluded: Set[str]) -> ResolvedStream:
        prefix, _, raw_id = track_id.partition(":")
        track = self._load_track_metadata(track_id)
        extra = (track or {}).get("_extra") or {}
        title = (track or {}).get("title") or ""
        artist = (track or {}).get("artist") or ""
        duration = int((track or {}).get("duration") or 0)
        preview_url = (track or {}).get("previewUrl") or ""

        # Offline synthesized WAV is STRICTLY limited to local:* demo tracks or explicit offline mode
        if prefix == "local" or http_client.is_offline_mode():
            resolved = self._tier5_offline_wav(track_id, duration=duration or 45)
            self._store_cache(resolved)
            return resolved

        yt_excluded = bool(excluded & _YT_SOURCES)

        # ------------------------------------------------------------------
        # Tier 0: Native full-length streams (Audius / JioSaavn / YouTube / Monochrome)
        # ------------------------------------------------------------------
        if prefix == "au" and raw_id and "audius" not in excluded:
            try:
                au_url = audius.get_stream_url(raw_id)
                ok, _, hdrs, final_url = http_client.probe_url(
                    au_url, timeout=config.STREAM_PROBE_TIMEOUT
                )
                if ok:
                    ctype = hdrs.get("content-type", "audio/mpeg").split(";")[0].strip()
                    if not ctype.startswith("audio/"):
                        ctype = "audio/mpeg"
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="full",
                        source="audius",
                        content_type=ctype,
                        url=final_url,
                        headers={"User-Agent": config.BROWSER_USER_AGENT},
                    )
                    self._store_cache(resolved)
                    return resolved
            except Exception:
                pass

        if prefix == "js" and raw_id and "jiosaavn" not in excluded:
            try:
                if not extra.get("encrypted_media_url") and not extra.get("direct_urls"):
                    details = jiosaavn.get_song_details(raw_id)
                    if details and details.get("_extra"):
                        extra = details["_extra"]
                        self.db.update_track_extra(track_id, extra)
                cand_urls = jiosaavn.resolve_candidate_urls(extra)
                found = jiosaavn.probe_best_url(cand_urls)
                if found:
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="full",
                        source="jiosaavn",
                        content_type=found[1],
                        url=found[0],
                        headers={"User-Agent": config.BROWSER_USER_AGENT},
                    )
                    self._store_cache(resolved)
                    return resolved
            except Exception:
                pass

        if prefix == "yt" and raw_id and not yt_excluded:
            try:
                yt_res = youtube_music.resolve_audio_stream(raw_id)
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

        if prefix == "mc" and raw_id and "monochrome" not in excluded:
            try:
                mc_res = monochrome.probe_track_stream(raw_id)
                if mc_res:
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="full",
                        source="monochrome",
                        content_type=mc_res[1],
                        url=mc_res[0],
                        headers={"User-Agent": config.BROWSER_USER_AGENT},
                    )
                    self._store_cache(resolved)
                    return resolved
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Tier 1 & Tier 2 (Environment-Aware Primary Full-Track Sources):
        # - On Cloud (Render/AWS datacenter IP): Monochrome (Lossless FLAC) FIRST -> YouTube second
        # - On Local / Residential IP: Fast YouTube ANDROID itag=18 FIRST -> Monochrome second
        # ------------------------------------------------------------------
        if config.is_cloud_env():
            mc_resolved = self._try_resolve_monochrome(
                track_id, prefix, raw_id, track, title, artist, duration, extra, excluded
            )
            if mc_resolved:
                return mc_resolved

            yt_resolved = self._try_resolve_youtube(
                track_id, prefix, track, title, artist, duration, extra, excluded
            )
            if yt_resolved:
                return yt_resolved
        else:
            yt_resolved = self._try_resolve_youtube(
                track_id, prefix, track, title, artist, duration, extra, excluded
            )
            if yt_resolved:
                return yt_resolved

            mc_resolved = self._try_resolve_monochrome(
                track_id, prefix, raw_id, track, title, artist, duration, extra, excluded
            )
            if mc_resolved:
                return mc_resolved

        # ------------------------------------------------------------------
        # Tier 3: JioSaavn 320kbps full-track cross-match (strict artist+title gate)
        # ------------------------------------------------------------------
        if title and "jiosaavn" not in excluded:
            try:
                js_match = jiosaavn.find_best_stream(title, artist, duration)
                if js_match:
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="full",
                        source="jiosaavn",
                        content_type=js_match[1],
                        url=js_match[0],
                        headers={"User-Agent": config.BROWSER_USER_AGENT},
                    )
                    self._store_cache(resolved)
                    return resolved
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Tier 4: SoundCloud Public API v2 full-track match (strict ALLOW + non-remix gate)
        # ------------------------------------------------------------------
        if title and "soundcloud" not in excluded:
            try:
                sc_match = soundcloud.find_best_stream(title, artist, duration)
                if sc_match:
                    if sc_match.get("kind") == "local" and sc_match.get("bytes"):
                        resolved = ResolvedStream(
                            track_id=track_id,
                            mode="full",
                            source="soundcloud",
                            content_type=str(sc_match.get("content_type") or "audio/mp4"),
                            local_bytes=sc_match["bytes"],
                        )
                    else:
                        resolved = ResolvedStream(
                            track_id=track_id,
                            mode="full",
                            source="soundcloud",
                            content_type=str(sc_match.get("content_type") or "audio/mpeg"),
                            url=str(sc_match.get("url") or ""),
                            headers={"User-Agent": config.BROWSER_USER_AGENT},
                        )
                    self._store_cache(resolved)
                    return resolved
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Tier 5: Audius full-track cross-match (strict artist+title gate)
        # ------------------------------------------------------------------
        if title and "audius" not in excluded:
            try:
                au_match = audius.find_best_stream(title, artist, duration)
                if au_match:
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="full",
                        source="audius",
                        content_type=au_match[1],
                        url=au_match[0],
                        headers={"User-Agent": config.BROWSER_USER_AGENT},
                    )
                    self._store_cache(resolved)
                    return resolved
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Tier 6: Official 30-second preview of THAT exact track (Deezer / iTunes)
        # ------------------------------------------------------------------
        preview_src = "deezer-preview" if prefix == "dz" else "itunes-preview"
        if preview_src not in excluded and (preview_url or (prefix in ("dz", "it") and raw_id)):
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
                    resolved = ResolvedStream(
                        track_id=track_id,
                        mode="preview",
                        source=preview_src,
                        content_type=ctype,
                        url=final_url,
                        headers={"User-Agent": config.BROWSER_USER_AGENT},
                    )
                    self._store_cache(resolved)
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
                            resolved = ResolvedStream(
                                track_id=track_id,
                                mode="preview",
                                source=preview_src,
                                content_type=ctype if ctype.startswith("audio/") else default_ct,
                                url=final_url,
                                headers={"User-Agent": config.BROWSER_USER_AGENT},
                            )
                            self._store_cache(resolved)
                            return resolved
                except Exception:
                    pass

        # Online mode NEVER silently plays a fake offline-synth theme song!
        raise StreamUnavailableError(
            f"Unable to stream '{title or track_id}' right now. Please try again or pick another result."
        )

    def _store_cache(self, resolved: ResolvedStream) -> None:
        with self._cache_lock:
            if len(self._cache) > 256:
                self._cache.pop(next(iter(self._cache)))
            self._cache[resolved.track_id] = resolved

    def get_stream_info(self, track_id: str) -> Dict[str, Any]:
        resolved = self.resolve_stream(track_id)
        return {
            "trackId": track_id,
            "streamUrl": f"/api/stream/{track_id}",
            "streamMode": resolved.mode,
            "streamSource": resolved.source,
            "contentType": resolved.content_type,
            "adFree": True,
        }

    def _send_common_stream_headers(self, handler: Any, resolved: ResolvedStream) -> None:
        handler.send_header("Content-Type", resolved.content_type)
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Cache-Control", "private, max-age=1800")
        handler.send_header("X-Stream-Mode", resolved.mode)
        handler.send_header("X-Stream-Source", resolved.source)
        handler.send_header("X-Ad-Free", "true")
        handler.send_header(
            "Access-Control-Expose-Headers",
            "X-Stream-Mode, X-Stream-Source, X-Ad-Free, Content-Range",
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

        if resolved.local_bytes is not None:
            self._serve_local_bytes(handler, resolved, range_header, head_only)
            return

        # Proxy remote stream; on pre-header failure, exclude failed source and cascade to next real provider
        excluded: Set[str] = set()
        for attempt in range(3):
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
                    elif head_exc.code == 429 and resolved.source == "monochrome":
                        head_exc.close()
                        time.sleep(2.0)
                        status, up_headers, resp = http_client.open_stream(
                            resolved.url,
                            headers=resolved.headers,
                            range_header=range_header,
                            method="HEAD" if head_only else "GET",
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
                if attempt < 2 and exc.code in (403, 404, 410, 429, 500, 502, 503):
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
                    if resolved.local_bytes is not None:
                        self._serve_local_bytes(handler, resolved, range_header, head_only)
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
                if attempt < 2:
                    self.invalidate(track_id)
                    excluded.add(resolved.source)
                    try:
                        resolved = self.resolve_stream(
                            track_id, force_refresh=True, exclude_sources=excluded
                        )
                    except StreamUnavailableError as uerr:
                        self._send_stream_unavailable(handler, uerr.message, head_only)
                        return
                    if resolved.local_bytes is not None:
                        self._serve_local_bytes(handler, resolved, range_header, head_only)
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
