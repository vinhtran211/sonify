"""SQLite persistence layer for Sonify (tracks cache, playlists, likes, history, lyrics, settings)."""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from backend import config, lrc_parser

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tracks_cache (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    artist_id TEXT DEFAULT '',
    album TEXT DEFAULT '',
    album_id TEXT DEFAULT '',
    duration INTEGER DEFAULT 0,
    cover_small TEXT DEFAULT '',
    cover_large TEXT DEFAULT '',
    explicit INTEGER DEFAULT 0,
    release_date TEXT DEFAULT '',
    preview_url TEXT DEFAULT '',
    extra_json TEXT DEFAULT '{}',
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS playlists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id TEXT NOT NULL,
    track_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, track_id),
    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS liked_tracks (
    track_id TEXT PRIMARY KEY,
    liked_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS play_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    played_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_played_at ON play_history(played_at DESC);

CREATE TABLE IF NOT EXISTS lyrics_cache (
    cache_key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    source TEXT DEFAULT '',
    instrumental INTEGER DEFAULT 0,
    synced_lrc TEXT DEFAULT '',
    plain_lyrics TEXT DEFAULT '',
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


def row_to_track(row: sqlite3.Row, liked_ids: Optional[Set[str]] = None) -> Dict[str, Any]:
    track_id = row["id"]
    return {
        "id": track_id,
        "source": row["source"],
        "title": row["title"],
        "artist": row["artist"],
        "artistId": row["artist_id"] or "",
        "album": row["album"] or "",
        "albumId": row["album_id"] or "",
        "duration": int(row["duration"] or 0),
        "coverSmall": row["cover_small"] or "",
        "coverLarge": row["cover_large"] or "",
        "explicit": bool(row["explicit"]),
        "releaseDate": row["release_date"] or "",
        "previewUrl": row["preview_url"] or "",
        "streamUrl": f"/api/stream/{track_id}",
        "liked": (track_id in liked_ids) if liked_ids is not None else False,
    }


class Database:
    """Thread-safe SQLite database wrapper with WAL mode and auto-initialization."""

    def __init__(self, db_path: str | Path = config.DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.init_db()
        except (OSError, sqlite3.OperationalError) as exc:
            fallback = config.FALLBACK_DB_PATH
            if self.db_path.resolve() == fallback.resolve():
                raise
            sys.stderr.write(
                f"[Sonify] Warning: DB path '{self.db_path}' not writable ({exc}); "
                f"falling back to '{fallback}'.\n"
            )
            self.db_path = fallback
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def init_db(self) -> None:
        with self._lock:
            conn = self.connect()
            try:
                conn.executescript(_SCHEMA_SQL)
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS followed_artists (
                        artist_id   TEXT PRIMARY KEY,
                        name        TEXT NOT NULL,
                        artwork_url TEXT DEFAULT '',
                        fans        INTEGER DEFAULT 0,
                        followed_at INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS saved_albums (
                        album_id     TEXT PRIMARY KEY,
                        title        TEXT NOT NULL,
                        artist       TEXT NOT NULL,
                        artist_id    TEXT DEFAULT '',
                        artwork_url  TEXT DEFAULT '',
                        release_date TEXT DEFAULT '',
                        track_count  INTEGER DEFAULT 0,
                        saved_at     INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS pinned_items (
                        item_type TEXT NOT NULL,
                        item_id   TEXT NOT NULL,
                        pinned_at INTEGER NOT NULL,
                        PRIMARY KEY (item_type, item_id)
                    );
                    """
                )
                existing_cols = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(playlists)").fetchall()
                }
                for col_name in ("cover_color", "cover_emoji", "cover_url"):
                    if col_name not in existing_cols:
                        conn.execute(
                            f"ALTER TABLE playlists ADD COLUMN {col_name} TEXT DEFAULT ''"
                        )
                conn.commit()
            finally:
                conn.close()

    def is_ready(self) -> bool:
        try:
            conn = self.connect()
            try:
                row = conn.execute("SELECT 1").fetchone()
                return bool(row and row[0] == 1)
            finally:
                conn.close()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Tracks cache
    # ------------------------------------------------------------------
    def upsert_track(self, track: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> None:
        if not track or not track.get("id"):
            return
        self.upsert_tracks([track], extra_map={track["id"]: extra} if extra else None)

    def upsert_tracks(
        self,
        tracks: Iterable[Dict[str, Any]],
        extra_map: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        now = int(time.time())
        items = [t for t in tracks if t and t.get("id")]
        if not items:
            return

        with self._lock:
            conn = self.connect()
            try:
                for t in items:
                    tid = str(t["id"])
                    incoming_extra = (extra_map or {}).get(tid) or t.get("_extra")
                    if incoming_extra is not None:
                        existing = conn.execute(
                            "SELECT extra_json FROM tracks_cache WHERE id = ?", (tid,)
                        ).fetchone()
                        merged: Dict[str, Any] = {}
                        if existing and existing["extra_json"]:
                            try:
                                merged = json.loads(existing["extra_json"])
                            except Exception:
                                merged = {}
                        merged.update(incoming_extra)
                        extra_str = json.dumps(merged, ensure_ascii=False)
                        conn.execute(
                            """
                            INSERT INTO tracks_cache (
                                id, source, title, artist, artist_id, album, album_id,
                                duration, cover_small, cover_large, explicit, release_date,
                                preview_url, extra_json, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                source = excluded.source,
                                title = excluded.title,
                                artist = excluded.artist,
                                artist_id = CASE WHEN excluded.artist_id != '' THEN excluded.artist_id ELSE tracks_cache.artist_id END,
                                album = CASE WHEN excluded.album != '' THEN excluded.album ELSE tracks_cache.album END,
                                album_id = CASE WHEN excluded.album_id != '' THEN excluded.album_id ELSE tracks_cache.album_id END,
                                duration = CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE tracks_cache.duration END,
                                cover_small = CASE WHEN excluded.cover_small != '' THEN excluded.cover_small ELSE tracks_cache.cover_small END,
                                cover_large = CASE WHEN excluded.cover_large != '' THEN excluded.cover_large ELSE tracks_cache.cover_large END,
                                explicit = excluded.explicit,
                                release_date = CASE WHEN excluded.release_date != '' THEN excluded.release_date ELSE tracks_cache.release_date END,
                                preview_url = CASE WHEN excluded.preview_url != '' THEN excluded.preview_url ELSE tracks_cache.preview_url END,
                                extra_json = excluded.extra_json,
                                updated_at = excluded.updated_at
                            """,
                            (
                                tid,
                                str(t.get("source") or tid.split(":", 1)[0]),
                                str(t.get("title") or "Untitled"),
                                str(t.get("artist") or "Unknown Artist"),
                                str(t.get("artistId") or ""),
                                str(t.get("album") or ""),
                                str(t.get("albumId") or ""),
                                int(t.get("duration") or 0),
                                str(t.get("coverSmall") or ""),
                                str(t.get("coverLarge") or t.get("coverSmall") or ""),
                                1 if t.get("explicit") else 0,
                                str(t.get("releaseDate") or ""),
                                str(t.get("previewUrl") or ""),
                                extra_str,
                                now,
                            ),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO tracks_cache (
                                id, source, title, artist, artist_id, album, album_id,
                                duration, cover_small, cover_large, explicit, release_date,
                                preview_url, extra_json, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)
                            ON CONFLICT(id) DO UPDATE SET
                                source = excluded.source,
                                title = excluded.title,
                                artist = excluded.artist,
                                artist_id = CASE WHEN excluded.artist_id != '' THEN excluded.artist_id ELSE tracks_cache.artist_id END,
                                album = CASE WHEN excluded.album != '' THEN excluded.album ELSE tracks_cache.album END,
                                album_id = CASE WHEN excluded.album_id != '' THEN excluded.album_id ELSE tracks_cache.album_id END,
                                duration = CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE tracks_cache.duration END,
                                cover_small = CASE WHEN excluded.cover_small != '' THEN excluded.cover_small ELSE tracks_cache.cover_small END,
                                cover_large = CASE WHEN excluded.cover_large != '' THEN excluded.cover_large ELSE tracks_cache.cover_large END,
                                explicit = excluded.explicit,
                                release_date = CASE WHEN excluded.release_date != '' THEN excluded.release_date ELSE tracks_cache.release_date END,
                                preview_url = CASE WHEN excluded.preview_url != '' THEN excluded.preview_url ELSE tracks_cache.preview_url END,
                                updated_at = excluded.updated_at
                            """,
                            (
                                tid,
                                str(t.get("source") or tid.split(":", 1)[0]),
                                str(t.get("title") or "Untitled"),
                                str(t.get("artist") or "Unknown Artist"),
                                str(t.get("artistId") or ""),
                                str(t.get("album") or ""),
                                str(t.get("albumId") or ""),
                                int(t.get("duration") or 0),
                                str(t.get("coverSmall") or ""),
                                str(t.get("coverLarge") or t.get("coverSmall") or ""),
                                1 if t.get("explicit") else 0,
                                str(t.get("releaseDate") or ""),
                                str(t.get("previewUrl") or ""),
                                now,
                            ),
                        )
                conn.commit()
            finally:
                conn.close()

    def get_track(self, track_id: str, include_extra: bool = False) -> Optional[Dict[str, Any]]:
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM tracks_cache WHERE id = ?", (track_id,)).fetchone()
            if not row:
                return None
            liked_ids = self.get_liked_ids(conn)
            track = row_to_track(row, liked_ids)
            if include_extra:
                try:
                    track["_extra"] = json.loads(row["extra_json"] or "{}")
                except Exception:
                    track["_extra"] = {}
            return track
        finally:
            conn.close()

    def update_track_extra(self, track_id: str, updates: Dict[str, Any]) -> None:
        with self._lock:
            conn = self.connect()
            try:
                row = conn.execute("SELECT extra_json FROM tracks_cache WHERE id = ?", (track_id,)).fetchone()
                if not row:
                    return
                try:
                    data = json.loads(row["extra_json"] or "{}")
                except Exception:
                    data = {}
                data.update(updates)
                conn.execute(
                    "UPDATE tracks_cache SET extra_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(data, ensure_ascii=False), int(time.time()), track_id),
                )
                conn.commit()
            finally:
                conn.close()

    def search_cached_tracks(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        q_clean = (query or "").strip()
        pattern = f"%{q_clean}%"
        q_norm = lrc_parser.normalize_key(q_clean)
        q_tokens = [tok for tok in q_norm.split() if tok]
        conn = self.connect()
        try:
            liked_ids = self.get_liked_ids(conn)
            rows = conn.execute(
                """
                SELECT * FROM tracks_cache
                WHERE title LIKE ? OR artist LIKE ? OR album LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (pattern, pattern, pattern, limit),
            ).fetchall()
            tracks = [row_to_track(r, liked_ids) for r in rows]
            if len(tracks) >= limit or not q_tokens:
                return tracks

            seen_ids = {t["id"] for t in tracks}
            # Fallback diacritic-insensitive scan across recent cached tracks (e.g. Vietnamese ASCII queries)
            scan_rows = conn.execute(
                "SELECT * FROM tracks_cache ORDER BY updated_at DESC LIMIT 500"
            ).fetchall()
            for r in scan_rows:
                if r["id"] in seen_ids:
                    continue
                haystack = lrc_parser.normalize_key(f"{r['title']} {r['artist']} {r['album']}")
                if q_norm in haystack or all(tok in haystack for tok in q_tokens):
                    tracks.append(row_to_track(r, liked_ids))
                    seen_ids.add(r["id"])
                    if len(tracks) >= limit:
                        break
            return tracks
        finally:
            conn.close()

    def get_tracks_by_artist_id(self, artist_id: str, limit: int = 25) -> List[Dict[str, Any]]:
        conn = self.connect()
        try:
            liked_ids = self.get_liked_ids(conn)
            rows = conn.execute(
                "SELECT * FROM tracks_cache WHERE artist_id = ? ORDER BY updated_at DESC LIMIT ?",
                (artist_id, limit),
            ).fetchall()
            return [row_to_track(r, liked_ids) for r in rows]
        finally:
            conn.close()

    def get_tracks_by_album_id(self, album_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self.connect()
        try:
            liked_ids = self.get_liked_ids(conn)
            rows = conn.execute(
                "SELECT * FROM tracks_cache WHERE album_id = ? ORDER BY updated_at ASC LIMIT ?",
                (album_id, limit),
            ).fetchall()
            return [row_to_track(r, liked_ids) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Likes
    # ------------------------------------------------------------------
    def get_liked_ids(self, conn: Optional[sqlite3.Connection] = None) -> Set[str]:
        own = conn is None
        c = conn or self.connect()
        try:
            rows = c.execute("SELECT track_id FROM liked_tracks").fetchall()
            return {r["track_id"] for r in rows}
        finally:
            if own:
                c.close()

    def annotate_tracks(self, tracks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        liked_ids = self.get_liked_ids()
        result: List[Dict[str, Any]] = []
        for t in tracks:
            item = {k: v for k, v in t.items() if k != "_extra"}
            item["streamUrl"] = f"/api/stream/{item['id']}"
            item["liked"] = item["id"] in liked_ids
            result.append(item)
        return result

    def get_liked_tracks(self) -> Tuple[List[Dict[str, Any]], List[str]]:
        conn = self.connect()
        try:
            rows = conn.execute(
                """
                SELECT tc.*, lt.liked_at
                FROM liked_tracks lt
                JOIN tracks_cache tc ON tc.id = lt.track_id
                ORDER BY lt.liked_at DESC
                """
            ).fetchall()
            liked_ids = [r["id"] for r in rows]
            liked_set = set(liked_ids)
            tracks = []
            for r in rows:
                t = row_to_track(r, liked_set)
                t["likedAt"] = int(r["liked_at"])
                tracks.append(t)
            return tracks, liked_ids
        finally:
            conn.close()

    def like_track(self, track_id: str, track: Optional[Dict[str, Any]] = None) -> None:
        if track:
            self.upsert_track(track)
        now = int(time.time())
        with self._lock:
            conn = self.connect()
            try:
                conn.execute(
                    """
                    INSERT INTO liked_tracks (track_id, liked_at)
                    VALUES (?, ?)
                    ON CONFLICT(track_id) DO UPDATE SET liked_at = excluded.liked_at
                    """,
                    (track_id, now),
                )
                conn.commit()
            finally:
                conn.close()

    def unlike_track(self, track_id: str) -> None:
        with self._lock:
            conn = self.connect()
            try:
                conn.execute("DELETE FROM liked_tracks WHERE track_id = ?", (track_id,))
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Playlists CRUD
    # ------------------------------------------------------------------
    def _playlist_row_to_dict(self, conn: sqlite3.Connection, row: sqlite3.Row) -> Dict[str, Any]:
        pid = row["id"]
        count_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM playlist_tracks WHERE playlist_id = ?", (pid,)
        ).fetchone()
        cover_rows = conn.execute(
            """
            SELECT tc.cover_small, tc.cover_large
            FROM playlist_tracks pt
            JOIN tracks_cache tc ON tc.id = pt.track_id
            WHERE pt.playlist_id = ?
            ORDER BY pt.position ASC
            LIMIT 8
            """,
            (pid,),
        ).fetchall()
        covers: List[str] = []
        for cr in cover_rows:
            c = cr["cover_large"] or cr["cover_small"] or ""
            if c and c not in covers:
                covers.append(c)
            if len(covers) >= 4:
                break
        row_keys = set(row.keys())
        return {
            "id": pid,
            "name": row["name"],
            "description": row["description"] or "",
            "trackCount": int(count_row["cnt"]) if count_row else 0,
            "covers": covers,
            "coverColor": (row["cover_color"] or "") if "cover_color" in row_keys else "",
            "coverEmoji": (row["cover_emoji"] or "") if "cover_emoji" in row_keys else "",
            "coverUrl": (row["cover_url"] or "") if "cover_url" in row_keys else "",
            "createdAt": int(row["created_at"]),
            "updatedAt": int(row["updated_at"]),
        }

    def list_playlists(self) -> List[Dict[str, Any]]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM playlists ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
            return [self._playlist_row_to_dict(conn, r) for r in rows]
        finally:
            conn.close()

    def create_playlist(self, name: str, description: str = "") -> Dict[str, Any]:
        pid = f"pl_{uuid.uuid4().hex[:12]}"
        now = int(time.time())
        with self._lock:
            conn = self.connect()
            try:
                conn.execute(
                    "INSERT INTO playlists (id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (pid, name.strip(), description.strip(), now, now),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM playlists WHERE id = ?", (pid,)).fetchone()
                return self._playlist_row_to_dict(conn, row)
            finally:
                conn.close()

    def get_playlist(self, playlist_id: str) -> Optional[Dict[str, Any]]:
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
            if not row:
                return None
            liked_ids = self.get_liked_ids(conn)
            track_rows = conn.execute(
                """
                SELECT tc.*, pt.position, pt.added_at
                FROM playlist_tracks pt
                JOIN tracks_cache tc ON tc.id = pt.track_id
                WHERE pt.playlist_id = ?
                ORDER BY pt.position ASC
                """,
                (playlist_id,),
            ).fetchall()
            tracks = []
            for tr in track_rows:
                t = row_to_track(tr, liked_ids)
                t["position"] = int(tr["position"])
                t["addedAt"] = int(tr["added_at"])
                tracks.append(t)
            return {
                "playlist": self._playlist_row_to_dict(conn, row),
                "tracks": tracks,
            }
        finally:
            conn.close()

    def update_playlist(
        self,
        playlist_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        cover_color: Optional[str] = None,
        cover_emoji: Optional[str] = None,
        cover_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        now = int(time.time())
        with self._lock:
            conn = self.connect()
            try:
                row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
                if not row:
                    return None
                new_name = row["name"] if name is None else name.strip()
                new_desc = row["description"] if description is None else description.strip()
                new_color = (row["cover_color"] or "") if cover_color is None else cover_color.strip()
                new_emoji = (row["cover_emoji"] or "") if cover_emoji is None else cover_emoji.strip()
                new_url = (row["cover_url"] or "") if cover_url is None else cover_url.strip()
                conn.execute(
                    """
                    UPDATE playlists
                    SET name = ?, description = ?, cover_color = ?, cover_emoji = ?, cover_url = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (new_name, new_desc, new_color, new_emoji, new_url, now, playlist_id),
                )
                conn.commit()
                updated = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
                return self._playlist_row_to_dict(conn, updated)
            finally:
                conn.close()

    def duplicate_playlist(
        self, playlist_id: str, new_name: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        source = self.get_playlist(playlist_id)
        if not source:
            return None
        pl = source["playlist"]
        cloned_name = (new_name or f"{pl['name']} (Copy)").strip()[:120]
        pid = f"pl_{uuid.uuid4().hex[:12]}"
        now = int(time.time())
        with self._lock:
            conn = self.connect()
            try:
                conn.execute(
                    """
                    INSERT INTO playlists (id, name, description, cover_color, cover_emoji, cover_url, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pid,
                        cloned_name,
                        pl.get("description", ""),
                        pl.get("coverColor", ""),
                        pl.get("coverEmoji", ""),
                        pl.get("coverUrl", ""),
                        now,
                        now,
                    ),
                )
                for idx, tr in enumerate(source["tracks"]):
                    conn.execute(
                        """
                        INSERT INTO playlist_tracks (playlist_id, track_id, position, added_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (pid, tr["id"], idx, now),
                    )
                conn.commit()
            finally:
                conn.close()
        return self.get_playlist(pid)

    def delete_playlist(self, playlist_id: str) -> bool:
        with self._lock:
            conn = self.connect()
            try:
                cur = conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def add_track_to_playlist(
        self, playlist_id: str, track_id: str, track: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        if track:
            self.upsert_track(track)
        now = int(time.time())
        with self._lock:
            conn = self.connect()
            try:
                exists = conn.execute("SELECT id FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
                if not exists:
                    return None
                existing_link = conn.execute(
                    "SELECT position FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
                    (playlist_id, track_id),
                ).fetchone()
                if existing_link:
                    position = int(existing_link["position"])
                    duplicate = True
                else:
                    max_row = conn.execute(
                        "SELECT COALESCE(MAX(position), -1) AS mx FROM playlist_tracks WHERE playlist_id = ?",
                        (playlist_id,),
                    ).fetchone()
                    position = int(max_row["mx"]) + 1
                    conn.execute(
                        """
                        INSERT INTO playlist_tracks (playlist_id, track_id, position, added_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (playlist_id, track_id, position, now),
                    )
                    conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
                    duplicate = False
                conn.commit()
                cnt_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,)
                ).fetchone()
                return {
                    "playlistId": playlist_id,
                    "trackId": track_id,
                    "position": position,
                    "trackCount": int(cnt_row["cnt"]),
                    "alreadyInPlaylist": duplicate,
                }
            finally:
                conn.close()

    def remove_track_from_playlist(self, playlist_id: str, track_id: str) -> Optional[Dict[str, Any]]:
        now = int(time.time())
        with self._lock:
            conn = self.connect()
            try:
                exists = conn.execute("SELECT id FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
                if not exists:
                    return None
                conn.execute(
                    "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
                    (playlist_id, track_id),
                )
                # Normalize remaining positions cleanly
                rem = conn.execute(
                    "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position ASC",
                    (playlist_id,),
                ).fetchall()
                for idx, r in enumerate(rem):
                    conn.execute(
                        "UPDATE playlist_tracks SET position = ? WHERE playlist_id = ? AND track_id = ?",
                        (idx, playlist_id, r["track_id"]),
                    )
                conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
                conn.commit()
                return {"removed": True, "trackCount": len(rem)}
            finally:
                conn.close()

    def reorder_playlist_tracks(self, playlist_id: str, track_ids: List[str]) -> Optional[bool]:
        now = int(time.time())
        with self._lock:
            conn = self.connect()
            try:
                exists = conn.execute("SELECT id FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
                if not exists:
                    return None
                existing_rows = conn.execute(
                    "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position ASC",
                    (playlist_id,),
                ).fetchall()
                existing_ids = [r["track_id"] for r in existing_rows]
                ordered: List[str] = []
                for tid in track_ids:
                    if tid in existing_ids and tid not in ordered:
                        ordered.append(tid)
                for tid in existing_ids:
                    if tid not in ordered:
                        ordered.append(tid)
                for idx, tid in enumerate(ordered):
                    conn.execute(
                        "UPDATE playlist_tracks SET position = ? WHERE playlist_id = ? AND track_id = ?",
                        (idx, playlist_id, tid),
                    )
                conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
                conn.commit()
                return True
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Play History
    # ------------------------------------------------------------------
    def record_history(self, track_id: str, track: Optional[Dict[str, Any]] = None) -> None:
        if track:
            self.upsert_track(track)
        now = int(time.time())
        with self._lock:
            conn = self.connect()
            try:
                # Avoid duplicate consecutive entries within 10 seconds
                last = conn.execute(
                    "SELECT id, track_id, played_at FROM play_history ORDER BY played_at DESC, id DESC LIMIT 1"
                ).fetchone()
                if last and last["track_id"] == track_id and abs(now - int(last["played_at"])) < 10:
                    conn.execute("UPDATE play_history SET played_at = ? WHERE id = ?", (now, last["id"]))
                else:
                    conn.execute(
                        "INSERT INTO play_history (track_id, played_at) VALUES (?, ?)",
                        (track_id, now),
                    )
                # Keep history bounded to latest 300 entries
                conn.execute(
                    """
                    DELETE FROM play_history
                    WHERE id NOT IN (
                        SELECT id FROM play_history ORDER BY played_at DESC, id DESC LIMIT 300
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self.connect()
        try:
            liked_ids = self.get_liked_ids(conn)
            rows = conn.execute(
                """
                SELECT tc.*, ph.played_at
                FROM play_history ph
                JOIN tracks_cache tc ON tc.id = ph.track_id
                ORDER BY ph.played_at DESC, ph.id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
            return [
                {"playedAt": int(r["played_at"]), "track": row_to_track(r, liked_ids)}
                for r in rows
            ]
        finally:
            conn.close()

    def clear_history(self) -> None:
        with self._lock:
            conn = self.connect()
            try:
                conn.execute("DELETE FROM play_history")
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Lyrics Cache
    # ------------------------------------------------------------------
    def get_lyrics_cache(self, cache_key: str, max_age: int = config.LYRICS_CACHE_TTL) -> Optional[Dict[str, Any]]:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM lyrics_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
            if not row:
                return None
            if (int(time.time()) - int(row["updated_at"])) > max_age:
                return None
            return {
                "status": row["status"],
                "source": row["source"] or "",
                "instrumental": bool(row["instrumental"]),
                "syncedLrc": row["synced_lrc"] or "",
                "plainLyrics": row["plain_lyrics"] or "",
                "updatedAt": int(row["updated_at"]),
            }
        finally:
            conn.close()

    def set_lyrics_cache(
        self,
        cache_key: str,
        status: str,
        source: str,
        instrumental: bool,
        synced_lrc: str,
        plain_lyrics: str,
    ) -> None:
        now = int(time.time())
        with self._lock:
            conn = self.connect()
            try:
                conn.execute(
                    """
                    INSERT INTO lyrics_cache (
                        cache_key, status, source, instrumental, synced_lrc, plain_lyrics, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        status = excluded.status,
                        source = excluded.source,
                        instrumental = excluded.instrumental,
                        synced_lrc = excluded.synced_lrc,
                        plain_lyrics = excluded.plain_lyrics,
                        updated_at = excluded.updated_at
                    """,
                    (
                        cache_key,
                        status,
                        source,
                        1 if instrumental else 0,
                        synced_lrc or "",
                        plain_lyrics or "",
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def get_settings(self) -> Dict[str, Any]:
        result = dict(config.DEFAULT_SETTINGS)
        conn = self.connect()
        try:
            rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
            for r in rows:
                try:
                    result[r["key"]] = json.loads(r["value_json"])
                except Exception:
                    pass
            return result
        finally:
            conn.close()

    def update_settings(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        allowed = set(config.DEFAULT_SETTINGS.keys())
        now = int(time.time())
        with self._lock:
            conn = self.connect()
            try:
                for k, v in updates.items():
                    if k not in allowed:
                        continue
                    conn.execute(
                        """
                        INSERT INTO settings (key, value_json, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            value_json = excluded.value_json,
                            updated_at = excluded.updated_at
                        """,
                        (k, json.dumps(v), now),
                    )
                conn.commit()
            finally:
                conn.close()
        return self.get_settings()

    # ------------------------------------------------------------------
    # Followed Artists, Saved Albums & Pinned Items
    # ------------------------------------------------------------------
    def get_followed_artists(self) -> Tuple[List[Dict[str, Any]], List[str]]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM followed_artists ORDER BY followed_at DESC"
            ).fetchall()
            artists = [
                {
                    "id": r["artist_id"],
                    "name": r["name"],
                    "pictureSmall": r["artwork_url"] or "",
                    "pictureLarge": r["artwork_url"] or "",
                    "fans": int(r["fans"] or 0),
                    "followedAt": int(r["followed_at"]),
                }
                for r in rows
            ]
            return artists, [a["id"] for a in artists]
        finally:
            conn.close()

    def follow_artist(self, artist: Dict[str, Any]) -> str:
        aid = str(artist.get("id") or "").strip()
        name = str(artist.get("name") or "Unknown Artist").strip()
        artwork = str(
            artist.get("pictureLarge")
            or artist.get("pictureSmall")
            or artist.get("picture")
            or artist.get("artworkUrl")
            or ""
        ).strip()
        fans = int(artist.get("fans") or 0)
        now = int(time.time())
        with self._lock:
            conn = self.connect()
            try:
                conn.execute(
                    """
                    INSERT INTO followed_artists (artist_id, name, artwork_url, fans, followed_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(artist_id) DO UPDATE SET
                        name = excluded.name,
                        artwork_url = CASE WHEN excluded.artwork_url != '' THEN excluded.artwork_url ELSE followed_artists.artwork_url END,
                        fans = CASE WHEN excluded.fans > 0 THEN excluded.fans ELSE followed_artists.fans END,
                        followed_at = excluded.followed_at
                    """,
                    (aid, name, artwork, fans, now),
                )
                conn.commit()
            finally:
                conn.close()
        return aid

    def unfollow_artist(self, artist_id: str) -> None:
        with self._lock:
            conn = self.connect()
            try:
                conn.execute("DELETE FROM followed_artists WHERE artist_id = ?", (artist_id,))
                conn.execute(
                    "DELETE FROM pinned_items WHERE item_type = 'artist' AND item_id = ?",
                    (artist_id,),
                )
                conn.commit()
            finally:
                conn.close()

    def get_saved_albums(self) -> Tuple[List[Dict[str, Any]], List[str]]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM saved_albums ORDER BY saved_at DESC"
            ).fetchall()
            albums = [
                {
                    "id": r["album_id"],
                    "title": r["title"],
                    "artist": r["artist"],
                    "artistId": r["artist_id"] or "",
                    "coverSmall": r["artwork_url"] or "",
                    "coverLarge": r["artwork_url"] or "",
                    "releaseDate": r["release_date"] or "",
                    "trackCount": int(r["track_count"] or 0),
                    "savedAt": int(r["saved_at"]),
                }
                for r in rows
            ]
            return albums, [a["id"] for a in albums]
        finally:
            conn.close()

    def save_album(self, album: Dict[str, Any]) -> str:
        albid = str(album.get("id") or "").strip()
        title = str(album.get("title") or "Untitled Album").strip()
        artist = str(album.get("artist") or "Unknown Artist").strip()
        artist_id = str(album.get("artistId") or "").strip()
        artwork = str(
            album.get("coverLarge")
            or album.get("coverSmall")
            or album.get("artworkUrl")
            or ""
        ).strip()
        rel = str(album.get("releaseDate") or "").strip()
        tc = int(album.get("trackCount") or 0)
        now = int(time.time())
        with self._lock:
            conn = self.connect()
            try:
                conn.execute(
                    """
                    INSERT INTO saved_albums (
                        album_id, title, artist, artist_id, artwork_url, release_date, track_count, saved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(album_id) DO UPDATE SET
                        title = excluded.title,
                        artist = excluded.artist,
                        artist_id = CASE WHEN excluded.artist_id != '' THEN excluded.artist_id ELSE saved_albums.artist_id END,
                        artwork_url = CASE WHEN excluded.artwork_url != '' THEN excluded.artwork_url ELSE saved_albums.artwork_url END,
                        release_date = CASE WHEN excluded.release_date != '' THEN excluded.release_date ELSE saved_albums.release_date END,
                        track_count = CASE WHEN excluded.track_count > 0 THEN excluded.track_count ELSE saved_albums.track_count END,
                        saved_at = excluded.saved_at
                    """,
                    (albid, title, artist, artist_id, artwork, rel, tc, now),
                )
                conn.commit()
            finally:
                conn.close()
        return albid

    def unsave_album(self, album_id: str) -> None:
        with self._lock:
            conn = self.connect()
            try:
                conn.execute("DELETE FROM saved_albums WHERE album_id = ?", (album_id,))
                conn.execute(
                    "DELETE FROM pinned_items WHERE item_type = 'album' AND item_id = ?",
                    (album_id,),
                )
                conn.commit()
            finally:
                conn.close()

    def get_pins(self) -> List[Dict[str, Any]]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT item_type, item_id, pinned_at FROM pinned_items ORDER BY pinned_at DESC"
            ).fetchall()
            return [
                {
                    "itemType": r["item_type"],
                    "itemId": r["item_id"],
                    "pinnedAt": int(r["pinned_at"]),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def pin_item(self, item_type: str, item_id: str) -> None:
        now = int(time.time())
        with self._lock:
            conn = self.connect()
            try:
                conn.execute(
                    """
                    INSERT INTO pinned_items (item_type, item_id, pinned_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(item_type, item_id) DO UPDATE SET pinned_at = excluded.pinned_at
                    """,
                    (item_type, item_id, now),
                )
                conn.commit()
            finally:
                conn.close()

    def unpin_item(self, item_type: str, item_id: str) -> None:
        with self._lock:
            conn = self.connect()
            try:
                conn.execute(
                    "DELETE FROM pinned_items WHERE item_type = ? AND item_id = ?",
                    (item_type, item_id),
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Listening Stats ("Sound Capsule")
    # ------------------------------------------------------------------
    def get_listening_stats(self) -> Dict[str, Any]:
        conn = self.connect()
        try:
            liked_ids = self.get_liked_ids(conn)
            total_plays_row = conn.execute("SELECT COUNT(*) AS cnt FROM play_history").fetchone()
            total_plays = int(total_plays_row["cnt"]) if total_plays_row else 0

            dur_row = conn.execute(
                """
                SELECT COALESCE(SUM(CASE WHEN tc.duration > 0 THEN tc.duration ELSE 180 END), 0) AS secs
                FROM play_history ph
                JOIN tracks_cache tc ON tc.id = ph.track_id
                """
            ).fetchone()
            total_minutes = int(round((int(dur_row["secs"]) if dur_row else 0) / 60.0))

            liked_count = len(liked_ids)
            playlist_count = int(conn.execute("SELECT COUNT(*) AS cnt FROM playlists").fetchone()["cnt"])
            followed_count = int(
                conn.execute("SELECT COUNT(*) AS cnt FROM followed_artists").fetchone()["cnt"]
            )
            saved_albums_count = int(
                conn.execute("SELECT COUNT(*) AS cnt FROM saved_albums").fetchone()["cnt"]
            )

            # Hourly activity & listening streak (UTC calendar days)
            ts_rows = conn.execute(
                "SELECT played_at FROM play_history ORDER BY played_at DESC LIMIT 2000"
            ).fetchall()
            hourly_counts = [0] * 24
            active_days: Set[int] = set()
            for r in ts_rows:
                ts = int(r["played_at"] or 0)
                if ts > 0:
                    tm = time.localtime(ts)
                    hourly_counts[tm.tm_hour] += 1
                    active_days.add(ts // 86400)

            most_active_hour: Optional[int] = None
            if any(hourly_counts):
                most_active_hour = max(range(24), key=lambda h: hourly_counts[h])

            streak_days = 0
            if active_days:
                today_day = int(time.time()) // 86400
                cursor_day = today_day if today_day in active_days else max(active_days)
                if today_day - cursor_day <= 1:
                    while cursor_day in active_days:
                        streak_days += 1
                        cursor_day -= 1

            # Top Tracks
            top_track_rows = conn.execute(
                """
                SELECT tc.*, COUNT(ph.id) AS play_count, MAX(ph.played_at) AS last_played
                FROM play_history ph
                JOIN tracks_cache tc ON tc.id = ph.track_id
                GROUP BY ph.track_id
                ORDER BY play_count DESC, last_played DESC
                LIMIT 10
                """
            ).fetchall()
            top_tracks = [
                {
                    "track": row_to_track(r, liked_ids),
                    "playCount": int(r["play_count"]),
                }
                for r in top_track_rows
            ]

            # Top Artists
            top_artist_rows = conn.execute(
                """
                SELECT tc.artist,
                       MAX(tc.artist_id) AS artist_id,
                       MAX(COALESCE(NULLIF(tc.cover_large, ''), tc.cover_small)) AS cover,
                       COUNT(ph.id) AS play_count
                FROM play_history ph
                JOIN tracks_cache tc ON tc.id = ph.track_id
                WHERE tc.artist != ''
                GROUP BY tc.artist
                ORDER BY play_count DESC
                LIMIT 10
                """
            ).fetchall()
            top_artists = [
                {
                    "artist": r["artist"],
                    "artistId": r["artist_id"] or "",
                    "cover": r["cover"] or "",
                    "playCount": int(r["play_count"]),
                }
                for r in top_artist_rows
            ]

            return {
                "totalPlays": total_plays,
                "totalMinutes": total_minutes,
                "likedCount": liked_count,
                "playlistCount": playlist_count,
                "followedArtistsCount": followed_count,
                "savedAlbumsCount": saved_albums_count,
                "streakDays": streak_days,
                "mostActiveHour": most_active_hour,
                "hourlyCounts": hourly_counts,
                "topTracks": top_tracks,
                "topArtists": top_artists,
            }
        finally:
            conn.close()
