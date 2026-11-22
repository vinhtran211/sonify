"""Apple Music RSS Charts & iTunes Search/Lookup provider (Global + Vietnam/V-Pop)."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from backend import config, http_client

APPLE_RSS_BASE = "https://rss.marketingtools.apple.com/api/v2"
ITUNES_BASE = "https://itunes.apple.com"


def _upscale_artwork(url: str, size: int) -> str:
    if not url:
        return ""
    return re.sub(r"/\d+x\d+bb\.", f"/{size}x{size}bb.", url)


def _normalize_itunes_song(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tid = raw.get("trackId")
    if not tid:
        return None
    art = str(raw.get("artworkUrl100") or raw.get("artworkUrl60") or "")
    artist_id = raw.get("artistId")
    album_id = raw.get("collectionId")
    duration_ms = int(raw.get("trackTimeMillis") or 0)
    release = str(raw.get("releaseDate") or "")[:10]
    explicit = str(raw.get("trackExplicitness") or "").lower() == "explicit"
    return {
        "id": f"it:{tid}",
        "source": "itunes",
        "title": str(raw.get("trackName") or "Untitled"),
        "artist": str(raw.get("artistName") or "Unknown Artist"),
        "artistId": f"it:{artist_id}" if artist_id else "",
        "album": str(raw.get("collectionName") or ""),
        "albumId": f"it:{album_id}" if album_id else "",
        "duration": duration_ms // 1000 if duration_ms > 0 else 0,
        "coverSmall": _upscale_artwork(art, 300),
        "coverLarge": _upscale_artwork(art, 600),
        "explicit": explicit,
        "releaseDate": release,
        "previewUrl": str(raw.get("previewUrl") or ""),
        "streamUrl": f"/api/stream/it:{tid}",
    }


def _normalize_rss_song(raw: Dict[str, Any], enriched: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if enriched:
        return _normalize_itunes_song(enriched)
    tid = raw.get("id")
    if not tid:
        return None
    art = str(raw.get("artworkUrl100") or "")
    artist_id = raw.get("artistId")
    return {
        "id": f"it:{tid}",
        "source": "itunes",
        "title": str(raw.get("name") or "Untitled"),
        "artist": str(raw.get("artistName") or "Unknown Artist"),
        "artistId": f"it:{artist_id}" if artist_id else "",
        "album": "",
        "albumId": "",
        "duration": 0,
        "coverSmall": _upscale_artwork(art, 300),
        "coverLarge": _upscale_artwork(art, 600),
        "explicit": str(raw.get("contentAdvisoryRating") or "").lower() == "explicit",
        "releaseDate": str(raw.get("releaseDate") or "")[:10],
        "previewUrl": "",
        "streamUrl": f"/api/stream/it:{tid}",
    }


def _normalize_rss_album(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    aid = raw.get("id")
    if not aid:
        return None
    art = str(raw.get("artworkUrl100") or "")
    artist_id = raw.get("artistId")
    return {
        "id": f"it:{aid}",
        "source": "itunes",
        "title": str(raw.get("name") or "Untitled Album"),
        "artist": str(raw.get("artistName") or "Various Artists"),
        "artistId": f"it:{artist_id}" if artist_id else "",
        "coverSmall": _upscale_artwork(art, 300),
        "coverLarge": _upscale_artwork(art, 600),
        "releaseDate": str(raw.get("releaseDate") or "")[:10],
        "trackCount": 0,
    }


def get_top_songs(country: str = "vn", limit: int = 20) -> List[Dict[str, Any]]:
    url = f"{APPLE_RSS_BASE}/{country}/music/most-played/{limit}/songs.json"
    data = http_client.fetch_json(url, timeout=config.PROVIDER_TIMEOUT)
    results = data.get("feed", {}).get("results") or []
    ids = [str(item["id"]) for item in results if item.get("id")]

    enriched_map: Dict[str, Dict[str, Any]] = {}
    if ids:
        try:
            lookup_url = f"{ITUNES_BASE}/lookup?id={','.join(ids[:25])}&country={country}"
            lookup = http_client.fetch_json(lookup_url, timeout=config.PROVIDER_TIMEOUT)
            for item in lookup.get("results") or []:
                if item.get("wrapperType") == "track" and item.get("trackId"):
                    enriched_map[str(item["trackId"])] = item
        except Exception:
            pass

    tracks: List[Dict[str, Any]] = []
    for item in results:
        sid = str(item.get("id") or "")
        norm = _normalize_rss_song(item, enriched_map.get(sid))
        if norm:
            tracks.append(norm)
    return tracks


def get_top_albums(country: str = "us", limit: int = 16) -> List[Dict[str, Any]]:
    url = f"{APPLE_RSS_BASE}/{country}/music/most-played/{limit}/albums.json"
    data = http_client.fetch_json(url, timeout=config.PROVIDER_TIMEOUT)
    results = data.get("feed", {}).get("results") or []
    return [a for a in (_normalize_rss_album(x) for x in results) if a]


def search_songs(query: str, country: str = "vn", limit: int = 12) -> List[Dict[str, Any]]:
    params = urllib.parse.urlencode(
        {"term": query.strip(), "media": "music", "entity": "song", "country": country, "limit": limit}
    )
    data = http_client.fetch_json(f"{ITUNES_BASE}/search?{params}", timeout=config.PROVIDER_TIMEOUT)
    return [t for t in (_normalize_itunes_song(x) for x in (data.get("results") or [])) if t]


def get_track(raw_id: str) -> Optional[Dict[str, Any]]:
    data = http_client.fetch_json(f"{ITUNES_BASE}/lookup?id={raw_id}", timeout=config.PROVIDER_TIMEOUT)
    for item in data.get("results") or []:
        if item.get("wrapperType") == "track":
            return _normalize_itunes_song(item)
    return None


def get_artist(raw_id: str) -> Optional[Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]]:
    data = http_client.fetch_json(
        f"{ITUNES_BASE}/lookup?id={raw_id}&entity=song&limit=25", timeout=config.PROVIDER_TIMEOUT
    )
    results = data.get("results") or []
    if not results:
        return None
    artist_row = None
    tracks: List[Dict[str, Any]] = []
    albums_seen: Dict[str, Dict[str, Any]] = {}
    for item in results:
        if item.get("wrapperType") == "artist" and artist_row is None:
            artist_row = item
        elif item.get("wrapperType") == "track":
            t = _normalize_itunes_song(item)
            if t:
                tracks.append(t)
                if t["albumId"] and t["albumId"] not in albums_seen:
                    albums_seen[t["albumId"]] = {
                        "id": t["albumId"],
                        "source": "itunes",
                        "title": t["album"],
                        "artist": t["artist"],
                        "artistId": t["artistId"],
                        "coverSmall": t["coverSmall"],
                        "coverLarge": t["coverLarge"],
                        "releaseDate": t["releaseDate"],
                        "trackCount": 0,
                    }
    if not artist_row and not tracks:
        return None
    name = (artist_row or {}).get("artistName") or (tracks[0]["artist"] if tracks else "Unknown Artist")
    pic = tracks[0]["coverLarge"] if tracks else ""
    artist = {
        "id": f"it:{raw_id}",
        "source": "itunes",
        "name": str(name),
        "pictureSmall": tracks[0]["coverSmall"] if tracks else "",
        "pictureLarge": pic,
        "fans": 0,
    }
    return artist, tracks, list(albums_seen.values())


def get_album(raw_id: str) -> Optional[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    data = http_client.fetch_json(
        f"{ITUNES_BASE}/lookup?id={raw_id}&entity=song", timeout=config.PROVIDER_TIMEOUT
    )
    results = data.get("results") or []
    if not results:
        return None
    col = None
    tracks: List[Dict[str, Any]] = []
    for item in results:
        if item.get("wrapperType") == "collection" and col is None:
            col = item
        elif item.get("wrapperType") == "track":
            t = _normalize_itunes_song(item)
            if t:
                tracks.append(t)
    if not col and not tracks:
        return None
    art = str((col or {}).get("artworkUrl100") or "")
    artist_id = (col or {}).get("artistId")
    album = {
        "id": f"it:{raw_id}",
        "source": "itunes",
        "title": str((col or {}).get("collectionName") or (tracks[0]["album"] if tracks else "Untitled Album")),
        "artist": str((col or {}).get("artistName") or (tracks[0]["artist"] if tracks else "Various Artists")),
        "artistId": f"it:{artist_id}" if artist_id else (tracks[0]["artistId"] if tracks else ""),
        "coverSmall": _upscale_artwork(art, 300) if art else (tracks[0]["coverSmall"] if tracks else ""),
        "coverLarge": _upscale_artwork(art, 600) if art else (tracks[0]["coverLarge"] if tracks else ""),
        "releaseDate": str((col or {}).get("releaseDate") or "")[:10],
        "trackCount": int((col or {}).get("trackCount") or len(tracks)),
        "totalDuration": sum(t["duration"] for t in tracks),
    }
    return album, tracks
