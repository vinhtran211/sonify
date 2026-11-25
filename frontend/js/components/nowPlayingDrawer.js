// Spotify-style 3rd Pane "Now Playing View" Drawer
// Displays large artwork, Like/Download/Radio/Share actions, lazy "About the Artist" card,
// Track Credits + Ad-Free / Audio Quality badge, and clickable "Next in Queue" preview.

import { api } from '../api.js';
import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml } from '../lrc.js';
import { downloadTrack, isDownloaded, removeDownload } from '../offlineDownloads.js';
import { jumpToQueueIndex, playFromUserQueue, startSongRadio } from '../player.js';
import { navigate } from '../router.js';
import { getState, refreshDownloads, toggleFollowArtist, toggleLike } from '../store.js';
import { showToast } from './modals.js';

const artistBioCache = new Map();

function getNextPreview(st) {
  if (st.userQueue.length > 0) {
    return { track: st.userQueue[0], source: 'user', index: 0 };
  }
  if (st.queueIndex >= 0 && st.queueIndex + 1 < st.queue.length) {
    return { track: st.queue[st.queueIndex + 1], source: 'context', index: st.queueIndex + 1 };
  }
  return null;
}

function qualityLabel(st) {
  if (st.streamMode === 'offline-download') return 'Offline Download (Cached)';
  if (st.streamSource === 'monochrome' || st.audioQuality === 'lossless') return 'Lossless / Hi-Fi Stream';
  if (st.streamMode === 'preview') return '30s Preview';
  return 'Full-Length Ad-Free Stream';
}

export function renderNowPlayingDrawer(container) {
  if (!container) return;
  const st = getState();
  const track = st.currentTrack;

  if (!track) {
    container.innerHTML = `
      <div class="empty-state" style="padding:48px 16px;">
        <div class="empty-state-icon">${ICONS.nowPlaying}</div>
        <h3>Nothing Playing</h3>
        <p style="font-size:13px;">Play any track to inspect artwork, artist details, credits, and what's up next.</p>
      </div>
    `;
    return;
  }

  const liked = st.likedIds.has(track.id);
  const downloaded = isDownloaded(track.id);
  const nextInfo = getNextPreview(st);
  const cachedArtist = track.artistId ? artistBioCache.get(track.artistId) : null;
  const following = track.artistId && st.followedArtistIds.has(track.artistId);

  container.innerHTML = `
    <div class="np-drawer">
      <div class="np-cover-card">
        <img class="np-cover-img" src="${escapeHtml(coverUrlFor(track, true))}" alt="${escapeHtml(track.title)}" />
      </div>

      <div class="np-meta-row">
        <div class="np-meta-text">
          <a class="np-title" href="#/track/${encodeURIComponent(track.id)}">${escapeHtml(track.title)}</a>
          ${
            track.artistId
              ? `<a class="np-artist" href="#/artist/${encodeURIComponent(track.artistId)}">${escapeHtml(track.artist)}</a>`
              : `<div class="np-artist">${escapeHtml(track.artist)}</div>`
          }
        </div>
        <button id="np-like-btn" class="icon-btn ${liked ? 'liked' : ''}" title="${liked ? 'Remove from Liked Songs' : 'Save to Liked Songs'}">
          ${liked ? ICONS.heartFilled : ICONS.heart}
        </button>
      </div>

      <div class="np-quick-actions">
        <button id="np-radio-btn" class="pill-btn" title="Start Song Radio">
          ${ICONS.radio} <span>Radio</span>
        </button>
        <button id="np-dl-btn" class="pill-btn ${downloaded ? 'active' : ''}" title="${downloaded ? 'Remove Offline Download' : 'Download for Offline'}">
          ${downloaded ? ICONS.check : ICONS.download} <span>${downloaded ? 'Saved' : 'Offline'}</span>
        </button>
        <button id="np-share-btn" class="pill-btn" title="Copy track link">
          ${ICONS.share} <span>Share</span>
        </button>
      </div>

      ${
        track.artistId
          ? `
        <div class="np-card" id="np-artist-card">
          <div class="np-card-label">About the artist</div>
          <div class="np-artist-head">
            <img id="np-artist-img" class="np-artist-avatar" src="${escapeHtml(
              cachedArtist?.image || coverUrlFor(track, false)
            )}" alt="${escapeHtml(track.artist)}" />
            <div style="min-width:0; flex:1;">
              <a class="np-artist-name" href="#/artist/${encodeURIComponent(track.artistId)}">${escapeHtml(
                cachedArtist?.name || track.artist
              )}</a>
              <div class="np-artist-fans" id="np-artist-fans">
                ${
                  cachedArtist?.fans
                    ? `${Number(cachedArtist.fans).toLocaleString()} fans`
                    : 'Featured Artist'
                }
              </div>
            </div>
            <button id="np-follow-btn" class="btn-subtle btn-xs ${following ? 'active-follow' : ''}">
              ${following ? 'Following' : 'Follow'}
            </button>
          </div>
        </div>
      `
          : ''
      }

      <div class="np-card">
        <div class="np-card-label">Track Credits & Stream</div>
        <div class="np-credit-row">
          <span>Artist</span>
          <strong>${escapeHtml(track.artist || 'Unknown')}</strong>
        </div>
        ${
          track.album
            ? `
          <div class="np-credit-row">
            <span>Album</span>
            ${
              track.albumId
                ? `<a href="#/album/${encodeURIComponent(track.albumId)}" class="np-credit-link">${escapeHtml(track.album)}</a>`
                : `<strong>${escapeHtml(track.album)}</strong>`
            }
          </div>
        `
            : ''
        }
        <div class="np-credit-row">
          <span>Audio</span>
          <strong class="np-adfree-text">${escapeHtml(qualityLabel(st))}</strong>
        </div>
      </div>

      ${
        nextInfo
          ? `
        <div class="np-card np-next-card" id="np-next-card" role="button" tabindex="0">
          <div class="np-card-label">Next in queue</div>
          <div class="np-next-row">
            <img class="np-next-cover" src="${escapeHtml(coverUrlFor(nextInfo.track, false))}" alt="" />
            <div style="min-width:0; flex:1;">
              <div class="np-next-title">${escapeHtml(nextInfo.track.title)}</div>
              <div class="np-next-artist">${escapeHtml(nextInfo.track.artist)}</div>
            </div>
          </div>
        </div>
      `
          : ''
      }
    </div>
  `;

  container.querySelector('#np-like-btn')?.addEventListener('click', () => {
    toggleLike(track).catch(() => {});
  });

  container.querySelector('#np-radio-btn')?.addEventListener('click', () => {
    startSongRadio(track);
  });

  container.querySelector('#np-dl-btn')?.addEventListener('click', async () => {
    if (isDownloaded(track.id)) {
      await removeDownload(track.id);
      refreshDownloads();
      showToast(`Removed "${track.title}" from offline downloads`, 'info');
      renderNowPlayingDrawer(container);
    } else {
      showToast(`Downloading "${track.title}" for offline playback...`, 'info', 2200);
      try {
        await downloadTrack(track);
        refreshDownloads();
        showToast(`Saved "${track.title}" for offline playback`, 'success');
        renderNowPlayingDrawer(container);
      } catch (err) {
        showToast(err.message || 'Offline download failed', 'error');
      }
    }
  });

  container.querySelector('#np-share-btn')?.addEventListener('click', async () => {
    const shareUrl = `${window.location.origin}/#/track/${encodeURIComponent(track.id)}`;
    try {
      await navigator.clipboard.writeText(shareUrl);
      showToast('Track link copied to clipboard', 'success');
    } catch {
      showToast(shareUrl, 'info', 3500);
    }
  });

  container.querySelector('#np-follow-btn')?.addEventListener('click', async () => {
    const artistObj = cachedArtist || {
      id: track.artistId,
      name: track.artist,
      image: coverUrlFor(track, false),
      fans: 0,
    };
    const nowFollowing = await toggleFollowArtist(artistObj);
    showToast(nowFollowing ? `Following ${artistObj.name}` : `Unfollowed ${artistObj.name}`);
  });

  container.querySelector('#np-next-card')?.addEventListener('click', () => {
    if (!nextInfo) return;
    if (nextInfo.source === 'user') playFromUserQueue(nextInfo.index);
    else jumpToQueueIndex(nextInfo.index);
  });

  // Lazy load full artist bio/fans once per artistId
  if (track.artistId && !cachedArtist) {
    api
      .getArtist(track.artistId)
      .then((res) => {
        const a = res?.artist;
        if (!a) return;
        artistBioCache.set(track.artistId, a);
        const imgEl = container.querySelector('#np-artist-img');
        const fansEl = container.querySelector('#np-artist-fans');
        if (imgEl && a.image) imgEl.src = a.image;
        if (fansEl && a.fans) fansEl.textContent = `${Number(a.fans).toLocaleString()} fans`;
      })
      .catch(() => {});
  }
}
