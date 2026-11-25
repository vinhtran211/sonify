"""Automated unit & HTTP integration tests for Section 5 of docs/STREAMING_SPEED_OPTIMIZATION.md."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from backend import http_client
from backend.db import Database
from backend.providers import monochrome, soundcloud
from backend.server import create_server
from backend.services.catalog_service import CatalogService
from backend.services.stream_service import ResolvedStream, StreamService

ROOT_DIR = Path(__file__).resolve().parent.parent


class TestMonochromeTailRangePassThrough(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "tail_pass.db")
        self.srv = create_server(host="127.0.0.1", port=0, db_path=self.db_path, offline=False, quiet=True)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_tail_range_uses_direct_passthrough_immediately_without_waiting_for_30mb_producer(self):
        total_size = 30_000_000
        head_chunk = b"fLaC" + b"H" * 131_068  # 131,072 bytes (128 KiB)
        tail_start = 29_934_464
        tail_end = 29_999_999
        tail_bytes = bytes((i * 11) % 256 for i in range(tail_end - tail_start + 1))  # 65,536 bytes
        release_producer = threading.Event()
        tail_calls = []

        class _StalledProducerResp:
            def __init__(self):
                self._sent = False

            def read(self, n: int = 65536) -> bytes:
                if not self._sent:
                    self._sent = True
                    return head_chunk
                release_producer.wait(timeout=5.0)
                return b""

            def close(self) -> None:
                pass

        class _DirectTailResp:
            def __init__(self):
                self._sent = False

            def read(self, n: int = 65536) -> bytes:
                if not self._sent:
                    self._sent = True
                    return tail_bytes
                return b""

            def close(self) -> None:
                pass

        def fake_open_stream(url, headers=None, range_header=None, **kw):
            if range_header is None:
                return 200, {
                    "content-type": "application/octet-stream",
                    "content-length": str(total_size),
                }, _StalledProducerResp()
            if range_header == f"bytes={tail_start}-{tail_end}":
                tail_calls.append(range_header)
                return 206, {
                    "content-type": "application/octet-stream",
                    "content-length": str(len(tail_bytes)),
                    "content-range": f"bytes {tail_start}-{tail_end}/{total_size}",
                }, _DirectTailResp()
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        cache_dir = Path(self._tmp.name) / "mc_tail_cache"
        self.srv.stream._store_cache(
            ResolvedStream(
                track_id="dz:tail30m",
                mode="full",
                source="monochrome",
                content_type="audio/flac",
                url=f"{monochrome.MONOCHROME_BASE}/track/300001",
                headers={"User-Agent": "SonifyTest"},
            )
        )

        with mock.patch.object(monochrome, "CACHE_DIR", cache_dir), \
             mock.patch.object(monochrome, "PACE_INTERVAL_SEC", 0.0), \
             mock.patch.dict(monochrome._ACTIVE_DOWNLOADS, {}, clear=True), \
             mock.patch.dict(monochrome._MIME_CACHE, {}, clear=True), \
             mock.patch("backend.http_client.open_stream", side_effect=fake_open_stream):
            try:
                t0 = time.monotonic()
                req = urllib.request.Request(
                    f"{self.base}/api/stream/dz:tail30m.flac",
                    headers={"Range": f"bytes={tail_start}-{tail_end}"},
                )
                with urllib.request.urlopen(req, timeout=5) as r:
                    body = r.read()
                    dt = time.monotonic() - t0
                    self.assertEqual(r.status, 206)
                    self.assertEqual(body, tail_bytes)
                    self.assertEqual(r.headers.get("Content-Type"), "audio/flac")
                    self.assertEqual(r.headers.get("Content-Range"), f"bytes {tail_start}-{tail_end}/{total_size}")
                    self.assertLess(dt, 1.0, f"Tail pass-through should not wait for producer, took {dt:.2f}s")
                    self.assertFalse(release_producer.is_set())
                    self.assertEqual(tail_calls, [f"bytes={tail_start}-{tail_end}"])
            finally:
                release_producer.set()

    def test_tail_range_429_falls_back_cleanly_to_producer_cond_wait(self):
        head_chunk = b"fLaC" + b"A" * 131_068  # 128 KiB
        mid_chunk = b"B" * 655_360             # 640 KiB
        tail_chunk = b"C" * 65_536             # 64 KiB -> total 851,968 bytes
        full_payload = head_chunk + mid_chunk + tail_chunk
        tail_start = len(full_payload) - 65_536
        tail_end = len(full_payload) - 1
        allow_finish = threading.Event()
        tail_429_seen = []

        class _ProgressiveResp:
            def __init__(self):
                self._stage = 0

            def read(self, n: int = 65536) -> bytes:
                if self._stage == 0:
                    self._stage = 1
                    return head_chunk
                if self._stage == 1:
                    allow_finish.wait(timeout=3.0)
                    self._stage = 2
                    return mid_chunk
                if self._stage == 2:
                    self._stage = 3
                    return tail_chunk
                return b""

            def close(self) -> None:
                pass

        def fake_open_stream(url, headers=None, range_header=None, **kw):
            if range_header is None:
                return 200, {
                    "content-type": "application/octet-stream",
                    "content-length": str(len(full_payload)),
                }, _ProgressiveResp()
            tail_429_seen.append(range_header)
            # Release producer right after direct tail gets 429 so cond.wait() completes smoothly
            allow_finish.set()
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

        cache_dir = Path(self._tmp.name) / "mc_tail_429_cache"
        self.srv.stream._store_cache(
            ResolvedStream(
                track_id="dz:tail429",
                mode="full",
                source="monochrome",
                content_type="audio/flac",
                url=f"{monochrome.MONOCHROME_BASE}/track/300429",
                headers={"User-Agent": "SonifyTest"},
            )
        )

        with mock.patch.object(monochrome, "CACHE_DIR", cache_dir), \
             mock.patch.object(monochrome, "PACE_INTERVAL_SEC", 0.0), \
             mock.patch.dict(monochrome._ACTIVE_DOWNLOADS, {}, clear=True), \
             mock.patch.dict(monochrome._MIME_CACHE, {}, clear=True), \
             mock.patch("backend.http_client.open_stream", side_effect=fake_open_stream):
            req = urllib.request.Request(
                f"{self.base}/api/stream/dz:tail429.flac",
                headers={"Range": f"bytes={tail_start}-{tail_end}"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                body = r.read()
                self.assertEqual(r.status, 206)
                self.assertEqual(body, tail_chunk)
                self.assertEqual(len(tail_429_seen), 1)


class TestParallelCloudProviderRaceAndEdgeURLs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self._tmp.name, "race.db"))
        self.stream = StreamService(self.db)
        http_client.set_offline_mode(False)

    def tearDown(self):
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_parallel_race_overlapping_timing_studio_master_preference_and_no_deezer_isrc_fetch(self):
        self.db.upsert_track({
            "id": "dz:555111",
            "source": "deezer",
            "title": "Fast Track",
            "artist": "Global Artist",
            "duration": 210,
            "previewUrl": "https://preview.dz/555.mp3",
        })

        # 1. Monochrome slow miss (0.35s) + SoundCloud fast hit (0.05s) run in parallel, ZERO deezer.get_track calls
        def slow_mc_miss(**kw):
            time.sleep(0.35)
            return None

        def fast_sc_hit(*args, **kw):
            time.sleep(0.05)
            return {
                "kind": "hls",
                "m3u8_url": "https://playback.media-streaming.soundcloud.cloud/fast.m3u8",
                "cache_key": "sc:555",
                "content_type": "audio/mp4",
            }

        with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=True), \
             mock.patch("backend.providers.deezer.get_track") as m_dz_get, \
             mock.patch("backend.providers.monochrome.find_best_stream", side_effect=slow_mc_miss), \
             mock.patch("backend.providers.soundcloud.find_best_stream", side_effect=fast_sc_hit), \
             mock.patch("backend.providers.soundcloud.start_background_hls_assembly") as m_sc_warm:
            t0 = time.monotonic()
            res = self.stream.resolve_stream("dz:555111", force_refresh=True)
            elapsed = time.monotonic() - t0
            self.assertEqual((res.mode, res.source), ("full", "soundcloud"))
            self.assertLess(elapsed, 0.70, f"Expected parallel overlap ~0.35s, got {elapsed:.2f}s")
            m_dz_get.assert_not_called()
            self.assertTrue(any(d.get("parallel") is True for d in res.diagnostics))

            # /info exposes hlsUrl (NOT directUrl) and triggers single-flight SC background HLS pre-warm
            info = self.stream.get_stream_info("dz:555111")
            self.assertEqual(info.get("hlsUrl"), "https://playback.media-streaming.soundcloud.cloud/fast.m3u8")
            self.assertNotIn("directUrl", info)
            m_sc_warm.assert_called_once()

        # 2. When Monochrome matches within grace window (0.15s) while SC finishes in 0.05s, Monochrome still wins!
        def fast_mc_hit(**kw):
            time.sleep(0.15)
            return (f"{monochrome.MONOCHROME_BASE}/track/888999", "audio/flac", "888999")

        with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=True), \
             mock.patch("backend.providers.monochrome.find_best_stream", side_effect=fast_mc_hit), \
             mock.patch("backend.providers.soundcloud.find_best_stream", side_effect=fast_sc_hit), \
             mock.patch("backend.providers.monochrome.start_background_cache"):
            res_mc = self.stream.resolve_stream("dz:555111", force_refresh=True)
            self.assertEqual((res_mc.mode, res_mc.source), ("full", "monochrome"))

            # Monochrome MUST NOT expose directUrl or hlsUrl
            info_mc = self.stream.get_stream_info("dz:555111")
            self.assertNotIn("directUrl", info_mc)
            self.assertNotIn("hlsUrl", info_mc)

    def test_parallel_race_losing_worker_never_overwrites_winner_cache(self):
        # Regression: when Monochrome wins fast (50ms) and SoundCloud finishes later (250ms),
        # the losing SoundCloud worker MUST NOT overwrite self.stream._cache with SoundCloud
        # (which previously broke in-flight Monochrome tail Range requests with HTTP 416!).
        sc_done = threading.Event()

        def fast_mc(**kw):
            time.sleep(0.05)
            return (f"{monochrome.MONOCHROME_BASE}/track/777666", "audio/flac", "777666")

        def slow_sc(*args, **kwargs):
            time.sleep(0.25)
            try:
                return {
                    "kind": "hls",
                    "m3u8_url": "https://cf-hls-media.sndcdn.com/playlist/late-loser.m3u8",
                    "content_type": "audio/mp4",
                    "cache_key": "sc:late",
                }
            finally:
                sc_done.set()

        with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=True), \
             mock.patch("backend.providers.monochrome.find_best_stream", side_effect=fast_mc), \
             mock.patch("backend.providers.soundcloud.find_best_stream", side_effect=slow_sc):
            winner = self.stream.resolve_stream("dz:555111", force_refresh=True)
            self.assertEqual(winner.source, "monochrome")
            # Wait until losing SoundCloud thread finishes completely
            self.assertTrue(sc_done.wait(timeout=2.0))
            time.sleep(0.05)
            # Cache MUST still belong to Monochrome!
            cached = self.stream._cache.get("dz:555111")
            self.assertIsNotNone(cached)
            self.assertEqual(cached.source, "monochrome")
            self.assertEqual(cached.content_type, "audio/flac")

    def test_direct_url_only_exposed_for_ip_agnostic_cdns_never_youtube_or_monochrome(self):
        # JioSaavn -> exposes directUrl
        self.stream._store_cache(
            ResolvedStream(
                track_id="js:direct1",
                mode="full",
                source="jiosaavn",
                content_type="audio/mp4",
                url="https://aac.saavncdn.com/track_320.mp4",
            )
        )
        info_js = self.stream.get_stream_info("js:direct1")
        self.assertEqual(info_js.get("directUrl"), "https://aac.saavncdn.com/track_320.mp4")
        self.assertNotIn("hlsUrl", info_js)

        # Audius -> exposes directUrl
        self.stream._store_cache(
            ResolvedStream(
                track_id="au:direct1",
                mode="full",
                source="audius",
                content_type="audio/mpeg",
                url="https://creatornode.audius.co/stream.mp3",
            )
        )
        info_au = self.stream.get_stream_info("au:direct1")
        self.assertEqual(info_au.get("directUrl"), "https://creatornode.audius.co/stream.mp3")

        # YouTube -> NEVER exposes directUrl or hlsUrl (IP-bound googlevideo signature)
        self.stream._store_cache(
            ResolvedStream(
                track_id="yt:bound1",
                mode="full",
                source="youtube-innertube",
                content_type="video/mp4",
                url="https://rr1.googlevideo.com/videoplayback?sig=ipbound",
            )
        )
        info_yt = self.stream.get_stream_info("yt:bound1")
        self.assertNotIn("directUrl", info_yt)
        self.assertNotIn("hlsUrl", info_yt)


class TestSharedHomeCacheAndFrontendIOSContracts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self._tmp.name, "home_cache.db"))
        self.catalog = CatalogService(self.db)
        http_client.set_offline_mode(False)

    def tearDown(self):
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_shared_home_base_cache_reuses_single_upstream_fetch_for_global_and_vn(self):
        fake_chart = {
            "tracks": [{"id": "dz:1", "source": "deezer", "title": "Global Hit", "artist": "Star", "duration": 200}],
            "albums": [{"id": "dz:a1", "source": "deezer", "title": "Hit Album", "artist": "Star"}],
            "artists": [{"id": "dz:ar1", "source": "deezer", "name": "Star"}],
        }
        fake_vn = [{"id": "it:vn1", "source": "itunes", "title": "VPop Hit", "artist": "VN Star", "duration": 230}]

        with mock.patch("backend.providers.deezer.get_chart", return_value=fake_chart) as m_chart, \
             mock.patch("backend.providers.deezer.get_new_releases", return_value=[]), \
             mock.patch("backend.providers.apple_itunes.get_top_songs", return_value=fake_vn), \
             mock.patch("backend.providers.apple_itunes.get_top_albums", return_value=[]), \
             mock.patch("backend.providers.audius.get_trending", return_value=[]):
            home_g = self.catalog.get_home("global")
            self.assertEqual(home_g["sections"][0]["id"], "trending-global")
            self.assertEqual(m_chart.call_count, 1)

            t0 = time.monotonic()
            home_vn = self.catalog.get_home("vn")
            dt_ms = (time.monotonic() - t0) * 1000.0
            self.assertEqual(home_vn["sections"][0]["id"], "trending-vn")
            self.assertEqual(m_chart.call_count, 1, "Switching global -> vn must reuse shared base cache")
            self.assertLess(dt_ms, 50.0)

    def test_web_and_ios_direct_fastpath_and_queue_prefetch_present(self):
        player_js = (ROOT_DIR / "frontend" / "js" / "player.js").read_text(encoding="utf-8")
        for needle in (
            "info?.hlsUrl",
            "info?.directUrl",
            "attemptProxyFallback",
            "scheduleNextTrackPrefetch",
            "prefetchedInfoCache",
        ):
            self.assertIn(needle, player_js)

        ios_player = (ROOT_DIR / "ios" / "Sonify" / "Services" / "AudioPlayerService.swift").read_text(encoding="utf-8")
        for needle in (
            "preferredPlaybackRoute",
            "scheduleNextTrackPrefetch",
            "preferredForwardBufferDuration = 2.0",
            "didFallbackToProxy",
        ):
            self.assertIn(needle, ios_player)


class TestMonochromeProducerRangeResumeIntegrity(unittest.TestCase):
    def test_producer_resumes_on_early_eof_via_http_range_and_completes_intact(self):
        # Simulates tracks.monochrome.st closing a 600 KiB transfer early at 240 KiB
        part1 = b"fLaC" + bytes((i % 251) for i in range(240_000 - 4))
        part2 = bytes(((i + 7) % 251) for i in range(240_000))
        part3 = bytes(((i + 19) % 251) for i in range(120_000))
        full = part1 + part2 + part3
        seen_ranges = []

        class _ChunkResp:
            def __init__(self, payload: bytes):
                self._mv = memoryview(payload)
                self._off = 0

            def read(self, n: int = 65536) -> bytes:
                if self._off >= len(self._mv):
                    return b""
                out = bytes(self._mv[self._off : self._off + n])
                self._off += len(out)
                return out

            def close(self) -> None:
                pass

        def fake_open_stream(url, headers=None, range_header=None, **kw):
            seen_ranges.append(range_header)
            if range_header is None:
                return 200, {
                    "content-type": "application/octet-stream",
                    "content-length": str(len(full)),
                }, _ChunkResp(part1)
            if range_header == f"bytes={len(part1)}-":
                return 206, {
                    "content-type": "application/octet-stream",
                    "content-length": str(len(full) - len(part1)),
                    "content-range": f"bytes {len(part1)}-{len(full)-1}/{len(full)}",
                }, _ChunkResp(part2)
            if range_header == f"bytes={len(part1) + len(part2)}-":
                return 206, {
                    "content-type": "application/octet-stream",
                    "content-length": str(len(part3)),
                    "content-range": f"bytes {len(part1) + len(part2)}-{len(full)-1}/{len(full)}",
                }, _ChunkResp(part3)
            raise AssertionError(f"Unexpected range {range_header}")

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "mc_resume"
            with mock.patch.object(monochrome, "CACHE_DIR", cache_dir), \
                 mock.patch.object(monochrome, "PACE_INTERVAL_SEC", 0.0), \
                 mock.patch.dict(monochrome._ACTIVE_DOWNLOADS, {}, clear=True), \
                 mock.patch.dict(monochrome._MIME_CACHE, {}, clear=True), \
                 mock.patch("backend.http_client.open_stream", side_effect=fake_open_stream):
                handle = monochrome.get_or_start_download("resume999")
                self.assertIsNotNone(handle)
                ok = handle.wait_until_done(timeout=10.0)
                self.assertTrue(ok)
                self.assertFalse(handle.failed)
                self.assertEqual(handle.bytes_written, len(full))
                self.assertEqual(handle.total_size, len(full))
                self.assertTrue(handle.final_path.is_file())
                self.assertEqual(handle.final_path.read_bytes(), full)
                self.assertEqual(
                    seen_ranges,
                    [None, f"bytes={len(part1)}-", f"bytes={len(part1) + len(part2)}-"],
                )

    def test_producer_never_caches_truncated_file_when_resume_fails(self):
        # Simulates upstream sending Content-Length=1,000,000, dropping at 300,000, and rejecting resume
        partial = b"fLaC" + b"X" * (300_000 - 4)

        class _TruncResp:
            def __init__(self):
                self._sent = False

            def read(self, n: int = 65536) -> bytes:
                if not self._sent:
                    self._sent = True
                    return partial
                return b""

            def close(self) -> None:
                pass

        def fake_open_stream(url, headers=None, range_header=None, **kw):
            if range_header is None:
                return 200, {
                    "content-type": "application/octet-stream",
                    "content-length": "1000000",
                }, _TruncResp()
            raise urllib.error.HTTPError(url, 502, "Bad Gateway", {}, None)

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "mc_trunc"
            with mock.patch.object(monochrome, "CACHE_DIR", cache_dir), \
                 mock.patch.object(monochrome, "PACE_INTERVAL_SEC", 0.0), \
                 mock.patch.dict(monochrome._ACTIVE_DOWNLOADS, {}, clear=True), \
                 mock.patch.dict(monochrome._MIME_CACHE, {}, clear=True), \
                 mock.patch("backend.http_client.open_stream", side_effect=fake_open_stream):
                handle = monochrome.get_or_start_download("trunc999")
                self.assertIsNotNone(handle)
                ok = handle.wait_until_done(timeout=10.0)
                self.assertFalse(ok)
                self.assertTrue(handle.failed)
                self.assertFalse(handle.final_path.is_file(), "Truncated .part must NEVER be promoted to .bin")
                self.assertFalse(handle.part_path.is_file())


if __name__ == "__main__":
    unittest.main()
