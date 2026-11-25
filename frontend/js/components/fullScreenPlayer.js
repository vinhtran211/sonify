// Ambient Full-Screen Player Overlay (Artwork + Synced Clickable Lyrics + Minimal Controls)

import { hashColorForSeed } from '../colorExtractor.js';
import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml, findActiveLineIndex, formatTime } from '../lrc.js';
import {
  getAudioElement,
  playNextTrack,
  playPrevTrack,
  seekTo,
  togglePlay,
} from '../player.js';
import { getState, setFullScreenMode, toggleLike } from '../store.js';
import { openAudioSettingsModal } from './audioSettingsModal.js';

let lastFsActiveLine = -1;

export function renderFullScreenPlayer() {
  const root = document.getElementById('fullscreen-root');
  if (!root) return;
  const st = getState();

  if (!st.fullScreenMode || !st.currentTrack) {
    root.classList.add('hidden');
    root.innerHTML = '';
    lastFsActiveLine = -1;
    return;
  }

  const track = st.currentTrack;
  const liked = st.likedIds.has(track.id);
  const accent = hashColorForSeed(track.album || track.title || track.id);
  const lyrics = st.lyricsData;
  const hasSynced = Boolean(lyrics?.synced && Array.isArray(lyrics.lines) && lyrics.lines.length > 0);
  const plainLines = lyrics?.plainLyrics ? lyrics.plainLyrics.split(/\r?\n/) : [];
  const dur = st.duration || track.duration || 0;
  const pct = dur > 0 ? Math.min(100, (st.currentTime / dur) * 100) : 0;

  let lyricsMarkup = '';
  if (st.lyricsLoading) {
    lyricsMarkup = `<div class="fs-lyrics-empty">Loading synced lyrics...</div>`;
  } else if (hasSynced) {
    lyricsMarkup = `
      <div class="fs-lyrics-scroll" id="fs-lyrics-scroll">
        ${lyrics.lines
          .map((line, i) =>
            line.interlude
              ? `<div class="fs-lyric-line interlude" data-fs-line="${i}" data-time="${line.time}">• • •</div>`
              : `<button type="button" class="fs-lyric-line" data-fs-line="${i}" data-time="${line.time}">${escapeHtml(
                  line.text
                )}</button>`
          )
          .join('')}
      </div>
    `;
  } else if (plainLines.length > 0) {
    lyricsMarkup = `
      <div class="fs-lyrics-scroll">
        ${plainLines.map((l) => `<div class="fs-lyric-plain">${escapeHtml(l || ' ')}</div>`).join('')}
      </div>
    `;
  } else {
    lyricsMarkup = `<div class="fs-lyrics-empty">No synced lyrics available for this track.</div>`;
  }

  root.classList.remove('hidden');
  root.style.background = `radial-gradient(circle at 22% 28%, ${accent} 0%, #121212 58%, #070707 100%)`;

  root.innerHTML = `
    <div class="fs-shell">
      <div class="fs-topbar">
        <div class="fs-brand">
          <span class="adfree-dot"></span>
          <span>Sonify · Ad-Free Full Screen</span>
        </div>
        <div style="display:flex; gap:8px; align-items:center;">
          ${
            st.sleepTimer?.mode !== 'off'
              ? `<button id="fs-sleep-pill" class="pill-btn active" title="Sleep Timer">
                  ${ICONS.moon} ${
                  st.sleepTimer.mode === 'countdown'
                    ? formatTime(st.sleepTimer.remainingSec)
                    : 'End of track'
                }
                </button>`
              : ''
          }
          <button id="fs-audio-btn" class="icon-btn" title="Audio Settings & EQ">${ICONS.sliders}</button>
          <button id="fs-close-btn" class="icon-circle-btn" title="Exit Full Screen (Esc / F)" aria-label="Exit Full Screen">${ICONS.close}</button>
        </div>
      </div>

      <div class="fs-body">
        <div class="fs-art-col">
          <img class="fs-cover" src="${escapeHtml(coverUrlFor(track, true))}" alt="${escapeHtml(track.title)}" />
          <div class="fs-track-info">
            <div class="fs-track-title">${escapeHtml(track.title)}</div>
            <div class="fs-track-artist">${escapeHtml(track.artist)}</div>
          </div>
        </div>

        <div class="fs-lyrics-col">
          ${lyricsMarkup}
        </div>
      </div>

      <div class="fs-controls">
        <div class="fs-progress-row">
          <span id="fs-time-cur" class="time-label">${formatTime(st.currentTime)}</span>
          <input id="fs-seek" class="fs-seek-input" type="range" min="0" max="${Math.max(1, Math.floor(dur))}" value="${Math.floor(st.currentTime)}" style="--pct:${pct}%" />
          <span id="fs-time-dur" class="time-label">${formatTime(dur)}</span>
        </div>
        <div class="fs-btn-row">
          <button id="fs-like-btn" class="icon-btn ${liked ? 'liked' : ''}" title="Like">
            ${liked ? ICONS.heartFilled : ICONS.heart}
          </button>
          <button id="fs-prev-btn" class="icon-btn" title="Previous">${ICONS.prev}</button>
          <button id="fs-play-btn" class="play-circle-btn" title="${st.isPlaying ? 'Pause' : 'Play'}">
            ${st.isPlaying ? ICONS.pause : ICONS.play}
          </button>
          <button id="fs-next-btn" class="icon-btn" title="Next">${ICONS.next}</button>
          <button id="fs-exit-btn" class="icon-btn" title="Exit Full Screen">${ICONS.close}</button>
        </div>
      </div>
    </div>
  `;

  root.querySelector('#fs-close-btn')?.addEventListener('click', () => setFullScreenMode(false));
  root.querySelector('#fs-exit-btn')?.addEventListener('click', () => setFullScreenMode(false));
  root.querySelector('#fs-audio-btn')?.addEventListener('click', () => openAudioSettingsModal());
  root.querySelector('#fs-sleep-pill')?.addEventListener('click', () => openAudioSettingsModal());
  root.querySelector('#fs-like-btn')?.addEventListener('click', () => toggleLike(track).catch(() => {}));
  root.querySelector('#fs-prev-btn')?.addEventListener('click', () => playPrevTrack());
  root.querySelector('#fs-play-btn')?.addEventListener('click', () => togglePlay());
  root.querySelector('#fs-next-btn')?.addEventListener('click', () => playNextTrack(false));

  const seekInput = root.querySelector('#fs-seek');
  seekInput?.addEventListener('input', () => {
    seekTo(Number(seekInput.value));
  });

  root.querySelectorAll('[data-fs-line]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const t = Number(btn.dataset.time);
      if (Number.isFinite(t)) seekTo(t, true);
    });
  });

  lastFsActiveLine = -1;
  syncFullScreenProgress();
}

export function syncFullScreenProgress() {
  const st = getState();
  if (!st.fullScreenMode) return;
  const root = document.getElementById('fullscreen-root');
  if (!root || root.classList.contains('hidden')) return;

  const audio = getAudioElement();
  const cur = audio && Number.isFinite(audio.currentTime) ? audio.currentTime : st.currentTime || 0;
  const dur = st.duration || st.currentTrack?.duration || 0;

  const curEl = root.querySelector('#fs-time-cur');
  const durEl = root.querySelector('#fs-time-dur');
  const seekEl = root.querySelector('#fs-seek');
  if (curEl) curEl.textContent = formatTime(cur);
  if (durEl) durEl.textContent = formatTime(dur);
  if (seekEl && document.activeElement !== seekEl) {
    seekEl.max = String(Math.max(1, Math.floor(dur)));
    seekEl.value = String(Math.floor(cur));
  }

  const lines = st.lyricsData?.synced ? st.lyricsData.lines : null;
  if (!lines || !lines.length) return;
  const idx = findActiveLineIndex(lines, cur);
  if (idx !== lastFsActiveLine) {
    lastFsActiveLine = idx;
    root.querySelectorAll('[data-fs-line]').forEach((el, i) => {
      el.classList.toggle('active', i === idx);
      el.classList.toggle('past', i < idx);
    });
    const activeEl = root.querySelector(`[data-fs-line="${idx}"]`);
    if (activeEl) {
      activeEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }
}
