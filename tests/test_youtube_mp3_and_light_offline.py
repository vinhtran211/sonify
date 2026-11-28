"""Tests for Smooth Ad-Free YouTube Audio/MP3 & Light Offline Downloads (docs/YOUTUBE_MP3_LIGHT_OFFLINE_SPEC.md)."""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from backend import config, http_client
from backend.db import Database
from backend.providers import jiosaavn, monochrome
from backend.server import create_server
from backend.services import transcoder
from backend.services.stream_service import ResolvedStream, StreamService

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestTranscoderHardeningAndLRUCache(unittest.TestCase):
    """Suite 1: ffmpeg hardening (-protocol_whitelist file,pipe, shell=False), SSRF blocking, & LRU cap."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmp.name) / "compact_cache"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ffmpeg_cmd_hardening_atomic_promotion_and_lru_eviction(self) -> None:
        src_file = Path(self.tmp.name) / "source.flac"
        src_file.write_bytes(b"fLaC" + b"\x00" * 5000)

        captured_cmds = []

        def fake_subprocess_run(cmd, **kwargs):
            self.assertFalse(kwargs.get("shell", True), "shell MUST be False")
            self.assertEqual(kwargs.get("timeout"), transcoder.TRANSCODE_TIMEOUT_SEC)
            captured_cmds.append(list(cmd))
            out_path = Path(cmd[-1])
            # Write valid MP3-like payload >= MIN_OUTPUT_BYTES (100 KiB)
            out_path.write_bytes(b"ID3" + b"\xff" * 120_000)
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with mock.patch.object(transcoder, "CACHE_DIR", self.cache_dir), \
             mock.patch.object(transcoder, "MAX_CACHE_BYTES", 250_000), \
             mock.patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             mock.patch("subprocess.run", side_effect=fake_subprocess_run):
            res1 = transcoder.transcode_file(src_file, "track:1", target="mp3")
            self.assertIsNotNone(res1)
            path1, mime1 = res1
            self.assertEqual(mime1, "audio/mpeg")
            self.assertTrue(path1.is_file() and path1.suffix == ".mp3")
            self.assertFalse(any(".part." in p.name for p in self.cache_dir.iterdir()))

            # Inspect exact hardened argv for mp3
            cmd1 = captured_cmds[0]
            self.assertIn("-protocol_whitelist", cmd1)
            self.assertEqual(cmd1[cmd1.index("-protocol_whitelist") + 1], "file,pipe")
            self.assertIn("-vn", cmd1)
            self.assertIn("libmp3lame", cmd1)
            self.assertIn("128k", cmd1)

            # Test m4a_copy argv
            time.sleep(0.02)
            res2 = transcoder.transcode_file(src_file, "track:2", target="m4a_copy")
            self.assertIsNotNone(res2)
            path2, mime2 = res2
            self.assertEqual(mime2, "audio/mp4")
            self.assertTrue(path2.is_file() and path2.suffix == ".m4a")
            cmd2 = captured_cmds[1]
            self.assertIn("-c:a", cmd2)
            self.assertEqual(cmd2[cmd2.index("-c:a") + 1], "copy")
            self.assertIn("+faststart", cmd2)

            # 3rd file (~120KB -> total ~360KB > 250KB cap) must evict oldest (path1)!
            time.sleep(0.02)
            res3 = transcoder.transcode_file(src_file, "track:3", target="mp3")
            self.assertIsNotNone(res3)
            self.assertFalse(path1.exists(), "Oldest cached file should be LRU-evicted")
            self.assertTrue(res3[0].exists())

    def test_transcode_stream_url_blocks_ssrf_before_spawning_ffmpeg_and_handles_missing_ffmpeg(self) -> None:
        with mock.patch.object(transcoder, "CACHE_DIR", self.cache_dir), \
             mock.patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             mock.patch("subprocess.run") as m_sub:
            for bad_url in (
                "http://169.254.169.254/latest/meta-data/",
                "file:///etc/passwd",
                "http://127.0.0.1:8080/api/health",
                "http://10.0.0.1/secret",
            ):
                self.assertIsNone(transcoder.transcode_stream_url(bad_url, None, "ssrf:1", target="mp3"))
            m_sub.assert_not_called()

        # Graceful fallback when ffmpeg is absent
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(transcoder.is_available())
            self.assertIsNone(transcoder.transcode_bytes(b"fLaC" + b"\x00" * 5000, "no_ff:1", target="mp3"))


class TestCompactWaterfallAndFormatMP3Endpoints(unittest.TestCase):
    """Suite 2: quality=compact waterfall, JioSaavn 160k preference, ?format=mp3, settings & health."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "compact.db"
        self.db = Database(self.db_path)
        self.db.upsert_track(
            {
                "id": "dz:555111",
                "title": "Compact Song",
                "artist": "Test Artist",
                "album": "Test Album",
                "duration": 210,
                "source": "deezer",
            }
        )
        self.stream = StreamService(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_jiosaavn_prefer_compact_orders_160k_before_320k(self) -> None:
        with mock.patch.object(
            jiosaavn.crypto_des,
            "decrypt_media_url",
            return_value="https://aac.saavncdn.com/123/abc_96.mp4",
        ):
            hi_urls = jiosaavn.resolve_candidate_urls({"encrypted_media_url": "enc"}, prefer_compact=False)
            lo_urls = jiosaavn.resolve_candidate_urls({"encrypted_media_url": "enc"}, prefer_compact=True)
            self.assertTrue(hi_urls[0].endswith("_320.mp4"))
            self.assertTrue(lo_urls[0].endswith("_160.mp4"))

    def test_quality_compact_prefers_soundcloud_before_monochrome_on_cloud_and_youtube_on_local(self) -> None:
        # 1. Cloud (RENDER=true): quality=compact picks SoundCloud (~3-4MB) BEFORE 25-42MB Monochrome FLAC!
        sc_result = {
            "type": "url",
            "url": "https://cf-media.sndcdn.com/track.m4a",
            "content_type": "audio/mp4",
            "headers": {"User-Agent": "Sonify"},
        }
        with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=False), \
             mock.patch("backend.providers.soundcloud.find_best_stream", return_value=sc_result), \
             mock.patch("backend.providers.monochrome.find_best_stream") as m_mc:
            res = self.stream.resolve_stream("dz:555111", force_refresh=True, quality="compact")
            self.assertEqual((res.mode, res.source), ("full", "soundcloud"))
            m_mc.assert_not_called()

        # 2. Local (non-cloud): quality=compact prefers YouTube audio first!
        with mock.patch.dict(os.environ, {"RENDER": ""}, clear=False), \
             mock.patch.object(config, "is_cloud_env", return_value=False), \
             mock.patch("backend.providers.youtube_music.find_best_video_id", return_value="dQw4w9WgXcQ"), \
             mock.patch(
                 "backend.providers.youtube_music.resolve_audio_stream",
                 return_value=("https://rr1.googlevideo.com/audio.m4a", "audio/mp4", {}, "youtube-innertube"),
             ), \
             mock.patch("backend.providers.monochrome.find_best_stream") as m_mc2:
            res_loc = self.stream.resolve_stream("dz:555111", force_refresh=True, quality="compact")
            self.assertEqual((res_loc.mode, res_loc.source), ("full", "youtube-innertube"))
            m_mc2.assert_not_called()

    def test_flac_only_track_compacts_to_mp3_with_206_range_and_format_mp3_works(self) -> None:
        cache_dir = Path(self.tmp.name) / "compact_out"
        mc_dir = Path(self.tmp.name) / "mc_src"
        mc_dir.mkdir(parents=True, exist_ok=True)
        flac_file = mc_dir / "999888777.bin"
        flac_file.write_bytes(b"fLaC" + b"\x01" * 380_000)

        mp3_payload = b"ID3" + (b"\xaa\xbb\xcc\xdd" * 40_000)  # 160,003 bytes compact MP3

        def fake_transcode_file(in_path, cache_key, target="mp3"):
            import hashlib
            cache_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(f"{cache_key}|{target}".encode("utf-8")).hexdigest()[:16]
            out = cache_dir / f"{digest}.mp3"
            out.write_bytes(mp3_payload)
            return out, "audio/mpeg"

        mc_resolved = ResolvedStream(
            track_id="dz:555111",
            mode="full",
            source="monochrome",
            content_type="audio/flac",
            url="https://tracks.monochrome.st/track/999888777",
        )

        srv = create_server("127.0.0.1", 0, db_path=self.db_path, offline=False, quiet=True)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with mock.patch.object(monochrome, "CACHE_DIR", mc_dir), \
                 mock.patch.object(monochrome, "_ACTIVE_DOWNLOADS", {}), \
                 mock.patch.object(monochrome, "_MIME_CACHE", {}), \
                 mock.patch.object(transcoder, "is_available", return_value=True), \
                 mock.patch.object(transcoder, "transcode_file", side_effect=fake_transcode_file), \
                 mock.patch.object(srv.stream, "resolve_stream", return_value=mc_resolved):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/api/stream/dz:555111/info?quality=compact")
                r_info = conn.getresponse()
                info = json.loads(r_info.read().decode("utf-8"))
                self.assertEqual(r_info.status, 200)
                self.assertEqual(info["contentType"], "audio/mpeg")
                self.assertTrue(info["streamUrlWithExt"].endswith(".mp3"))
                self.assertTrue(info.get("compact"))
                self.assertTrue(info.get("transcoded"))
                self.assertNotIn("directUrl", info)
                self.assertNotIn("hlsUrl", info)

                # HTTP 206 Range request on compacted MP3
                conn.request("GET", "/api/stream/dz:555111?quality=compact", headers={"Range": "bytes=0-1023"})
                r_st = conn.getresponse()
                chunk = r_st.read()
                self.assertEqual(r_st.status, 206)
                self.assertEqual(r_st.getheader("Content-Type"), "audio/mpeg")
                self.assertEqual(len(chunk), 1024)
                self.assertTrue(chunk.startswith(b"ID3"))
                conn.close()

            # Verify ?format=mp3 on a YouTube video/mp4 stream triggers transcode_stream_url -> audio/mpeg
            yt_resolved = ResolvedStream(
                track_id="dz:555111",
                mode="full",
                source="youtube-innertube",
                content_type="video/mp4",
                url="https://rr1.googlevideo.com/videoplayback?itag=18",
                headers={"User-Agent": "Android"},
            )
            with mock.patch.object(transcoder, "is_available", return_value=True), \
                 mock.patch.object(transcoder, "transcode_stream_url", side_effect=lambda u, h, k, target="mp3": fake_transcode_file(None, k, target)) as m_turl, \
                 mock.patch.object(srv.stream, "resolve_stream", return_value=yt_resolved):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/api/stream/dz:555111/info?format=mp3")
                r_mp3 = conn.getresponse()
                info_mp3 = json.loads(r_mp3.read().decode("utf-8"))
                self.assertEqual(r_mp3.status, 200)
                self.assertEqual(info_mp3["contentType"], "audio/mpeg")
                self.assertTrue(info_mp3.get("transcoded"))

                conn.request("GET", "/api/stream/dz:555111?format=mp3", headers={"Range": "bytes=0-511"})
                r_mp3_s = conn.getresponse()
                b_mp3 = r_mp3_s.read()
                self.assertEqual(r_mp3_s.status, 206)
                self.assertEqual(r_mp3_s.getheader("Content-Type"), "audio/mpeg")
                self.assertEqual(len(b_mp3), 512)
                self.assertTrue(m_turl.called)
                conn.close()

            # Verify PATCH /api/settings accepts audioQuality="compact" & rejects invalid, and /api/health reports ffmpegAvailable
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("PATCH", "/api/settings", body=json.dumps({"audioQuality": "compact"}), headers={"Content-Type": "application/json"})
            r_set = conn.getresponse(); body_set = json.loads(r_set.read().decode("utf-8"))
            self.assertEqual(r_set.status, 200)
            self.assertEqual(body_set["audioQuality"], "compact")

            conn.request("PATCH", "/api/settings", body=json.dumps({"audioQuality": "ultra_invalid"}), headers={"Content-Type": "application/json"})
            r_bad = conn.getresponse(); r_bad.read()
            self.assertEqual(r_bad.status, 400)

            conn.request("GET", "/api/health")
            r_hlth = conn.getresponse(); health = json.loads(r_hlth.read().decode("utf-8"))
            self.assertEqual(r_hlth.status, 200)
            self.assertIsInstance(health.get("ffmpegAvailable"), bool)
            conn.close()
        finally:
            srv.shutdown()
            srv.server_close()

        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ffmpeg", dockerfile)


class TestWebAndNativeIOSLightOfflineAndYouTubeDirect(unittest.TestCase):
    """Suite 3: Web light offline + settings contracts and Native iOS YouTube Direct + AVAssetExportSession contracts."""

    def test_web_light_offline_settings_and_badges(self) -> None:
        dl_js = (PROJECT_ROOT / "frontend" / "js" / "offlineDownloads.js").read_text(encoding="utf-8")
        modal_js = (PROJECT_ROOT / "frontend" / "js" / "components" / "audioSettingsModal.js").read_text(encoding="utf-8")
        store_js = (PROJECT_ROOT / "frontend" / "js" / "store.js").read_text(encoding="utf-8")
        player_js = (PROJECT_ROOT / "frontend" / "js" / "player.js").read_text(encoding="utf-8")
        bar_js = (PROJECT_ROOT / "frontend" / "js" / "components" / "playerBar.js").read_text(encoding="utf-8")

        self.assertIn("export function getDownloadQuality(", dl_js)
        self.assertIn("export function setDownloadQuality(", dl_js)
        self.assertIn("?quality=${encodeURIComponent(qual)}", dl_js)
        self.assertIn("'compact'", store_js)
        self.assertIn("Light / Compact", modal_js)
        self.assertIn("Offline Download Size", modal_js)
        self.assertIn("quality=", player_js)
        self.assertIn("Compact MP3", bar_js)

    def test_ios_youtube_direct_itag140_hls_and_light_offline_compaction(self) -> None:
        yt_swift = (PROJECT_ROOT / "ios" / "Sonify" / "Services" / "YouTubeDirectResolver.swift").read_text(encoding="utf-8")
        yt_tests = (PROJECT_ROOT / "ios" / "SonifyTests" / "YouTubeDirectResolverTests.swift").read_text(encoding="utf-8")
        dl_swift = (PROJECT_ROOT / "ios" / "Sonify" / "Services" / "OfflineDownloadManager.swift").read_text(encoding="utf-8")
        player_swift = (PROJECT_ROOT / "ios" / "Sonify" / "Services" / "AudioPlayerService.swift").read_text(encoding="utf-8")
        api_swift = (PROJECT_ROOT / "ios" / "Sonify" / "Services" / "APIClient.swift").read_text(encoding="utf-8")
        settings_swift = (PROJECT_ROOT / "ios" / "Sonify" / "Views" / "SettingsView.swift").read_text(encoding="utf-8")

        self.assertIn("pickAdaptiveAudioFormat", yt_swift)
        self.assertIn("itag == 140", yt_swift)
        self.assertIn("pickHLSManifestURL", yt_swift)
        self.assertIn("verifyTail", yt_swift)
        self.assertIn("testPickAdaptiveAudioItag140AndHLSManifest", yt_tests)

        self.assertIn("downloadQuality", dl_swift)
        self.assertIn("AVAssetExportSession", dl_swift)
        self.assertIn("AVAssetExportPresetAppleM4A", dl_swift)
        self.assertIn("allowHLS: false", dl_swift)

        self.assertIn("preferYouTubeDirect", player_swift)
        self.assertIn("quality: String?", api_swift)
        self.assertIn("Download Quality", settings_swift)
        self.assertIn("YouTube Direct", settings_swift)


if __name__ == "__main__":
    unittest.main()
