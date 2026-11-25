"""Automated tests for full Spotify Feature Parity across Backend, Web/PWA, and Native iOS."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from backend import http_client
from backend.db import Database
from backend.providers import monochrome
from backend.server import create_server
from backend.services.stream_service import StreamService

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
IOS_DIR = ROOT_DIR / "ios"


def _http_json(url: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


class TestSQLiteParityMigrations(unittest.TestCase):
    def test_idempotent_migration_on_legacy_v1_database_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "legacy_v1.db")
            # Create a pre-parity v1 schema (no followed_artists/saved_albums/pinned_items,
            # and playlists table lacks cover_color/cover_emoji/cover_url)
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE playlists (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                INSERT INTO playlists (id, name, description, created_at, updated_at)
                VALUES ('pl_legacy', 'Old Mix', 'Keep me', 1700000000, 1700000000);
                """
            )
            conn.commit()
            conn.close()

            # Initialize Database twice (idempotent check)
            db = Database(db_path)
            db.init_db()

            conn = db.connect()
            try:
                tables = {
                    r["name"]
                    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                for required in ("followed_artists", "saved_albums", "pinned_items"):
                    self.assertIn(required, tables)

                cols = {r["name"] for r in conn.execute("PRAGMA table_info(playlists)").fetchall()}
                for col in ("cover_color", "cover_emoji", "cover_url"):
                    self.assertIn(col, cols)
            finally:
                conn.close()

            pl = db.get_playlist("pl_legacy")
            self.assertIsNotNone(pl)
            self.assertEqual(pl["playlist"]["name"], "Old Mix")
            self.assertEqual(pl["playlist"]["coverColor"], "")
            self.assertEqual(pl["playlist"]["coverEmoji"], "")
            self.assertEqual(pl["playlist"]["coverUrl"], "")


class TestSpotifyParityHTTPAPI(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "parity.db")
        self.srv = create_server(
            host="127.0.0.1",
            port=0,
            db_path=self.db_path,
            offline=True,
            quiet=True,
        )
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_follow_artists_save_albums_pins_and_playlist_power_actions(self):
        # 1. Follow / List / Unfollow Artist
        st, res = _http_json(
            f"{self.base}/api/library/artists",
            method="POST",
            body={
                "artist": {
                    "id": "dz:4050205",
                    "name": "The Weeknd",
                    "pictureSmall": "https://cdn.example.com/tw_s.jpg",
                    "pictureLarge": "https://cdn.example.com/tw_l.jpg",
                    "fans": 25000000,
                }
            },
        )
        self.assertEqual(st, 201)
        self.assertEqual(res, {"followed": True, "artistId": "dz:4050205"})

        _, art_list = _http_json(f"{self.base}/api/library/artists")
        self.assertEqual(art_list["artistIds"], ["dz:4050205"])
        self.assertEqual(art_list["artists"][0]["pictureLarge"], "https://cdn.example.com/tw_l.jpg")

        # 2. Save / List / Unsave Album
        st, res = _http_json(
            f"{self.base}/api/library/albums",
            method="POST",
            body={
                "album": {
                    "id": "dz:alb99",
                    "title": "After Hours",
                    "artist": "The Weeknd",
                    "artistId": "dz:4050205",
                    "coverLarge": "https://cdn.example.com/ah.jpg",
                    "releaseDate": "2020-03-20",
                    "trackCount": 14,
                }
            },
        )
        self.assertEqual(st, 201)
        self.assertEqual(res, {"saved": True, "albumId": "dz:alb99"})

        _, alb_list = _http_json(f"{self.base}/api/library/albums")
        self.assertEqual(alb_list["albumIds"], ["dz:alb99"])
        self.assertEqual(alb_list["albums"][0]["coverLarge"], "https://cdn.example.com/ah.jpg")

        # 3. Pin / List / Unpin items + unfollow cascades artist pin removal
        st, pin_res = _http_json(
            f"{self.base}/api/library/pins",
            method="POST",
            body={"itemType": "artist", "itemId": "dz:4050205"},
        )
        self.assertEqual(st, 201)
        self.assertTrue(pin_res["pinned"])
        _http_json(
            f"{self.base}/api/library/pins",
            method="POST",
            body={"itemType": "liked", "itemId": "liked"},
        )

        _, pins = _http_json(f"{self.base}/api/library/pins")
        self.assertEqual(len(pins["pins"]), 2)

        # Unfollow artist should also remove its pin
        _http_json(f"{self.base}/api/library/artists/dz%3A4050205", method="DELETE")
        _, pins_after = _http_json(f"{self.base}/api/library/pins")
        self.assertEqual([(p["itemType"], p["itemId"]) for p in pins_after["pins"]], [("liked", "liked")])

        _http_json(f"{self.base}/api/library/pins/liked/liked", method="DELETE")
        _, pins_empty = _http_json(f"{self.base}/api/library/pins")
        self.assertEqual(pins_empty["pins"], [])

        _http_json(f"{self.base}/api/library/albums/dz%3Aalb99", method="DELETE")
        _, alb_empty = _http_json(f"{self.base}/api/library/albums")
        self.assertEqual(alb_empty["albumIds"], [])

        # 4. Playlist cover customization, duplicate, and JSON + M3U import
        _, created = _http_json(
            f"{self.base}/api/playlists",
            method="POST",
            body={"name": "Synthwave", "description": "Night drive"},
        )
        pid = created["playlist"]["id"]
        _, patched = _http_json(
            f"{self.base}/api/playlists/{pid}",
            method="PATCH",
            body={"coverColor": "#7C3AED", "coverEmoji": "🌙", "coverUrl": "https://cdn.example.com/cov.jpg"},
        )
        self.assertEqual(patched["playlist"]["coverColor"], "#7C3AED")
        self.assertEqual(patched["playlist"]["coverEmoji"], "🌙")
        self.assertEqual(patched["playlist"]["coverUrl"], "https://cdn.example.com/cov.jpg")

        # Add an offline track and duplicate
        _, home = _http_json(f"{self.base}/api/home?region=global")
        sample_tracks = [s["items"] for s in home["sections"] if s["type"] == "tracks"][0]
        _http_json(
            f"{self.base}/api/playlists/{pid}/tracks",
            method="POST",
            body={"track": sample_tracks[0]},
        )

        st, dup = _http_json(f"{self.base}/api/playlists/{pid}/duplicate", method="POST", body={})
        self.assertEqual(st, 201)
        self.assertEqual(dup["playlist"]["name"], "Synthwave (Copy)")
        self.assertEqual(dup["playlist"]["coverEmoji"], "🌙")
        self.assertEqual(len(dup["tracks"]), 1)
        self.assertEqual(dup["tracks"][0]["id"], sample_tracks[0]["id"])

        # Recommendations must exclude tracks already in playlist
        _, recs = _http_json(f"{self.base}/api/playlists/{pid}/recommendations?limit=8")
        self.assertEqual(recs["playlistId"], pid)
        self.assertGreater(len(recs["tracks"]), 0)
        self.assertNotIn(sample_tracks[0]["id"], [t["id"] for t in recs["tracks"]])

        # Import playlist from JSON
        st, imp_json = _http_json(
            f"{self.base}/api/playlists/import",
            method="POST",
            body={"name": "Imported JSON", "format": "json", "tracks": sample_tracks[:2]},
        )
        self.assertEqual(st, 201)
        self.assertEqual(imp_json["importedCount"], 2)
        self.assertEqual(imp_json["playlist"]["trackCount"], 2)

        # Import playlist from M3U #EXTINF
        t0 = sample_tracks[0]
        m3u_text = f"#EXTM3U\n#EXTINF:{t0['duration']},{t0['artist']} - {t0['title']}\n/stream/0\n"
        st, imp_m3u = _http_json(
            f"{self.base}/api/playlists/import",
            method="POST",
            body={"name": "Imported M3U", "format": "m3u", "m3uText": m3u_text},
        )
        self.assertEqual(st, 201)
        self.assertGreaterEqual(imp_m3u["importedCount"], 1)

    def test_radio_made_for_you_genre_hubs_track_detail_and_sound_capsule_stats(self):
        _, home = _http_json(f"{self.base}/api/home?region=global")
        tracks = [s["items"] for s in home["sections"] if s["type"] == "tracks"][0]
        seed = tracks[0]

        # 1. Song Radio (excludes seed track) & Artist Radio
        _, song_radio = _http_json(
            f"{self.base}/api/radio?trackId={urllib.parse.quote(seed['id'])}&limit=10"
        )
        self.assertEqual(song_radio["seedType"], "track")
        self.assertGreater(len(song_radio["tracks"]), 0)
        self.assertNotIn(seed["id"], [t["id"] for t in song_radio["tracks"]])

        _, artist_radio = _http_json(
            f"{self.base}/api/radio?artistId={urllib.parse.quote(seed['artistId'])}&limit=10"
        )
        self.assertEqual(artist_radio["seedType"], "artist")
        self.assertGreater(len(artist_radio["tracks"]), 0)

        # 2. Made For You (4 canonical mixes)
        _, mfy = _http_json(f"{self.base}/api/made-for-you?region=global")
        mix_ids = [m["id"] for m in mfy["mixes"]]
        self.assertEqual(mix_ids, ["daily-mix-1", "daily-mix-2", "discover-weekly", "release-radar"])
        for mix in mfy["mixes"]:
            self.assertEqual(len(mix["gradient"]), 2)
            self.assertGreater(len(mix["tracks"]), 0)

        # 3. Genre Hubs (all 12 canonical slugs 200; unknown slug 404)
        for slug in (
            "pop", "vpop", "hiphop", "kpop", "indie", "electronic",
            "chill", "workout", "focus", "rnb", "acoustic", "lofi",
        ):
            st, hub = _http_json(f"{self.base}/api/genre/{slug}?region=global")
            self.assertEqual(st, 200)
            self.assertEqual(hub["genre"]["slug"], slug)
            self.assertGreater(len(hub["tracks"]), 0)

        with self.assertRaises(urllib.error.HTTPError) as ctx_404:
            urllib.request.urlopen(f"{self.base}/api/genre/nonexistent-genre", timeout=5)
        self.assertEqual(ctx_404.exception.code, 404)
        ctx_404.exception.close()

        # 4. Single Track Deep Link
        _, detail = _http_json(f"{self.base}/api/track/{urllib.parse.quote(seed['id'])}")
        self.assertEqual(detail["track"]["id"], seed["id"])
        self.assertGreater(len(detail["relatedTracks"]), 0)

        # 5. Sound Capsule Stats (/api/stats)
        _http_json(f"{self.base}/api/history", method="POST", body={"track": tracks[0]})
        _http_json(f"{self.base}/api/history", method="POST", body={"track": tracks[1]})
        _http_json(f"{self.base}/api/library/likes", method="POST", body={"track": tracks[0]})

        _, stats = _http_json(f"{self.base}/api/stats")
        self.assertGreaterEqual(stats["totalPlays"], 2)
        self.assertGreater(stats["totalMinutes"], 0)
        self.assertEqual(stats["likedCount"], 1)
        self.assertEqual(len(stats["hourlyCounts"]), 24)
        self.assertGreaterEqual(stats["streakDays"], 1)
        self.assertGreater(len(stats["topTracks"]), 0)
        self.assertIn("track", stats["topTracks"][0])
        self.assertIn("playCount", stats["topTracks"][0])
        self.assertGreater(len(stats["topArtists"]), 0)

    def test_extended_settings_validation_and_clamping(self):
        _, patched = _http_json(
            f"{self.base}/api/settings",
            method="PATCH",
            body={
                "autoplay": False,
                "crossfade": 18,             # clamped to 12
                "playbackRate": 3.5,         # clamped to 2.0
                "audioQuality": "lossless",
                "eqPreset": "bass_boost",
                "eqBands": [20, 4, 0, -1, -25],  # clamped to [-12, 12]
            },
        )
        self.assertIs(patched["autoplay"], False)
        self.assertEqual(patched["crossfade"], 12)
        self.assertEqual(patched["playbackRate"], 2.0)
        self.assertEqual(patched["audioQuality"], "lossless")
        self.assertEqual(patched["eqPreset"], "bass_boost")
        self.assertEqual(patched["eqBands"], [12.0, 4.0, 0.0, -1.0, -12.0])

        # Invalid audioQuality and bad eqBands length must return 400
        for bad_body in (
            {"audioQuality": "ultra-999k"},
            {"eqBands": [1, 2, 3]},
        ):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                _http_json(f"{self.base}/api/settings", method="PATCH", body=bad_body)
            self.assertEqual(ctx.exception.code, 400)
            ctx.exception.close()


class TestAudioQualityRoutingAndFrontendIOSParity(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self._tmp.name, "qual.db"))
        self.stream = StreamService(self.db)
        http_client.set_offline_mode(False)
        self.db.upsert_track(
            {
                "id": "dz:900900",
                "source": "deezer",
                "title": "Quality Test Track",
                "artist": "Test Artist",
                "duration": 210,
            }
        )

    def tearDown(self):
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_quality_fast_vs_lossless_vs_auto_routing(self):
        def slow_mc(**kw):
            time.sleep(0.25)
            return (f"{monochrome.MONOCHROME_BASE}/track/900900", "audio/flac", "900900")

        def fast_sc(*args, **kwargs):
            time.sleep(0.05)
            return {
                "kind": "hls",
                "m3u8_url": "https://cf-hls-media.sndcdn.com/playlist/fast.m3u8",
                "content_type": "audio/mp4",
                "cache_key": "sc:fast900",
            }

        with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=True), \
             mock.patch("backend.providers.monochrome.find_best_stream", side_effect=slow_mc), \
             mock.patch("backend.providers.soundcloud.find_best_stream", side_effect=fast_sc), \
             mock.patch("backend.providers.monochrome.start_background_cache"), \
             mock.patch("backend.providers.soundcloud.start_background_hls_assembly"):
            # quality=fast takes SoundCloud immediately
            info_fast = self.stream.get_stream_info("dz:900900", quality="fast")
            self.assertEqual(info_fast["streamSource"], "soundcloud")
            self.assertEqual(info_fast.get("hlsUrl"), "https://cf-hls-media.sndcdn.com/playlist/fast.m3u8")

            # quality=lossless prefers Monochrome FLAC
            info_lossless = self.stream.get_stream_info("dz:900900", quality="lossless")
            self.assertEqual(info_lossless["streamSource"], "monochrome")
            self.assertEqual(info_lossless["contentType"], "audio/flac")

            # quality=auto stays isolated from fast/lossless cache keys and prefers Monochrome within grace
            self.stream.invalidate("dz:900900")
            info_auto = self.stream.get_stream_info("dz:900900", quality="auto")
            self.assertEqual(info_auto["streamSource"], "monochrome")

    def test_frontend_parity_modules_lazy_eq_cors_safety_and_fresh_blob_urls(self):
        expected_modules = (
            "audioEffects.js",
            "offlineDownloads.js",
            "colorExtractor.js",
            "components/nowPlayingDrawer.js",
            "components/audioSettingsModal.js",
            "components/fullScreenPlayer.js",
            "views/genreView.js",
            "views/statsView.js",
        )
        for rel in expected_modules:
            self.assertTrue((FRONTEND_DIR / "js" / rel).is_file(), f"Missing frontend/js/{rel}")

        eq_js = (FRONTEND_DIR / "js" / "audioEffects.js").read_text(encoding="utf-8")
        self.assertIn("createMediaElementSource", eq_js)
        self.assertIn("if (!isEqNonFlat(currentBands)) return;", eq_js)

        player_js = (FRONTEND_DIR / "js" / "player.js").read_text(encoding="utf-8")
        self.assertIn("const forceProxyForEq = isEqGraphAttached() || isEqNonFlat(getState().eqBands);", player_js)
        self.assertIn("triggerAutoplayRadio", player_js)
        self.assertIn("setSleepTimerMinutes", player_js)

        # Ensure offlineDownloads.js does not cache revoked blob URLs across replays
        dl_js = (FRONTEND_DIR / "js" / "offlineDownloads.js").read_text(encoding="utf-8")
        self.assertNotIn("activeBlobUrls", dl_js)
        self.assertIn("URL.createObjectURL(blob)", dl_js)

        router_js = (FRONTEND_DIR / "js" / "router.js").read_text(encoding="utf-8")
        for route in ("'/genre/'", "'/track/'", "'/mix/'", "'/stats'", "'/downloads'"):
            self.assertIn(route, router_js)

    def test_ios_swift_models_match_live_backend_contracts_and_balanced_delimiters(self):
        lib_models = (IOS_DIR / "Sonify" / "Models" / "LibraryModels.swift").read_text(encoding="utf-8")
        for key in ("case followed", "pictureLarge", "coverLarge", "eqBands"):
            self.assertIn(key, lib_models)

        cat_models = (IOS_DIR / "Sonify" / "Models" / "CatalogModels.swift").read_text(encoding="utf-8")
        for key in ("case id, title, subtitle, color, gradient", "relatedTracks", "struct TopTrackStat", "case artist, artistId, playCount, cover, coverSmall"):
            self.assertIn(key, cat_models)

        decoding_tests = (IOS_DIR / "SonifyTests" / "ModelDecodingTests.swift").read_text(encoding="utf-8")
        for needle in (
            "RadioResponse.self",
            "MadeForYouResponse.self",
            "GenreHubResponse.self",
            "TrackDetailResponse.self",
            "SoundCapsuleStats.self",
            "FollowedArtistsResponse.self",
            "FollowMutationResponse.self",
            "SavedAlbumsResponse.self",
        ):
            self.assertIn(needle, decoding_tests)

        # Balanced braces across all Swift source files
        for sf in sorted(IOS_DIR.rglob("*.swift")):
            src = sf.read_text(encoding="utf-8")
            self.assertEqual(src.count("{"), src.count("}"), f"Unbalanced braces in {sf.name}")


if __name__ == "__main__":
    unittest.main()
