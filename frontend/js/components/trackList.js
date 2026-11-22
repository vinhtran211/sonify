// Reusable Spotify-style Track Table and Mosaic Cover Builder

import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml, formatRelativeTime, formatTime } from '../lrc.js';
import { playTrack, togglePlay } from '../player.js';
import { navigate } from '../router.js';
import { getState, toggleLike } from '../store.js';
import { openTrackContextMenu } from './contextMenu.js';
import { showToast } from './modals.js';

export function renderMosaicHtml(covers = []) {
  const valid = (covers || []).filter(Boolean).slice(0, 4);
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

export function renderTrackTable(container, tracks, options = {}) {
  const {
    showAlbum = true,
    showHeader = true,
    compact = false,
    playlistId = null,
    playedAtList = null,
    onMoveTrack = null,
    onRemoveFromPlaylist = null,
  } = options;

  const st = getState();
  const currentId = st.currentTrack?.id || null;
  const isPlaying = st.isPlaying;
  const likedIds = st.likedIds;

  if (!tracks || tracks.length === 0) {
    container.innerHTML = '';
    return;
  }

  const hasPlActions = Boolean(playlistId && (onMoveTrack || onRemoveFromPlaylist));
  const rowLayoutClass = `${compact || !showAlbum ? 'compact' : ''} ${hasPlActions ? 'has-pl-actions' : ''}`.trim();

  const headHtml = showHeader
    ? `<div class="track-table-head ${rowLayoutClass}">
         <span style="text-align:center;">#</span>
         <span>Title</span>
         ${showAlbum && !compact ? `<span class="col-album">${playedAtList ? 'Played' : 'Album'}</span>` : ''}
         <span style="text-align:right; padding-right: 12px;">${ICONS.clock}</span>
       </div>`
    : '';

  const rowsHtml = tracks
    .map((track, idx) => {
      const active = track.id === currentId;
      const liked = likedIds.has(track.id);
      const art = coverUrlFor(track, false);
      const indexVisual = active
        ? `<div class="equalizer-bars ${isPlaying ? '' : 'paused'}" aria-label="Playing"><span></span><span></span><span></span></div>`
        : `<span class="track-index-num">${idx + 1}</span>`;

      const thirdCol = showAlbum && !compact
        ? `<div class="track-album truncate">
             ${
               playedAtList
                 ? escapeHtml(formatRelativeTime(playedAtList[idx]))
                 : track.albumId
                 ? `<a href="#/album/${encodeURIComponent(track.albumId)}" class="album-link" data-album-id="${escapeHtml(track.albumId)}">${escapeHtml(track.album || '')}</a>`
                 : escapeHtml(track.album || '')
             }
           </div>`
        : '';

      const reorderBtns = playlistId && onMoveTrack
        ? `
          <button class="icon-btn btn-move-up" data-idx="${idx}" title="Move up" ${idx === 0 ? 'disabled style="opacity:0.25;"' : ''}>${ICONS.arrowUp}</button>
          <button class="icon-btn btn-move-down" data-idx="${idx}" title="Move down" ${idx === tracks.length - 1 ? 'disabled style="opacity:0.25;"' : ''}>${ICONS.arrowDown}</button>
        `
        : '';

      const removeBtn = playlistId && onRemoveFromPlaylist
        ? `<button class="icon-btn btn-remove-pl" data-idx="${idx}" title="Remove from playlist">${ICONS.close}</button>`
        : '';

      return `
        <div class="track-row ${active ? 'active' : ''} ${rowLayoutClass}" data-idx="${idx}" data-track-id="${escapeHtml(track.id)}">
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
            <button class="icon-btn heart-btn ${liked ? 'liked' : ''}" data-idx="${idx}" title="${liked ? 'Remove from Liked Songs' : 'Save to Liked Songs'}" aria-label="Like track">
              ${liked ? ICONS.heartFilled : ICONS.heart}
            </button>
            <span class="track-duration">${formatTime(track.duration)}</span>
            <button class="icon-btn btn-track-more" data-idx="${idx}" title="More options" aria-label="More options">
              ${ICONS.more}
            </button>
          </div>
        </div>
      `;
    })
    .join('');

  container.innerHTML = `<div class="track-table">${headHtml}${rowsHtml}</div>`;

  // Event delegation
  container.onclick = async (e) => {
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
