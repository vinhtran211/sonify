"""Unit & proxy integration tests for ad-free streaming waterfall, PO-token probe, cover guards, and HTTP Range."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from backend import crypto_des, http_client
from backend.db import Database
from backend.providers import audius, jiosaavn, youtube_music
from backend.server import create_server
from backend.services.stream_service import (
    ResolvedStream,
    StreamService,
    StreamUnavailableError,
    parse_byte_range,
)


class _MockAudioCDNHandler(BaseHTTPRequestHandler):
    payload = b"ID3" + bytes(i % 256 for i in range(8192))
    content_type = "audio/mpeg"
    fail_first_get = False
    reject_head_405 = False
    omit_content_length = False
    reject_open_range_403 = False
    simulate_po_token_tail_403 = False
    return_html_error_200 = False
    request_log = []

    def log_message(self, format, *args):
        pass

    def do_HEAD(self):
        self.__class__.request_log.append(("HEAD", self.path, self.headers.get("Range")))
        if self.__class__.reject_head_405:
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._serve(head_only=True)

    def do_GET(self):
        rng = self.headers.get("Range")
        self.__class__.request_log.append(("GET", self.path, rng))

        if self.__class__.return_html_error_200:
            body = b"<html><body>Error page</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Simulate YouTube GVS PO-token trap: allows bytes=0-0, rejects open bytes=0- with 403
        if self.__class__.reject_open_range_403 and rng == "bytes=0-":
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # Simulate PO-token 1 MiB cutoff: bytes=0- reports total=2,500,000, tail offset > 1 MiB returns 403
        if self.__class__.simulate_po_token_tail_403:
            fake_total = 2_500_000
            if rng and rng.startswith("bytes="):
                spec = rng[6:]
                start_s = spec.split("-", 1)[0]
                start = int(start_s) if start_s.isdigit() else 0
                if start >= 1_000_000:
                    self.send_response(403)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                chunk = self.__class__.payload[:2048]
                self.send_response(206)
                self.send_header("Content-Type", "audio/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes 0-{len(chunk)-1}/{fake_total}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
                return

        if self.__class__.fail_first_get:
            self.__class__.fail_first_get = False
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._serve(head_only=False)

    def _serve(self, head_only: bool):
        data = self.__class__.payload
        total = len(data)
        rng = self.headers.get("Range")
        byte_range, ok = parse_byte_range(rng, total)
        if not ok:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{total}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if byte_range is not None:
            start, end = byte_range
            chunk = data[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Type", self.__class__.content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            if not self.__class__.omit_content_length:
                self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            if not head_only:
                self.wfile.write(chunk)
        else:
            self.send_response(200)
            self.send_header("Content-Type", self.__class__.content_type)
            self.send_header("Accept-Ranges", "bytes")
            if not self.__class__.omit_content_length:
                self.send_header("Content-Length", str(total))
            self.end_headers()
            if not head_only:
                self.wfile.write(data)


class TestByteRangeParser(unittest.TestCase):
    def test_valid_and_invalid_ranges(self):
        self.assertEqual(parse_byte_range(None, 1000), (None, True))
        self.assertEqual(parse_byte_range("bytes=0-1023", 5000), ((0, 1023), True))
        self.assertEqual(parse_byte_range("bytes=100-", 500), ((100, 499), True))
        self.assertEqual(parse_byte_range("bytes=-100", 500), ((400, 499), True))
        self.assertEqual(parse_byte_range("bytes=-9999", 500), ((0, 499), True))
        self.assertEqual(parse_byte_range("bytes=0-9999", 500), ((0, 499), True))

        for bad in ("bytes=500-600", "bytes=300-200", "bytes=-0", "bytes=-", "items=0-10", "bytes=abc-def"):
            self.assertEqual(parse_byte_range(bad, 500), (None, False), f"Expected invalid for {bad}")
        self.assertEqual(parse_byte_range("bytes=0-10", 0), (None, False))


class TestProbeUrlAndStrictMatchers(unittest.TestCase):
    def setUp(self):
        http_client.set_offline_mode(False)
        _MockAudioCDNHandler.content_type = "audio/mpeg"
        _MockAudioCDNHandler.fail_first_get = False
        _MockAudioCDNHandler.reject_head_405 = False
        _MockAudioCDNHandler.omit_content_length = False
        _MockAudioCDNHandler.reject_open_range_403 = False
        _MockAudioCDNHandler.simulate_po_token_tail_403 = False
        _MockAudioCDNHandler.return_html_error_200 = False
        _MockAudioCDNHandler.request_log = []
        self.cdn = ThreadingHTTPServer(("127.0.0.1", 0), _MockAudioCDNHandler)
        self.cdn_port = self.cdn.server_address[1]
        self.cdn_thread = threading.Thread(target=self.cdn.serve_forever, daemon=True)
        self.cdn_thread.start()
        self.url = f"http://127.0.0.1:{self.cdn_port}/track.mp4"

    def tearDown(self):
        self.cdn.shutdown()
        self.cdn.server_close()
        http_client.set_offline_mode(None)

    def test_probe_url_uses_open_range_rejects_po_token_and_html(self):
        # 1. Valid media stream succeeds with Range: bytes=0-
        _MockAudioCDNHandler.content_type = "video/mp4"
        ok, st, _, _ = http_client.probe_url(self.url)
        self.assertTrue(ok)
        self.assertEqual(st, 206)
        self.assertEqual(_MockAudioCDNHandler.request_log[-1][2], "bytes=0-")

        # 2. Rejects URLs that fail open-ended Range: bytes=0- with 403
        _MockAudioCDNHandler.reject_open_range_403 = True
        ok, st, _, _ = http_client.probe_url(self.url)
        self.assertFalse(ok)
        self.assertEqual(st, 403)
        _MockAudioCDNHandler.reject_open_range_403 = False

        # 3. verify_tail=True rejects 1 MiB PO-token cutoff trap
        _MockAudioCDNHandler.simulate_po_token_tail_403 = True
        ok_no_tail, _, _, _ = http_client.probe_url(self.url, verify_tail=False)
        self.assertTrue(ok_no_tail)
        ok_with_tail, st_tail, _, _ = http_client.probe_url(self.url, verify_tail=True)
        self.assertFalse(ok_with_tail)
        self.assertEqual(st_tail, 403)
        _MockAudioCDNHandler.simulate_po_token_tail_403 = False

        # 4. Rejects text/html 200 error pages
        _MockAudioCDNHandler.return_html_error_200 = True
        ok_html, _, _, _ = http_client.probe_url(self.url)
        self.assertFalse(ok_html)

    def test_youtube_innertube_android_itag18_extraction_and_strict_matching(self):
        captured = {}

        def fake_fetch(url, method="GET", headers=None, json_body=None, **kwargs):
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["body"] = json_body or {}
            return {
                "playabilityStatus": {"status": "OK"},
                "streamingData": {
                    "adaptiveFormats": [
                        {
                            "itag": 140,
                            "mimeType": 'audio/mp4; codecs="mp4a.40.2"',
                            "bitrate": 128000,
                            "url": "https://googlevideo.test/adaptive_po_gated",
                        }
                    ],
                    "formats": [
                        {
                            "itag": 18,
                            "mimeType": 'video/mp4; codecs="avc1.42001E, mp4a.40.2"',
                            "bitrate": 500000,
                            "url": "https://googlevideo.test/progressive_itag18",
                        }
                    ],
                },
            }

        def fake_probe(url, headers=None, verify_tail=False, **kwargs):
            # Adaptive fails PO-token tail check; progressive itag=18 succeeds!
            if "adaptive_po_gated" in url:
                return False, 403, {}, url
            return True, 206, {"content-type": "video/mp4"}, url

        with mock.patch("backend.http_client.fetch_json", side_effect=fake_fetch), \
             mock.patch("backend.http_client.probe_url", side_effect=fake_probe):
            res = youtube_music._resolve_via_innertube_player("7u4g483WTzw")
            self.assertIsNotNone(res)
            final_url, mime, stream_hdrs = res
            self.assertEqual(final_url, "https://googlevideo.test/progressive_itag18")
            self.assertEqual(mime, "video/mp4")
            self.assertEqual(captured["body"]["context"]["client"]["clientName"], "ANDROID")
            self.assertEqual(captured["body"]["context"]["client"]["clientVersion"], "20.10.38")
            self.assertNotIn("thirdParty", captured["body"]["context"])
            self.assertEqual(captured["headers"].get("X-YouTube-Client-Name"), "3")
            self.assertIn("Android 15", stream_hdrs.get("User-Agent", ""))

        # Strict find_best_video_id matching: blocks covers/piano/wrong titles/wrong durations
        fake_cands = [
            {
                "id": "yt:wrong_song",
                "title": "Totally Different Song",
                "artist": "Sơn Tùng M-TP - Topic",
                "duration": 279,
                "_extra": {"videoId": "wrong_song"},
            },
            {
                "id": "yt:piano_cover",
                "title": "Đừng Làm Trái Tim Anh Đau (Piano Cover)",
                "artist": "Piano Man",
                "duration": 278,
                "_extra": {"videoId": "piano_cover"},
            },
            {
                "id": "yt:short_teaser",
                "title": "Đừng Làm Trái Tim Anh Đau",
                "artist": "Sơn Tùng M-TP",
                "duration": 35,
                "_extra": {"videoId": "short_teaser"},
            },
            {
                "id": "yt:7u4g483WTzw",
                "title": "Đừng Làm Trái Tim Anh Đau",
                "artist": "Sơn Tùng M-TP - Topic",
                "duration": 279,
                "_extra": {"videoId": "7u4g483WTzw"},
            },
        ]
        with mock.patch("backend.providers.youtube_music.search_songs", return_value=fake_cands):
            vid = youtube_music.find_best_video_id("Đừng Làm Trái Tim Anh Đau", "Son Tung M-TP", 279)
            self.assertEqual(vid, "7u4g483WTzw")

        # When only covers / unrelated songs exist, must return None
        with mock.patch("backend.providers.youtube_music.search_songs", return_value=fake_cands[:3]):
            vid_none = youtube_music.find_best_video_id("Đừng Làm Trái Tim Anh Đau", "Son Tung M-TP", 279)
            self.assertIsNone(vid_none)

    def test_jiosaavn_and_audius_reject_cover_hijacks(self):
        cover_candidates = [
            {
                "id": "js:cov1",
                "title": "Die With A Smile",
                "artist": "Nyae",
                "duration": 251,
                "_extra": {"direct_urls": ["https://saavn/fake.mp4"]},
            },
            {
                "id": "js:cov2",
                "title": "Die With A Smile",
                "artist": "Sound Tower",
                "duration": 252,
                "_extra": {"direct_urls": ["https://saavn/fake2.mp4"]},
            },
        ]
        with mock.patch("backend.providers.jiosaavn.search_songs", return_value=cover_candidates):
            self.assertIsNone(jiosaavn.find_best_stream("Die With A Smile", "Lady Gaga & Bruno Mars", 251))

        au_covers = [
            {
                "id": "au:cov1",
                "title": "Blinding Lights",
                "artist": "Random Nightcore Cover",
                "duration": 200,
            }
        ]
        with mock.patch("backend.providers.audius.search_tracks", return_value=au_covers):
            self.assertIsNone(audius.find_best_stream("Blinding Lights", "The Weeknd", 200))


class TestStreamWaterfall(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self._tmp.name, "stream.db"))
        self.stream = StreamService(self.db)
        http_client.set_offline_mode(False)

    def tearDown(self):
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_tier0_native_audius_and_jiosaavn(self):
        self.db.upsert_track({"id": "au:abc123", "source": "audius", "title": "Pulse", "artist": "Synth"})
        with mock.patch("backend.providers.audius.get_stream_url", return_value="https://node.audius/stream"), \
             mock.patch("backend.http_client.probe_url", return_value=(True, 206, {"content-type": "audio/mpeg"}, "https://cdn.audius/abc.mp3")):
            res = self.stream.resolve_stream("au:abc123")
            self.assertEqual(res.mode, "full")
            self.assertEqual(res.source, "audius")
            self.assertEqual(res.content_type, "audio/mpeg")

        enc = crypto_des.encrypt_media_url("https://aac.saavncdn.com/x/track_96.mp4")
        self.db.upsert_track({
            "id": "js:saavn1",
            "source": "jiosaavn",
            "title": "Song",
            "artist": "Singer",
            "_extra": {"encrypted_media_url": enc, "320kbps": True},
        })
        with mock.patch("backend.providers.jiosaavn.probe_best_url", return_value=("https://aac.saavncdn.com/x/track_320.mp4", "audio/mp4")) as m_probe:
            res = self.stream.resolve_stream("js:saavn1")
            self.assertEqual(res.mode, "full")
            self.assertEqual(res.source, "jiosaavn")
            self.assertEqual(res.url, "https://aac.saavncdn.com/x/track_320.mp4")
            called_urls = m_probe.call_args[0][0]
            self.assertEqual(called_urls[0], "https://aac.saavncdn.com/x/track_320.mp4")

    def test_online_waterfall_order_and_zero_silent_offline_synth(self):
        self.db.upsert_track({
            "id": "dz:908604612",
            "source": "deezer",
            "title": "Blinding Lights",
            "artist": "The Weeknd",
            "duration": 200,
            "previewUrl": "https://preview.dz/old.mp3",
        })

        # Tier 1: YouTube Music (ANDROID itag=18 video/mp4) is primary universal full-track
        with mock.patch("backend.providers.youtube_music.find_best_video_id", return_value="J7p4bzqLvCw"), \
             mock.patch("backend.providers.youtube_music.resolve_audio_stream", return_value=("https://yt/itag18.mp4", "video/mp4", {"User-Agent": "Android"}, "youtube-innertube")):
            r1 = self.stream.resolve_stream("dz:908604612", force_refresh=True)
            self.assertEqual((r1.mode, r1.source, r1.content_type), ("full", "youtube-innertube", "video/mp4"))
            cached_t = self.db.get_track("dz:908604612", include_extra=True)
            self.assertEqual(cached_t["_extra"].get("videoId"), "J7p4bzqLvCw")
            self.assertEqual(cached_t["_extra"].get("videoIdMatchVersion"), 2)

        # Tier 2: YouTube fails -> JioSaavn 320kbps cross-match
        with mock.patch("backend.providers.youtube_music.resolve_audio_stream", return_value=None), \
             mock.patch("backend.providers.jiosaavn.find_best_stream", return_value=("https://saavn/bl_320.mp4", "audio/mp4")):
            r2 = self.stream.resolve_stream("dz:908604612", force_refresh=True)
            self.assertEqual((r2.mode, r2.source), ("full", "jiosaavn"))

        # Tier 3: YouTube & JioSaavn fail -> Audius cross-match
        with mock.patch("backend.providers.youtube_music.resolve_audio_stream", return_value=None), \
             mock.patch("backend.providers.jiosaavn.find_best_stream", return_value=None), \
             mock.patch("backend.providers.audius.find_best_stream", return_value=("https://audius/full.mp3", "audio/mpeg")):
            r3 = self.stream.resolve_stream("dz:908604612", force_refresh=True)
            self.assertEqual((r3.mode, r3.source), ("full", "audius"))

        # Tier 4: All full sources fail -> Official Deezer 30s preview (with expired-403 refresh)
        with mock.patch("backend.providers.youtube_music.resolve_audio_stream", return_value=None), \
             mock.patch("backend.providers.jiosaavn.find_best_stream", return_value=None), \
             mock.patch("backend.providers.audius.find_best_stream", return_value=None), \
             mock.patch("backend.providers.deezer.get_track", return_value={"id": "dz:908604612", "source": "deezer", "title": "Blinding Lights", "artist": "The Weeknd", "previewUrl": "https://preview.dz/fresh.mp3"}), \
             mock.patch("backend.http_client.probe_url", side_effect=[
                 (False, 403, {}, "https://preview.dz/old.mp3"),
                 (True, 206, {"content-type": "audio/mpeg"}, "https://preview.dz/fresh.mp3"),
             ]):
            r4 = self.stream.resolve_stream("dz:908604612", force_refresh=True)
            self.assertEqual((r4.mode, r4.source), ("preview", "deezer-preview"))
            self.assertEqual(r4.url, "https://preview.dz/fresh.mp3")

        # Online mode MUST NEVER fall back to fake offline-synth WAV when all upstream sources fail!
        with mock.patch("backend.providers.youtube_music.resolve_audio_stream", return_value=None), \
             mock.patch("backend.providers.jiosaavn.find_best_stream", return_value=None), \
             mock.patch("backend.providers.audius.find_best_stream", return_value=None), \
             mock.patch("backend.providers.deezer.get_track", return_value=None), \
             mock.patch("backend.http_client.probe_url", return_value=(False, 0, {}, "")):
            with self.assertRaises(StreamUnavailableError) as ctx:
                self.stream.resolve_stream("dz:908604612", force_refresh=True)
            self.assertEqual(ctx.exception.status, 502)

        # Explicit local:* track or explicit offline mode STILL serves deterministic offline-synth WAV
        r_local = self.stream.resolve_stream("local:neon-horizon")
        self.assertEqual((r_local.mode, r_local.source), ("offline-synth", "offline-synth"))
        self.assertTrue(r_local.local_bytes.startswith(b"RIFF"))

        http_client.set_offline_mode(True)
        r_off = self.stream.resolve_stream("dz:908604612", force_refresh=True)
        self.assertEqual((r_off.mode, r_off.source), ("offline-synth", "offline-synth"))

    def test_tier4_itunes_missing_preview_refreshes_from_lookup(self):
        self.db.upsert_track({
            "id": "it:777888",
            "source": "itunes",
            "title": "VN Hit",
            "artist": "VN Artist",
            "duration": 220,
            "previewUrl": "",
        })
        with mock.patch("backend.providers.youtube_music.find_best_video_id", return_value=None), \
             mock.patch("backend.providers.jiosaavn.find_best_stream", return_value=None), \
             mock.patch("backend.providers.audius.find_best_stream", return_value=None), \
             mock.patch("backend.providers.apple_itunes.get_track", return_value={
                 "id": "it:777888",
                 "source": "itunes",
                 "title": "VN Hit",
                 "artist": "VN Artist",
                 "duration": 220,
                 "previewUrl": "https://audio-ssl.itunes.apple.com/preview.m4a",
             }), \
             mock.patch("backend.http_client.probe_url", return_value=(True, 206, {"content-type": "audio/mp4"}, "https://audio-ssl.itunes.apple.com/preview.m4a")):
            res = self.stream.resolve_stream("it:777888", force_refresh=True)
            self.assertEqual((res.mode, res.source), ("preview", "itunes-preview"))
            self.assertEqual(res.content_type, "audio/mp4")


class TestStreamProxyLiveHTTP(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "proxy.db")

        _MockAudioCDNHandler.content_type = "audio/mpeg"
        _MockAudioCDNHandler.fail_first_get = False
        _MockAudioCDNHandler.reject_head_405 = False
        _MockAudioCDNHandler.omit_content_length = False
        _MockAudioCDNHandler.reject_open_range_403 = False
        _MockAudioCDNHandler.simulate_po_token_tail_403 = False
        _MockAudioCDNHandler.return_html_error_200 = False
        _MockAudioCDNHandler.request_log = []
        self.cdn = ThreadingHTTPServer(("127.0.0.1", 0), _MockAudioCDNHandler)
        self.cdn_port = self.cdn.server_address[1]
        self.cdn_thread = threading.Thread(target=self.cdn.serve_forever, daemon=True)
        self.cdn_thread.start()

        self.srv = create_server(host="127.0.0.1", port=0, db_path=self.db_path, offline=False, quiet=True)
        self.port = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.srv_thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.srv_thread.start()

        self.cdn_url = f"http://127.0.0.1:{self.cdn_port}/audio.mp3"
        self.srv.stream._store_cache(
            ResolvedStream(
                track_id="dz:mockproxy",
                mode="full",
                source="jiosaavn",
                content_type="audio/mpeg",
                url=self.cdn_url,
                headers={"User-Agent": "SonifyTest"},
            )
        )

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.cdn.shutdown()
        self.cdn.server_close()
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_remote_proxy_200_206_416_head_and_video_mp4_passthrough(self):
        # 200 OK full stream
        with urllib.request.urlopen(f"{self.base}/api/stream/dz:mockproxy", timeout=5) as r:
            body = r.read()
            self.assertEqual(r.status, 200)
            self.assertEqual(body, _MockAudioCDNHandler.payload)
            self.assertEqual(r.headers.get("X-Ad-Free"), "true")
            self.assertEqual(r.headers.get("X-Stream-Mode"), "full")
            self.assertEqual(r.headers.get("X-Stream-Source"), "jiosaavn")
            self.assertEqual(r.headers.get("Accept-Ranges"), "bytes")
            self.assertIn("X-Ad-Free", r.headers.get("Access-Control-Expose-Headers", ""))

        # 206 Partial Content open-ended Range: bytes=0- & video/mp4 Content-Type pass-through
        _MockAudioCDNHandler.content_type = "video/mp4"
        req = urllib.request.Request(
            f"{self.base}/api/stream/dz:mockproxy", headers={"Range": "bytes=0-1023"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            chunk = r.read()
            self.assertEqual(r.status, 206)
            self.assertEqual(len(chunk), 1024)
            self.assertEqual(chunk, _MockAudioCDNHandler.payload[:1024])
            self.assertEqual(r.headers.get("Content-Type"), "video/mp4")
            self.assertEqual(r.headers.get("Content-Range"), f"bytes 0-1023/{len(_MockAudioCDNHandler.payload)}")
            self.assertEqual(r.headers.get("X-Ad-Free"), "true")

        # 416 Range Not Satisfiable
        bad_req = urllib.request.Request(
            f"{self.base}/api/stream/dz:mockproxy", headers={"Range": "bytes=999999-"}
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(bad_req, timeout=5)
        self.assertEqual(ctx.exception.code, 416)
        self.assertEqual(ctx.exception.headers.get("X-Ad-Free"), "true")
        self.assertEqual(ctx.exception.headers.get("Accept-Ranges"), "bytes")
        ctx.exception.close()

        # HEAD request returns 0 body bytes
        head_req = urllib.request.Request(f"{self.base}/api/stream/dz:mockproxy", method="HEAD")
        with urllib.request.urlopen(head_req, timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.read(), b"")
            self.assertEqual(r.headers.get("X-Stream-Mode"), "full")

    def test_remote_proxy_head_405_fallback_and_no_content_length_does_not_hang(self):
        _MockAudioCDNHandler.reject_head_405 = True
        head_req = urllib.request.Request(f"{self.base}/api/stream/dz:mockproxy", method="HEAD")
        with urllib.request.urlopen(head_req, timeout=5) as r:
            self.assertIn(r.status, (200, 206))
            self.assertEqual(r.headers.get("X-Stream-Mode"), "full")
            self.assertEqual(r.read(), b"")

        _MockAudioCDNHandler.reject_head_405 = False
        _MockAudioCDNHandler.omit_content_length = True
        with urllib.request.urlopen(f"{self.base}/api/stream/dz:mockproxy", timeout=4) as r:
            data = r.read()
            self.assertEqual(r.status, 200)
            self.assertEqual(data, _MockAudioCDNHandler.payload)
            self.assertEqual(r.headers.get("Connection", "").lower(), "close")

    def test_remote_proxy_403_excludes_failed_source_and_cascades_or_returns_502(self):
        # 1. Upstream 403 on freshly cached source cascades with exclude_sources={"jiosaavn"} to next real source
        initial = self.srv.stream._cache["dz:mockproxy"]
        _MockAudioCDNHandler.fail_first_get = True
        fresh = ResolvedStream(
            track_id="dz:mockproxy",
            mode="preview",
            source="deezer-preview",
            content_type="audio/mpeg",
            url=self.cdn_url,
            headers={"User-Agent": "SonifyFresh"},
        )
        with mock.patch.object(
            self.srv.stream, "resolve_stream", side_effect=[initial, fresh]
        ) as m_res:
            with urllib.request.urlopen(f"{self.base}/api/stream/dz:mockproxy", timeout=5) as r:
                self.assertEqual(r.status, 200)
                self.assertEqual(r.read(), _MockAudioCDNHandler.payload)
                self.assertEqual(r.headers.get("X-Stream-Mode"), "preview")
                self.assertEqual(r.headers.get("X-Stream-Source"), "deezer-preview")
            self.assertEqual(m_res.call_count, 2)
            # Second call passed exclude_sources containing failed 'jiosaavn'
            _, kwargs = m_res.call_args
            self.assertIn("jiosaavn", kwargs.get("exclude_sources", set()))

        # 2. If cascade fails with StreamUnavailableError -> HTTP 502 JSON (NEVER fake offline-synth WAV!)
        _MockAudioCDNHandler.fail_first_get = True
        with mock.patch.object(
            self.srv.stream,
            "resolve_stream",
            side_effect=[initial, StreamUnavailableError("No real stream")],
        ):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(f"{self.base}/api/stream/dz:mockproxy", timeout=5)
            self.assertEqual(ctx.exception.code, 502)
            err_body = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertEqual(err_body["error"]["code"], "STREAM_UNAVAILABLE")
            self.assertIn("application/json", ctx.exception.headers.get("Content-Type", ""))
            ctx.exception.close()


if __name__ == "__main__":
    unittest.main()
