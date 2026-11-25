// Spotify Premium-Style Offline Track & Playlist Downloads (Cache API + localStorage metadata)

const AUDIO_CACHE_NAME = 'sonify-offline-audio-v1';
const META_STORAGE_KEY = 'sonify_downloads_v1';

function readDownloadsMeta() {
  try {
    const raw = localStorage.getItem(META_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeDownloadsMeta(list) {
  try {
    localStorage.setItem(META_STORAGE_KEY, JSON.stringify(list.slice(0, 300)));
  } catch {
    // Ignore storage quota errors
  }
}

function offlineAudioKey(trackId) {
  return `/offline-audio/${encodeURIComponent(trackId)}`;
}

export function getAllDownloads() {
  return readDownloadsMeta();
}

export function getDownloadedIds() {
  return new Set(readDownloadsMeta().map((t) => t.id));
}

export function isDownloaded(trackId) {
  if (!trackId) return false;
  return readDownloadsMeta().some((t) => t.id === trackId);
}

export async function downloadTrack(track) {
  if (!track || !track.id) throw new Error('Invalid track');
  if (!('caches' in window)) {
    throw new Error('Offline Cache API is not supported in this browser');
  }

  const streamEndpoint = `/api/stream/${encodeURIComponent(track.id)}`;
  const res = await fetch(streamEndpoint);
  if (!res.ok) {
    throw new Error(`Could not download audio (HTTP ${res.status})`);
  }

  const contentType = res.headers.get('Content-Type') || 'audio/mpeg';
  const blob = await res.blob();
  if (!blob || blob.size < 1024) {
    throw new Error('Downloaded audio payload was empty');
  }

  const cache = await caches.open(AUDIO_CACHE_NAME);
  await cache.put(
    offlineAudioKey(track.id),
    new Response(blob, {
      status: 200,
      headers: {
        'Content-Type': contentType,
        'Content-Length': String(blob.size),
      },
    })
  );

  const list = readDownloadsMeta().filter((t) => t.id !== track.id);
  list.unshift({
    id: track.id,
    source: track.source || '',
    title: track.title || 'Untitled',
    artist: track.artist || 'Unknown Artist',
    artistId: track.artistId || '',
    album: track.album || '',
    albumId: track.albumId || '',
    duration: Number(track.duration) || 0,
    coverSmall: track.coverSmall || '',
    coverLarge: track.coverLarge || '',
    explicit: Boolean(track.explicit),
    downloadedAt: Date.now(),
    sizeBytes: blob.size,
  });
  writeDownloadsMeta(list);
  return true;
}

export async function downloadPlaylist(tracks, onProgress = null) {
  const items = Array.isArray(tracks) ? tracks : [];
  let completed = 0;
  let failed = 0;
  for (const tr of items) {
    if (!tr?.id) continue;
    if (!isDownloaded(tr.id)) {
      try {
        await downloadTrack(tr);
      } catch {
        failed += 1;
      }
    }
    completed += 1;
    if (onProgress) onProgress(completed, items.length, failed);
  }
  return { completed, total: items.length, failed };
}

export async function removeDownload(trackId) {
  if (!trackId) return false;
  if ('caches' in window) {
    try {
      const cache = await caches.open(AUDIO_CACHE_NAME);
      await cache.delete(offlineAudioKey(trackId));
    } catch {
      // ignore
    }
  }
  const next = readDownloadsMeta().filter((t) => t.id !== trackId);
  writeDownloadsMeta(next);
  return true;
}

export async function getBlobUrl(trackId) {
  if (!trackId || !('caches' in window) || !isDownloaded(trackId)) return null;
  try {
    const cache = await caches.open(AUDIO_CACHE_NAME);
    const match = await cache.match(offlineAudioKey(trackId));
    if (!match) return null;
    const blob = await match.blob();
    if (!blob || blob.size < 1024) return null;
    // Always mint a fresh object URL; caller (player.js) owns URL.revokeObjectURL lifecycle
    return URL.createObjectURL(blob);
  } catch {
    return null;
  }
}
