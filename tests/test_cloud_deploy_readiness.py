"""Tests for Render Free Web Service (Docker) deployment readiness, cloud env vars, SIGTERM, and yt-dlp fallback."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from backend import config, http_client
from backend.db import Database
from backend.providers import youtube_music

ROOT_DIR = Path(__file__).resolve().parent.parent


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestCloudEnvAndDBFallback(unittest.TestCase):
    def test_resolve_default_host_and_port_precedence(self):
        # Local default (clean env)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(config.resolve_default_host(), "127.0.0.1")
            self.assertEqual(config.resolve_default_port(), 8080)

        # Cloud PORT env var (Render default 10000) auto-binds 0.0.0.0
        with mock.patch.dict(os.environ, {"PORT": "10000"}, clear=True):
            self.assertEqual(config.resolve_default_host(), "0.0.0.0")
            self.assertEqual(config.resolve_default_port(), 10000)

        # HF Spaces SPACE_ID auto-binds 0.0.0.0 even without PORT
        with mock.patch.dict(os.environ, {"SPACE_ID": "user/sonify"}, clear=True):
            self.assertEqual(config.resolve_default_host(), "0.0.0.0")
            self.assertEqual(config.resolve_default_port(), 8080)

        # Explicit SONIFY_HOST and SONIFY_PORT override PORT/SPACE_ID
        with mock.patch.dict(
            os.environ,
            {"PORT": "7860", "SPACE_ID": "user/sonify", "SONIFY_HOST": "127.0.0.1", "SONIFY_PORT": "9090"},
            clear=True,
        ):
            self.assertEqual(config.resolve_default_host(), "127.0.0.1")
            self.assertEqual(config.resolve_default_port(), 9090)

        # Invalid PORT falls back safely to 8080
        with mock.patch.dict(os.environ, {"PORT": "not-a-number"}, clear=True):
            self.assertEqual(config.resolve_default_port(), 8080)

    def test_is_cloud_env_detection_for_render_railway_fly_spaces_and_override(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(config.is_cloud_env())

        for env_key in ("RENDER", "RENDER_SERVICE_ID", "RAILWAY_ENVIRONMENT", "FLY_APP_NAME", "SPACE_ID"):
            with mock.patch.dict(os.environ, {env_key: "true"}, clear=True):
                self.assertTrue(config.is_cloud_env(), f"Expected is_cloud_env() True for {env_key}")

        with mock.patch.dict(os.environ, {"SONIFY_CLOUD_MODE": "1"}, clear=True):
            self.assertTrue(config.is_cloud_env())

        with mock.patch.dict(os.environ, {"RENDER": "true", "SONIFY_CLOUD_MODE": "0"}, clear=True):
            self.assertFalse(config.is_cloud_env())

    def test_ytdlp_cookies_b64_and_proxy_and_cloud_piped_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cookie_file = Path(tmp) / "yt_cookies.txt"
            import base64
            cookie_payload = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n"
            b64_str = base64.b64encode(cookie_payload).decode("ascii")

            with mock.patch.object(youtube_music, "_COOKIES_FILE_PATH", cookie_file), \
                 mock.patch.object(youtube_music, "_COOKIES_B64_HASH", ""), \
                 mock.patch.dict(
                     os.environ,
                     {"YTDLP_COOKIES_B64": b64_str, "YTDLP_PROXY": "http://127.0.0.1:8888"},
                     clear=True,
                 ):
                args = youtube_music._get_ytdlp_extra_args()
                self.assertEqual(
                    args,
                    ["--cookies", str(cookie_file), "--proxy", "http://127.0.0.1:8888"],
                )
                self.assertEqual(cookie_file.read_bytes(), cookie_payload)

        # On cloud IPs (RENDER=true), unauthenticated yt-dlp and dead public Piped/Invidious seeds are skipped
        with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=True), \
             mock.patch("backend.providers.youtube_music._resolve_via_innertube_player", return_value=None), \
             mock.patch("subprocess.run") as m_subproc, \
             mock.patch("backend.providers.youtube_music._resolve_via_piped") as m_piped, \
             mock.patch("backend.providers.youtube_music._resolve_via_invidious") as m_inv:
            self.assertIsNone(youtube_music.resolve_audio_stream("7u4g483WTzw"))
            m_subproc.assert_not_called()
            m_piped.assert_not_called()
            m_inv.assert_not_called()

    def test_unwritable_db_path_falls_back_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback_path = Path(tmp) / "fallback" / "music.db"
            # Use a path whose parent cannot be created (existing regular file used as parent dir)
            blocker = Path(tmp) / "not_a_dir"
            blocker.write_text("file", encoding="utf-8")
            bad_db = blocker / "nested" / "music.db"

            with mock.patch.object(config, "FALLBACK_DB_PATH", fallback_path):
                db = Database(bad_db)
                self.assertEqual(db.db_path, fallback_path)
                self.assertTrue(db.is_ready())
                self.assertTrue(fallback_path.is_file())


class TestCloudDatacenterResilience(unittest.TestCase):
    def setUp(self):
        http_client.set_offline_mode(False)

    def tearDown(self):
        http_client.set_offline_mode(None)

    def test_innertube_login_required_falls_back_to_yt_dlp_progressive_with_exact_ua(self):
        yt_dlp_json = json.dumps(
            {
                "http_headers": {"User-Agent": "yt-dlp-android/2026"},
                "formats": [
                    {
                        "format_id": "140",
                        "ext": "m4a",
                        "vcodec": "none",
                        "acodec": "mp4a.40.2",
                        "abr": 128,
                        "url": "https://googlevideo.test/ytdlp_adaptive_140",
                        "http_headers": {"User-Agent": "yt-dlp-android/2026"},
                    },
                    {
                        "format_id": "18",
                        "ext": "mp4",
                        "vcodec": "avc1.42001E",
                        "acodec": "mp4a.40.2",
                        "tbr": 480,
                        "url": "https://googlevideo.test/ytdlp_prog_18",
                        "http_headers": {"User-Agent": "yt-dlp-android/2026"},
                    },
                ],
            }
        )
        fake_proc = mock.Mock(returncode=0, stdout=yt_dlp_json, stderr="")

        def fake_probe(url, headers=None, verify_tail=False, **kwargs):
            # Adaptive 140 fails PO-token tail check; progressive 18 succeeds!
            if "ytdlp_adaptive_140" in url:
                return False, 403, {}, url
            return True, 206, {"content-type": "video/mp4"}, url

        with mock.patch("backend.providers.youtube_music._resolve_via_innertube_player", return_value=None), \
             mock.patch("shutil.which", return_value="/usr/local/bin/yt-dlp"), \
             mock.patch("subprocess.run", return_value=fake_proc), \
             mock.patch("backend.http_client.probe_url", side_effect=fake_probe) as m_probe:
            res = youtube_music.resolve_audio_stream("7u4g483WTzw")
            self.assertIsNotNone(res)
            url, mime, hdrs, src = res
            self.assertEqual(src, "yt-dlp")
            self.assertEqual(url, "https://googlevideo.test/ytdlp_prog_18")
            self.assertEqual(mime, "video/mp4")
            self.assertEqual(hdrs.get("User-Agent"), "yt-dlp-android/2026")
            self.assertEqual(m_probe.call_count, 2)


class TestCloudServerProcessAndRenderPackaging(unittest.TestCase):
    def test_cloud_port_env_headers_and_sigterm_graceful_shutdown(self):
        port = _pick_free_port()
        with tempfile.TemporaryDirectory() as tmp:
            db_file = os.path.join(tmp, "cloud.db")
            env = {
                k: v
                for k, v in os.environ.items()
                if k not in ("SONIFY_HOST", "SONIFY_PORT", "SPACE_ID")
            }
            env["PORT"] = str(port)
            env["SONIFY_DB"] = db_file
            env["SONIFY_OFFLINE"] = "1"

            proc = subprocess.Popen(
                ["python3", "app.py", "--quiet"],
                cwd=str(ROOT_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                health = None
                for _ in range(40):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                            hdrs = {k.lower(): v for k, v in r.headers.items()}
                            self.assertNotIn("x-frame-options", hdrs)
                            self.assertEqual(r.status, 200)
                            health = json.loads(r.read().decode("utf-8"))
                            break
                    except Exception:
                        time.sleep(0.1)
                self.assertIsNotNone(health, "Cloud server failed to respond on PORT")
                self.assertEqual(health["status"], "ok")
                self.assertTrue(health["adFree"])
                self.assertTrue(health["dbReady"])

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
                    hdrs = {k.lower(): v for k, v in r.headers.items()}
                    self.assertEqual(r.status, 200)
                    self.assertNotIn("x-frame-options", hdrs)
                    self.assertIn(b"Sonify", r.read())

                # Verify fast, clean SIGTERM shutdown (Docker container stop)
                t0 = time.time()
                proc.send_signal(signal.SIGTERM)
                rc = proc.wait(timeout=4)
                elapsed = time.time() - t0
                self.assertEqual(rc, 0, f"Expected clean SIGTERM exit 0, got {rc}")
                self.assertLess(elapsed, 2.5, f"SIGTERM shutdown too slow: {elapsed:.2f}s")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()

    def test_render_blueprint_dockerfile_and_github_readme(self):
        # 1. render.yaml Render Blueprint
        render_yaml = (ROOT_DIR / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("type: web", render_yaml)
        self.assertIn("name: sonify-music", render_yaml)
        self.assertIn("runtime: docker", render_yaml)
        self.assertIn("plan: free", render_yaml)
        self.assertIn("autoDeploy: true", render_yaml)
        self.assertIn("healthCheckPath: /api/health", render_yaml)
        self.assertIn("SONIFY_HOST", render_yaml)
        self.assertIn("0.0.0.0", render_yaml)
        self.assertIn("PYTHONUNBUFFERED", render_yaml)

        # 2. README.md renders cleanly on GitHub (no HF YAML frontmatter) with Render instructions
        readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
        self.assertFalse(readme.startswith("---"), "README.md should not have HF YAML frontmatter")
        self.assertTrue(readme.startswith("# Sonify"))
        self.assertIn("Render", readme)
        self.assertIn("render.yaml", readme)

        # 3. Dockerfile checks (Render default port 10000, non-root UID 1000, dynamic ${PORT} healthcheck)
        dockerfile = (ROOT_DIR / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("useradd -m -u 1000 user", dockerfile)
        self.assertIn("USER user", dockerfile)
        self.assertIn("PORT=10000", dockerfile)
        self.assertIn("EXPOSE 10000", dockerfile)
        self.assertNotIn("SONIFY_PORT=", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("http://127.0.0.1:${PORT}/api/health", dockerfile)
        self.assertIn('CMD ["python3", "app.py"]', dockerfile)
        self.assertIn("requirements.txt", dockerfile)

        # 4. requirements.txt
        reqs = (ROOT_DIR / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertIn("cryptography", reqs)
        self.assertIn("yt-dlp", reqs)

        # 5. .gitignore & .dockerignore
        gitignore = (ROOT_DIR / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("data/*.db", gitignore)
        self.assertIn("__pycache__/", gitignore)

        dockerignore = (ROOT_DIR / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("data/", dockerignore)
        self.assertIn("tests/", dockerignore)
        self.assertIn("docs/", dockerignore)
        self.assertIn(".git/", dockerignore)


if __name__ == "__main__":
    unittest.main()
