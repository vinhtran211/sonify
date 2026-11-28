"""ThreadingHTTPServer REST API router and static frontend server for Sonify."""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import signal
import sys
import threading
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from backend import config, http_client, offline_catalog, security
from backend.db import Database
from backend.providers import lyrics as lyrics_provider, youtube_music
from backend.services import ios_install_service
from backend.services.catalog_service import CatalogService
from backend.services.library_service import LibraryError, LibraryService
from backend.services.stream_service import (
    StreamService,
    StreamUnavailableError,
    strip_known_stream_extension,
)

_MIME_OVERRIDES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


class SonifyHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        db: Database,
        catalog_service: CatalogService,
        stream_service: StreamService,
        library_service: LibraryService,
        frontend_dir: Path,
        quiet: bool = False,
    ) -> None:
        self.db = db
        self.catalog = catalog_service
        self.stream = stream_service
        self.library = library_service
        self.frontend_dir = frontend_dir.resolve()
        self.quiet = quiet
        self._concurrency_sem = threading.Semaphore(64)
        self.rate_limiter = security.RateLimiter()
        super().__init__(server_address, handler_class)


class SonifyRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: SonifyHTTPServer

    def setup(self) -> None:
        super().setup()
        try:
            self.request.settimeout(30.0)
        except OSError:
            pass

    def log_message(self, format: str, *args: Any) -> None:
        if not getattr(self.server, "quiet", False):
            sys.stderr.write(f"[Sonify] {self.address_string()} - {format % args}\n")

    # ------------------------------------------------------------------
    # Security, Session, CSRF & Rate-Limit helpers
    # ------------------------------------------------------------------
    def _is_https_request(self) -> bool:
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        return proto == "https"

    def _get_client_ip(self) -> str:
        if config.is_cloud_env():
            xff = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if xff:
                return xff
        if self.client_address:
            return str(self.client_address[0])
        return "unknown"

    def _has_session_cookie_header(self) -> bool:
        raw_cookie = (self.headers.get("Cookie") or "").strip()
        if not raw_cookie or security.SESSION_COOKIE_NAME not in raw_cookie:
            return False
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(raw_cookie)
        except Exception:
            return True
        return security.SESSION_COOKIE_NAME in jar

    def _resolve_owner_and_cookie(self, is_api: bool = True) -> str:
        """Resolve verified owner_id from signed cookie or X-Sonify-Session; mint cookie for browsers/tampered cookies."""
        verified = security.extract_owner_id(self.headers)
        if verified:
            return verified

        has_invalid_cookie = self._has_session_cookie_header()
        accept = (self.headers.get("Accept") or "").lower()
        is_browser = ("text/html" in accept) or bool(self.headers.get("Sec-Fetch-Mode"))
        require_session = config.is_session_required()

        if has_invalid_cookie or is_browser or (is_api and require_session):
            sid, token = security.mint_session_token()
            cookie_val = f"{security.SESSION_COOKIE_NAME}={token}; HttpOnly; SameSite=Lax; Path=/"
            if self._is_https_request():
                cookie_val += "; Secure"
            self._pending_set_cookie = cookie_val
            return sid

        return "default"

    def _check_api_token(self, path: str) -> bool:
        expected = config.get_api_token()
        if not expected:
            return True
        if path == "/api/health":
            return True
        auth = (self.headers.get("Authorization") or "").strip()
        provided = ""
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
        if not provided:
            provided = (self.headers.get("X-Sonify-Token") or "").strip()
        if not provided:
            return False
        return hmac.compare_digest(provided, expected)

    def _check_csrf(self) -> bool:
        sfs = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if sfs == "cross-site":
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        if origin.lower() == "null":
            return False
        try:
            parsed = urllib.parse.urlsplit(origin)
        except Exception:
            return False
        origin_norm = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        if origin_norm in config.get_allowed_origins():
            return True
        origin_host = parsed.netloc.lower()
        req_host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(",")[0].strip().lower()
        if not req_host:
            return False
        if origin_host == req_host:
            return True
        # Allow standard port omission (e.g. :443 / :80) when hostnames match
        origin_hostname = (parsed.hostname or "").lower()
        req_hostname = req_host.rsplit(":", 1)[0] if ":" in req_host and not req_host.startswith("[") else req_host
        if origin_hostname and origin_hostname == req_hostname and (parsed.port in (None, 80, 443)):
            return True
        return False

    def _send_security_headers(self, *, is_html: bool = False, is_artwork: bool = False) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if self._is_https_request():
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if is_html:
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' https: data: blob:; media-src 'self' https: blob:; "
                "connect-src 'self' https:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'",
            )
        elif is_artwork:
            self.send_header("Content-Security-Policy", "sandbox")
        pending_cookie = getattr(self, "_pending_set_cookie", None)
        if pending_cookie:
            self.send_header("Set-Cookie", pending_cookie)
            self._pending_set_cookie = None

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------
    def _send_json(
        self,
        payload: Any,
        status: int = 200,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Ad-Free", "true")
        self._send_security_headers()
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error_json(
        self,
        status: int,
        code: str,
        message: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self._send_json(
            {"error": {"code": code, "message": message}},
            status=status,
            extra_headers=extra_headers,
        )

    def _read_json_body(self) -> Dict[str, Any]:
        ctype = (self.headers.get("Content-Type") or "").strip().lower()
        if ctype and "application/json" not in ctype:
            raise LibraryError(
                "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json", 415
            )
        length_str = self.headers.get("Content-Length")
        if not length_str:
            return {}
        try:
            length = int(length_str)
        except ValueError:
            raise LibraryError("INVALID_BODY", "Invalid Content-Length header", 400)
        if length <= 0:
            return {}
        if length > 2 * 1024 * 1024:
            raise LibraryError("PAYLOAD_TOO_LARGE", "Request body exceeds 2MB limit", 413)
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            raise LibraryError("INVALID_JSON", "Malformed JSON request body", 400)
        if not isinstance(parsed, dict):
            raise LibraryError("INVALID_JSON", "JSON request body must be an object", 400)
        return parsed

    def _parse_url(self) -> tuple[str, Dict[str, list[str]]]:
        parsed = urllib.parse.urlsplit(self.path)
        return parsed.path, urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    @staticmethod
    def _qparam(qs: Dict[str, list[str]], key: str, default: str = "") -> str:
        vals = qs.get(key)
        if not vals:
            return default
        return vals[0]

    # ------------------------------------------------------------------
    # Static frontend serving
    # ------------------------------------------------------------------
    def _serve_static(self, raw_path: str, head_only: bool = False) -> None:
        rel = urllib.parse.unquote(raw_path).lstrip("/")
        if not rel or rel.endswith("/"):
            rel = "index.html"

        target = (self.server.frontend_dir / rel).resolve()
        # Prevent path traversal outside frontend_dir
        try:
            target.relative_to(self.server.frontend_dir)
        except ValueError:
            self._send_error_json(403, "FORBIDDEN", "Access denied")
            return

        if not target.is_file():
            # SPA fallback for client routes without a file extension
            if "." not in Path(rel).name:
                target = (self.server.frontend_dir / "index.html").resolve()
            else:
                self._send_error_json(404, "NOT_FOUND", f"Asset '{rel}' not found")
                return

        if not target.is_file():
            self._send_error_json(404, "NOT_FOUND", "Frontend index.html not found")
            return

        suffix = target.suffix.lower()
        ctype = _MIME_OVERRIDES.get(suffix) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache" if suffix in (".html", ".js", ".css") else "public, max-age=86400")
        self.send_header("X-Ad-Free", "true")
        self._send_security_headers(is_html=(suffix == ".html"))
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    # ------------------------------------------------------------------
    # HTTP method entrypoints
    # ------------------------------------------------------------------
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self._send_security_headers()
        self.end_headers()

    def do_HEAD(self) -> None:
        self._dispatch(head_only=True)

    def do_GET(self) -> None:
        self._dispatch(head_only=False)

    def do_POST(self) -> None:
        self._dispatch(head_only=False)

    def do_PATCH(self) -> None:
        self._dispatch(head_only=False)

    def do_DELETE(self) -> None:
        self._dispatch(head_only=False)

    # ------------------------------------------------------------------
    # Router
    # ------------------------------------------------------------------
    def _dispatch(self, head_only: bool = False) -> None:
        self._pending_set_cookie = None
        if not self.server._concurrency_sem.acquire(blocking=False):
            self._send_error_json(503, "SERVICE_UNAVAILABLE", "Server busy, please retry shortly")
            return

        path, qs = self._parse_url()
        method = "GET" if self.command == "HEAD" else self.command

        try:
            if not path.startswith("/api/"):
                if method == "GET":
                    self._resolve_owner_and_cookie(is_api=False)
                    self._serve_static(path, head_only=head_only)
                else:
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Method not allowed")
                return

            # Per-IP rate limiting (exempt /api/health probes)
            if path != "/api/health":
                if path == "/api/playlists/import":
                    rclass = "import"
                elif path == "/api/search":
                    rclass = "search"
                elif method in ("POST", "PATCH", "DELETE"):
                    rclass = "write"
                elif path.startswith("/api/stream/"):
                    rclass = "stream"
                else:
                    rclass = "general"
                allowed, retry_after = self.server.rate_limiter.allow(
                    self._get_client_ip(), rclass
                )
                if not allowed:
                    self._send_error_json(
                        429,
                        "RATE_LIMITED",
                        "Too many requests. Please slow down.",
                        extra_headers={"Retry-After": str(retry_after)},
                    )
                    return

            # Optional single-owner API bearer token guard
            if not self._check_api_token(path):
                self._send_error_json(401, "UNAUTHORIZED", "Missing or invalid API token")
                return

            # CSRF guard for state-changing requests
            if method in ("POST", "PATCH", "DELETE") and not self._check_csrf():
                self._send_error_json(403, "FORBIDDEN", "Cross-origin write request rejected")
                return

            owner_id = self._resolve_owner_and_cookie(is_api=True)

            # /api/health
            if path == "/api/health":
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET for /api/health")
                    return
                cloud_mode = config.is_cloud_env()
                self._send_json(
                    {
                        "status": "ok",
                        "version": config.APP_VERSION,
                        "adFree": True,
                        "dbReady": self.server.db.is_ready(),
                        "ytDlpAvailable": config.has_yt_dlp(),
                        "ytDlpConfigured": youtube_music.has_cloud_youtube_credentials(),
                        "ffmpegAvailable": config.has_ffmpeg(),
                        "cloudMode": cloud_mode,
                        "renderRegion": os.environ.get("RENDER_REGION") or ("cloud" if cloud_mode else "local"),
                        "offlineMode": http_client.is_offline_mode(),
                        "timestamp": int(time.time()),
                    }
                )
                return

            # /api/ios/* (Direct iPhone install profile, OTA manifest, AltStore source, install metadata)
            if path.startswith("/api/ios/"):
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                    return
                origin = ios_install_service.resolve_public_origin(self.headers)
                if path == "/api/ios/install-info":
                    self._send_json(ios_install_service.get_install_info(origin))
                    return
                if path == "/api/ios/install.mobileconfig":
                    payload = ios_install_service.build_mobileconfig(origin)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-apple-aspen-config")
                    self.send_header("Content-Disposition", 'attachment; filename="Sonify.mobileconfig"')
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Ad-Free", "true")
                    self._send_security_headers()
                    self.end_headers()
                    if not head_only:
                        self.wfile.write(payload)
                    return
                if path == "/api/ios/manifest.plist":
                    manifest = ios_install_service.build_ota_manifest(origin)
                    if manifest is None:
                        self._send_error_json(
                            404,
                            "IPA_NOT_CONFIGURED",
                            "Direct signed IPA OTA install is not configured (set SONIFY_IOS_IPA_URL).",
                        )
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/xml; charset=utf-8")
                    self.send_header("Content-Length", str(len(manifest)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Ad-Free", "true")
                    self._send_security_headers()
                    self.end_headers()
                    if not head_only:
                        self.wfile.write(manifest)
                    return
                if path == "/api/ios/altstore-source.json":
                    self._send_json(ios_install_service.build_altstore_source(origin))
                    return
                self._send_error_json(404, "NOT_FOUND", f"Unknown iOS endpoint '{path}'")
                return

            # /api/artwork/{slug}
            if path.startswith("/api/artwork/"):
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                    return
                slug = urllib.parse.unquote(path[len("/api/artwork/") :]).strip("/")
                svg = offline_catalog.render_artwork_svg(slug)
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
                self.send_header("Content-Length", str(len(svg)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("X-Ad-Free", "true")
                self._send_security_headers(is_artwork=True)
                self.end_headers()
                if not head_only:
                    self.wfile.write(svg)
                return

            # /api/home
            if path == "/api/home":
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                    return
                region = self._qparam(qs, "region", "global")
                refresh = self._qparam(qs, "refresh", "0") in ("1", "true", "yes")
                self._send_json(
                    self.server.catalog.get_home(region=region, refresh=refresh, owner_id=owner_id)
                )
                return

            # /api/search
            if path == "/api/search":
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                    return
                q = self._qparam(qs, "q", "")
                stype = self._qparam(qs, "type", "all")
                try:
                    limit = int(self._qparam(qs, "limit", "20") or "20")
                except ValueError:
                    limit = 20
                self._send_json(
                    self.server.catalog.search(q, search_type=stype, limit=limit, owner_id=owner_id)
                )
                return

            # /api/artist/{artist_id}
            if path.startswith("/api/artist/"):
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                    return
                artist_id = urllib.parse.unquote(path[len("/api/artist/") :]).strip("/")
                data = self.server.catalog.get_artist(artist_id, owner_id=owner_id)
                if not data:
                    self._send_error_json(404, "NOT_FOUND", f"Artist '{artist_id}' not found")
                    return
                self._send_json(data)
                return

            # /api/album/{album_id}
            if path.startswith("/api/album/"):
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                    return
                album_id = urllib.parse.unquote(path[len("/api/album/") :]).strip("/")
                data = self.server.catalog.get_album(album_id, owner_id=owner_id)
                if not data:
                    self._send_error_json(404, "NOT_FOUND", f"Album '{album_id}' not found")
                    return
                self._send_json(data)
                return

            # /api/stream/{track_id}/info and /api/stream/{track_id}
            if path.startswith("/api/stream/"):
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET or HEAD")
                    return
                rest = path[len("/api/stream/") :]
                if rest.endswith("/info"):
                    track_id = urllib.parse.unquote(rest[: -len("/info")]).strip("/")
                    if not track_id:
                        self._send_error_json(400, "INVALID_TRACK", "Missing trackId")
                        return
                    raw_debug = self._qparam(qs, "debug", "0").strip().lower() in ("1", "true", "yes")
                    expected_dbg = config.get_debug_token()
                    provided_dbg = (self.headers.get("X-Sonify-Debug-Token") or "").strip()
                    valid_dbg_token = bool(
                        expected_dbg and provided_dbg and hmac.compare_digest(provided_dbg, expected_dbg)
                    )
                    debug = raw_debug and (config.is_debug_allowed() or valid_dbg_token)
                    quality = self._qparam(qs, "quality", "auto").strip().lower() or "auto"
                    fmt = self._qparam(qs, "format", "").strip().lower()
                    prewarm = self._qparam(qs, "prewarm", "0").strip().lower() in ("1", "true", "yes")
                    refresh = self._qparam(qs, "refresh", "0").strip().lower() in ("1", "true", "yes")
                    info_kwargs: Dict[str, Any] = {
                        "debug": debug,
                        "quality": quality,
                        "prewarm": prewarm,
                        "force_refresh": refresh,
                    }
                    if fmt:
                        info_kwargs["target_format"] = fmt
                    self._send_json(
                        self.server.stream.get_stream_info(track_id, **info_kwargs)
                    )
                    return
                track_id = strip_known_stream_extension(urllib.parse.unquote(rest).strip("/"))
                if not track_id:
                    self._send_error_json(400, "INVALID_TRACK", "Missing trackId")
                    return
                quality = self._qparam(qs, "quality", "auto").strip().lower() or "auto"
                fmt = self._qparam(qs, "format", "").strip().lower()
                stream_kwargs: Dict[str, Any] = {"head_only": head_only}
                if quality != "auto":
                    stream_kwargs["quality"] = quality
                if fmt:
                    stream_kwargs["target_format"] = fmt
                self.server.stream.stream_to_handler(self, track_id, **stream_kwargs)
                return

            # /api/lyrics
            if path == "/api/lyrics":
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                    return
                track_id = self._qparam(qs, "trackId", "")
                title = self._qparam(qs, "title", "")
                artist = self._qparam(qs, "artist", "")
                album = self._qparam(qs, "album", "")
                refresh = self._qparam(qs, "refresh", "0") in ("1", "true", "yes")
                try:
                    duration = int(float(self._qparam(qs, "duration", "0") or "0"))
                except ValueError:
                    duration = 0
                payload = lyrics_provider.get_lyrics(
                    self.server.db,
                    track_id=track_id,
                    title=title,
                    artist=artist,
                    album=album,
                    duration=duration,
                    refresh=refresh,
                )
                self._send_json(payload)
                return

            # /api/radio (Song / Artist Radio & Autoplay seed)
            if path == "/api/radio":
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                    return
                track_id = self._qparam(qs, "trackId", "")
                artist_id = self._qparam(qs, "artistId", "")
                try:
                    limit = int(self._qparam(qs, "limit", "25") or "25")
                except ValueError:
                    limit = 25
                self._send_json(
                    self.server.catalog.get_radio(
                        track_id=track_id, artist_id=artist_id, limit=limit, owner_id=owner_id
                    )
                )
                return

            # /api/made-for-you (Daily Mix 1/2, Discover Weekly, Release Radar)
            if path == "/api/made-for-you":
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                    return
                region = self._qparam(qs, "region", "global")
                self._send_json(
                    self.server.catalog.get_made_for_you(region=region, owner_id=owner_id)
                )
                return

            # /api/genre/{slug} (12 Spotify Browse Genre/Mood Hubs)
            if path.startswith("/api/genre/"):
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                    return
                slug = urllib.parse.unquote(path[len("/api/genre/") :]).strip("/")
                region = self._qparam(qs, "region", "global")
                data = self.server.catalog.get_genre_hub(slug, region=region, owner_id=owner_id)
                if not data:
                    self._send_error_json(404, "NOT_FOUND", f"Genre '{slug}' not found")
                    return
                self._send_json(data)
                return

            # /api/track/{track_id} (Shareable Single-Track Deep Link + Related Radio)
            if path.startswith("/api/track/"):
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                    return
                track_id = urllib.parse.unquote(path[len("/api/track/") :]).strip("/")
                data = self.server.catalog.get_track_detail(track_id, owner_id=owner_id)
                if not data:
                    self._send_error_json(404, "NOT_FOUND", f"Track '{track_id}' not found")
                    return
                self._send_json(data)
                return

            # /api/stats (Listening Stats / Sound Capsule)
            if path == "/api/stats":
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                    return
                self._send_json(self.server.library.get_stats(owner_id=owner_id))
                return

            # /api/library/likes & /api/library/likes/{track_id}
            if path == "/api/library/likes":
                if method == "GET":
                    self._send_json(self.server.library.get_likes(owner_id=owner_id))
                    return
                if method == "POST":
                    body = self._read_json_body()
                    self._send_json(
                        self.server.library.like_track(body, owner_id=owner_id), status=201
                    )
                    return
                self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET or POST")
                return

            if path.startswith("/api/library/likes/"):
                if method != "DELETE":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use DELETE")
                    return
                track_id = urllib.parse.unquote(path[len("/api/library/likes/") :]).strip("/")
                self._send_json(self.server.library.unlike_track(track_id, owner_id=owner_id))
                return

            # /api/library/artists & /api/library/artists/{artist_id}
            if path == "/api/library/artists":
                if method == "GET":
                    self._send_json(self.server.library.get_followed_artists(owner_id=owner_id))
                    return
                if method == "POST":
                    body = self._read_json_body()
                    self._send_json(
                        self.server.library.follow_artist(body, owner_id=owner_id), status=201
                    )
                    return
                self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET or POST")
                return

            if path.startswith("/api/library/artists/"):
                if method != "DELETE":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use DELETE")
                    return
                artist_id = urllib.parse.unquote(path[len("/api/library/artists/") :]).strip("/")
                self._send_json(self.server.library.unfollow_artist(artist_id, owner_id=owner_id))
                return

            # /api/library/albums & /api/library/albums/{album_id}
            if path == "/api/library/albums":
                if method == "GET":
                    self._send_json(self.server.library.get_saved_albums(owner_id=owner_id))
                    return
                if method == "POST":
                    body = self._read_json_body()
                    self._send_json(
                        self.server.library.save_album(body, owner_id=owner_id), status=201
                    )
                    return
                self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET or POST")
                return

            if path.startswith("/api/library/albums/"):
                if method != "DELETE":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use DELETE")
                    return
                album_id = urllib.parse.unquote(path[len("/api/library/albums/") :]).strip("/")
                self._send_json(self.server.library.unsave_album(album_id, owner_id=owner_id))
                return

            # /api/library/pins & /api/library/pins/{item_type}/{item_id}
            if path == "/api/library/pins":
                if method == "GET":
                    self._send_json(self.server.library.get_pins(owner_id=owner_id))
                    return
                if method == "POST":
                    body = self._read_json_body()
                    self._send_json(
                        self.server.library.pin_item(body, owner_id=owner_id), status=201
                    )
                    return
                self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET or POST")
                return

            if path.startswith("/api/library/pins/"):
                if method != "DELETE":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use DELETE")
                    return
                sub_pin = path[len("/api/library/pins/") :].strip("/")
                pin_parts = sub_pin.split("/", 1)
                if len(pin_parts) != 2:
                    self._send_error_json(400, "INVALID_PIN", "Use /api/library/pins/{itemType}/{itemId}")
                    return
                item_type = urllib.parse.unquote(pin_parts[0])
                item_id = urllib.parse.unquote(pin_parts[1])
                self._send_json(
                    self.server.library.unpin_item(item_type, item_id, owner_id=owner_id)
                )
                return

            # /api/playlists/import
            if path == "/api/playlists/import":
                if method != "POST":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use POST")
                    return
                body = self._read_json_body()
                self._send_json(
                    self.server.library.import_playlist(
                        body, catalog_service=self.server.catalog, owner_id=owner_id
                    ),
                    status=201,
                )
                return

            # /api/playlists
            if path == "/api/playlists":
                if method == "GET":
                    self._send_json(self.server.library.list_playlists(owner_id=owner_id))
                    return
                if method == "POST":
                    body = self._read_json_body()
                    self._send_json(
                        self.server.library.create_playlist(body, owner_id=owner_id), status=201
                    )
                    return
                self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET or POST")
                return

            if path.startswith("/api/playlists/"):
                sub = path[len("/api/playlists/") :].strip("/")
                parts = [urllib.parse.unquote(p) for p in sub.split("/") if p]
                if len(parts) == 1:
                    pid = parts[0]
                    if method == "GET":
                        self._send_json(self.server.library.get_playlist(pid, owner_id=owner_id))
                        return
                    if method == "PATCH":
                        body = self._read_json_body()
                        self._send_json(
                            self.server.library.update_playlist(pid, body, owner_id=owner_id)
                        )
                        return
                    if method == "DELETE":
                        self._send_json(self.server.library.delete_playlist(pid, owner_id=owner_id))
                        return
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Method not allowed")
                    return

                if len(parts) == 2 and parts[1] == "recommendations":
                    pid = parts[0]
                    if method != "GET":
                        self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                        return
                    try:
                        limit = int(self._qparam(qs, "limit", "10") or "10")
                    except ValueError:
                        limit = 10
                    recs = self.server.catalog.get_playlist_recommendations(
                        pid, limit=limit, owner_id=owner_id
                    )
                    if recs is None:
                        self._send_error_json(404, "NOT_FOUND", f"Playlist '{pid}' not found")
                        return
                    self._send_json(recs)
                    return

                if len(parts) == 2 and parts[1] == "duplicate":
                    pid = parts[0]
                    if method != "POST":
                        self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use POST")
                        return
                    body = self._read_json_body()
                    self._send_json(
                        self.server.library.duplicate_playlist(pid, body, owner_id=owner_id),
                        status=201,
                    )
                    return

                if len(parts) == 2 and parts[1] == "tracks":
                    pid = parts[0]
                    if method == "POST":
                        body = self._read_json_body()
                        self._send_json(
                            self.server.library.add_track_to_playlist(pid, body, owner_id=owner_id),
                            status=201,
                        )
                        return
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use POST")
                    return

                if len(parts) == 3 and parts[1] == "tracks":
                    pid = parts[0]
                    target = parts[2]
                    if target == "reorder" and method == "PATCH":
                        body = self._read_json_body()
                        self._send_json(
                            self.server.library.reorder_playlist_tracks(pid, body, owner_id=owner_id)
                        )
                        return
                    if method == "DELETE":
                        self._send_json(
                            self.server.library.remove_track_from_playlist(
                                pid, target, owner_id=owner_id
                            )
                        )
                        return
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Method not allowed")
                    return

                self._send_error_json(404, "NOT_FOUND", "Playlist route not found")
                return

            # /api/history
            if path == "/api/history":
                if method == "GET":
                    try:
                        limit = int(self._qparam(qs, "limit", "50") or "50")
                    except ValueError:
                        limit = 50
                    self._send_json(self.server.library.get_history(limit=limit, owner_id=owner_id))
                    return
                if method == "POST":
                    body = self._read_json_body()
                    self._send_json(
                        self.server.library.record_history(body, owner_id=owner_id), status=201
                    )
                    return
                if method == "DELETE":
                    self._send_json(self.server.library.clear_history(owner_id=owner_id))
                    return
                self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET, POST, or DELETE")
                return

            # /api/settings
            if path == "/api/settings":
                if method == "GET":
                    self._send_json(self.server.library.get_settings(owner_id=owner_id))
                    return
                if method == "PATCH":
                    body = self._read_json_body()
                    self._send_json(self.server.library.update_settings(body, owner_id=owner_id))
                    return
                self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET or PATCH")
                return

            self._send_error_json(404, "NOT_FOUND", f"API endpoint '{path}' not found")

        except LibraryError as exc:
            self._send_error_json(exc.status, exc.code, exc.message)
        except StreamUnavailableError as exc:
            self._send_error_json(exc.status, exc.code, exc.message)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            sys.stderr.write(f"[Sonify] Unhandled error on {self.command} {path}: {exc!r}\n")
            self._send_error_json(500, "INTERNAL_ERROR", "Internal server error")
        finally:
            self.server._concurrency_sem.release()


def create_server(
    host: str = config.DEFAULT_HOST,
    port: int = config.DEFAULT_PORT,
    db_path: Optional[str | Path] = None,
    offline: Optional[bool] = None,
    quiet: bool = False,
) -> SonifyHTTPServer:
    if offline is not None:
        http_client.set_offline_mode(offline)
    db = Database(db_path or config.DEFAULT_DB_PATH)
    catalog_service = CatalogService(db)
    stream_service = StreamService(db)
    library_service = LibraryService(db)
    return SonifyHTTPServer(
        (host, port),
        SonifyRequestHandler,
        db=db,
        catalog_service=catalog_service,
        stream_service=stream_service,
        library_service=library_service,
        frontend_dir=config.FRONTEND_DIR,
        quiet=quiet,
    )


def run_server(
    host: str = config.DEFAULT_HOST,
    port: int = config.DEFAULT_PORT,
    db_path: Optional[str | Path] = None,
    offline: Optional[bool] = None,
    quiet: bool = False,
) -> None:
    httpd = create_server(host=host, port=port, db_path=db_path, offline=offline, quiet=quiet)
    actual_host, actual_port = httpd.server_address[:2]
    shutdown_started = threading.Event()

    def _trigger_shutdown(signum, frame):
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        # Call httpd.shutdown() from a helper thread so serve_forever() exits cleanly without deadlock
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _trigger_shutdown)
        signal.signal(signal.SIGINT, _trigger_shutdown)

    if not quiet:
        mode_note = "OFFLINE DEMO" if http_client.is_offline_mode() else "ONLINE + AUTO-FALLBACK"
        print(
            f"Sonify v{config.APP_VERSION} running at http://{actual_host}:{actual_port}/ "
            f"({mode_note}, 100% Ad-Free)",
            flush=True,
        )
        print("Press Ctrl+C to stop.", flush=True)

    # Pre-warm shared Home catalog in background on container boot so first page load is instant
    if not http_client.is_offline_mode():
        threading.Thread(
            target=lambda: httpd.catalog.get_home("global"),
            name="home-boot-prewarm",
            daemon=True,
        ).start()

    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        if not quiet:
            print("\nShutting down Sonify...", flush=True)
        httpd.server_close()
