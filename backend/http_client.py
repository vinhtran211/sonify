"""Pure-stdlib HTTP/HTTPS client with gzip, timeouts, redirect handling, and streaming."""

from __future__ import annotations

import gzip
import json
import os
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any, Dict, Optional, Tuple

from backend import config

try:
    import certifi  # type: ignore
except ImportError:  # pragma: no cover
    certifi = None

try:
    import requests  # type: ignore
    from requests.adapters import HTTPAdapter  # type: ignore
except ImportError:  # pragma: no cover
    requests = None
    HTTPAdapter = None


_POOLED_SESSION_LOCK = threading.Lock()
_POOLED_SESSION: Any = None


def _get_pooled_session() -> Any:
    """Return a thread-safe Keep-Alive requests.Session if available and enabled."""
    global _POOLED_SESSION
    if requests is None or HTTPAdapter is None:
        return None
    if os.environ.get("SONIFY_USE_POOLED_HTTP", "1").strip().lower() in ("0", "false", "no"):
        return None
    if _POOLED_SESSION is not None:
        return _POOLED_SESSION
    with _POOLED_SESSION_LOCK:
        if _POOLED_SESSION is None:
            sess = requests.Session()
            adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=0)
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            if certifi is not None:
                try:
                    sess.verify = certifi.where()
                except Exception:
                    pass
            _POOLED_SESSION = sess
        return _POOLED_SESSION


class OfflineModeError(OSError):
    """Raised when outbound network requests are disabled via offline mode."""


_OFFLINE_OVERRIDE: Optional[bool] = None


def set_offline_mode(enabled: Optional[bool]) -> None:
    """Explicitly enable/disable offline mode at runtime (None uses env var)."""
    global _OFFLINE_OVERRIDE
    _OFFLINE_OVERRIDE = enabled


def is_offline_mode() -> bool:
    if _OFFLINE_OVERRIDE is not None:
        return _OFFLINE_OVERRIDE
    return config.is_offline_env()


def _build_ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            pass
    return ssl.create_default_context()


_SSL_CONTEXT = _build_ssl_context()


def _default_headers(extra: Optional[Dict[str, str]] = None, accept_encoding: bool = True) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "User-Agent": config.BROWSER_USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
    }
    if accept_encoding:
        headers["Accept-Encoding"] = "gzip, deflate"
    if extra:
        for k, v in extra.items():
            if v is not None:
                headers[k] = v
    return headers


def _decode_body(raw: bytes, content_encoding: str) -> bytes:
    enc = (content_encoding or "").lower()
    if "gzip" in enc:
        try:
            return gzip.decompress(raw)
        except Exception:
            return raw
    if "deflate" in enc:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            try:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except Exception:
                return raw
    return raw


def fetch_bytes(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    data: Optional[bytes] = None,
    json_body: Any = None,
    timeout: float = config.PROVIDER_TIMEOUT,
) -> Tuple[int, Dict[str, str], bytes]:
    if is_offline_mode():
        raise OfflineModeError("Network access disabled in offline mode")

    req_headers = _default_headers(headers, accept_encoding=True)
    body = data
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    sess = _get_pooled_session()
    if sess is not None:
        try:
            resp = sess.request(
                method=method,
                url=url,
                headers=req_headers,
                data=body,
                timeout=timeout,
                allow_redirects=True,
            )
            status = int(resp.status_code)
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            if status >= 400:
                raise urllib.error.HTTPError(
                    url, status, resp.reason or f"HTTP {status}", resp.headers, None
                )
            return status, resp_headers, resp.content
        except urllib.error.HTTPError:
            raise
        except Exception as exc:
            raise urllib.error.URLError(str(exc)) from exc

    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as resp:
        status = getattr(resp, "status", resp.getcode())
        resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        raw = resp.read()
        decoded = _decode_body(raw, resp_headers.get("content-encoding", ""))
        return status, resp_headers, decoded


def fetch_text(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    data: Optional[bytes] = None,
    json_body: Any = None,
    timeout: float = config.PROVIDER_TIMEOUT,
) -> str:
    _, _, raw = fetch_bytes(
        url, method=method, headers=headers, data=data, json_body=json_body, timeout=timeout
    )
    return raw.decode("utf-8", errors="replace")


def fetch_json(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    data: Optional[bytes] = None,
    json_body: Any = None,
    timeout: float = config.PROVIDER_TIMEOUT,
) -> Any:
    hdrs = {"Accept": "application/json, text/plain, */*"}
    if headers:
        hdrs.update(headers)
    text = fetch_text(
        url, method=method, headers=hdrs, data=data, json_body=json_body, timeout=timeout
    )
    return json.loads(text)


def sniff_media_mime(head_bytes: bytes, header_ctype: str = "") -> str:
    """Sniff true audio/video MIME type from magic bytes when upstream sends application/octet-stream."""
    raw_ct = (header_ctype or "").split(";")[0].strip().lower()
    if head_bytes:
        if head_bytes.startswith(b"fLaC"):
            return "audio/flac"
        if len(head_bytes) >= 8 and head_bytes[4:8] == b"ftyp":
            return "video/mp4" if raw_ct == "video/mp4" else "audio/mp4"
        if head_bytes.startswith(b"ID3") or (
            len(head_bytes) >= 2 and head_bytes[0] == 0xFF and (head_bytes[1] & 0xE0) == 0xE0
        ):
            return "audio/mpeg"
        if head_bytes.startswith(b"OggS"):
            return "audio/ogg"
        if head_bytes.startswith(b"\x1a\x45\xdf\xa3"):
            return "video/webm" if raw_ct == "video/webm" else "audio/webm"
        if len(head_bytes) >= 12 and head_bytes[:4] == b"RIFF" and head_bytes[8:12] == b"WAVE":
            return "audio/wav"
    if raw_ct.startswith("audio/") or raw_ct in ("video/mp4", "video/webm"):
        return raw_ct
    return ""


def _is_valid_media_content_type(content_type: str, sample: bytes) -> bool:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct:
        if ct.startswith("audio/") or ct in ("video/mp4", "video/webm"):
            return True
        if ct in ("application/octet-stream", "binary/octet-stream"):
            stripped = (sample or b"").lstrip()
            return bool(sample) and not stripped.startswith((b"<", b"{", b"["))
        return False
    if not sample:
        return False
    stripped = sample.lstrip()
    return not stripped.startswith((b"<", b"{", b"["))


def _extract_total_size(status: int, resp_headers: Dict[str, str]) -> Optional[int]:
    cr = resp_headers.get("content-range", "")
    if "/" in cr:
        total_part = cr.rsplit("/", 1)[-1].strip()
        if total_part.isdigit():
            return int(total_part)
    cl = resp_headers.get("content-length", "").strip()
    if cl.isdigit():
        return int(cl)
    return None


def probe_url(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = config.STREAM_PROBE_TIMEOUT,
    verify_tail: bool = False,
    min_total_bytes: int = 0,
    initial_range: str = "bytes=0-",
) -> Tuple[bool, int, Dict[str, str], str]:
    """Probe a media URL with open-ended Range: bytes=0- (and optional tail/size check) to prove real playability."""
    if is_offline_mode():
        return False, 0, {}, url

    def _run_probe(target_url: str, range_val: Optional[str]) -> Tuple[int, Dict[str, str], str, bytes]:
        req_headers = _default_headers(headers, accept_encoding=False)
        req_headers["Accept-Encoding"] = "identity"
        if range_val is not None:
            req_headers["Range"] = range_val
        req = urllib.request.Request(target_url, headers=req_headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as resp:
            st = getattr(resp, "status", resp.getcode())
            rh = {k.lower(): v for k, v in resp.headers.items()}
            f_url = resp.geturl()
            chunk = resp.read(1024)
            return st, rh, f_url, chunk

    try:
        status, resp_headers, final_url, sample = _run_probe(url, initial_range)
    except urllib.error.HTTPError as exc:
        code = exc.code
        try:
            exc.close()
        except Exception:
            pass
        # Some servers reject Range: bytes=0- with 405 or 416; retry plain GET
        if code in (405, 416):
            try:
                status, resp_headers, final_url, sample = _run_probe(url, None)
            except Exception:
                return False, code, {}, url
        else:
            return False, code, {}, url
    except Exception:
        return False, 0, {}, url

    if status not in (200, 206) or not sample:
        return False, status, resp_headers, final_url

    if not _is_valid_media_content_type(resp_headers.get("content-type", ""), sample):
        return False, status, resp_headers, final_url

    # Upgrade application/octet-stream to true sniffed audio MIME (e.g. fLaC -> audio/flac)
    raw_ct = resp_headers.get("content-type", "").split(";")[0].strip().lower()
    sniffed = sniff_media_mime(sample, raw_ct)
    if sniffed and (not raw_ct or raw_ct in ("application/octet-stream", "binary/octet-stream")):
        resp_headers["content-type"] = sniffed

    total = _extract_total_size(status, resp_headers)
    if min_total_bytes > 0 and total is not None and total < min_total_bytes:
        return False, status, resp_headers, final_url

    if verify_tail:
        if total is not None and total > 1_200_000:
            tail_start = max(0, total - 1024)
            tail_end = total - 1
            try:
                t_status, _, _, t_sample = _run_probe(
                    final_url, f"bytes={tail_start}-{tail_end}"
                )
                if t_status not in (200, 206) or not t_sample:
                    return False, t_status, resp_headers, final_url
            except urllib.error.HTTPError as exc:
                t_code = exc.code
                try:
                    exc.close()
                except Exception:
                    pass
                return False, t_code, resp_headers, final_url
            except Exception:
                return False, 0, resp_headers, final_url

    return True, status, resp_headers, final_url


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=_SSL_CONTEXT),
    _NoRedirectHandler(),
)


def open_stream(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    range_header: Optional[str] = None,
    method: str = "GET",
    timeout: float = config.STREAM_READ_TIMEOUT,
    max_redirects: int = 6,
) -> Tuple[int, Dict[str, str], Any]:
    """Open an upstream audio stream, following redirects while preserving Range/UA headers."""
    if is_offline_mode():
        raise OfflineModeError("Network access disabled in offline mode")

    current_url = url
    req_headers = _default_headers(headers, accept_encoding=False)
    req_headers["Accept-Encoding"] = "identity"
    if range_header:
        req_headers["Range"] = range_header

    for _ in range(max_redirects + 1):
        req = urllib.request.Request(current_url, headers=req_headers, method=method)
        try:
            resp = _NO_REDIRECT_OPENER.open(req, timeout=timeout)
            status = getattr(resp, "status", resp.getcode())
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return status, resp_headers, resp
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                location = exc.headers.get("Location")
                exc.close()
                if not location:
                    raise
                current_url = urllib.parse.urljoin(current_url, location)
                continue
            raise

    raise urllib.error.URLError("Too many redirects while opening stream")
