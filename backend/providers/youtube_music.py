"""YouTube Music InnerTube search/player/lyrics provider with Piped/Invidious & optional yt-dlp."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend import config, http_client, lrc_parser

YTM_ORIGIN = "https://music.youtube.com"
YTM_SEARCH_URL = "https://music.youtube.com/youtubei/v1/search"
YTM_NEXT_URL = "https://music.youtube.com/youtubei/v1/next"
YTM_BROWSE_URL = "https://music.youtube.com/youtubei/v1/browse"
YT_PLAYER_URL = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"

_PENALTY_KEYWORDS = (
    "1 hour",
    "10 hours",
    "slowed",
    "sped up",
    "nightcore",
    "cover",
    "karaoke",
    "8d audio",
    "8d",
    "reverb",
    "remix",
    "live",
    "piano",
    "instrumental",
    "acoustic",
    "lofi",
    "lo-fi",
    "mashup",
    "teaser",
    "trailer",
    "reaction",
    "parody",
    "beat",
    "guitar",
    "violin",
    "tribute",
    "lullaby",
)
_PENALTY_PATTERNS = [
    (kw, re.compile(rf"(?<!\w){re.escape(kw)}(?!\w)", re.IGNORECASE))
    for kw in _PENALTY_KEYWORDS
]
_ARTIST_SUFFIX_RE = re.compile(
    r"(?:\s*[-–—]\s*topic\b|\bofficial\s*(?:music\s*)?channel\b|\bofficial\b|\bvevo\b)",
    re.IGNORECASE,
)

_PIPED_SEED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.leptons.xyz",
    "https://api.piped.private.coffee",
]

_INVIDIOUS_SEED_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://yewtu.be",
    "https://invidious.jing.rocks",
]

_POOL_LOCK = threading.Lock()
_FAILED_INSTANCES: Dict[str, float] = {}
_INVIDIOUS_DYNAMIC: List[str] = []
_INVIDIOUS_FETCHED_AT: float = 0.0


def _ytm_context() -> Dict[str, Any]:
    return {
        "client": {
            "clientName": "WEB_REMIX",
            "clientVersion": "1.20260301.01.00",
            "hl": "en",
            "gl": "US",
        }
    }


def _ytm_headers() -> Dict[str, str]:
    return {
        "Origin": YTM_ORIGIN,
        "Referer": f"{YTM_ORIGIN}/",
        "Content-Type": "application/json",
        "User-Agent": config.BROWSER_USER_AGENT,
    }


def _parse_duration_text(text: str) -> int:
    m = re.match(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*$", text or "")
    if not m:
        return 0
    hrs = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    secs = int(m.group(3) or 0)
    return hrs * 3600 + mins * 60 + secs


def _iter_renderers(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, dict):
        if "musicResponsiveListItemRenderer" in obj and isinstance(obj["musicResponsiveListItemRenderer"], dict):
            yield obj["musicResponsiveListItemRenderer"]
        for val in obj.values():
            yield from _iter_renderers(val)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_renderers(item)


def _extract_runs_text(col: Dict[str, Any]) -> List[str]:
    runs = (
        col.get("musicResponsiveListItemFlexColumnRenderer", {})
        .get("text", {})
        .get("runs", [])
    )
    return [str(r.get("text") or "").strip() for r in runs if str(r.get("text") or "").strip()]


def _parse_ytm_item(renderer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Locate videoId from overlay or playlistItemData or navigationEndpoint
    video_id = (renderer.get("playlistItemData") or {}).get("videoId")
    if not video_id:
        video_id = (
            (renderer.get("overlay") or {})
            .get("musicItemThumbnailOverlayRenderer", {})
            .get("content", {})
            .get("musicPlayButtonRenderer", {})
            .get("playNavigationEndpoint", {})
            .get("watchEndpoint", {})
            .get("videoId")
        )
    if not video_id:
        flex_cols = renderer.get("flexColumns") or []
        for fc in flex_cols:
            runs = (
                fc.get("musicResponsiveListItemFlexColumnRenderer", {})
                .get("text", {})
                .get("runs", [])
            )
            for r in runs:
                vid = (r.get("navigationEndpoint") or {}).get("watchEndpoint", {}).get("videoId")
                if vid:
                    video_id = vid
                    break
            if video_id:
                break
    if not video_id or not isinstance(video_id, str):
        return None

    flex_cols = renderer.get("flexColumns") or []
    title_runs = _extract_runs_text(flex_cols[0]) if len(flex_cols) > 0 else []
    meta_runs = _extract_runs_text(flex_cols[1]) if len(flex_cols) > 1 else []
    title = "".join(title_runs).strip()
    if not title:
        return None

    # Meta runs typically look like ["Song", "•", "Artist", "•", "Album", "•", "3:20"]
    tokens = [t for t in meta_runs if t not in ("•", "-", "·", "|", ",")]
    duration = 0
    clean_tokens: List[str] = []
    for tok in tokens:
        d = _parse_duration_text(tok)
        if d > 0:
            duration = d
        elif tok.lower() not in ("song", "video", "single", "ep"):
            clean_tokens.append(tok)

    artist = clean_tokens[0] if clean_tokens else "Unknown Artist"
    album = clean_tokens[1] if len(clean_tokens) > 1 else ""

    thumbs = (
        (renderer.get("thumbnail") or {})
        .get("musicThumbnailRenderer", {})
        .get("thumbnail", {})
        .get("thumbnails", [])
    )
    cover_small = str(thumbs[0].get("url") or "") if thumbs else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    cover_large = str(thumbs[-1].get("url") or "") if thumbs else cover_small
    if "lh3.googleusercontent.com" in cover_large:
        cover_large = re.sub(r"=w\d+-h\d+", "=w544-h544", cover_large)

    return {
        "id": f"yt:{video_id}",
        "source": "youtube_music",
        "title": title,
        "artist": artist,
        "artistId": "",
        "album": album,
        "albumId": "",
        "duration": duration,
        "coverSmall": cover_small,
        "coverLarge": cover_large,
        "explicit": False,
        "releaseDate": "",
        "previewUrl": "",
        "streamUrl": f"/api/stream/yt:{video_id}",
        "_extra": {"videoId": video_id},
    }


def search_songs(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    q = query.strip()
    if not q:
        return []
    body = {
        "context": _ytm_context(),
        "query": q,
        "params": "EgWKAQIIAWoKEAkQBRAKEAMQBA%3D%3D",
    }
    try:
        data = http_client.fetch_json(
            YTM_SEARCH_URL,
            method="POST",
            headers=_ytm_headers(),
            json_body=body,
            timeout=config.PROVIDER_TIMEOUT,
        )
    except Exception:
        return []

    seen: set[str] = set()
    tracks: List[Dict[str, Any]] = []
    for renderer in _iter_renderers(data):
        item = _parse_ytm_item(renderer)
        if item and item["id"] not in seen:
            seen.add(item["id"])
            tracks.append(item)
            if len(tracks) >= limit:
                break
    return tracks


def _clean_candidate_artist(artist: str) -> str:
    cleaned = _ARTIST_SUFFIX_RE.sub("", artist or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")
    return cleaned or (artist or "").strip()


def _penalty_score(cand_text: str, target_text: str) -> float:
    penalty = 0.0
    for _, pat in _PENALTY_PATTERNS:
        if pat.search(cand_text) and not pat.search(target_text):
            penalty += 0.35
    return penalty


def find_best_video_id(title: str, artist: str, duration: int = 0) -> Optional[str]:
    clean_title = lrc_parser.clean_track_title(title)
    primary_artist = lrc_parser.extract_primary_artist(artist)
    query = f"{primary_artist} {clean_title}".strip()
    if not query:
        return None

    candidates = search_songs(query, limit=8)
    if not candidates:
        return None

    target_context = f"{title or ''} {artist or ''}"
    target_has_artist = bool(
        primary_artist and primary_artist.lower() not in ("unknown artist", "various artists")
    )
    norm_pa = lrc_parser.normalize_key(primary_artist)

    best_score = -1.0
    best_vid: Optional[str] = None

    for cand in candidates:
        vid = (cand.get("_extra") or {}).get("videoId")
        if not vid:
            continue

        cand_title = str(cand.get("title") or "")
        cand_artist_raw = str(cand.get("artist") or "")
        cand_artist_clean = _clean_candidate_artist(cand_artist_raw)
        cand_dur = int(cand.get("duration") or 0)

        # Asymmetric duration & anti-unwanted-version gates
        if not lrc_parser.is_duration_compatible(
            cand_dur, duration, relaxed=False, mv_capable=True
        ):
            continue
        ver_class = lrc_parser.classify_unwanted_version(
            f"{cand_title} {cand_artist_clean}", target_context
        )
        if ver_class != "ok":
            continue

        # Hard Gate 1: Title similarity >= 0.55
        title_sim = max(
            lrc_parser.token_similarity(lrc_parser.clean_track_title(cand_title), clean_title),
            lrc_parser.token_similarity(cand_title, clean_title),
        )
        if title_sim < 0.55:
            continue

        # Hard Gate 2: Artist similarity >= 0.40 when target artist is known
        artist_sim = max(
            lrc_parser.token_similarity(cand_artist_clean, primary_artist),
            lrc_parser.token_similarity(cand_artist_clean, artist or primary_artist),
        )
        if norm_pa and len(norm_pa) >= 2:
            norm_ca = lrc_parser.normalize_key(cand_artist_clean)
            norm_ct = lrc_parser.normalize_key(cand_title)
            if (
                f" {norm_pa} " in f" {norm_ca} "
                or f" {norm_pa} " in f" {norm_ct} "
                or (lrc_parser._has_cjk(norm_pa) and (norm_pa in norm_ca or norm_pa in norm_ct))
            ):
                artist_sim = max(artist_sim, 0.55)
        if target_has_artist and artist_sim < 0.40:
            continue

        if duration > 0 and cand_dur > 0:
            dur_prox = max(0.0, 1.0 - abs(cand_dur - duration) / 30.0)
        else:
            dur_prox = 0.65

        score = 0.55 * title_sim + 0.30 * artist_sim + 0.15 * dur_prox
        if duration > 0 and cand_dur > 0 and abs(cand_dur - duration) <= 5:
            score += 0.08
        if cand_artist_raw.lower().endswith(" - topic"):
            score += 0.03

        score -= _penalty_score(f"{cand_title} {cand_artist_clean}", target_context)

        if score > best_score:
            best_score = score
            best_vid = vid

    return best_vid if best_score >= 0.62 else None


_COOKIES_LOCK = threading.Lock()
_COOKIES_B64_HASH: str = ""
_COOKIES_FILE_PATH: Path = Path("/tmp/sonify/yt_cookies.txt")


def has_cloud_youtube_credentials() -> bool:
    """Return True when authenticated/proxied YouTube access is configured for cloud deployments."""
    if os.environ.get("SONIFY_FORCE_CLOUD_YOUTUBE", "").strip().lower() in ("1", "true", "yes"):
        return True
    if os.environ.get("YTDLP_COOKIES_B64", "").strip():
        return True
    cookies_path = os.environ.get("YTDLP_COOKIES_PATH", "").strip()
    if cookies_path and Path(cookies_path).is_file():
        return True
    if os.environ.get("YTDLP_PROXY", "").strip():
        return True
    return False


def _get_ytdlp_extra_args() -> List[str]:
    """Return optional --cookies and --proxy arguments when YTDLP_COOKIES_B64 / YTDLP_COOKIES_PATH / YTDLP_PROXY are set."""
    global _COOKIES_B64_HASH
    extra_args: List[str] = []

    cookies_b64 = os.environ.get("YTDLP_COOKIES_B64", "").strip()
    if cookies_b64:
        with _COOKIES_LOCK:
            try:
                if cookies_b64 != _COOKIES_B64_HASH or not _COOKIES_FILE_PATH.is_file():
                    raw = base64.b64decode(cookies_b64)
                    _COOKIES_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
                    _COOKIES_FILE_PATH.write_bytes(raw)
                    try:
                        os.chmod(_COOKIES_FILE_PATH, 0o600)
                    except OSError:
                        pass
                    _COOKIES_B64_HASH = cookies_b64
                extra_args.extend(["--cookies", str(_COOKIES_FILE_PATH)])
            except Exception:
                pass
    else:
        cookies_path = os.environ.get("YTDLP_COOKIES_PATH", "").strip()
        if cookies_path and Path(cookies_path).is_file():
            extra_args.extend(["--cookies", cookies_path])

    proxy_val = os.environ.get("YTDLP_PROXY", "").strip()
    if proxy_val:
        extra_args.extend(["--proxy", proxy_val])

    return extra_args


def _resolve_via_yt_dlp(video_id: str) -> Optional[Tuple[str, str, Dict[str, str]]]:
    if not shutil.which("yt-dlp"):
        return None
    # On cloud datacenter IPs without cookies or proxy, anonymous yt-dlp hits LOGIN_REQUIRED
    # and wastes 7-10s before reaching SoundCloud/JioSaavn — skip immediately unless configured.
    if config.is_cloud_env() and not has_cloud_youtube_credentials():
        return None
    try:
        cmd = [
            "yt-dlp",
            "-J",
            "--no-download",
            "--no-playlist",
            "--no-warnings",
            "--no-check-formats",
            "--extractor-args",
            "youtube:player_client=android,tv_embedded,ios",
            "--socket-timeout",
            "6",
        ]
        cmd.extend(_get_ytdlp_extra_args())
        cmd.append(f"https://www.youtube.com/watch?v={video_id}")
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            return None
        info = json.loads(proc.stdout)
        formats = info.get("formats") or []

        audio_only: List[Tuple[int, float, str, str, Dict[str, str]]] = []
        progressive: List[Tuple[int, float, str, str, Dict[str, str]]] = []

        for fmt in formats:
            url = fmt.get("url")
            if not url or not isinstance(url, str) or not url.startswith("http"):
                continue
            ext = str(fmt.get("ext") or "").lower()
            vcodec = str(fmt.get("vcodec") or "none").lower()
            acodec = str(fmt.get("acodec") or "none").lower()
            if acodec == "none":
                continue
            raw_hdrs = fmt.get("http_headers") or info.get("http_headers") or {}
            ua = str(raw_hdrs.get("User-Agent") or config.BROWSER_USER_AGENT)
            hdrs = {"User-Agent": ua}
            abr = float(fmt.get("abr") or fmt.get("tbr") or 0)

            if vcodec == "none":
                mime = "audio/mp4" if ext in ("m4a", "mp4") else ("audio/webm" if ext == "webm" else "audio/mpeg")
                pref = 2 if mime == "audio/mp4" else 1
                audio_only.append((pref, abr, url, mime, hdrs))
            elif ext == "mp4":
                fid = str(fmt.get("format_id") or "")
                pref = 3 if fid == "18" else (2 if "mp4a" in acodec else 1)
                progressive.append((pref, -abr, url, "video/mp4", hdrs))

        # 1. Try best audio-only stream ONLY if tail verification passes (blocks 1 MiB PO-token traps)
        audio_only.sort(key=lambda x: (x[0], x[1]), reverse=True)
        for _, _, url, mime, hdrs in audio_only[:2]:
            ok, _, resp_hdrs, final_url = http_client.probe_url(
                url, headers=hdrs, timeout=config.STREAM_PROBE_TIMEOUT, verify_tail=True
            )
            if ok:
                rc = resp_hdrs.get("content-type", mime).split(";")[0].strip().lower()
                if rc.startswith(("audio/", "video/mp4", "video/webm")):
                    mime = rc
                return final_url, mime, hdrs

        # 2. Fall back to progressive MP4 (preferring itag=18 fast-start AAC, PO-token exempt)
        progressive.sort(key=lambda x: (x[0], x[1]), reverse=True)
        for _, _, url, mime, hdrs in progressive[:2]:
            ok, _, resp_hdrs, final_url = http_client.probe_url(
                url, headers=hdrs, timeout=config.STREAM_PROBE_TIMEOUT, verify_tail=False
            )
            if ok:
                rc = resp_hdrs.get("content-type", mime).split(";")[0].strip().lower()
                if rc.startswith(("audio/", "video/mp4", "video/webm")):
                    mime = rc
                return final_url, mime, hdrs
    except Exception:
        pass
    return None


def _pick_best_adaptive_audio(formats: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    audio_candidates: List[Tuple[int, int, str, str]] = []
    for fmt in formats:
        url = fmt.get("url")
        if not url or not isinstance(url, str):
            continue
        mime = str(fmt.get("mimeType") or fmt.get("type") or "")
        if not mime.startswith("audio/"):
            continue
        base_mime = mime.split(";")[0].strip()
        # Prefer audio/mp4 (broadest Safari/Chrome/Firefox seek compatibility) then audio/webm
        mime_pref = 2 if "mp4" in base_mime else 1
        bitrate = int(fmt.get("bitrate") or fmt.get("averageBitrate") or 0)
        audio_candidates.append((mime_pref, bitrate, url, base_mime))

    if not audio_candidates:
        return None
    audio_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    _, _, url, mime = audio_candidates[0]
    return url, mime


def _pick_best_progressive_format(formats: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """Select fast-start progressive MP4 with AAC audio (preferring itag=18, exempt from GVS PO-token 403)."""
    prog_candidates: List[Tuple[int, int, str, str]] = []
    for fmt in formats:
        url = fmt.get("url")
        if not url or not isinstance(url, str):
            continue
        mime = str(fmt.get("mimeType") or fmt.get("type") or "")
        base_mime = mime.split(";")[0].strip().lower()
        if not (base_mime.startswith("video/mp4") or base_mime.startswith("audio/")):
            continue
        itag = int(fmt.get("itag") or 0)
        has_aac = "mp4a" in mime.lower()
        if itag == 18:
            pref = 3
        elif base_mime == "video/mp4" and has_aac:
            pref = 2
        else:
            pref = 1
        # Prefer smaller progressive bitrate (360p itag=18 ~2MB) for fast audio playback start
        bitrate = int(fmt.get("bitrate") or fmt.get("averageBitrate") or 0)
        prog_candidates.append((pref, -bitrate, url, base_mime))

    if not prog_candidates:
        return None
    prog_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    _, _, url, mime = prog_candidates[0]
    return url, mime


def _resolve_via_innertube_player(video_id: str) -> Optional[Tuple[str, str, Dict[str, str]]]:
    clients = [
        (
            {
                "clientName": "ANDROID",
                "clientVersion": "20.10.38",
                "androidSdkVersion": 35,
                "osName": "Android",
                "osVersion": "15",
                "hl": "en",
                "gl": "US",
            },
            "com.google.android.youtube/20.10.38 (Linux; U; Android 15) gzip",
            {"X-YouTube-Client-Name": "3", "X-YouTube-Client-Version": "20.10.38"},
        ),
        (
            {
                "clientName": "IOS",
                "clientVersion": "20.10.4",
                "deviceMake": "Apple",
                "deviceModel": "iPhone16,2",
                "hl": "en",
                "gl": "US",
            },
            "com.google.ios.youtube/20.10.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)",
            {"X-YouTube-Client-Name": "5", "X-YouTube-Client-Version": "20.10.4"},
        ),
        (
            {
                "clientName": "ANDROID_VR",
                "clientVersion": "1.60.19",
                "androidSdkVersion": 32,
                "hl": "en",
                "gl": "US",
            },
            "com.google.android.apps.youtube.vr.oculus/1.60.19 (Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip",
            {"X-YouTube-Client-Name": "28", "X-YouTube-Client-Version": "1.60.19"},
        ),
    ]

    for client_ctx, ua, extra_hdrs in clients:
        try:
            body = {
                "videoId": video_id,
                "context": {"client": client_ctx},
                "contentCheckOk": True,
                "racyCheckOk": True,
            }
            req_hdrs = {"User-Agent": ua, "Content-Type": "application/json", **extra_hdrs}
            data = http_client.fetch_json(
                YT_PLAYER_URL,
                method="POST",
                headers=req_hdrs,
                json_body=body,
                timeout=config.STREAM_PROBE_TIMEOUT,
            )
            play_status = (data.get("playabilityStatus") or {}).get("status")
            if play_status and play_status != "OK":
                continue

            streaming_data = data.get("streamingData") or {}
            stream_hdrs = {"User-Agent": ua}

            # 1. Try audio-only adaptiveFormats ONLY if both bytes=0- AND tail range pass (no PO-token 403 trap)
            adaptive_picked = _pick_best_adaptive_audio(streaming_data.get("adaptiveFormats") or [])
            if adaptive_picked:
                audio_url, mime = adaptive_picked
                ok, _, resp_hdrs, final_url = http_client.probe_url(
                    audio_url,
                    headers=stream_hdrs,
                    timeout=config.STREAM_PROBE_TIMEOUT,
                    verify_tail=True,
                )
                if ok:
                    rc = resp_hdrs.get("content-type", mime).split(";")[0].strip()
                    if rc.startswith(("audio/", "video/mp4", "video/webm")):
                        mime = rc
                    return final_url, mime, stream_hdrs

            # 2. Fall back to progressive streamingData.formats (itag=18 fast-start MP4 + AAC, PO-token exempt)
            prog_picked = _pick_best_progressive_format(streaming_data.get("formats") or [])
            if prog_picked:
                prog_url, mime = prog_picked
                ok, _, resp_hdrs, final_url = http_client.probe_url(
                    prog_url,
                    headers=stream_hdrs,
                    timeout=config.STREAM_PROBE_TIMEOUT,
                    verify_tail=False,
                )
                if ok:
                    rc = resp_hdrs.get("content-type", mime).split(";")[0].strip()
                    if rc.startswith(("audio/", "video/mp4", "video/webm")):
                        mime = rc
                    return final_url, mime, stream_hdrs
        except Exception:
            continue
    return None


def _is_instance_healthy(instance: str) -> bool:
    with _POOL_LOCK:
        failed_at = _FAILED_INSTANCES.get(instance)
        if failed_at and (time.time() - failed_at) < config.INSTANCE_FAIL_TTL:
            return False
    return True


def _mark_instance_failed(instance: str) -> None:
    with _POOL_LOCK:
        _FAILED_INSTANCES[instance] = time.time()


def _get_invidious_instances() -> List[str]:
    global _INVIDIOUS_DYNAMIC, _INVIDIOUS_FETCHED_AT
    now = time.time()
    with _POOL_LOCK:
        if _INVIDIOUS_DYNAMIC and (now - _INVIDIOUS_FETCHED_AT) < config.INVIDIOUS_LIST_TTL:
            return list(_INVIDIOUS_DYNAMIC)

    fetched: List[str] = []
    try:
        data = http_client.fetch_json(
            "https://api.invidious.io/instances.json?sort_by=health", timeout=4.0
        )
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, list) and len(entry) >= 2 and isinstance(entry[1], dict):
                    info = entry[1]
                    if info.get("api") and info.get("type") == "https":
                        uri = str(info.get("uri") or "").rstrip("/")
                        if uri.startswith("https://"):
                            fetched.append(uri)
                if len(fetched) >= 6:
                    break
    except Exception:
        pass

    merged = fetched + [s for s in _INVIDIOUS_SEED_INSTANCES if s not in fetched]
    with _POOL_LOCK:
        _INVIDIOUS_DYNAMIC = merged
        _INVIDIOUS_FETCHED_AT = now
    return list(merged)


def _resolve_via_piped(video_id: str) -> Optional[Tuple[str, str, Dict[str, str]]]:
    tried = 0
    for inst in _PIPED_SEED_INSTANCES:
        if not _is_instance_healthy(inst):
            continue
        tried += 1
        try:
            data = http_client.fetch_json(
                f"{inst}/streams/{video_id}", timeout=3.0
            )
            hdrs = {"User-Agent": config.BROWSER_USER_AGENT}
            picked = _pick_best_adaptive_audio(data.get("audioStreams") or [])
            if picked:
                url, mime = picked
                ok, _, resp_hdrs, final_url = http_client.probe_url(
                    url, headers=hdrs, timeout=3.0, verify_tail=True
                )
                if ok:
                    rc = resp_hdrs.get("content-type", mime).split(";")[0].strip().lower()
                    if rc.startswith(("audio/", "video/mp4", "video/webm")):
                        mime = rc
                    return final_url, mime, hdrs

            # Fallback to progressive video+audio MP4 stream (e.g. 360p itag=18)
            prog_streams = [
                s for s in (data.get("videoStreams") or []) if not s.get("videoOnly")
            ]
            prog = _pick_best_progressive_format(prog_streams)
            if prog:
                url, mime = prog
                ok, _, resp_hdrs, final_url = http_client.probe_url(
                    url, headers=hdrs, timeout=3.0, verify_tail=False
                )
                if ok:
                    rc = resp_hdrs.get("content-type", mime).split(";")[0].strip().lower()
                    if rc.startswith(("audio/", "video/mp4", "video/webm")):
                        mime = rc
                    return final_url, mime, hdrs

            _mark_instance_failed(inst)
        except Exception:
            _mark_instance_failed(inst)
        if tried >= 2:
            break
    return None


def _resolve_via_invidious(video_id: str) -> Optional[Tuple[str, str, Dict[str, str]]]:
    tried = 0
    for inst in _get_invidious_instances()[:4]:
        if not _is_instance_healthy(inst):
            continue
        tried += 1
        try:
            data = http_client.fetch_json(
                f"{inst}/api/v1/videos/{video_id}", timeout=3.0
            )
            hdrs = {"User-Agent": config.BROWSER_USER_AGENT}
            picked = _pick_best_adaptive_audio(data.get("adaptiveFormats") or [])
            if picked:
                url, mime = picked
                ok, _, resp_hdrs, final_url = http_client.probe_url(
                    url, headers=hdrs, timeout=3.0, verify_tail=True
                )
                if ok:
                    rc = resp_hdrs.get("content-type", mime).split(";")[0].strip().lower()
                    if rc.startswith(("audio/", "video/mp4", "video/webm")):
                        mime = rc
                    return final_url, mime, hdrs

            prog = _pick_best_progressive_format(data.get("formatStreams") or [])
            if prog:
                url, mime = prog
                ok, _, resp_hdrs, final_url = http_client.probe_url(
                    url, headers=hdrs, timeout=3.0, verify_tail=False
                )
                if ok:
                    rc = resp_hdrs.get("content-type", mime).split(";")[0].strip().lower()
                    if rc.startswith(("audio/", "video/mp4", "video/webm")):
                        mime = rc
                    return final_url, mime, hdrs

            _mark_instance_failed(inst)
        except Exception:
            _mark_instance_failed(inst)
        if tried >= 2:
            break
    return None


def resolve_audio_stream(video_id: str) -> Optional[Tuple[str, str, Dict[str, str], str]]:
    """Resolve a direct playable audio/MP4 stream URL for a YouTube videoId.

    Tries fast InnerTube ANDROID first, then falls back cleanly to yt-dlp (crucial on
    cloud datacenter IPs where InnerTube may hit LOGIN_REQUIRED), then Piped & Invidious.
    Returns (url, content_type, headers, source_label) or None.
    """
    if not video_id:
        return None

    res = _resolve_via_innertube_player(video_id)
    if res:
        return res[0], res[1], res[2], "youtube-innertube"

    res = _resolve_via_yt_dlp(video_id)
    if res:
        return res[0], res[1], res[2], "yt-dlp"

    # Skip dead public Piped/Invidious seeds on cloud datacenter IPs unless explicitly enabled
    if config.is_cloud_env() and os.environ.get("SONIFY_ENABLE_PUBLIC_PIPED", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return None

    res = _resolve_via_piped(video_id)
    if res:
        return res[0], res[1], res[2], "piped"

    res = _resolve_via_invidious(video_id)
    if res:
        return res[0], res[1], res[2], "invidious"

    return None


def _find_browse_id_with_prefix(obj: Any, prefix: str) -> Optional[str]:
    if isinstance(obj, dict):
        bid = obj.get("browseId")
        if isinstance(bid, str) and bid.startswith(prefix):
            return bid
        for v in obj.values():
            found = _find_browse_id_with_prefix(v, prefix)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_browse_id_with_prefix(item, prefix)
            if found:
                return found
    return None


def _extract_ytm_lyrics_text(obj: Any) -> Optional[str]:
    if isinstance(obj, dict):
        if "musicDescriptionShelfRenderer" in obj:
            runs = (
                obj["musicDescriptionShelfRenderer"]
                .get("description", {})
                .get("runs", [])
            )
            text = "".join(str(r.get("text") or "") for r in runs).strip()
            if text:
                return text
        for v in obj.values():
            found = _extract_ytm_lyrics_text(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _extract_ytm_lyrics_text(item)
            if found:
                return found
    return None


def fetch_plain_lyrics(video_id: str) -> Optional[str]:
    if not video_id:
        return None
    try:
        next_data = http_client.fetch_json(
            YTM_NEXT_URL,
            method="POST",
            headers=_ytm_headers(),
            json_body={"context": _ytm_context(), "videoId": video_id},
            timeout=config.PROVIDER_TIMEOUT,
        )
        browse_id = _find_browse_id_with_prefix(next_data, "MPLYt")
        if not browse_id:
            return None
        browse_data = http_client.fetch_json(
            YTM_BROWSE_URL,
            method="POST",
            headers=_ytm_headers(),
            json_body={"context": _ytm_context(), "browseId": browse_id},
            timeout=config.PROVIDER_TIMEOUT,
        )
        return _extract_ytm_lyrics_text(browse_data)
    except Exception:
        return None
