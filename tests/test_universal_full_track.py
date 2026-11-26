"""Automated unit & HTTP integration tests for Section 4 of docs/UNIVERSAL_FULL_TRACK_FIX.md."""

from __future__ import annotations

import base64
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
from backend.providers import bilibili, jiosaavn, monochrome, soundcloud
from backend.server import create_server
from backend.services.catalog_service import CatalogService
from backend.services.stream_service import (
    ResolvedStream,
    SOUNDCLOUD_HLS_CACHE_TTL,
    StreamService,
    _cloudfront_policy_expiry,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
IOS_DIR = ROOT_DIR / "ios"


def _make_cf_policy_url(epoch_exp: int) -> str:
    policy = json.dumps(
        {"Statement": [{"Condition": {"DateLessThan": {"AWS:EpochTime": epoch_exp}}}]}
    ).encode("utf-8")
    b64 = base64.b64encode(policy).decode("ascii").replace("+", "-").replace("=", "_").replace("/", "~")
    return f"https://cf-hls-media.sndcdn.com/playlist/test.m3u8?Policy={b64}&Signature=abc"


class TestSoundCloudExpiryAndClientRotation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "sc_ttl.db")
        self.srv = create_server(host="127.0.0.1", port=0, db_path=self.db_path, offline=False, quiet=True)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_soundcloud_hls_ttl_and_cloudfront_policy_expiry_eviction(self):
        now = time.time()
        exp_soon = int(now + 20)  # within 60s safety margin -> already expired!
        exp_later = int(now + 600)

        self.assertEqual(_cloudfront_policy_expiry(_make_cf_policy_url(exp_later)), float(exp_later))

        # 1. SoundCloud HLS older than SOUNDCLOUD_HLS_CACHE_TTL (240s) is expired
        old_sc = ResolvedStream(
            track_id="dz:sc_old",
            mode="full",
            source="soundcloud",
            content_type="audio/mp4",
            url=_make_cf_policy_url(exp_later),
            headers={"X-SC-M3U8": _make_cf_policy_url(exp_later)},
            resolved_at=now - (SOUNDCLOUD_HLS_CACHE_TTL + 5),
        )
        self.srv.stream._store_cache(old_sc)
        self.assertIsNone(self.srv.stream._get_valid_cached("dz:sc_old", now))

        # 2. Freshly cached entry whose CloudFront Policy AWS:EpochTime is < 60s away is expired
        soon_url = _make_cf_policy_url(exp_soon)
        soon_sc = ResolvedStream(
            track_id="dz:sc_soon",
            mode="full",
            source="soundcloud",
            content_type="audio/mp4",
            url=soon_url,
            headers={"X-SC-M3U8": soon_url},
            resolved_at=now - 5,
        )
        self.srv.stream._store_cache(soon_sc)
        self.assertIsNone(self.srv.stream._get_valid_cached("dz:sc_soon", now))

    def test_stale_soundcloud_hls_retries_soundcloud_without_excluding_or_downgrading(self):
        now = time.time()
        stale_url = "https://cf-hls-media.sndcdn.com/playlist/expired.m3u8"
        fresh_url = "https://cf-hls-media.sndcdn.com/playlist/fresh.m3u8"
        audio_bytes = b"\x00\x00\x00\x1cftypisom" + b"S" * 65536

        self.srv.db.upsert_track(
            {
                "id": "it:6827404203",
                "source": "itunes",
                "title": "Stale HLS Test",
                "artist": "Test Artist",
                "duration": 200,
                "previewUrl": "https://preview.apple/30s.m4a",
            }
        )
        self.srv.stream._store_cache(
            ResolvedStream(
                track_id="it:6827404203",
                mode="full",
                source="soundcloud",
                content_type="audio/mp4",
                url=stale_url,
                headers={"X-SC-M3U8": stale_url, "X-SC-Cache-Key": "sc:old"},
                resolved_at=now - 45.0,  # > 30s stale!
            )
        )

        seen_excluded = []

        def fake_assemble(m3u8_url, cache_key="", mime_hint="audio/mpeg"):
            if m3u8_url == stale_url:
                return None  # simulates 403 expired CloudFront segments
            return audio_bytes, "audio/mp4"

        orig_resolve = self.srv.stream.resolve_stream

        def spy_resolve(tid, force_refresh=False, exclude_sources=None, quality="auto"):
            seen_excluded.append(set(exclude_sources or ()))
            if force_refresh:
                fresh = ResolvedStream(
                    track_id=tid,
                    mode="full",
                    source="soundcloud",
                    content_type="audio/mp4",
                    url=fresh_url,
                    headers={"X-SC-M3U8": fresh_url, "X-SC-Cache-Key": "sc:fresh"},
                )
                self.srv.stream._store_cache(fresh)
                return fresh
            return orig_resolve(tid, force_refresh=force_refresh, exclude_sources=exclude_sources, quality=quality)

        with mock.patch("backend.providers.soundcloud.assemble_hls_segments", side_effect=fake_assemble), \
             mock.patch.object(self.srv.stream, "resolve_stream", side_effect=spy_resolve):
            req = urllib.request.Request(
                f"{self.base}/api/stream/it%3A6827404203.m4a",
                headers={"Range": "bytes=0-15"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(r.status, 206)
                self.assertEqual(r.read(), audio_bytes[:16])
                self.assertEqual(r.headers.get("X-Stream-Mode"), "full")
                self.assertEqual(r.headers.get("X-Stream-Source"), "soundcloud")

        # Stale retry MUST NOT exclude "soundcloud" (no silent downgrade to 30s preview!)
        self.assertGreaterEqual(len(seen_excluded), 2)
        self.assertNotIn("soundcloud", seen_excluded[1])

    def test_soundcloud_transcoding_401_refreshes_client_id_once_and_succeeds(self):
        item = {
            "id": 999111,
            "media": {
                "transcodings": [
                    {
                        "url": "https://api-v2.soundcloud.com/media/track/stream/hls",
                        "format": {"protocol": "hls", "mime_type": 'audio/mp4; codecs="mp4a.40.2"'},
                        "snipped": False,
                    }
                ]
            },
        }
        calls = []

        def fake_fetch_json(url, **kw):
            calls.append(url)
            if "client_id=old_id" in url:
                raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
            return {"url": "https://cf-hls-media.sndcdn.com/playlist/rotated.m3u8"}

        with mock.patch("backend.providers.soundcloud.get_client_id", return_value="fresh_id") as m_rot, \
             mock.patch("backend.http_client.fetch_json", side_effect=fake_fetch_json):
            res = soundcloud._resolve_item_stream(item, "old_id", assemble_now=False)
            self.assertIsNotNone(res)
            self.assertEqual(res["kind"], "hls")
            self.assertEqual(res["m3u8_url"], "https://cf-hls-media.sndcdn.com/playlist/rotated.m3u8")
            m_rot.assert_called_once_with(force_refresh=True)
            self.assertEqual(len(calls), 2)


class TestLateMonochromeWaitAndBilibiliProvider(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self._tmp.name, "univ.db"))
        self.stream = StreamService(self.db)
        http_client.set_offline_mode(False)
        self.db.upsert_track(
            {
                "id": "dz:707070",
                "source": "deezer",
                "title": "Die With A Smile (with Bruno Mars)",
                "artist": "Lady Gaga, Bruno Mars",
                "duration": 251,
                "previewUrl": "https://preview.dz/dwas.mp3",
            }
        )

    def tearDown(self):
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_late_monochrome_wait_recovers_full_track_when_soundcloud_misses(self):
        # Monochrome takes 0.25s (> patched 0.05s grace), SoundCloud misses fast (0.02s).
        # Must wait for late Monochrome instead of falling back to 30s preview!
        def slow_mc(**kw):
            time.sleep(0.25)
            return (f"{monochrome.MONOCHROME_BASE}/track/707070", "audio/flac", "707070")

        with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=True), \
             mock.patch("backend.services.stream_service.MONOCHROME_PARALLEL_GRACE_SEC", 0.05), \
             mock.patch("backend.providers.monochrome.find_best_stream", side_effect=slow_mc), \
             mock.patch("backend.providers.soundcloud.find_best_stream", return_value=None), \
             mock.patch("backend.providers.jiosaavn.find_best_stream", return_value=None), \
             mock.patch("backend.providers.bilibili.find_best_stream", return_value=None), \
             mock.patch("backend.providers.audius.find_best_stream", return_value=None):
            res = self.stream.resolve_stream("dz:707070", force_refresh=True)
            self.assertEqual((res.mode, res.source), ("full", "monochrome"))

        self.assertEqual(
            monochrome._clean_search_title('Die With A Smile (with Bruno Mars)'),
            "Die With A Smile",
        )
        self.assertEqual(
            monochrome._clean_search_title('What Was I Made For? (From "Barbie")'),
            "What Was I Made For?",
        )

    def test_bilibili_helpers_strict_scoring_dash_extraction_and_tier45_waterfall(self):
        # 1. HTML stripping & duration parsing
        self.assertEqual(bilibili.strip_html('<em class="keyword">Blinding</em> &amp; Lights'), "Blinding & Lights")
        self.assertEqual(bilibili.parse_duration_seconds("3:20"), 200)
        self.assertEqual(bilibili.parse_duration_seconds("1:02:03"), 3723)

        # 2. buvid3/buvid4 caching
        with mock.patch.object(bilibili, "_CACHED_COOKIE", ""), \
             mock.patch.object(bilibili, "_CACHED_COOKIE_AT", 0.0), \
             mock.patch("backend.http_client.fetch_json", return_value={"data": {"b_3": "B3VAL", "b_4": "B4VAL"}}) as m_spi:
            c1 = bilibili.get_buvid_cookie()
            c2 = bilibili.get_buvid_cookie()
            self.assertEqual(c1, "buvid3=B3VAL; buvid4=B4VAL")
            self.assertEqual(c2, c1)
            self.assertEqual(m_spi.call_count, 1)

        # 3. Strict candidate scoring (accept genuine, reject karaoke/cover/翻唱/short/wrong artist)
        good_item = {
            "bvid": "BV1GOOD",
            "title": "【Hi-Res】The Weeknd - Blinding Lights (Official Audio)",
            "author": "PopMusicHD",
            "duration": 200,
        }
        good_sc = bilibili.score_candidate(
            good_item, "Blinding Lights", "The Weeknd", "The Weeknd", target_duration=200
        )
        self.assertGreaterEqual(good_sc, 0.70)

        for bad_item in (
            {"bvid": "BV1COV", "title": "The Weeknd - Blinding Lights 翻唱 伴奏", "author": "CoverGirl", "duration": 200},
            {"bvid": "BV1KAR", "title": "Blinding Lights Karaoke Piano Cover - The Weeknd", "author": "Karaoke", "duration": 200},
            {"bvid": "BV1SHORT", "title": "The Weeknd - Blinding Lights", "author": "Clips", "duration": 35},
            {"bvid": "BV1WRONG", "title": "Completely Unrelated Song", "author": "Other", "duration": 200},
        ):
            bad_sc = bilibili.score_candidate(
                bad_item, "Blinding Lights", "The Weeknd", "The Weeknd", target_duration=200
            )
            self.assertLess(bad_sc, 0.62, f"Expected bad Bilibili candidate to be rejected: {bad_item}")

        # 4. DASH audio extraction + Referer probe
        with mock.patch("backend.providers.bilibili.get_buvid_cookie", return_value="buvid3=X"), \
             mock.patch("backend.http_client.fetch_json", side_effect=[
                 {"data": {"cid": 123456}},
                 {
                     "data": {
                         "dash": {
                             "audio": [
                                 {"codecs": "mp4a.40.2", "bandwidth": 192000, "baseUrl": "https://upos.bilivideo.com/audio.m4s"},
                             ]
                         }
                     }
                 },
             ]), \
             mock.patch("backend.http_client.probe_url", return_value=(True, 206, {"content-type": "audio/mp4"}, "https://upos.bilivideo.com/audio.m4s")) as m_probe:
            resolved_bili = bilibili.resolve_bvid_audio("BV1GOOD")
            self.assertIsNotNone(resolved_bili)
            url, ctype, hdrs = resolved_bili
            self.assertEqual((url, ctype), ("https://upos.bilivideo.com/audio.m4s", "audio/mp4"))
            self.assertEqual(hdrs.get("Referer"), "https://www.bilibili.com/")
            self.assertEqual(m_probe.call_args.kwargs["headers"]["Referer"], "https://www.bilibili.com/")

        # 5. Tier 4.5 waterfall integration: proxied with Referer, NEVER exposed as directUrl/hlsUrl
        with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=True), \
             mock.patch("backend.providers.monochrome.find_best_stream", return_value=None), \
             mock.patch("backend.providers.soundcloud.find_best_stream", return_value=None), \
             mock.patch("backend.providers.jiosaavn.find_best_stream", return_value=None), \
             mock.patch("backend.providers.bilibili.find_best_stream", return_value=(
                 "https://upos.bilivideo.com/audio.m4s",
                 "audio/mp4",
                 {"User-Agent": "UA", "Referer": "https://www.bilibili.com/"},
             )):
            res = self.stream.resolve_stream("dz:707070", force_refresh=True)
            self.assertEqual((res.mode, res.source, res.content_type), ("full", "bilibili", "audio/mp4"))
            self.assertEqual(res.headers.get("Referer"), "https://www.bilibili.com/")

            info = self.stream.get_stream_info("dz:707070")
            self.assertEqual(info["streamSource"], "bilibili")
            self.assertNotIn("directUrl", info)
            self.assertNotIn("hlsUrl", info)


class TestGenreHubSpamFilterJioSaavnAndIOSYouTubeResolver(unittest.TestCase):
    def test_genre_hub_filters_karaoke_tribute_spam_and_interleaves(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(os.path.join(tmp, "genre.db"))
            cat = CatalogService(db)
            http_client.set_offline_mode(False)
            try:
                fake_dz = [
                    {"id": "dz:spam1", "source": "deezer", "title": "Ayo Technology (As Made Famous By 50 Cent)", "artist": "Karaoke Kings", "duration": 210},
                    {"id": "dz:good1", "source": "deezer", "title": "HUMBLE.", "artist": "Kendrick Lamar", "duration": 177},
                ]
                fake_dz2 = [
                    {"id": "dz:spam2", "source": "deezer", "title": "Hip Hop Tribute Ringtone", "artist": "Tribute Band", "duration": 190},
                    {"id": "dz:good2", "source": "deezer", "title": "God's Plan", "artist": "Drake", "duration": 198},
                ]
                with mock.patch("backend.providers.deezer.search_tracks", side_effect=[fake_dz, fake_dz2, []]), \
                     mock.patch("backend.providers.deezer.search_albums", return_value=[]), \
                     mock.patch("backend.providers.deezer.search_artists", return_value=[]), \
                     mock.patch("backend.providers.apple_itunes.search_songs", return_value=[]):
                    hub = cat.get_genre_hub("hiphop", region="global")
                    ids = [t["id"] for t in hub["tracks"]]
                    self.assertIn("dz:good1", ids)
                    self.assertIn("dz:good2", ids)
                    self.assertNotIn("dz:spam1", ids)
                    self.assertNotIn("dz:spam2", ids)
            finally:
                http_client.set_offline_mode(None)

    def test_jiosaavn_multi_artist_recall_and_ascii_fallback(self):
        # Multi-artist candidate where primary artist is a substring of combined artists
        item = {
            "id": "js:123",
            "title": "Die With A Smile",
            "artist": "Lady Gaga, Bruno Mars",
            "duration": 251,
            "_extra": {"direct_urls": ["https://aac.saavncdn.com/ok.mp4"]},
        }
        with mock.patch("backend.providers.jiosaavn.search_songs", side_effect=[[], [item]]) as m_search, \
             mock.patch("backend.providers.jiosaavn.probe_best_url", return_value=("https://aac.saavncdn.com/ok.mp4", "audio/mp4")):
            # Diacritic title triggers ASCII fallback query on second call
            found = jiosaavn.find_best_stream("Dié With A Smile", "Bruno Mars", duration=251)
            self.assertEqual(found, ("https://aac.saavncdn.com/ok.mp4", "audio/mp4"))
            self.assertEqual(m_search.call_count, 2)

    def test_ios_youtube_direct_resolver_wired_and_registered(self):
        yt_swift = (IOS_DIR / "Sonify" / "Services" / "YouTubeDirectResolver.swift").read_text(encoding="utf-8")
        for needle in (
            "struct DirectYouTubeStream",
            "WEB_REMIX",
            "ANDROID",
            "20.10.38",
            "itag == 18",
            "bytes=0-1023",
            "paddedCandAll.contains",
        ):
            self.assertIn(needle, yt_swift)

        yt_tests = (IOS_DIR / "SonifyTests" / "YouTubeDirectResolverTests.swift").read_text(encoding="utf-8")
        self.assertIn("testPickProgressiveItag18FormatAndRejectsLoginRequired", yt_tests)

        audio_swift = (IOS_DIR / "Sonify" / "Services" / "AudioPlayerService.swift").read_text(encoding="utf-8")
        self.assertIn("YouTubeDirectResolver.shared.resolveFullTrack(for: track)", audio_swift)
        self.assertIn("AVURLAssetHTTPHeaderFieldsKey", audio_swift)

        dl_swift = (IOS_DIR / "Sonify" / "Services" / "OfflineDownloadManager.swift").read_text(encoding="utf-8")
        self.assertIn("YouTubeDirectResolver.shared.resolveFullTrack(for: track)", dl_swift)

        pbx = (IOS_DIR / "Sonify.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")
        self.assertIn("YouTubeDirectResolver.swift in Sources", pbx)
        self.assertIn("YouTubeDirectResolverTests.swift in Sources", pbx)


if __name__ == "__main__":
    unittest.main()
