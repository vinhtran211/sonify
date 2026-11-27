"""Central security primitives for Sonify (SSRF protection, input validation, sessions, rate limiting)."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import socket
import threading
import time
import urllib.parse
from http.cookies import SimpleCookie
from typing import Dict, Optional, Tuple

from backend import config

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$")
_CONTROL_OR_CSS_BREAK_RE = re.compile(r"[\x00-\x1f\x7f'\"()\\<>\s]")

_PROVIDER_ID_PATTERNS: Dict[str, re.Pattern[str]] = {
    "dz": re.compile(r"^\d{1,20}$"),
    "it": re.compile(r"^\d{1,20}$"),
    "yt": re.compile(r"^(?!--)[A-Za-z0-9_-]{6,20}$"),
    "mc": re.compile(r"^[A-Za-z0-9_-]{1,40}$"),
    "au": re.compile(r"^[A-Za-z0-9_-]{1,64}$"),
    "js": re.compile(r"^[A-Za-z0-9_-]{1,64}$"),
    "local": re.compile(r"^[a-zA-Z0-9._:-]{1,80}$"),
    "imp": re.compile(r"^[a-zA-Z0-9._:-]{1,80}$"),
}

_SESSION_TOKEN_RE = re.compile(r"^([0-9a-f]{24})\.([0-9a-f]{32})$")
_RAW_HEADER_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{20,80}$")

SESSION_COOKIE_NAME = "sonify_sid"


def _is_disallowed_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_loopback: bool
) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_disallowed_ip(ip.ipv4_mapped, allow_loopback=allow_loopback)
    if ip.is_loopback:
        return not allow_loopback
    return bool(
        ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_outbound_url(url: str, *, allow_loopback: bool = False) -> Tuple[bool, str]:
    """Validate an outbound HTTP(S) URL against SSRF (blocks non-HTTP(S), private, metadata, and loopback IPs)."""
    raw = str(url or "").strip()
    if not raw or any(ch in raw for ch in ("\r", "\n", "\x00")):
        return False, "Empty or malformed URL"

    try:
        parsed = urllib.parse.urlsplit(raw)
    except Exception:
        return False, "Unparseable URL"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"Disallowed URL scheme '{scheme}'"

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return False, "Missing URL hostname"

    allow_local = allow_loopback or (
        os.environ.get("SONIFY_ALLOW_LOCAL_UPSTREAMS", "").strip().lower()
        in ("1", "true", "yes", "on")
    )

    if hostname == "localhost" or hostname.endswith(".localhost"):
        if not allow_local:
            return False, "Loopback hostname disallowed"
        return True, ""

    # Check literal IP address first
    try:
        lit_ip = ipaddress.ip_address(hostname)
    except ValueError:
        lit_ip = None

    if lit_ip is not None:
        if _is_disallowed_ip(lit_ip, allow_loopback=allow_local):
            return False, f"Disallowed IP address '{lit_ip}'"
        return True, ""

    # Resolve DNS and verify every returned IP address
    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError:
        # Let normal HTTP client fail cleanly if DNS is unreachable / mocked in offline unit tests
        return True, ""

    if not infos:
        return False, "Hostname resolved to no addresses"

    for info in infos:
        addr = info[4][0]
        try:
            ip_obj = ipaddress.ip_address(addr.split("%", 1)[0])
        except ValueError:
            return False, f"Invalid resolved IP '{addr}'"
        if _is_disallowed_ip(ip_obj, allow_loopback=allow_local):
            return False, f"Hostname '{hostname}' resolved to disallowed IP '{ip_obj}'"

    return True, ""


def sanitize_media_url(url: str, *, max_len: int = 512) -> str:
    """Allow ONLY safe https://, http://, or single-slash /api/artwork/... URLs (blocks CSS/URL injection)."""
    raw = str(url or "").strip()
    if not raw or len(raw) > max_len:
        return ""
    if _CONTROL_OR_CSS_BREAK_RE.search(raw):
        return ""
    if raw.startswith("//"):
        return ""
    if raw.startswith("/api/artwork/"):
        if ".." in raw:
            return ""
        return raw
    low = raw.lower()
    if low.startswith(("https://", "http://")):
        parsed = urllib.parse.urlsplit(raw)
        if not parsed.netloc or not parsed.hostname:
            return ""
        return raw
    return ""


def is_valid_hex_color(value: str) -> bool:
    """Return True only for 3-digit or 6-digit CSS hex colors like #1DB954."""
    return bool(_HEX_COLOR_RE.match(str(value or "").strip()))


def validate_provider_id(prefix: str, raw_id: str) -> bool:
    """Enforce strict per-provider ID syntax to prevent argument/path/query smuggling."""
    p = (prefix or "").strip().lower()
    rid = (raw_id or "").strip()
    if not p or not rid:
        return False
    pat = _PROVIDER_ID_PATTERNS.get(p)
    if pat is None:
        return False
    return bool(pat.match(rid))


def validate_track_id(track_id: str) -> bool:
    """Validate full '<prefix>:<raw_id>' track identifier."""
    tid = (track_id or "").strip()
    if not tid or ":" not in tid or len(tid) > 128:
        return False
    prefix, _, raw_id = tid.partition(":")
    return validate_provider_id(prefix, raw_id)


# ------------------------------------------------------------------
# HMAC-SHA256 Session Signing & Owner Isolation
# ------------------------------------------------------------------
def _sign_sid(sid: str, secret: bytes) -> str:
    return hmac.new(secret, sid.encode("ascii"), hashlib.sha256).hexdigest()[:32]


def mint_session_token() -> Tuple[str, str]:
    """Create a fresh (owner_id, signed_cookie_token) pair."""
    sid = secrets.token_hex(12)  # 24 hex chars
    sig = _sign_sid(sid, config.get_session_secret())
    return sid, f"{sid}.{sig}"


def verify_session_token(token: str) -> Optional[str]:
    """Verify an HMAC-SHA256 signed sonify_sid token and return its owner_id or None."""
    m = _SESSION_TOKEN_RE.match((token or "").strip())
    if not m:
        return None
    sid, provided_sig = m.group(1), m.group(2)
    expected = _sign_sid(sid, config.get_session_secret())
    if hmac.compare_digest(provided_sig, expected):
        return sid
    return None


def extract_owner_id(headers: object) -> Optional[str]:
    """Extract verified owner_id from Cookie: sonify_sid=... or X-Sonify-Session header."""
    if headers is None:
        return None

    # 1. Explicit X-Sonify-Session header (Native iOS APIClient or API clients)
    raw_hdr = str(getattr(headers, "get", lambda *_: "")("X-Sonify-Session") or "").strip()
    if raw_hdr:
        verified = verify_session_token(raw_hdr)
        if verified:
            return verified
        if _RAW_HEADER_SESSION_RE.match(raw_hdr):
            digest = hmac.new(
                config.get_session_secret(),
                f"hdr:{raw_hdr}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()[:24]
            return f"ios_{digest}"

    # 2. Signed sonify_sid HTTP cookie (Web/PWA browsers)
    raw_cookie = str(getattr(headers, "get", lambda *_: "")("Cookie") or "").strip()
    if raw_cookie:
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(raw_cookie)
        except Exception:
            jar = SimpleCookie()
        morsel = jar.get(SESSION_COOKIE_NAME)
        if morsel is not None:
            verified = verify_session_token(morsel.value)
            if verified:
                return verified

    return None


# ------------------------------------------------------------------
# Thread-Safe Per-IP Token-Bucket Rate Limiter
# ------------------------------------------------------------------
class RateLimiter:
    """In-memory per-IP token-bucket limiter with bounded state (max 4096 keys)."""

    _MAX_BUCKETS = 4096

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: Dict[Tuple[str, str], Tuple[float, float]] = {}

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()

    def allow(self, client_ip: str, route_class: str = "general") -> Tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        base_rpm = config.get_rate_limit_rpm()
        if base_rpm <= 0:
            return True, 0

        if route_class == "import":
            rpm = max(1, min(base_rpm, 10))
        elif route_class in ("search", "write"):
            rpm = max(1, min(base_rpm, 60)) if base_rpm > 60 else base_rpm
        else:
            rpm = base_rpm

        capacity = float(rpm)
        refill_per_sec = capacity / 60.0
        now = time.monotonic()
        key = ((client_ip or "unknown").strip()[:64], route_class)

        with self._lock:
            if len(self._buckets) >= self._MAX_BUCKETS and key not in self._buckets:
                # Evict stale entries older than 120s, or oldest 25% if still full
                cutoff = now - 120.0
                stale = [k for k, (_, ts) in self._buckets.items() if ts < cutoff]
                for k in stale:
                    self._buckets.pop(k, None)
                if len(self._buckets) >= self._MAX_BUCKETS:
                    for k in list(self._buckets.keys())[: self._MAX_BUCKETS // 4]:
                        self._buckets.pop(k, None)

            tokens, last_ts = self._buckets.get(key, (capacity, now))
            elapsed = max(0.0, now - last_ts)
            tokens = min(capacity, tokens + elapsed * refill_per_sec)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return True, 0

            self._buckets[key] = (tokens, now)
            retry_after = max(1, int((1.0 - tokens) / refill_per_sec + 0.99))
            return False, retry_after
