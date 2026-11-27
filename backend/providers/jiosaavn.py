"""JioSaavn API provider with DES-ECB 320kbps decryption and mirror fallback."""

from __future__ import annotations

import html
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from backend import config, crypto_des, http_client, lrc_parser

DIRECT_API = "https://www.jiosaavn.com/api.php"
MIRROR_API = "https://saavn.sumit.co/api/search/songs"
MIN_TRACK_BYTES = 350_000

is_valid_full_duration = lrc_parser.is_valid_full_duration

_NOISE_KEYWORDS = (
    "nightcore",
    "techno",
    "ambient",
    "cover",
    "remix",
    "karaoke",
    "instrumental",
    "slowed",
    "sped up",
    "lofi",
    "lo-fi",
    "piano",
    "8d",
    "reverb",
    "tribute",
    "lullaby",
    "acoustic",
    "mashup",
)
_NOISE_PATTERNS = [
    (kw, re.compile(rf"(?<!\w){re.escape(kw)}(?!\w)", re.IGNORECASE))
    for kw in _NOISE_KEYWORDS
]


def _unescape(val: Any) -> str:
    return html.unescape(str(val or "")).strip()


def _extract_artists(song: Dict[str, Any]) -> str:
    more = song.get("more_info") or {}
    artist_map = more.get("artistMap") or {}
    primary = artist_map.get("primary_artists") or []
    if primary:
        names = [_unescape(a.get("name")) for a in primary if a.get("name")]
        if names:
            return ", ".join(names)
    if song.get("primary_artists"):
        return _unescape(song.get("primary_artists"))
    if song.get("subtitle"):
        return _unescape(song.get("subtitle")).split(" - ")[0].strip()
    return "Unknown Artist"


def _normalize_direct_song(song: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sid = song.get("id")
    if not sid:
        return None
    more = song.get("more_info") or {}
    enc_url = more.get("encrypted_media_url") or song.get("encrypted_media_url") or ""
    img = str(song.get("image") or "")
    cover_small = img.replace("150x150", "250x250").replace("50x50", "250x250")
    cover_large = img.replace("150x150", "500x500").replace("50x50", "500x500")
    duration_raw = more.get("duration") or song.get("duration") or 0
    try:
        duration = int(duration_raw)
    except Exception:
        duration = 0
    is_320 = str(more.get("320kbps") or song.get("320kbps") or "").lower() == "true"

    return {
        "id": f"js:{sid}",
        "source": "jiosaavn",
        "title": _unescape(song.get("title") or song.get("song") or "Untitled"),
        "artist": _extract_artists(song),
        "artistId": "",
        "album": _unescape(more.get("album") or song.get("album") or ""),
        "albumId": "",
        "duration": duration,
        "coverSmall": cover_small,
        "coverLarge": cover_large,
        "explicit": str(song.get("explicit_content") or "0") == "1",
        "releaseDate": str(more.get("release_date") or song.get("year") or ""),
        "previewUrl": "",
        "streamUrl": f"/api/stream/js:{sid}",
        "_extra": {
            "encrypted_media_url": enc_url,
            "320kbps": is_320,
        },
    }


def _normalize_mirror_song(song: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sid = song.get("id")
    if not sid:
        return None
    images = song.get("image") or []
    cover_small = ""
    cover_large = ""
    if isinstance(images, list) and images:
        cover_small = str(images[min(1, len(images) - 1)].get("url") or "")
        cover_large = str(images[-1].get("url") or cover_small)

    artists_obj = song.get("artists") or {}
    primary = artists_obj.get("primary") or []
    artist_name = ", ".join(_unescape(a.get("name")) for a in primary if a.get("name")) or "Unknown Artist"

    downloads = song.get("downloadUrl") or []
    direct_urls: List[str] = []
    if isinstance(downloads, list):
        # Prefer 320kbps -> 160kbps -> 96kbps
        def _q_rank(item: Dict[str, Any]) -> int:
            q = str(item.get("quality") or "")
            if "320" in q:
                return 3
            if "160" in q:
                return 2
            if "96" in q:
                return 1
            return 0

        for item in sorted(downloads, key=_q_rank, reverse=True):
            u = item.get("url")
            if u:
                direct_urls.append(str(u))

    album_obj = song.get("album") or {}
    return {
        "id": f"js:{sid}",
        "source": "jiosaavn",
        "title": _unescape(song.get("name") or "Untitled"),
        "artist": artist_name,
        "artistId": "",
        "album": _unescape(album_obj.get("name") if isinstance(album_obj, dict) else ""),
        "albumId": "",
        "duration": int(song.get("duration") or 0),
        "coverSmall": cover_small,
        "coverLarge": cover_large,
        "explicit": bool(song.get("explicitContent")),
        "releaseDate": str(song.get("releaseDate") or song.get("year") or ""),
        "previewUrl": "",
        "streamUrl": f"/api/stream/js:{sid}",
        "_extra": {
            "direct_urls": direct_urls,
        },
    }


def search_songs(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    q = query.strip()
    if not q:
        return []
    params = urllib.parse.urlencode(
        {
            "__call": "search.getResults",
            "_format": "json",
            "_marker": "0",
            "api_version": "4",
            "ctx": "web6dot0",
            "q": q,
            "p": "1",
            "n": str(limit),
        }
    )
    last_exc: Optional[Exception] = None
    succeeded = False
    try:
        data = http_client.fetch_json(f"{DIRECT_API}?{params}", timeout=config.PROVIDER_TIMEOUT)
        succeeded = True
        results = data.get("results") or []
        tracks = [t for t in (_normalize_direct_song(s) for s in results) if t]
        if tracks:
            return tracks
    except Exception as exc:
        last_exc = exc

    # Fallback mirror
    try:
        mparams = urllib.parse.urlencode({"query": q, "limit": str(min(limit, 8))})
        mdata = http_client.fetch_json(f"{MIRROR_API}?{mparams}", timeout=config.PROVIDER_TIMEOUT)
        succeeded = True
        mresults = (mdata.get("data") or {}).get("results") or []
        return [t for t in (_normalize_mirror_song(s) for s in mresults) if t]
    except Exception as exc:
        last_exc = exc

    if not succeeded and last_exc is not None:
        raise last_exc
    return []


def get_song_details(raw_id: str) -> Optional[Dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "__call": "song.getDetails",
            "_format": "json",
            "_marker": "0",
            "api_version": "4",
            "ctx": "web6dot0",
            "pids": raw_id,
        }
    )
    data = http_client.fetch_json(f"{DIRECT_API}?{params}", timeout=config.PROVIDER_TIMEOUT)
    songs = data.get("songs") or []
    if songs and isinstance(songs, list):
        return _normalize_direct_song(songs[0])
    if raw_id in data and isinstance(data[raw_id], dict):
        return _normalize_direct_song(data[raw_id])
    return None


def resolve_candidate_urls(extra: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    enc = extra.get("encrypted_media_url")
    if enc:
        try:
            decrypted = crypto_des.decrypt_media_url(enc)
            prefer_320 = bool(extra.get("320kbps", True))
            for cand in crypto_des.upgrade_bitrate_candidates(decrypted, prefer_320=prefer_320):
                if cand not in urls:
                    urls.append(cand)
        except Exception:
            pass
    for u in extra.get("direct_urls") or []:
        if u and u not in urls:
            urls.append(u)
    return urls


def probe_best_url(
    urls: List[str], min_total_bytes: int = MIN_TRACK_BYTES
) -> Optional[Tuple[str, str]]:
    """Return (reachable_url, content_type) for the highest-bitrate playable JioSaavn URL."""
    for url in urls:
        ok, _, hdrs, final_url = http_client.probe_url(
            url, timeout=config.STREAM_PROBE_TIMEOUT, min_total_bytes=min_total_bytes
        )
        if ok:
            ctype = hdrs.get("content-type", "audio/mp4").split(";")[0].strip()
            if not ctype.startswith("audio/") and ctype != "application/octet-stream":
                ctype = "audio/mp4"
            return final_url, ctype
    return None


def score_match(
    cand_title: str,
    cand_artist: str,
    cand_duration: int,
    target_title: str,
    target_artist: str,
    target_duration: int = 0,
) -> float:
    title_sim = lrc_parser.token_similarity(
        lrc_parser.clean_track_title(cand_title),
        lrc_parser.clean_track_title(target_title),
    )
    artist_sim = lrc_parser.token_similarity(cand_artist, target_artist)
    if target_duration > 0 and cand_duration > 0:
        dur_prox = max(0.0, 1.0 - abs(cand_duration - target_duration) / 25.0)
    else:
        dur_prox = 0.7
    return 0.55 * title_sim + 0.30 * artist_sim + 0.15 * dur_prox


def _noise_penalty(cand_text: str, target_text: str) -> float:
    penalty = 0.0
    for _, pat in _NOISE_PATTERNS:
        if pat.search(cand_text) and not pat.search(target_text):
            penalty += 0.35
    return penalty


def find_best_stream(
    title: str,
    artist: str,
    duration: int = 0,
    min_score: float = 0.72,
    relaxed: bool = False,
) -> Optional[Tuple[str, str]]:
    clean_title = lrc_parser.clean_track_title(title)
    primary_artist = lrc_parser.extract_primary_artist(artist)
    query = f"{primary_artist} {clean_title}".strip()
    if not query:
        return None

    eff_min_score = 0.48 if (relaxed and min_score == 0.72) else min_score

    queries = [query]
    if any(ord(ch) > 127 for ch in query):
        ascii_q = lrc_parser.normalize_key(query).strip()
        if ascii_q and ascii_q.lower() != query.lower():
            queries.append(ascii_q)
    if relaxed:
        for rq in (
            f"{primary_artist} {clean_title} audio".strip(),
            f"{clean_title} {primary_artist}".strip(),
            clean_title.strip() if len(clean_title.strip()) >= 5 else "",
        ):
            if rq and all(rq.lower() != q.lower() for q in queries):
                queries.append(rq)

    target_context = f"{title or ''} {artist or ''}"
    target_has_artist = bool(
        primary_artist and primary_artist.lower() not in ("unknown artist", "various artists")
    )
    norm_pa = lrc_parser.normalize_key(primary_artist)
    norm_pa_compact = norm_pa.replace(" ", "")

    scored: List[Tuple[float, Dict[str, Any]]] = []
    seen_ids: set[str] = set()

    for q in queries:
        try:
            candidates = search_songs(q, limit=8)
        except Exception:
            candidates = []

        for cand in candidates:
            cid = str(cand.get("id") or "")
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)

            cand_title = str(cand.get("title") or "")
            cand_artist = str(cand.get("artist") or "")
            cand_dur = int(cand.get("duration") or 0)

            # Universal anti-short-clip floor (>= 60s unless genuine short interlude)
            if not is_valid_full_duration(cand_dur, duration):
                continue
            if relaxed and cand_dur > 900:
                continue

            # Hard Gate 1: Title similarity (>= 0.60 strict, >= 0.52 relaxed)
            title_sim = max(
                lrc_parser.token_similarity(lrc_parser.clean_track_title(cand_title), clean_title),
                lrc_parser.token_similarity(cand_title, clean_title),
            )
            if title_sim < (0.52 if relaxed else 0.60):
                continue

            # Hard Gate 2: Artist similarity (with multi-artist substring match & Pass-2 rescue)
            artist_sim = max(
                lrc_parser.token_similarity(cand_artist, primary_artist),
                lrc_parser.token_similarity(cand_artist, artist or primary_artist),
            )
            norm_ca = lrc_parser.normalize_key(cand_artist)
            if norm_pa and len(norm_pa) >= 2 and (
                norm_pa in norm_ca
                or (len(norm_pa_compact) >= 3 and norm_pa_compact in norm_ca.replace(" ", ""))
            ):
                artist_sim = max(artist_sim, 0.70)

            if relaxed:
                if (
                    artist_sim >= 0.25
                    or lrc_parser.has_primary_artist_token(primary_artist, f"{cand_title} {cand_artist}")
                    or (
                        title_sim >= 0.85
                        and (duration <= 30 or (cand_dur > 0 and abs(cand_dur - duration) <= 45))
                    )
                ):
                    artist_sim = max(artist_sim, 0.50)
                elif target_has_artist:
                    continue
            else:
                if target_has_artist and artist_sim < 0.50:
                    continue

            # Hard Gate 3: Duration within max(25s, 20%) in strict mode; [60, 900] in relaxed mode
            if not relaxed and duration > 30 and cand_dur > 0:
                max_diff = max(25, int(duration * 0.20))
                if abs(cand_dur - duration) > max_diff:
                    continue

            dur_prox = (
                max(0.25 if relaxed else 0.0, 1.0 - abs(cand_dur - duration) / (90.0 if relaxed else 25.0))
                if (duration > 0 and cand_dur > 0)
                else 0.7
            )
            sc = 0.55 * title_sim + 0.30 * artist_sim + 0.15 * dur_prox
            noise_pen = _noise_penalty(f"{cand_title} {cand_artist}", target_context)
            if relaxed:
                noise_pen *= 0.25
            sc -= noise_pen
            if sc >= eff_min_score:
                scored.append((sc, cand))

        if scored:
            break

    scored.sort(key=lambda item: item[0], reverse=True)
    for _, cand in scored[:3]:
        urls = resolve_candidate_urls(cand.get("_extra") or {})
        found = probe_best_url(urls)
        if found:
            return found
    return None
