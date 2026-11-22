"""Deezer Public API provider (global charts, new releases, search, artist, album, track)."""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from backend import config, http_client

BASE_URL = "https://api.deezer.com"


def _normalize_track(raw: Dict[str, Any], album_override: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    tid = raw.get("id")
    if not tid:
        return None
    artist_obj = raw.get("artist") or {}
    album_obj = raw.get("album") or album_override or {}
    cover_small = (
        album_obj.get("cover_medium")
        or album_obj.get("cover_small")
        or album_obj.get("cover")
        or ""
    )
    cover_large = (
        album_obj.get("cover_xl")
        or album_obj.get("cover_big")
        or cover_small
    )
    artist_id = artist_obj.get("id")
    album_id = album_obj.get("id")
    return {
        "id": f"dz:{tid}",
        "source": "deezer",
        "title": str(raw.get("title") or raw.get("title_short") or "Untitled"),
        "artist": str(artist_obj.get("name") or "Unknown Artist"),
        "artistId": f"dz:{artist_id}" if artist_id else "",
        "album": str(album_obj.get("title") or ""),
        "albumId": f"dz:{album_id}" if album_id else "",
        "duration": int(raw.get("duration") or 0),
        "coverSmall": cover_small,
        "coverLarge": cover_large,
        "explicit": bool(raw.get("explicit_lyrics")),
        "releaseDate": str(raw.get("release_date") or album_obj.get("release_date") or ""),
        "previewUrl": str(raw.get("preview") or ""),
        "streamUrl": f"/api/stream/dz:{tid}",
    }


def _normalize_artist(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    aid = raw.get("id")
    if not aid:
        return None
    pic_small = raw.get("picture_medium") or raw.get("picture_small") or raw.get("picture") or ""
    pic_large = raw.get("picture_xl") or raw.get("picture_big") or pic_small
    return {
        "id": f"dz:{aid}",
        "source": "deezer",
        "name": str(raw.get("name") or "Unknown Artist"),
        "pictureSmall": pic_small,
        "pictureLarge": pic_large,
        "fans": int(raw.get("nb_fan") or 0),
    }


def _normalize_album(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    aid = raw.get("id")
    if not aid:
        return None
    artist_obj = raw.get("artist") or {}
    cover_small = raw.get("cover_medium") or raw.get("cover_small") or raw.get("cover") or ""
    cover_large = raw.get("cover_xl") or raw.get("cover_big") or cover_small
    artist_id = artist_obj.get("id")
    return {
        "id": f"dz:{aid}",
        "source": "deezer",
        "title": str(raw.get("title") or "Untitled Album"),
        "artist": str(artist_obj.get("name") or "Various Artists"),
        "artistId": f"dz:{artist_id}" if artist_id else "",
        "coverSmall": cover_small,
        "coverLarge": cover_large,
        "releaseDate": str(raw.get("release_date") or ""),
        "trackCount": int(raw.get("nb_tracks") or 0),
    }


def get_chart(limit: int = 20) -> Dict[str, List[Dict[str, Any]]]:
    data = http_client.fetch_json(f"{BASE_URL}/chart?limit={limit}", timeout=config.PROVIDER_TIMEOUT)
    tracks = [
        t for t in (_normalize_track(x) for x in (data.get("tracks", {}).get("data") or [])) if t
    ]
    albums = [
        a for a in (_normalize_album(x) for x in (data.get("albums", {}).get("data") or [])) if a
    ]
    artists = [
        ar for ar in (_normalize_artist(x) for x in (data.get("artists", {}).get("data") or [])) if ar
    ]
    return {"tracks": tracks, "albums": albums, "artists": artists}


def get_new_releases(limit: int = 16) -> List[Dict[str, Any]]:
    data = http_client.fetch_json(
        f"{BASE_URL}/editorial/0/releases?limit={limit}", timeout=config.PROVIDER_TIMEOUT
    )
    return [a for a in (_normalize_album(x) for x in (data.get("data") or [])) if a]


def search_tracks(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    q = urllib.parse.quote(query.strip())
    data = http_client.fetch_json(
        f"{BASE_URL}/search/track?q={q}&limit={limit}", timeout=config.PROVIDER_TIMEOUT
    )
    return [t for t in (_normalize_track(x) for x in (data.get("data") or [])) if t]


def search_artists(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    q = urllib.parse.quote(query.strip())
    data = http_client.fetch_json(
        f"{BASE_URL}/search/artist?q={q}&limit={limit}", timeout=config.PROVIDER_TIMEOUT
    )
    return [a for a in (_normalize_artist(x) for x in (data.get("data") or [])) if a]


def search_albums(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    q = urllib.parse.quote(query.strip())
    data = http_client.fetch_json(
        f"{BASE_URL}/search/album?q={q}&limit={limit}", timeout=config.PROVIDER_TIMEOUT
    )
    return [a for a in (_normalize_album(x) for x in (data.get("data") or [])) if a]


def get_track(raw_id: str) -> Optional[Dict[str, Any]]:
    data = http_client.fetch_json(f"{BASE_URL}/track/{raw_id}", timeout=config.PROVIDER_TIMEOUT)
    if not isinstance(data, dict) or data.get("error"):
        return None
    return _normalize_track(data)


def get_artist(raw_id: str) -> Optional[Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]]:
    info = http_client.fetch_json(f"{BASE_URL}/artist/{raw_id}", timeout=config.PROVIDER_TIMEOUT)
    if not isinstance(info, dict) or info.get("error"):
        return None
    artist = _normalize_artist(info)
    if not artist:
        return None

    top_tracks: List[Dict[str, Any]] = []
    albums: List[Dict[str, Any]] = []
    try:
        top_data = http_client.fetch_json(
            f"{BASE_URL}/artist/{raw_id}/top?limit=25", timeout=config.PROVIDER_TIMEOUT
        )
        top_tracks = [
            t for t in (_normalize_track(x) for x in (top_data.get("data") or [])) if t
        ]
    except Exception:
        pass
    try:
        alb_data = http_client.fetch_json(
            f"{BASE_URL}/artist/{raw_id}/albums?limit=20", timeout=config.PROVIDER_TIMEOUT
        )
        for item in alb_data.get("data") or []:
            item.setdefault("artist", {"id": info.get("id"), "name": info.get("name")})
            norm = _normalize_album(item)
            if norm:
                albums.append(norm)
    except Exception:
        pass

    # If Deezer /top returns empty for an artist that has albums, populate from their top album
    if not top_tracks and albums:
        try:
            first_alb_raw_id = albums[0]["id"].split(":", 1)[-1]
            alb_res = get_album(first_alb_raw_id)
            if alb_res and alb_res[1]:
                top_tracks = alb_res[1][:20]
        except Exception:
            pass
    return artist, top_tracks, albums


def get_album(raw_id: str) -> Optional[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    info = http_client.fetch_json(f"{BASE_URL}/album/{raw_id}", timeout=config.PROVIDER_TIMEOUT)
    if not isinstance(info, dict) or info.get("error"):
        return None
    album = _normalize_album(info)
    if not album:
        return None
    album_meta = {
        "id": info.get("id"),
        "title": info.get("title"),
        "cover_small": info.get("cover_small"),
        "cover_medium": info.get("cover_medium"),
        "cover_big": info.get("cover_big"),
        "cover_xl": info.get("cover_xl"),
        "release_date": info.get("release_date"),
    }
    tracks = [
        t
        for t in (
            _normalize_track(x, album_override=album_meta)
            for x in (info.get("tracks", {}).get("data") or [])
        )
        if t
    ]
    album["totalDuration"] = int(info.get("duration") or sum(t["duration"] for t in tracks))
    return album, tracks
