"""Tests for 5-stage Lyrics waterfall + caching and CatalogService online/offline behavior."""

from __future__ import annotations

import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from backend import config, http_client
from backend.db import Database
from backend.providers import lyrics as lyrics_provider
from backend.services.catalog_service import CatalogService


class TestLyricsWaterfall(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self._tmp.name, "lyrics.db"))
        http_client.set_offline_mode(False)

    def tearDown(self):
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_stage1_lrclib_exact_user_agent_and_sqlite_cache(self):
        captured_headers = {}

        def fake_fetch(url, headers=None, **kwargs):
            captured_headers.update(headers or {})
            return {
                "trackName": "Blinding Lights",
                "artistName": "The Weeknd",
                "instrumental": False,
                "syncedLyrics": "[00:27.12]I've been tryna call\n[00:29.85]I've been on my own",
                "plainLyrics": "I've been tryna call\nI've been on my own",
            }

        with mock.patch("backend.http_client.fetch_json", side_effect=fake_fetch) as m_fetch:
            res = lyrics_provider.get_lyrics(
                self.db,
                track_id="dz:908604612",
                title="Blinding Lights (Official Video)",
                artist="The Weeknd",
                duration=200,
            )
            self.assertEqual(res["status"], "synced")
            self.assertEqual(res["source"], "lrclib")
            self.assertTrue(res["synced"])
            self.assertEqual(len(res["lines"]), 2)
            self.assertEqual(captured_headers.get("User-Agent"), config.LRCLIB_USER_AGENT)
            self.assertEqual(m_fetch.call_count, 1)

            # Second call hits SQLite 7-day cache without calling HTTP
            res2 = lyrics_provider.get_lyrics(
                self.db,
                track_id="dz:908604612",
                title="Blinding Lights",
                artist="The Weeknd",
            )
            self.assertEqual(m_fetch.call_count, 1)
            self.assertEqual(res2["lines"], res["lines"])

            # refresh=True bypasses cache
            lyrics_provider.get_lyrics(
                self.db,
                track_id="dz:908604612",
                title="Blinding Lights",
                artist="The Weeknd",
                refresh=True,
            )
            self.assertEqual(m_fetch.call_count, 2)

    def test_stage2_lrclib_search_ranks_synced_and_supports_korean(self):
        def fake_fetch(url, **kwargs):
            if "/api/get?" in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if "/api/search?" in url:
                return [
                    {
                        "trackName": "밤편지",
                        "artistName": "아이유",
                        "duration": 253,
                        "syncedLyrics": "",
                        "plainLyrics": "이 밤 그날의 반딧불을",
                        "instrumental": False,
                    },
                    {
                        "trackName": "밤편지",
                        "artistName": "아이유",
                        "duration": 253,
                        "syncedLyrics": "[00:12.00]이 밤 그날의 반딧불을",
                        "plainLyrics": "이 밤 그날의 반딧불을",
                        "instrumental": False,
                    },
                ]
            return {}

        with mock.patch("backend.http_client.fetch_json", side_effect=fake_fetch):
            res = lyrics_provider.get_lyrics(
                self.db,
                title="밤편지",
                artist="아이유",
                duration=253,
            )
            self.assertEqual(res["status"], "synced")
            self.assertEqual(res["lines"][0]["text"], "이 밤 그날의 반딧불을")

    def test_stage3_ytm_and_stage4_lyrics_ovh_fallbacks(self):
        with mock.patch("backend.providers.lyrics._query_lrclib_get", return_value=None), \
             mock.patch("backend.providers.lyrics._query_lrclib_search", return_value=None), \
             mock.patch("backend.providers.youtube_music.fetch_plain_lyrics", return_value="YTM plain line 1\nLine 2"):
            res = lyrics_provider.get_lyrics(
                self.db, track_id="yt:vid999", title="Rare Live Track", artist="Indie Band"
            )
            self.assertEqual(res["status"], "plain")
            self.assertEqual(res["source"], "youtube_music")
            self.assertFalse(res["synced"])
            self.assertIn("YTM plain line 1", res["plainLyrics"])

        with mock.patch("backend.providers.lyrics._query_lrclib_get", return_value=None), \
             mock.patch("backend.providers.lyrics._query_lrclib_search", return_value=None), \
             mock.patch("backend.providers.lyrics._query_lyrics_ovh", return_value="OVH plain lyrics"):
            res2 = lyrics_provider.get_lyrics(
                self.db, title="Another Track", artist="Some Artist"
            )
            self.assertEqual(res2["status"], "plain")
            self.assertEqual(res2["source"], "lyrics.ovh")
            self.assertEqual(res2["plainLyrics"], "OVH plain lyrics")

    def test_stage5_local_synced_and_instrumental(self):
        synced = lyrics_provider.get_lyrics(self.db, track_id="local:dem-saigon")
        self.assertEqual(synced["status"], "synced")
        self.assertEqual(synced["source"], "local")
        self.assertGreater(len(synced["lines"]), 5)

        inst = lyrics_provider.get_lyrics(self.db, track_id="local:analog-waves")
        self.assertEqual(inst["status"], "instrumental")
        self.assertTrue(inst["instrumental"])
        self.assertFalse(inst["synced"])


class TestCatalogService(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self._tmp.name, "cat.db"))
        self.catalog = CatalogService(self.db)
        http_client.set_offline_mode(False)

    def tearDown(self):
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_network_unreachable_triggers_offline_fallback_on_home_and_search(self):
        net_err = urllib.error.URLError("DNS unreachable")
        with mock.patch("backend.http_client.fetch_json", side_effect=net_err):
            home = self.catalog.get_home("global")
            self.assertEqual(home["mode"], "offline")
            self.assertGreaterEqual(len(home["sections"]), 5)

            # Both type=all AND type=tracks/artists/albums must fall back to offline mode when network fails!
            for stype in ("all", "tracks", "artists", "albums"):
                srch = self.catalog.search("Neon", search_type=stype)
                self.assertEqual(srch["mode"], "offline", f"Expected offline fallback for type={stype}")
            srch_all = self.catalog.search("Dem Sai Gon", search_type="all")
            self.assertEqual(srch_all["mode"], "offline")
            self.assertGreaterEqual(len(srch_all["tracks"]), 1)
            self.assertEqual(srch_all["tracks"][0]["id"], "local:dem-saigon")

    def test_online_search_ranking_prefers_official_artist_over_unofficial_reupload(self):
        official_track = {
            "id": "dz:500",
            "source": "deezer",
            "title": "Lạc Trôi",
            "artist": "Sơn Tùng M-TP",
            "artistId": "dz:50",
            "album": "m-tp M-TP",
            "albumId": "dz:51",
            "duration": 233,
            "coverSmall": "https://dz/s.jpg",
            "coverLarge": "https://dz/l.jpg",
        }
        unofficial_reupload = {
            "id": "au:999",
            "source": "audius",
            "title": "Lac Troi - Son Tung M-TP",
            "artist": "Random Uploader",
            "artistId": "au:1",
            "album": "Audius Single",
            "albumId": "",
            "duration": 233,
            "coverSmall": "https://au/s.jpg",
            "coverLarge": "https://au/l.jpg",
        }
        ranked = self.catalog._dedup_and_rank_tracks(
            [unofficial_reupload, official_track],
            query="Sơn Tùng M-TP",
            limit=10,
        )
        self.assertEqual(ranked[0]["id"], "dz:500")

        # Karaoke knockoffs must be demoted below original tracks
        karaoke = {
            "id": "it:k1",
            "source": "itunes",
            "title": "Through The Night (밤편지) (By IU(아이유)) (Melody Karaoke Version)",
            "artist": "ZZang KARAOKE",
            "duration": 250,
            "coverLarge": "https://img/k.jpg",
        }
        original = {
            "id": "it:orig1",
            "source": "itunes",
            "title": "Through the Night (밤편지)",
            "artist": "IU (아이유)",
            "duration": 253,
            "coverLarge": "https://img/o.jpg",
        }
        ranked_kr = self.catalog._dedup_and_rank_tracks(
            [karaoke, original],
            query="아이유 밤편지",
            limit=10,
        )
        self.assertEqual(ranked_kr[0]["id"], "it:orig1")

        # "Song + Artist" queries must rank official track above bootlegs/covers that stuff artist into title
        bl_bootleg = {
            "id": "dz:2282985187",
            "source": "deezer",
            "title": "Blinding Lights The Weeknd",
            "artist": "Pancadão GD Som",
            "duration": 185,
            "coverLarge": "https://img/b.jpg",
        }
        bl_official = {
            "id": "dz:908604612",
            "source": "deezer",
            "title": "Blinding Lights",
            "artist": "The Weeknd",
            "duration": 200,
            "coverLarge": "https://img/o.jpg",
        }
        ranked_bl = self.catalog._dedup_and_rank_tracks(
            [bl_bootleg, bl_official],
            query="Blinding Lights The Weeknd",
            limit=10,
        )
        self.assertEqual(ranked_bl[0]["id"], "dz:908604612")

        dwas_cover = {
            "id": "dz:3476486241",
            "source": "deezer",
            "title": "Die With A Smile (Lady Gaga & Bruno Mars Cover)",
            "artist": "Houston",
            "duration": 263,
            "coverLarge": "https://img/c.jpg",
        }
        dwas_official = {
            "id": "dz:2945791331",
            "source": "deezer",
            "title": "Die With A Smile",
            "artist": "Lady Gaga, Bruno Mars",
            "duration": 251,
            "coverLarge": "https://img/d.jpg",
        }
        ranked_dwas = self.catalog._dedup_and_rank_tracks(
            [dwas_cover, dwas_official],
            query="Die With A Smile Lady Gaga Bruno Mars",
            limit=10,
        )
        self.assertEqual(ranked_dwas[0]["id"], "dz:2945791331")

    def test_home_supplements_sparse_deezer_new_releases_with_apple_albums(self):
        dz_chart = {
            "tracks": [{"id": "dz:1", "source": "deezer", "title": "Hit 1", "artist": "Art 1", "duration": 200}],
            "albums": [{"id": "dz:a1", "source": "deezer", "title": "Alb 1", "artist": "Art 1"}],
            "artists": [{"id": "dz:ar1", "source": "deezer", "name": "Art 1"}],
        }
        apple_albums = [
            {"id": f"it:alb{i}", "source": "itunes", "title": f"Apple Alb {i}", "artist": f"Artist {i}"}
            for i in range(1, 13)
        ]
        with mock.patch("backend.providers.deezer.get_chart", return_value=dz_chart), \
             mock.patch("backend.providers.deezer.get_new_releases", return_value=[]), \
             mock.patch("backend.providers.apple_itunes.get_top_songs", return_value=[{"id": "it:1", "source": "itunes", "title": "VPop Hit", "artist": "VN Artist", "duration": 210}]), \
             mock.patch("backend.providers.apple_itunes.get_top_albums", return_value=apple_albums), \
             mock.patch("backend.providers.audius.get_trending", return_value=[]):
            home = self.catalog.get_home("vn", refresh=True)
            self.assertEqual(home["mode"], "online")
            self.assertEqual(home["sections"][0]["id"], "trending-vn")
            nr = next(s for s in home["sections"] if s["id"] == "new-releases")
            self.assertGreaterEqual(len(nr["items"]), 10)

    def test_home_falls_back_vpop_search_when_apple_vn_rss_times_out(self):
        dz_chart = {
            "tracks": [{"id": "dz:1", "source": "deezer", "title": "Global Hit", "artist": "Pop Star", "duration": 200}],
            "albums": [],
            "artists": [],
        }
        vpop_fallback = [{"id": "it:99", "source": "itunes", "title": "Lạc Trôi", "artist": "Sơn Tùng M-TP", "duration": 233}]
        with mock.patch("backend.providers.deezer.get_chart", return_value=dz_chart), \
             mock.patch("backend.providers.deezer.get_new_releases", return_value=[]), \
             mock.patch("backend.providers.apple_itunes.get_top_songs", side_effect=TimeoutError("RSS timeout")), \
             mock.patch("backend.providers.apple_itunes.get_top_albums", side_effect=TimeoutError("RSS timeout")), \
             mock.patch("backend.providers.audius.get_trending", return_value=[]), \
             mock.patch("backend.providers.apple_itunes.search_songs", return_value=vpop_fallback):
            home = self.catalog.get_home("global", refresh=True)
            self.assertEqual(home["mode"], "online")
            sec_ids = [s["id"] for s in home["sections"]]
            self.assertIn("trending-vn", sec_ids)
            vn_sec = next(s for s in home["sections"] if s["id"] == "trending-vn")
            self.assertEqual(vn_sec["items"][0]["id"], "it:99")

    def test_home_derives_albums_from_live_chart_tracks_if_album_feeds_timeout(self):
        dz_chart = {
            "tracks": [
                {
                    "id": f"dz:t{i}",
                    "source": "deezer",
                    "title": f"Song {i}",
                    "artist": f"Artist {i}",
                    "artistId": f"dz:ar{i}",
                    "album": f"Hot Album {i}",
                    "albumId": f"dz:alb{i}",
                    "duration": 200,
                    "coverSmall": f"https://img/{i}_s.jpg",
                    "coverLarge": f"https://img/{i}_l.jpg",
                }
                for i in range(1, 15)
            ],
            "albums": [{"id": "dz:book", "source": "deezer", "title": "Sci-Fi Roman (Ungekürzt)", "artist": "Author"}],
            "artists": [],
        }
        with mock.patch("backend.providers.deezer.get_chart", return_value=dz_chart), \
             mock.patch("backend.providers.deezer.get_new_releases", return_value=[]), \
             mock.patch("backend.providers.apple_itunes.get_top_songs", return_value=[]), \
             mock.patch("backend.providers.apple_itunes.get_top_albums", side_effect=TimeoutError("timeout")), \
             mock.patch("backend.providers.apple_itunes.search_songs", return_value=[]), \
             mock.patch("backend.providers.audius.get_trending", return_value=[]):
            home = self.catalog.get_home("global", refresh=True)
            nr = next(s for s in home["sections"] if s["id"] == "new-releases")
            self.assertGreaterEqual(len(nr["items"]), 12)
            # Audiobook entry filtered out
            self.assertFalse(any("ungekürzt" in a["title"].lower() for a in nr["items"]))

    def test_deezer_artist_populates_tracks_from_album_when_top_is_empty(self):
        from backend.providers import deezer

        def fake_fetch(url, **kwargs):
            if url.endswith("/artist/123"):
                return {"id": 123, "name": "Indie Author", "picture_big": "https://img/a.jpg", "nb_fan": 500}
            if "/artist/123/top" in url:
                return {"data": []}
            if "/artist/123/albums" in url:
                return {"data": [{"id": 999, "title": "First Album", "cover_big": "https://img/al.jpg"}]}
            if url.endswith("/album/999"):
                return {
                    "id": 999,
                    "title": "First Album",
                    "artist": {"id": 123, "name": "Indie Author"},
                    "tracks": {
                        "data": [
                            {"id": 7001, "title": "Track One", "duration": 200, "artist": {"id": 123, "name": "Indie Author"}}
                        ]
                    },
                }
            return {}

        with mock.patch("backend.http_client.fetch_json", side_effect=fake_fetch):
            res = deezer.get_artist("123")
            self.assertIsNotNone(res)
            artist, tracks, albums = res
            self.assertEqual(artist["name"], "Indie Author")
            self.assertEqual(len(albums), 1)
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0]["id"], "dz:7001")


if __name__ == "__main__":
    unittest.main()
