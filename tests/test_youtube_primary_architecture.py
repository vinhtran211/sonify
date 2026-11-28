"""Tests for YouTube-Primary MP3/MP4 Architecture (docs/YOUTUBE_PRIMARY_ARCHITECTURE.md)."""

from __future__ import annotations

import http.client
import json
import os
import pathlib
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from backend import config
from backend.db import Database
from backend.providers import youtube_music
from backend.server import create_server
from backend.services import transcoder
from backend.services.catalog_service import CatalogService
from backend.services.stream_service import ResolvedStream, StreamService

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestYouTubePrimarySearchAndWaterfall(unittest.TestCase):
    """Tests 1–3: YouTube-primary search + dedup enrichment, Tier 1 priority (local & cloud w/ creds), & cloud anon breaker."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "yt_primary.db")
        self.catalog = CatalogService(self.db)
        self.stream = StreamService(self.db)
        youtube_music.reset_cloud_anon_gate()

    def tearDown(self) -> None:
        youtube_music.reset_cloud_anon_gate()
        self.tmp.cleanup()

    def test_youtube_primary_search_runs_in_parallel_and_enriches_deduped_tracks(self) -> None:
        dz_tracks = [
            {
                "id": f"dz:{1000 + i}",
                "title": f"Song {i}" if i > 0 else "Đừng Làm Trái Tim Anh Đau",
                "artist": f"Artist {i}" if i > 0 else "Sơn Tùng M-TP",
                "album": "Single Album",
                "duration": 279,
                "artwork": "https://cdn-images.dzcdn.net/cover.jpg",
                "source": "deezer",
                "_extra": {"isrc": "VN1234567890"},
            }
            for i in range(12)
        ]
        yt_tracks = [
            {
                "id": "yt:abPmRqxGfxU",
                "title": "Đừng Làm Trái Tim Anh Đau",
                "artist": "Sơn Tùng M-TP",
                "album": "",
                "duration": 279,
                "artwork": "https://lh3.googleusercontent.com/yt.jpg",
                "source": "youtube_music",
                "_extra": {"videoId": "abPmRqxGfxU"},
            }
        ]

        with mock.patch("backend.http_client.is_offline_mode", return_value=False), \
             mock.patch("backend.providers.deezer.search_tracks", return_value=dz_tracks), \
             mock.patch("backend.providers.apple_itunes.search_songs", return_value=[]), \
             mock.patch("backend.providers.jiosaavn.search_songs", return_value=[]), \
             mock.patch("backend.providers.audius.search_tracks", return_value=[]), \
             mock.patch("backend.providers.youtube_music.search_songs", return_value=yt_tracks) as m_yt:
            res = self.catalog.search("Sơn Tùng M-TP Đừng Làm Trái Tim Anh Đau", search_type="tracks", limit=10)
            m_yt.assert_called_once()
            tracks = res["tracks"]
            self.assertGreaterEqual(len(tracks), 1)
            # Exact YouTube Music track ranks #1 and cross-inherits Deezer album & ISRC on dedup!
            top = tracks[0]
            self.assertEqual(top["id"], "yt:abPmRqxGfxU")
            self.assertEqual(top["album"], "Single Album")
            stored = self.db.get_track("yt:abPmRqxGfxU", include_extra=True)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.get("_extra", {}).get("videoId"), "abPmRqxGfxU")
            self.assertEqual(stored.get("_extra", {}).get("isrc"), "VN1234567890")

    def test_youtube_is_tier1_primary_locally_and_on_cloud_with_credentials(self) -> None:
        self.db.upsert_track(
            {
                "id": "dz:2026001",
                "title": "Blinding Lights",
                "artist": "The Weeknd",
                "duration": 200,
                "source": "deezer",
            }
        )

        # 1. Cloud (RENDER=true) WITH YTDLP_COOKIES_B64: YouTube wins Tier 1 BEFORE Monochrome & SoundCloud in BOTH auto & compact!
        for qual in ("auto", "compact"):
            with mock.patch.dict(os.environ, {"RENDER": "true", "YTDLP_COOKIES_B64": "ZHVtbXk="}, clear=False), \
                 mock.patch("backend.providers.youtube_music.find_best_video_id", return_value="4NRXx6U8ABQ"), \
                 mock.patch(
                     "backend.providers.youtube_music.resolve_audio_stream",
                     return_value=("https://rr1.googlevideo.com/audio.m4a", "audio/mp4", {}, "yt-dlp"),
                 ), \
                 mock.patch("backend.providers.monochrome.find_best_stream") as m_mc, \
                 mock.patch("backend.providers.soundcloud.find_best_stream") as m_sc:
                res = self.stream.resolve_stream("dz:2026001", force_refresh=True, quality=qual)
                self.assertEqual((res.mode, res.source), ("full", "yt-dlp"))
                m_mc.assert_not_called()
                m_sc.assert_not_called()

        # 2. Local (non-cloud): YouTube wins Tier 1 BEFORE Monochrome & SoundCloud in BOTH auto & compact!
        for qual in ("auto", "compact"):
            with mock.patch.dict(os.environ, {"RENDER": ""}, clear=False), \
                 mock.patch.object(config, "is_cloud_env", return_value=False), \
                 mock.patch("backend.providers.youtube_music.find_best_video_id", return_value="4NRXx6U8ABQ"), \
                 mock.patch(
                     "backend.providers.youtube_music.resolve_audio_stream",
                     return_value=("https://rr1.googlevideo.com/audio.m4a", "audio/mp4", {}, "youtube-innertube"),
                 ), \
                 mock.patch("backend.providers.monochrome.find_best_stream") as m_mc, \
                 mock.patch("backend.providers.soundcloud.find_best_stream") as m_sc:
                res = self.stream.resolve_stream("dz:2026001", force_refresh=True, quality=qual)
                self.assertEqual((res.mode, res.source), ("full", "youtube-innertube"))
                m_mc.assert_not_called()
                m_sc.assert_not_called()

    def test_cloud_anon_gate_circuit_breaker_trips_and_falls_back_cleanly(self) -> None:
        self.db.upsert_track(
            {
                "id": "dz:2026002",
                "title": "Test Song",
                "artist": "Test Artist",
                "duration": 200,
                "source": "deezer",
            }
        )
        self.assertFalse(youtube_music.is_cloud_anon_gated())
        youtube_music.trip_cloud_anon_gate()
        self.assertTrue(youtube_music.is_cloud_anon_gated())

        sc_fallback = {
            "type": "url",
            "url": "https://cf-media.sndcdn.com/song.m4a",
            "content_type": "audio/mp4",
            "headers": {"User-Agent": "Sonify"},
        }
        with mock.patch.dict(os.environ, {"RENDER": "true", "YTDLP_COOKIES_B64": "", "YTDLP_PROXY": ""}, clear=False), \
             mock.patch("backend.providers.youtube_music.find_best_video_id") as m_yt_search, \
             mock.patch("backend.providers.monochrome.find_best_stream", return_value=None), \
             mock.patch("backend.providers.soundcloud.find_best_stream", return_value=sc_fallback):
            res = self.stream.resolve_stream("dz:2026002", force_refresh=True, quality="auto")
            self.assertEqual((res.mode, res.source), ("full", "soundcloud"))
            m_yt_search.assert_not_called()


class TestDirectMP3M4ARoutesHealthAndPackaging(unittest.TestCase):
    """Tests 4–7: .mp3/.m4a extension inference, 400 INVALID_FORMAT, /api/health youtubeRuntime, Docker & iOS."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "routes.db"
        self.srv = create_server("127.0.0.1", 0, db_path=self.db_path, offline=False, quiet=True)
        self.srv.db.upsert_track(
            {
                "id": "yt:dQw4w9WgXcQ",
                "title": "Never Gonna Give You Up",
                "artist": "Rick Astley",
                "duration": 213,
                "source": "youtube",
                "_extra": {"videoId": "dQw4w9WgXcQ"},
            }
        )
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def test_direct_mp3_and_m4a_extension_routes_and_invalid_format_400(self) -> None:
        mp3_out = Path(self.tmp.name) / "out.mp3"
        mp3_out.write_bytes(b"ID3" + b"\xaa" * 150_000)
        m4a_out = Path(self.tmp.name) / "out.m4a"
        m4a_out.write_bytes(b"\x00\x00\x00\x20ftypM4A " + b"\xbb" * 150_000)

        yt_mp4 = ResolvedStream(
            track_id="yt:dQw4w9WgXcQ",
            mode="full",
            source="youtube-innertube",
            content_type="video/mp4",
            url="https://rr1.googlevideo.com/videoplayback?itag=18",
            headers={"User-Agent": "Android"},
        )

        def fake_transcode_url(url, headers, cache_key, target="mp3"):
            if target == "mp3":
                return mp3_out, "audio/mpeg"
            return m4a_out, "audio/mp4"

        with mock.patch.object(self.srv.stream, "resolve_stream", return_value=yt_mp4), \
             mock.patch.object(transcoder, "is_available", return_value=True), \
             mock.patch.object(transcoder, "transcode_stream_url", side_effect=fake_transcode_url):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

            # 1. /api/stream/yt:dQw4w9WgXcQ.mp3 -> infers mp3 -> audio/mpeg 206
            conn.request("GET", "/api/stream/yt:dQw4w9WgXcQ.mp3", headers={"Range": "bytes=0-1023"})
            r1 = conn.getresponse()
            b1 = r1.read()
            self.assertEqual(r1.status, 206)
            self.assertEqual(r1.getheader("Content-Type"), "audio/mpeg")
            self.assertTrue(b1.startswith(b"ID3"))

            # 2. /api/stream/yt:dQw4w9WgXcQ.m4a -> infers m4a -> audio/mp4 206
            conn.request("GET", "/api/stream/yt:dQw4w9WgXcQ.m4a", headers={"Range": "bytes=0-1023"})
            r2 = conn.getresponse()
            b2 = r2.read()
            self.assertEqual(r2.status, 206)
            self.assertEqual(r2.getheader("Content-Type"), "audio/mp4")
            self.assertEqual(b2[4:8], b"ftyp")

            # 3. Invalid ?format=evil -> 400 INVALID_FORMAT
            conn.request("GET", "/api/stream/yt:dQw4w9WgXcQ?format=evil")
            r3 = conn.getresponse()
            err = json.loads(r3.read().decode("utf-8"))
            self.assertEqual(r3.status, 400)
            self.assertEqual(err["error"]["code"], "INVALID_FORMAT")
            conn.close()

    def test_health_exposes_non_sensitive_youtube_runtime_never_leaks_secrets(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "RENDER": "true",
                "YTDLP_COOKIES_B64": "U0VDUkVUX0NPT0tJRV9WQUxVRQ==",
                "YTDLP_PROXY": "http://user:SUPER_SECRET_PASS@proxy.example:8080",
            },
            clear=False,
        ):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            conn.close()
            self.assertEqual(resp.status, 200)
            data = json.loads(raw)
            rt = data.get("youtubeRuntime")
            self.assertIsInstance(rt, dict)
            for key in ("ytDlp", "deno", "ffmpeg", "cloudMode", "cloudCredentialsConfigured", "cloudAnonGated"):
                self.assertIsInstance(rt.get(key), bool, f"youtubeRuntime.{key} must be bool")
            self.assertTrue(rt["cloudCredentialsConfigured"])
            self.assertNotIn("SUPER_SECRET_PASS", raw)
            self.assertNotIn("U0VDUkVUX0NPT0tJRV9WQUxVRQ", raw)

    def test_dockerfile_requirements_web_badges_and_ios_youtube_first_contracts(self) -> None:
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        reqs = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
        bar_js = (PROJECT_ROOT / "frontend" / "js" / "components" / "playerBar.js").read_text(encoding="utf-8")
        yt_swift = (PROJECT_ROOT / "ios" / "Sonify" / "Services" / "YouTubeDirectResolver.swift").read_text(encoding="utf-8")
        yt_tests = (PROJECT_ROOT / "ios" / "SonifyTests" / "YouTubeDirectResolverTests.swift").read_text(encoding="utf-8")
        player_swift = (PROJECT_ROOT / "ios" / "Sonify" / "Services" / "AudioPlayerService.swift").read_text(encoding="utf-8")
        dl_swift = (PROJECT_ROOT / "ios" / "Sonify" / "Services" / "OfflineDownloadManager.swift").read_text(encoding="utf-8")

        # Dockerfile & requirements.txt (Deno + ffmpeg + yt-dlp[default], ZERO bgutil anti-bot bypass)
        self.assertIn("COPY --from=denoland/deno:bin /deno /usr/local/bin/deno", dockerfile)
        self.assertIn("ffmpeg", dockerfile)
        self.assertIn("XDG_CACHE_HOME=/tmp/.cache", dockerfile)
        self.assertIn("USER user", dockerfile)
        self.assertIn("yt-dlp[default]", reqs)
        self.assertNotIn("bgutil", reqs.lower())
        self.assertNotIn("bgutil", dockerfile.lower())

        # Web badges
        self.assertIn("YouTube MP3", bar_js)
        self.assertIn("YouTube Audio", bar_js)
        self.assertIn("YouTube MP4", bar_js)

        # Native iOS YouTube-First contracts
        self.assertIn("static func extractDirectVideoId(from trackId: String) -> String?", yt_swift)
        self.assertIn("testExtractDirectVideoIdValidation", yt_tests)
        self.assertIn("self.preferYouTubeDirect = true", player_swift)
        self.assertIn("YouTubeDirectResolver.shared.resolveFullTrack(for: track, allowHLS: false)", dl_swift)


if __name__ == "__main__":
    unittest.main()
