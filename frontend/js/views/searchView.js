// Live Search View — Debounced search, Recent Searches, 12 Dedicated Genre/Mood Hubs, Top Result, Tabs

import { api } from '../api.js';
import { renderTrackTable } from '../components/trackList.js';
import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml } from '../lrc.js';
import { playList, playTrack } from '../player.js';
import { navigate } from '../router.js';
import { addRecentSearch, clearRecentSearches, getState, setState } from '../store.js';

const GENRE_TILES = [
  { slug: 'pop', label: 'Pop', bg: 'linear-gradient(135deg, #e13300, #f59b23)' },
  { slug: 'vpop', label: 'V-Pop', bg: 'linear-gradient(135deg, #dc148c, #ff6b9d)' },
  { slug: 'hiphop', label: 'Hip-Hop', bg: 'linear-gradient(135deg, #ba5d07, #e89b3c)' },
  { slug: 'kpop', label: 'K-Pop', bg: 'linear-gradient(135deg, #148a08, #1db954)' },
  { slug: 'indie', label: 'Indie', bg: 'linear-gradient(135deg, #e91429, #ff6437)' },
  { slug: 'electronic', label: 'Electronic', bg: 'linear-gradient(135deg, #0d73ec, #48a9ff)' },
  { slug: 'chill', label: 'Chill', bg: 'linear-gradient(135deg, #477d95, #1e3264)' },
  { slug: 'workout', label: 'Workout', bg: 'linear-gradient(135deg, #777777, #2e2e2e)' },
  { slug: 'focus', label: 'Focus', bg: 'linear-gradient(135deg, #503750, #8c67ac)' },
  { slug: 'rnb', label: 'R&B / Soul', bg: 'linear-gradient(135deg, #7d4b32, #d84000)' },
  { slug: 'acoustic', label: 'Acoustic', bg: 'linear-gradient(135deg, #006450, #1db954)' },
  { slug: 'lofi', label: 'Lo-Fi Beats', bg: 'linear-gradient(135deg, #1e3264, #5c4b8a)' },
];

let searchAbort = null;
let lastQuery = '';
let activeTab = 'all'; // 'all' | 'tracks' | 'artists' | 'albums'
let lastResult = null;
let isSearching = false;

export async function runLiveSearch(query, container) {
  const q = (query || '').trim();
  if (q !== lastQuery) {
    activeTab = 'all';
  }
  lastQuery = q;

  if (!q) {
    lastResult = null;
    isSearching = false;
    renderSearchView(container);
    return;
  }

  if (searchAbort) searchAbort.abort();
  searchAbort = new AbortController();
  const signal = searchAbort.signal;

  isSearching = true;
  renderSearchView(container);

  try {
    const data = await api.search(q, 'all', 24, signal);
    if (lastQuery !== q) return;
    lastResult = data;
    isSearching = false;
    addRecentSearch(q);
    if (data.mode === 'offline' && !getState().offlineMode) {
      setState({ offlineMode: true });
    }
    renderSearchView(container);
  } catch (err) {
    if (err.name === 'AbortError') return;
    isSearching = false;
    lastResult = { query: q, mode: 'offline', topResult: null, tracks: [], artists: [], albums: [] };
    renderSearchView(container);
  }
}

export function renderSearchView(container) {
  container.classList.remove('no-padding');
  const st = getState();
  const queryFromRoute = (st.route?.query?.q || '').trim();

  if (queryFromRoute && queryFromRoute !== lastQuery) {
    runLiveSearch(queryFromRoute, container);
    return;
  }

  if (!queryFromRoute) {
    lastQuery = '';
    lastResult = null;
    isSearching = false;
  }

  // Empty search -> Recent Searches + Browse 12 Genre/Mood Hubs
  if (!lastQuery) {
    const recents = st.recentSearches || [];
    const recentHtml = recents.length
      ? `
        <div class="shelf-section">
          <div class="shelf-header">
            <h2 class="shelf-title">Recent Searches</h2>
            <button id="btn-clear-recents" class="btn-subtle btn-xs">Clear</button>
          </div>
          <div class="recent-chips">
            ${recents
              .map(
                (term) =>
                  `<button class="pill-btn recent-search-chip" data-q="${escapeHtml(term)}">${escapeHtml(term)}</button>`
              )
              .join('')}
          </div>
        </div>
      `
      : '';

    const genresHtml = GENRE_TILES.map(
      (g) => `
      <div class="genre-tile" style="background: ${g.bg};" data-genre-slug="${escapeHtml(g.slug)}">
        <div class="genre-tile-title">${escapeHtml(g.label)}</div>
        <div class="genre-tile-badge"></div>
      </div>
    `
    ).join('');

    container.innerHTML = `
      ${recentHtml}
      <div class="shelf-section">
        <div class="shelf-header">
          <div>
            <h2 class="shelf-title">Browse Genres &amp; Moods</h2>
            <p class="shelf-subtitle">Explore curated hubs across Pop, V-Pop, Hip-Hop, K-Pop, Chill, Focus, and Lo-Fi</p>
          </div>
        </div>
        <div class="genre-grid">${genresHtml}</div>
      </div>
    `;

    container.querySelector('#btn-clear-recents')?.addEventListener('click', () => {
      clearRecentSearches();
      renderSearchView(container);
    });

    container.querySelectorAll('.recent-search-chip').forEach((chip) => {
      chip.addEventListener('click', () => navigate(`#/search?q=${encodeURIComponent(chip.dataset.q)}`));
    });

    container.querySelectorAll('.genre-tile').forEach((tile) => {
      tile.addEventListener('click', () => navigate(`#/genre/${encodeURIComponent(tile.dataset.genreSlug)}`));
    });
    return;
  }

  // Searching state
  if (isSearching && (!lastResult || lastResult.query !== lastQuery)) {
    container.innerHTML = `
      <div class="search-tabs">
        <span class="pill-btn active">Searching "${escapeHtml(lastQuery)}"...</span>
      </div>
      <div style="display:flex; flex-direction:column; gap:10px; max-width:760px;">
        ${Array.from({ length: 6 }, () => '<div class="skeleton" style="height:56px;"></div>').join('')}
      </div>
    `;
    return;
  }

  const res = lastResult || { tracks: [], artists: [], albums: [], topResult: null };
  const tracks = res.tracks || [];
  const artists = res.artists || [];
  const albums = res.albums || [];
  const hasAny = tracks.length > 0 || artists.length > 0 || albums.length > 0;

  const tabsHtml = `
    <div class="search-tabs">
      <button class="pill-btn ${activeTab === 'all' ? 'active' : ''}" data-stab="all">All</button>
      <button class="pill-btn ${activeTab === 'tracks' ? 'active' : ''}" data-stab="tracks">Songs (${tracks.length})</button>
      <button class="pill-btn ${activeTab === 'artists' ? 'active' : ''}" data-stab="artists">Artists (${artists.length})</button>
      <button class="pill-btn ${activeTab === 'albums' ? 'active' : ''}" data-stab="albums">Albums (${albums.length})</button>
    </div>
  `;

  if (!hasAny) {
    container.innerHTML = `
      ${tabsHtml}
      <div class="empty-state">
        <h3>No results found for "${escapeHtml(lastQuery)}"</h3>
        <p>Check the spelling or try searching by artist name, song title, or genre.</p>
      </div>
    `;
    bindTabs(container);
    return;
  }

  let bodyHtml = '';

  if (activeTab === 'all') {
    const top = res.topResult;
    const topHtml = top
      ? `
        <div>
          <h2 class="shelf-title" style="margin-bottom: 12px;">Top Result</h2>
          <div class="top-result-card" id="search-top-card">
            <img class="top-result-art ${top.kind === 'artist' ? 'round' : ''}" src="${escapeHtml(coverUrlFor(top.item, true))}" alt="" />
            <h3 class="top-result-title truncate">${escapeHtml(top.item.title || top.item.name)}</h3>
            <div class="top-result-sub">
              <span class="chip-tag">${escapeHtml(top.kind === 'track' ? 'Song' : top.kind)}</span>
              <span class="truncate">${escapeHtml(top.item.artist || (top.item.fans ? `${Number(top.item.fans).toLocaleString()} fans` : ''))}</span>
            </div>
            <button class="play-fab" id="top-result-play" style="opacity:1; transform:none; right:18px; bottom:18px;" title="Play">${ICONS.play}</button>
          </div>
        </div>
      `
      : '';

    bodyHtml = `
      <div class="search-top-grid">
        ${topHtml}
        <div>
          <h2 class="shelf-title" style="margin-bottom: 12px;">Songs</h2>
          <div id="search-songs-slot"></div>
        </div>
      </div>
      ${
        artists.length
          ? `<div class="shelf-section">
               <h2 class="shelf-title" style="margin-bottom: 14px;">Artists</h2>
               <div class="card-grid">
                 ${artists
                   .slice(0, 6)
                   .map(
                     (a) => `
                   <div class="media-card artist-card" data-artist-id="${escapeHtml(a.id)}">
                     <div class="media-card-art">
                       <img src="${escapeHtml(coverUrlFor(a, true))}" alt="${escapeHtml(a.name)}" loading="lazy" />
                       <button class="play-fab" title="Open artist">${ICONS.play}</button>
                     </div>
                     <div class="media-card-title truncate">${escapeHtml(a.name)}</div>
                     <div class="media-card-sub">Artist</div>
                   </div>`
                   )
                   .join('')}
               </div>
             </div>`
          : ''
      }
      ${
        albums.length
          ? `<div class="shelf-section">
               <h2 class="shelf-title" style="margin-bottom: 14px;">Albums</h2>
               <div class="card-grid">
                 ${albums
                   .slice(0, 6)
                   .map(
                     (al) => `
                   <div class="media-card" data-album-id="${escapeHtml(al.id)}">
                     <div class="media-card-art">
                       <img src="${escapeHtml(coverUrlFor(al, true))}" alt="${escapeHtml(al.title)}" loading="lazy" />
                       <button class="play-fab" title="Open album">${ICONS.play}</button>
                     </div>
                     <div class="media-card-title truncate">${escapeHtml(al.title)}</div>
                     <div class="media-card-sub">${escapeHtml(al.artist || 'Album')}</div>
                   </div>`
                   )
                   .join('')}
               </div>
             </div>`
          : ''
      }
    `;
  } else if (activeTab === 'tracks') {
    bodyHtml = `<div id="search-songs-slot"></div>`;
  } else if (activeTab === 'artists') {
    bodyHtml = `
      <div class="card-grid">
        ${artists
          .map(
            (a) => `
          <div class="media-card artist-card" data-artist-id="${escapeHtml(a.id)}">
            <div class="media-card-art">
              <img src="${escapeHtml(coverUrlFor(a, true))}" alt="${escapeHtml(a.name)}" loading="lazy" />
              <button class="play-fab">${ICONS.play}</button>
            </div>
            <div class="media-card-title truncate">${escapeHtml(a.name)}</div>
            <div class="media-card-sub">Artist</div>
          </div>`
          )
          .join('')}
      </div>
    `;
  } else if (activeTab === 'albums') {
    bodyHtml = `
      <div class="card-grid">
        ${albums
          .map(
            (al) => `
          <div class="media-card" data-album-id="${escapeHtml(al.id)}">
            <div class="media-card-art">
              <img src="${escapeHtml(coverUrlFor(al, true))}" alt="${escapeHtml(al.title)}" loading="lazy" />
              <button class="play-fab">${ICONS.play}</button>
            </div>
            <div class="media-card-title truncate">${escapeHtml(al.title)}</div>
            <div class="media-card-sub">${escapeHtml(al.artist || 'Album')}</div>
          </div>`
          )
          .join('')}
      </div>
    `;
  }

  container.innerHTML = `${tabsHtml}${bodyHtml}`;
  bindTabs(container);

  const songsSlot = container.querySelector('#search-songs-slot');
  if (songsSlot) {
    renderTrackTable(songsSlot, activeTab === 'all' ? tracks.slice(0, 5) : tracks, {
      showHeader: activeTab === 'tracks',
      showAlbum: activeTab === 'tracks',
      compact: activeTab === 'all',
      sortable: activeTab === 'tracks',
    });
  }

  container.querySelector('#search-top-card')?.addEventListener('click', async (e) => {
    const top = res.topResult;
    if (!top) return;
    const clickedPlay = Boolean(e.target.closest('#top-result-play'));
    if (top.kind === 'track') {
      playTrack(top.item, tracks);
    } else if (top.kind === 'artist') {
      if (clickedPlay) {
        try {
          const art = await api.getArtist(top.item.id);
          if (art.topTracks?.length) playList(art.topTracks, 0);
          else navigate(`#/artist/${encodeURIComponent(top.item.id)}`);
        } catch {
          navigate(`#/artist/${encodeURIComponent(top.item.id)}`);
        }
        return;
      }
      navigate(`#/artist/${encodeURIComponent(top.item.id)}`);
    } else if (top.kind === 'album') {
      if (clickedPlay) {
        try {
          const alb = await api.getAlbum(top.item.id);
          if (alb.tracks?.length) playList(alb.tracks, 0);
          else navigate(`#/album/${encodeURIComponent(top.item.id)}`);
        } catch {
          navigate(`#/album/${encodeURIComponent(top.item.id)}`);
        }
        return;
      }
      navigate(`#/album/${encodeURIComponent(top.item.id)}`);
    }
  });

  container.querySelectorAll('[data-artist-id]').forEach((el) => {
    el.addEventListener('click', async (e) => {
      const id = el.dataset.artistId;
      if (e.target.closest('.play-fab')) {
        e.stopPropagation();
        try {
          const art = await api.getArtist(id);
          if (art.topTracks?.length) playList(art.topTracks, 0);
          else navigate(`#/artist/${encodeURIComponent(id)}`);
        } catch {
          navigate(`#/artist/${encodeURIComponent(id)}`);
        }
        return;
      }
      navigate(`#/artist/${encodeURIComponent(id)}`);
    });
  });

  container.querySelectorAll('[data-album-id]').forEach((el) => {
    el.addEventListener('click', async (e) => {
      const id = el.dataset.albumId;
      if (e.target.closest('.play-fab')) {
        e.stopPropagation();
        try {
          const alb = await api.getAlbum(id);
          if (alb.tracks?.length) playList(alb.tracks, 0);
          else navigate(`#/album/${encodeURIComponent(id)}`);
        } catch {
          navigate(`#/album/${encodeURIComponent(id)}`);
        }
        return;
      }
      navigate(`#/album/${encodeURIComponent(id)}`);
    });
  });
}

function bindTabs(container) {
  container.querySelectorAll('[data-stab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      activeTab = btn.dataset.stab;
      renderSearchView(container);
    });
  });
}
