// Reactive application store with localStorage backup & ephemeral-disk auto-restore

import { api } from './api.js';

const LS_KEY = 'sonify_prefs_v1';
const LS_LIBRARY_KEY = 'sonify_library_backup_v1';

function readJsonStorage(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function writeJsonStorage(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Ignore storage quota or private-mode errors
  }
}

function sanitizeTrackForBackup(track) {
  if (!track || !track.id) return null;
  return {
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
    releaseDate: track.releaseDate || '',
    previewUrl: track.previewUrl || '',
    streamUrl: track.streamUrl || `/api/stream/${track.id}`,
  };
}

function readLibraryBackup() {
  const data = readJsonStorage(LS_LIBRARY_KEY);
  return {
    likedTracks: Array.isArray(data.likedTracks) ? data.likedTracks : [],
    playlists: Array.isArray(data.playlists) ? data.playlists : [],
    updatedAt: Number(data.updatedAt) || 0,
  };
}

function writeLibraryBackup(partial) {
  const cur = readLibraryBackup();
  const next = {
    likedTracks:
      partial.likedTracks !== undefined
        ? partial.likedTracks.map(sanitizeTrackForBackup).filter(Boolean).slice(0, 500)
        : cur.likedTracks,
    playlists:
      partial.playlists !== undefined
        ? partial.playlists.slice(0, 50).map((pl) => ({
            id: pl.id,
            name: pl.name || 'Playlist',
            description: pl.description || '',
            tracks: Array.isArray(pl.tracks)
              ? pl.tracks.map(sanitizeTrackForBackup).filter(Boolean).slice(0, 300)
              : [],
          }))
        : cur.playlists,
    updatedAt: Date.now(),
  };
  writeJsonStorage(LS_LIBRARY_KEY, next);
}

const initialPrefs = readJsonStorage(LS_KEY);

const state = {
  // Navigation & Global UI
  route: { path: '/', params: {}, query: {} },
  region: initialPrefs.region || 'global',
  offlineMode: false,
  drawerMode: null, // null | 'queue' | 'lyrics'
  libraryFilter: 'all', // 'all' | 'playlists'
  recentSearches: Array.isArray(initialPrefs.recentSearches) ? initialPrefs.recentSearches : [],

  // Catalog cache in client memory
  homeData: null,

  // Library & History
  likedIds: new Set(),
  likedTracks: [],
  playlists: [],
  history: [],

  // Player state
  currentTrack: null,
  isPlaying: false,
  isLoading: false,
  currentTime: 0,
  duration: 0,
  buffered: 0,
  volume: typeof initialPrefs.volume === 'number' ? initialPrefs.volume : 0.8,
  muted: Boolean(initialPrefs.muted),
  shuffle: Boolean(initialPrefs.shuffle),
  repeat: initialPrefs.repeat || 'off', // 'off' | 'all' | 'one'
  streamMode: null, // 'full' | 'preview' | 'offline-synth' | 'error'
  streamSource: '',

  // Queue state
  queue: [],       // Context tracks list
  queueIndex: -1,  // Active index inside queue
  userQueue: [],   // Tracks explicitly queued via "Play Next" / "Add to Queue"

  // Lyrics state
  lyricsTrackId: null,
  lyricsData: null,
  lyricsLoading: false,
};

const listeners = new Set();
let settingsTimer = null;
let pendingServerSettings = {};
let restoringBackup = false;

export function getState() {
  return state;
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function emit(changedKeys = []) {
  const keySet = new Set(changedKeys);
  for (const fn of listeners) {
    try {
      fn(state, keySet);
    } catch (err) {
      console.error('[Sonify Store] Listener error:', err);
    }
  }
}

export function setState(patch) {
  const keys = Object.keys(patch);
  Object.assign(state, patch);
  emit(keys);
}

export function persistPrefs(partialServerSettings = null) {
  const prev = readJsonStorage(LS_KEY);
  writeJsonStorage(LS_KEY, {
    savedByUser: Boolean(partialServerSettings || prev.savedByUser),
    volume: state.volume,
    muted: state.muted,
    shuffle: state.shuffle,
    repeat: state.repeat,
    region: state.region,
    recentSearches: state.recentSearches,
  });

  if (partialServerSettings) {
    Object.assign(pendingServerSettings, partialServerSettings);
    clearTimeout(settingsTimer);
    settingsTimer = setTimeout(() => {
      const payload = { ...pendingServerSettings };
      pendingServerSettings = {};
      api.patchSettings(payload).catch(() => {});
    }, 250);
  }
}

/**
 * Cache a single playlist's full track list into localStorage backup so ephemeral
 * container restarts (e.g. free Hugging Face Spaces sleep/rebuild) preserve tracks & order.
 */
export function backupPlaylistDetail(playlist, tracks = []) {
  if (!playlist || !playlist.id) return;
  const cur = readLibraryBackup();
  const entry = {
    id: playlist.id,
    name: playlist.name || 'Playlist',
    description: playlist.description || '',
    tracks: Array.isArray(tracks) ? tracks : [],
  };
  const nextPlaylists = cur.playlists.filter((p) => p.id !== playlist.id);
  nextPlaylists.unshift(entry);
  writeLibraryBackup({ playlists: nextPlaylists });
}

async function syncPlaylistsBackup(playlists) {
  const list = Array.isArray(playlists) ? playlists : [];
  if (list.length === 0) {
    writeLibraryBackup({ playlists: [] });
    return;
  }

  const prevById = new Map(readLibraryBackup().playlists.map((p) => [p.id, p]));
  const detailed = await Promise.all(
    list.slice(0, 50).map(async (pl) => {
      try {
        const res = await api.getPlaylist(pl.id);
        return {
          id: pl.id,
          name: pl.name,
          description: pl.description || '',
          tracks: res?.tracks || [],
        };
      } catch {
        const fallback = prevById.get(pl.id);
        return {
          id: pl.id,
          name: pl.name,
          description: pl.description || '',
          tracks: fallback?.tracks || [],
        };
      }
    })
  );
  writeLibraryBackup({ playlists: detailed });
}

function isFreshServerSettings(s) {
  return (
    s &&
    s.volume === 0.8 &&
    s.muted === false &&
    s.shuffle === false &&
    s.repeat === 'off' &&
    s.region === 'global' &&
    !s.lastTrackId
  );
}

export async function hydrateFromBackend() {
  const [settingsRes, likesRes, playlistsRes, historyRes] = await Promise.allSettled([
    api.getSettings(),
    api.getLikes(),
    api.listPlaylists(),
    api.getHistory(30),
  ]);

  const localPrefs = readJsonStorage(LS_KEY);
  const backup = readLibraryBackup();

  let serverLikes =
    likesRes.status === 'fulfilled' && likesRes.value ? likesRes.value.tracks || [] : [];
  let serverLikedIds =
    likesRes.status === 'fulfilled' && likesRes.value ? likesRes.value.likedIds || [] : [];
  let serverPlaylists =
    playlistsRes.status === 'fulfilled' && playlistsRes.value
      ? playlistsRes.value.playlists || []
      : [];
  const serverHistory =
    historyRes.status === 'fulfilled' && historyRes.value ? historyRes.value.items || [] : [];

  const serverLooksFresh =
    likesRes.status === 'fulfilled' &&
    playlistsRes.status === 'fulfilled' &&
    serverLikes.length === 0 &&
    serverPlaylists.length === 0 &&
    serverHistory.length === 0;

  // Auto-restore from browser localStorage backup if the cloud Space container restarted with an empty DB
  if (!restoringBackup && (likesRes.status === 'fulfilled' || playlistsRes.status === 'fulfilled')) {
    restoringBackup = true;
    try {
      if (likesRes.status === 'fulfilled' && serverLikes.length === 0 && backup.likedTracks.length > 0) {
        for (const tr of backup.likedTracks.slice().reverse()) {
          if (tr?.id) {
            await api.likeTrack(tr).catch(() => {});
          }
        }
        const refreshedLikes = await api.getLikes().catch(() => null);
        if (refreshedLikes) {
          serverLikes = refreshedLikes.tracks || [];
          serverLikedIds = refreshedLikes.likedIds || [];
        }
      }

      if (
        playlistsRes.status === 'fulfilled' &&
        serverPlaylists.length === 0 &&
        backup.playlists.length > 0
      ) {
        for (const pl of backup.playlists.slice().reverse()) {
          if (!pl?.name) continue;
          try {
            const created = await api.createPlaylist(pl.name, pl.description || '');
            const newId = created?.playlist?.id;
            if (newId && Array.isArray(pl.tracks)) {
              for (const tr of pl.tracks) {
                if (tr?.id) {
                  await api.addTrackToPlaylist(newId, tr).catch(() => {});
                }
              }
            }
          } catch {
            // Continue restoring remaining playlists
          }
        }
        const refreshedPls = await api.listPlaylists().catch(() => null);
        if (refreshedPls) {
          serverPlaylists = refreshedPls.playlists || [];
        }
      }
    } finally {
      restoringBackup = false;
    }
  }

  const patch = {};
  if (settingsRes.status === 'fulfilled' && settingsRes.value) {
    const s = settingsRes.value;
    if (serverLooksFresh && localPrefs.savedByUser && isFreshServerSettings(s)) {
      // Restore customized browser settings to fresh ephemeral server DB
      const restored = {};
      if (typeof localPrefs.volume === 'number') restored.volume = localPrefs.volume;
      if (typeof localPrefs.muted === 'boolean') restored.muted = localPrefs.muted;
      if (typeof localPrefs.shuffle === 'boolean') restored.shuffle = localPrefs.shuffle;
      if (['off', 'all', 'one'].includes(localPrefs.repeat)) restored.repeat = localPrefs.repeat;
      if (['global', 'vn'].includes(localPrefs.region)) restored.region = localPrefs.region;
      Object.assign(patch, restored);
      if (Object.keys(restored).length > 0) {
        api.patchSettings(restored).catch(() => {});
      }
    } else {
      if (typeof s.volume === 'number') patch.volume = s.volume;
      if (typeof s.muted === 'boolean') patch.muted = s.muted;
      if (typeof s.shuffle === 'boolean') patch.shuffle = s.shuffle;
      if (['off', 'all', 'one'].includes(s.repeat)) patch.repeat = s.repeat;
      if (['global', 'vn'].includes(s.region)) patch.region = s.region;
    }
  }

  if (likesRes.status === 'fulfilled') {
    patch.likedTracks = serverLikes;
    patch.likedIds = new Set(serverLikedIds);
    writeLibraryBackup({ likedTracks: serverLikes });
  }
  if (playlistsRes.status === 'fulfilled') {
    patch.playlists = serverPlaylists;
    syncPlaylistsBackup(serverPlaylists).catch(() => {});
  }
  if (historyRes.status === 'fulfilled') {
    patch.history = serverHistory;
  }

  setState(patch);
  persistPrefs();
}

export async function refreshPlaylists() {
  const res = await api.listPlaylists();
  const playlists = res.playlists || [];
  setState({ playlists });
  syncPlaylistsBackup(playlists).catch(() => {});
}

export async function refreshLikes() {
  const res = await api.getLikes();
  const tracks = res.tracks || [];
  setState({
    likedTracks: tracks,
    likedIds: new Set(res.likedIds || []),
  });
  writeLibraryBackup({ likedTracks: tracks });
}

export async function refreshHistory() {
  const res = await api.getHistory(50);
  setState({ history: res.items || [] });
}

export async function toggleLike(track) {
  if (!track || !track.id) return false;
  const isLiked = state.likedIds.has(track.id);
  const nextIds = new Set(state.likedIds);
  if (isLiked) {
    nextIds.delete(track.id);
    const nextTracks = state.likedTracks.filter((t) => t.id !== track.id);
    setState({ likedIds: nextIds, likedTracks: nextTracks });
    try {
      await api.unlikeTrack(track.id);
      writeLibraryBackup({ likedTracks: nextTracks });
      return false;
    } catch (err) {
      await refreshLikes().catch(() => {});
      throw err;
    }
  } else {
    nextIds.add(track.id);
    const updatedTrack = { ...track, liked: true };
    const nextTracks = [updatedTrack, ...state.likedTracks.filter((t) => t.id !== track.id)];
    setState({ likedIds: nextIds, likedTracks: nextTracks });
    try {
      await api.likeTrack(track);
      writeLibraryBackup({ likedTracks: nextTracks });
      return true;
    } catch (err) {
      await refreshLikes().catch(() => {});
      throw err;
    }
  }
}

export function addRecentSearch(query) {
  const q = (query || '').trim();
  if (!q) return;
  const filtered = state.recentSearches.filter((x) => x.toLowerCase() !== q.toLowerCase());
  const updated = [q, ...filtered].slice(0, 8);
  setState({ recentSearches: updated });
  persistPrefs();
}

export function clearRecentSearches() {
  setState({ recentSearches: [] });
  persistPrefs();
}

export function setRegion(region) {
  const reg = region === 'vn' ? 'vn' : 'global';
  if (state.region === reg) return;
  setState({ region: reg, homeData: null });
  persistPrefs({ region: reg });
}

export function toggleDrawer(mode) {
  const next = state.drawerMode === mode ? null : mode;
  setState({ drawerMode: next });
}

export function closeDrawer() {
  if (state.drawerMode !== null) {
    setState({ drawerMode: null });
  }
}
