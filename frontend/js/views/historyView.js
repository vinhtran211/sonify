// Play History View (#/history) — Chronological listening history with relative timestamps & Clear

import { api } from '../api.js';
import { showToast } from '../components/modals.js';
import { renderTrackTable } from '../components/trackList.js';
import { ICONS } from '../icons.js';
import { playList } from '../player.js';
import { getState, refreshHistory } from '../store.js';

export function renderHistoryView(container) {
  container.classList.remove('no-padding');
  const st = getState();
  const items = st.history || [];
  const tracks = items.map((x) => x.track).filter(Boolean);
  const timestamps = items.map((x) => x.playedAt);

  container.innerHTML = `
    <section class="view-hero artist-hero">
      <div class="hero-art" style="background: linear-gradient(135deg, #1db954, #121212); color:#fff;">
        <div style="width:72px; height:72px;">${ICONS.history}</div>
      </div>
      <div class="hero-meta">
        <div class="hero-type">Your Activity</div>
        <h1 class="hero-title">Listening History</h1>
        <div class="hero-stats">
          <strong>${tracks.length} recent ${tracks.length === 1 ? 'play' : 'plays'}</strong>
        </div>
      </div>
    </section>

    <div class="action-bar">
      <button id="btn-hist-play" class="play-big-btn" title="Play recent songs" ${tracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.play}</button>
      <button id="btn-hist-clear" class="btn-subtle" ${tracks.length === 0 ? 'disabled style="opacity:0.4;"' : ''}>${ICONS.trash} Clear History</button>
    </div>

    <div id="history-tracks-slot"></div>
  `;

  const slot = container.querySelector('#history-tracks-slot');
  if (tracks.length > 0) {
    renderTrackTable(slot, tracks, {
      showAlbum: true,
      showHeader: true,
      playedAtList: timestamps,
    });
  } else {
    slot.innerHTML = `
      <div class="empty-state">
        <h3>No listening history yet</h3>
        <p>Tracks you play will be recorded here chronologically.</p>
      </div>
    `;
  }

  container.querySelector('#btn-hist-play')?.addEventListener('click', () => {
    if (tracks.length) playList(tracks, 0, false);
  });

  container.querySelector('#btn-hist-clear')?.addEventListener('click', async () => {
    try {
      await api.clearHistory();
      await refreshHistory();
      showToast('Play history cleared', 'info');
    } catch (err) {
      showToast(err.message || 'Could not clear history', 'error');
    }
  });
}
