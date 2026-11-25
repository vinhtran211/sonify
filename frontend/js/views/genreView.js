// Genre & Mood Hub View (#/genre/:slug)

import { api } from '../api.js';
import { applyDynamicHeroGradient } from '../colorExtractor.js';
import { renderMediaCard, renderTrackList, wireTrackListEvents } from '../components/trackList.js';
import { ICONS } from '../icons.js';
import { escapeHtml } from '../lrc.js';
import { playList } from '../player.js';
import { navigate } from '../router.js';
import { getState } from '../store.js';

let activeReqId = 0;

export async function renderGenreView(container, slug) {
  if (!container || !slug) return;
  const reqId = ++activeReqId;
  const st = getState();

  container.innerHTML = `
    <div style="padding:20px 0; display:flex; flex-direction:column; gap:18px;">
      <div class="skeleton" style="height:180px; border-radius:12px;"></div>
      <div class="skeleton" style="height:26px; width:220px;"></div>
      <div class="skeleton" style="height:220px; border-radius:10px;"></div>
    </div>
  `;

  try {
    const data = await api.getGenreHub(slug, st.region);
    if (reqId !== activeReqId) return;

    const genre = data?.genre || { slug, name: slug, color: '#1e3264' };
    const tracks = data?.tracks || [];
    const albums = data?.albums || [];
    const artists = data?.artists || [];

    container.innerHTML = `
      <section class="hero-banner genre-hero" id="genre-hero-banner" style="--hero-color:${escapeHtml(genre.color || '#1e3264')};">
        <div class="hero-meta">
          <span class="hero-type">GENRE & MOOD HUB</span>
          <h1 class="hero-title">${escapeHtml(genre.name)}</h1>
          <div class="hero-sub">
            <span>${tracks.length} tracks</span>
            ${albums.length ? `<span>• ${albums.length} albums</span>` : ''}
            ${artists.length ? `<span>• ${artists.length} artists</span>` : ''}
          </div>
        </div>
      </section>

      <div class="action-bar">
        <button id="genre-play-btn" class="hero-play-btn" title="Play ${escapeHtml(genre.name)}" ${tracks.length ? '' : 'disabled'}>
          ${ICONS.play}
        </button>
        <button id="genre-shuffle-btn" class="btn-subtle" ${tracks.length ? '' : 'disabled'}>
          ${ICONS.shuffle} Shuffle
        </button>
        <button id="genre-browse-all" class="btn-subtle">Browse All Genres</button>
      </div>

      ${
        tracks.length
          ? `
        <section class="section">
          <div class="section-header"><h2 class="section-title">Popular in ${escapeHtml(genre.name)}</h2></div>
          <div id="genre-tracks-slot">
            ${renderTrackList(tracks, { showAlbum: true, sortable: true })}
          </div>
        </section>
      `
          : `<div class="empty-state"><h3>No tracks found for this genre yet</h3></div>`
      }

      ${
        albums.length
          ? `
        <section class="section">
          <div class="section-header"><h2 class="section-title">Featured Albums</h2></div>
          <div class="card-grid">
            ${albums.map((a) => renderMediaCard(a, 'album')).join('')}
          </div>
        </section>
      `
          : ''
      }

      ${
        artists.length
          ? `
        <section class="section">
          <div class="section-header"><h2 class="section-title">Top Artists</h2></div>
          <div class="card-grid">
            ${artists.map((ar) => renderMediaCard(ar, 'artist')).join('')}
          </div>
        </section>
      `
          : ''
      }
    `;

    const hero = container.querySelector('#genre-hero-banner');
    if (hero && !genre.color) {
      applyDynamicHeroGradient(hero, genre.name);
    }

    container.querySelector('#genre-play-btn')?.addEventListener('click', () => playList(tracks, 0));
    container.querySelector('#genre-shuffle-btn')?.addEventListener('click', () => playList(tracks, 0, true));
    container.querySelector('#genre-browse-all')?.addEventListener('click', () => navigate('#/search'));

    const tracksSlot = container.querySelector('#genre-tracks-slot');
    if (tracksSlot) wireTrackListEvents(tracksSlot, tracks);

    container.querySelectorAll('.media-card').forEach((card) => {
      card.addEventListener('click', () => {
        const type = card.dataset.cardType;
        const id = card.dataset.cardId;
        if (type === 'album') navigate(`#/album/${encodeURIComponent(id)}`);
        else if (type === 'artist') navigate(`#/artist/${encodeURIComponent(id)}`);
      });
    });
  } catch (err) {
    if (reqId !== activeReqId) return;
    container.innerHTML = `
      <div class="empty-state">
        <h3>Could not load genre hub</h3>
        <p>${escapeHtml(err.message || 'Unknown error')}</p>
        <button class="btn-primary" id="genre-back-btn">Back to Search</button>
      </div>
    `;
    container.querySelector('#genre-back-btn')?.addEventListener('click', () => navigate('#/search'));
  }
}
