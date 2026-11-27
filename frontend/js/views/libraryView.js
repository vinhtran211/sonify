// Library Views: Liked Songs (#/liked), Custom Playlist Detail (#/playlist/:id) & Offline Downloads (#/downloads)

import { api } from '../api.js';
import { applyDynamicHeroGradient } from '../colorExtractor.js';
import {
  openDeletePlaylistModal,
  openEditPlaylistModal,
  openImportPlaylistModal,
  showToast,
} from '../components/modals.js';
import { renderPlaylistCoverHtml, renderTrackTable } from '../components/trackList.js';
import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml, formatTotalDuration, sanitizeCssColor } from '../lrc.js';
import {
  downloadPlaylist,
  getAllDownloads,
  removeDownload,
} from '../offlineDownloads.js';
import { playList } from '../player.js';
import { navigate } from '../router.js';
import {
  backupPlaylistDetail,
  getState,
  refreshDownloads,
  refreshPlaylists,
} from '../store.js';

let likedFilterText = '';
let currentPlaylistData = null;
let loadedPlaylistId = null;
let addSearchTimer = null;

function triggerFileDownload(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

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
      <button id="btn-liked-offline" class="btn-subtle" ${allTracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.download} Download Offline</button>
      <input id="liked-filter-input" class="inline-filter-input" type="search" placeholder="Filter liked songs..." value="${escapeHtml(likedFilterText)}" />
    </div>

    <div id="liked-tracks-slot"></div>
  `;

  const slot = container.querySelector('#liked-tracks-slot');
  if (filtered.length > 0) {
    renderTrackTable(slot, filtered, { showAlbum: true, showHeader: true, sortable: true });
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
  container.querySelector('#btn-liked-offline')?.addEventListener('click', async () => {
    if (!filtered.length) return;
    showToast(`Downloading ${filtered.length} liked songs for offline playback...`, 'info', 2500);
    const res = await downloadPlaylist(filtered);
    refreshDownloads();
    showToast(`Saved ${res.completed}/${res.total} songs for offline listening`, 'success');
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

export function renderDownloadsView(container) {
  container.classList.remove('no-padding');
  const items = getAllDownloads();
  const totalDur = items.reduce((acc, t) => acc + (Number(t.duration) || 0), 0);

  container.innerHTML = `
    <section class="view-hero" style="background:linear-gradient(135deg,#006450,#181818);">
      <div class="hero-art downloads-thumb" style="display:flex; align-items:center; justify-content:center; font-size:48px;">
        ${ICONS.download}
      </div>
      <div class="hero-meta">
        <div class="hero-type">LOCAL CACHE</div>
        <h1 class="hero-title">Offline Downloads</h1>
        <div class="hero-stats">
          <strong>${items.length} ${items.length === 1 ? 'song' : 'songs'} cached on this device</strong>
          ${totalDur ? `<span>&bull; ${formatTotalDuration(totalDur)}</span>` : ''}
        </div>
      </div>
    </section>

    <div class="action-bar">
      <button id="btn-dl-play" class="play-big-btn" ${items.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.play}</button>
      <button id="btn-dl-shuffle" class="btn-subtle" ${items.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.shuffle} Shuffle</button>
      ${
        items.length > 0
          ? `<button id="btn-dl-clear" class="btn-subtle" style="color:var(--danger);">${ICONS.trash} Clear All Downloads</button>`
          : ''
      }
    </div>

    <div id="downloads-tracks-slot"></div>
  `;

  const slot = container.querySelector('#downloads-tracks-slot');
  if (items.length > 0) {
    renderTrackTable(slot, items, { showAlbum: true, showHeader: true, sortable: true });
  } else {
    slot.innerHTML = `
      <div class="empty-state">
        <h3>No offline downloads yet</h3>
        <p>Right-click any song or open a playlist and choose <strong>Download for Offline</strong> to cache full tracks for instant playback.</p>
      </div>
    `;
  }

  container.querySelector('#btn-dl-play')?.addEventListener('click', () => {
    if (items.length) playList(items, 0, false);
  });
  container.querySelector('#btn-dl-shuffle')?.addEventListener('click', () => {
    if (items.length) playList(items, 0, true);
  });
  container.querySelector('#btn-dl-clear')?.addEventListener('click', async () => {
    for (const tr of items) {
      await removeDownload(tr.id);
    }
    refreshDownloads();
    showToast('Cleared all offline downloads', 'info');
    renderDownloadsView(container);
  });
}

async function loadPlaylistRecommendations(container, playlist) {
  const slot = container.querySelector('#pl-recs-slot');
  if (!slot) return;
  slot.innerHTML = `<p style="color:var(--text-secondary); font-size:13px;">Loading personalized recommendations...</p>`;
  try {
    const res = await api.getPlaylistRecommendations(playlist.id, 10);
    const recs = res?.tracks || [];
    if (!recs.length) {
      slot.innerHTML = `<p style="color:var(--text-secondary); font-size:13px;">Add a couple songs first and we'll recommend tracks that fit this vibe.</p>`;
      return;
    }
    slot.innerHTML = recs
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
        <button class="btn-subtle btn-xs btn-rec-add" data-rec-idx="${i}">+ Add</button>
      </div>
    `
      )
      .join('');

    slot.querySelectorAll('.btn-rec-add').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const tr = recs[Number(btn.dataset.recIdx)];
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
    slot.innerHTML = `<p style="color:var(--text-secondary); font-size:13px;">Recommendations unavailable right now.</p>`;
  }
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
      storePl.coverColor !== currentPlaylistData.playlist.coverColor ||
      storePl.coverEmoji !== currentPlaylistData.playlist.coverEmoji ||
      storePl.coverUrl !== currentPlaylistData.playlist.coverUrl ||
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
    <section class="view-hero" id="pl-hero-el">
      <div class="hero-art">${renderPlaylistCoverHtml(playlist)}</div>
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
      <button id="btn-pl-edit-meta" class="btn-subtle">${ICONS.edit} Edit</button>
      <button id="btn-pl-dup" class="btn-subtle">${ICONS.copy} Duplicate</button>
      <button id="btn-pl-dl" class="btn-subtle" ${tracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.download} Offline</button>
      <button id="btn-pl-exp-json" class="btn-subtle" ${tracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>Export JSON</button>
      <button id="btn-pl-exp-m3u" class="btn-subtle" ${tracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>Export M3U</button>
      <button id="btn-pl-import" class="btn-subtle">Import</button>
      <button id="btn-pl-delete" class="btn-subtle" style="color: var(--danger);">${ICONS.trash} Delete</button>
    </div>

    <div id="pl-tracks-slot"></div>

    <section class="playlist-add-box">
      <h3>Let's find something for your playlist</h3>
      <input id="pl-add-search" class="inline-filter-input" type="search" placeholder="Search for songs to add..." style="max-width: 420px;" />
      <div id="pl-add-results" style="margin-top: 14px;"></div>
    </section>

    <section class="playlist-add-box" style="margin-top: 20px;">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:10px;">
        <div>
          <h3 style="margin:0;">Recommended</h3>
          <p style="margin:2px 0 0; font-size:12.5px; color:var(--text-secondary);">Based on what's in this playlist</p>
        </div>
        <button id="btn-pl-refresh-recs" class="btn-subtle btn-xs">Refresh</button>
      </div>
      <div id="pl-recs-slot"></div>
    </section>
  `;

  const heroEl = container.querySelector('#pl-hero-el');
  if (heroEl) {
    const safeColor = sanitizeCssColor(playlist.coverColor);
    if (safeColor) {
      heroEl.style.background = `linear-gradient(135deg, ${safeColor}, #181818)`;
    } else {
      applyDynamicHeroGradient(heroEl, playlist.name || playlist.id);
    }
  }

  const performReorder = async (fromIdx, toIdx) => {
    if (fromIdx === toIdx || fromIdx < 0 || toIdx < 0 || fromIdx >= tracks.length || toIdx >= tracks.length) return;
    const reordered = tracks.slice();
    const [moved] = reordered.splice(fromIdx, 1);
    reordered.splice(toIdx, 0, moved);
    try {
      await api.reorderPlaylistTracks(playlist.id, reordered.map((t) => t.id));
      currentPlaylistData.tracks = reordered;
      backupPlaylistDetail(playlist, reordered);
      renderPlaylistView(container, playlist.id, false);
    } catch (err) {
      showToast(err.message || 'Could not reorder tracks', 'error');
    }
  };

  const slot = container.querySelector('#pl-tracks-slot');
  if (tracks.length > 0) {
    renderTrackTable(slot, tracks, {
      showAlbum: true,
      showHeader: true,
      sortable: true,
      playlistId: playlist.id,
      onMoveTrack: (idx, delta) => performReorder(idx, idx + delta),
      onReorderIndices: (fromIdx, toIdx) => performReorder(fromIdx, toIdx),
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
        <p>Use the search box below or the recommendations to add tracks immediately.</p>
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
  container.querySelector('#btn-pl-dup')?.addEventListener('click', async () => {
    try {
      const res = await api.duplicatePlaylist(playlist.id);
      await refreshPlaylists();
      showToast(`Duplicated as "${res.playlist?.name || 'Copy'}"`, 'success');
      if (res.playlist?.id) navigate(`#/playlist/${encodeURIComponent(res.playlist.id)}`);
    } catch (err) {
      showToast(err.message || 'Could not duplicate playlist', 'error');
    }
  });
  container.querySelector('#btn-pl-dl')?.addEventListener('click', async () => {
    if (!tracks.length) return;
    showToast(`Downloading ${tracks.length} tracks for offline playback...`, 'info', 2500);
    const res = await downloadPlaylist(tracks);
    refreshDownloads();
    showToast(`Cached ${res.completed}/${res.total} songs for offline playback`, 'success');
  });
  container.querySelector('#btn-pl-exp-json')?.addEventListener('click', () => {
    const safeName = (playlist.name || 'playlist').replace(/[^a-z0-9-_]+/gi, '_');
    const payload = JSON.stringify(
      {
        name: playlist.name,
        description: playlist.description || '',
        exportedAt: new Date().toISOString(),
        tracks,
      },
      null,
      2
    );
    triggerFileDownload(`${safeName}.json`, payload, 'application/json');
    showToast('Exported playlist as JSON', 'success');
  });
  container.querySelector('#btn-pl-exp-m3u')?.addEventListener('click', () => {
    const safeName = (playlist.name || 'playlist').replace(/[^a-z0-9-_]+/gi, '_');
    const lines = ['#EXTM3U'];
    for (const t of tracks) {
      lines.push(`#EXTINF:${Math.round(Number(t.duration) || 0)},${t.artist || 'Unknown'} - ${t.title || 'Untitled'}`);
      lines.push(`${window.location.origin}/#/track/${encodeURIComponent(t.id)}`);
    }
    triggerFileDownload(`${safeName}.m3u`, lines.join('\n') + '\n', 'audio/x-mpegurl');
    showToast('Exported playlist as M3U', 'success');
  });
  container.querySelector('#btn-pl-import')?.addEventListener('click', () => openImportPlaylistModal());
  container.querySelector('#btn-pl-delete')?.addEventListener('click', () => {
    openDeletePlaylistModal(playlist, () => navigate('#/'));
  });
  container.querySelector('#btn-pl-refresh-recs')?.addEventListener('click', () => {
    loadPlaylistRecommendations(container, playlist);
  });

  loadPlaylistRecommendations(container, playlist);

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
