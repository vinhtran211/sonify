"""Automated unit & integration tests for Section 3.1 of docs/ZERO_PREVIEW_ELIMINATION.md."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from backend import config, http_client, lrc_parser
from backend.db import Database
from backend.providers import audius, bilibili, jiosaavn, monochrome, soundcloud
from backend.services.stream_service import StreamService, StreamUnavailableError

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
IOS_DIR = ROOT_DIR / "ios"


class TestZeroVoluntaryPreviewsAndTwoPassRescue(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self._tmp.name, "zero_prev.db"))
        self.stream = StreamService(self.db)
        http_client.set_offline_mode(False)
        self.db.upsert_track(
            {
                "id": "dz:888777",
                "source": "deezer",
                "title": "Super Shy",
                "artist": "NewJeans",
                "duration": 155,
                "previewUrl": "https://cdns-preview.dzcdn.net/stream/c-30s.mp3",
            }
        )

    def tearDown(self):
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_default_disables_30s_previews_and_opt_in_env_enables(self):
        with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=True), \
             mock.patch("backend.providers.monochrome.find_best_stream", return_value=None), \
             mock.patch("backend.providers.soundcloud.find_best_stream", return_value=None), \
             mock.patch("backend.providers.jiosaavn.find_best_stream", return_value=None), \
             mock.patch("backend.providers.bilibili.find_best_stream", return_value=None), \
             mock.patch("backend.providers.audius.find_best_stream", return_value=None), \
             mock.patch("backend.http_client.probe_url", return_value=(True, 206, {"content-type": "audio/mpeg"}, "https://cdns-preview.dzcdn.net/stream/c-30s.mp3")):
            # Default: SONIFY_ALLOW_PREVIEWS is False -> raises StreamUnavailableError (502), NEVER 30s preview!
            self.assertFalse(config.allow_previews())
            with self.assertRaises(StreamUnavailableError) as ctx:
                self.stream.resolve_stream("dz:888777", force_refresh=True)
            self.assertEqual(ctx.exception.status, 502)

        # Opt-in SONIFY_ALLOW_PREVIEWS=1 still serves preview when explicitly requested
        with mock.patch.dict(os.environ, {"RENDER": "true", "SONIFY_ALLOW_PREVIEWS": "1"}, clear=True), \
             mock.patch("backend.providers.monochrome.find_best_stream", return_value=None), \
             mock.patch("backend.providers.soundcloud.find_best_stream", return_value=None), \
             mock.patch("backend.providers.jiosaavn.find_best_stream", return_value=None), \
             mock.patch("backend.providers.bilibili.find_best_stream", return_value=None), \
             mock.patch("backend.providers.audius.find_best_stream", return_value=None), \
             mock.patch("backend.http_client.probe_url", return_value=(True, 206, {"content-type": "audio/mpeg"}, "https://cdns-preview.dzcdn.net/stream/c-30s.mp3")):
            self.assertTrue(config.allow_previews())
            res = self.stream.resolve_stream("dz:888777", force_refresh=True)
            self.assertEqual((res.mode, res.source), ("preview", "deezer-preview"))

    def test_two_pass_rescue_engine_recovers_label_channel_mv_while_rejecting_snips(self):
        # Simulate a label-uploaded Official MV on Bilibili ("HYBE LABELS", duration +32s):
        # Pass 1 (strict) rejects due to MV/duration/channel mismatch; Pass 2 (relaxed=True) rescues it!
        mv_item = {
            "bvid": "BV1HYBEMV",
            "title": "NewJeans (뉴진스) 'Super Shy' Official MV",
            "author": "HYBE LABELS",
            "duration": 200,  # +45s over 155s studio track -> rejected in Pass 1 (max_diff 38s), accepted in Pass 2!
        }
        strict_sc = bilibili.score_candidate(
            mv_item, "Super Shy", "NewJeans", "NewJeans", target_duration=155, relaxed=False
        )
        relaxed_sc = bilibili.score_candidate(
            mv_item, "Super Shy", "NewJeans", "NewJeans", target_duration=155, relaxed=True
        )
        self.assertLess(strict_sc, 0.0, "Strict Pass 1 should reject +45s MV duration")
        self.assertGreaterEqual(relaxed_sc, 0.48, f"Relaxed Pass 2 must rescue label MV, got {relaxed_sc}")

        # Relaxed Pass 2 MUST still reject < 60s short clips!
        short_clip = {
            "bvid": "BV1TEASER",
            "title": "NewJeans 'Super Shy' Teaser",
            "author": "HYBE LABELS",
            "duration": 30,
        }
        self.assertLess(
            bilibili.score_candidate(short_clip, "Super Shy", "NewJeans", "NewJeans", target_duration=155, relaxed=True),
            0.0,
        )

        # End-to-end Pass 1 miss -> Pass 2 parallel rescue in StreamService
        def bili_side_effect(title, artist, duration=0, **kw):
            if kw.get("relaxed"):
                return (
                    "https://upos.bilivideo.com/hybe_supershy.m4s",
                    "audio/mp4",
                    {"User-Agent": "UA", "Referer": "https://www.bilibili.com/"},
                )
            return None

        with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=True), \
             mock.patch("backend.providers.monochrome.find_best_stream", return_value=None), \
             mock.patch("backend.providers.soundcloud.find_best_stream", return_value=None), \
             mock.patch("backend.providers.jiosaavn.find_best_stream", return_value=None), \
             mock.patch("backend.providers.audius.find_best_stream", return_value=None), \
             mock.patch("backend.providers.bilibili.find_best_stream", side_effect=bili_side_effect):
            res = self.stream.resolve_stream("dz:888777", force_refresh=True)
            self.assertEqual((res.mode, res.source), ("full", "bilibili"))
            self.assertTrue(
                any(d.get("provider") == "bilibili-rescue" and d.get("outcome") == "ok" for d in res.diagnostics),
                f"Expected bilibili-rescue in diagnostics: {res.diagnostics}",
            )


class TestSoundCloudDRMMonochromeCapAndShortClipFloors(unittest.TestCase):
    def test_soundcloud_drm_only_item_filtered_before_scoring(self):
        drm_only_item = {
            "id": 2384261229,
            "title": "Tình Cờ",
            "user": {"username": "Lope Dope"},
            "policy": "ALLOW",
            "duration": 210_000,
            "media": {
                "transcodings": [
                    {
                        "url": "https://api-v2.soundcloud.com/media/drm1",
                        "preset": "aac_160k",
                        "snipped": False,
                        "format": {"protocol": "cbc-encrypted-hls", "mime_type": "audio/mp4"},
                    },
                    {
                        "url": "https://api-v2.soundcloud.com/media/drm2",
                        "preset": "aac_160k",
                        "snipped": False,
                        "format": {"protocol": "ctr-encrypted-hls", "mime_type": "audio/mp4"},
                    },
                ]
            },
        }
        playable_item = {
            "id": 2384261230,
            "title": "Tình Cờ",
            "user": {"username": "Lope Dope"},
            "policy": "ALLOW",
            "duration": 210_000,
            "media": {
                "transcodings": [
                    {
                        "url": "https://api-v2.soundcloud.com/media/clean_hls",
                        "preset": "aac_160k",
                        "snipped": False,
                        "format": {"protocol": "hls", "mime_type": 'audio/mp4; codecs="mp4a.40.2"'},
                    }
                ]
            },
        }
        self.assertFalse(soundcloud.has_playable_transcoding(drm_only_item))
        self.assertFalse(soundcloud.is_full_allow_track(drm_only_item, target_duration=210))
        self.assertTrue(soundcloud.has_playable_transcoding(playable_item))
        self.assertTrue(soundcloud.is_full_allow_track(playable_item, target_duration=210))

        # SNIP policy and /preview/ URLs are ALWAYS rejected even in relaxed mode
        snip_item = dict(playable_item, policy="SNIP")
        self.assertFalse(soundcloud.is_full_allow_track(snip_item, target_duration=210))

    def test_monochrome_concurrent_producer_cap_and_conditional_prewarm(self):
        block_event = threading.Event()
        head_chunk = b"fLaC" + b"M" * 65532

        class _BlockingResp:
            def __init__(self):
                self._sent = False

            def read(self, n: int = 65536) -> bytes:
                if not self._sent:
                    self._sent = True
                    return head_chunk
                block_event.wait(timeout=3.0)
                return b""

            def close(self) -> None:
                pass

        def fake_open_stream(url, **kw):
            return 200, {"content-type": "audio/flac", "content-length": "5000000"}, _BlockingResp()

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "mc_cap"
            with mock.patch.object(monochrome, "CACHE_DIR", cache_dir), \
                 mock.patch.object(monochrome, "PACE_INTERVAL_SEC", 0.0), \
                 mock.patch.dict(monochrome._ACTIVE_DOWNLOADS, {}, clear=True), \
                 mock.patch("backend.http_client.open_stream", side_effect=fake_open_stream):
                try:
                    h1 = monochrome.get_or_start_download("cap_1")
                    time.sleep(0.02)
                    h2 = monochrome.get_or_start_download("cap_2")
                    self.assertFalse(h1.cancel_event.is_set())
                    self.assertFalse(h2.cancel_event.is_set())

                    # Starting 3rd concurrent producer (> MAX_CONCURRENT_PRODUCERS=2) cancels oldest (h1)!
                    time.sleep(0.02)
                    h3 = monochrome.get_or_start_download("cap_3")
                    self.assertTrue(h1.cancel_event.is_set(), "Oldest active producer must be cancelled")
                    self.assertFalse(h2.cancel_event.is_set())
                    self.assertFalse(h3.cancel_event.is_set())
                    self.assertLessEqual(len(monochrome._ACTIVE_DOWNLOADS), monochrome.MAX_CONCURRENT_PRODUCERS)

                    # Speculative ?prewarm=1 must NOT preempt active downloads; active playback (prewarm=False)
                    # verifies Monochrome readiness so /info and /stream never disagree on codec!
                    db = Database(os.path.join(tmp, "prewarm.db"))
                    svc = StreamService(db)
                    db.upsert_track(
                        {
                            "id": "dz:prewarm_test",
                            "source": "deezer",
                            "title": "Prewarm Track",
                            "artist": "Artist",
                            "duration": 200,
                            "_extra": {"monochromeTrackId": "cap_999"},
                        }
                    )
                    with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=True), \
                         mock.patch("backend.providers.monochrome.start_background_cache") as m_bg:
                        svc.get_stream_info("dz:prewarm_test", prewarm=True)
                        m_bg.assert_not_called()

                        svc.get_stream_info("dz:prewarm_test", prewarm=False)
                        m_bg.assert_called_once_with("cap_999")
                finally:
                    block_event.set()

    def test_universal_60s_and_350kb_short_clip_floors_across_all_providers(self):
        # 1. lrc_parser.is_valid_full_duration
        self.assertFalse(lrc_parser.is_valid_full_duration(30, 0))
        self.assertFalse(lrc_parser.is_valid_full_duration(45, 200))
        self.assertTrue(lrc_parser.is_valid_full_duration(180, 0))
        # Genuine 45s interlude in catalog (30 <= target < 60) is allowed when within 12s
        self.assertTrue(lrc_parser.is_valid_full_duration(45, 45))

        # 2. Monochrome MIN_TRACK_BYTES >= 350KB & rejects 30s candidate
        self.assertGreaterEqual(monochrome.MIN_TRACK_BYTES, 350_000)
        self.assertLess(
            monochrome.score_candidate(
                {"id": "1", "title": "Song", "artist": "Artist", "duration": 30},
                "Song",
                "Artist",
                "Artist",
                target_duration=0,
                relaxed=True,
            ),
            0.0,
        )

        # 3. SoundCloud rejects 30s candidate when target_duration=0
        self.assertFalse(
            soundcloud.is_full_allow_track(
                {"policy": "ALLOW", "duration": 30_000, "title": "Song"}, target_duration=0
            )
        )

        # 4. JioSaavn & Audius enforce min_total_bytes=350_000 on probe_url
        with mock.patch("backend.http_client.probe_url", return_value=(False, 206, {}, "https://x")) as m_probe:
            jiosaavn.probe_best_url(["https://aac.saavncdn.com/short.mp4"])
            self.assertGreaterEqual(m_probe.call_args.kwargs.get("min_total_bytes", 0), 350_000)

        with mock.patch("backend.providers.audius.search_tracks", return_value=[
            {"id": "au:1", "title": "Song", "artist": "Artist", "duration": 200}
        ]), mock.patch("backend.http_client.probe_url", return_value=(False, 206, {}, "https://au")) as m_au_probe:
            audius.find_best_stream("Song", "Artist", duration=200)
            self.assertGreaterEqual(m_au_probe.call_args.kwargs.get("min_total_bytes", 0), 350_000)

    def test_client_210s_hls_ttl_web_auto_rescue_skip_and_ios_youtube_hardening(self):
        player_js = (FRONTEND_DIR / "js" / "player.js").read_text(encoding="utf-8")
        for needle in (
            "210_000",
            "consecutiveSkips",
            "Full track unavailable for",
            ".getStreamInfo(nextTrack.id, undefined, qual, true)",
        ):
            self.assertIn(needle, player_js)

        yt_swift = (IOS_DIR / "Sonify" / "Services" / "YouTubeDirectResolver.swift").read_text(encoding="utf-8")
        for needle in (
            "EgWKAQIIAWoKEAkQBRAKEAMQBA==",
            "350_000",
            "official audio",
            "relaxed: true",
        ):
            self.assertIn(needle, yt_swift)

        audio_swift = (IOS_DIR / "Sonify" / "Services" / "AudioPlayerService.swift").read_text(encoding="utf-8")
        for needle in ("210", "prewarm: true", "didAttemptYouTubeRescue"):
            self.assertIn(needle, audio_swift)


if __name__ == "__main__":
    unittest.main()
