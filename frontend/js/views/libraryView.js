// Library Views: Liked Songs (#/liked) and Custom Playlist Detail (#/playlist/:id)

import { api } from '../api.js';
import { openDeletePlaylistModal, openEditPlaylistModal, showToast } from '../components/modals.js';
import { renderMosaicHtml, renderTrackTable } from '../components/trackList.js';
import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml, formatTotalDuration } from '../lrc.js';
import { playList } from '../player.js';
import { navigate } from '../router.js';
import { backupPlaylistDetail, getState, refreshPlaylists } from '../store.js';

let likedFilterText = '';
let currentPlaylistData = null;
let loadedPlaylistId = null;
let addSearchTimer = null;

export function renderLikedView(container) {
  container.classList.remove('no-padding');
  const st = getState();
  const allTracks = st.likedTracks || [];
  const totalDur = allTracks.reduce((acc, t) => acc + (Number(t.duration) || 0), 0);

  const q = likedFilterText.trim().toLowerCase();
  const filtered = q
    ? allTracks.filter(
        (t) =>
          (t.title || '').toLowerCase().includes(q) ||
          (t.artist || '').toLowerCase().includes(q) ||
          (t.album || '').toLowerCase().includes(q)
      )
    : allTracks;

  container.innerHTML = `
    <section class="view-hero liked-hero">
      <div class="hero-art liked-art">${ICONS.heartFilled}</div>
      <div class="hero-meta">
        <div class="hero-type">Playlist</div>
        <h1 class="hero-title">Liked Songs</h1>
        <div class="hero-stats">
          <strong>${allTracks.length} ${allTracks.length === 1 ? 'song' : 'songs'}</strong>
          ${totalDur ? `<span>&bull; ${formatTotalDuration(totalDur)}</span>` : ''}
        </div>
      </div>
    </section>

    <div class="action-bar">
      <button id="btn-liked-play" class="play-big-btn" title="Play Liked Songs" ${allTracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.play}</button>
      <button id="btn-liked-shuffle" class="btn-subtle" ${allTracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.shuffle} Shuffle</button>
      <input id="liked-filter-input" class="inline-filter-input" type="search" placeholder="Filter liked songs..." value="${escapeHtml(likedFilterText)}" />
    </div>

    <div id="liked-tracks-slot"></div>
  `;

  const slot = container.querySelector('#liked-tracks-slot');
  if (filtered.length > 0) {
    renderTrackTable(slot, filtered, { showAlbum: true, showHeader: true });
  } else {
    slot.innerHTML = `
      <div class="empty-state">
        <h3>${allTracks.length === 0 ? 'Songs you like will appear here' : 'No matching songs'}</h3>
        <p>${allTracks.length === 0 ? 'Click the heart icon next to any song to save it to your Liked Songs.' : 'Try another search filter.'}</p>
      </div>
    `;
  }

  container.querySelector('#btn-liked-play')?.addEventListener('click', () => {
    if (filtered.length) playList(filtered, 0, false);
  });
  container.querySelector('#btn-liked-shuffle')?.addEventListener('click', () => {
    if (filtered.length) playList(filtered, 0, true);
  });
  container.querySelector('#liked-filter-input')?.addEventListener('input', (e) => {
    likedFilterText = e.target.value;
    const pos = e.target.selectionStart;
    renderLikedView(container);
    const inp = container.querySelector('#liked-filter-input');
    if (inp) {
      inp.focus();
      inp.setSelectionRange(pos, pos);
    }
  });
}

export async function renderPlaylistView(container, playlistId, forceReload = false) {
  container.classList.remove('no-padding');

  const storePl = (getState().playlists || []).find((p) => p.id === playlistId);
  const storeOutOfSync =
    currentPlaylistData &&
    loadedPlaylistId === playlistId &&
    storePl &&
    (storePl.trackCount !== (currentPlaylistData.tracks || []).length ||
      storePl.name !== currentPlaylistData.playlist.name ||
      storePl.description !== currentPlaylistData.playlist.description ||
      storePl.updatedAt !== currentPlaylistData.playlist.updatedAt);

  if (!currentPlaylistData || loadedPlaylistId !== playlistId || forceReload || storeOutOfSync) {
    loadedPlaylistId = playlistId;
    container.innerHTML = `
      <div style="display:flex; gap:20px; align-items:flex-end; margin-bottom:24px;">
        <div class="skeleton" style="width:196px; height:196px;"></div>
        <div style="flex:1; display:flex; flex-direction:column; gap:12px;">
          <div class="skeleton" style="height:42px; width:280px;"></div>
          <div class="skeleton" style="height:20px; width:180px;"></div>
        </div>
      </div>
    `;
    try {
      const fetched = await api.getPlaylist(playlistId);
      const curRoute = getState().route;
      if (curRoute?.name !== 'playlist' || curRoute.params?.id !== playlistId) return;
      currentPlaylistData = fetched;
    } catch (err) {
      const curRoute = getState().route;
      if (curRoute?.name !== 'playlist' || curRoute.params?.id !== playlistId) return;
      container.innerHTML = `
        <div class="empty-state">
          <h3>Playlist not found</h3>
          <p>${escapeHtml(err.message || 'This playlist may have been deleted.')}</p>
          <button class="btn-primary" id="btn-pl-home" style="margin-top:12px;">Back to Home</button>
        </div>
      `;
      container.querySelector('#btn-pl-home')?.addEventListener('click', () => navigate('#/'));
      return;
    }
  }

  const playlist = currentPlaylistData.playlist;
  const tracks = currentPlaylistData.tracks || [];
  const totalDur = tracks.reduce((acc, t) => acc + (Number(t.duration) || 0), 0);

  container.innerHTML = `
    <section class="view-hero">
      <div class="hero-art">${renderMosaicHtml(playlist.covers)}</div>
      <div class="hero-meta">
        <div class="hero-type">Playlist</div>
        <h1 class="hero-title">${escapeHtml(playlist.name)}</h1>
        ${playlist.description ? `<p class="hero-desc">${escapeHtml(playlist.description)}</p>` : ''}
        <div class="hero-stats">
          <strong>${tracks.length} ${tracks.length === 1 ? 'song' : 'songs'}</strong>
          ${totalDur ? `<span>&bull; ${formatTotalDuration(totalDur)}</span>` : ''}
        </div>
      </div>
    </section>

    <div class="action-bar">
      <button id="btn-pl-play" class="play-big-btn" title="Play playlist" ${tracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.play}</button>
      <button id="btn-pl-shuffle" class="btn-subtle" ${tracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.shuffle} Shuffle</button>
      <button id="btn-pl-edit-meta" class="btn-subtle">${ICONS.edit} Edit Details</button>
      <button id="btn-pl-delete" class="btn-subtle" style="color: var(--danger);">${ICONS.trash} Delete</button>
    </div>

    <div id="pl-tracks-slot"></div>

    <section class="playlist-add-box">
      <h3>Let's find something for your playlist</h3>
      <input id="pl-add-search" class="inline-filter-input" type="search" placeholder="Search for songs to add..." style="max-width: 420px;" />
      <div id="pl-add-results" style="margin-top: 14px;"></div>
    </section>
  `;

  const slot = container.querySelector('#pl-tracks-slot');
  if (tracks.length > 0) {
    renderTrackTable(slot, tracks, {
      showAlbum: true,
      showHeader: true,
      playlistId: playlist.id,
      onMoveTrack: async (idx, delta) => {
        const target = idx + delta;
        if (target < 0 || target >= tracks.length) return;
        const reordered = tracks.slice();
        const [moved] = reordered.splice(idx, 1);
        reordered.splice(target, 0, moved);
        try {
          await api.reorderPlaylistTracks(playlist.id, reordered.map((t) => t.id));
          currentPlaylistData.tracks = reordered;
          backupPlaylistDetail(playlist, reordered);
          renderPlaylistView(container, playlist.id, false);
        } catch (err) {
          showToast(err.message || 'Could not reorder tracks', 'error');
        }
      },
      onRemoveFromPlaylist: async (track) => {
        try {
          await api.removeTrackFromPlaylist(playlist.id, track.id);
          currentPlaylistData = null;
          await refreshPlaylists();
          showToast(`Removed "${track.title}" from ${playlist.name}`, 'info');
        } catch (err) {
          showToast(err.message || 'Could not remove track', 'error');
        }
      },
    });
  } else {
    slot.innerHTML = `
      <div class="empty-state" style="padding: 28px 10px;">
        <h3>This playlist is empty</h3>
        <p>Use the search box below to add tracks immediately.</p>
      </div>
    `;
  }

  container.querySelector('#btn-pl-play')?.addEventListener('click', () => {
    if (tracks.length) playList(tracks, 0, false);
  });
  container.querySelector('#btn-pl-shuffle')?.addEventListener('click', () => {
    if (tracks.length) playList(tracks, 0, true);
  });
  container.querySelector('#btn-pl-edit-meta')?.addEventListener('click', () => {
    openEditPlaylistModal(playlist, () => renderPlaylistView(container, playlist.id, true));
  });
  container.querySelector('#btn-pl-delete')?.addEventListener('click', () => {
    openDeletePlaylistModal(playlist, () => navigate('#/'));
  });

  // Inline "Find songs to add" search
  const addInput = container.querySelector('#pl-add-search');
  const addResults = container.querySelector('#pl-add-results');
  addInput?.addEventListener('input', () => {
    clearTimeout(addSearchTimer);
    const q = addInput.value.trim();
    if (!q) {
      addResults.innerHTML = '';
      return;
    }
    addSearchTimer = setTimeout(async () => {
      addResults.innerHTML = '<p style="color:var(--text-secondary); font-size:13px;">Searching...</p>';
      try {
        const res = await api.search(q, 'tracks', 8);
        const found = res.tracks || [];
        if (!found.length) {
          addResults.innerHTML = '<p style="color:var(--text-secondary); font-size:13px;">No matching tracks found.</p>';
          return;
        }
        addResults.innerHTML = found
          .map(
            (t, i) => `
            <div class="queue-item" style="justify-content:space-between; padding: 8px 10px;">
              <div style="display:flex; align-items:center; gap:12px; min-width:0; flex:1;">
                <img src="${escapeHtml(coverUrlFor(t, false))}" alt="" loading="lazy" />
                <div class="queue-item-meta">
                  <div class="queue-item-title truncate">${escapeHtml(t.title)}</div>
                  <div class="queue-item-artist truncate">${escapeHtml(t.artist)}</div>
                </div>
              </div>
              <button class="btn-subtle btn-xs btn-inline-add" data-add-idx="${i}">Add</button>
            </div>
          `
          )
          .join('');

        addResults.querySelectorAll('.btn-inline-add').forEach((btn) => {
          btn.addEventListener('click', async () => {
            const tr = found[Number(btn.dataset.addIdx)];
            if (!tr) return;
            try {
              const r = await api.addTrackToPlaylist(playlist.id, tr);
              currentPlaylistData = null;
              await refreshPlaylists();
              showToast(r.alreadyInPlaylist ? `"${tr.title}" is already in this playlist` : `Added "${tr.title}"`);
            } catch (err) {
              showToast(err.message || 'Could not add track', 'error');
            }
          });
        });
      } catch {
        addResults.innerHTML = '<p style="color:var(--danger); font-size:13px;">Search unavailable.</p>';
      }
    }, 320);
  });
}
