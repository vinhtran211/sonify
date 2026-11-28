"""Central configuration for Sonify backend."""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path
from typing import Optional, Set

APP_NAME = "Sonify"
APP_VERSION = "1.0.0"

_PROCESS_SESSION_SECRET: bytes = secrets.token_bytes(32)

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = Path(os.environ.get("SONIFY_DB") or (DATA_DIR / "music.db"))
FALLBACK_DB_PATH = Path("/tmp/sonify/music.db")


def resolve_default_host() -> str:
    """Resolve bind host: explicit SONIFY_HOST > 0.0.0.0 on cloud (PORT/SPACE_ID) > 127.0.0.1 local."""
    explicit = os.environ.get("SONIFY_HOST", "").strip()
    if explicit:
        return explicit
    if os.environ.get("PORT") or os.environ.get("SPACE_ID"):
        return "0.0.0.0"
    return "127.0.0.1"


def resolve_default_port() -> int:
    """Resolve listen port: SONIFY_PORT > cloud PORT > 8080."""
    raw = (os.environ.get("SONIFY_PORT") or os.environ.get("PORT") or "8080").strip()
    try:
        return int(raw)
    except ValueError:
        return 8080


DEFAULT_HOST = resolve_default_host()
DEFAULT_PORT = resolve_default_port()


def is_offline_env() -> bool:
    return os.environ.get("SONIFY_OFFLINE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def allow_previews() -> bool:
    """Return True ONLY when SONIFY_ALLOW_PREVIEWS is explicitly enabled (default False: zero 30s previews)."""
    return os.environ.get("SONIFY_ALLOW_PREVIEWS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def is_cloud_env() -> bool:
    """Detect cloud hosting environment (Render, Railway, Fly.io, HF Spaces, or explicit SONIFY_CLOUD_MODE)."""
    cloud_flag = os.environ.get("SONIFY_CLOUD_MODE", "").strip().lower()
    if cloud_flag in ("1", "true", "yes", "on"):
        return True
    if cloud_flag in ("0", "false", "no", "off"):
        return False
    return any(
        bool(os.environ.get(k))
        for k in (
            "RENDER",
            "RENDER_SERVICE_ID",
            "RAILWAY_ENVIRONMENT",
            "FLY_APP_NAME",
            "SPACE_ID",
        )
    )


# Cache TTLs (seconds)
HOME_CACHE_TTL = 600          # 10 minutes
SEARCH_CACHE_TTL = 300        # 5 minutes
STREAM_CACHE_TTL = 1800       # 30 minutes
LYRICS_CACHE_TTL = 7 * 86400  # 7 days
AUDIUS_HOST_TTL = 1800        # 30 minutes
INVIDIOUS_LIST_TTL = 3600     # 60 minutes
INSTANCE_FAIL_TTL = 600       # 10 minutes

# HTTP Timeouts (seconds)
PROVIDER_TIMEOUT = 7.5
STREAM_PROBE_TIMEOUT = 4.5
MONOCHROME_PROBE_TIMEOUT = 22.0
STREAM_READ_TIMEOUT = 25.0

# Streaming
STREAM_CHUNK_SIZE = 65536     # 64 KiB

# Headers & Provider constants
LRCLIB_USER_AGENT = "Sonify/1.0 (https://github.com/sonify-local/sonify)"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
# JioSaavn's public protocol DES-ECB obfuscation constant (not an app secret; optional env override)
JIOSAAVN_DES_KEY = (
    os.environ.get("JIOSAAVN_DES_KEY", "").strip().encode("utf-8")[:8] or b"38346591"
)

DEFAULT_SETTINGS = {
    "volume": 0.8,
    "muted": False,
    "shuffle": False,
    "repeat": "off",
    "region": "global",
    "lastTrackId": None,
    "lastPosition": 0,
    "autoplay": True,
    "crossfade": 0,
    "playbackRate": 1.0,
    "audioQuality": "auto",
    "eqPreset": "flat",
    "eqBands": [0, 0, 0, 0, 0],
}


VALID_AUDIO_QUALITIES = ("auto", "compact", "lossless", "fast")


def has_yt_dlp() -> bool:
    return shutil.which("yt-dlp") is not None


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


# Optional iOS distribution / App Store / TestFlight / OTA env helpers
def get_testflight_url() -> str | None:
    val = os.environ.get("SONIFY_TESTFLIGHT_URL", "").strip()
    if val.lower().startswith("https://"):
        return val
    return None


def get_ios_ipa_url() -> str | None:
    val = os.environ.get("SONIFY_IOS_IPA_URL", "").strip()
    if val.lower().startswith("https://"):
        return val
    return None


def get_ios_bundle_id() -> str:
    return os.environ.get("SONIFY_IOS_BUNDLE_ID", "").strip() or "com.sonify.music"


def get_github_repo() -> str:
    return os.environ.get("SONIFY_GITHUB_REPO", "").strip() or "vinhtran211/sonify"


# ------------------------------------------------------------------
# Security & Session Configuration Helpers
# ------------------------------------------------------------------
def get_session_secret() -> bytes:
    """Return signing key for sonify_sid cookies (SONIFY_SESSION_SECRET or per-process CSPRNG)."""
    raw = os.environ.get("SONIFY_SESSION_SECRET", "").strip()
    if raw:
        return raw.encode("utf-8")
    return _PROCESS_SESSION_SECRET


def is_session_required() -> bool:
    return os.environ.get("SONIFY_REQUIRE_SESSION", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def get_api_token() -> Optional[str]:
    val = os.environ.get("SONIFY_API_TOKEN", "").strip()
    return val or None


def is_debug_allowed() -> bool:
    flag = os.environ.get("SONIFY_ALLOW_DEBUG", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return not is_cloud_env()


def get_debug_token() -> Optional[str]:
    val = os.environ.get("SONIFY_DEBUG_TOKEN", "").strip()
    return val or None


def get_public_origin() -> Optional[str]:
    val = os.environ.get("SONIFY_PUBLIC_ORIGIN", "").strip().rstrip("/")
    if val.lower().startswith(("https://", "http://")):
        return val
    return None


def get_allowed_hosts() -> Set[str]:
    hosts: Set[str] = set()
    raw = os.environ.get("SONIFY_ALLOWED_HOSTS", "")
    for part in raw.split(","):
        h = part.strip().lower()
        if h:
            hosts.add(h)
    render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip().lower()
    if render_host:
        hosts.add(render_host)
    return hosts


def get_allowed_origins() -> Set[str]:
    origins: Set[str] = set()
    raw = os.environ.get("SONIFY_ALLOWED_ORIGINS", "")
    for part in raw.split(","):
        o = part.strip().rstrip("/").lower()
        if o:
            origins.add(o)
    pub = get_public_origin()
    if pub:
        origins.add(pub.lower())
    return origins


def get_rate_limit_rpm() -> int:
    raw = os.environ.get("SONIFY_RATE_LIMIT_RPM", "180").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 180
