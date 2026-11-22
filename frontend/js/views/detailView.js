// Artist Detail (#/artist/:id) and Album Detail (#/album/:id) Views

import { api } from '../api.js';
import { renderTrackTable } from '../components/trackList.js';
import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml, formatTotalDuration } from '../lrc.js';
import { playList } from '../player.js';
import { navigate } from '../router.js';
import { getState } from '../store.js';

let cachedArtist = { id: null, data: null };
let cachedAlbum = { id: null, data: null };

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

  const { artist, topTracks = [], albums = [] } = cachedArtist.data;

  container.innerHTML = `
    <section class="view-hero artist-hero">
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

  renderTrackTable(container.querySelector('#artist-tracks-slot'), topTracks, {
    showAlbum: true,
    showHeader: true,
  });

  container.querySelector('#btn-artist-play')?.addEventListener('click', () => {
    if (topTracks.length) playList(topTracks, 0, false);
  });
  container.querySelector('#btn-artist-shuffle')?.addEventListener('click', () => {
    if (topTracks.length) playList(topTracks, 0, true);
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

  const { album, tracks = [] } = cachedAlbum.data;
  const totalDur = album.totalDuration || tracks.reduce((acc, t) => acc + (Number(t.duration) || 0), 0);

  container.innerHTML = `
    <section class="view-hero">
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
    </div>

    <div id="album-tracks-slot"></div>
  `;

  renderTrackTable(container.querySelector('#album-tracks-slot'), tracks, {
    showAlbum: false,
    showHeader: true,
  });

  container.querySelector('#btn-album-play')?.addEventListener('click', () => {
    if (tracks.length) playList(tracks, 0, false);
  });
  container.querySelector('#btn-album-shuffle')?.addEventListener('click', () => {
    if (tracks.length) playList(tracks, 0, true);
  });
}
