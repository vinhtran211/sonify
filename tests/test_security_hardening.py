"""Comprehensive security regression tests covering OWASP Top 10 & audit findings (docs/SECURITY_AUDIT_REPORT.md)."""

from __future__ import annotations

import http.client
import json
import os
import re
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from backend import config, http_client, security
from backend.db import Database
from backend.providers import youtube_music
from backend.server import create_server
from backend.services import ios_install_service, library_service
from backend.services.stream_service import StreamService, StreamUnavailableError

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _http_req(
    port: int,
    method: str,
    path: str,
    body: dict | bytes | str | None = None,
    headers: dict[str, str] | None = None,
):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = dict(headers or {})
    payload: bytes | None = None
    if isinstance(body, dict):
        payload = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    elif isinstance(body, str):
        payload = body.encode("utf-8")
    elif isinstance(body, bytes):
        payload = body
    conn.request(method, path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read()
    resp_hdrs = {k.lower(): v for k, v in resp.getheaders()}
    set_cookies = [v for k, v in resp.getheaders() if k.lower() == "set-cookie"]
    conn.close()
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else None
    except Exception:
        parsed = raw.decode("utf-8", errors="replace")
    return resp.status, resp_hdrs, set_cookies, parsed


def _extract_cookie_pair(set_cookies: list[str], name: str = security.SESSION_COOKIE_NAME) -> str:
    for sc in set_cookies:
        m = re.search(rf"(?:^|;\s*){re.escape(name)}=([^;]+)", sc)
        if m:
            return f"{name}={m.group(1)}"
    return ""


class TestSSRFAndIngestionSanitization(unittest.TestCase):
    """VULN-01: Outbound SSRF blocking (initial + redirect hops) and client track metadata sanitization."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = create_server(
            "127.0.0.1", 0, db_path=Path(self.tmp.name) / "ssrf.db", offline=True, quiet=True
        )
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        http_client.set_allow_loopback_for_tests(False)
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def test_validate_outbound_url_blocks_schemes_private_metadata_and_loopback(self) -> None:
        blocked_urls = [
            "file:///etc/passwd",
            "gopher://127.0.0.1:6379/_INFO",
            "ftp://example.com/file.mp3",
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8080/api/health",
            "http://10.0.0.1/internal",
            "http://172.16.0.5/internal",
            "http://192.168.1.1/admin",
            "http://[::1]:8080/",
            "http://[::ffff:127.0.0.1]/",
            "http://localhost:8080/",
            "http://sub.localhost/",
            "https://example.com/\r\nX-Injected: 1",
        ]
        for u in blocked_urls:
            ok, _reason = http_client.validate_outbound_url(u)
            self.assertFalse(ok, f"Expected SSRF block for {u}")

        # Public literal IP is allowed
        ok_pub, _ = http_client.validate_outbound_url("https://1.1.1.1/audio.mp3")
        self.assertTrue(ok_pub)

    def test_redirect_to_metadata_ip_is_blocked_in_fetch_open_and_probe(self) -> None:
        """Even if an initial hop were permitted, a 302 redirect to 169.254.169.254 MUST be rejected."""

        class _RedirectHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                self.send_header("Content-Length", "0")
                self.end_headers()

        redir_srv = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
        rport = redir_srv.server_address[1]
        threading.Thread(target=redir_srv.serve_forever, daemon=True).start()
        try:
            http_client.set_offline_mode(False)
            http_client.set_allow_loopback_for_tests(True)
            url = f"http://127.0.0.1:{rport}/start"
            with self.assertRaises(http_client.DisallowedURLError):
                http_client.fetch_bytes(url, timeout=3)
            with self.assertRaises(http_client.DisallowedURLError):
                http_client.open_stream(url, timeout=3)
            ok, status, _, _ = http_client.probe_url(url, timeout=3)
            self.assertFalse(ok)
            self.assertEqual(status, 0)
        finally:
            http_client.set_allow_loopback_for_tests(False)
            http_client.set_offline_mode(None)
            redir_srv.shutdown()
            redir_srv.server_close()

    def test_client_likes_and_playlist_tracks_strip_extra_and_untrusted_preview_url(self) -> None:
        malicious_track = {
            "id": "dz:99887766",
            "title": "Legit Title",
            "artist": "Legit Artist",
            "album": "Album",
            "duration": 200,
            "source": "deezer",
            "previewUrl": "http://169.254.169.254/latest/meta-data/",
            "artwork": "javascript:alert(1)",
            "_extra": {
                "direct_urls": ["http://127.0.0.1:8080/internal"],
                "monochromeTrackId": "evil_override",
            },
        }
        st, _, _, _ = _http_req(self.port, "POST", "/api/library/likes", body={"track": malicious_track})
        self.assertIn(st, (200, 201))

        stored = self.srv.db.get_track("dz:99887766")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.get("previewUrl", ""), "")
        self.assertEqual(stored.get("artwork", ""), "")
        self.assertEqual(stored.get("_extra", {}), {})


class TestSessionIsolationIDORAndAPIToken(unittest.TestCase):
    """VULN-02: Per-session owner_id isolation, signed cookies, tampered cookies, API token, & legacy DB migration."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "sessions.db"
        self.srv = create_server("127.0.0.1", 0, db_path=self.db_path, offline=True, quiet=True)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def test_two_browser_sessions_are_fully_isolated_and_cross_owner_idor_returns_404(self) -> None:
        # Mint Session A and Session B via HTML load
        st_a, _, sc_a, _ = _http_req(self.port, "GET", "/", headers={"Accept": "text/html"})
        st_b, _, sc_b, _ = _http_req(self.port, "GET", "/", headers={"Accept": "text/html"})
        self.assertEqual(st_a, 200)
        self.assertEqual(st_b, 200)
        cookie_a = _extract_cookie_pair(sc_a)
        cookie_b = _extract_cookie_pair(sc_b)
        self.assertTrue(cookie_a and cookie_b and cookie_a != cookie_b)
        # Verify HttpOnly and SameSite=Lax flags
        self.assertTrue(any("HttpOnly" in c and "SameSite=Lax" in c for c in sc_a))

        # Session A creates playlist, likes track, sets volume 0.3
        st, _, _, pl = _http_req(
            self.port, "POST", "/api/playlists", body={"name": "Alice Secret Mix"}, headers={"Cookie": cookie_a}
        )
        self.assertEqual(st, 201)
        pl_id = pl["playlist"]["id"]
        st, _, _, _ = _http_req(
            self.port,
            "POST",
            "/api/library/likes",
            body={"track": {"id": "local:sunset-drive", "title": "Sunset Drive", "artist": "Synth", "duration": 210}},
            headers={"Cookie": cookie_a},
        )
        self.assertIn(st, (200, 201))
        st, _, _, _ = _http_req(
            self.port, "PATCH", "/api/settings", body={"volume": 0.3}, headers={"Cookie": cookie_a}
        )
        self.assertEqual(st, 200)

        # Session B sees empty playlists/likes, default volume 0.8, and gets 404 on Session A's playlist_id
        _, _, _, pls_b = _http_req(self.port, "GET", "/api/playlists", headers={"Cookie": cookie_b})
        self.assertEqual(pls_b["playlists"], [])
        _, _, _, likes_b = _http_req(self.port, "GET", "/api/library/likes", headers={"Cookie": cookie_b})
        self.assertEqual(likes_b["tracks"], [])
        _, _, _, settings_b = _http_req(self.port, "GET", "/api/settings", headers={"Cookie": cookie_b})
        vol_b = settings_b.get("settings", settings_b)["volume"]
        self.assertAlmostEqual(vol_b, 0.8, places=2)

        for method, body in (("GET", None), ("PATCH", {"name": "Pwned"}), ("DELETE", None)):
            st_idor, _, _, _ = _http_req(
                self.port, method, f"/api/playlists/{pl_id}", body=body, headers={"Cookie": cookie_b}
            )
            self.assertEqual(st_idor, 404, f"Expected 404 on cross-owner {method} /api/playlists/{pl_id}")

        # Tampered cookie is rejected and replaced with a fresh isolated session (cannot read Alice's playlist)
        tampered = f"{security.SESSION_COOKIE_NAME}=0123456789abcdef01234567.deadbeefdeadbeefdeadbeefdeadbeef"
        st_t, _, sc_t, pls_t = _http_req(
            self.port, "GET", "/api/playlists", headers={"Cookie": tampered, "Sec-Fetch-Mode": "cors"}
        )
        self.assertEqual(st_t, 200)
        self.assertEqual(pls_t["playlists"], [])
        self.assertTrue(_extract_cookie_pair(sc_t), "Expected tampered cookie to be replaced with a fresh cookie")

        # X-Sonify-Session header (iOS clients) also isolates cleanly
        st_ios, _, _, _ = _http_req(
            self.port,
            "GET",
            f"/api/playlists/{pl_id}",
            headers={"X-Sonify-Session": "ios-device-token-1234567890abcdef"},
        )
        self.assertEqual(st_ios, 404)

    def test_optional_api_token_enforces_401_on_missing_or_wrong_bearer(self) -> None:
        with mock.patch.dict(os.environ, {"SONIFY_API_TOKEN": "test-bearer-secret-999"}):
            st_no, _, _, _ = _http_req(self.port, "POST", "/api/playlists", body={"name": "Denied"})
            self.assertEqual(st_no, 401)
            st_bad, _, _, _ = _http_req(
                self.port,
                "POST",
                "/api/playlists",
                body={"name": "Denied"},
                headers={"Authorization": "Bearer wrong-token"},
            )
            self.assertEqual(st_bad, 401)
            st_ok, _, _, _ = _http_req(
                self.port,
                "POST",
                "/api/playlists",
                body={"name": "Allowed"},
                headers={"Authorization": "Bearer test-bearer-secret-999"},
            )
            self.assertEqual(st_ok, 201)

    def test_legacy_db_without_owner_id_migrates_cleanly(self) -> None:
        legacy_path = Path(self.tmp.name) / "legacy.db"
        conn = sqlite3.connect(legacy_path)
        conn.executescript(
            """
            CREATE TABLE playlists (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
                cover_color TEXT DEFAULT '#1DB954', cover_url TEXT DEFAULT '',
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE TABLE liked_tracks (track_id TEXT PRIMARY KEY, liked_at INTEGER NOT NULL);
            CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at INTEGER NOT NULL);
            INSERT INTO playlists VALUES ('pl_old', 'Old Mix', '', '#1DB954', '', 1, 1);
            INSERT INTO liked_tracks VALUES ('local:sunset-drive', 1);
            INSERT INTO settings VALUES ('volume', '0.55', 1);
            """
        )
        conn.commit()
        conn.close()

        migrated = Database(legacy_path)
        pls = migrated.list_playlists(owner_id="default")
        self.assertEqual([p["id"] for p in pls], ["pl_old"])
        self.assertEqual(migrated.list_playlists(owner_id="other_user"), [])
        self.assertAlmostEqual(migrated.get_settings(owner_id="default")["volume"], 0.55, places=2)


class TestCSSAndURLInjectionDefense(unittest.TestCase):
    """VULN-03: Strict coverColor & coverUrl validation on backend + safe rendering in frontend."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = create_server(
            "127.0.0.1", 0, db_path=Path(self.tmp.name) / "xss.db", offline=True, quiet=True
        )
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def test_playlist_patch_rejects_malicious_cover_color_and_cover_url(self) -> None:
        st, _, _, created = _http_req(self.port, "POST", "/api/playlists", body={"name": "Test PL"})
        self.assertEqual(st, 201)
        pl_id = created["playlist"]["id"]

        for bad_color in ("red);background:url(//evil.com/x)", "#12345", "javascript:alert(1)", "rgb(1,2,3)"):
            st_c, _, _, _ = _http_req(self.port, "PATCH", f"/api/playlists/{pl_id}", body={"coverColor": bad_color})
            self.assertEqual(st_c, 400, f"Expected 400 for coverColor={bad_color}")

        for bad_url in (
            "javascript:alert(1)",
            "data:image/svg+xml,<svg>",
            "//evil.com/x.png",
            "https://evil.com/a') ;background:red",
            "/api/artwork/../etc/passwd",
        ):
            st_u, _, _, _ = _http_req(self.port, "PATCH", f"/api/playlists/{pl_id}", body={"coverUrl": bad_url})
            self.assertEqual(st_u, 400, f"Expected 400 for coverUrl={bad_url}")

        st_ok, _, _, updated = _http_req(
            self.port,
            "PATCH",
            f"/api/playlists/{pl_id}",
            body={"coverColor": "#7C3AED", "coverUrl": "https://cdn.example.com/cov.jpg"},
        )
        self.assertEqual(st_ok, 200)
        self.assertEqual(updated["playlist"]["coverColor"], "#7C3AED")
        self.assertEqual(updated["playlist"]["coverUrl"], "https://cdn.example.com/cov.jpg")

    def test_frontend_uses_sanitize_safe_url_and_css_color_and_dom_safe_ambient_bg(self) -> None:
        lrc_js = (PROJECT_ROOT / "frontend" / "js" / "lrc.js").read_text(encoding="utf-8")
        track_list_js = (PROJECT_ROOT / "frontend" / "js" / "components" / "trackList.js").read_text(encoding="utf-8")
        lib_view_js = (PROJECT_ROOT / "frontend" / "js" / "views" / "libraryView.js").read_text(encoding="utf-8")
        lyrics_js = (PROJECT_ROOT / "frontend" / "js" / "components" / "lyricsPanel.js").read_text(encoding="utf-8")

        self.assertIn("export function sanitizeSafeUrl(", lrc_js)
        self.assertIn("export function sanitizeCssColor(", lrc_js)
        self.assertIn("sanitizeSafeUrl", track_list_js)
        self.assertIn("sanitizeCssColor", track_list_js)
        self.assertIn("sanitizeCssColor", lib_view_js)
        self.assertIn("renderPlaylistCoverHtml", lib_view_js)
        self.assertIn("sanitizeSafeUrl", lyrics_js)
        self.assertNotIn("style=\"background-image: url('${escapeHtml(art)}')\"", lyrics_js)


class TestHostSpoofingAndProviderIDValidation(unittest.TestCase):
    """VULN-04, VULN-05, VULN-10: Pinned iOS public origin & strict provider/yt-dlp ID guards."""

    def test_ios_origin_pins_public_origin_and_blocks_x_forwarded_host_spoofing(self) -> None:
        spoofed_headers = {
            "Host": "evil.attacker.com",
            "X-Forwarded-Host": "evil.attacker.com",
            "X-Forwarded-Proto": "https",
        }
        with mock.patch.dict(os.environ, {"SONIFY_PUBLIC_ORIGIN": "https://sonify-music.onrender.com"}, clear=False):
            origin = ios_install_service.resolve_public_origin(spoofed_headers)
            self.assertEqual(origin, "https://sonify-music.onrender.com")
            profile = ios_install_service.build_mobileconfig(origin).decode("utf-8")
            self.assertIn("https://sonify-music.onrender.com", profile)
            self.assertNotIn("evil.attacker.com", profile)

        with mock.patch.dict(
            os.environ,
            {"SONIFY_PUBLIC_ORIGIN": "", "RENDER_EXTERNAL_HOSTNAME": "sonify-music.onrender.com"},
            clear=False,
        ):
            origin2 = ios_install_service.resolve_public_origin(spoofed_headers)
            self.assertEqual(origin2, "https://sonify-music.onrender.com")

    def test_provider_id_validation_and_yt_dlp_double_dash_and_flag_guards(self) -> None:
        for bad_yt in ("--exec=touch", "--flag12345", "abc&list=123", "../../etc/passwd", "a" * 30, "short"):
            self.assertFalse(security.validate_provider_id("yt", bad_yt), f"Should reject yt:{bad_yt}")
            with mock.patch("subprocess.run") as m_run:
                self.assertIsNone(youtube_music._resolve_via_yt_dlp(bad_yt))
                m_run.assert_not_called()

        self.assertTrue(security.validate_provider_id("yt", "dQw4w9WgXcQ"))

        # Valid yt ID passes "--" before watch URL and ignores "-"-prefixed proxy/cookies
        fake_proc = mock.Mock(returncode=1, stdout="", stderr="")
        with mock.patch.dict(
            os.environ,
            {"YTDLP_PROXY": "--exec=bad", "YTDLP_COOKIES_PATH": "-bad", "YTDLP_COOKIES_B64": ""},
            clear=False,
        ), \
             mock.patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
             mock.patch.object(config, "is_cloud_env", return_value=False), \
             mock.patch("subprocess.run", return_value=fake_proc) as m_run:
            youtube_music._resolve_via_yt_dlp("dQw4w9WgXcQ")
            cmd = m_run.call_args[0][0]
            self.assertIn("--", cmd)
            dd_idx = cmd.index("--")
            self.assertEqual(cmd[dd_idx + 1], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            self.assertNotIn("--exec=bad", cmd)

        # StreamService rejects malformed track IDs
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "tid.db")
            svc = StreamService(db)
            with self.assertRaises(StreamUnavailableError):
                svc.resolve_stream("yt:--exec=pwn")


class TestCSRFRateLimitDoSErrorSanitizationHeadersAndIOS(unittest.TestCase):
    """VULN-06..09, VULN-11..13: CSRF, Rate Limit, Import Cap, 500 Sanitization, ?debug=1, Headers, Packaging."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = create_server(
            "127.0.0.1", 0, db_path=Path(self.tmp.name) / "hardening.db", offline=True, quiet=True
        )
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def test_csrf_origin_sec_fetch_site_and_non_json_content_type_rejected(self) -> None:
        st1, _, _, _ = _http_req(
            self.port,
            "POST",
            "/api/playlists",
            body={"name": "Cross Origin"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(st1, 403)

        st2, _, _, _ = _http_req(
            self.port,
            "POST",
            "/api/playlists",
            body={"name": "Cross Site"},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(st2, 403)

        st3, _, _, _ = _http_req(
            self.port,
            "POST",
            "/api/playlists",
            body='{"name":"PlainText"}',
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(st3, 415)

    def test_per_ip_rate_limit_returns_429_with_retry_after(self) -> None:
        self.srv.rate_limiter.clear()
        with mock.patch.dict(os.environ, {"SONIFY_RATE_LIMIT_RPM": "3"}):
            statuses = []
            last_hdrs = {}
            for _ in range(5):
                st, hdrs, _, _ = _http_req(self.port, "GET", "/api/settings")
                statuses.append(st)
                last_hdrs = hdrs
            self.assertIn(429, statuses)
            self.assertTrue(int(last_hdrs.get("retry-after", "0")) >= 1)
        self.srv.rate_limiter.clear()

    def test_import_playlist_caps_m3u_size_and_live_search_fanout(self) -> None:
        # >64KiB M3U is safely bounded (truncated/capped) and never triggers unbounded search fan-out
        huge_m3u = "#EXTM3U\n" + ("#EXTINF:180,Artist - Song\nhttp://example.com/a.mp3\n" * 2000)
        with mock.patch.object(self.srv.catalog, "search", return_value={"tracks": []}) as m_huge_search:
            st_big, _, _, res_big = _http_req(
                self.port, "POST", "/api/playlists/import", body={"name": "Huge", "m3uText": huge_m3u}
            )
            self.assertIn(st_big, (201, 400))
            self.assertLessEqual(m_huge_search.call_count, 20)
            if st_big == 201:
                self.assertLessEqual(res_big["importedCount"], 200)

        # 50-line M3U triggers at most 20 catalog searches
        lines = ["#EXTM3U"]
        for i in range(50):
            lines.append(f"#EXTINF:180,Artist {i} - Song {i}")
            lines.append(f"song_{i}.mp3")
        m3u_50 = "\n".join(lines)
        with mock.patch.object(self.srv.catalog, "search", return_value={"tracks": []}) as m_search:
            st_ok, _, _, res = _http_req(
                self.port, "POST", "/api/playlists/import", body={"name": "Capped", "m3uText": m3u_50}
            )
            self.assertEqual(st_ok, 201)
            self.assertLessEqual(m_search.call_count, 20)
            self.assertEqual(res["importedCount"], 50)

    def test_500_sanitization_cloud_debug_gating_and_security_headers(self) -> None:
        # 1. Unhandled 500 never leaks exception message
        with mock.patch.object(self.srv.library, "list_playlists", side_effect=RuntimeError("SECRET_DB_PATH_/var/data")):
            st, _, _, body = _http_req(self.port, "GET", "/api/playlists")
            self.assertEqual(st, 500)
            self.assertEqual(body["error"]["message"], "Internal server error")
            self.assertNotIn("SECRET_DB_PATH", json.dumps(body))

        # 2. Cloud ?debug=1 omits diagnostics unless SONIFY_ALLOW_DEBUG=1 or valid token
        with mock.patch.dict(os.environ, {"RENDER": "true", "SONIFY_ALLOW_DEBUG": "0", "SONIFY_DEBUG_TOKEN": "dbg-tok"}):
            st_d, _, _, info_d = _http_req(self.port, "GET", "/api/stream/local:sunset-drive/info?debug=1")
            self.assertEqual(st_d, 200)
            self.assertNotIn("diagnostics", info_d)

            st_d2, _, _, info_d2 = _http_req(
                self.port,
                "GET",
                "/api/stream/local:sunset-drive/info?debug=1",
                headers={"X-Sonify-Debug-Token": "dbg-tok"},
            )
            self.assertEqual(st_d2, 200)
            self.assertIn("diagnostics", info_d2)

        # 3. Security headers across HTML, API JSON, and SVG artwork
        st_h, hdrs_h, _, _ = _http_req(self.port, "GET", "/", headers={"Accept": "text/html"})
        self.assertEqual(st_h, 200)
        self.assertEqual(hdrs_h.get("x-content-type-options"), "nosniff")
        self.assertEqual(hdrs_h.get("x-frame-options"), "DENY")
        self.assertEqual(hdrs_h.get("referrer-policy"), "strict-origin-when-cross-origin")
        self.assertIn("default-src 'self'", hdrs_h.get("content-security-policy", ""))

        st_svg, hdrs_svg, _, _ = _http_req(self.port, "GET", "/api/artwork/sunset-drive")
        self.assertEqual(st_svg, 200)
        self.assertEqual(hdrs_svg.get("content-security-policy"), "sandbox")
        self.assertEqual(hdrs_svg.get("x-content-type-options"), "nosniff")

    def test_packaging_env_example_render_yaml_workflow_and_ios_hardening(self) -> None:
        env_ex = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        for var in ("SONIFY_SESSION_SECRET", "SONIFY_PUBLIC_ORIGIN", "SONIFY_ALLOW_DEBUG", "SONIFY_RATE_LIMIT_RPM"):
            self.assertIn(var, env_ex)

        render_yaml = (PROJECT_ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("SONIFY_PUBLIC_ORIGIN", render_yaml)
        self.assertIn("SONIFY_ALLOW_DEBUG", render_yaml)
        self.assertIn("SONIFY_SESSION_SECRET", render_yaml)

        wf = (PROJECT_ROOT / ".github" / "workflows" / "ios-build.yml").read_text(encoding="utf-8")
        self.assertIn("permissions:", wf)
        self.assertIn("contents: read", wf)

        api_swift = (PROJECT_ROOT / "ios" / "Sonify" / "Services" / "APIClient.swift").read_text(encoding="utf-8")
        self.assertIn("X-Sonify-Session", api_swift)
        self.assertIn('scheme = "https"', api_swift)


if __name__ == "__main__":
    unittest.main()
