"""ThreadingHTTPServer REST API router and static frontend server for Sonify."""

from __future__ import annotations

import json
import mimetypes
import os
import signal
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from backend import config, http_client, offline_catalog
from backend.db import Database
from backend.providers import lyrics as lyrics_provider, youtube_music
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
        super().__init__(server_address, handler_class)


class SonifyRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: SonifyHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        if not getattr(self.server, "quiet", False):
            sys.stderr.write(f"[Sonify] {self.address_string()} - {format % args}\n")

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------
    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Ad-Free", "true")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error_json(self, status: int, code: str, message: str) -> None:
        self._send_json({"error": {"code": code, "message": message}}, status=status)

    def _read_json_body(self) -> Dict[str, Any]:
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
        path, qs = self._parse_url()
        method = "GET" if self.command == "HEAD" else self.command

        try:
            if not path.startswith("/api/"):
                if method == "GET":
                    self._serve_static(path, head_only=head_only)
                else:
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Method not allowed")
                return

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
                        "cloudMode": cloud_mode,
                        "renderRegion": os.environ.get("RENDER_REGION") or ("cloud" if cloud_mode else "local"),
                        "offlineMode": http_client.is_offline_mode(),
                        "timestamp": int(time.time()),
                    }
                )
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
                self._send_json(self.server.catalog.get_home(region=region, refresh=refresh))
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
                self._send_json(self.server.catalog.search(q, search_type=stype, limit=limit))
                return

            # /api/artist/{artist_id}
            if path.startswith("/api/artist/"):
                if method != "GET":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET")
                    return
                artist_id = urllib.parse.unquote(path[len("/api/artist/") :]).strip("/")
                data = self.server.catalog.get_artist(artist_id)
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
                data = self.server.catalog.get_album(album_id)
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
                    debug = self._qparam(qs, "debug", "0").strip().lower() in ("1", "true", "yes")
                    self._send_json(self.server.stream.get_stream_info(track_id, debug=debug))
                    return
                track_id = strip_known_stream_extension(urllib.parse.unquote(rest).strip("/"))
                if not track_id:
                    self._send_error_json(400, "INVALID_TRACK", "Missing trackId")
                    return
                self.server.stream.stream_to_handler(self, track_id, head_only=head_only)
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

            # /api/library/likes & /api/library/likes/{track_id}
            if path == "/api/library/likes":
                if method == "GET":
                    self._send_json(self.server.library.get_likes())
                    return
                if method == "POST":
                    body = self._read_json_body()
                    self._send_json(self.server.library.like_track(body), status=201)
                    return
                self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET or POST")
                return

            if path.startswith("/api/library/likes/"):
                if method != "DELETE":
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use DELETE")
                    return
                track_id = urllib.parse.unquote(path[len("/api/library/likes/") :]).strip("/")
                self._send_json(self.server.library.unlike_track(track_id))
                return

            # /api/playlists
            if path == "/api/playlists":
                if method == "GET":
                    self._send_json(self.server.library.list_playlists())
                    return
                if method == "POST":
                    body = self._read_json_body()
                    self._send_json(self.server.library.create_playlist(body), status=201)
                    return
                self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET or POST")
                return

            if path.startswith("/api/playlists/"):
                sub = path[len("/api/playlists/") :].strip("/")
                parts = [urllib.parse.unquote(p) for p in sub.split("/") if p]
                if len(parts) == 1:
                    pid = parts[0]
                    if method == "GET":
                        self._send_json(self.server.library.get_playlist(pid))
                        return
                    if method == "PATCH":
                        body = self._read_json_body()
                        self._send_json(self.server.library.update_playlist(pid, body))
                        return
                    if method == "DELETE":
                        self._send_json(self.server.library.delete_playlist(pid))
                        return
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Method not allowed")
                    return

                if len(parts) == 2 and parts[1] == "tracks":
                    pid = parts[0]
                    if method == "POST":
                        body = self._read_json_body()
                        self._send_json(self.server.library.add_track_to_playlist(pid, body), status=201)
                        return
                    self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use POST")
                    return

                if len(parts) == 3 and parts[1] == "tracks":
                    pid = parts[0]
                    target = parts[2]
                    if target == "reorder" and method == "PATCH":
                        body = self._read_json_body()
                        self._send_json(self.server.library.reorder_playlist_tracks(pid, body))
                        return
                    if method == "DELETE":
                        self._send_json(self.server.library.remove_track_from_playlist(pid, target))
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
                    self._send_json(self.server.library.get_history(limit=limit))
                    return
                if method == "POST":
                    body = self._read_json_body()
                    self._send_json(self.server.library.record_history(body), status=201)
                    return
                if method == "DELETE":
                    self._send_json(self.server.library.clear_history())
                    return
                self._send_error_json(405, "METHOD_NOT_ALLOWED", "Use GET, POST, or DELETE")
                return

            # /api/settings
            if path == "/api/settings":
                if method == "GET":
                    self._send_json(self.server.library.get_settings())
                    return
                if method == "PATCH":
                    body = self._read_json_body()
                    self._send_json(self.server.library.update_settings(body))
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
            self._send_error_json(500, "INTERNAL_ERROR", f"Unexpected server error: {exc}")


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
