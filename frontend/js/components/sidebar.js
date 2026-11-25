// Left Sidebar (Primary Nav + Enhanced Your Library with Filter, Sort, Search, Pin, Followed Artists, Saved Albums & Offline Downloads) + Mobile Nav

import { api } from '../api.js';
import { ICONS } from '../icons.js';
import { escapeHtml } from '../lrc.js';
import { playList } from '../player.js';
import { navigate } from '../router.js';
import {
  getState,
  isItemPinned,
  persistPrefs,
  setState,
  togglePinItem,
} from '../store.js';
import { openInstallAppModal } from './installModal.js';
import {
  openCreatePlaylistModal,
  openDeletePlaylistModal,
  openEditPlaylistModal,
  openImportPlaylistModal,
  showToast,
} from './modals.js';
import { renderPlaylistCoverHtml } from './trackList.js';

export function renderSidebar() {
  const el = document.getElementById('sidebar');
  if (!el) return;

  const st = getState();
  const routeName = st.route?.name || 'home';
  const activePlId = routeName === 'playlist' ? st.route.params?.id : null;
  const activeArtistId = routeName === 'artist' ? st.route.params?.id : null;
  const activeAlbumId = routeName === 'album' ? st.route.params?.id : null;
  const filter = st.libraryFilter || 'all';
  const sort = st.librarySort || 'recents';
  const q = (st.librarySearch || '').trim().toLowerCase();
  const likedCount = st.likedIds?.size || 0;
  const downloadCount = st.downloadedIds?.size || 0;

  let playlists = (st.playlists || []).slice();
  let artists = (st.followedArtists || []).slice();
  let albums = (st.savedAlbums || []).slice();

  if (q) {
    playlists = playlists.filter((p) => (p.name || '').toLowerCase().includes(q));
    artists = artists.filter((a) => (a.name || '').toLowerCase().includes(q));
    albums = albums.filter(
      (a) =>
        (a.title || '').toLowerCase().includes(q) || (a.artist || '').toLowerCase().includes(q)
    );
  }

  const sortWithPins = (list, type, labelFn) =>
    list.sort((a, b) => {
      const pa = isItemPinned(type, a.id) ? 1 : 0;
      const pb = isItemPinned(type, b.id) ? 1 : 0;
      if (pa !== pb) return pb - pa;
      if (sort === 'alpha') {
        return String(labelFn(a) || '').localeCompare(String(labelFn(b) || ''));
      }
      return 0;
    });

  sortWithPins(playlists, 'playlist', (p) => p.name);
  sortWithPins(artists, 'artist', (a) => a.name);
  sortWithPins(albums, 'album', (a) => a.title);

  const showLiked = (filter === 'all' || filter === 'playlists') && (!q || 'liked songs'.includes(q));
  const showDownloads = (filter === 'all' || filter === 'downloads') && (!q || 'downloads offline'.includes(q));
  const showPlaylists = filter === 'all' || filter === 'playlists';
  const showArtists = filter === 'all' || filter === 'artists';
  const showAlbums = filter === 'all' || filter === 'albums';

  const playlistsHtml = showPlaylists
    ? playlists
        .map((pl) => {
          const pinned = isItemPinned('playlist', pl.id);
          return `
            <div class="library-item ${activePlId === pl.id ? 'active' : ''}" data-pl-id="${escapeHtml(pl.id)}">
              <div class="library-thumb">${renderPlaylistCoverHtml(pl)}</div>
              <div class="library-item-info">
                <div class="library-item-title truncate">${escapeHtml(pl.name)}</div>
                <div class="library-item-sub truncate">
                  ${pinned ? '<span class="pin-dot" title="Pinned">📌</span> ' : ''}Playlist &bull; ${pl.trackCount} ${pl.trackCount === 1 ? 'song' : 'songs'}
                </div>
              </div>
              <div class="library-item-actions">
                <button class="icon-btn btn-pin-item ${pinned ? 'pinned' : ''}" data-pin-type="playlist" data-pin-id="${escapeHtml(pl.id)}" title="${pinned ? 'Unpin' : 'Pin to top'}">${ICONS.pin}</button>
                <button class="icon-btn btn-pl-play" data-pl-id="${escapeHtml(pl.id)}" title="Play playlist">${ICONS.play}</button>
                <button class="icon-btn btn-pl-edit" data-pl-id="${escapeHtml(pl.id)}" title="Edit playlist">${ICONS.edit}</button>
                <button class="icon-btn btn-pl-del" data-pl-id="${escapeHtml(pl.id)}" title="Delete playlist">${ICONS.trash}</button>
              </div>
            </div>
          `;
        })
        .join('')
    : '';

  const artistsHtml = showArtists
    ? artists
        .map((ar) => {
          const pinned = isItemPinned('artist', ar.id);
          return `
            <div class="library-item ${activeArtistId === ar.id ? 'active' : ''}" data-ar-id="${escapeHtml(ar.id)}">
              <div class="library-thumb artist-round-thumb">
                <img src="${escapeHtml(ar.image || '/api/artwork/' + encodeURIComponent(ar.name || 'artist'))}" alt="" loading="lazy" />
              </div>
              <div class="library-item-info">
                <div class="library-item-title truncate">${escapeHtml(ar.name)}</div>
                <div class="library-item-sub truncate">${pinned ? '<span class="pin-dot">📌</span> ' : ''}Artist</div>
              </div>
              <div class="library-item-actions">
                <button class="icon-btn btn-pin-item ${pinned ? 'pinned' : ''}" data-pin-type="artist" data-pin-id="${escapeHtml(ar.id)}" title="${pinned ? 'Unpin' : 'Pin'}">${ICONS.pin}</button>
              </div>
            </div>
          `;
        })
        .join('')
    : '';

  const albumsHtml = showAlbums
    ? albums
        .map((al) => {
          const pinned = isItemPinned('album', al.id);
          return `
            <div class="library-item ${activeAlbumId === al.id ? 'active' : ''}" data-al-id="${escapeHtml(al.id)}">
              <div class="library-thumb">
                <img src="${escapeHtml(al.cover || '/api/artwork/' + encodeURIComponent(al.title || 'album'))}" alt="" loading="lazy" />
              </div>
              <div class="library-item-info">
                <div class="library-item-title truncate">${escapeHtml(al.title)}</div>
                <div class="library-item-sub truncate">${pinned ? '<span class="pin-dot">📌</span> ' : ''}Album &bull; ${escapeHtml(al.artist || '')}</div>
              </div>
              <div class="library-item-actions">
                <button class="icon-btn btn-pin-item ${pinned ? 'pinned' : ''}" data-pin-type="album" data-pin-id="${escapeHtml(al.id)}" title="${pinned ? 'Unpin' : 'Pin'}">${ICONS.pin}</button>
              </div>
            </div>
          `;
        })
        .join('')
    : '';

  const wasSearchFocused = document.activeElement?.id === 'lib-search-input';
  const prevCaret = wasSearchFocused ? document.activeElement.selectionStart : null;

  el.innerHTML = `
    <div class="sidebar-card">
      <div class="sidebar-brand" id="brand-home-link">
        <span class="sidebar-brand-logo">${ICONS.brand}</span>
        <span class="sidebar-brand-name">Sonify</span>
      </div>
      <nav class="sidebar-nav" aria-label="Main views">
        <a href="#/" class="sidebar-nav-item ${routeName === 'home' ? 'active' : ''}">
          ${ICONS.home}<span>Home</span>
        </a>
        <a href="#/search" class="sidebar-nav-item ${routeName === 'search' ? 'active' : ''}">
          ${ICONS.search}<span>Search</span>
        </a>
        <a href="#/stats" class="sidebar-nav-item ${routeName === 'stats' ? 'active' : ''}">
          ${ICONS.stats}<span>Sound Capsule</span>
        </a>
        <a href="#/history" class="sidebar-nav-item ${routeName === 'history' ? 'active' : ''}">
          ${ICONS.history}<span>History</span>
        </a>
      </nav>
    </div>

    <div class="sidebar-card sidebar-library-card">
      <div class="library-header">
        <div class="library-title">${ICONS.library}<span>Your Library</span></div>
        <div style="display:flex; gap:2px; align-items:center;">
          <button id="btn-import-playlist" class="icon-btn" title="Import playlist (JSON / M3U)" aria-label="Import playlist">
            ${ICONS.download}
          </button>
          <button id="btn-create-playlist" class="icon-btn" title="Create playlist" aria-label="Create playlist">
            ${ICONS.plus}
          </button>
        </div>
      </div>

      <div class="library-filters">
        <button class="pill-btn ${filter === 'all' ? 'active' : ''}" data-lib-filter="all">All</button>
        <button class="pill-btn ${filter === 'playlists' ? 'active' : ''}" data-lib-filter="playlists">Playlists</button>
        <button class="pill-btn ${filter === 'artists' ? 'active' : ''}" data-lib-filter="artists">Artists</button>
        <button class="pill-btn ${filter === 'albums' ? 'active' : ''}" data-lib-filter="albums">Albums</button>
        <button class="pill-btn ${filter === 'downloads' ? 'active' : ''}" data-lib-filter="downloads">Offline</button>
      </div>

      <div class="library-tools-row">
        <input
          id="lib-search-input"
          class="lib-search-input"
          type="search"
          placeholder="Filter library..."
          value="${escapeHtml(st.librarySearch || '')}"
          aria-label="Filter library"
        />
        <button id="btn-lib-sort" class="lib-sort-btn" title="Toggle library sort">
          ${sort === 'alpha' ? 'A–Z' : 'Recents'}
        </button>
      </div>

      <div class="library-list">
        ${
          showLiked
            ? `
          <div class="library-item ${routeName === 'liked' ? 'active' : ''}" id="lib-liked-item">
            <div class="library-thumb liked-thumb">${ICONS.heartFilled}</div>
            <div class="library-item-info">
              <div class="library-item-title truncate">Liked Songs</div>
              <div class="library-item-sub truncate">📌 Playlist &bull; ${likedCount} ${likedCount === 1 ? 'song' : 'songs'}</div>
            </div>
          </div>`
            : ''
        }
        ${
          showDownloads
            ? `
          <div class="library-item ${routeName === 'downloads' ? 'active' : ''}" id="lib-downloads-item">
            <div class="library-thumb downloads-thumb">${ICONS.download}</div>
            <div class="library-item-info">
              <div class="library-item-title truncate">Offline Downloads</div>
              <div class="library-item-sub truncate">Local Cache &bull; ${downloadCount} ${downloadCount === 1 ? 'song' : 'songs'}</div>
            </div>
          </div>`
            : ''
        }
        ${playlistsHtml}
        ${artistsHtml}
        ${albumsHtml}
        ${
          !playlistsHtml && !artistsHtml && !albumsHtml && !showLiked && !showDownloads
            ? `<div style="padding: 16px 10px; color: var(--text-secondary); font-size: 12.5px;">
                 No matching items in Your Library.
               </div>`
            : ''
        }
      </div>

      <div class="sidebar-install-footer">
        <button id="btn-sidebar-install" class="sidebar-install-btn" type="button">
          ${ICONS.smartphone}<span>Install on Phone</span>
        </button>
      </div>
    </div>
  `;

  if (wasSearchFocused) {
    const inp = el.querySelector('#lib-search-input');
    if (inp) {
      inp.focus();
      if (typeof prevCaret === 'number') inp.setSelectionRange(prevCaret, prevCaret);
    }
  }

  el.querySelector('#brand-home-link')?.addEventListener('click', () => navigate('#/'));
  el.querySelector('#btn-create-playlist')?.addEventListener('click', () => openCreatePlaylistModal());
  el.querySelector('#btn-import-playlist')?.addEventListener('click', () => openImportPlaylistModal());
  el.querySelector('#btn-sidebar-install')?.addEventListener('click', () => openInstallAppModal());
  el.querySelector('#lib-liked-item')?.addEventListener('click', () => navigate('#/liked'));
  el.querySelector('#lib-downloads-item')?.addEventListener('click', () => navigate('#/downloads'));

  el.querySelector('#lib-search-input')?.addEventListener('input', (e) => {
    setState({ librarySearch: e.target.value });
  });

  el.querySelector('#btn-lib-sort')?.addEventListener('click', () => {
    const next = sort === 'alpha' ? 'recents' : 'alpha';
    setState({ librarySort: next });
    persistPrefs();
  });

  el.querySelectorAll('[data-lib-filter]').forEach((btn) => {
    btn.addEventListener('click', () => setState({ libraryFilter: btn.dataset.libFilter }));
  });

  el.querySelectorAll('.btn-pin-item').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const pinned = await togglePinItem(btn.dataset.pinType, btn.dataset.pinId);
      showToast(pinned ? 'Pinned to top of Your Library' : 'Unpinned from Your Library', 'info', 1800);
    });
  });

  el.querySelectorAll('.library-item[data-pl-id]').forEach((item) => {
    item.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      navigate(`#/playlist/${encodeURIComponent(item.dataset.plId)}`);
    });
  });

  el.querySelectorAll('.library-item[data-ar-id]').forEach((item) => {
    item.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      navigate(`#/artist/${encodeURIComponent(item.dataset.arId)}`);
    });
  });

  el.querySelectorAll('.library-item[data-al-id]').forEach((item) => {
    item.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      navigate(`#/album/${encodeURIComponent(item.dataset.alId)}`);
    });
  });

  el.querySelectorAll('.btn-pl-play').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        const res = await api.getPlaylist(btn.dataset.plId);
        if (res.tracks?.length) {
          playList(res.tracks, 0);
        } else {
          showToast('This playlist has no songs yet', 'info');
        }
      } catch (err) {
        showToast(err.message || 'Could not play playlist', 'error');
      }
    });
  });

  el.querySelectorAll('.btn-pl-edit').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const pl = (st.playlists || []).find((p) => p.id === btn.dataset.plId);
      if (pl) openEditPlaylistModal(pl);
    });
  });

  el.querySelectorAll('.btn-pl-del').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const pl = (st.playlists || []).find((p) => p.id === btn.dataset.plId);
      if (pl) {
        openDeletePlaylistModal(pl, () => {
          if (activePlId === pl.id) navigate('#/');
        });
      }
    });
  });

  renderMobileNav();
}

export function renderMobileNav() {
  const nav = document.getElementById('mobile-nav');
  if (!nav) return;
  const routeName = getState().route?.name || 'home';

  nav.innerHTML = `
    <button class="mobile-nav-btn ${routeName === 'home' ? 'active' : ''}" data-nav="#/">
      ${ICONS.home}<span>Home</span>
    </button>
    <button class="mobile-nav-btn ${routeName === 'search' || routeName === 'genre' ? 'active' : ''}" data-nav="#/search">
      ${ICONS.search}<span>Search</span>
    </button>
    <button class="mobile-nav-btn ${routeName === 'liked' || routeName === 'playlist' || routeName === 'downloads' ? 'active' : ''}" data-nav="#/liked">
      ${ICONS.library}<span>Library</span>
    </button>
    <button class="mobile-nav-btn ${routeName === 'stats' ? 'active' : ''}" data-nav="#/stats">
      ${ICONS.stats}<span>Stats</span>
    </button>
    <button class="mobile-nav-btn ${routeName === 'lyrics' ? 'active' : ''}" data-nav="#/lyrics">
      ${ICONS.lyrics}<span>Lyrics</span>
    </button>
  `;

  nav.querySelectorAll('[data-nav]').forEach((btn) => {
    btn.addEventListener('click', () => navigate(btn.dataset.nav));
  });
}
