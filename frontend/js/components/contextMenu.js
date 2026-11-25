// Floating Track Context Menu (Right-click or '...' button)

import { api } from '../api.js';
import { escapeHtml } from '../lrc.js';
import { downloadTrack, isDownloaded, removeDownload } from '../offlineDownloads.js';
import {
  enqueueLater,
  enqueueNext,
  playTrack,
  startArtistRadio,
  startSongRadio,
} from '../player.js';
import { navigate } from '../router.js';
import { getState, refreshDownloads, refreshPlaylists, setState, toggleLike } from '../store.js';
import { openCreatePlaylistModal, showToast } from './modals.js';

let menuOpen = false;

export function closeContextMenu() {
  const root = document.getElementById('context-menu-root');
  if (!root) return;
  root.classList.add('hidden');
  root.innerHTML = '';
  menuOpen = false;
}

export function isContextMenuOpen() {
  return menuOpen;
}

export function openTrackContextMenu(track, x, y, options = {}) {
  if (!track) return;
  const root = document.getElementById('context-menu-root');
  if (!root) return;

  const st = getState();
  const isLiked = st.likedIds.has(track.id);
  const downloaded = isDownloaded(track.id);
  const playlists = st.playlists || [];
  const flipSubmenu = x > window.innerWidth - 440;

  const playlistItemsHtml = playlists
    .map(
      (pl) =>
        `<button class="ctx-item" data-action="add-to-pl" data-pl-id="${escapeHtml(pl.id)}">
          <span class="truncate">${escapeHtml(pl.name)}</span>
        </button>`
    )
    .join('');

  const removeFromPlHtml = options.playlistId
    ? `<button class="ctx-item" data-action="remove-from-pl" style="color: var(--danger);">Remove from this playlist</button>`
    : '';

  root.innerHTML = `
    <button class="ctx-item" data-action="play-now">Play Now</button>
    <button class="ctx-item" data-action="play-next">Play Next</button>
    <button class="ctx-item" data-action="add-queue">Add to Queue</button>
    <div class="ctx-divider"></div>
    <button class="ctx-item" data-action="song-radio">Go to Song Radio</button>
    ${track.artistId ? '<button class="ctx-item" data-action="artist-radio">Go to Artist Radio</button>' : ''}
    <div class="ctx-divider"></div>
    <button class="ctx-item" data-action="toggle-like">${isLiked ? 'Remove from Liked Songs' : 'Save to Liked Songs'}</button>
    <button class="ctx-item" data-action="toggle-download">${downloaded ? 'Remove Offline Download' : 'Download for Offline'}</button>
    <div class="ctx-submenu-wrap">
      <button class="ctx-item" type="button">
        <span>Add to Playlist</span>
        <span>&#9656;</span>
      </button>
      <div class="ctx-submenu ${flipSubmenu ? 'flip-left' : ''}">
        <button class="ctx-item" data-action="new-pl-with-track" style="color: var(--accent-primary); font-weight: 700;">+ Create New Playlist</button>
        ${playlists.length ? '<div class="ctx-divider"></div>' : ''}
        ${playlistItemsHtml}
      </div>
    </div>
    ${removeFromPlHtml}
    <div class="ctx-divider"></div>
    <button class="ctx-item" data-action="view-lyrics">View Lyrics</button>
    <button class="ctx-item" data-action="copy-link">Copy Song Link</button>
    ${track.artistId ? '<button class="ctx-item" data-action="go-artist">Go to Artist</button>' : ''}
    ${track.albumId ? '<button class="ctx-item" data-action="go-album">Go to Album</button>' : ''}
  `;

  root.classList.remove('hidden');
  menuOpen = true;

  // Keep inside viewport bounds
  const rect = root.getBoundingClientRect();
  const left = Math.max(8, Math.min(x, window.innerWidth - rect.width - 10));
  const top = Math.max(8, Math.min(y, window.innerHeight - rect.height - 10));
  root.style.left = `${left}px`;
  root.style.top = `${top}px`;

  root.onclick = async (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    closeContextMenu();

    try {
      if (action === 'play-now') {
        playTrack(track, options.contextTracks || null);
      } else if (action === 'play-next') {
        enqueueNext(track);
        showToast(`Playing "${track.title}" next`, 'info');
      } else if (action === 'add-queue') {
        enqueueLater(track);
        showToast(`Added "${track.title}" to queue`, 'info');
      } else if (action === 'song-radio') {
        startSongRadio(track);
      } else if (action === 'artist-radio' && track.artistId) {
        startArtistRadio(track.artistId, track.artist);
      } else if (action === 'toggle-like') {
        const liked = await toggleLike(track);
        showToast(liked ? 'Saved to Liked Songs' : 'Removed from Liked Songs', liked ? 'success' : 'info');
      } else if (action === 'toggle-download') {
        if (isDownloaded(track.id)) {
          await removeDownload(track.id);
          refreshDownloads();
          showToast(`Removed "${track.title}" from offline downloads`, 'info');
        } else {
          showToast(`Downloading "${track.title}"...`, 'info', 2200);
          await downloadTrack(track);
          refreshDownloads();
          showToast(`Saved "${track.title}" for offline playback`, 'success');
        }
      } else if (action === 'new-pl-with-track') {
        openCreatePlaylistModal(track);
      } else if (action === 'add-to-pl') {
        const plId = btn.dataset.plId;
        const res = await api.addTrackToPlaylist(plId, track);
        await refreshPlaylists();
        showToast(
          res.alreadyInPlaylist
            ? `"${track.title}" is already in that playlist`
            : `Added "${track.title}" to playlist`
        );
      } else if (action === 'remove-from-pl' && options.onRemoveFromPlaylist) {
        options.onRemoveFromPlaylist(track);
      } else if (action === 'view-lyrics') {
        if (getState().currentTrack?.id !== track.id) {
          playTrack(track, options.contextTracks || null);
        }
        setState({ drawerMode: 'lyrics' });
      } else if (action === 'copy-link') {
        const shareUrl = `${window.location.origin}/#/track/${encodeURIComponent(track.id)}`;
        try {
          await navigator.clipboard.writeText(shareUrl);
          showToast('Song link copied to clipboard', 'success');
        } catch {
          showToast(shareUrl, 'info', 3500);
        }
      } else if (action === 'go-artist' && track.artistId) {
        navigate(`#/artist/${encodeURIComponent(track.artistId)}`);
      } else if (action === 'go-album' && track.albumId) {
        navigate(`#/album/${encodeURIComponent(track.albumId)}`);
      }
    } catch (err) {
      showToast(err.message || 'Action failed', 'error');
    }
  };
}

export function initContextMenuGlobalClose() {
  document.addEventListener('click', (e) => {
    if (!menuOpen) return;
    const root = document.getElementById('context-menu-root');
    if (root && !root.contains(e.target)) {
      closeContextMenu();
    }
  });
  window.addEventListener('scroll', closeContextMenu, true);
  window.addEventListener('resize', closeContextMenu);
}
