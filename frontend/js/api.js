// REST API client for Sonify backend

async function request(path, options = {}) {
  const init = {
    method: options.method || 'GET',
    headers: { Accept: 'application/json', ...(options.headers || {}) },
    signal: options.signal,
  };
  if (options.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(options.body);
  }

  const res = await fetch(path, init);
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    const msg = data?.error?.message || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    err.code = data?.error?.code || 'API_ERROR';
    throw err;
  }
  return data;
}

export const api = {
  health: () => request('/api/health'),

  getHome: (region = 'global', refresh = false) =>
    request(`/api/home?region=${encodeURIComponent(region)}&refresh=${refresh ? '1' : '0'}`),

  search: (query, type = 'all', limit = 20, signal = undefined) =>
    request(
      `/api/search?q=${encodeURIComponent(query)}&type=${encodeURIComponent(type)}&limit=${limit}`,
      { signal }
    ),

  getArtist: (artistId) => request(`/api/artist/${encodeURIComponent(artistId)}`),

  getAlbum: (albumId) => request(`/api/album/${encodeURIComponent(albumId)}`),

  getStreamInfo: (trackId, signal = undefined, quality = 'auto') => {
    const q = quality && quality !== 'auto' ? `?quality=${encodeURIComponent(quality)}` : '';
    return request(`/api/stream/${encodeURIComponent(trackId)}/info${q}`, { signal });
  },

  getLyrics: ({ trackId = '', title = '', artist = '', album = '', duration = 0, refresh = false }, signal = undefined) => {
    const params = new URLSearchParams();
    if (trackId) params.set('trackId', trackId);
    if (title) params.set('title', title);
    if (artist) params.set('artist', artist);
    if (album) params.set('album', album);
    if (duration) params.set('duration', String(Math.round(duration)));
    if (refresh) params.set('refresh', '1');
    return request(`/api/lyrics?${params.toString()}`, { signal });
  },

  // Liked Songs
  getLikes: () => request('/api/library/likes'),
  likeTrack: (track) => request('/api/library/likes', { method: 'POST', body: { track } }),
  unlikeTrack: (trackId) =>
    request(`/api/library/likes/${encodeURIComponent(trackId)}`, { method: 'DELETE' }),

  // Playlists CRUD
  listPlaylists: () => request('/api/playlists'),
  createPlaylist: (name, description = '') =>
    request('/api/playlists', { method: 'POST', body: { name, description } }),
  getPlaylist: (playlistId) => request(`/api/playlists/${encodeURIComponent(playlistId)}`),
  updatePlaylist: (playlistId, payload) =>
    request(`/api/playlists/${encodeURIComponent(playlistId)}`, { method: 'PATCH', body: payload }),
  deletePlaylist: (playlistId) =>
    request(`/api/playlists/${encodeURIComponent(playlistId)}`, { method: 'DELETE' }),
  addTrackToPlaylist: (playlistId, track) =>
    request(`/api/playlists/${encodeURIComponent(playlistId)}/tracks`, {
      method: 'POST',
      body: { track },
    }),
  removeTrackFromPlaylist: (playlistId, trackId) =>
    request(
      `/api/playlists/${encodeURIComponent(playlistId)}/tracks/${encodeURIComponent(trackId)}`,
      { method: 'DELETE' }
    ),
  reorderPlaylistTracks: (playlistId, trackIds) =>
    request(`/api/playlists/${encodeURIComponent(playlistId)}/tracks/reorder`, {
      method: 'PATCH',
      body: { trackIds },
    }),

  // History
  getHistory: (limit = 50) => request(`/api/history?limit=${limit}`),
  recordHistory: (track) => request('/api/history', { method: 'POST', body: { track } }),
  clearHistory: () => request('/api/history', { method: 'DELETE' }),

  // Settings
  getSettings: () => request('/api/settings'),
  patchSettings: (partial) => request('/api/settings', { method: 'PATCH', body: partial }),

  // iOS Direct Install & Distribution
  getIosInstallInfo: () => request('/api/ios/install-info'),

  // Spotify Parity: Radio, Made For You, Genres, Track Detail, Stats, Follows, Albums, Pins, Duplicate/Import/Recs
  getRadio: ({ trackId = '', artistId = '', limit = 25 } = {}) => {
    const params = new URLSearchParams();
    if (trackId) params.set('trackId', trackId);
    if (artistId) params.set('artistId', artistId);
    params.set('limit', String(limit));
    return request(`/api/radio?${params.toString()}`);
  },
  getMadeForYou: (region = 'global') =>
    request(`/api/made-for-you?region=${encodeURIComponent(region)}`),
  getGenreHub: (slug, region = 'global') =>
    request(`/api/genre/${encodeURIComponent(slug)}?region=${encodeURIComponent(region)}`),
  getTrackDetail: (trackId) => request(`/api/track/${encodeURIComponent(trackId)}`),
  getStats: () => request('/api/stats'),

  getPlaylistRecommendations: (playlistId, limit = 10) =>
    request(`/api/playlists/${encodeURIComponent(playlistId)}/recommendations?limit=${limit}`),
  duplicatePlaylist: (playlistId, name = null) =>
    request(`/api/playlists/${encodeURIComponent(playlistId)}/duplicate`, {
      method: 'POST',
      body: name ? { name } : {},
    }),
  importPlaylist: (payload) =>
    request('/api/playlists/import', { method: 'POST', body: payload }),

  getFollowedArtists: () => request('/api/library/artists'),
  followArtist: (artist) => request('/api/library/artists', { method: 'POST', body: { artist } }),
  unfollowArtist: (artistId) =>
    request(`/api/library/artists/${encodeURIComponent(artistId)}`, { method: 'DELETE' }),

  getSavedAlbums: () => request('/api/library/albums'),
  saveAlbum: (album) => request('/api/library/albums', { method: 'POST', body: { album } }),
  unsaveAlbum: (albumId) =>
    request(`/api/library/albums/${encodeURIComponent(albumId)}`, { method: 'DELETE' }),

  getPins: () => request('/api/library/pins'),
  pinItem: (itemType, itemId) =>
    request('/api/library/pins', { method: 'POST', body: { itemType, itemId } }),
  unpinItem: (itemType, itemId) =>
    request(
      `/api/library/pins/${encodeURIComponent(itemType)}/${encodeURIComponent(itemId)}`,
      { method: 'DELETE' }
    ),
};
