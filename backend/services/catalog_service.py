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


HOME_SWR_TTL = 3600.0          # Serve stale Home snapshot up to 1h while refreshing in background
HOME_FETCH_DEADLINE = 3.5      # Cap Home parallel provider wait at 3.5s


_GENRE_SPAM_RE = re.compile(
    r"(?:as\s+made\s+famous\s+by|\bkaraoke\b|\btribute\b|workout\s+music\s+tv|"
    r"\bringtone\b|\b8-bit\b|\blullaby\b|christmas\s+music)",
    re.IGNORECASE,
)


def _is_genre_spam(item: Dict[str, Any]) -> bool:
    haystack = f"{item.get('title') or ''} {item.get('artist') or ''} {item.get('album') or ''}"
    return bool(_GENRE_SPAM_RE.search(haystack))


def _interleave_lists(lists: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    max_len = max((len(lst) for lst in lists), default=0)
    for i in range(max_len):
        for lst in lists:
            if i < len(lst):
                out.append(lst[i])
    return out


class CatalogService:
    """Aggregates live music providers with TTL caching, SQLite upsert, and offline fallback."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = threading.Lock()
        self._home_base_cache: Optional[Tuple[float, Dict[str, Any]]] = None
        self._home_refreshing: bool = False
        self._search_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._genre_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._mix_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def clear_caches(self) -> None:
        with self._lock:
            self._home_base_cache = None
            self._search_cache.clear()
            self._genre_cache.clear()
            self._mix_cache.clear()

    def _annotate_sections(
        self, payload: Dict[str, Any], owner_id: str = "default"
    ) -> Dict[str, Any]:
        out = dict(payload)
        sections = []
        for sec in payload.get("sections") or []:
            sec_copy = dict(sec)
            if sec.get("type") == "tracks":
                sec_copy["items"] = self.db.annotate_tracks(
                    sec.get("items") or [], owner_id=owner_id
                )
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

    @staticmethod
    def _project_home_region(base_payload: Dict[str, Any], region_key: str) -> Dict[str, Any]:
        sections = [dict(s) for s in (base_payload.get("sections") or [])]
        if region_key == "vn":
            vn_idx = next((i for i, s in enumerate(sections) if s.get("id") == "trending-vn"), -1)
            if vn_idx > 0:
                vn_sec = sections.pop(vn_idx)
                sections.insert(0, vn_sec)
        else:
            gl_idx = next((i for i, s in enumerate(sections) if s.get("id") == "trending-global"), -1)
            if gl_idx > 0:
                gl_sec = sections.pop(gl_idx)
                sections.insert(0, gl_sec)
        return {
            "mode": base_payload.get("mode", "online"),
            "region": region_key,
            "generatedAt": base_payload.get("generatedAt", int(time.time())),
            "sections": sections,
        }

    def _fetch_home_base(self) -> Optional[Dict[str, Any]]:
        """Fetch live Home providers concurrently with a 3.5s deadline and store shared base cache."""
        def _safe(fn, *args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                return None

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        try:
            f_dz_chart = pool.submit(_safe, deezer.get_chart, 20)
            f_dz_releases = pool.submit(_safe, deezer.get_new_releases, 16)
            f_vn_songs = pool.submit(_safe, apple_itunes.get_top_songs, "vn", 20)
            f_us_albums = pool.submit(_safe, apple_itunes.get_top_albums, "us", 16)
            f_audius = pool.submit(_safe, audius.get_trending, 15)

            all_futs = [f_dz_chart, f_dz_releases, f_vn_songs, f_us_albums, f_audius]
            concurrent.futures.wait(all_futs, timeout=HOME_FETCH_DEADLINE)

            def _done_result(fut, default):
                if fut.done():
                    try:
                        val = fut.result()
                        return val if val is not None else default
                    except Exception:
                        return default
                return default

            dz_chart = _done_result(f_dz_chart, {})
            dz_releases = _done_result(f_dz_releases, [])
            vn_songs = _done_result(f_vn_songs, [])
            us_albums = _done_result(f_us_albums, [])
            audius_tracks = _done_result(f_audius, [])
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

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

        # Build rich music-only New Releases carousel:
        # Prioritize top-streamed US albums + Deezer chart albums + live chart track albums FIRST
        # (which have ~100% full-track availability), using Deezer editorial releases only as a last resort
        # if < 12 albums were collected (avoids obscure 1990s soundtrack/regional reissues).
        audiobook_kw = ("ungekürzt", "gekürzt", "hörbuch", "audiobook", "kapitel", "teil ")

        def _is_clean_album(alb: Dict[str, Any]) -> bool:
            title_l = (alb.get("title") or "").lower()
            if any(kw in title_l for kw in audiobook_kw):
                return False
            return not _is_genre_spam(alb)

        chart_track_albums: List[Dict[str, Any]] = []
        for tr in global_tracks + vn_songs:
            alb_id = tr.get("albumId") or ""
            alb_title = tr.get("album") or ""
            if not alb_id or not alb_title:
                continue
            if any(kw in alb_title.lower() for kw in audiobook_kw) or _is_genre_spam(tr):
                continue
            chart_track_albums.append(
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

        primary_albums = [
            a
            for a in (list(us_albums or []) + chart_track_albums + list(dz_chart.get("albums") or []))
            if _is_clean_album(a)
        ]
        new_releases: List[Dict[str, Any]] = []
        seen_alb: set[str] = set()
        for alb in primary_albums:
            key = f"{lrc_parser.normalize_key(alb.get('title', ''))}::{lrc_parser.normalize_key(alb.get('artist', ''))}"
            if key not in seen_alb:
                seen_alb.add(key)
                new_releases.append(alb)
            if len(new_releases) >= 16:
                break

        # Last-resort fallback to Deezer editorial releases only if < 12 albums were collected
        if len(new_releases) < 12:
            for alb in (dz_releases or []):
                if not _is_clean_album(alb):
                    continue
                key = f"{lrc_parser.normalize_key(alb.get('title', ''))}::{lrc_parser.normalize_key(alb.get('artist', ''))}"
                if key not in seen_alb:
                    seen_alb.add(key)
                    new_releases.append(alb)
                if len(new_releases) >= 16:
                    break

        # If every live upstream failed, signal offline fallback
        if not global_tracks and not vn_songs and not audius_tracks and not new_releases:
            return None

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

        now = time.time()
        base_payload = {
            "mode": "online",
            "region": "global",
            "generatedAt": int(now),
            "sections": sections,
        }
        with self._lock:
            self._home_base_cache = (now, base_payload)
        return base_payload

    def _trigger_background_home_refresh(self) -> None:
        with self._lock:
            if self._home_refreshing:
                return
            self._home_refreshing = True

        def _bg() -> None:
            try:
                if not http_client.is_offline_mode():
                    self._fetch_home_base()
            except Exception:
                pass
            finally:
                with self._lock:
                    self._home_refreshing = False

        threading.Thread(target=_bg, name="home-swr-refresh", daemon=True).start()

    def get_home(
        self, region: str = "global", refresh: bool = False, owner_id: str = "default"
    ) -> Dict[str, Any]:
        region_key = "vn" if (region or "").lower() == "vn" else "global"
        now = time.time()

        if http_client.is_offline_mode():
            offline_payload = self._build_offline_home(region_key)
            return self._annotate_sections(offline_payload, owner_id=owner_id)

        if not refresh:
            with self._lock:
                entry = self._home_base_cache
            if entry is not None:
                age = now - entry[0]
                if age < config.HOME_CACHE_TTL:
                    return self._annotate_sections(
                        self._project_home_region(entry[1], region_key), owner_id=owner_id
                    )
                if age < HOME_SWR_TTL:
                    # Stale-while-revalidate: instant response + background refresh
                    self._trigger_background_home_refresh()
                    return self._annotate_sections(
                        self._project_home_region(entry[1], region_key), owner_id=owner_id
                    )

        base_payload = self._fetch_home_base()
        if base_payload is None:
            offline_payload = self._build_offline_home(region_key)
            return self._annotate_sections(offline_payload, owner_id=owner_id)

        return self._annotate_sections(
            self._project_home_region(base_payload, region_key), owner_id=owner_id
        )

    def _dedup_and_rank_tracks(
        self, tracks: List[Dict[str, Any]], query: str, limit: int
    ) -> List[Dict[str, Any]]:
        q_norm = lrc_parser.normalize_key(query)
        q_tokens = set(q_norm.split()) if q_norm else set()
        dedup: Dict[str, Tuple[float, Dict[str, Any]]] = {}

        source_bonus = {
            "youtube_music": 0.11,
            "deezer": 0.08,
            "itunes": 0.07,
            "jiosaavn": 0.04,
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

            rank_boost = (
                max(0.0, 0.07 * (1.0 - pos / 20.0))
                if src in ("youtube_music", "deezer", "itunes")
                else 0.0
            )
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
            if existing is None:
                dedup[key] = (score, dict(t))
            else:
                prev_score, prev_track = existing
                winner = dict(t) if score > prev_score else dict(prev_track)
                loser = prev_track if score > prev_score else t
                # Cross-enrich winner with videoId, richer album/artwork/isrc/previewUrl from loser
                w_extra = dict(winner.get("_extra") or {})
                l_extra = dict(loser.get("_extra") or {})
                if not w_extra.get("videoId") and l_extra.get("videoId"):
                    w_extra["videoId"] = l_extra["videoId"]
                    w_extra["videoIdMatchVersion"] = 2
                if not w_extra.get("isrc") and l_extra.get("isrc"):
                    w_extra["isrc"] = l_extra["isrc"]
                if w_extra:
                    winner["_extra"] = w_extra
                if not winner.get("album") and loser.get("album"):
                    winner["album"] = loser["album"]
                    if not winner.get("albumId") and loser.get("albumId"):
                        winner["albumId"] = loser["albumId"]
                if not winner.get("artistId") and loser.get("artistId"):
                    winner["artistId"] = loser["artistId"]
                if not winner.get("previewUrl") and loser.get("previewUrl"):
                    winner["previewUrl"] = loser["previewUrl"]
                if (
                    loser.get("coverLarge")
                    and (
                        not winner.get("coverLarge")
                        or "i.ytimg.com" in str(winner.get("coverLarge") or "")
                    )
                ):
                    winner["coverLarge"] = loser["coverLarge"]
                    if loser.get("coverSmall"):
                        winner["coverSmall"] = loser["coverSmall"]
                dedup[key] = (max(score, prev_score), winner)

        ranked = sorted(dedup.values(), key=lambda x: x[0], reverse=True)
        return [item[1] for item in ranked[:limit]]

    def _search_offline(
        self, query: str, search_type: str, limit: int, owner_id: str = "default"
    ) -> Dict[str, Any]:
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

        cached = self.db.search_cached_tracks(query, limit=limit, owner_id=owner_id)
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

        annotated = self.db.annotate_tracks(tracks, owner_id=owner_id)
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

    def search(
        self, query: str, search_type: str = "all", limit: int = 20, owner_id: str = "default"
    ) -> Dict[str, Any]:
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
                cached_res["tracks"] = self.db.annotate_tracks(
                    cached_res.get("tracks") or [], owner_id=owner_id
                )
                if cached_res.get("topResult") and cached_res["topResult"].get("kind") == "track":
                    top_list = self.db.annotate_tracks(
                        [cached_res["topResult"]["item"]], owner_id=owner_id
                    )
                    cached_res["topResult"] = {"kind": "track", "item": top_list[0]}
                return cached_res

        if http_client.is_offline_mode():
            return self._search_offline(q, stype, lim, owner_id=owner_id)

        def _safe(fn, *args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                return None

        need_tracks = stype in ("all", "tracks")
        need_artists = stype in ("all", "artists")
        need_albums = stype in ("all", "albums")

        with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
            f_yt_t = pool.submit(_safe, youtube_music.search_songs, q, min(lim, 15)) if need_tracks else None
            f_dz_t = pool.submit(_safe, deezer.search_tracks, q, lim) if need_tracks else None
            f_it_t = pool.submit(_safe, apple_itunes.search_songs, q, "vn", 12) if need_tracks else None
            f_au_t = pool.submit(_safe, audius.search_tracks, q, 8) if need_tracks else None
            f_js_t = pool.submit(_safe, jiosaavn.search_songs, q, 8) if need_tracks else None
            f_dz_a = pool.submit(_safe, deezer.search_artists, q, 10) if need_artists else None
            f_dz_al = pool.submit(_safe, deezer.search_albums, q, 10) if need_albums else None

            yt_tracks = f_yt_t.result() if f_yt_t else None
            dz_tracks = f_dz_t.result() if f_dz_t else None
            it_tracks = f_it_t.result() if f_it_t else None
            au_tracks = f_au_t.result() if f_au_t else None
            js_tracks = f_js_t.result() if f_js_t else None
            artists = f_dz_a.result() if f_dz_a else None
            albums = f_dz_al.result() if f_dz_al else None

        submitted_results = [
            res
            for fut, res in (
                (f_yt_t, yt_tracks if yt_tracks else None),
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
            return self._search_offline(q, stype, lim, owner_id=owner_id)

        combined_tracks = (
            (yt_tracks or [])
            + (dz_tracks or [])
            + (it_tracks or [])
            + (js_tracks or [])
            + (au_tracks or [])
        )

        ranked_tracks = self._dedup_and_rank_tracks(combined_tracks, q, lim) if need_tracks else []
        if ranked_tracks:
            self.db.upsert_tracks(ranked_tracks)

        annotated = self.db.annotate_tracks(ranked_tracks, owner_id=owner_id)
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

    def get_artist(self, artist_id: str, owner_id: str = "default") -> Optional[Dict[str, Any]]:
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
                "topTracks": self.db.annotate_tracks(tracks, owner_id=owner_id),
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
                        "topTracks": self.db.annotate_tracks(tracks, owner_id=owner_id),
                        "albums": albums,
                    }
            except Exception:
                pass

        # Fallback to cached tracks for this artist_id
        cached_tracks = self.db.get_tracks_by_artist_id(artist_id, owner_id=owner_id)
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

    def get_album(self, album_id: str, owner_id: str = "default") -> Optional[Dict[str, Any]]:
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
                "tracks": self.db.annotate_tracks(tracks, owner_id=owner_id),
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
                        "tracks": self.db.annotate_tracks(tracks, owner_id=owner_id),
                    }
            except Exception:
                pass

        cached_tracks = self.db.get_tracks_by_album_id(album_id, owner_id=owner_id)
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

    # ------------------------------------------------------------------
    # Spotify Parity: Genre/Mood Hubs, Song/Artist Radio, Made For You,
    # Playlist Recommendations & Single Track Deep Link
    # ------------------------------------------------------------------
    GENRE_HUBS: Dict[str, Dict[str, Any]] = {
        "pop": {
            "slug": "pop",
            "name": "Pop",
            "color": "#E8115B",
            "description": "Chart-topping pop anthems and global hits.",
            "queries": ["taylor swift", "sabrina carpenter", "ariana grande"],
        },
        "vpop": {
            "slug": "vpop",
            "name": "V-Pop & Vietnam",
            "color": "#1DB954",
            "description": "Vietnam's hottest V-Pop, Indie & Rap hits.",
            "queries": ["sơn tùng m-tp", "hieuthuhai", "vũ."],
        },
        "hiphop": {
            "slug": "hiphop",
            "name": "Hip-Hop & Rap",
            "color": "#BC5900",
            "description": "Hard-hitting hip-hop, trap, and rap essentials.",
            "queries": ["kendrick lamar", "drake", "travis scott"],
        },
        "kpop": {
            "slug": "kpop",
            "name": "K-Pop",
            "color": "#E13300",
            "description": "Seoul's biggest idols, choreo bangers, and K-R&B.",
            "queries": ["blackpink", "newjeans", "aespa"],
        },
        "indie": {
            "slug": "indie",
            "name": "Indie & Alt",
            "color": "#148A08",
            "description": "Dreamy guitars, alt-pop, and independent gems.",
            "queries": ["tame impala", "arctic monkeys", "clairo"],
        },
        "electronic": {
            "slug": "electronic",
            "name": "Electronic & EDM",
            "color": "#0D73EC",
            "description": "House, synthwave, and festival-ready electronic.",
            "queries": ["calvin harris", "daft punk", "fred again"],
        },
        "chill": {
            "slug": "chill",
            "name": "Chill & Unwind",
            "color": "#1E3264",
            "description": "Laid-back tracks for late nights and easy afternoons.",
            "queries": ["billie eilish", "lana del rey", "cigarettes after sex"],
        },
        "workout": {
            "slug": "workout",
            "name": "Workout & Energy",
            "color": "#D84000",
            "description": "High-tempo motivation to power every rep and run.",
            "queries": ["eminem", "dua lipa", "david guetta"],
        },
        "focus": {
            "slug": "focus",
            "name": "Deep Focus",
            "color": "#503750",
            "description": "Ambient, piano, and instrumental flow for deep work.",
            "queries": ["ludovico einaudi", "max richter", "yiruma"],
        },
        "rnb": {
            "slug": "rnb",
            "name": "R&B & Soul",
            "color": "#8D67AB",
            "description": "Silky vocals, neo-soul grooves, and midnight R&B.",
            "queries": ["sza", "the weeknd", "frank ocean"],
        },
        "acoustic": {
            "slug": "acoustic",
            "name": "Acoustic & Ballad",
            "color": "#7358FF",
            "description": "Stripped-back ballads, warm strings, and pure vocals.",
            "queries": ["ed sheeran", "adele", "john mayer"],
        },
        "lofi": {
            "slug": "lofi",
            "name": "Lo-Fi Beats",
            "color": "#27856A",
            "description": "Dusty beats to study, code, and relax to.",
            "queries": ["joji", "nujabes", "keshi"],
        },
    }

    def _dedup_tracks_keep_order(
        self, tracks: List[Dict[str, Any]], exclude_ids: Optional[set[str]] = None, limit: int = 25
    ) -> List[Dict[str, Any]]:
        excluded = exclude_ids or set()
        seen_ids: set[str] = set()
        seen_keys: set[str] = set()
        out: List[Dict[str, Any]] = []
        for tr in tracks:
            if not isinstance(tr, dict):
                continue
            tid = str(tr.get("id") or "").strip()
            if not tid or tid in excluded or tid in seen_ids:
                continue
            key = f"{lrc_parser.normalize_key(tr.get('title', ''))}::{lrc_parser.normalize_key(tr.get('artist', ''))}"
            if key in seen_keys:
                continue
            seen_ids.add(tid)
            seen_keys.add(key)
            out.append(tr)
            if len(out) >= limit:
                break
        return out

    def get_genre_hub(
        self, slug: str, region: str = "global", owner_id: str = "default"
    ) -> Optional[Dict[str, Any]]:
        clean_slug = (slug or "").strip().lower()
        meta = self.GENRE_HUBS.get(clean_slug)
        if not meta:
            return None
        region_key = "vn" if (region or "").lower() == "vn" else "global"
        cache_key = f"{clean_slug}::{region_key}"
        now = time.time()

        if not http_client.is_offline_mode():
            with self._lock:
                cached = self._genre_cache.get(cache_key)
                if cached and (now - cached[0]) < config.HOME_CACHE_TTL:
                    payload = dict(cached[1])
                    payload["tracks"] = self.db.annotate_tracks(
                        payload.get("tracks") or [], owner_id=owner_id
                    )
                    return payload

        if http_client.is_offline_mode():
            tracks = [dict(t) for t in offline_catalog.OFFLINE_TRACKS]
            self.db.upsert_tracks(tracks)
            return {
                "genre": {
                    "slug": meta["slug"],
                    "name": meta["name"],
                    "color": meta["color"],
                    "description": meta["description"],
                },
                "tracks": self.db.annotate_tracks(tracks, owner_id=owner_id),
                "albums": [dict(a) for a in offline_catalog.OFFLINE_ALBUMS],
                "artists": [dict(a) for a in offline_catalog.OFFLINE_ARTISTS],
            }

        queries = list(meta["queries"])
        if clean_slug == "vpop" or region_key == "vn":
            country = "vn" if clean_slug == "vpop" else "us"
        else:
            country = "us"

        def _safe(fn, *args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                return []

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=6)
        try:
            track_futs = [
                pool.submit(_safe, deezer.search_tracks, q, 12) for q in queries[:3]
            ]
            f_it_t = pool.submit(_safe, apple_itunes.search_songs, queries[-1], country, 10)
            f_dz_a = pool.submit(_safe, deezer.search_albums, queries[0], 10)
            f_dz_art = pool.submit(_safe, deezer.search_artists, queries[0], 10)
            all_futs = track_futs + [f_it_t, f_dz_a, f_dz_art]
            concurrent.futures.wait(all_futs, timeout=4.0)

            per_query_tracks: List[List[Dict[str, Any]]] = []
            for fut in track_futs:
                raw_list = fut.result() if fut.done() else []
                clean_list = [t for t in (raw_list or []) if not _is_genre_spam(t)]
                if clean_list:
                    per_query_tracks.append(clean_list)
            it_tracks = [
                t for t in ((f_it_t.result() if f_it_t.done() else []) or []) if not _is_genre_spam(t)
            ]
            if it_tracks:
                per_query_tracks.append(it_tracks)
            dz_albums = [
                a for a in ((f_dz_a.result() if f_dz_a.done() else []) or []) if not _is_genre_spam(a)
            ]
            dz_artists = (f_dz_art.result() if f_dz_art.done() else []) or []
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        interleaved = _interleave_lists(per_query_tracks)
        merged_tracks = self._dedup_tracks_keep_order(interleaved, limit=24)
        if not merged_tracks:
            merged_tracks = [dict(t) for t in offline_catalog.OFFLINE_TRACKS]
        self.db.upsert_tracks(merged_tracks)

        payload = {
            "genre": {
                "slug": meta["slug"],
                "name": meta["name"],
                "color": meta["color"],
                "description": meta["description"],
            },
            "tracks": merged_tracks,
            "albums": (dz_albums or [])[:12],
            "artists": (dz_artists or [])[:12],
        }
        with self._lock:
            self._genre_cache[cache_key] = (now, payload)

        out = dict(payload)
        out["tracks"] = self.db.annotate_tracks(merged_tracks, owner_id=owner_id)
        return out

    def get_radio(
        self, track_id: str = "", artist_id: str = "", limit: int = 25, owner_id: str = "default"
    ) -> Dict[str, Any]:
        max_items = max(1, min(int(limit or 25), 40))
        seed_type = "track" if track_id else "artist"
        seed_title = ""
        seed_artist = ""
        resolved_artist_id = (artist_id or "").strip()
        exclude_ids: set[str] = set()

        if track_id:
            exclude_ids.add(track_id)
            tr = self.db.get_track(track_id)
            if not tr and not http_client.is_offline_mode():
                prefix, _, raw_id = track_id.partition(":")
                try:
                    if prefix == "dz" and raw_id:
                        tr = deezer.get_track(raw_id)
                    elif prefix == "it" and raw_id:
                        tr = apple_itunes.get_track(raw_id)
                    elif prefix == "au" and raw_id:
                        tr = audius.get_track(raw_id)
                except Exception:
                    tr = None
                if tr:
                    self.db.upsert_track(tr)
            if not tr and track_id.startswith("local:"):
                tr = offline_catalog.get_offline_track(track_id)
            if tr:
                seed_title = str(tr.get("title") or "")
                seed_artist = str(tr.get("artist") or "")
                if not resolved_artist_id:
                    resolved_artist_id = str(tr.get("artistId") or "")

        if not seed_artist and resolved_artist_id:
            cached_art_tracks = self.db.get_tracks_by_artist_id(resolved_artist_id, limit=1)
            if cached_art_tracks:
                seed_artist = str(cached_art_tracks[0].get("artist") or "")

        candidates: List[Dict[str, Any]] = []

        if not http_client.is_offline_mode():
            # 1. Deezer native artist top + related artists' top tracks (true Spotify Radio feel)
            dz_artist_raw = ""
            if resolved_artist_id.startswith("dz:"):
                dz_artist_raw = resolved_artist_id.split(":", 1)[1]
            elif seed_artist:
                try:
                    primary = lrc_parser.extract_primary_artist(seed_artist)
                    found_arts = deezer.search_artists(primary, limit=1)
                    if found_arts:
                        dz_artist_raw = str(found_arts[0]["id"]).split(":", 1)[-1]
                except Exception:
                    dz_artist_raw = ""

            if dz_artist_raw:
                pool = concurrent.futures.ThreadPoolExecutor(max_workers=5)
                try:
                    f_top = pool.submit(deezer.get_artist_top, dz_artist_raw, 10)
                    f_rel = pool.submit(deezer.get_artist_related, dz_artist_raw, 6)
                    concurrent.futures.wait([f_top, f_rel], timeout=3.5)
                    if f_top.done():
                        candidates.extend(f_top.result() or [])
                    rel_artists = f_rel.result() if f_rel.done() else []
                    rel_futs = [
                        pool.submit(deezer.get_artist_top, str(ra["id"]).split(":", 1)[-1], 5)
                        for ra in (rel_artists or [])[:4]
                        if ra.get("id")
                    ]
                    if rel_futs:
                        concurrent.futures.wait(rel_futs, timeout=3.0)
                        for rf in rel_futs:
                            if rf.done():
                                try:
                                    candidates.extend(rf.result() or [])
                                except Exception:
                                    pass
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)

            # 2. Search fallback if related artists didn't yield enough tracks
            if len(candidates) < max_items // 2 and (seed_artist or seed_title):
                q = lrc_parser.extract_primary_artist(seed_artist) or seed_title
                try:
                    candidates.extend(deezer.search_tracks(q, limit=18) or [])
                except Exception:
                    pass
                if len(candidates) < 8:
                    try:
                        candidates.extend(apple_itunes.search_songs(q, "us", 12) or [])
                    except Exception:
                        pass

        # 3. Graceful offline / cached fallback so Radio & Autoplay ALWAYS work
        if len(candidates) < 5:
            home = self.get_home("global")
            for sec in home.get("sections") or []:
                if sec.get("type") == "tracks":
                    candidates.extend(sec.get("items") or [])
            if len(candidates) < 5:
                candidates.extend(dict(t) for t in offline_catalog.OFFLINE_TRACKS)

        final_tracks = self._dedup_tracks_keep_order(
            candidates, exclude_ids=exclude_ids, limit=max_items
        )
        if final_tracks:
            self.db.upsert_tracks(final_tracks)

        return {
            "seedType": seed_type,
            "seedTitle": seed_title or seed_artist or "Sonify Radio",
            "seedArtist": seed_artist,
            "tracks": self.db.annotate_tracks(final_tracks, owner_id=owner_id),
        }

    def get_made_for_you(self, region: str = "global", owner_id: str = "default") -> Dict[str, Any]:
        region_key = "vn" if (region or "").lower() == "vn" else "global"
        now = time.time()

        history_items = self.db.get_history(limit=30, owner_id=owner_id)
        liked_tracks, _ = self.db.get_liked_tracks(owner_id=owner_id)
        followed_artists, _ = self.db.get_followed_artists(owner_id=owner_id)

        fingerprint = (
            f"{owner_id}:{region_key}:{len(history_items)}:{len(liked_tracks)}:{len(followed_artists)}:"
            f"{history_items[0]['track']['id'] if history_items else ''}"
        )

        if not http_client.is_offline_mode():
            with self._lock:
                cached = self._mix_cache.get(fingerprint)
                if cached and (now - cached[0]) < config.HOME_CACHE_TTL:
                    mixes_copy = []
                    for m in cached[1].get("mixes") or []:
                        mc = dict(m)
                        mc["tracks"] = self.db.annotate_tracks(
                            mc.get("tracks") or [], owner_id=owner_id
                        )
                        mixes_copy.append(mc)
                    return {"mixes": mixes_copy}

        home = self.get_home(region_key, owner_id=owner_id)
        home_global: List[Dict[str, Any]] = []
        home_vn: List[Dict[str, Any]] = []
        home_indie: List[Dict[str, Any]] = []
        for sec in home.get("sections") or []:
            if sec.get("id") == "trending-global":
                home_global = [t for t in (sec.get("items") or []) if not _is_genre_spam(t)]
            elif sec.get("id") == "trending-vn":
                home_vn = [t for t in (sec.get("items") or []) if not _is_genre_spam(t)]
            elif sec.get("id") == "audius-trending":
                home_indie = [t for t in (sec.get("items") or []) if not _is_genre_spam(t)]

        played_tracks = [h["track"] for h in history_items if h.get("track")]
        played_ids = {t["id"] for t in played_tracks if t.get("id")}
        user_seed_tracks = self._dedup_tracks_keep_order(played_tracks + liked_tracks, limit=20)

        # Daily Mix 1: top played/liked artists + radio expansion (cold-start: trending-global + trending-vn)
        dm1_pool: List[Dict[str, Any]] = list(user_seed_tracks[:8])
        if user_seed_tracks:
            radio1 = self.get_radio(track_id=user_seed_tracks[0]["id"], limit=14)
            dm1_pool.extend(t for t in (radio1.get("tracks") or []) if not _is_genre_spam(t))
        elif followed_artists:
            radio1 = self.get_radio(artist_id=followed_artists[0]["id"], limit=16)
            dm1_pool.extend(t for t in (radio1.get("tracks") or []) if not _is_genre_spam(t))
        dm1_pool.extend(home_global if region_key == "global" else home_vn)
        dm1_pool.extend(home_vn if region_key == "global" else home_global)
        dm1_tracks = self._dedup_tracks_keep_order(dm1_pool, limit=18)

        # Daily Mix 2: secondary artist/vibe + regional blend + direct full-track Audius
        dm2_pool: List[Dict[str, Any]] = []
        if len(user_seed_tracks) >= 2:
            dm2_pool.extend(user_seed_tracks[1:6])
            radio2 = self.get_radio(track_id=user_seed_tracks[1]["id"], limit=12)
            dm2_pool.extend(t for t in (radio2.get("tracks") or []) if not _is_genre_spam(t))
        dm2_pool.extend(home_vn if region_key == "global" else home_global)
        dm2_pool.extend(home_global[8:] if region_key == "global" else home_vn[8:])
        dm2_pool.extend(home_indie)
        dm2_tracks = self._dedup_tracks_keep_order(dm2_pool, limit=18)

        # Discover Weekly: tracks user hasn't played yet from related artists / charts / indie
        dw_pool: List[Dict[str, Any]] = []
        if len(followed_artists) >= 1:
            dw_radio = self.get_radio(artist_id=followed_artists[-1]["id"], limit=15)
            dw_pool.extend(t for t in (dw_radio.get("tracks") or []) if not _is_genre_spam(t))
        dw_pool.extend(home_global[5:])
        dw_pool.extend(home_vn[5:])
        dw_pool.extend(home_indie)
        dw_pool.extend(home_global[:5])
        dw_pool.extend(home_vn[:5])
        dw_tracks = self._dedup_tracks_keep_order(dw_pool, exclude_ids=played_ids, limit=18)
        if not dw_tracks:
            dw_tracks = self._dedup_tracks_keep_order(dw_pool, limit=18)

        # Release Radar: newest releases + followed artists + fresh charts
        rr_pool: List[Dict[str, Any]] = []
        if followed_artists and not http_client.is_offline_mode():
            for fa in followed_artists[:2]:
                if str(fa["id"]).startswith("dz:"):
                    rr_pool.extend(deezer.get_artist_top(str(fa["id"]).split(":", 1)[1], limit=6))
        rr_pool.extend(home_global[:10])
        rr_pool.extend(home_vn[:10])
        rr_tracks = self._dedup_tracks_keep_order(rr_pool, limit=18)

        def _artist_preview(tr_list: List[Dict[str, Any]], fallback: str) -> str:
            names: List[str] = []
            for t in tr_list:
                a = str(t.get("artist") or "").strip()
                if a and a not in names:
                    names.append(a)
                if len(names) >= 3:
                    break
            return ", ".join(names) + " and more" if names else fallback

        def _first_cover(tr_list: List[Dict[str, Any]]) -> str:
            for t in tr_list:
                c = t.get("coverLarge") or t.get("coverSmall") or ""
                if c:
                    return str(c)
            return ""

        mixes = [
            {
                "id": "daily-mix-1",
                "title": "Daily Mix 1",
                "subtitle": _artist_preview(dm1_tracks, "Your favorite tracks and artists"),
                "description": "Made for you from your recent listens, liked songs, and similar artists.",
                "gradient": ["#1DB954", "#0D3B22"],
                "cover": _first_cover(dm1_tracks),
                "tracks": dm1_tracks,
            },
            {
                "id": "daily-mix-2",
                "title": "Daily Mix 2",
                "subtitle": _artist_preview(dm2_tracks, "Fresh global & V-Pop rotation"),
                "description": "A second personalized vibe mixing regional favorites and indie gems.",
                "gradient": ["#7C3AED", "#1E1B4B"],
                "cover": _first_cover(dm2_tracks),
                "tracks": dm2_tracks,
            },
            {
                "id": "discover-weekly",
                "title": "Discover Weekly",
                "subtitle": _artist_preview(dw_tracks, "Fresh discoveries you haven't played yet"),
                "description": "Your weekly mixtape of fresh music and related artists you haven't heard yet.",
                "gradient": ["#E11D48", "#3B0A1F"],
                "cover": _first_cover(dw_tracks),
                "tracks": dw_tracks,
            },
            {
                "id": "release-radar",
                "title": "Release Radar",
                "subtitle": _artist_preview(rr_tracks, "Catch all the latest releases"),
                "description": "New music from artists you follow plus fresh chart singles picked for you.",
                "gradient": ["#0284C7", "#0C2D48"],
                "cover": _first_cover(rr_tracks),
                "tracks": rr_tracks,
            },
        ]

        payload = {"mixes": mixes}
        with self._lock:
            if len(self._mix_cache) > 16:
                self._mix_cache.pop(next(iter(self._mix_cache)))
            self._mix_cache[fingerprint] = (now, payload)

        annotated_mixes = []
        for m in mixes:
            mc = dict(m)
            mc["tracks"] = self.db.annotate_tracks(m["tracks"], owner_id=owner_id)
            annotated_mixes.append(mc)
        return {"mixes": annotated_mixes}

    def get_playlist_recommendations(
        self, playlist_id: str, limit: int = 10, owner_id: str = "default"
    ) -> Optional[Dict[str, Any]]:
        pl_data = self.db.get_playlist(playlist_id, owner_id=owner_id)
        if not pl_data:
            return None
        max_items = max(1, min(int(limit or 10), 25))
        existing_tracks = pl_data.get("tracks") or []
        existing_ids = {t["id"] for t in existing_tracks if t.get("id")}

        candidates: List[Dict[str, Any]] = []
        if existing_tracks:
            # Seed radio from up to 2 distinct artists inside the playlist
            seen_seed_artists: set[str] = set()
            for tr in existing_tracks:
                art_key = lrc_parser.normalize_key(tr.get("artist", ""))
                if art_key in seen_seed_artists:
                    continue
                seen_seed_artists.add(art_key)
                radio = self.get_radio(track_id=tr["id"], limit=max_items + 4)
                candidates.extend(radio.get("tracks") or [])
                if len(seen_seed_artists) >= 2:
                    break
        else:
            home = self.get_home("global", owner_id=owner_id)
            for sec in home.get("sections") or []:
                if sec.get("type") == "tracks":
                    candidates.extend(sec.get("items") or [])

        recs = self._dedup_tracks_keep_order(
            candidates, exclude_ids=existing_ids, limit=max_items
        )
        if recs:
            self.db.upsert_tracks(recs)
        return {
            "playlistId": playlist_id,
            "tracks": self.db.annotate_tracks(recs, owner_id=owner_id),
        }

    def get_track_detail(self, track_id: str, owner_id: str = "default") -> Optional[Dict[str, Any]]:
        if not track_id:
            return None
        track = self.db.get_track(track_id, owner_id=owner_id)
        if not track:
            prefix, _, raw_id = track_id.partition(":")
            if prefix == "local":
                track = offline_catalog.get_offline_track(track_id)
            elif not http_client.is_offline_mode():
                try:
                    if prefix == "dz" and raw_id:
                        track = deezer.get_track(raw_id)
                    elif prefix == "it" and raw_id:
                        track = apple_itunes.get_track(raw_id)
                    elif prefix == "au" and raw_id:
                        track = audius.get_track(raw_id)
                    elif prefix == "js" and raw_id:
                        track = jiosaavn.get_song_details(raw_id)
                except Exception:
                    track = None
            if track:
                self.db.upsert_track(track)
                track = self.db.get_track(track_id, owner_id=owner_id) or track
        if not track:
            return None

        radio = self.get_radio(track_id=track_id, limit=10, owner_id=owner_id)
        return {
            "track": track,
            "relatedTracks": radio.get("tracks") or [],
        }
