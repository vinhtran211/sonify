// Left Sidebar (Primary Nav + Your Library with Liked Songs & Custom Playlists) + Mobile Nav

import { api } from '../api.js';
import { ICONS } from '../icons.js';
import { escapeHtml } from '../lrc.js';
import { playList } from '../player.js';
import { navigate } from '../router.js';
import { getState, setState } from '../store.js';
import { openCreatePlaylistModal, openDeletePlaylistModal, openEditPlaylistModal, showToast } from './modals.js';
import { renderMosaicHtml } from './trackList.js';

export function renderSidebar() {
  const el = document.getElementById('sidebar');
  if (!el) return;

  const st = getState();
  const routeName = st.route?.name || 'home';
  const activePlId = routeName === 'playlist' ? st.route.params?.id : null;
  const filter = st.libraryFilter || 'all';
  const likedCount = st.likedIds?.size || 0;
  const playlists = st.playlists || [];

  const showLiked = filter === 'all';

  const playlistsHtml = playlists
    .map(
      (pl) => `
      <div class="library-item ${activePlId === pl.id ? 'active' : ''}" data-pl-id="${escapeHtml(pl.id)}">
        <div class="library-thumb">${renderMosaicHtml(pl.covers)}</div>
        <div class="library-item-info">
          <div class="library-item-title truncate">${escapeHtml(pl.name)}</div>
          <div class="library-item-sub truncate">Playlist &bull; ${pl.trackCount} ${pl.trackCount === 1 ? 'song' : 'songs'}</div>
        </div>
        <div class="library-item-actions">
          <button class="icon-btn btn-pl-play" data-pl-id="${escapeHtml(pl.id)}" title="Play playlist">${ICONS.play}</button>
          <button class="icon-btn btn-pl-edit" data-pl-id="${escapeHtml(pl.id)}" title="Edit playlist">${ICONS.edit}</button>
          <button class="icon-btn btn-pl-del" data-pl-id="${escapeHtml(pl.id)}" title="Delete playlist">${ICONS.trash}</button>
        </div>
      </div>
    `
    )
    .join('');

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
        <a href="#/history" class="sidebar-nav-item ${routeName === 'history' ? 'active' : ''}">
          ${ICONS.history}<span>History</span>
        </a>
      </nav>
    </div>

    <div class="sidebar-card sidebar-library-card">
      <div class="library-header">
        <div class="library-title">${ICONS.library}<span>Your Library</span></div>
        <button id="btn-create-playlist" class="icon-btn" title="Create playlist" aria-label="Create playlist">
          ${ICONS.plus}
        </button>
      </div>

      <div class="library-filters">
        <button class="pill-btn ${filter === 'all' ? 'active' : ''}" data-lib-filter="all">All</button>
        <button class="pill-btn ${filter === 'playlists' ? 'active' : ''}" data-lib-filter="playlists">Playlists</button>
      </div>

      <div class="library-list">
        ${
          showLiked
            ? `
          <div class="library-item ${routeName === 'liked' ? 'active' : ''}" id="lib-liked-item">
            <div class="library-thumb liked-thumb">${ICONS.heartFilled}</div>
            <div class="library-item-info">
              <div class="library-item-title truncate">Liked Songs</div>
              <div class="library-item-sub truncate">Playlist &bull; ${likedCount} ${likedCount === 1 ? 'song' : 'songs'}</div>
            </div>
          </div>`
            : ''
        }
        ${playlistsHtml}
        ${
          playlists.length === 0
            ? `<div style="padding: 16px 10px; color: var(--text-secondary); font-size: 12.5px;">
                 Create your first custom playlist with the <strong>+</strong> button above.
               </div>`
            : ''
        }
      </div>
    </div>
  `;

  el.querySelector('#brand-home-link')?.addEventListener('click', () => navigate('#/'));
  el.querySelector('#btn-create-playlist')?.addEventListener('click', () => openCreatePlaylistModal());
  el.querySelector('#lib-liked-item')?.addEventListener('click', () => navigate('#/liked'));

  el.querySelectorAll('[data-lib-filter]').forEach((btn) => {
    btn.addEventListener('click', () => setState({ libraryFilter: btn.dataset.libFilter }));
  });

  el.querySelectorAll('.library-item[data-pl-id]').forEach((item) => {
    item.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      navigate(`#/playlist/${encodeURIComponent(item.dataset.plId)}`);
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
      const pl = playlists.find((p) => p.id === btn.dataset.plId);
      if (pl) openEditPlaylistModal(pl);
    });
  });

  el.querySelectorAll('.btn-pl-del').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const pl = playlists.find((p) => p.id === btn.dataset.plId);
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
    <button class="mobile-nav-btn ${routeName === 'search' ? 'active' : ''}" data-nav="#/search">
      ${ICONS.search}<span>Search</span>
    </button>
    <button class="mobile-nav-btn ${routeName === 'liked' || routeName === 'playlist' ? 'active' : ''}" data-nav="#/liked">
      ${ICONS.library}<span>Library</span>
    </button>
    <button class="mobile-nav-btn ${routeName === 'lyrics' ? 'active' : ''}" data-nav="#/lyrics">
      ${ICONS.lyrics}<span>Lyrics</span>
    </button>
  `;

  nav.querySelectorAll('[data-nav]').forEach((btn) => {
    btn.addEventListener('click', () => navigate(btn.dataset.nav));
  });
}
