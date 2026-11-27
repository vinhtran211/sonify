// LRC parser, binary-search active line finder, and formatting helpers

const TIMESTAMP_RE = /\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]/g;
const OFFSET_RE = /^\s*\[offset\s*:\s*([+-]?\d+)\s*\]\s*$/i;
const META_RE = /^\s*\[(?:ar|ti|al|au|by|length|re|ve|id|offset)\s*:[^\]]*\]\s*$/i;
const WORD_TIME_RE = /<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>/g;

function parseFrac(frac) {
  if (!frac) return 0;
  if (frac.length === 1) return Number(frac) / 10;
  if (frac.length === 2) return Number(frac) / 100;
  return Number(frac.slice(0, 3)) / 1000;
}

export function parseLrc(lrcText) {
  if (!lrcText || typeof lrcText !== 'string') return [];
  const rawLines = lrcText.split(/\r?\n/);
  let offsetMs = 0;

  for (const raw of rawLines) {
    const m = raw.match(OFFSET_RE);
    if (m) offsetMs = parseInt(m[1], 10) || 0;
  }

  const entries = [];
  let seq = 0;

  for (const raw of rawLines) {
    const trimmed = raw.trim();
    if (!trimmed || META_RE.test(trimmed)) continue;

    TIMESTAMP_RE.lastIndex = 0;
    const matches = [...trimmed.matchAll(TIMESTAMP_RE)];
    if (!matches.length) continue;

    const text = trimmed
      .replace(TIMESTAMP_RE, '')
      .replace(WORD_TIME_RE, '')
      .replace(/\s+/g, ' ')
      .trim();

    for (const m of matches) {
      const mins = parseInt(m[1], 10);
      const secs = parseInt(m[2], 10);
      const frac = parseFrac(m[3]);
      const t = Math.max(0, Math.round((mins * 60 + secs + frac + offsetMs / 1000) * 1000) / 1000);
      entries.push({ time: t, seq: seq++, text });
    }
  }

  entries.sort((a, b) => (a.time !== b.time ? a.time - b.time : a.seq - b.seq));

  const out = [];
  let seenLyric = false;
  let lastInterlude = false;

  for (const e of entries) {
    if (!e.text) {
      if (seenLyric && !lastInterlude) {
        out.push({ time: e.time, text: '', interlude: true });
        lastInterlude = true;
      }
      continue;
    }
    seenLyric = true;
    lastInterlude = false;
    out.push({ time: e.time, text: e.text });
  }

  while (out.length && out[out.length - 1].interlude) {
    out.pop();
  }
  return out;
}

/**
 * Binary search over sorted LRC lines to find the active line index for currentTime.
 * Uses a tiny 0.08s lead compensation for crisp visual sync.
 */
export function findActiveLineIndex(lines, currentTime) {
  if (!Array.isArray(lines) || !lines.length) return -1;
  const target = (Number(currentTime) || 0) + 0.08;
  if (target < lines[0].time) return -1;

  let lo = 0;
  let hi = lines.length - 1;
  let ans = 0;

  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].time <= target) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

export function formatTime(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const mins = Math.floor(s / 60);
  const rem = s % 60;
  return `${mins}:${rem < 10 ? '0' : ''}${rem}`;
}

export function formatTotalDuration(totalSec) {
  const s = Math.max(0, Math.floor(Number(totalSec) || 0));
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const rem = s % 60;
  if (hrs > 0) return `${hrs} hr ${mins} min`;
  if (mins > 0) return `${mins} min ${rem} sec`;
  return `${rem} sec`;
}

export function formatRelativeTime(unixSec) {
  const diff = Math.max(0, Math.floor(Date.now() / 1000) - (Number(unixSec) || 0));
  if (diff < 45) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 7 * 86400) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(unixSec * 1000).toLocaleDateString();
}

export function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const HEX_COLOR_RE = /^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$/;
const UNSAFE_URL_CHARS_RE = /[\x00-\x1f\x7f'"()\\<>\s]/;

export function sanitizeSafeUrl(url) {
  const raw = String(url ?? '').trim();
  if (!raw || raw.length > 512) return '';
  if (UNSAFE_URL_CHARS_RE.test(raw)) return '';
  if (raw.startsWith('//')) return '';
  if (raw.startsWith('/api/artwork/')) {
    return raw.includes('..') ? '' : raw;
  }
  const low = raw.toLowerCase();
  if (low.startsWith('https://') || low.startsWith('http://')) {
    return raw;
  }
  return '';
}

export function sanitizeCssColor(color, fallback = '') {
  const raw = String(color ?? '').trim();
  return HEX_COLOR_RE.test(raw) ? raw : fallback;
}

export function coverUrlFor(item, large = false) {
  if (!item) return '/api/artwork/sonify';
  const rawUrl = large
    ? item.coverLarge || item.pictureLarge || item.coverSmall || item.pictureSmall
    : item.coverSmall || item.pictureSmall || item.coverLarge || item.pictureLarge;
  const safe = sanitizeSafeUrl(rawUrl);
  if (safe) return safe;
  const seed = encodeURIComponent(item.title || item.name || item.id || 'sonify');
  return `/api/artwork/${seed}`;
}
