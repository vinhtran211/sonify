// Reusable Spotify-style Track Table, Sortable Columns, Drag-and-Drop Reorder, Media Cards & Playlist Cover Builder

import { ICONS } from '../icons.js';
import {
  coverUrlFor,
  escapeHtml,
  formatRelativeTime,
  formatTime,
  sanitizeCssColor,
  sanitizeSafeUrl,
} from '../lrc.js';
import { playTrack, togglePlay } from '../player.js';
import { navigate } from '../router.js';
import { getState, toggleLike } from '../store.js';
import { openTrackContextMenu } from './contextMenu.js';
import { showToast } from './modals.js';

export function renderMosaicHtml(covers = []) {
  const valid = (covers || []).map((u) => sanitizeSafeUrl(u)).filter(Boolean).slice(0, 4);
  if (valid.length === 0) {
    return `<div class="mosaic-placeholder">${ICONS.musicNote}</div>`;
  }
  if (valid.length < 4) {
    return `<div class="mosaic-grid single"><img src="${escapeHtml(valid[0])}" alt="" loading="lazy" /></div>`;
  }
  return `<div class="mosaic-grid">${valid
    .map((u) => `<img src="${escapeHtml(u)}" alt="" loading="lazy" />`)
    .join('')}</div>`;
}

export function renderPlaylistCoverHtml(playlist, covers = null) {
  const safeCoverUrl = sanitizeSafeUrl(playlist?.coverUrl);
  if (safeCoverUrl) {
    return `<div class="mosaic-grid single"><img src="${escapeHtml(safeCoverUrl)}" alt="${escapeHtml(playlist.name || '')}" loading="lazy" /></div>`;
  }
  const safeColor = sanitizeCssColor(playlist?.coverColor);
  if (safeColor || playlist?.coverEmoji) {
    const bg = safeColor || '#1e3264';
    const emoji = playlist.coverEmoji || '🎵';
    return `<div class="custom-cover-art" style="background:linear-gradient(135deg, ${escapeHtml(bg)}, #121212);">${escapeHtml(emoji)}</div>`;
  }
  return renderMosaicHtml(covers || playlist?.covers || []);
}

export function renderMediaCard(item, type = 'album') {
  const isArtist = type === 'artist';
  const title = item.title || item.name || 'Untitled';
  const sub = isArtist
    ? item.fans
      ? `${Number(item.fans).toLocaleString()} fans`
      : 'Artist'
    : item.artist || item.subtitle || 'Album';
  const img = sanitizeSafeUrl(item.image || item.cover) || coverUrlFor(item, false);
  return `
    <article class="media-card ${isArtist ? 'artist-card' : ''}" data-card-type="${escapeHtml(type)}" data-card-id="${escapeHtml(item.id || '')}">
      <div class="media-card-art ${isArtist ? 'round' : ''}">
        <img src="${escapeHtml(img)}" alt="${escapeHtml(title)}" loading="lazy" />
      </div>
      <div class="media-card-title truncate">${escapeHtml(title)}</div>
      <div class="media-card-sub truncate">${escapeHtml(sub)}</div>
    </article>
  `;
}

function sortTracksList(tracks, sortKey, sortDir) {
  if (!sortKey || sortKey === 'default') return tracks.map((t, i) => ({ track: t, origIdx: i }));
  const mult = sortDir === 'desc' ? -1 : 1;
  return tracks
    .map((t, i) => ({ track: t, origIdx: i }))
    .sort((a, b) => {
      if (sortKey === 'title') {
        return mult * String(a.track.title || '').localeCompare(String(b.track.title || ''));
      }
      if (sortKey === 'album') {
        return mult * String(a.track.album || '').localeCompare(String(b.track.album || ''));
      }
      if (sortKey === 'duration') {
        return mult * ((Number(a.track.duration) || 0) - (Number(b.track.duration) || 0));
      }
      return a.origIdx - b.origIdx;
    });
}

function buildTrackTableHtml(tracks, options = {}, sortState = { key: 'default', dir: 'asc' }) {
  const {
    showAlbum = true,
    showHeader = true,
    compact = false,
    playlistId = null,
    playedAtList = null,
    sortable = false,
    onMoveTrack = null,
    onReorderIndices = null,
    onRemoveFromPlaylist = null,
  } = options;

  const st = getState();
  const currentId = st.currentTrack?.id || null;
  const isPlaying = st.isPlaying;
  const likedIds = st.likedIds;
  const downloadedIds = st.downloadedIds || new Set();

  if (!tracks || tracks.length === 0) return '';

  const hasPlActions = Boolean(playlistId && (onMoveTrack || onRemoveFromPlaylist));
  const rowLayoutClass = `${compact || !showAlbum ? 'compact' : ''} ${hasPlActions ? 'has-pl-actions' : ''}`.trim();
  const canDrag = Boolean(playlistId && onReorderIndices && sortState.key === 'default');

  const sortMark = (k) =>
    sortState.key === k ? (sortState.dir === 'asc' ? ' ▲' : ' ▼') : '';

  const headHtml = showHeader
    ? `<div class="track-table-head ${rowLayoutClass}">
         <span class="${sortable ? 'sortable-col' : ''}" data-sort-col="default" style="text-align:center;">#</span>
         <span class="${sortable ? 'sortable-col' : ''}" data-sort-col="title">Title${sortMark('title')}</span>
         ${
           showAlbum && !compact
             ? `<span class="col-album ${sortable && !playedAtList ? 'sortable-col' : ''}" data-sort-col="${playedAtList ? '' : 'album'}">${playedAtList ? 'Played' : 'Album'}${playedAtList ? '' : sortMark('album')}</span>`
             : ''
         }
         <span class="${sortable ? 'sortable-col' : ''}" data-sort-col="duration" style="text-align:right; padding-right: 12px;">${ICONS.clock}${sortMark('duration')}</span>
       </div>`
    : '';

  const items = sortTracksList(tracks, sortState.key, sortState.dir);
  const rowsHtml = items
    .map(({ track, origIdx }, displayIdx) => {
      const active = track.id === currentId;
      const liked = likedIds.has(track.id);
      const isOffline = downloadedIds.has(track.id);
      const art = coverUrlFor(track, false);
      const indexVisual = active
        ? `<div class="equalizer-bars ${isPlaying ? '' : 'paused'}" aria-label="Playing"><span></span><span></span><span></span></div>`
        : `<span class="track-index-num">${displayIdx + 1}</span>`;

      const thirdCol = showAlbum && !compact
        ? `<div class="track-album truncate">
             ${
               playedAtList
                 ? escapeHtml(formatRelativeTime(playedAtList[origIdx]))
                 : track.albumId
                 ? `<a href="#/album/${encodeURIComponent(track.albumId)}" class="album-link" data-album-id="${escapeHtml(track.albumId)}">${escapeHtml(track.album || '')}</a>`
                 : escapeHtml(track.album || '')
             }
           </div>`
        : '';

      const reorderBtns = playlistId && onMoveTrack
        ? `
          <button class="icon-btn btn-move-up" data-idx="${origIdx}" title="Move up" ${origIdx === 0 ? 'disabled style="opacity:0.25;"' : ''}>${ICONS.arrowUp}</button>
          <button class="icon-btn btn-move-down" data-idx="${origIdx}" title="Move down" ${origIdx === tracks.length - 1 ? 'disabled style="opacity:0.25;"' : ''}>${ICONS.arrowDown}</button>
        `
        : '';

      const removeBtn = playlistId && onRemoveFromPlaylist
        ? `<button class="icon-btn btn-remove-pl" data-idx="${origIdx}" title="Remove from playlist">${ICONS.close}</button>`
        : '';

      return `
        <div class="track-row ${active ? 'active' : ''} ${rowLayoutClass} ${canDrag ? 'draggable-track-row' : ''}" ${canDrag ? 'draggable="true"' : ''} data-idx="${origIdx}" data-track-id="${escapeHtml(track.id)}">
          <div class="track-col-index">
            ${indexVisual}
            <span class="track-index-play">${active && isPlaying ? ICONS.pause : ICONS.play}</span>
          </div>
          <div class="track-col-main">
            <img class="track-thumb" src="${escapeHtml(art)}" alt="" loading="lazy" />
            <div class="track-meta">
              <div class="track-title">
                <span class="truncate">${escapeHtml(track.title)}</span>
                ${track.explicit ? '<span class="explicit-badge" title="Explicit">E</span>' : ''}
                ${isOffline ? '<span class="offline-dl-dot" title="Downloaded for offline playback">↓</span>' : ''}
              </div>
              <div class="track-artist truncate">
                ${
                  track.artistId
                    ? `<a href="#/artist/${encodeURIComponent(track.artistId)}" class="artist-link" data-artist-id="${escapeHtml(track.artistId)}">${escapeHtml(track.artist)}</a>`
                    : escapeHtml(track.artist)
                }
              </div>
            </div>
          </div>
          ${thirdCol}
          <div class="track-col-actions">
            ${reorderBtns}
            ${removeBtn}
            <button class="icon-btn heart-btn ${liked ? 'liked' : ''}" data-idx="${origIdx}" title="${liked ? 'Remove from Liked Songs' : 'Save to Liked Songs'}" aria-label="Like track">
              ${liked ? ICONS.heartFilled : ICONS.heart}
            </button>
            <span class="track-duration">${formatTime(track.duration)}</span>
            <button class="icon-btn btn-track-more" data-idx="${origIdx}" title="More options" aria-label="More options">
              ${ICONS.more}
            </button>
          </div>
        </div>
      `;
    })
    .join('');

  return `<div class="track-table">${headHtml}${rowsHtml}</div>`;
}

export function renderTrackList(tracks, options = {}) {
  return buildTrackTableHtml(tracks, options, { key: 'default', dir: 'asc' });
}

export function wireTrackListEvents(container, tracks, options = {}) {
  if (!container || !Array.isArray(tracks)) return;
  const {
    playlistId = null,
    sortable = false,
    onMoveTrack = null,
    onReorderIndices = null,
    onRemoveFromPlaylist = null,
  } = options;

  let sortState = { key: 'default', dir: 'asc' };

  const bindDragHandlers = () => {
    if (!playlistId || !onReorderIndices) return;
    let dragFrom = -1;
    container.querySelectorAll('.draggable-track-row').forEach((row) => {
      row.addEventListener('dragstart', (e) => {
        dragFrom = Number(row.dataset.idx);
        row.classList.add('dragging');
        if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
      });
      row.addEventListener('dragover', (e) => {
        e.preventDefault();
        row.classList.add('drag-over');
      });
      row.addEventListener('dragleave', () => {
        row.classList.remove('drag-over');
      });
      row.addEventListener('drop', (e) => {
        e.preventDefault();
        row.classList.remove('drag-over');
        const toIdx = Number(row.dataset.idx);
        if (dragFrom >= 0 && toIdx >= 0 && dragFrom !== toIdx) {
          onReorderIndices(dragFrom, toIdx);
        }
        dragFrom = -1;
      });
      row.addEventListener('dragend', () => {
        row.classList.remove('dragging');
        dragFrom = -1;
      });
    });
  };

  bindDragHandlers();

  container.onclick = async (e) => {
    const sortCol = e.target.closest('.sortable-col');
    if (sortCol && sortable) {
      const key = sortCol.dataset.sortCol;
      if (!key) return;
      if (sortState.key === key) {
        sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
      } else {
        sortState = { key, dir: 'asc' };
      }
      container.innerHTML = buildTrackTableHtml(tracks, options, sortState);
      bindDragHandlers();
      return;
    }

    const artistLink = e.target.closest('.artist-link');
    if (artistLink) {
      e.preventDefault();
      e.stopPropagation();
      navigate(`#/artist/${encodeURIComponent(artistLink.dataset.artistId)}`);
      return;
    }

    const albumLink = e.target.closest('.album-link');
    if (albumLink) {
      e.preventDefault();
      e.stopPropagation();
      navigate(`#/album/${encodeURIComponent(albumLink.dataset.albumId)}`);
      return;
    }

    const heartBtn = e.target.closest('.heart-btn');
    if (heartBtn) {
      e.stopPropagation();
      const idx = Number(heartBtn.dataset.idx);
      const track = tracks[idx];
      if (!track) return;
      try {
        const liked = await toggleLike(track);
        showToast(liked ? 'Saved to Liked Songs' : 'Removed from Liked Songs', liked ? 'success' : 'info');
      } catch (err) {
        showToast(err.message || 'Could not update like', 'error');
      }
      return;
    }

    const moreBtn = e.target.closest('.btn-track-more');
    if (moreBtn) {
      e.stopPropagation();
      const idx = Number(moreBtn.dataset.idx);
      const rect = moreBtn.getBoundingClientRect();
      openTrackContextMenu(tracks[idx], rect.left - 180, rect.bottom + 4, {
        contextTracks: tracks,
        playlistId,
        onRemoveFromPlaylist,
      });
      return;
    }

    const moveUpBtn = e.target.closest('.btn-move-up');
    if (moveUpBtn && onMoveTrack) {
      e.stopPropagation();
      onMoveTrack(Number(moveUpBtn.dataset.idx), -1);
      return;
    }

    const moveDownBtn = e.target.closest('.btn-move-down');
    if (moveDownBtn && onMoveTrack) {
      e.stopPropagation();
      onMoveTrack(Number(moveDownBtn.dataset.idx), 1);
      return;
    }

    const removeBtn = e.target.closest('.btn-remove-pl');
    if (removeBtn && onRemoveFromPlaylist) {
      e.stopPropagation();
      onRemoveFromPlaylist(tracks[Number(removeBtn.dataset.idx)]);
      return;
    }

    const row = e.target.closest('.track-row');
    if (row) {
      const idx = Number(row.dataset.idx);
      const track = tracks[idx];
      if (!track) return;
      if (getState().currentTrack?.id === track.id) {
        togglePlay();
      } else {
        playTrack(track, tracks, idx);
      }
    }
  };

  container.oncontextmenu = (e) => {
    const row = e.target.closest('.track-row');
    if (!row) return;
    e.preventDefault();
    const idx = Number(row.dataset.idx);
    openTrackContextMenu(tracks[idx], e.clientX, e.clientY, {
      contextTracks: tracks,
      playlistId,
      onRemoveFromPlaylist,
    });
  };
}

export function renderTrackTable(container, tracks, options = {}) {
  if (!container) return;
  if (!tracks || tracks.length === 0) {
    container.innerHTML = '';
    return;
  }
  container.innerHTML = buildTrackTableHtml(tracks, options, { key: 'default', dir: 'asc' });
  wireTrackListEvents(container, tracks, options);
}
