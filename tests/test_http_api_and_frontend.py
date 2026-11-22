"""End-to-end HTTP REST API, static frontend, ad-free guarantee, and module integrity tests."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from backend import config, http_client
from backend.server import create_server

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"


class TestHTTPServerE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "e2e.db")
        self.srv = create_server(host="127.0.0.1", port=0, db_path=self.db_path, offline=True, quiet=True)
        self.port = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def _req(self, method: str, path: str, body=None, headers=None):
        hdrs = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.base}{path}", data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, dict(exc.headers), exc.read()
            finally:
                exc.close()

    def test_health_and_catalog_endpoints(self):
        st, hdrs, raw = self._req("GET", "/api/health")
        self.assertEqual(st, 200)
        self.assertEqual(hdrs.get("X-Ad-Free"), "true")
        health = json.loads(raw)
        self.assertEqual(health["status"], "ok")
        self.assertTrue(health["adFree"])
        self.assertTrue(health["dbReady"])

        # Home global & vn
        st, _, raw = self._req("GET", "/api/home?region=global")
        self.assertEqual(st, 200)
        home = json.loads(raw)
        self.assertEqual(home["mode"], "offline")
        self.assertEqual(home["sections"][0]["id"], "trending-global")

        st, _, raw = self._req("GET", "/api/home?region=vn")
        self.assertEqual(st, 200)
        home_vn = json.loads(raw)
        self.assertEqual(home_vn["sections"][0]["id"], "trending-vn")

        # Search (English, Vietnamese diacritics, empty query)
        st, _, raw = self._req("GET", "/api/search?q=Neon&type=all")
        self.assertEqual(st, 200)
        srch = json.loads(raw)
        self.assertGreaterEqual(len(srch["tracks"]), 1)
        self.assertIsNotNone(srch["topResult"])

        st, _, raw = self._req("GET", "/api/search?q=" + urllib.parse.quote("Đêm Sài Gòn"))
        self.assertEqual(st, 200)
        vn_srch = json.loads(raw)
        self.assertEqual(vn_srch["tracks"][0]["id"], "local:dem-saigon")

        st, _, raw = self._req("GET", "/api/search?q=")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(raw)["tracks"], [])

        # Artist & Album detail + 404
        st, _, raw = self._req("GET", "/api/artist/local:artist-nova")
        self.assertEqual(st, 200)
        art = json.loads(raw)
        self.assertEqual(art["artist"]["name"], "Nova Pulse")
        self.assertGreaterEqual(len(art["topTracks"]), 3)

        st, _, raw = self._req("GET", "/api/album/local:album-saigon")
        self.assertEqual(st, 200)
        alb = json.loads(raw)
        self.assertEqual(alb["album"]["title"], "Đêm Sài Gòn")
        self.assertEqual(len(alb["tracks"]), 3)

        st, _, _ = self._req("GET", "/api/artist/local:does-not-exist")
        self.assertEqual(st, 404)
        st, _, _ = self._req("GET", "/api/album/local:does-not-exist")
        self.assertEqual(st, 404)

    def test_stream_range_and_lyrics_and_artwork_endpoints(self):
        # Stream info
        st, _, raw = self._req("GET", "/api/stream/local:neon-horizon/info")
        self.assertEqual(st, 200)
        info = json.loads(raw)
        self.assertTrue(info["adFree"])
        self.assertEqual(info["streamMode"], "offline-synth")
        self.assertEqual(info["contentType"], "audio/wav")

        # Stream 206 Range bytes=0-1023
        st, hdrs, chunk = self._req("GET", "/api/stream/local:neon-horizon", headers={"Range": "bytes=0-1023"})
        self.assertEqual(st, 206)
        self.assertEqual(len(chunk), 1024)
        self.assertTrue(chunk.startswith(b"RIFF"))
        self.assertEqual(hdrs.get("Accept-Ranges"), "bytes")
        self.assertEqual(hdrs.get("X-Ad-Free"), "true")
        self.assertTrue(hdrs.get("Content-Range", "").startswith("bytes 0-1023/"))

        # Stream 416 Range Not Satisfiable
        st, hdrs, _ = self._req("GET", "/api/stream/local:neon-horizon", headers={"Range": "bytes=999999999-"})
        self.assertEqual(st, 416)
        self.assertEqual(hdrs.get("X-Ad-Free"), "true")

        # HEAD stream
        st, hdrs, body = self._req("HEAD", "/api/stream/local:neon-horizon")
        self.assertEqual(st, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(hdrs.get("Content-Length", "0")), 100000)

        # Lyrics synced & instrumental
        st, _, raw = self._req("GET", "/api/lyrics?trackId=local:neon-horizon")
        self.assertEqual(st, 200)
        lyr = json.loads(raw)
        self.assertEqual(lyr["status"], "synced")
        self.assertTrue(lyr["synced"])
        self.assertGreater(len(lyr["lines"]), 5)

        st, _, raw = self._req("GET", "/api/lyrics?trackId=local:analog-waves")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(raw)["status"], "instrumental")

        # SVG artwork
        st, hdrs, svg = self._req("GET", "/api/artwork/neon-horizon")
        self.assertEqual(st, 200)
        self.assertIn("image/svg+xml", hdrs.get("Content-Type", ""))
        self.assertEqual(hdrs.get("X-Ad-Free"), "true")
        self.assertIn(b"<svg", svg)

    def test_library_playlists_history_settings_and_error_codes(self):
        # Like / Unlike
        st, _, raw = self._req("POST", "/api/library/likes", body={"trackId": "local:neon-horizon"})
        self.assertEqual(st, 201)
        st, _, raw = self._req("GET", "/api/library/likes")
        self.assertEqual(json.loads(raw)["likedIds"], ["local:neon-horizon"])
        st, _, _ = self._req("DELETE", "/api/library/likes/local:neon-horizon")
        self.assertEqual(st, 200)
        st, _, raw = self._req("GET", "/api/library/likes")
        self.assertEqual(json.loads(raw)["likedIds"], [])

        # Playlist CRUD + reorder
        st, _, raw = self._req("POST", "/api/playlists", body={"name": "Roadtrip", "description": "Cruising"})
        self.assertEqual(st, 201)
        pid = json.loads(raw)["playlist"]["id"]

        st, _, _ = self._req("POST", f"/api/playlists/{pid}/tracks", body={"trackId": "local:neon-horizon"})
        self.assertEqual(st, 201)
        st, _, _ = self._req("POST", f"/api/playlists/{pid}/tracks", body={"trackId": "local:dem-saigon"})
        self.assertEqual(st, 201)

        st, _, _ = self._req(
            "PATCH",
            f"/api/playlists/{pid}/tracks/reorder",
            body={"trackIds": ["local:dem-saigon", "local:neon-horizon"]},
        )
        self.assertEqual(st, 200)

        st, _, raw = self._req("GET", f"/api/playlists/{pid}")
        self.assertEqual(
            [t["id"] for t in json.loads(raw)["tracks"]],
            ["local:dem-saigon", "local:neon-horizon"],
        )

        st, _, raw = self._req("PATCH", f"/api/playlists/{pid}", body={"name": "Night Drive"})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(raw)["playlist"]["name"], "Night Drive")

        st, _, _ = self._req("DELETE", f"/api/playlists/{pid}/tracks/local:dem-saigon")
        self.assertEqual(st, 200)

        st, _, _ = self._req("DELETE", f"/api/playlists/{pid}")
        self.assertEqual(st, 200)

        # History
        st, _, _ = self._req("POST", "/api/history", body={"trackId": "local:neon-horizon"})
        self.assertEqual(st, 201)
        st, _, raw = self._req("GET", "/api/history")
        self.assertEqual(len(json.loads(raw)["items"]), 1)
        st, _, _ = self._req("DELETE", "/api/history")
        self.assertEqual(st, 200)
        st, _, raw = self._req("GET", "/api/history")
        self.assertEqual(json.loads(raw)["items"], [])

        # Settings
        st, _, raw = self._req("PATCH", "/api/settings", body={"volume": 0.65, "repeat": "all"})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(raw)["volume"], 0.65)
        self.assertEqual(json.loads(raw)["repeat"], "all")

        # HTTP error codes: 404, 405, 400 malformed JSON, OPTIONS 204
        st, _, _ = self._req("GET", "/api/does-not-exist")
        self.assertEqual(st, 404)
        st, _, _ = self._req("POST", "/api/health")
        self.assertEqual(st, 405)
        st, _, _ = self._req("OPTIONS", "/api/health")
        self.assertEqual(st, 204)

        bad_req = urllib.request.Request(
            f"{self.base}/api/playlists",
            data=b"{not valid json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(bad_req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()

    def test_static_frontend_assets_and_security(self):
        # Root HTML
        st, hdrs, html_bytes = self._req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("text/html", hdrs.get("Content-Type", ""))
        self.assertEqual(hdrs.get("X-Ad-Free"), "true")
        self.assertIn(b"Sonify", html_bytes)

        # Every CSS and JS file serves 200 with exact MIME type
        for css_file in sorted((FRONTEND_DIR / "css").glob("*.css")):
            rel = "/" + css_file.relative_to(FRONTEND_DIR).as_posix()
            st, hdrs, _ = self._req("GET", rel)
            self.assertEqual(st, 200, rel)
            self.assertIn("text/css", hdrs.get("Content-Type", ""))

        js_files = sorted((FRONTEND_DIR / "js").rglob("*.js"))
        self.assertGreaterEqual(len(js_files), 15)
        for js_file in js_files:
            rel = "/" + js_file.relative_to(FRONTEND_DIR).as_posix()
            st, hdrs, _ = self._req("GET", rel)
            self.assertEqual(st, 200, rel)
            self.assertIn("application/javascript", hdrs.get("Content-Type", ""))

        # SPA fallback route without extension returns index.html
        st, hdrs, spa_bytes = self._req("GET", "/playlist/demo")
        self.assertEqual(st, 200)
        self.assertIn("text/html", hdrs.get("Content-Type", ""))
        self.assertIn(b"id=\"app\"", spa_bytes)

        # Missing static asset with extension returns 404
        st, _, _ = self._req("GET", "/js/nonexistent.js")
        self.assertEqual(st, 404)

        # Path traversal blocked with 403
        st, _, _ = self._req("GET", "/%2e%2e/app.py")
        self.assertEqual(st, 403)


class TestAdFreeAndFrontendIntegrity(unittest.TestCase):
    def test_zero_ad_networks_or_trackers_in_codebase(self):
        banned_patterns = [
            "doubleclick.net",
            "googlesyndication",
            "googleadservices",
            "adservice.google",
            "adsense",
            "admob",
            "taboola",
            "outbrain",
            "googletagmanager",
            "google-analytics",
            "facebook.net/en_US/fbevents",
        ]
        scan_roots = [ROOT_DIR / "frontend", ROOT_DIR / "backend", ROOT_DIR / "app.py"]
        files_checked = 0
        for target in scan_roots:
            paths = [target] if target.is_file() else [p for p in target.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
            for p in paths:
                text = p.read_text(encoding="utf-8", errors="ignore").lower()
                files_checked += 1
                for pat in banned_patterns:
                    self.assertNotIn(pat, text, f"Found banned ad/tracker pattern '{pat}' in {p}")
        self.assertGreater(files_checked, 25)

    def test_frontend_es_module_imports_and_exports_match(self):
        js_files = sorted((FRONTEND_DIR / "js").rglob("*.js"))
        export_map = {}
        export_re = re.compile(
            r"export\s+(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)"
        )
        export_braces_re = re.compile(r"export\s*\{([^}]+)\}")

        for jf in js_files:
            src = jf.read_text(encoding="utf-8")
            exports = set(export_re.findall(src))
            for block in export_braces_re.findall(src):
                for part in block.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    name = part.split(" as ")[-1].strip()
                    exports.add(name)
            export_map[jf.resolve()] = exports

        import_re = re.compile(r"import\s+\{([^}]+)\}\s+from\s+['\"](\.[^'\"]+)['\"]", re.MULTILINE)
        for jf in js_files:
            src = jf.read_text(encoding="utf-8")
            for names_block, rel_spec in import_re.findall(src):
                target = (jf.parent / rel_spec).resolve()
                self.assertTrue(target.is_file(), f"Broken import '{rel_spec}' in {jf}")
                available = export_map.get(target, set())
                for raw_name in names_block.split(","):
                    raw_name = raw_name.strip()
                    if not raw_name:
                        continue
                    orig = raw_name.split(" as ")[0].strip()
                    self.assertIn(
                        orig,
                        available,
                        f"Missing export '{orig}' from '{rel_spec}' imported in {jf.name}",
                    )

    def test_index_html_contains_all_static_root_ids(self):
        html_src = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        required_ids = [
            "app",
            "sidebar",
            "topbar",
            "btn-nav-back",
            "btn-nav-forward",
            "topbar-search-icon",
            "global-search-input",
            "global-search-clear",
            "offline-banner",
            "btn-retry-online",
            "main-content",
            "right-drawer",
            "player-bar",
            "mobile-nav",
            "context-menu-root",
            "modal-root",
            "toast-root",
            "audio-engine",
        ]
        for dom_id in required_ids:
            self.assertIn(f'id="{dom_id}"', html_src, f"Missing required element id='{dom_id}' in index.html")


if __name__ == "__main__":
    unittest.main()
