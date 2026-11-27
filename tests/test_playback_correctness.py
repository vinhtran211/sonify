"""Regression tests for Playback Correctness & Zero-Mismatch fixes (docs/PLAYBACK_CORRECTNESS_FIX.md)."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from backend import lrc_parser
from backend.db import Database
from backend.providers import audius, bilibili, jiosaavn, monochrome, soundcloud
from backend.server import create_server
from backend.services.stream_service import ResolvedStream, StreamService

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestInfoStreamCodecConsistency(unittest.TestCase):
    """Suite 1: /info <-> /stream codec & source consistency, fast 429 failover, and ?refresh=1."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.db.upsert_track(
            {
                "id": "it:1749963740",
                "title": "Đừng Làm Trái Tim Anh Đau",
                "artist": "Sơn Tùng M-TP",
                "album": "Đừng Làm Trái Tim Anh Đau",
                "duration": 279,
                "source": "itunes",
            }
        )
        self.stream = StreamService(self.db)

    def tearDown(self) -> None:
        monochrome._RATE_LIMITED_UNTIL = 0.0
        self.tmp.cleanup()

    def test_monochrome_429_or_stall_cascades_inside_get_stream_info_never_returns_flac(self) -> None:
        """When Monochrome fails/429s during /info verification, /info must cascade fast and return true fallback MIME/ext."""
        part_path = Path(self.tmp.name) / "mc_fail.part"
        final_path = Path(self.tmp.name) / "mc_fail.bin"

        def fake_start_bg(mc_id: str):
            entry = monochrome.ProgressiveCacheEntry(
                clean_id=mc_id,
                part_path=part_path,
                final_path=final_path,
            )

            def _fail_fast():
                time.sleep(0.05)
                with entry.cond:
                    entry.failed = True
                    entry.done = True
                    entry.cond.notify_all()

            threading.Thread(target=_fail_fast, daemon=True).start()
            return entry

        def fake_resolve(track_id: str, force_refresh: bool = False, exclude_sources=None, quality: str = "auto"):
            excl = set(exclude_sources or ())
            if "monochrome" not in excl:
                return ResolvedStream(
                    track_id=track_id,
                    mode="full",
                    source="monochrome",
                    content_type="audio/flac",
                    url="https://tracks.monochrome.st/track/164376983856218112",
                )
            return ResolvedStream(
                track_id=track_id,
                mode="full",
                source="soundcloud",
                content_type="audio/mp4",
                url="https://cf-hls-media.sndcdn.com/stream.m4a",
                headers={"X-SC-M3U8": "https://cf-hls-media.sndcdn.com/playlist.m3u8"},
            )

        t0 = time.monotonic()
        with mock.patch.object(monochrome, "_ACTIVE_DOWNLOADS", {}), \
             mock.patch.object(monochrome, "start_background_cache", side_effect=fake_start_bg), \
             mock.patch.object(soundcloud, "start_background_hls_assembly", return_value=None), \
             mock.patch.object(self.stream, "resolve_stream", side_effect=fake_resolve):
            info = self.stream.get_stream_info("it:1749963740", prewarm=False)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 3.5)
        self.assertEqual(info["streamSource"], "soundcloud")
        self.assertEqual(info["contentType"], "audio/mp4")
        self.assertEqual(info["streamUrlWithExt"], "/api/stream/it:1749963740.m4a")
        self.assertEqual(info["hlsUrl"], "https://cf-hls-media.sndcdn.com/playlist.m3u8")

    def test_healthy_monochrome_64kb_head_verified_in_info_and_serves_206_immediately(self) -> None:
        """Healthy Monochrome 64KiB+ fLaC payload is verified in /info (<1s) and immediately serves HTTP 206."""
        flac_bytes = b"fLaC" + (b"\x00\x01\x02\x03" * 95_000)  # 380,004 bytes (>= MIN_TRACK_BYTES)
        cache_dir = Path(self.tmp.name) / "mc_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        final_path = cache_dir / "164376983856218112.bin"
        final_path.write_bytes(flac_bytes)

        mc_resolved = ResolvedStream(
            track_id="it:1749963740",
            mode="full",
            source="monochrome",
            content_type="audio/flac",
            url="https://tracks.monochrome.st/track/164376983856218112",
        )

        server = create_server("127.0.0.1", 0, db_path=Path(self.tmp.name) / "http.db", offline=False, quiet=True)
        server.db.upsert_track(
            {
                "id": "it:1749963740",
                "title": "Đừng Làm Trái Tim Anh Đau",
                "artist": "Sơn Tùng M-TP",
                "duration": 279,
                "source": "itunes",
            }
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch.object(monochrome, "CACHE_DIR", cache_dir), \
                 mock.patch.object(monochrome, "_ACTIVE_DOWNLOADS", {}), \
                 mock.patch.object(monochrome, "_MIME_CACHE", {}), \
                 mock.patch.object(server.stream, "resolve_stream", return_value=mc_resolved):
                t0 = time.monotonic()
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/api/stream/it:1749963740/info")
                resp = conn.getresponse()
                info = json.loads(resp.read().decode("utf-8"))
                info_elapsed = time.monotonic() - t0
                self.assertEqual(resp.status, 200)
                self.assertLess(info_elapsed, 1.0)
                self.assertEqual(info["streamSource"], "monochrome")
                self.assertEqual(info["contentType"], "audio/flac")
                self.assertEqual(info["streamUrlWithExt"], "/api/stream/it:1749963740.flac")

                conn.request("GET", info["streamUrlWithExt"], headers={"Range": "bytes=0-1023"})
                sresp = conn.getresponse()
                chunk = sresp.read()
                conn.close()
                self.assertEqual(sresp.status, 206)
                self.assertEqual(sresp.getheader("Content-Type"), "audio/flac")
                self.assertEqual(len(chunk), 1024)
                self.assertTrue(chunk.startswith(b"fLaC"))
        finally:
            server.shutdown()
            server.server_close()

    def test_info_refresh_query_param_and_fast_initial_429_producer_budget(self) -> None:
        """?refresh=1 forwards force_refresh=True; initial Monochrome 429 retries at most once (0.75s)."""
        server = create_server("127.0.0.1", 0, db_path=Path(self.tmp.name) / "refresh.db", offline=False, quiet=True)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        seen_calls = []

        def fake_get_info(track_id, debug=False, quality="auto", prewarm=False, force_refresh=False):
            seen_calls.append((track_id, prewarm, force_refresh))
            return {
                "trackId": track_id,
                "streamUrl": f"/api/stream/{track_id}",
                "streamUrlWithExt": f"/api/stream/{track_id}.m4a",
                "streamMode": "full",
                "streamSource": "soundcloud",
                "contentType": "audio/mp4",
                "adFree": True,
            }

        try:
            with mock.patch.object(server.stream, "get_stream_info", side_effect=fake_get_info):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/api/stream/it:1749963740/info?refresh=1")
                resp = conn.getresponse()
                resp.read()
                conn.close()
                self.assertEqual(resp.status, 200)
                self.assertEqual(seen_calls, [("it:1749963740", False, True)])
        finally:
            server.shutdown()
            server.server_close()

        # Verify monochrome._run_producer performs only 1 fast retry (0.75s) on initial 429
        entry = monochrome.ProgressiveCacheEntry(
            clean_id="99999",
            part_path=Path(self.tmp.name) / "99999.part",
            final_path=Path(self.tmp.name) / "99999.bin",
        )
        attempts = []
        sleeps = []

        def fake_open(*args, **kwargs):
            attempts.append(1)
            raise urllib.error.HTTPError(args[0], 429, "Too Many Requests", {}, None)

        with mock.patch.object(monochrome, "_pace_upstream_request", return_value=None), \
             mock.patch.object(monochrome.http_client, "open_stream", side_effect=fake_open), \
             mock.patch.object(monochrome.time, "sleep", side_effect=lambda s: sleeps.append(s)):
            monochrome._RATE_LIMITED_UNTIL = 0.0
            monochrome._run_producer(entry)

        self.assertTrue(entry.failed)
        self.assertEqual(len(attempts), 2)  # initial + 1 fast retry only (never 4 attempts!)
        self.assertEqual(sleeps, [0.75])
        # Cooldown circuit-breaker must immediately prevent hammering Monochrome during burst 429s
        self.assertTrue(monochrome.is_rate_limited())
        self.assertIsNone(monochrome.find_best_stream("Any Song", "Any Artist", duration=200))
        monochrome._RATE_LIMITED_UNTIL = 0.0


class TestSongIdentityDurationAndVersionGates(unittest.TestCase):
    """Suite 2: token_similarity, asymmetric duration, unwanted versions, & closed zero-artist loophole."""

    def test_token_similarity_word_boundary_cjk_and_no_short_substring_false_positives(self) -> None:
        self.assertEqual(lrc_parser.token_similarity("a", "osad"), 0.0)
        self.assertEqual(lrc_parser.token_similarity("den", "golden hour"), 0.0)
        self.assertGreater(lrc_parser.token_similarity("Đen", "Đen, MIN"), 0.85)
        self.assertEqual(lrc_parser.token_similarity("아이유 밤편지", "아이유 밤편지"), 1.0)
        self.assertGreaterEqual(lrc_parser.token_similarity("밤편지", "아이유 밤편지"), 0.85)
        self.assertGreaterEqual(lrc_parser.token_similarity("周杰伦", "周杰伦 晴天"), 0.85)

    def test_asymmetric_duration_and_repro_tracks_reject_chopped_and_sped_up(self) -> None:
        # 1. Repro: Như Một Người Rưng (177s, OSAD & Hà An Huy)
        # SoundCloud 1:59 (119s) AYDEN EDIT FREEDOWNLOAD must be rejected in BOTH Pass 1 and Pass 2!
        sc_chopped_edit = {
            "id": 2434705296,
            "title": "NHƯ MỘT NGƯỜI RƯNG - OSAD x HÀ AN HUY (AYDEN EDIT) FREEDOWNLOAD",
            "duration": 119_000,
            "policy": "ALLOW",
            "user": {"username": "AYDEN"},
            "media": {
                "transcodings": [
                    {"format": {"protocol": "hls"}, "url": "https://api-v2.soundcloud.com/media/stream/hls"}
                ]
            },
        }
        self.assertFalse(soundcloud.is_full_allow_track(sc_chopped_edit, target_duration=177, relaxed=False))
        self.assertFalse(soundcloud.is_full_allow_track(sc_chopped_edit, target_duration=177, relaxed=True))
        self.assertLess(
            soundcloud.score_candidate(
                sc_chopped_edit, "Như Một Người Rưng", "OSAD", "OSAD & Hà An Huy", target_duration=177, relaxed=False
            ),
            0.0,
        )
        self.assertLess(
            soundcloud.score_candidate(
                sc_chopped_edit, "Như Một Người Rưng", "OSAD", "OSAD & Hà An Huy", target_duration=177, relaxed=True
            ),
            0.0,
        )

        # Authentic 3:00 (180s) Bilibili studio match must score >= 0.90!
        bili_authentic = {
            "bvid": "BV1fpWn6nEMM",
            "title": "OSAD × HÀ AN HUY | NHƯ MỘT NGƯỜI RƯNG (Official Audio)",
            "author": "OSAD Official",
            "duration": 180,
        }
        bili_score = bilibili.score_candidate(
            bili_authentic, "Như Một Người Rưng", "OSAD", "OSAD & Hà An Huy", target_duration=177, relaxed=False
        )
        self.assertGreaterEqual(bili_score, 0.90)

        # 2. Repro: Đừng Làm Trái Tim Anh Đau (279s, Sơn Tùng M-TP)
        # 4:20 (260s, -19s sped-up copyright-dodge reupload) must be rejected in BOTH Pass 1 and Pass 2!
        sc_sped_up = {
            "id": 1848871632,
            "title": "Đừng Làm Trái Tim Anh Đau - Sơn Tùng M-TP",
            "duration": 260_000,
            "policy": "ALLOW",
            "user": {"username": "Sơn Tùng M-TP"},
            "media": {
                "transcodings": [
                    {"format": {"protocol": "hls"}, "url": "https://api-v2.soundcloud.com/media/stream/hls"}
                ]
            },
        }
        self.assertFalse(soundcloud.is_full_allow_track(sc_sped_up, target_duration=279, relaxed=False))
        self.assertFalse(soundcloud.is_full_allow_track(sc_sped_up, target_duration=279, relaxed=True))
        self.assertLess(
            soundcloud.score_candidate(
                sc_sped_up, "Đừng Làm Trái Tim Anh Đau", "Sơn Tùng M-TP", "Sơn Tùng M-TP", target_duration=279, relaxed=False
            ),
            0.0,
        )
        self.assertLess(
            soundcloud.score_candidate(
                sc_sped_up, "Đừng Làm Trái Tim Anh Đau", "Sơn Tùng M-TP", "Sơn Tùng M-TP", target_duration=279, relaxed=True
            ),
            0.0,
        )

        # 3. Official MVs that are longer (+33s in Pass 1 mv_capable; +45s in Pass 2) remain allowed!
        self.assertTrue(lrc_parser.is_duration_compatible(225, 192, relaxed=False, mv_capable=True))
        self.assertFalse(lrc_parser.is_duration_compatible(200, 155, relaxed=False, mv_capable=True))
        self.assertTrue(lrc_parser.is_duration_compatible(200, 155, relaxed=True, mv_capable=True))

    def test_unwanted_versions_and_generic_title_zero_artist_loophole_closed_across_all_providers(self) -> None:
        # classify_unwanted_version checks
        self.assertEqual(lrc_parser.classify_unwanted_version("Song (Sped Up)", "Song Artist"), "hard_reject")
        self.assertEqual(lrc_parser.classify_unwanted_version("Song (DANNYZ MASHUP)", "Song Artist"), "hard_reject")
        self.assertEqual(lrc_parser.classify_unwanted_version("Song bản Cha Cha Cha", "Song Artist"), "hard_reject")
        self.assertEqual(lrc_parser.classify_unwanted_version("Song FREEDOWNLOAD", "Song Artist"), "hard_reject")
        self.assertEqual(lrc_parser.classify_unwanted_version("Song (Remix)", "Song Artist"), "derivative")
        self.assertEqual(lrc_parser.classify_unwanted_version("Song (Cover)", "Song Artist"), "derivative")
        # Requested derivative or benign radio edit / original mix / artist name allowed
        self.assertEqual(lrc_parser.classify_unwanted_version("Song (Remix)", "Song (Remix) Artist"), "ok")
        self.assertEqual(lrc_parser.classify_unwanted_version("Song (Radio Edit)", "Song Artist"), "ok")
        self.assertEqual(lrc_parser.classify_unwanted_version("Beat It - Michael Jackson", "Beat It Michael Jackson"), "ok")
        self.assertEqual(lrc_parser.classify_unwanted_version("Turn Down for What - DJ Snake", "Turn Down for What DJ Snake"), "ok")

        # Generic title "Stay" (141s, The Kid LAROI) MUST NEVER match "Stay" (150s, Random Stranger) in Pass 1 or Pass 2!
        mc_wrong = {"id": "mc1", "title": "Stay", "artist": "Random Stranger", "duration": 150}
        sc_wrong = {
            "id": 11,
            "title": "Stay",
            "duration": 150_000,
            "policy": "ALLOW",
            "user": {"username": "Random Stranger"},
            "media": {
                "transcodings": [
                    {"format": {"protocol": "hls"}, "url": "https://api-v2.soundcloud.com/media/stream/hls"}
                ]
            },
        }
        bili_wrong = {"bvid": "BV1xx411c7mD", "title": "Stay", "author": "Random Stranger", "duration": 150}
        jio_wrong = {
            "id": "j1",
            "title": "Stay",
            "artist": "Random Stranger",
            "duration": 150,
            "encrypted_media_url": "dummy",
        }
        aud_wrong = {
            "id": "a1",
            "title": "Stay",
            "artist": "Random Stranger",
            "duration": 150,
            "is_streamable": True,
        }

        for relaxed in (False, True):
            self.assertLess(
                monochrome.score_candidate(mc_wrong, "Stay", "The Kid LAROI", "The Kid LAROI", target_duration=141, relaxed=relaxed),
                0.0,
            )
            self.assertLess(
                soundcloud.score_candidate(sc_wrong, "Stay", "The Kid LAROI", "The Kid LAROI", target_duration=141, relaxed=relaxed),
                0.0,
            )
            self.assertLess(
                bilibili.score_candidate(bili_wrong, "Stay", "The Kid LAROI", "The Kid LAROI", target_duration=141, relaxed=relaxed),
                0.0,
            )
            with mock.patch.object(jiosaavn, "search_songs", return_value=[jio_wrong]):
                self.assertIsNone(jiosaavn.find_best_stream("Stay", "The Kid LAROI", duration=141, relaxed=relaxed))
            with mock.patch.object(audius, "search_tracks", return_value=[aud_wrong]):
                self.assertIsNone(audius.find_best_stream("Stay", "The Kid LAROI", duration=141, relaxed=relaxed))

        # Unrequested remix/cover rejected in Pass 1 across Monochrome & SoundCloud
        mc_remix = {"id": "mc2", "title": "Stay (Club Remix)", "artist": "The Kid LAROI", "duration": 141}
        self.assertLess(
            monochrome.score_candidate(mc_remix, "Stay", "The Kid LAROI", "The Kid LAROI", target_duration=141, relaxed=False),
            0.0,
        )
        sc_cover = {
            "id": 12,
            "title": "Stay - The Kid LAROI (Acoustic Cover)",
            "duration": 141_000,
            "policy": "ALLOW",
            "user": {"username": "The Kid LAROI"},
            "media": {
                "transcodings": [
                    {"format": {"protocol": "hls"}, "url": "https://api-v2.soundcloud.com/media/stream/hls"}
                ]
            },
        }
        self.assertLess(
            soundcloud.score_candidate(sc_cover, "Stay", "The Kid LAROI", "The Kid LAROI", target_duration=141, relaxed=False),
            0.0,
        )

        # Ensure soundcloud._UPLOAD_JUNK_RE does NOT strip "free download"
        self.assertIn("free download", soundcloud._UPLOAD_JUNK_RE.sub("", "Song (free download)").lower())


class TestWebAudioEngineAndPlayerContracts(unittest.TestCase):
    """Suite 3: Web audioEffects.js suspended-AudioContext guard & player.js HLS/crossfade/alt-rescue fixes."""

    def test_audio_effects_and_player_contracts(self) -> None:
        audio_fx = (PROJECT_ROOT / "frontend" / "js" / "audioEffects.js").read_text(encoding="utf-8")
        player_js = (PROJECT_ROOT / "frontend" / "js" / "player.js").read_text(encoding="utf-8")

        # audioEffects.js: exports unlockAudioContextFromGesture and defers ensureEqGraph when !hasGesture
        self.assertIn("export function unlockAudioContextFromGesture(", audio_fx)
        self.assertIn("let gestureUnlocked = false;", audio_fx)
        self.assertIn("if (!hasGesture) return;", audio_fx)

        # player.js: gesture unlockers, pre-await unlocks, src swap before applyEqBands
        self.assertIn("unlockAudioContextFromGesture", player_js)
        self.assertIn("pointerdown", player_js)
        idx_src_swap = player_js.index("audioEl.src = activeProxyUrl;")
        idx_apply_eq = player_js.index("applyEqBands(audioEl, normalized);", idx_src_swap)
        self.assertLess(idx_src_swap, idx_apply_eq)

        # player.js: restricts raw hlsUrl to genuine Apple WebKit/Safari (not Desktop Chrome/Edge/Firefox)
        self.assertIn("isAppleNativeHls", player_js)
        self.assertIn("Chrome|Chromium|CriOS|Edg|OPR|Firefox", player_js)
        self.assertIn("if (info?.hlsUrl && isAppleNativeHls)", player_js)

        # player.js: crossfade volume recovery via isCrossfadeRampingOut
        self.assertIn("let isCrossfadeRampingOut = false;", player_js)
        self.assertIn("if (!cfActive && isCrossfadeRampingOut)", player_js)

        # player.js: strict identity check on 1-shot alternate-track rescue + badge sync on proxy fallback
        self.assertIn("function isSameSongCandidate(alt, target)", player_js)
        self.assertIn(".find((t) => isSameSongCandidate(t, track))", player_js)
        self.assertIn(".getStreamInfo(curTrack.id", player_js)


class TestIOSPlaybackCorrectnessContracts(unittest.TestCase):
    """Suite 4: Native iOS AVPlayer MIME lock, defaultRate, ?refresh=1, Offline magic-byte sniff & YouTube guards."""

    def test_ios_services_and_tests_contain_all_pillar4_guards(self) -> None:
        api_swift = (PROJECT_ROOT / "ios" / "Sonify" / "Services" / "APIClient.swift").read_text(encoding="utf-8")
        player_swift = (PROJECT_ROOT / "ios" / "Sonify" / "Services" / "AudioPlayerService.swift").read_text(encoding="utf-8")
        offline_swift = (PROJECT_ROOT / "ios" / "Sonify" / "Services" / "OfflineDownloadManager.swift").read_text(encoding="utf-8")
        yt_swift = (PROJECT_ROOT / "ios" / "Sonify" / "Services" / "YouTubeDirectResolver.swift").read_text(encoding="utf-8")
        yt_tests = (PROJECT_ROOT / "ios" / "SonifyTests" / "YouTubeDirectResolverTests.swift").read_text(encoding="utf-8")

        # APIClient: ?refresh=1
        self.assertIn("refresh: Bool = false", api_swift)
        self.assertIn('URLQueryItem(name: "refresh", value: "1")', api_swift)

        # AudioPlayerService: never force AVURLAssetOverrideMIMETypeKey on .proxy routes; defaultRate; refresh retry
        self.assertIn("if !route.isHLS && !route.isLocal && !route.isProxy", player_swift)
        self.assertIn("player.defaultRate =", player_swift)
        self.assertIn("didRetryFreshProxy", player_swift)
        self.assertIn("refresh: true", player_swift)

        # OfflineDownloadManager: proxy-failure YouTube fallback + 350KB floor + magic-byte sniffing
        self.assertIn("static func sniffAudioMIME(head: Data, fallback: String) -> String", offline_swift)
        self.assertIn("minNetworkBytes", offline_swift)
        self.assertIn("YouTubeDirectResolver.shared.resolveFullTrack(for: track)", offline_swift)
        self.assertIn("0x66 && bytes[1] == 0x4C && bytes[2] == 0x61 && bytes[3] == 0x43", offline_swift)

        # YouTubeDirectResolver & XCTests: asymmetric duration + hard/derivative reject + closed zero-artist loophole
        self.assertIn("maxShorter", yt_swift)
        self.assertIn("hardRejectKeywords", yt_swift)
        self.assertIn("derivativeKeywords", yt_swift)
        self.assertIn("choppedEdit", yt_tests)
        self.assertIn("genericWrongArtist", yt_tests)


if __name__ == "__main__":
    unittest.main()
