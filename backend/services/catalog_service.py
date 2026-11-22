"""Catalog service: Home/Discover, Live Multi-Source Search, Artist & Album detail."""

from __future__ import annotations

import concurrent.futures
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from backend import config, http_client, lrc_parser, offline_catalog
from backend.db import Database
from backend.providers import apple_itunes, audius, deezer, jiosaavn, youtube_music

_SEARCH_NOISE_KEYWORDS = (
    "karaoke",
    "cover",
    "tribute",
    "ringtone",
    "lullaby",
    "8-bit",
    "slowed",
    "sped up",
    "nightcore",
    "instrumental version",
    "parody",
    "mashup",
    "8d audio",
    "reverb",
)
_SEARCH_NOISE_PATTERNS = [
    re.compile(rf"(?<!\w){re.escape(kw)}(?!\w)", re.IGNORECASE)
    for kw in _SEARCH_NOISE_KEYWORDS
]


class CatalogService:
    """Aggregates live music providers with TTL caching, SQLite upsert, and offline fallback."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = threading.Lock()
        self._home_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._search_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def clear_caches(self) -> None:
        with self._lock:
            self._home_cache.clear()
            self._search_cache.clear()

    def _annotate_sections(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(payload)
        sections = []
        for sec in payload.get("sections") or []:
            sec_copy = dict(sec)
            if sec.get("type") == "tracks":
                sec_copy["items"] = self.db.annotate_tracks(sec.get("items") or [])
            sections.append(sec_copy)
        out["sections"] = sections
        return out

    def _build_offline_home(self, region: str) -> Dict[str, Any]:
        tracks = [dict(t) for t in offline_catalog.OFFLINE_TRACKS]
        self.db.upsert_tracks(tracks)

        global_tracks = [t for t in tracks if t.get("region") in ("global", "indie")]
        vn_tracks = [t for t in tracks if t.get("region") == "vn"] + global_tracks[:3]
        indie_tracks = [t for t in tracks if t.get("region") == "indie"] + global_tracks[:2]

        sections = [
            {
                "id": "trending-global",
                "title": "Trending Worldwide",
                "subtitle": "Offline showcase — connect internet for live global charts",
                "type": "tracks",
                "items": global_tracks,
            },
            {
                "id": "trending-vn",
                "title": "Top Vietnam / V-Pop",
                "subtitle": "Offline V-Pop showcase with synced lyrics",
                "type": "tracks",
                "items": vn_tracks,
            },
            {
                "id": "new-releases",
                "title": "New Releases & Hot Albums",
                "subtitle": "Featured studio albums",
                "type": "albums",
                "items": [dict(a) for a in offline_catalog.OFFLINE_ALBUMS],
            },
            {
                "id": "audius-trending",
                "title": "Full-Track Indie & Electronic",
                "subtitle": "Ad-free studio synthesis demo",
                "type": "tracks",
                "items": indie_tracks,
            },
            {
                "id": "top-artists",
                "title": "Popular Artists",
                "subtitle": "Featured artists",
                "type": "artists",
                "items": [dict(a) for a in offline_catalog.OFFLINE_ARTISTS],
            },
        ]
        if region == "vn":
            sections[0], sections[1] = sections[1], sections[0]

        return {
            "mode": "offline",
            "region": region,
            "generatedAt": int(time.time()),
            "sections": sections,
        }

    def get_home(self, region: str = "global", refresh: bool = False) -> Dict[str, Any]:
        region_key = "vn" if (region or "").lower() == "vn" else "global"
        now = time.time()

        if not refresh:
            with self._lock:
                entry = self._home_cache.get(region_key)
                if entry and (now - entry[0]) < config.HOME_CACHE_TTL:
                    return self._annotate_sections(entry[1])

        if http_client.is_offline_mode():
            offline_payload = self._build_offline_home(region_key)
            return self._annotate_sections(offline_payload)

        # Fetch multiple live providers concurrently
        def _safe(fn, *args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            f_dz_chart = pool.submit(_safe, deezer.get_chart, 20)
            f_dz_releases = pool.submit(_safe, deezer.get_new_releases, 16)
            f_vn_songs = pool.submit(_safe, apple_itunes.get_top_songs, "vn", 20)
            f_us_albums = pool.submit(_safe, apple_itunes.get_top_albums, "us", 16)
            f_audius = pool.submit(_safe, audius.get_trending, 15)

            dz_chart = f_dz_chart.result() or {}
            dz_releases = f_dz_releases.result() or []
            vn_songs = f_vn_songs.result() or []
            us_albums = f_us_albums.result() or []
            audius_tracks = f_audius.result() or []

        global_tracks = dz_chart.get("tracks") or []
        if not global_tracks and (vn_songs or audius_tracks or us_albums):
            global_tracks = _safe(apple_itunes.get_top_songs, "us", 16) or []

        top_artists = [
            a
            for a in (dz_chart.get("artists") or [])
            if not any(
                kw in (a.get("name") or "").lower()
                for kw in ("hörbuch", "audiobook", "die drei ???", "tkkg", "bibi blocksberg")
            )
        ]

        # Build rich music-only New Releases carousel (filter out Deezer audiobook entries and prefer fresh albums)
        audiobook_kw = ("ungekürzt", "gekürzt", "hörbuch", "audiobook", "kapitel", "teil ")
        dz_music_albums = [
            a
            for a in (dz_releases or dz_chart.get("albums") or [])
            if not any(kw in (a.get("title") or "").lower() for kw in audiobook_kw)
        ]
        new_releases: List[Dict[str, Any]] = []
        seen_alb: set[str] = set()
        for alb in dz_music_albums + us_albums:
            key = f"{lrc_parser.normalize_key(alb.get('title', ''))}::{lrc_parser.normalize_key(alb.get('artist', ''))}"
            if key not in seen_alb:
                seen_alb.add(key)
                new_releases.append(alb)
            if len(new_releases) >= 16:
                break

        # Guarantee a full New Releases carousel even if Apple RSS albums times out, using real albums from live charts
        if len(new_releases) < 12:
            for tr in global_tracks + vn_songs:
                alb_id = tr.get("albumId") or ""
                alb_title = tr.get("album") or ""
                if not alb_id or not alb_title:
                    continue
                if any(kw in alb_title.lower() for kw in audiobook_kw):
                    continue
                key = f"{lrc_parser.normalize_key(alb_title)}::{lrc_parser.normalize_key(tr.get('artist', ''))}"
                if key in seen_alb:
                    continue
                seen_alb.add(key)
                new_releases.append(
                    {
                        "id": alb_id,
                        "source": tr.get("source", "deezer"),
                        "title": alb_title,
                        "artist": tr.get("artist", "Various Artists"),
                        "artistId": tr.get("artistId", ""),
                        "coverSmall": tr.get("coverSmall", ""),
                        "coverLarge": tr.get("coverLarge", ""),
                        "releaseDate": tr.get("releaseDate", ""),
                        "trackCount": 0,
                    }
                )
                if len(new_releases) >= 16:
                    break

        # If every live upstream failed, switch seamlessly to offline fallback
        if not global_tracks and not vn_songs and not audius_tracks and not new_releases:
            offline_payload = self._build_offline_home(region_key)
            return self._annotate_sections(offline_payload)

        # If Apple RSS Vietnam chart timed out while online, populate Vietnam/V-Pop via live search
        if not vn_songs:
            vn_songs = _safe(apple_itunes.search_songs, "V-Pop", "vn", 16) or _safe(deezer.search_tracks, "v-pop", 16) or []

        all_tracks = global_tracks + vn_songs + audius_tracks
        self.db.upsert_tracks(all_tracks)

        sections: List[Dict[str, Any]] = []
        if global_tracks:
            sections.append(
                {
                    "id": "trending-global",
                    "title": "Trending Worldwide",
                    "subtitle": "Updated live global charts",
                    "type": "tracks",
                    "items": global_tracks,
                }
            )
        if vn_songs:
            sections.append(
                {
                    "id": "trending-vn",
                    "title": "Top Vietnam / V-Pop",
                    "subtitle": "Most played in Vietnam right now",
                    "type": "tracks",
                    "items": vn_songs,
                }
            )
        if new_releases:
            sections.append(
                {
                    "id": "new-releases",
                    "title": "New Releases & Hot Albums",
                    "subtitle": "Fresh releases and top albums",
                    "type": "albums",
                    "items": new_releases,
                }
            )
        if audius_tracks:
            sections.append(
                {
                    "id": "audius-trending",
                    "title": "Full-Track Indie & Electronic",
                    "subtitle": "Direct ad-free streams via Audius",
                    "type": "tracks",
                    "items": audius_tracks,
                }
            )
        if top_artists:
            sections.append(
                {
                    "id": "top-artists",
                    "title": "Popular Artists",
                    "subtitle": "Top trending artists worldwide",
                    "type": "artists",
                    "items": top_artists,
                }
            )

        if region_key == "vn" and len(sections) >= 2 and sections[1]["id"] == "trending-vn":
            sections[0], sections[1] = sections[1], sections[0]

        payload = {
            "mode": "online",
            "region": region_key,
            "generatedAt": int(time.time()),
            "sections": sections,
        }
        with self._lock:
            self._home_cache[region_key] = (now, payload)
        return self._annotate_sections(payload)

    def _dedup_and_rank_tracks(
        self, tracks: List[Dict[str, Any]], query: str, limit: int
    ) -> List[Dict[str, Any]]:
        q_norm = lrc_parser.normalize_key(query)
        q_tokens = set(q_norm.split()) if q_norm else set()
        dedup: Dict[str, Tuple[float, Dict[str, Any]]] = {}

        source_bonus = {
            "deezer": 0.14,
            "itunes": 0.12,
            "jiosaavn": 0.08,
            "youtube_music": 0.05,
            "audius": 0.02,
            "local": 0.0,
        }

        # Track per-source original popularity position so official #1 catalog hits win ties
        source_pos: Dict[str, int] = {}

        for t in tracks:
            src = str(t.get("source", ""))
            pos = source_pos.get(src, 0)
            source_pos[src] = pos + 1

            title_clean = lrc_parser.clean_track_title(t.get("title", ""))
            primary_art = lrc_parser.extract_primary_artist(t.get("artist", ""))
            full_art = str(t.get("artist", ""))
            title_norm = lrc_parser.normalize_key(title_clean)
            art_norm = lrc_parser.normalize_key(primary_art)
            full_art_norm = lrc_parser.normalize_key(full_art)
            key = f"{title_norm}::{art_norm}"
            if not key.strip(":"):
                key = str(t.get("id"))

            t_sim = lrc_parser.token_similarity(title_clean, query)
            a_sim = max(
                lrc_parser.token_similarity(primary_art, query),
                lrc_parser.token_similarity(full_art, query),
            )
            combined_sim = max(
                lrc_parser.token_similarity(f"{title_clean} {primary_art}", query),
                lrc_parser.token_similarity(f"{title_clean} {full_art}", query),
            )

            art_tokens = set(full_art_norm.split()) if full_art_norm else set()
            title_tokens = set(title_norm.split()) if title_norm else set()
            artist_hits_query = bool(q_tokens and art_tokens and (q_tokens & art_tokens))
            title_hits_query = bool(q_tokens and title_tokens and (q_tokens & title_tokens))
            title_in_q = bool(title_norm and len(title_norm) >= 2 and title_norm in q_norm)
            art_in_q = bool(
                (art_norm and len(art_norm) >= 2 and art_norm in q_norm)
                or (full_art_norm and len(full_art_norm) >= 2 and full_art_norm in q_norm)
            )
            exact_title = bool(q_norm and (title_norm == q_norm or q_norm in title_norm))
            exact_artist = bool(q_norm and (art_norm == q_norm or q_norm in art_norm or q_norm in full_art_norm))

            exact_boost = 0.0
            if title_in_q and art_in_q:
                # Query contains BOTH the exact track title AND the actual credited artist (e.g. "Blinding Lights The Weeknd")
                exact_boost = 0.42
            elif artist_hits_query and title_hits_query and q_tokens.issubset(title_tokens | art_tokens):
                # Every query token is explained across real title + real artist
                exact_boost = 0.36
            elif exact_artist:
                exact_boost = 0.28 if art_norm == q_norm else 0.20
            elif exact_title:
                # If query is multi-word and artist has ZERO overlap, this is likely a bootleg/reupload embedding artist in title
                if len(q_tokens) >= 3 and not artist_hits_query:
                    exact_boost = 0.04
                elif title_norm == q_norm:
                    exact_boost = 0.25 if src != "audius" else 0.14
                else:
                    exact_boost = 0.16 if src != "audius" else 0.06
            elif artist_hits_query and title_hits_query:
                exact_boost = 0.18

            rank_boost = max(0.0, 0.07 * (1.0 - pos / 20.0)) if src in ("deezer", "itunes") else 0.0
            meta_boost = (0.04 if t.get("coverLarge") else 0.0) + (0.03 if t.get("duration", 0) > 0 else 0.0)
            score = (
                max(t_sim, a_sim * 0.96, combined_sim)
                + exact_boost
                + rank_boost
                + meta_boost
                + source_bonus.get(src, 0.0)
            )

            # Demote karaoke/cover/tribute/ringtone/slowed knockoffs unless user explicitly searched for them
            raw_text = f"{t.get('title', '')} {t.get('artist', '')}"
            for pat in _SEARCH_NOISE_PATTERNS:
                if pat.search(raw_text) and not pat.search(query):
                    score -= 0.42
                    break

            existing = dedup.get(key)
            if existing is None or score > existing[0]:
                dedup[key] = (score, t)

        ranked = sorted(dedup.values(), key=lambda x: x[0], reverse=True)
        return [item[1] for item in ranked[:limit]]

    def _search_offline(self, query: str, search_type: str, limit: int) -> Dict[str, Any]:
        q_norm = lrc_parser.normalize_key(query)
        tokens = [tok for tok in q_norm.split() if tok]

        def _matches(haystack: str) -> bool:
            h_norm = lrc_parser.normalize_key(haystack)
            if not tokens:
                return True
            if q_norm in h_norm or all(tok in h_norm for tok in tokens):
                return True
            q_compact = q_norm.replace(" ", "")
            if len(q_compact) >= 4 and q_compact in h_norm.replace(" ", ""):
                return True
            return lrc_parser.token_similarity(h_norm, q_norm) >= 0.45

        offline_tracks = [dict(t) for t in offline_catalog.OFFLINE_TRACKS]
        self.db.upsert_tracks(offline_tracks)

        cached = self.db.search_cached_tracks(query, limit=limit)
        matched_local = [
            t
            for t in offline_tracks
            if _matches(f"{t['title']} {t['artist']} {t['album']}")
        ]
        # Combine SQLite cached hits + offline catalog
        seen_ids = set()
        tracks: List[Dict[str, Any]] = []
        for t in cached + matched_local:
            if t["id"] not in seen_ids:
                seen_ids.add(t["id"])
                tracks.append(t)
        tracks = self._dedup_and_rank_tracks(tracks, query, limit)

        artists = [
            dict(a) for a in offline_catalog.OFFLINE_ARTISTS if _matches(a["name"])
        ]
        albums = [
            dict(a)
            for a in offline_catalog.OFFLINE_ALBUMS
            if _matches(f"{a['title']} {a['artist']}")
        ]

        if search_type == "tracks":
            artists, albums = [], []
        elif search_type == "artists":
            tracks, albums = [], []
        elif search_type == "albums":
            tracks, artists = [], []

        annotated = self.db.annotate_tracks(tracks)
        top_result = self._pick_top_result(query, annotated, artists, albums)
        return {
            "query": query,
            "mode": "offline",
            "topResult": top_result,
            "tracks": annotated,
            "artists": artists,
            "albums": albums,
        }

    def _pick_top_result(
        self,
        query: str,
        tracks: List[Dict[str, Any]],
        artists: List[Dict[str, Any]],
        albums: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        best_artist_sim = lrc_parser.token_similarity(artists[0]["name"], query) if artists else 0.0
        best_track_sim = lrc_parser.token_similarity(tracks[0]["title"], query) if tracks else 0.0
        if artists and best_artist_sim >= 0.85 and best_artist_sim >= best_track_sim:
            return {"kind": "artist", "item": artists[0]}
        if tracks:
            return {"kind": "track", "item": tracks[0]}
        if artists:
            return {"kind": "artist", "item": artists[0]}
        if albums:
            return {"kind": "album", "item": albums[0]}
        return None

    def search(self, query: str, search_type: str = "all", limit: int = 20) -> Dict[str, Any]:
        q = (query or "").strip()
        stype = (search_type or "all").lower()
        if stype not in ("all", "tracks", "artists", "albums"):
            stype = "all"
        lim = max(1, min(int(limit or 20), 40))

        if not q:
            return {
                "query": "",
                "mode": "offline" if http_client.is_offline_mode() else "online",
                "topResult": None,
                "tracks": [],
                "artists": [],
                "albums": [],
            }

        cache_key = f"{stype}::{lim}::{q.lower()}"
        now = time.time()
        with self._lock:
            entry = self._search_cache.get(cache_key)
            if entry and (now - entry[0]) < config.SEARCH_CACHE_TTL:
                cached_res = dict(entry[1])
                cached_res["tracks"] = self.db.annotate_tracks(cached_res.get("tracks") or [])
                if cached_res.get("topResult") and cached_res["topResult"].get("kind") == "track":
                    top_list = self.db.annotate_tracks([cached_res["topResult"]["item"]])
                    cached_res["topResult"] = {"kind": "track", "item": top_list[0]}
                return cached_res

        if http_client.is_offline_mode():
            return self._search_offline(q, stype, lim)

        def _safe(fn, *args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                return None

        need_tracks = stype in ("all", "tracks")
        need_artists = stype in ("all", "artists")
        need_albums = stype in ("all", "albums")

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            f_dz_t = pool.submit(_safe, deezer.search_tracks, q, lim) if need_tracks else None
            f_it_t = pool.submit(_safe, apple_itunes.search_songs, q, "vn", 12) if need_tracks else None
            f_au_t = pool.submit(_safe, audius.search_tracks, q, 8) if need_tracks else None
            f_js_t = pool.submit(_safe, jiosaavn.search_songs, q, 8) if need_tracks else None
            f_dz_a = pool.submit(_safe, deezer.search_artists, q, 10) if need_artists else None
            f_dz_al = pool.submit(_safe, deezer.search_albums, q, 10) if need_albums else None

            dz_tracks = f_dz_t.result() if f_dz_t else None
            it_tracks = f_it_t.result() if f_it_t else None
            au_tracks = f_au_t.result() if f_au_t else None
            js_tracks = f_js_t.result() if f_js_t else None
            artists = f_dz_a.result() if f_dz_a else None
            albums = f_dz_al.result() if f_dz_al else None

        submitted_results = [
            res
            for fut, res in (
                (f_dz_t, dz_tracks),
                (f_it_t, it_tracks),
                (f_au_t, au_tracks),
                (f_js_t, js_tracks),
                (f_dz_a, artists),
                (f_dz_al, albums),
            )
            if fut is not None
        ]
        # If every submitted upstream provider failed (returned None), network is unreachable -> offline fallback
        if not any(res is not None for res in submitted_results):
            return self._search_offline(q, stype, lim)

        combined_tracks = (dz_tracks or []) + (it_tracks or []) + (js_tracks or []) + (au_tracks or [])
        # If standard providers found < 3 tracks, also supplement with YTM InnerTube search
        if need_tracks and len(combined_tracks) < 3:
            yt_tracks = _safe(youtube_music.search_songs, q, 8) or []
            combined_tracks.extend(yt_tracks)

        ranked_tracks = self._dedup_and_rank_tracks(combined_tracks, q, lim) if need_tracks else []
        if ranked_tracks:
            self.db.upsert_tracks(ranked_tracks)

        annotated = self.db.annotate_tracks(ranked_tracks)
        artists_list = artists or []
        albums_list = albums or []
        top_result = self._pick_top_result(q, annotated, artists_list, albums_list)

        result = {
            "query": q,
            "mode": "online",
            "topResult": top_result,
            "tracks": annotated,
            "artists": artists_list,
            "albums": albums_list,
        }
        with self._lock:
            if len(self._search_cache) > 80:
                self._search_cache.pop(next(iter(self._search_cache)))
            self._search_cache[cache_key] = (now, result)
        return result

    def get_artist(self, artist_id: str) -> Optional[Dict[str, Any]]:
        if not artist_id:
            return None
        prefix, _, raw_id = artist_id.partition(":")

        if prefix == "local":
            res = offline_catalog.get_offline_artist(artist_id)
            if not res:
                return None
            artist, tracks, albums = res
            self.db.upsert_tracks(tracks)
            return {
                "artist": artist,
                "topTracks": self.db.annotate_tracks(tracks),
                "albums": albums,
            }

        if not http_client.is_offline_mode():
            try:
                res = None
                if prefix == "dz" and raw_id:
                    res = deezer.get_artist(raw_id)
                elif prefix == "it" and raw_id:
                    res = apple_itunes.get_artist(raw_id)
                elif prefix == "au" and raw_id:
                    res = audius.get_artist(raw_id)
                if res:
                    artist, tracks, albums = res
                    if tracks:
                        self.db.upsert_tracks(tracks)
                    return {
                        "artist": artist,
                        "topTracks": self.db.annotate_tracks(tracks),
                        "albums": albums,
                    }
            except Exception:
                pass

        # Fallback to cached tracks for this artist_id
        cached_tracks = self.db.get_tracks_by_artist_id(artist_id)
        if cached_tracks:
            first = cached_tracks[0]
            return {
                "artist": {
                    "id": artist_id,
                    "source": first["source"],
                    "name": first["artist"],
                    "pictureSmall": first["coverSmall"],
                    "pictureLarge": first["coverLarge"],
                    "fans": 0,
                },
                "topTracks": cached_tracks,
                "albums": [],
            }
        return None

    def get_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        if not album_id:
            return None
        prefix, _, raw_id = album_id.partition(":")

        if prefix == "local":
            res = offline_catalog.get_offline_album(album_id)
            if not res:
                return None
            album, tracks = res
            self.db.upsert_tracks(tracks)
            return {
                "album": album,
                "tracks": self.db.annotate_tracks(tracks),
            }

        if not http_client.is_offline_mode():
            try:
                res = None
                if prefix == "dz" and raw_id:
                    res = deezer.get_album(raw_id)
                elif prefix == "it" and raw_id:
                    res = apple_itunes.get_album(raw_id)
                if res:
                    album, tracks = res
                    if tracks:
                        self.db.upsert_tracks(tracks)
                    return {
                        "album": album,
                        "tracks": self.db.annotate_tracks(tracks),
                    }
            except Exception:
                pass

        cached_tracks = self.db.get_tracks_by_album_id(album_id)
        if cached_tracks:
            first = cached_tracks[0]
            return {
                "album": {
                    "id": album_id,
                    "source": first["source"],
                    "title": first["album"] or "Album",
                    "artist": first["artist"],
                    "artistId": first["artistId"],
                    "coverSmall": first["coverSmall"],
                    "coverLarge": first["coverLarge"],
                    "releaseDate": first["releaseDate"],
                    "trackCount": len(cached_tracks),
                    "totalDuration": sum(t["duration"] for t in cached_tracks),
                },
                "tracks": cached_tracks,
            }
        return None
