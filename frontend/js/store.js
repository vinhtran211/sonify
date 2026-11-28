// Reactive application store with localStorage backup & ephemeral-disk auto-restore

import { api } from './api.js';
import { getDownloadedIds } from './offlineDownloads.js';

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
            coverColor: pl.coverColor || '',
            coverEmoji: pl.coverEmoji || '',
            coverUrl: pl.coverUrl || '',
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
  drawerMode: null, // null | 'now-playing' | 'queue' | 'lyrics'
  queueSubTab: 'queue', // 'queue' | 'recent'
  libraryFilter: 'all', // 'all' | 'playlists' | 'artists' | 'albums' | 'downloads'
  librarySort: initialPrefs.librarySort || 'recents', // 'recents' | 'alpha'
  librarySearch: '',
  fullScreenMode: false,
  recentSearches: Array.isArray(initialPrefs.recentSearches) ? initialPrefs.recentSearches : [],

  // Catalog cache in client memory
  homeData: null,
  madeForYou: [],

  // Library & History
  likedIds: new Set(),
  likedTracks: [],
  playlists: [],
  history: [],
  followedArtists: [],
  followedArtistIds: new Set(),
  savedAlbums: [],
  savedAlbumIds: new Set(),
  pinnedItems: [],
  downloadedIds: getDownloadedIds(),

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
  streamMode: null, // 'full' | 'preview' | 'offline-download' | 'offline-synth' | 'error'
  streamSource: '',

  // Audio Settings & Spotify Parity Controls
  autoplay: initialPrefs.autoplay !== undefined ? Boolean(initialPrefs.autoplay) : true,
  crossfade: typeof initialPrefs.crossfade === 'number' ? initialPrefs.crossfade : 0,
  playbackRate: typeof initialPrefs.playbackRate === 'number' ? initialPrefs.playbackRate : 1.0,
  audioQuality: ['auto', 'compact', 'lossless', 'fast'].includes(initialPrefs.audioQuality)
    ? initialPrefs.audioQuality
    : 'auto',
  eqPreset: typeof initialPrefs.eqPreset === 'string' ? initialPrefs.eqPreset : 'flat',
  eqBands:
    Array.isArray(initialPrefs.eqBands) && initialPrefs.eqBands.length === 5
      ? initialPrefs.eqBands.map((v) => Math.max(-12, Math.min(12, Number(v) || 0)))
      : [0, 0, 0, 0, 0],
  sleepTimer: { mode: 'off', endsAt: null, remainingSec: 0 },

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
    librarySort: state.librarySort,
    autoplay: state.autoplay,
    crossfade: state.crossfade,
    playbackRate: state.playbackRate,
    audioQuality: state.audioQuality,
    eqPreset: state.eqPreset,
    eqBands: state.eqBands,
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
    coverColor: playlist.coverColor || '',
    coverEmoji: playlist.coverEmoji || '',
    coverUrl: playlist.coverUrl || '',
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
          coverColor: pl.coverColor || '',
          coverEmoji: pl.coverEmoji || '',
          coverUrl: pl.coverUrl || '',
          tracks: res?.tracks || [],
        };
      } catch {
        const fallback = prevById.get(pl.id);
        return {
          id: pl.id,
          name: pl.name,
          description: pl.description || '',
          coverColor: pl.coverColor || fallback?.coverColor || '',
          coverEmoji: pl.coverEmoji || fallback?.coverEmoji || '',
          coverUrl: pl.coverUrl || fallback?.coverUrl || '',
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
  const [settingsRes, likesRes, playlistsRes, historyRes, artistsRes, albumsRes, pinsRes] =
    await Promise.allSettled([
      api.getSettings(),
      api.getLikes(),
      api.listPlaylists(),
      api.getHistory(30),
      api.getFollowedArtists(),
      api.getSavedAlbums(),
      api.getPins(),
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
            if (newId && (pl.coverColor || pl.coverEmoji || pl.coverUrl)) {
              await api
                .updatePlaylist(newId, {
                  coverColor: pl.coverColor || '',
                  coverEmoji: pl.coverEmoji || '',
                  coverUrl: pl.coverUrl || '',
                })
                .catch(() => {});
            }
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
      const restored = {};
      if (typeof localPrefs.volume === 'number') restored.volume = localPrefs.volume;
      if (typeof localPrefs.muted === 'boolean') restored.muted = localPrefs.muted;
      if (typeof localPrefs.shuffle === 'boolean') restored.shuffle = localPrefs.shuffle;
      if (['off', 'all', 'one'].includes(localPrefs.repeat)) restored.repeat = localPrefs.repeat;
      if (['global', 'vn'].includes(localPrefs.region)) restored.region = localPrefs.region;
      if (typeof localPrefs.autoplay === 'boolean') restored.autoplay = localPrefs.autoplay;
      if (typeof localPrefs.crossfade === 'number') restored.crossfade = localPrefs.crossfade;
      if (typeof localPrefs.playbackRate === 'number') restored.playbackRate = localPrefs.playbackRate;
      if (['auto', 'compact', 'lossless', 'fast'].includes(localPrefs.audioQuality)) restored.audioQuality = localPrefs.audioQuality;
      if (typeof localPrefs.eqPreset === 'string') restored.eqPreset = localPrefs.eqPreset;
      if (Array.isArray(localPrefs.eqBands) && localPrefs.eqBands.length === 5) restored.eqBands = localPrefs.eqBands;
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
      if (typeof s.autoplay === 'boolean') patch.autoplay = s.autoplay;
      if (typeof s.crossfade === 'number') patch.crossfade = Math.max(0, Math.min(12, s.crossfade));
      if (typeof s.playbackRate === 'number') patch.playbackRate = Math.max(0.5, Math.min(2.0, s.playbackRate));
      if (['auto', 'compact', 'lossless', 'fast'].includes(s.audioQuality)) patch.audioQuality = s.audioQuality;
      if (typeof s.eqPreset === 'string') patch.eqPreset = s.eqPreset;
      if (Array.isArray(s.eqBands) && s.eqBands.length === 5) {
        patch.eqBands = s.eqBands.map((v) => Math.max(-12, Math.min(12, Number(v) || 0)));
      }
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
  if (artistsRes.status === 'fulfilled' && artistsRes.value) {
    const list = artistsRes.value.artists || [];
    patch.followedArtists = list;
    patch.followedArtistIds = new Set(artistsRes.value.artistIds || list.map((a) => a.id));
  }
  if (albumsRes.status === 'fulfilled' && albumsRes.value) {
    const list = albumsRes.value.albums || [];
    patch.savedAlbums = list;
    patch.savedAlbumIds = new Set(albumsRes.value.albumIds || list.map((a) => a.id));
  }
  if (pinsRes.status === 'fulfilled' && pinsRes.value) {
    patch.pinnedItems = pinsRes.value.pins || [];
  }

  patch.downloadedIds = getDownloadedIds();
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

export async function refreshFollowedArtists() {
  const res = await api.getFollowedArtists();
  const list = res.artists || [];
  setState({
    followedArtists: list,
    followedArtistIds: new Set(res.artistIds || list.map((a) => a.id)),
  });
}

export async function refreshSavedAlbums() {
  const res = await api.getSavedAlbums();
  const list = res.albums || [];
  setState({
    savedAlbums: list,
    savedAlbumIds: new Set(res.albumIds || list.map((a) => a.id)),
  });
}

export async function refreshPins() {
  const res = await api.getPins();
  setState({ pinnedItems: res.pins || [] });
}

export function refreshDownloads() {
  setState({ downloadedIds: getDownloadedIds() });
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

export async function toggleFollowArtist(artist) {
  if (!artist || !artist.id) return false;
  const isFollowing = state.followedArtistIds.has(artist.id);
  const nextIds = new Set(state.followedArtistIds);
  if (isFollowing) {
    nextIds.delete(artist.id);
    const nextList = state.followedArtists.filter((a) => a.id !== artist.id);
    setState({ followedArtistIds: nextIds, followedArtists: nextList });
    try {
      await api.unfollowArtist(artist.id);
      return false;
    } catch (err) {
      await refreshFollowedArtists().catch(() => {});
      throw err;
    }
  } else {
    nextIds.add(artist.id);
    const item = {
      id: artist.id,
      name: artist.name || 'Artist',
      image: artist.image || '',
      fans: Number(artist.fans) || 0,
      followedAt: Math.floor(Date.now() / 1000),
    };
    const nextList = [item, ...state.followedArtists.filter((a) => a.id !== artist.id)];
    setState({ followedArtistIds: nextIds, followedArtists: nextList });
    try {
      await api.followArtist(item);
      return true;
    } catch (err) {
      await refreshFollowedArtists().catch(() => {});
      throw err;
    }
  }
}

export async function toggleSaveAlbum(album) {
  if (!album || !album.id) return false;
  const isSaved = state.savedAlbumIds.has(album.id);
  const nextIds = new Set(state.savedAlbumIds);
  if (isSaved) {
    nextIds.delete(album.id);
    const nextList = state.savedAlbums.filter((a) => a.id !== album.id);
    setState({ savedAlbumIds: nextIds, savedAlbums: nextList });
    try {
      await api.unsaveAlbum(album.id);
      return false;
    } catch (err) {
      await refreshSavedAlbums().catch(() => {});
      throw err;
    }
  } else {
    nextIds.add(album.id);
    const item = {
      id: album.id,
      title: album.title || 'Album',
      artist: album.artist || '',
      artistId: album.artistId || '',
      cover: album.cover || album.coverLarge || album.coverSmall || '',
      releaseDate: album.releaseDate || '',
      savedAt: Math.floor(Date.now() / 1000),
    };
    const nextList = [item, ...state.savedAlbums.filter((a) => a.id !== album.id)];
    setState({ savedAlbumIds: nextIds, savedAlbums: nextList });
    try {
      await api.saveAlbum(item);
      return true;
    } catch (err) {
      await refreshSavedAlbums().catch(() => {});
      throw err;
    }
  }
}

export function isItemPinned(itemType, itemId) {
  return state.pinnedItems.some((p) => p.itemType === itemType && p.itemId === itemId);
}

export async function togglePinItem(itemType, itemId) {
  const pinned = isItemPinned(itemType, itemId);
  if (pinned) {
    const next = state.pinnedItems.filter((p) => !(p.itemType === itemType && p.itemId === itemId));
    setState({ pinnedItems: next });
    try {
      await api.unpinItem(itemType, itemId);
      return false;
    } catch (err) {
      await refreshPins().catch(() => {});
      throw err;
    }
  } else {
    const next = [{ itemType, itemId, pinnedAt: Math.floor(Date.now() / 1000) }, ...state.pinnedItems];
    setState({ pinnedItems: next });
    try {
      await api.pinItem(itemType, itemId);
      return true;
    } catch (err) {
      await refreshPins().catch(() => {});
      throw err;
    }
  }
}

export function updateAudioSettings(partial) {
  const patch = {};
  const serverPatch = {};

  if (typeof partial.autoplay === 'boolean') {
    patch.autoplay = partial.autoplay;
    serverPatch.autoplay = partial.autoplay;
  }
  if (typeof partial.crossfade === 'number') {
    const v = Math.max(0, Math.min(12, Math.round(partial.crossfade)));
    patch.crossfade = v;
    serverPatch.crossfade = v;
  }
  if (typeof partial.playbackRate === 'number') {
    const v = Math.max(0.5, Math.min(2.0, Math.round(partial.playbackRate * 100) / 100));
    patch.playbackRate = v;
    serverPatch.playbackRate = v;
  }
  if (['auto', 'compact', 'lossless', 'fast'].includes(partial.audioQuality)) {
    patch.audioQuality = partial.audioQuality;
    serverPatch.audioQuality = partial.audioQuality;
  }
  if (typeof partial.eqPreset === 'string') {
    patch.eqPreset = partial.eqPreset;
    serverPatch.eqPreset = partial.eqPreset;
  }
  if (Array.isArray(partial.eqBands) && partial.eqBands.length === 5) {
    const bands = partial.eqBands.map((v) => Math.max(-12, Math.min(12, Math.round(Number(v) || 0))));
    patch.eqBands = bands;
    serverPatch.eqBands = bands;
  }

  if (Object.keys(patch).length > 0) {
    setState(patch);
    persistPrefs(serverPatch);
  }
}

export function setFullScreenMode(enabled) {
  const next = Boolean(enabled);
  if (state.fullScreenMode !== next) {
    setState({ fullScreenMode: next });
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
  setState({ region: reg, homeData: null, madeForYou: [] });
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
