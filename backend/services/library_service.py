"""User library service: Playlists CRUD, Liked Songs, Play History, and Player Settings."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from backend import offline_catalog
from backend.db import Database


class LibraryError(ValueError):
    """Domain validation error mapped to HTTP 400/404."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class LibraryService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _resolve_track_payload(self, body: Dict[str, Any]) -> tuple[str, Optional[Dict[str, Any]]]:
        track = body.get("track")
        if isinstance(track, dict) and track.get("id"):
            return str(track["id"]), track
        track_id = str(body.get("trackId") or "").strip()
        if not track_id:
            raise LibraryError("INVALID_TRACK", "Missing 'track' object or 'trackId'", 400)
        existing = self.db.get_track(track_id)
        if not existing and track_id.startswith("local:"):
            existing = offline_catalog.get_offline_track(track_id)
        if not existing:
            raise LibraryError("NOT_FOUND", f"Track '{track_id}' not found in cache", 404)
        return track_id, existing

    # ------------------------------------------------------------------
    # Likes
    # ------------------------------------------------------------------
    def get_likes(self) -> Dict[str, Any]:
        tracks, liked_ids = self.db.get_liked_tracks()
        return {"tracks": tracks, "likedIds": liked_ids}

    def like_track(self, body: Dict[str, Any]) -> Dict[str, Any]:
        track_id, track = self._resolve_track_payload(body)
        self.db.like_track(track_id, track)
        return {"liked": True, "trackId": track_id}

    def unlike_track(self, track_id: str) -> Dict[str, Any]:
        if not track_id:
            raise LibraryError("INVALID_TRACK", "Missing trackId", 400)
        self.db.unlike_track(track_id)
        return {"liked": False, "trackId": track_id}

    # ------------------------------------------------------------------
    # Playlists CRUD
    # ------------------------------------------------------------------
    def list_playlists(self) -> Dict[str, Any]:
        return {"playlists": self.db.list_playlists()}

    def create_playlist(self, body: Dict[str, Any]) -> Dict[str, Any]:
        name = str(body.get("name") or "").strip()
        if not name:
            raise LibraryError("INVALID_NAME", "Playlist name is required", 400)
        if len(name) > 120:
            raise LibraryError("INVALID_NAME", "Playlist name must be 120 characters or fewer", 400)
        description = str(body.get("description") or "").strip()[:400]
        playlist = self.db.create_playlist(name, description)
        return {"playlist": playlist}

    def get_playlist(self, playlist_id: str) -> Dict[str, Any]:
        data = self.db.get_playlist(playlist_id)
        if not data:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return data

    def update_playlist(self, playlist_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        name = body.get("name")
        description = body.get("description")
        cover_color = body.get("coverColor")
        cover_emoji = body.get("coverEmoji")
        cover_url = body.get("coverUrl")
        if name is not None:
            name = str(name).strip()
            if not name:
                raise LibraryError("INVALID_NAME", "Playlist name cannot be empty", 400)
            if len(name) > 120:
                raise LibraryError("INVALID_NAME", "Playlist name must be 120 characters or fewer", 400)
        if description is not None:
            description = str(description).strip()[:400]
        if cover_color is not None:
            cover_color = str(cover_color).strip()[:32]
        if cover_emoji is not None:
            cover_emoji = str(cover_emoji).strip()[:16]
        if cover_url is not None:
            c_url = str(cover_url).strip()[:500]
            if c_url and not c_url.lower().startswith(("https://", "http://", "/")):
                raise LibraryError("INVALID_PAYLOAD", "coverUrl must be an http(s) or relative URL", 400)
            cover_url = c_url
        updated = self.db.update_playlist(
            playlist_id,
            name=name,
            description=description,
            cover_color=cover_color,
            cover_emoji=cover_emoji,
            cover_url=cover_url,
        )
        if not updated:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return {"playlist": updated}

    def duplicate_playlist(self, playlist_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        new_name = None
        if isinstance(body, dict) and body.get("name") is not None:
            new_name = str(body.get("name") or "").strip()
            if not new_name:
                raise LibraryError("INVALID_NAME", "Playlist name cannot be empty", 400)
            if len(new_name) > 120:
                raise LibraryError("INVALID_NAME", "Playlist name must be 120 characters or fewer", 400)
        cloned = self.db.duplicate_playlist(playlist_id, new_name=new_name)
        if not cloned:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return cloned

    def import_playlist(
        self, body: Dict[str, Any], catalog_service: Any = None
    ) -> Dict[str, Any]:
        if not isinstance(body, dict):
            raise LibraryError("INVALID_PAYLOAD", "Import payload must be a JSON object", 400)
        name = str(body.get("name") or "").strip()
        if not name:
            raise LibraryError("INVALID_NAME", "Playlist name is required", 400)
        if len(name) > 120:
            raise LibraryError("INVALID_NAME", "Playlist name must be 120 characters or fewer", 400)
        description = str(body.get("description") or "").strip()[:400]

        playlist = self.db.create_playlist(name, description)
        pid = playlist["id"]
        imported = 0

        raw_tracks = body.get("tracks")
        if isinstance(raw_tracks, list) and raw_tracks:
            for item in raw_tracks[:200]:
                if not isinstance(item, dict):
                    continue
                tid = str(item.get("id") or "").strip()
                title = str(item.get("title") or "").strip()
                artist = str(item.get("artist") or "Unknown Artist").strip()
                if tid and title:
                    self.db.add_track_to_playlist(pid, tid, item)
                    imported += 1
                elif title and catalog_service is not None:
                    q = f"{artist} {title}" if artist and artist != "Unknown Artist" else title
                    res = catalog_service.search(q, search_type="tracks", limit=1)
                    found = (res.get("tracks") or [])[:1]
                    if found:
                        self.db.add_track_to_playlist(pid, found[0]["id"], found[0])
                        imported += 1

        m3u_text = str(body.get("m3uText") or "").strip()
        if m3u_text and imported == 0:
            queries: List[Tuple[str, str]] = []
            for raw_line in m3u_text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.upper().startswith("#EXTINF:"):
                    meta = line.split(",", 1)[1].strip() if "," in line else ""
                    if " - " in meta:
                        art, ttl = meta.split(" - ", 1)
                        queries.append((art.strip(), ttl.strip()))
                    elif meta:
                        queries.append(("", meta))
            for art, ttl in queries[:50]:
                if not ttl:
                    continue
                matched = None
                if catalog_service is not None:
                    q = f"{art} {ttl}".strip()
                    try:
                        res = catalog_service.search(q, search_type="tracks", limit=1)
                        tracks = res.get("tracks") or []
                        if tracks:
                            matched = tracks[0]
                    except Exception:
                        matched = None
                if matched:
                    self.db.add_track_to_playlist(pid, matched["id"], matched)
                    imported += 1
                else:
                    # Fallback cached/synthetic entry if offline or unindexed
                    local_id = f"imp:{abs(hash((art.lower(), ttl.lower()))) % 10_000_000}"
                    fallback_track = {
                        "id": local_id,
                        "source": "import",
                        "title": ttl,
                        "artist": art or "Unknown Artist",
                        "duration": 0,
                    }
                    self.db.add_track_to_playlist(pid, local_id, fallback_track)
                    imported += 1

        updated = self.db.get_playlist(pid)
        return {
            "playlist": updated["playlist"] if updated else playlist,
            "importedCount": imported,
        }

    def delete_playlist(self, playlist_id: str) -> Dict[str, Any]:
        ok = self.db.delete_playlist(playlist_id)
        if not ok:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return {"deleted": True, "id": playlist_id}

    def add_track_to_playlist(self, playlist_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        track_id, track = self._resolve_track_payload(body)
        result = self.db.add_track_to_playlist(playlist_id, track_id, track)
        if result is None:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return result

    def remove_track_from_playlist(self, playlist_id: str, track_id: str) -> Dict[str, Any]:
        result = self.db.remove_track_from_playlist(playlist_id, track_id)
        if result is None:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return result

    def reorder_playlist_tracks(self, playlist_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        track_ids = body.get("trackIds")
        if not isinstance(track_ids, list):
            raise LibraryError("INVALID_PAYLOAD", "'trackIds' must be a list", 400)
        ok = self.db.reorder_playlist_tracks(playlist_id, [str(x) for x in track_ids])
        if ok is None:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return {"reordered": True}

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    def get_history(self, limit: int = 50) -> Dict[str, Any]:
        return {"items": self.db.get_history(limit=limit)}

    def record_history(self, body: Dict[str, Any]) -> Dict[str, Any]:
        track_id, track = self._resolve_track_payload(body)
        self.db.record_history(track_id, track)
        return {"recorded": True, "trackId": track_id}

    def clear_history(self) -> Dict[str, Any]:
        self.db.clear_history()
        return {"cleared": True}

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def get_settings(self) -> Dict[str, Any]:
        return self.db.get_settings()

    def update_settings(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(body, dict):
            raise LibraryError("INVALID_PAYLOAD", "Settings payload must be a JSON object", 400)
        cleaned: Dict[str, Any] = {}
        if "volume" in body:
            try:
                cleaned["volume"] = max(0.0, min(1.0, float(body["volume"])))
            except Exception:
                raise LibraryError("INVALID_SETTING", "volume must be a number in [0, 1]", 400)
        if "muted" in body:
            cleaned["muted"] = bool(body["muted"])
        if "shuffle" in body:
            cleaned["shuffle"] = bool(body["shuffle"])
        if "repeat" in body:
            rep = str(body["repeat"]).lower()
            if rep not in ("off", "all", "one"):
                raise LibraryError("INVALID_SETTING", "repeat must be 'off', 'all', or 'one'", 400)
            cleaned["repeat"] = rep
        if "region" in body:
            reg = str(body["region"]).lower()
            if reg not in ("global", "vn"):
                raise LibraryError("INVALID_SETTING", "region must be 'global' or 'vn'", 400)
            cleaned["region"] = reg
        if "lastTrackId" in body:
            cleaned["lastTrackId"] = str(body["lastTrackId"]) if body["lastTrackId"] else None
        if "lastPosition" in body:
            try:
                cleaned["lastPosition"] = max(0.0, float(body["lastPosition"]))
            except Exception:
                cleaned["lastPosition"] = 0.0
        if "autoplay" in body:
            cleaned["autoplay"] = bool(body["autoplay"])
        if "crossfade" in body:
            try:
                cleaned["crossfade"] = max(0, min(12, int(round(float(body["crossfade"])))))
            except Exception:
                raise LibraryError("INVALID_SETTING", "crossfade must be an integer in [0, 12]", 400)
        if "playbackRate" in body:
            try:
                cleaned["playbackRate"] = round(max(0.5, min(2.0, float(body["playbackRate"]))), 2)
            except Exception:
                raise LibraryError("INVALID_SETTING", "playbackRate must be a number in [0.5, 2.0]", 400)
        if "audioQuality" in body:
            qual = str(body["audioQuality"]).strip().lower()
            if qual not in ("auto", "lossless", "fast"):
                raise LibraryError("INVALID_SETTING", "audioQuality must be 'auto', 'lossless', or 'fast'", 400)
            cleaned["audioQuality"] = qual
        if "eqPreset" in body:
            preset = str(body["eqPreset"] or "flat").strip().lower()[:40]
            cleaned["eqPreset"] = preset or "flat"
        if "eqBands" in body:
            raw_bands = body["eqBands"]
            if not isinstance(raw_bands, list) or len(raw_bands) != 5:
                raise LibraryError("INVALID_SETTING", "eqBands must be a list of 5 numbers in [-12, 12]", 400)
            try:
                cleaned["eqBands"] = [round(max(-12.0, min(12.0, float(v))), 1) for v in raw_bands]
            except Exception:
                raise LibraryError("INVALID_SETTING", "eqBands values must be numeric", 400)
        return self.db.update_settings(cleaned)

    # ------------------------------------------------------------------
    # Followed Artists, Saved Albums, Pins & Stats
    # ------------------------------------------------------------------
    def get_followed_artists(self) -> Dict[str, Any]:
        artists, ids = self.db.get_followed_artists()
        return {"artists": artists, "artistIds": ids}

    def follow_artist(self, body: Dict[str, Any]) -> Dict[str, Any]:
        artist = body.get("artist") if isinstance(body, dict) else None
        if not isinstance(artist, dict) or not str(artist.get("id") or "").strip():
            raise LibraryError("INVALID_ARTIST", "Missing 'artist' object with 'id'", 400)
        aid = self.db.follow_artist(artist)
        return {"followed": True, "artistId": aid}

    def unfollow_artist(self, artist_id: str) -> Dict[str, Any]:
        if not artist_id:
            raise LibraryError("INVALID_ARTIST", "Missing artistId", 400)
        self.db.unfollow_artist(artist_id)
        return {"followed": False, "artistId": artist_id}

    def get_saved_albums(self) -> Dict[str, Any]:
        albums, ids = self.db.get_saved_albums()
        return {"albums": albums, "albumIds": ids}

    def save_album(self, body: Dict[str, Any]) -> Dict[str, Any]:
        album = body.get("album") if isinstance(body, dict) else None
        if not isinstance(album, dict) or not str(album.get("id") or "").strip():
            raise LibraryError("INVALID_ALBUM", "Missing 'album' object with 'id'", 400)
        albid = self.db.save_album(album)
        return {"saved": True, "albumId": albid}

    def unsave_album(self, album_id: str) -> Dict[str, Any]:
        if not album_id:
            raise LibraryError("INVALID_ALBUM", "Missing albumId", 400)
        self.db.unsave_album(album_id)
        return {"saved": False, "albumId": album_id}

    def get_pins(self) -> Dict[str, Any]:
        return {"pins": self.db.get_pins()}

    def pin_item(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(body, dict):
            raise LibraryError("INVALID_PAYLOAD", "Pin payload must be a JSON object", 400)
        item_type = str(body.get("itemType") or "").strip().lower()
        item_id = str(body.get("itemId") or "").strip()
        if item_type not in ("playlist", "artist", "album", "liked"):
            raise LibraryError("INVALID_PIN", "itemType must be playlist, artist, album, or liked", 400)
        if not item_id:
            raise LibraryError("INVALID_PIN", "itemId is required", 400)
        self.db.pin_item(item_type, item_id)
        return {"pinned": True, "itemType": item_type, "itemId": item_id}

    def unpin_item(self, item_type: str, item_id: str) -> Dict[str, Any]:
        it = (item_type or "").strip().lower()
        if it not in ("playlist", "artist", "album", "liked") or not item_id:
            raise LibraryError("INVALID_PIN", "Valid itemType and itemId are required", 400)
        self.db.unpin_item(it, item_id)
        return {"pinned": False, "itemType": it, "itemId": item_id}

    def get_stats(self) -> Dict[str, Any]:
        return self.db.get_listening_stats()
