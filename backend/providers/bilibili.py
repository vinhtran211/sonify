"""Bilibili DASH full-track audio provider (cloud-safe, keyless via /x/frontend/finger/spi).

Provides a verified Tier 4.5 full-track audio fallback that works cleanly from cloud datacenter IPs
(including Render/AWS Singapore) with strict title, artist, duration, and noise gates.
Streams must be proxied through Sonify with `Referer: https://www.bilibili.com/` (never directUrl).
"""

from __future__ import annotations

import html
import re
import threading
import time
import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from backend import config, http_client, lrc_parser

BILI_API_BASE = "https://api.bilibili.com"
BILI_REFERER = "https://www.bilibili.com/"
BILI_ORIGIN = "https://www.bilibili.com"
BUVID_TTL = 12 * 3600  # 12 hours

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_DECORATIVE_BRACKETS_RE = re.compile(r"(?:【[^】]*】|《[^》]*》|「[^」]*」|\[[^\]]*\]|\([^)]*\))")
_SEP_SPLIT_RE = re.compile(r"\s*[-–—|/:：]\s*")

_NOISE_KEYWORDS = (
    "cover",
    "karaoke",
    "remix",
    "speed up",
    "sped up",
    "slowed",
    "nightcore",
    "live",
    "reaction",
    "8d",
    "piano",
    "instrumental",
    "lofi",
    "lo-fi",
)
_CJK_NOISE_KEYWORDS = (
    "翻唱",
    "伴奏",
    "纯音乐",
    "现场",
    "教学",
    "吉他",
)
_NOISE_PATTERNS = [
    (kw, re.compile(rf"(?<!\w){re.escape(lrc_parser.normalize_key(kw))}(?!\w)", re.IGNORECASE))
    for kw in _NOISE_KEYWORDS
]

_BUVID_LOCK = threading.Lock()
_CACHED_COOKIE: str = ""
_CACHED_COOKIE_AT: float = 0.0


def _base_headers(cookie: str = "") -> Dict[str, str]:
    hdrs = {
        "User-Agent": config.BROWSER_USER_AGENT,
        "Referer": BILI_REFERER,
        "Origin": BILI_ORIGIN,
    }
    if cookie:
        hdrs["Cookie"] = cookie
    return hdrs


def get_buvid_cookie(force_refresh: bool = False) -> str:
    """Fetch and cache keyless buvid3/buvid4 cookies from Bilibili's finger/spi endpoint."""
    global _CACHED_COOKIE, _CACHED_COOKIE_AT
    now = time.time()
    with _BUVID_LOCK:
        if not force_refresh and _CACHED_COOKIE and (now - _CACHED_COOKIE_AT) < BUVID_TTL:
            return _CACHED_COOKIE

    try:
        data = http_client.fetch_json(
            f"{BILI_API_BASE}/x/frontend/finger/spi",
            headers=_base_headers(),
            timeout=5.0,
        )
        inner = (data or {}).get("data") if isinstance(data, dict) else None
        if isinstance(inner, dict):
            b3 = str(inner.get("b_3") or "").strip()
            b4 = str(inner.get("b_4") or "").strip()
            parts = []
            if b3:
                parts.append(f"buvid3={b3}")
            if b4:
                parts.append(f"buvid4={b4}")
            cookie = "; ".join(parts)
            if cookie:
                with _BUVID_LOCK:
                    _CACHED_COOKIE = cookie
                    _CACHED_COOKIE_AT = time.time()
                return cookie
    except Exception:
        pass
    return ""


def strip_html(text: str) -> str:
    """Remove HTML tags (like <em class='keyword'>) and decode HTML entities."""
    return html.unescape(_HTML_TAG_RE.sub("", str(text or ""))).strip()


def parse_duration_seconds(raw: Any) -> int:
    """Parse Bilibili duration strings like '3:18', '04:05', '1:02:03', or int seconds."""
    if isinstance(raw, (int, float)):
        return max(0, int(raw))
    text = str(raw or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(nums) == 2:
        return max(0, nums[0] * 60 + nums[1])
    if len(nums) == 3:
        return max(0, nums[0] * 3600 + nums[1] * 60 + nums[2])
    return 0


def _noise_penalty(cand_raw: str, target_text: str) -> float:
    norm_c = lrc_parser.normalize_key(cand_raw)
    norm_t = lrc_parser.normalize_key(target_text)
    raw_c_lower = (cand_raw or "").lower()
    raw_t_lower = (target_text or "").lower()
    penalty = 0.0
    for _, pat in _NOISE_PATTERNS:
        if pat.search(norm_c) and not pat.search(norm_t):
            penalty += 0.25
    for kw in _CJK_NOISE_KEYWORDS:
        if kw in raw_c_lower and kw not in raw_t_lower:
            penalty += 0.25
    return penalty


def search_videos(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search Bilibili videos with automatic buvid3 discovery and 1x 412/-352 retry."""
    q = (query or "").strip()
    if not q:
        return []

    cookie = get_buvid_cookie(force_refresh=False)
    for attempt in range(2):
        params = urllib.parse.urlencode(
            {
                "search_type": "video",
                "page_size": str(limit),
                "keyword": q,
            }
        )
        url = f"{BILI_API_BASE}/x/web-interface/search/type?{params}"
        try:
            data = http_client.fetch_json(url, headers=_base_headers(cookie), timeout=5.5)
            code = (data or {}).get("code") if isinstance(data, dict) else None
            if code == -352 and attempt == 0:
                cookie = get_buvid_cookie(force_refresh=True)
                continue
            results = ((data or {}).get("data") or {}).get("result") or []
            out: List[Dict[str, Any]] = []
            for item in results:
                if not isinstance(item, dict):
                    continue
                bvid = str(item.get("bvid") or "").strip()
                if not bvid:
                    continue
                out.append(
                    {
                        "bvid": bvid,
                        "title": strip_html(item.get("title") or ""),
                        "author": strip_html(item.get("author") or ""),
                        "duration": parse_duration_seconds(item.get("duration")),
                    }
                )
            return out
        except urllib.error.HTTPError as exc:
            try:
                exc.close()
            except Exception:
                pass
            if exc.code == 412 and attempt == 0:
                cookie = get_buvid_cookie(force_refresh=True)
                continue
            return []
        except Exception:
            return []
    return []


def score_candidate(
    item: Dict[str, Any],
    clean_title: str,
    primary_artist: str,
    full_artist: str,
    target_duration: int = 0,
    raw_target_title: str = "",
) -> float:
    """Strictly score a Bilibili candidate; returns -1.0 if rejected by duration/title/artist gates."""
    raw_title = str(item.get("title") or "").strip()
    author = str(item.get("author") or "").strip()
    cand_dur = int(item.get("duration") or 0)

    # Hard Gate 1: Full-track duration (90..720s) + target duration match
    if cand_dur < 90 or cand_dur > 720:
        return -1.0
    if target_duration > 30:
        max_diff = max(35, int(target_duration * 0.25))
        if abs(cand_dur - target_duration) > max_diff:
            return -1.0

    # Clean decorative brackets for title comparison while preserving raw title for artist & noise gates
    stripped_brackets = _DECORATIVE_BRACKETS_RE.sub(" ", raw_title)
    stripped_brackets = re.sub(r"\s+", " ", stripped_brackets).strip(" -–—|/:：")
    cleaned_cand = lrc_parser.clean_track_title(stripped_brackets or raw_title)

    # Hard Gate 2: Title similarity >= 0.58 (across cleaned title & separator-split segments)
    title_sim = max(
        lrc_parser.token_similarity(cleaned_cand, clean_title),
        lrc_parser.token_similarity(stripped_brackets, clean_title),
        lrc_parser.token_similarity(lrc_parser.clean_track_title(raw_title), clean_title),
    )
    parts = [p.strip() for p in _SEP_SPLIT_RE.split(stripped_brackets or raw_title) if p.strip()]
    for part in parts:
        title_sim = max(
            title_sim,
            lrc_parser.token_similarity(lrc_parser.clean_track_title(part), clean_title),
        )
    if title_sim < 0.58:
        return -1.0

    # Hard Gate 3: Artist similarity >= 0.40 when target artist is known
    target_has_artist = bool(
        primary_artist and primary_artist.lower() not in ("unknown artist", "various artists")
    )
    artist_sim = max(
        lrc_parser.token_similarity(author, primary_artist),
        lrc_parser.token_similarity(author, full_artist or primary_artist),
        lrc_parser.token_similarity(raw_title, primary_artist),
    )
    for part in parts:
        artist_sim = max(
            artist_sim,
            lrc_parser.token_similarity(part, primary_artist),
            lrc_parser.token_similarity(part, full_artist or primary_artist),
        )

    norm_pa = lrc_parser.normalize_key(primary_artist)
    norm_pa_compact = norm_pa.replace(" ", "")
    norm_raw = lrc_parser.normalize_key(raw_title)
    norm_raw_compact = norm_raw.replace(" ", "")
    norm_author = lrc_parser.normalize_key(author)
    norm_author_compact = norm_author.replace(" ", "")

    if norm_pa and len(norm_pa) >= 2:
        if norm_pa in norm_raw or norm_pa in norm_author:
            artist_sim = max(artist_sim, 0.85)
    if len(norm_pa_compact) >= 3:
        if norm_pa_compact in norm_raw_compact or norm_pa_compact in norm_author_compact:
            artist_sim = max(artist_sim, 0.85)

    if target_has_artist and artist_sim < 0.40:
        return -1.0

    if target_duration > 0 and cand_dur > 0:
        dur_prox = max(0.0, 1.0 - abs(cand_dur - target_duration) / 45.0)
    else:
        dur_prox = 0.65

    target_context = f"{raw_target_title or clean_title} {full_artist or primary_artist}"
    noise_pen = _noise_penalty(f"{raw_title} {author}", target_context)

    score = 0.55 * title_sim + 0.30 * artist_sim + 0.15 * dur_prox - noise_pen
    return score


def resolve_bvid_audio(bvid: str) -> Optional[Tuple[str, str, Dict[str, str]]]:
    """Resolve a Bilibili BV video ID to a verified DASH audio/mp4 stream URL + proxy headers."""
    clean_bvid = (bvid or "").strip()
    if not clean_bvid:
        return None
    cookie = get_buvid_cookie(force_refresh=False)
    hdrs = _base_headers(cookie)

    try:
        view_data = http_client.fetch_json(
            f"{BILI_API_BASE}/x/web-interface/view?{urllib.parse.urlencode({'bvid': clean_bvid})}",
            headers=hdrs,
            timeout=5.0,
        )
        vinfo = (view_data or {}).get("data") if isinstance(view_data, dict) else None
        if not isinstance(vinfo, dict):
            return None
        cid = vinfo.get("cid")
        if not cid:
            pages = vinfo.get("pages") or []
            if pages and isinstance(pages[0], dict):
                cid = pages[0].get("cid")
        if not cid:
            return None

        play_params = urllib.parse.urlencode(
            {
                "bvid": clean_bvid,
                "cid": str(cid),
                "qn": "64",
                "fnval": "16",
                "fnver": "0",
                "fourk": "0",
            }
        )
        play_data = http_client.fetch_json(
            f"{BILI_API_BASE}/x/player/playurl?{play_params}",
            headers=hdrs,
            timeout=5.5,
        )
        dash = ((play_data or {}).get("data") or {}).get("dash") or {}
        audios = dash.get("audio") or []
        if not isinstance(audios, list) or not audios:
            return None

        # Prefer mp4a 128-192 kbps for fast startup & universal AVPlayer/browser compatibility, then highest bandwidth
        def _audio_rank(a: Dict[str, Any]) -> Tuple[int, int]:
            codecs = str(a.get("codecs") or "").lower()
            is_mp4a = 1 if ("mp4a" in codecs or not codecs) else 0
            bw = int(a.get("bandwidth") or 0)
            in_sweet_spot = 1 if (100_000 <= bw <= 220_000) else 0
            return (is_mp4a, in_sweet_spot * 10_000_000 + bw)

        sorted_audios = sorted(
            [a for a in audios if isinstance(a, dict)],
            key=_audio_rank,
            reverse=True,
        )

        proxy_hdrs = {
            "User-Agent": config.BROWSER_USER_AGENT,
            "Referer": BILI_REFERER,
        }

        for stream in sorted_audios[:3]:
            urls: List[str] = []
            base_u = str(stream.get("baseUrl") or stream.get("base_url") or "").strip()
            if base_u.startswith("http"):
                urls.append(base_u)
            backups = stream.get("backupUrl") or stream.get("backup_url") or []
            if isinstance(backups, list):
                for bu in backups[:1]:
                    sbu = str(bu or "").strip()
                    if sbu.startswith("http") and sbu not in urls:
                        urls.append(sbu)

            for cand_u in urls:
                ok, _, _, final_url = http_client.probe_url(
                    cand_u,
                    headers=proxy_hdrs,
                    timeout=config.STREAM_PROBE_TIMEOUT,
                    min_total_bytes=400_000,
                    initial_range="bytes=0-1023",
                )
                if ok:
                    return final_url, "audio/mp4", proxy_hdrs
    except Exception:
        return None
    return None


def find_best_stream(
    title: str,
    artist: str,
    duration: int = 0,
    min_score: float = 0.62,
) -> Optional[Tuple[str, str, Dict[str, str]]]:
    """Search Bilibili and return (audio_url, 'audio/mp4', proxy_headers) for a verified full track."""
    clean_title = lrc_parser.clean_track_title(title)
    primary_artist = lrc_parser.extract_primary_artist(artist)
    query = f"{primary_artist} {clean_title}".strip()
    if not query:
        return None

    queries = [query]
    ascii_q = lrc_parser.normalize_key(query).strip()
    if ascii_q and ascii_q.lower() != query.lower():
        queries.append(ascii_q)

    scored: List[Tuple[float, Dict[str, Any]]] = []
    seen_bvids: set[str] = set()

    for q in queries:
        for item in search_videos(q, limit=10):
            bvid = item.get("bvid") or ""
            if not bvid or bvid in seen_bvids:
                continue
            seen_bvids.add(bvid)
            sc = score_candidate(
                item,
                clean_title=clean_title,
                primary_artist=primary_artist,
                full_artist=artist,
                target_duration=duration,
                raw_target_title=title,
            )
            if sc >= min_score:
                scored.append((sc, item))
        if scored:
            break

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    for _, item in scored[:3]:
        found = resolve_bvid_audio(item["bvid"])
        if found:
            return found
    return None
