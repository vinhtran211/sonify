"""User library service: Playlists CRUD, Liked Songs, Play History, and Player Settings."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional, Tuple

from backend import offline_catalog, security
from backend.db import Database


class LibraryError(ValueError):
    """Domain validation error mapped to HTTP 400/404."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _sanitize_client_track(track: Dict[str, Any]) -> Dict[str, Any]:
    """Allowlist safe track metadata from client payloads (never trust _extra or previewUrl)."""
    tid = str(track.get("id") or "").strip()[:128]
    try:
        dur = max(0, min(86400, int(track.get("duration") or 0)))
    except Exception:
        dur = 0
    cover_small = security.sanitize_media_url(str(track.get("coverSmall") or ""))
    cover_large = security.sanitize_media_url(
        str(track.get("coverLarge") or track.get("coverSmall") or "")
    )
    return {
        "id": tid,
        "source": str(track.get("source") or tid.split(":", 1)[0])[:32],
        "title": str(track.get("title") or "Untitled").strip()[:240] or "Untitled",
        "artist": str(track.get("artist") or "Unknown Artist").strip()[:200] or "Unknown Artist",
        "artistId": str(track.get("artistId") or "").strip()[:128],
        "album": str(track.get("album") or "").strip()[:200],
        "albumId": str(track.get("albumId") or "").strip()[:128],
        "duration": dur,
        "coverSmall": cover_small,
        "coverLarge": cover_large or cover_small,
        "explicit": bool(track.get("explicit")),
        "releaseDate": str(track.get("releaseDate") or "").strip()[:32],
    }


def _sanitize_client_artist(artist: Dict[str, Any]) -> Dict[str, Any]:
    raw_art = (
        artist.get("pictureLarge")
        or artist.get("pictureSmall")
        or artist.get("picture")
        or artist.get("artworkUrl")
        or ""
    )
    safe_art = security.sanitize_media_url(str(raw_art))
    try:
        fans = max(0, int(artist.get("fans") or 0))
    except Exception:
        fans = 0
    return {
        "id": str(artist.get("id") or "").strip()[:128],
        "name": str(artist.get("name") or "Unknown Artist").strip()[:200] or "Unknown Artist",
        "pictureSmall": safe_art,
        "pictureLarge": safe_art,
        "fans": fans,
    }


def _sanitize_client_album(album: Dict[str, Any]) -> Dict[str, Any]:
    raw_art = (
        album.get("coverLarge")
        or album.get("coverSmall")
        or album.get("artworkUrl")
        or ""
    )
    safe_art = security.sanitize_media_url(str(raw_art))
    try:
        tc = max(0, int(album.get("trackCount") or 0))
    except Exception:
        tc = 0
    return {
        "id": str(album.get("id") or "").strip()[:128],
        "title": str(album.get("title") or "Untitled Album").strip()[:240] or "Untitled Album",
        "artist": str(album.get("artist") or "Unknown Artist").strip()[:200] or "Unknown Artist",
        "artistId": str(album.get("artistId") or "").strip()[:128],
        "coverSmall": safe_art,
        "coverLarge": safe_art,
        "releaseDate": str(album.get("releaseDate") or "").strip()[:32],
        "trackCount": tc,
    }


class LibraryService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _resolve_track_payload(
        self, body: Dict[str, Any], owner_id: str = "default"
    ) -> tuple[str, Optional[Dict[str, Any]]]:
        track = body.get("track")
        if isinstance(track, dict) and track.get("id"):
            sanitized = _sanitize_client_track(track)
            if not sanitized["id"]:
                raise LibraryError("INVALID_TRACK", "Missing 'track.id'", 400)
            return sanitized["id"], sanitized
        track_id = str(body.get("trackId") or "").strip()[:128]
        if not track_id:
            raise LibraryError("INVALID_TRACK", "Missing 'track' object or 'trackId'", 400)
        existing = self.db.get_track(track_id, owner_id=owner_id)
        if not existing and track_id.startswith("local:"):
            existing = offline_catalog.get_offline_track(track_id)
        if not existing:
            raise LibraryError("NOT_FOUND", f"Track '{track_id}' not found in cache", 404)
        return track_id, existing

    # ------------------------------------------------------------------
    # Likes
    # ------------------------------------------------------------------
    def get_likes(self, owner_id: str = "default") -> Dict[str, Any]:
        tracks, liked_ids = self.db.get_liked_tracks(owner_id=owner_id)
        return {"tracks": tracks, "likedIds": liked_ids}

    def like_track(self, body: Dict[str, Any], owner_id: str = "default") -> Dict[str, Any]:
        track_id, track = self._resolve_track_payload(body, owner_id=owner_id)
        self.db.like_track(track_id, track, owner_id=owner_id)
        return {"liked": True, "trackId": track_id}

    def unlike_track(self, track_id: str, owner_id: str = "default") -> Dict[str, Any]:
        if not track_id:
            raise LibraryError("INVALID_TRACK", "Missing trackId", 400)
        self.db.unlike_track(track_id, owner_id=owner_id)
        return {"liked": False, "trackId": track_id}

    # ------------------------------------------------------------------
    # Playlists CRUD
    # ------------------------------------------------------------------
    def list_playlists(self, owner_id: str = "default") -> Dict[str, Any]:
        return {"playlists": self.db.list_playlists(owner_id=owner_id)}

    def create_playlist(self, body: Dict[str, Any], owner_id: str = "default") -> Dict[str, Any]:
        name = str(body.get("name") or "").strip()
        if not name:
            raise LibraryError("INVALID_NAME", "Playlist name is required", 400)
        if len(name) > 120:
            raise LibraryError("INVALID_NAME", "Playlist name must be 120 characters or fewer", 400)
        description = str(body.get("description") or "").strip()[:400]
        playlist = self.db.create_playlist(name, description, owner_id=owner_id)
        return {"playlist": playlist}

    def get_playlist(self, playlist_id: str, owner_id: str = "default") -> Dict[str, Any]:
        data = self.db.get_playlist(playlist_id, owner_id=owner_id)
        if not data:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return data

    def update_playlist(
        self, playlist_id: str, body: Dict[str, Any], owner_id: str = "default"
    ) -> Dict[str, Any]:
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
            c_col = str(cover_color).strip()[:32]
            if c_col and not security.is_valid_hex_color(c_col):
                raise LibraryError(
                    "INVALID_PAYLOAD", "coverColor must be a hex color like #1DB954", 400
                )
            cover_color = c_col
        if cover_emoji is not None:
            cover_emoji = str(cover_emoji).strip()[:16]
        if cover_url is not None:
            c_url = str(cover_url).strip()[:500]
            if c_url:
                safe_url = security.sanitize_media_url(c_url)
                if not safe_url:
                    raise LibraryError(
                        "INVALID_PAYLOAD",
                        "coverUrl must be a valid http(s) or /api/artwork/ URL",
                        400,
                    )
                cover_url = safe_url
            else:
                cover_url = ""
        updated = self.db.update_playlist(
            playlist_id,
            name=name,
            description=description,
            cover_color=cover_color,
            cover_emoji=cover_emoji,
            cover_url=cover_url,
            owner_id=owner_id,
        )
        if not updated:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return {"playlist": updated}

    def duplicate_playlist(
        self, playlist_id: str, body: Optional[Dict[str, Any]] = None, owner_id: str = "default"
    ) -> Dict[str, Any]:
        new_name = None
        if isinstance(body, dict) and body.get("name") is not None:
            new_name = str(body.get("name") or "").strip()
            if not new_name:
                raise LibraryError("INVALID_NAME", "Playlist name cannot be empty", 400)
            if len(new_name) > 120:
                raise LibraryError("INVALID_NAME", "Playlist name must be 120 characters or fewer", 400)
        cloned = self.db.duplicate_playlist(playlist_id, new_name=new_name, owner_id=owner_id)
        if not cloned:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return cloned

    def import_playlist(
        self, body: Dict[str, Any], catalog_service: Any = None, owner_id: str = "default"
    ) -> Dict[str, Any]:
        if not isinstance(body, dict):
            raise LibraryError("INVALID_PAYLOAD", "Import payload must be a JSON object", 400)
        name = str(body.get("name") or "").strip()
        if not name:
            raise LibraryError("INVALID_NAME", "Playlist name is required", 400)
        if len(name) > 120:
            raise LibraryError("INVALID_NAME", "Playlist name must be 120 characters or fewer", 400)
        description = str(body.get("description") or "").strip()[:400]

        playlist = self.db.create_playlist(name, description, owner_id=owner_id)
        pid = playlist["id"]
        imported = 0
        live_lookups = 0
        max_live_lookups = 20
        deadline = time.monotonic() + 10.0

        raw_tracks = body.get("tracks")
        if isinstance(raw_tracks, list) and raw_tracks:
            for item in raw_tracks[:200]:
                if not isinstance(item, dict):
                    continue
                tid = str(item.get("id") or "").strip()[:128]
                title = str(item.get("title") or "").strip()[:240]
                artist = str(item.get("artist") or "Unknown Artist").strip()[:200]
                if tid and title:
                    sanitized = _sanitize_client_track(item)
                    self.db.add_track_to_playlist(pid, sanitized["id"], sanitized, owner_id=owner_id)
                    imported += 1
                elif (
                    title
                    and catalog_service is not None
                    and live_lookups < max_live_lookups
                    and time.monotonic() < deadline
                ):
                    live_lookups += 1
                    q = f"{artist} {title}" if artist and artist != "Unknown Artist" else title
                    try:
                        res = catalog_service.search(q, search_type="tracks", limit=1)
                        found = (res.get("tracks") or [])[:1]
                    except Exception:
                        found = []
                    if found:
                        self.db.add_track_to_playlist(pid, found[0]["id"], found[0], owner_id=owner_id)
                        imported += 1

        m3u_text = str(body.get("m3uText") or "")[:65_536].strip()
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
                        queries.append((art.strip()[:200], ttl.strip()[:240]))
                    elif meta:
                        queries.append(("", meta[:240]))
            for art, ttl in queries[:50]:
                if not ttl:
                    continue
                matched = None
                if (
                    catalog_service is not None
                    and live_lookups < max_live_lookups
                    and time.monotonic() < deadline
                ):
                    live_lookups += 1
                    q = f"{art} {ttl}".strip()
                    try:
                        res = catalog_service.search(q, search_type="tracks", limit=1)
                        tracks = res.get("tracks") or []
                        if tracks:
                            matched = tracks[0]
                    except Exception:
                        matched = None
                if matched:
                    self.db.add_track_to_playlist(pid, matched["id"], matched, owner_id=owner_id)
                    imported += 1
                else:
                    # Deterministic synthetic entry if offline, unindexed, or lookup cap reached
                    digest = hashlib.sha256(
                        f"{art.lower()}\0{ttl.lower()}".encode("utf-8")
                    ).hexdigest()[:12]
                    local_id = f"imp:{digest}"
                    fallback_track = {
                        "id": local_id,
                        "source": "import",
                        "title": ttl,
                        "artist": art or "Unknown Artist",
                        "duration": 0,
                    }
                    self.db.add_track_to_playlist(pid, local_id, fallback_track, owner_id=owner_id)
                    imported += 1

        updated = self.db.get_playlist(pid, owner_id=owner_id)
        return {
            "playlist": updated["playlist"] if updated else playlist,
            "importedCount": imported,
        }

    def delete_playlist(self, playlist_id: str, owner_id: str = "default") -> Dict[str, Any]:
        ok = self.db.delete_playlist(playlist_id, owner_id=owner_id)
        if not ok:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return {"deleted": True, "id": playlist_id}

    def add_track_to_playlist(
        self, playlist_id: str, body: Dict[str, Any], owner_id: str = "default"
    ) -> Dict[str, Any]:
        track_id, track = self._resolve_track_payload(body, owner_id=owner_id)
        result = self.db.add_track_to_playlist(playlist_id, track_id, track, owner_id=owner_id)
        if result is None:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return result

    def remove_track_from_playlist(
        self, playlist_id: str, track_id: str, owner_id: str = "default"
    ) -> Dict[str, Any]:
        result = self.db.remove_track_from_playlist(playlist_id, track_id, owner_id=owner_id)
        if result is None:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return result

    def reorder_playlist_tracks(
        self, playlist_id: str, body: Dict[str, Any], owner_id: str = "default"
    ) -> Dict[str, Any]:
        track_ids = body.get("trackIds")
        if not isinstance(track_ids, list):
            raise LibraryError("INVALID_PAYLOAD", "'trackIds' must be a list", 400)
        ok = self.db.reorder_playlist_tracks(
            playlist_id, [str(x) for x in track_ids], owner_id=owner_id
        )
        if ok is None:
            raise LibraryError("NOT_FOUND", f"Playlist '{playlist_id}' not found", 404)
        return {"reordered": True}

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    def get_history(self, limit: int = 50, owner_id: str = "default") -> Dict[str, Any]:
        return {"items": self.db.get_history(limit=limit, owner_id=owner_id)}

    def record_history(self, body: Dict[str, Any], owner_id: str = "default") -> Dict[str, Any]:
        track_id, track = self._resolve_track_payload(body, owner_id=owner_id)
        self.db.record_history(track_id, track, owner_id=owner_id)
        return {"recorded": True, "trackId": track_id}

    def clear_history(self, owner_id: str = "default") -> Dict[str, Any]:
        self.db.clear_history(owner_id=owner_id)
        return {"cleared": True}

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def get_settings(self, owner_id: str = "default") -> Dict[str, Any]:
        return self.db.get_settings(owner_id=owner_id)

    def update_settings(self, body: Dict[str, Any], owner_id: str = "default") -> Dict[str, Any]:
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
            cleaned["lastTrackId"] = str(body["lastTrackId"])[:128] if body["lastTrackId"] else None
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
            if qual not in ("auto", "compact", "lossless", "fast"):
                raise LibraryError(
                    "INVALID_SETTING",
                    "audioQuality must be 'auto', 'compact', 'lossless', or 'fast'",
                    400,
                )
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
        return self.db.update_settings(cleaned, owner_id=owner_id)

    # ------------------------------------------------------------------
    # Followed Artists, Saved Albums, Pins & Stats
    # ------------------------------------------------------------------
    def get_followed_artists(self, owner_id: str = "default") -> Dict[str, Any]:
        artists, ids = self.db.get_followed_artists(owner_id=owner_id)
        return {"artists": artists, "artistIds": ids}

    def follow_artist(self, body: Dict[str, Any], owner_id: str = "default") -> Dict[str, Any]:
        artist = body.get("artist") if isinstance(body, dict) else None
        if not isinstance(artist, dict) or not str(artist.get("id") or "").strip():
            raise LibraryError("INVALID_ARTIST", "Missing 'artist' object with 'id'", 400)
        sanitized = _sanitize_client_artist(artist)
        aid = self.db.follow_artist(sanitized, owner_id=owner_id)
        return {"followed": True, "artistId": aid}

    def unfollow_artist(self, artist_id: str, owner_id: str = "default") -> Dict[str, Any]:
        if not artist_id:
            raise LibraryError("INVALID_ARTIST", "Missing artistId", 400)
        self.db.unfollow_artist(artist_id, owner_id=owner_id)
        return {"followed": False, "artistId": artist_id}

    def get_saved_albums(self, owner_id: str = "default") -> Dict[str, Any]:
        albums, ids = self.db.get_saved_albums(owner_id=owner_id)
        return {"albums": albums, "albumIds": ids}

    def save_album(self, body: Dict[str, Any], owner_id: str = "default") -> Dict[str, Any]:
        album = body.get("album") if isinstance(body, dict) else None
        if not isinstance(album, dict) or not str(album.get("id") or "").strip():
            raise LibraryError("INVALID_ALBUM", "Missing 'album' object with 'id'", 400)
        sanitized = _sanitize_client_album(album)
        albid = self.db.save_album(sanitized, owner_id=owner_id)
        return {"saved": True, "albumId": albid}

    def unsave_album(self, album_id: str, owner_id: str = "default") -> Dict[str, Any]:
        if not album_id:
            raise LibraryError("INVALID_ALBUM", "Missing albumId", 400)
        self.db.unsave_album(album_id, owner_id=owner_id)
        return {"saved": False, "albumId": album_id}

    def get_pins(self, owner_id: str = "default") -> Dict[str, Any]:
        return {"pins": self.db.get_pins(owner_id=owner_id)}

    def pin_item(self, body: Dict[str, Any], owner_id: str = "default") -> Dict[str, Any]:
        if not isinstance(body, dict):
            raise LibraryError("INVALID_PAYLOAD", "Pin payload must be a JSON object", 400)
        item_type = str(body.get("itemType") or "").strip().lower()
        item_id = str(body.get("itemId") or "").strip()[:128]
        if item_type not in ("playlist", "artist", "album", "liked"):
            raise LibraryError("INVALID_PIN", "itemType must be playlist, artist, album, or liked", 400)
        if not item_id:
            raise LibraryError("INVALID_PIN", "itemId is required", 400)
        self.db.pin_item(item_type, item_id, owner_id=owner_id)
        return {"pinned": True, "itemType": item_type, "itemId": item_id}

    def unpin_item(
        self, item_type: str, item_id: str, owner_id: str = "default"
    ) -> Dict[str, Any]:
        it = (item_type or "").strip().lower()
        if it not in ("playlist", "artist", "album", "liked") or not item_id:
            raise LibraryError("INVALID_PIN", "Valid itemType and itemId are required", 400)
        self.db.unpin_item(it, item_id, owner_id=owner_id)
        return {"pinned": False, "itemType": it, "itemId": item_id}

    def get_stats(self, owner_id: str = "default") -> Dict[str, Any]:
        return self.db.get_listening_stats(owner_id=owner_id)
