"""User library service: Playlists CRUD, Liked Songs, Play History, and Player Settings."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
        if name is not None:
            name = str(name).strip()
            if not name:
                raise LibraryError("INVALID_NAME", "Playlist name cannot be empty", 400)
            if len(name) > 120:
                raise LibraryError("INVALID_NAME", "Playlist name must be 120 characters or fewer", 400)
        if description is not None:
            description = str(description).strip()[:400]
        updated = self.db.update_playlist(playlist_id, name=name, description=description)
        if not updated:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return {"playlist": updated}

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
        return self.db.update_settings(cleaned)
