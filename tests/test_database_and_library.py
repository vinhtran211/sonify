"""Unit and persistence tests for SQLite Database and LibraryService."""

from __future__ import annotations

import os
import tempfile
import unittest

from backend.db import Database
from backend.services.library_service import LibraryError, LibraryService


def _sample_track(tid: str, title: str, artist: str = "Test Artist", cover: str = "") -> dict:
    return {
        "id": tid,
        "source": tid.split(":", 1)[0],
        "title": title,
        "artist": artist,
        "artistId": "dz:100",
        "album": "Test Album",
        "albumId": "dz:200",
        "duration": 210,
        "coverSmall": cover or f"https://img.example.com/{tid}_s.jpg",
        "coverLarge": cover or f"https://img.example.com/{tid}_l.jpg",
        "explicit": False,
        "releaseDate": "2026-01-15",
        "previewUrl": f"https://preview.example.com/{tid}.mp3",
    }


class TestDatabaseAndLibrary(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "test_music.db")
        self.db = Database(self.db_path)
        self.lib = LibraryService(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_wal_mode_and_schema_tables(self):
        self.assertTrue(self.db.is_ready())
        conn = self.db.connect()
        try:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            tables = {
                r[0]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        finally:
            conn.close()
        self.assertEqual(mode.lower(), "wal")
        self.assertEqual(fk, 1)
        for expected in (
            "tracks_cache",
            "playlists",
            "playlist_tracks",
            "liked_tracks",
            "play_history",
            "lyrics_cache",
            "settings",
        ):
            self.assertIn(expected, tables)

    def test_tracks_cache_upsert_and_extra_merge_and_vietnamese_search(self):
        t = _sample_track("dz:1", "Đêm Sài Gòn", "Minh Linh")
        t["_extra"] = {"videoId": "abc123"}
        self.db.upsert_track(t)

        # Second upsert without _extra must NOT erase existing extra_json
        t2 = _sample_track("dz:1", "Đêm Sài Gòn", "Minh Linh")
        self.db.upsert_track(t2)

        loaded = self.db.get_track("dz:1", include_extra=True)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["_extra"].get("videoId"), "abc123")

        # Update extra merges keys
        self.db.update_track_extra("dz:1", {"bitrate": 320})
        loaded2 = self.db.get_track("dz:1", include_extra=True)
        self.assertEqual(loaded2["_extra"], {"videoId": "abc123", "bitrate": 320})

        # Diacritic-insensitive cached search
        hits = self.db.search_cached_tracks("dem sai gon")
        self.assertEqual([h["id"] for h in hits], ["dz:1"])

    def test_likes_and_persistence_across_restart(self):
        t1 = _sample_track("dz:10", "Track One")
        t2 = _sample_track("dz:20", "Track Two")

        res1 = self.lib.like_track({"track": t1})
        self.assertEqual(res1, {"liked": True, "trackId": "dz:10"})
        # Idempotent re-like
        self.lib.like_track({"trackId": "dz:10"})
        self.lib.like_track({"track": t2})

        likes = self.lib.get_likes()
        self.assertEqual(set(likes["likedIds"]), {"dz:10", "dz:20"})
        self.assertTrue(all(t["liked"] for t in likes["tracks"]))

        # Simulate server restart by opening a new Database instance on same file
        db_reopened = Database(self.db_path)
        lib_reopened = LibraryService(db_reopened)
        likes2 = lib_reopened.get_likes()
        self.assertEqual(set(likes2["likedIds"]), {"dz:10", "dz:20"})

        # Unlike persists
        lib_reopened.unlike_track("dz:10")
        self.assertEqual(lib_reopened.get_likes()["likedIds"], ["dz:20"])

        # Missing trackId raises 404
        with self.assertRaises(LibraryError) as ctx:
            self.lib.like_track({"trackId": "dz:nonexistent"})
        self.assertEqual(ctx.exception.status, 404)

    def test_playlist_full_crud_mosaic_reorder_and_persistence(self):
        # Validation errors
        with self.assertRaises(LibraryError) as ctx:
            self.lib.create_playlist({"name": "   "})
        self.assertEqual(ctx.exception.status, 400)

        with self.assertRaises(LibraryError) as ctx:
            self.lib.create_playlist({"name": "x" * 121})
        self.assertEqual(ctx.exception.status, 400)

        created = self.lib.create_playlist({"name": "Chill Night", "description": "Late vibes"})
        pl = created["playlist"]
        pid = pl["id"]
        self.assertEqual(pl["name"], "Chill Night")
        self.assertEqual(pl["trackCount"], 0)
        self.assertEqual(pl["covers"], [])

        # Add 5 tracks with distinct covers -> mosaic caps at 4
        tracks = [_sample_track(f"dz:{i}", f"Song {i}", cover=f"https://c/{i}.jpg") for i in range(1, 6)]
        for idx, tr in enumerate(tracks):
            r = self.lib.add_track_to_playlist(pid, {"track": tr})
            self.assertEqual(r["position"], idx)
            self.assertFalse(r["alreadyInPlaylist"])

        # Duplicate add handled cleanly without raising
        dup = self.lib.add_track_to_playlist(pid, {"track": tracks[0]})
        self.assertTrue(dup["alreadyInPlaylist"])
        self.assertEqual(dup["trackCount"], 5)

        detail = self.lib.get_playlist(pid)
        self.assertEqual(len(detail["playlist"]["covers"]), 4)
        self.assertEqual([t["id"] for t in detail["tracks"]], ["dz:1", "dz:2", "dz:3", "dz:4", "dz:5"])

        # Reorder tracks (put dz:5 first, dz:2 second, rest preserve order)
        self.lib.reorder_playlist_tracks(pid, {"trackIds": ["dz:5", "dz:2", "dz:unknown"]})
        reordered = self.lib.get_playlist(pid)
        self.assertEqual([t["id"] for t in reordered["tracks"]], ["dz:5", "dz:2", "dz:1", "dz:3", "dz:4"])

        # Remove track and verify normalized positions
        rem = self.lib.remove_track_from_playlist(pid, "dz:2")
        self.assertEqual(rem, {"removed": True, "trackCount": 4})
        after_rem = self.lib.get_playlist(pid)
        self.assertEqual([t["id"] for t in after_rem["tracks"]], ["dz:5", "dz:1", "dz:3", "dz:4"])
        self.assertEqual([t["position"] for t in after_rem["tracks"]], [0, 1, 2, 3])

        # Rename & update description
        upd = self.lib.update_playlist(pid, {"name": "Midnight Focus", "description": "Updated"})
        self.assertEqual(upd["playlist"]["name"], "Midnight Focus")
        self.assertEqual(upd["playlist"]["description"], "Updated")

        # Persistence across DB reopen
        db2 = Database(self.db_path)
        lib2 = LibraryService(db2)
        persisted = lib2.get_playlist(pid)
        self.assertEqual(persisted["playlist"]["name"], "Midnight Focus")
        self.assertEqual([t["id"] for t in persisted["tracks"]], ["dz:5", "dz:1", "dz:3", "dz:4"])

        # Delete playlist cascades cleanly
        deleted = lib2.delete_playlist(pid)
        self.assertEqual(deleted, {"deleted": True, "id": pid})
        with self.assertRaises(LibraryError) as ctx:
            lib2.get_playlist(pid)
        self.assertEqual(ctx.exception.status, 404)

    def test_history_and_settings_validation_and_persistence(self):
        t1 = _sample_track("dz:101", "First Played")
        t2 = _sample_track("dz:102", "Second Played")
        self.lib.record_history({"track": t1})
        self.lib.record_history({"track": t1})  # consecutive within 10s deduped
        self.lib.record_history({"track": t2})

        hist = self.lib.get_history(limit=10)["items"]
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["track"]["id"], "dz:102")
        self.assertEqual(hist[1]["track"]["id"], "dz:101")

        self.lib.clear_history()
        self.assertEqual(self.lib.get_history()["items"], [])

        # Settings defaults & partial PATCH
        defaults = self.lib.get_settings()
        self.assertEqual(defaults["volume"], 0.8)
        self.assertEqual(defaults["repeat"], "off")

        updated = self.lib.update_settings({"volume": 1.5, "shuffle": True, "repeat": "one", "region": "vn"})
        self.assertEqual(updated["volume"], 1.0)  # clamped
        self.assertTrue(updated["shuffle"])
        self.assertEqual(updated["repeat"], "one")
        self.assertEqual(updated["region"], "vn")

        # Invalid settings rejected
        with self.assertRaises(LibraryError):
            self.lib.update_settings({"repeat": "forever"})
        with self.assertRaises(LibraryError):
            self.lib.update_settings({"region": "mars"})

        # Settings persist across DB reopen
        db2 = Database(self.db_path)
        self.assertEqual(db2.get_settings()["repeat"], "one")
        self.assertEqual(db2.get_settings()["region"], "vn")


if __name__ == "__main__":
    unittest.main()
