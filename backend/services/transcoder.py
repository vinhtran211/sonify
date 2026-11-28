"""Hardened, zero-SSRF, bounded-resource ffmpeg transcoder with single-flight disk LRU cache."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend import config, http_client

CACHE_DIR = Path(tempfile.gettempdir()) / "sonify_compact_cache"
MAX_CACHE_FILES = 25
MAX_CACHE_BYTES = 150 * 1024 * 1024  # 150 MiB total disk cap (Render 512MB safe)
MAX_INPUT_BYTES = 80 * 1024 * 1024   # 80 MiB max upstream source size
MIN_OUTPUT_BYTES = 100_000           # 100 KiB minimum valid full-track output
TRANSCODE_TIMEOUT_SEC = 30.0
SEM_ACQUIRE_TIMEOUT_SEC = 15.0

_TRANSCODE_SEM = threading.Semaphore(2)
_LOCKS_GUARD = threading.Lock()
_KEY_LOCKS: Dict[str, threading.Lock] = {}
_CACHE_LOCK = threading.Lock()

_TARGET_META: Dict[str, Tuple[str, str]] = {
    "mp3": (".mp3", "audio/mpeg"),
    "m4a_copy": (".m4a", "audio/mp4"),
}


def is_available() -> bool:
    """Return True when ffmpeg binary is installed on PATH."""
    return shutil.which("ffmpeg") is not None


def _ensure_cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(CACHE_DIR, 0o700)
    except OSError:
        pass
    return CACHE_DIR


def _cache_digest(cache_key: str, target: str) -> str:
    return hashlib.sha256(f"{cache_key}|{target}".encode("utf-8")).hexdigest()[:24]


def _target_spec(target: str) -> Tuple[str, str]:
    t = (target or "mp3").strip().lower()
    if t not in _TARGET_META:
        raise ValueError(f"Unsupported transcode target '{target}'")
    return _TARGET_META[t]


def _final_cache_path(cache_key: str, target: str) -> Tuple[Path, str]:
    ext, mime = _target_spec(target)
    digest = _cache_digest(cache_key, target)
    return _ensure_cache_dir() / f"{digest}{ext}", mime


def _get_key_lock(lock_key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _KEY_LOCKS.get(lock_key)
        if lock is None:
            if len(_KEY_LOCKS) > 256:
                for k in list(_KEY_LOCKS.keys()):
                    if not _KEY_LOCKS[k].locked():
                        _KEY_LOCKS.pop(k, None)
                        if len(_KEY_LOCKS) <= 128:
                            break
            lock = threading.Lock()
            _KEY_LOCKS[lock_key] = lock
        return lock


def _evict_lru_cache() -> None:
    """Enforce MAX_CACHE_FILES and MAX_CACHE_BYTES in CACHE_DIR via mtime LRU."""
    with _CACHE_LOCK:
        try:
            entries: List[Tuple[float, int, Path]] = []
            total_bytes = 0
            for p in CACHE_DIR.iterdir():
                if not p.is_file() or ".part." in p.name or ".src." in p.name:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, p))
                total_bytes += st.st_size

            if len(entries) <= MAX_CACHE_FILES and total_bytes <= MAX_CACHE_BYTES:
                return

            entries.sort(key=lambda item: item[0])  # oldest first
            remaining_count = len(entries)
            for _, size, path in entries:
                if remaining_count <= MAX_CACHE_FILES and total_bytes <= MAX_CACHE_BYTES:
                    break
                try:
                    path.unlink(missing_ok=True)
                    total_bytes = max(0, total_bytes - size)
                    remaining_count -= 1
                except OSError:
                    pass
        except OSError:
            pass


def clear_cache() -> None:
    """Remove all cached compact files (primarily for tests)."""
    with _CACHE_LOCK:
        if not CACHE_DIR.exists():
            return
        for p in CACHE_DIR.iterdir():
            try:
                if p.is_file():
                    p.unlink(missing_ok=True)
            except OSError:
                pass


def get_cached(cache_key: str, target: str = "mp3") -> Optional[Tuple[Path, str]]:
    """Return (path, content_type) if a valid transcoded file is already on disk."""
    try:
        final_path, mime = _final_cache_path(cache_key, target)
    except ValueError:
        return None
    try:
        if final_path.is_file() and final_path.stat().st_size >= MIN_OUTPUT_BYTES:
            try:
                os.utime(final_path, None)
            except OSError:
                pass
            return final_path, mime
    except OSError:
        pass
    return None


def _build_ffmpeg_cmd(
    ffmpeg_bin: str,
    input_path: Path,
    part_out_path: Path,
    target: str,
    *,
    aac_reencode_fallback: bool = False,
) -> List[str]:
    base = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(input_path),
        "-vn",
        "-sn",
        "-dn",
        "-map",
        "0:a:0",
    ]
    if target == "mp3":
        return base + [
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-f",
            "mp3",
            str(part_out_path),
        ]
    if target == "m4a_copy":
        codec_args = (
            ["-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"]
            if aac_reencode_fallback
            else ["-c:a", "copy"]
        )
        return base + codec_args + [
            "-movflags",
            "+faststart",
            "-f",
            "ipod",
            str(part_out_path),
        ]
    raise ValueError(f"Unsupported transcode target '{target}'")


def _run_ffmpeg_to_cache(
    input_path: Path,
    cache_key: str,
    target: str = "mp3",
) -> Optional[Tuple[Path, str]]:
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return None

    target_norm = (target or "mp3").strip().lower()
    if target_norm not in _TARGET_META:
        return None

    try:
        resolved_in = input_path.resolve(strict=True)
    except OSError:
        return None
    if not resolved_in.is_file():
        return None

    final_path, mime = _final_cache_path(cache_key, target_norm)
    digest = _cache_digest(cache_key, target_norm)
    part_out_path = _ensure_cache_dir() / f"{digest}.part.{os.getpid()}.{threading.get_ident()}"

    acquired = _TRANSCODE_SEM.acquire(timeout=SEM_ACQUIRE_TIMEOUT_SEC)
    if not acquired:
        return None

    try:
        cmd = _build_ffmpeg_cmd(ffmpeg_bin, resolved_in, part_out_path, target_norm)
        proc = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            timeout=TRANSCODE_TIMEOUT_SEC,
            check=False,
        )
        if (
            proc.returncode != 0
            and target_norm == "m4a_copy"
        ):
            try:
                part_out_path.unlink(missing_ok=True)
            except OSError:
                pass
            fallback_cmd = _build_ffmpeg_cmd(
                ffmpeg_bin,
                resolved_in,
                part_out_path,
                target_norm,
                aac_reencode_fallback=True,
            )
            proc = subprocess.run(
                fallback_cmd,
                shell=False,
                capture_output=True,
                timeout=TRANSCODE_TIMEOUT_SEC,
                check=False,
            )

        if proc.returncode != 0 or not part_out_path.is_file():
            return None
        if part_out_path.stat().st_size < MIN_OUTPUT_BYTES:
            return None

        part_out_path.replace(final_path)
        _evict_lru_cache()
        return final_path, mime
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    finally:
        _TRANSCODE_SEM.release()
        try:
            part_out_path.unlink(missing_ok=True)
        except OSError:
            pass


def transcode_file(
    input_path: Path,
    cache_key: str,
    target: str = "mp3",
) -> Optional[Tuple[Path, str]]:
    """Transcode a local file into the compact disk LRU cache (single-flight per key)."""
    if not is_available():
        return None
    cached = get_cached(cache_key, target)
    if cached:
        return cached
    lock = _get_key_lock(f"{cache_key}|{target}")
    with lock:
        cached = get_cached(cache_key, target)
        if cached:
            return cached
        return _run_ffmpeg_to_cache(input_path, cache_key, target=target)


def transcode_bytes(
    raw_bytes: bytes,
    cache_key: str,
    target: str = "mp3",
) -> Optional[Tuple[Path, str]]:
    """Transcode in-memory media bytes into the compact disk LRU cache."""
    if not is_available() or not raw_bytes or len(raw_bytes) > MAX_INPUT_BYTES:
        return None
    cached = get_cached(cache_key, target)
    if cached:
        return cached
    lock = _get_key_lock(f"{cache_key}|{target}")
    with lock:
        cached = get_cached(cache_key, target)
        if cached:
            return cached
        digest = _cache_digest(cache_key, target)
        src_path = _ensure_cache_dir() / f"{digest}.src.{os.getpid()}.{threading.get_ident()}"
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(str(src_path), flags, 0o600)
            try:
                os.write(fd, raw_bytes)
            finally:
                os.close(fd)
            return _run_ffmpeg_to_cache(src_path, cache_key, target=target)
        except OSError:
            return None
        finally:
            try:
                src_path.unlink(missing_ok=True)
            except OSError:
                pass


def transcode_stream_url(
    url: str,
    headers: Optional[Dict[str, str]],
    cache_key: str,
    target: str = "mp3",
) -> Optional[Tuple[Path, str]]:
    """Download an SSRF-validated upstream HTTP(S) stream to a temp file and transcode locally."""
    if not is_available():
        return None
    cached = get_cached(cache_key, target)
    if cached:
        return cached

    # Validate outbound URL upfront before acquiring locks or opening sockets
    ok, _ = http_client.validate_outbound_url(url)
    if not ok:
        return None

    lock = _get_key_lock(f"{cache_key}|{target}")
    with lock:
        cached = get_cached(cache_key, target)
        if cached:
            return cached

        digest = _cache_digest(cache_key, target)
        src_path = _ensure_cache_dir() / f"{digest}.src.{os.getpid()}.{threading.get_ident()}"
        resp = None
        try:
            status, _, resp = http_client.open_stream(
                url,
                headers=headers or {"User-Agent": config.BROWSER_USER_AGENT},
                method="GET",
                timeout=config.STREAM_READ_TIMEOUT,
            )
            if status not in (200, 206):
                return None

            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(str(src_path), flags, 0o600)
            total = 0
            try:
                while True:
                    chunk = resp.read(config.STREAM_CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_INPUT_BYTES:
                        return None
                    os.write(fd, chunk)
            finally:
                os.close(fd)

            if total < 4096:
                return None
            return _run_ffmpeg_to_cache(src_path, cache_key, target=target)
        except Exception:
            return None
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
            try:
                src_path.unlink(missing_ok=True)
            except OSError:
                pass
