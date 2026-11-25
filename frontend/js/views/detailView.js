// Detail Views: Artist (#/artist/:id), Album (#/album/:id), Track Deep-Link (#/track/:id) & Made For You Mix (#/mix/:id)

import { api } from '../api.js';
import { applyDynamicHeroGradient } from '../colorExtractor.js';
import { showToast } from '../components/modals.js';
import { renderTrackTable } from '../components/trackList.js';
import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml, formatTime, formatTotalDuration } from '../lrc.js';
import { downloadTrack, isDownloaded, removeDownload } from '../offlineDownloads.js';
import { playList, playTrack, startArtistRadio, startSongRadio } from '../player.js';
import { navigate } from '../router.js';
import {
  getState,
  refreshDownloads,
  refreshPlaylists,
  setState,
  toggleFollowArtist,
  toggleLike,
  toggleSaveAlbum,
} from '../store.js';

let cachedArtist = { id: null, data: null };
let cachedAlbum = { id: null, data: null };
let cachedTrack = { id: null, data: null };

async function copyShareUrl(hashPath, label) {
  const url = `${window.location.origin}/${hashPath}`;
  try {
    await navigator.clipboard.writeText(url);
    showToast(`${label} link copied to clipboard`, 'success');
  } catch {
    showToast(url, 'info', 3400);
  }
}

export async function renderArtistView(container, artistId) {
  container.classList.remove('no-padding');

  if (cachedArtist.id !== artistId || !cachedArtist.data) {
    container.innerHTML = `
      <div style="display:flex; gap:20px; align-items:flex-end; margin-bottom:24px;">
        <div class="skeleton" style="width:196px; height:196px; border-radius:50%;"></div>
        <div style="flex:1; display:flex; flex-direction:column; gap:12px;">
          <div class="skeleton" style="height:44px; width:260px;"></div>
          <div class="skeleton" style="height:20px; width:160px;"></div>
        </div>
      </div>
    `;
    try {
      const data = await api.getArtist(artistId);
      const curRoute = getState().route;
      if (curRoute?.name !== 'artist' || curRoute.params?.id !== artistId) return;
      cachedArtist = { id: artistId, data };
    } catch (err) {
      const curRoute = getState().route;
      if (curRoute?.name !== 'artist' || curRoute.params?.id !== artistId) return;
      container.innerHTML = `
        <div class="empty-state">
          <h3>Artist unavailable</h3>
          <p>${escapeHtml(err.message || 'Could not load artist details.')}</p>
        </div>
      `;
      return;
    }
  }

  const st = getState();
  const { artist, topTracks = [], albums = [] } = cachedArtist.data;
  const following = st.followedArtistIds.has(artist.id);

  container.innerHTML = `
    <section class="view-hero artist-hero" id="artist-hero-el">
      <div class="hero-art round">
        <img src="${escapeHtml(coverUrlFor(artist, true))}" alt="${escapeHtml(artist.name)}" />
      </div>
      <div class="hero-meta">
        <div class="hero-type">Artist</div>
        <h1 class="hero-title">${escapeHtml(artist.name)}</h1>
        <div class="hero-stats">
          ${artist.fans ? `<strong>${Number(artist.fans).toLocaleString()} fans</strong>` : ''}
          <span>${topTracks.length} popular tracks</span>
        </div>
      </div>
    </section>

    <div class="action-bar">
      <button id="btn-artist-play" class="play-big-btn" title="Play top tracks" ${topTracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.play}</button>
      <button id="btn-artist-shuffle" class="btn-subtle" ${topTracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.shuffle} Shuffle</button>
      <button id="btn-artist-follow" class="btn-subtle ${following ? 'active-follow' : ''}">${following ? 'Following' : 'Follow'}</button>
      <button id="btn-artist-radio" class="btn-subtle">${ICONS.radio} Artist Radio</button>
      <button id="btn-artist-share" class="btn-subtle">${ICONS.share} Share</button>
    </div>

    <div class="shelf-section">
      <h2 class="shelf-title" style="margin-bottom: 12px;">Popular Tracks</h2>
      <div id="artist-tracks-slot"></div>
    </div>

    ${
      albums.length > 0
        ? `<div class="shelf-section">
             <h2 class="shelf-title" style="margin-bottom: 14px;">Discography</h2>
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
                   <div class="media-card-sub">${escapeHtml(al.releaseDate ? al.releaseDate.slice(0, 4) : 'Album')}</div>
                 </div>`
                 )
                 .join('')}
             </div>
           </div>`
        : ''
    }
  `;

  applyDynamicHeroGradient(container.querySelector('#artist-hero-el'), artist.name || artist.id);

  renderTrackTable(container.querySelector('#artist-tracks-slot'), topTracks, {
    showAlbum: true,
    showHeader: true,
    sortable: true,
  });

  container.querySelector('#btn-artist-play')?.addEventListener('click', () => {
    if (topTracks.length) playList(topTracks, 0, false);
  });
  container.querySelector('#btn-artist-shuffle')?.addEventListener('click', () => {
    if (topTracks.length) playList(topTracks, 0, true);
  });
  container.querySelector('#btn-artist-follow')?.addEventListener('click', async () => {
    const nowFollowing = await toggleFollowArtist({
      id: artist.id,
      name: artist.name,
      image: coverUrlFor(artist, false),
      fans: artist.fans || 0,
    });
    showToast(nowFollowing ? `Following ${artist.name}` : `Unfollowed ${artist.name}`, 'info');
  });
  container.querySelector('#btn-artist-radio')?.addEventListener('click', () => {
    startArtistRadio(artist.id, artist.name);
  });
  container.querySelector('#btn-artist-share')?.addEventListener('click', () => {
    copyShareUrl(`#/artist/${encodeURIComponent(artist.id)}`, 'Artist');
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

export async function renderAlbumView(container, albumId) {
  container.classList.remove('no-padding');

  if (cachedAlbum.id !== albumId || !cachedAlbum.data) {
    container.innerHTML = `
      <div style="display:flex; gap:20px; align-items:flex-end; margin-bottom:24px;">
        <div class="skeleton" style="width:196px; height:196px;"></div>
        <div style="flex:1; display:flex; flex-direction:column; gap:12px;">
          <div class="skeleton" style="height:44px; width:280px;"></div>
          <div class="skeleton" style="height:20px; width:180px;"></div>
        </div>
      </div>
    `;
    try {
      const data = await api.getAlbum(albumId);
      const curRoute = getState().route;
      if (curRoute?.name !== 'album' || curRoute.params?.id !== albumId) return;
      cachedAlbum = { id: albumId, data };
    } catch (err) {
      const curRoute = getState().route;
      if (curRoute?.name !== 'album' || curRoute.params?.id !== albumId) return;
      container.innerHTML = `
        <div class="empty-state">
          <h3>Album unavailable</h3>
          <p>${escapeHtml(err.message || 'Could not load album details.')}</p>
        </div>
      `;
      return;
    }
  }

  const st = getState();
  const { album, tracks = [] } = cachedAlbum.data;
  const totalDur = album.totalDuration || tracks.reduce((acc, t) => acc + (Number(t.duration) || 0), 0);
  const saved = st.savedAlbumIds.has(album.id);

  container.innerHTML = `
    <section class="view-hero" id="album-hero-el">
      <div class="hero-art">
        <img src="${escapeHtml(coverUrlFor(album, true))}" alt="${escapeHtml(album.title)}" />
      </div>
      <div class="hero-meta">
        <div class="hero-type">Album</div>
        <h1 class="hero-title">${escapeHtml(album.title)}</h1>
        <div class="hero-stats">
          ${
            album.artistId
              ? `<a href="#/artist/${encodeURIComponent(album.artistId)}" class="link-hover" style="font-weight:700;">${escapeHtml(album.artist)}</a>`
              : `<strong>${escapeHtml(album.artist || 'Various Artists')}</strong>`
          }
          ${album.releaseDate ? `<span>&bull; ${escapeHtml(album.releaseDate.slice(0, 4))}</span>` : ''}
          <span>&bull; ${tracks.length} ${tracks.length === 1 ? 'song' : 'songs'}</span>
          ${totalDur ? `<span>&bull; ${formatTotalDuration(totalDur)}</span>` : ''}
        </div>
      </div>
    </section>

    <div class="action-bar">
      <button id="btn-album-play" class="play-big-btn" title="Play album" ${tracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.play}</button>
      <button id="btn-album-shuffle" class="btn-subtle" ${tracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.shuffle} Shuffle</button>
      <button id="btn-album-save" class="btn-subtle ${saved ? 'active-follow' : ''}">${saved ? ICONS.heartFilled : ICONS.heart} ${saved ? 'Saved in Library' : 'Save Album'}</button>
      <button id="btn-album-share" class="btn-subtle">${ICONS.share} Share</button>
    </div>

    <div id="album-tracks-slot"></div>
  `;

  applyDynamicHeroGradient(container.querySelector('#album-hero-el'), album.title || album.id);

  renderTrackTable(container.querySelector('#album-tracks-slot'), tracks, {
    showAlbum: false,
    showHeader: true,
    sortable: true,
  });

  container.querySelector('#btn-album-play')?.addEventListener('click', () => {
    if (tracks.length) playList(tracks, 0, false);
  });
  container.querySelector('#btn-album-shuffle')?.addEventListener('click', () => {
    if (tracks.length) playList(tracks, 0, true);
  });
  container.querySelector('#btn-album-save')?.addEventListener('click', async () => {
    const nowSaved = await toggleSaveAlbum({
      id: album.id,
      title: album.title,
      artist: album.artist || '',
      artistId: album.artistId || '',
      cover: coverUrlFor(album, false),
      releaseDate: album.releaseDate || '',
    });
    showToast(nowSaved ? `Saved "${album.title}" to Your Library` : `Removed "${album.title}" from Library`, 'info');
  });
  container.querySelector('#btn-album-share')?.addEventListener('click', () => {
    copyShareUrl(`#/album/${encodeURIComponent(album.id)}`, 'Album');
  });
}

export async function renderTrackView(container, trackId) {
  container.classList.remove('no-padding');

  if (cachedTrack.id !== trackId || !cachedTrack.data) {
    container.innerHTML = `
      <div style="display:flex; gap:20px; align-items:flex-end; margin-bottom:24px;">
        <div class="skeleton" style="width:196px; height:196px;"></div>
        <div style="flex:1; display:flex; flex-direction:column; gap:12px;">
          <div class="skeleton" style="height:44px; width:280px;"></div>
          <div class="skeleton" style="height:20px; width:180px;"></div>
        </div>
      </div>
    `;
    try {
      const data = await api.getTrackDetail(trackId);
      const curRoute = getState().route;
      if (curRoute?.name !== 'track' || curRoute.params?.id !== trackId) return;
      cachedTrack = { id: trackId, data };
    } catch (err) {
      const curRoute = getState().route;
      if (curRoute?.name !== 'track' || curRoute.params?.id !== trackId) return;
      container.innerHTML = `
        <div class="empty-state">
          <h3>Track unavailable</h3>
          <p>${escapeHtml(err.message || 'Could not load track details.')}</p>
        </div>
      `;
      return;
    }
  }

  const st = getState();
  const { track, related = [] } = cachedTrack.data;
  const liked = st.likedIds.has(track.id);
  const downloaded = isDownloaded(track.id);

  container.innerHTML = `
    <section class="view-hero" id="track-hero-el">
      <div class="hero-art">
        <img src="${escapeHtml(coverUrlFor(track, true))}" alt="${escapeHtml(track.title)}" />
      </div>
      <div class="hero-meta">
        <div class="hero-type">Song</div>
        <h1 class="hero-title">${escapeHtml(track.title)}</h1>
        <div class="hero-stats">
          ${
            track.artistId
              ? `<a href="#/artist/${encodeURIComponent(track.artistId)}" class="link-hover" style="font-weight:700;">${escapeHtml(track.artist)}</a>`
              : `<strong>${escapeHtml(track.artist)}</strong>`
          }
          ${
            track.album
              ? track.albumId
                ? `<span>&bull; <a href="#/album/${encodeURIComponent(track.albumId)}" class="link-hover">${escapeHtml(track.album)}</a></span>`
                : `<span>&bull; ${escapeHtml(track.album)}</span>`
              : ''
          }
          ${track.duration ? `<span>&bull; ${formatTime(track.duration)}</span>` : ''}
        </div>
      </div>
    </section>

    <div class="action-bar">
      <button id="btn-track-play" class="play-big-btn" title="Play song">${ICONS.play}</button>
      <button id="btn-track-like" class="btn-subtle ${liked ? 'active-follow' : ''}">${liked ? ICONS.heartFilled : ICONS.heart} ${liked ? 'Liked' : 'Like'}</button>
      <button id="btn-track-radio" class="btn-subtle">${ICONS.radio} Song Radio</button>
      <button id="btn-track-dl" class="btn-subtle ${downloaded ? 'active-follow' : ''}">${downloaded ? ICONS.check : ICONS.download} ${downloaded ? 'Offline Saved' : 'Download'}</button>
      <button id="btn-track-share" class="btn-subtle">${ICONS.share} Share</button>
    </div>

    ${
      related.length
        ? `
      <div class="shelf-section">
        <h2 class="shelf-title" style="margin-bottom:12px;">Recommended Based on This Song</h2>
        <div id="track-related-slot"></div>
      </div>
    `
        : ''
    }
  `;

  applyDynamicHeroGradient(container.querySelector('#track-hero-el'), track.title || track.id);

  if (related.length) {
    renderTrackTable(container.querySelector('#track-related-slot'), related, {
      showAlbum: true,
      showHeader: true,
    });
  }

  container.querySelector('#btn-track-play')?.addEventListener('click', () => {
    playTrack(track, [track, ...related], 0);
  });
  container.querySelector('#btn-track-like')?.addEventListener('click', async () => {
    const nowLiked = await toggleLike(track);
    showToast(nowLiked ? 'Saved to Liked Songs' : 'Removed from Liked Songs', 'info');
  });
  container.querySelector('#btn-track-radio')?.addEventListener('click', () => startSongRadio(track));
  container.querySelector('#btn-track-dl')?.addEventListener('click', async () => {
    if (isDownloaded(track.id)) {
      await removeDownload(track.id);
      refreshDownloads();
      showToast('Removed from offline downloads', 'info');
      renderTrackView(container, trackId);
    } else {
      showToast(`Downloading "${track.title}"...`, 'info', 2000);
      try {
        await downloadTrack(track);
        refreshDownloads();
        showToast('Saved for offline playback', 'success');
        renderTrackView(container, trackId);
      } catch (err) {
        showToast(err.message || 'Download failed', 'error');
      }
    }
  });
  container.querySelector('#btn-track-share')?.addEventListener('click', () => {
    copyShareUrl(`#/track/${encodeURIComponent(track.id)}`, 'Song');
  });
}

export async function renderMixView(container, mixId) {
  container.classList.remove('no-padding');
  const st = getState();
  let mixes = st.madeForYou || [];
  let mix = mixes.find((m) => m.id === mixId);

  if (!mix) {
    container.innerHTML = `<div class="skeleton" style="height:200px; border-radius:12px;"></div>`;
    try {
      const res = await api.getMadeForYou(st.region);
      mixes = res?.mixes || [];
      setState({ madeForYou: mixes });
      mix = mixes.find((m) => m.id === mixId);
    } catch {
      mix = null;
    }
  }

  if (!mix) {
    container.innerHTML = `
      <div class="empty-state">
        <h3>Mix not found</h3>
        <button class="btn-primary" id="mix-back-home">Back to Home</button>
      </div>
    `;
    container.querySelector('#mix-back-home')?.addEventListener('click', () => navigate('#/'));
    return;
  }

  const tracks = mix.tracks || [];
  const totalDur = tracks.reduce((acc, t) => acc + (Number(t.duration) || 0), 0);

  container.innerHTML = `
    <section class="view-hero" id="mix-hero-el" style="background:linear-gradient(135deg, ${escapeHtml(mix.color || '#1e3264')}, #181818);">
      <div class="hero-art">
        ${
          mix.cover
            ? `<img src="${escapeHtml(mix.cover)}" alt="${escapeHtml(mix.title)}" />`
            : `<div class="custom-cover-art" style="background:${escapeHtml(mix.color || '#1e3264')};">${escapeHtml(mix.title.slice(0, 2))}</div>`
        }
      </div>
      <div class="hero-meta">
        <div class="hero-type">MADE FOR YOU</div>
        <h1 class="hero-title">${escapeHtml(mix.title)}</h1>
        <p class="hero-desc">${escapeHtml(mix.subtitle || '')}</p>
        <div class="hero-stats">
          <strong>${tracks.length} songs</strong>
          ${totalDur ? `<span>&bull; ${formatTotalDuration(totalDur)}</span>` : ''}
        </div>
      </div>
    </section>

    <div class="action-bar">
      <button id="btn-mix-play" class="play-big-btn" ${tracks.length ? '' : 'disabled'}>${ICONS.play}</button>
      <button id="btn-mix-shuffle" class="btn-subtle" ${tracks.length ? '' : 'disabled'}>${ICONS.shuffle} Shuffle</button>
      <button id="btn-mix-save" class="btn-subtle" ${tracks.length ? '' : 'disabled'}>${ICONS.plus} Save as Playlist</button>
    </div>

    <div id="mix-tracks-slot"></div>
  `;

  renderTrackTable(container.querySelector('#mix-tracks-slot'), tracks, {
    showAlbum: true,
    showHeader: true,
    sortable: true,
  });

  container.querySelector('#btn-mix-play')?.addEventListener('click', () => playList(tracks, 0, false));
  container.querySelector('#btn-mix-shuffle')?.addEventListener('click', () => playList(tracks, 0, true));
  container.querySelector('#btn-mix-save')?.addEventListener('click', async () => {
    try {
      const created = await api.createPlaylist(mix.title, mix.subtitle || 'Saved from Made For You');
      const newId = created?.playlist?.id;
      if (newId) {
        if (mix.color) {
          await api.updatePlaylist(newId, { coverColor: mix.color }).catch(() => {});
        }
        for (const tr of tracks) {
          await api.addTrackToPlaylist(newId, tr).catch(() => {});
        }
        await refreshPlaylists();
        showToast(`Saved "${mix.title}" to Your Library`, 'success');
        navigate(`#/playlist/${encodeURIComponent(newId)}`);
      }
    } catch (err) {
      showToast(err.message || 'Could not save mix as playlist', 'error');
    }
  });
}
