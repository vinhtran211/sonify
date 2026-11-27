"""LRC timestamp parser and track metadata normalization utilities."""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Tuple

# Matches [mm:ss], [mm:ss.x], [mm:ss.xx], [mm:ss.xxx], [mm:ss:xx]
_TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_OFFSET_RE = re.compile(r"^\s*\[offset\s*:\s*([+-]?\d+)\s*\]\s*$", re.IGNORECASE)
_META_TAG_RE = re.compile(
    r"^\s*\[(?:ar|ti|al|au|by|length|re|ve|id|offset)\s*:[^\]]*\]\s*$",
    re.IGNORECASE,
)
_INLINE_WORD_TIME_RE = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")

# Title suffixes that degrade matching on LRCLIB / JioSaavn / YouTube
_TITLE_NOISE_PATTERNS = [
    r"\(\s*official\s*(?:music\s*)?(?:video|audio|mv|lyric\s*video|visualizer)[^)]*\)",
    r"\[\s*official\s*(?:music\s*)?(?:video|audio|mv|lyric\s*video|visualizer)[^\]]*\]",
    r"\(\s*remaster(?:ed)?(?:\s*\d{4})?[^)]*\)",
    r"\[\s*remaster(?:ed)?(?:\s*\d{4})?[^\]]*\]",
    r"\(\s*(?:feat\.?|ft\.?|featuring)\s+[^)]+\)",
    r"\[\s*(?:feat\.?|ft\.?|featuring)\s+[^\]]+\]",
    r"\(\s*radio\s*edit[^)]*\)",
    r"\[\s*radio\s*edit[^\]]*\]",
    r"\(\s*live\s*at[^)]*\)",
    r"\[\s*live\s*at[^\]]*\]",
    r"\(\s*(?:audio|hd|4k|hq|mv|lyrics?)\s*\)",
    r"\[\s*(?:audio|hd|4k|hq|mv|lyrics?)\s*\]",
    r"\s*[-–—|]\s*(?:remaster(?:ed)?(?:\s*\d{4})?|radio\s*edit|official\s*(?:music\s*)?(?:audio|video|mv|lyric\s*video|visualizer)[^-–—|]*)$",
    r"\s+(?:feat\.?|ft\.?|featuring)\s+.+$",
]
_TITLE_NOISE_RE = [re.compile(p, re.IGNORECASE) for p in _TITLE_NOISE_PATTERNS]
_ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|&|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\sx\s)\s*", re.IGNORECASE)


def _parse_fraction(frac: str | None) -> float:
    if not frac:
        return 0.0
    if len(frac) == 1:
        return int(frac) / 10.0
    if len(frac) == 2:
        return int(frac) / 100.0
    return int(frac[:3]) / 1000.0


def parse_lrc(lrc_text: str) -> List[Dict]:
    """Parse an LRC string into sorted time-synced lyric entries.

    Supports:
    - [mm:ss], [mm:ss.x], [mm:ss.xx], [mm:ss.xxx], [mm:ss:xx]
    - Multiple timestamps on a single line (e.g. [00:15.20][01:12.40]Chorus)
    - [offset:+/-ms] header tag (+ adds ms, - subtracts ms; clamped at >= 0.0)
    - Ignoring metadata tags ([ar:], [ti:], [al:], [au:], [by:], [length:], [re:], [ve:])
    - Stripping inline word-level <mm:ss.xx> tags
    - Converting blank timed lines after lyrical lines into interlude markers
    """
    if not lrc_text or not isinstance(lrc_text, str):
        return []

    offset_ms = 0
    raw_lines = lrc_text.splitlines()

    # Pass 1: inspect header tags for [offset:+/-ms]
    for raw in raw_lines:
        m_off = _OFFSET_RE.match(raw)
        if m_off:
            try:
                offset_ms = int(m_off.group(1))
            except ValueError:
                offset_ms = 0

    entries: List[Tuple[float, int, str]] = []
    seq = 0

    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if _META_TAG_RE.match(stripped):
            continue

        matches = list(_TIMESTAMP_RE.finditer(stripped))
        if not matches:
            continue

        # Remove all [mm:ss...] tags and inline <mm:ss...> word timings
        text = _TIMESTAMP_RE.sub("", stripped)
        text = _INLINE_WORD_TIME_RE.sub("", text)
        text = re.sub(r"\s+", " ", text).strip()

        for m in matches:
            minutes = int(m.group(1))
            seconds = int(m.group(2))
            frac = _parse_fraction(m.group(3))
            base_time = minutes * 60.0 + seconds + frac
            adjusted = max(0.0, round(base_time + (offset_ms / 1000.0), 3))
            entries.append((adjusted, seq, text))
            seq += 1

    entries.sort(key=lambda item: (item[0], item[1]))

    # Convert blank timed lines following lyrical lines into interlude markers;
    # omit leading blanks and consecutive duplicate blanks.
    result: List[Dict] = []
    seen_lyric = False
    last_was_interlude = False

    for timestamp, _, text in entries:
        if not text:
            if seen_lyric and not last_was_interlude:
                result.append({"time": timestamp, "text": "", "interlude": True})
                last_was_interlude = True
            continue
        seen_lyric = True
        last_was_interlude = False
        result.append({"time": timestamp, "text": text})

    # Trim trailing interlude marker if present
    while result and result[-1].get("interlude"):
        result.pop()

    return result


def lrc_to_plain(lrc_text: str) -> str:
    """Convert raw LRC text into clean plain multi-line lyrics."""
    lines = parse_lrc(lrc_text)
    if not lines:
        return ""
    out: List[str] = []
    for item in lines:
        if item.get("interlude"):
            out.append("")
        else:
            out.append(item["text"])
    return "\n".join(out).strip()


def clean_track_title(title: str) -> str:
    """Strip video/remaster/featuring noise from track titles for accurate matching."""
    cleaned = (title or "").strip()
    for pattern in _TITLE_NOISE_RE:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")
    return cleaned or (title or "").strip()


def extract_primary_artist(artist: str) -> str:
    """Extract the primary artist name from multi-artist credit strings."""
    raw = (artist or "").strip()
    if not raw:
        return ""
    parts = _ARTIST_SPLIT_RE.split(raw)
    return (parts[0] if parts else raw).strip()


def normalize_key(text: str) -> str:
    """Fold Latin/Vietnamese diacritics and punctuation while preserving non-Latin scripts (CJK/Hangul/Cyrillic)."""
    if not text:
        return ""
    # Preserve Vietnamese 'đ' -> 'd' before NFKD decomposition
    s = text.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    # Recompose non-Latin scripts like Hangul cleanly after stripping combining marks
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[^\w\s]|_", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _has_cjk(text: str) -> bool:
    return any(ord(ch) >= 0x2E80 for ch in text)


def token_similarity(a: str, b: str) -> float:
    """Compute a fast normalized similarity score in [0.0, 1.0] without short-substring false positives."""
    na = normalize_key(a)
    nb = normalize_key(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    shorter = min(len(na), len(nb))
    longer = max(len(na), len(nb))
    pa, pb = f" {na} ", f" {nb} "
    if shorter >= 3 and (pa in pb or pb in pa):
        return 0.8 + 0.2 * (shorter / longer)
    if (_has_cjk(na) or _has_cjk(nb)) and shorter >= 2 and (na in nb or nb in na):
        return 0.8 + 0.2 * (shorter / longer)

    ta = set(na.split())
    tb = set(nb.split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    jaccard = inter / union
    overlap = inter / min(len(ta), len(tb))
    return 0.55 * jaccard + 0.45 * overlap


def is_valid_full_duration(cand_dur: int, target_duration: int = 0) -> bool:
    """Return True only if cand_dur passes the universal anti-short-clip floor.

    - cand_dur <= 0: unknown metadata duration -> True (rely on min_total_bytes >= 350_000).
    - 30 <= target_duration < 60: genuine short album interlude -> require >= max(30, target_duration - 10).
    - Otherwise: strictly require cand_dur >= 60 (blocks 29-30s previews/ringtones/teasers).
    """
    if cand_dur <= 0:
        return True
    if 30 <= target_duration < 60:
        return cand_dur >= max(30, target_duration - 10)
    return cand_dur >= 60


def is_duration_compatible(
    cand_dur: int,
    target_duration: int = 0,
    relaxed: bool = False,
    mv_capable: bool = False,
) -> bool:
    """Asymmetric duration gate: allows MV intros/outros (+15..65s) while blocking chopped/sped-up edits."""
    if not is_valid_full_duration(cand_dur, target_duration):
        return False
    if cand_dur <= 0:
        return True
    if cand_dur > 720:
        return False
    if target_duration <= 30:
        return cand_dur >= (60 if relaxed else 75)
    if target_duration < 60:
        return abs(cand_dur - target_duration) <= (20 if relaxed else 12)

    delta = cand_dur - target_duration
    if not relaxed:
        max_shorter = max(10, int(target_duration * 0.05))
        max_longer = (
            max(35, int(target_duration * 0.20))
            if mv_capable
            else max(18, int(target_duration * 0.12))
        )
    else:
        max_shorter = max(12, int(target_duration * 0.06))
        max_longer = max(65, int(target_duration * 0.40))
    return (-max_shorter) <= delta <= max_longer


_HARD_REJECT_KEYWORDS = (
    "sped up",
    "speed up",
    "slowed",
    "slower",
    "nightcore",
    "daycore",
    "8d",
    "chopped",
    "screwed",
    "karaoke",
    "instrumental",
    "backing track",
    "off vocal",
    "mashup",
    "bootleg",
    "vinahouse",
    "nonstop",
    "megamix",
    "cha cha",
    "chachacha",
    "nhac song",
    "freedownload",
    "free download",
    "ringtone",
    "snippet",
    "teaser",
    "preview",
    "demo",
    "trailer",
    "parody",
    "reaction",
    "1 hour",
    "10 hours",
)
_HARD_REJECT_PATTERNS = [
    re.compile(rf"(?<!\w){re.escape(normalize_key(kw))}(?!\w)", re.IGNORECASE)
    for kw in _HARD_REJECT_KEYWORDS
]
_CJK_HARD_REJECT = ("伴奏", "纯音乐", "教学")

_BENIGN_MIX_RE = re.compile(r"(?<!\w)(?:radio edit|original mix)(?!\w)", re.IGNORECASE)
_DERIVATIVE_KEYWORDS = (
    "remix",
    "cover",
    "rework",
    "flip",
    "vip mix",
    "club mix",
    "lofi",
    "lo fi",
    "piano",
    "guitar",
    "violin",
    "cinematic",
    "orchestral",
    "lullaby",
    "tribute",
    "hop xuong",
    "beat",
    "dj",
    "edit",
    "mix",
)
_DERIVATIVE_PATTERNS = [
    re.compile(rf"(?<!\w){re.escape(normalize_key(kw))}(?!\w)", re.IGNORECASE)
    for kw in _DERIVATIVE_KEYWORDS
]
_CJK_DERIVATIVE = ("翻唱",)

_LIVE_ACOUSTIC_KEYWORDS = ("live", "acoustic", "unplugged")
_LIVE_ACOUSTIC_PATTERNS = [
    re.compile(rf"(?<!\w){re.escape(normalize_key(kw))}(?!\w)", re.IGNORECASE)
    for kw in _LIVE_ACOUSTIC_KEYWORDS
]
_CJK_LIVE = ("现场",)


def classify_unwanted_version(cand_text: str, target_text: str) -> str:
    """Classify unrequested version alterations: 'hard_reject' | 'derivative' | 'live_acoustic' | 'ok'."""
    norm_c = normalize_key(cand_text)
    norm_t = normalize_key(target_text)
    raw_c = (cand_text or "").lower()
    raw_t = (target_text or "").lower()

    for pat in _HARD_REJECT_PATTERNS:
        if pat.search(norm_c) and not pat.search(norm_t):
            return "hard_reject"
    for kw in _CJK_HARD_REJECT:
        if kw in raw_c and kw not in raw_t:
            return "hard_reject"

    masked_c = _BENIGN_MIX_RE.sub(" ", norm_c)
    masked_t = _BENIGN_MIX_RE.sub(" ", norm_t)
    for pat in _DERIVATIVE_PATTERNS:
        if pat.search(masked_c) and not pat.search(masked_t):
            return "derivative"
    for kw in _CJK_DERIVATIVE:
        if kw in raw_c and kw not in raw_t:
            return "derivative"

    for pat in _LIVE_ACOUSTIC_PATTERNS:
        if pat.search(norm_c) and not pat.search(norm_t):
            return "live_acoustic"
    for kw in _CJK_LIVE:
        if kw in raw_c and kw not in raw_t:
            return "live_acoustic"

    return "ok"


def has_primary_artist_token(primary_artist: str, haystack: str) -> bool:
    """Return True if any normalized primary artist token of length >= 3 appears in haystack."""
    norm_pa = normalize_key(primary_artist)
    norm_hay = normalize_key(haystack)
    if not norm_pa or not norm_hay:
        return False
    hay_tokens = set(norm_hay.split())
    hay_compact = norm_hay.replace(" ", "")
    for tok in norm_pa.split():
        if len(tok) >= 3 and (tok in hay_tokens or (len(tok) >= 4 and tok in hay_compact)):
            return True
    return False


def passes_relaxed_artist_gate(
    artist_sim: float,
    primary_artist: str,
    haystack: str,
    clean_title: str,
    title_sim: float,
    cand_dur: int,
    target_duration: int,
) -> bool:
    """Verify artist identity in Pass 2 without the generic-title ±45s zero-artist loophole."""
    if artist_sim >= 0.30 or has_primary_artist_token(primary_artist, haystack):
        return True
    norm_ct = normalize_key(clean_title)
    if (
        len(norm_ct) >= 12
        and len(norm_ct.split()) >= 3
        and title_sim >= 0.95
        and target_duration > 60
        and cand_dur > 0
        and abs(cand_dur - target_duration) <= 5
    ):
        return True
    return False

