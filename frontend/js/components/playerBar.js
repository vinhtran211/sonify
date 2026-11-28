// Persistent Bottom Player Bar (DOM mounted once, updated incrementally for silky scrubbing)

import { ICONS } from '../icons.js';
import { coverUrlFor, formatTime } from '../lrc.js';
import {
  cycleRepeat,
  playNextTrack,
  playPrevTrack,
  seekTo,
  setVolume,
  toggleMute,
  togglePlay,
  toggleShuffle,
} from '../player.js';
import { navigate } from '../router.js';
import { getState, setFullScreenMode, toggleDrawer, toggleLike } from '../store.js';
import { openAudioSettingsModal } from './audioSettingsModal.js';
import { showToast } from './modals.js';

let mounted = false;
let isDraggingSeek = false;
let dragSeekPercent = 0;
let isDraggingVol = false;

function bindSlider(barEl, onMove, onEnd) {
  const computeRatio = (clientX) => {
    const rect = barEl.getBoundingClientRect();
    if (rect.width <= 0) return 0;
    return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  };

  barEl.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    barEl.setPointerCapture(e.pointerId);
    barEl.classList.add('dragging');
    const ratio = computeRatio(e.clientX);
    onMove(ratio);

    const handleMove = (ev) => onMove(computeRatio(ev.clientX));
    const handleUp = (ev) => {
      barEl.classList.remove('dragging');
      barEl.removeEventListener('pointermove', handleMove);
      barEl.removeEventListener('pointerup', handleUp);
      barEl.removeEventListener('pointercancel', handleUp);
      onEnd(computeRatio(ev.clientX));
    };

    barEl.addEventListener('pointermove', handleMove);
    barEl.addEventListener('pointerup', handleUp);
    barEl.addEventListener('pointercancel', handleUp);
  });
}

export function initPlayerBar() {
  const bar = document.getElementById('player-bar');
  if (!bar || mounted) return;

  bar.innerHTML = `
    <div class="player-left">
      <img id="pb-cover" class="player-cover" src="/api/artwork/sonify" alt="Album cover" />
      <div class="player-track-info">
        <div class="player-title-row">
          <span id="pb-title" class="player-track-title truncate">Ready to Play</span>
        </div>
        <div id="pb-artist" class="player-track-artist truncate">Choose any track to start listening ad-free</div>
        <span id="pb-stream-badge" class="stream-badge hidden"></span>
      </div>
      <button id="pb-like" class="icon-btn heart-btn" title="Save to Liked Songs" aria-label="Save to Liked Songs">${ICONS.heart}</button>
    </div>

    <div class="player-center">
      <div class="player-controls">
        <button id="pb-shuffle" class="icon-btn btn-shuffle" title="Shuffle (S)" aria-label="Shuffle">${ICONS.shuffle}</button>
        <button id="pb-prev" class="icon-btn" title="Previous (Shift+Left)" aria-label="Previous track">${ICONS.prev}</button>
        <button id="pb-play" class="play-pause-circle" title="Play / Pause (Space)" aria-label="Play or pause">${ICONS.play}</button>
        <button id="pb-next" class="icon-btn" title="Next (Shift+Right)" aria-label="Next track">${ICONS.next}</button>
        <button id="pb-repeat" class="icon-btn repeat-btn btn-repeat" title="Repeat (R)" aria-label="Repeat">
          ${ICONS.repeat}
          <span id="pb-repeat-one" class="repeat-one-dot hidden">1</span>
        </button>
      </div>
      <div class="seek-row">
        <span id="pb-time-cur" class="time-label right">0:00</span>
        <div id="pb-seek-bar" class="range-bar" role="slider" aria-label="Seek audio position" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
          <div class="range-track">
            <div id="pb-seek-buffered" class="range-buffered"></div>
            <div id="pb-seek-fill" class="range-fill"></div>
          </div>
          <div id="pb-seek-thumb" class="range-thumb"></div>
        </div>
        <span id="pb-time-dur" class="time-label">0:00</span>
      </div>
    </div>

    <div class="player-right">
      <button id="pb-sleep-badge" class="sleep-pill hidden" title="Sleep Timer Active"></button>
      <button id="pb-nowplaying-btn" class="icon-btn" title="Now Playing View" aria-label="Now Playing View">${ICONS.nowPlaying}</button>
      <button id="pb-lyrics-btn" class="icon-btn" title="Synced Lyrics Drawer (L)" aria-label="Toggle lyrics drawer">${ICONS.lyrics}</button>
      <button id="pb-queue-btn" class="icon-btn" title="Queue (Q)" aria-label="Toggle queue drawer">${ICONS.queue}</button>
      <button id="pb-audio-btn" class="icon-btn" title="Equalizer, Crossfade, Speed & Sleep Timer" aria-label="Audio Settings">${ICONS.sliders}</button>
      <button id="pb-fullscreen-btn" class="icon-btn" title="Full-Screen Mode (F)" aria-label="Full-Screen Mode">${ICONS.expand}</button>
      <div class="volume-wrap">
        <button id="pb-mute-btn" class="icon-btn" title="Mute / Unmute (M)" aria-label="Mute or unmute">${ICONS.volumeHigh}</button>
        <div id="pb-vol-bar" class="range-bar" role="slider" aria-label="Volume" aria-valuemin="0" aria-valuemax="100" aria-valuenow="80">
          <div class="range-track">
            <div id="pb-vol-fill" class="range-fill"></div>
          </div>
          <div id="pb-vol-thumb" class="range-thumb"></div>
        </div>
      </div>
    </div>
  `;

  // Wire controls
  bar.querySelector('#pb-play').addEventListener('click', () => togglePlay());
  bar.querySelector('#pb-prev').addEventListener('click', () => playPrevTrack());
  bar.querySelector('#pb-next').addEventListener('click', () => playNextTrack(false));
  bar.querySelector('#pb-shuffle').addEventListener('click', () => toggleShuffle());
  bar.querySelector('#pb-repeat').addEventListener('click', () => cycleRepeat());
  bar.querySelector('#pb-mute-btn').addEventListener('click', () => toggleMute());
  bar.querySelector('#pb-nowplaying-btn').addEventListener('click', () => toggleDrawer('now-playing'));
  bar.querySelector('#pb-lyrics-btn').addEventListener('click', () => toggleDrawer('lyrics'));
  bar.querySelector('#pb-queue-btn').addEventListener('click', () => toggleDrawer('queue'));
  bar.querySelector('#pb-audio-btn').addEventListener('click', () => openAudioSettingsModal());
  bar.querySelector('#pb-sleep-badge').addEventListener('click', () => openAudioSettingsModal());

  bar.querySelector('#pb-fullscreen-btn').addEventListener('click', () => {
    const st = getState();
    if (!st.currentTrack) {
      showToast('Play a track first to open Full-Screen Mode', 'info');
      return;
    }
    setFullScreenMode(!st.fullScreenMode);
  });

  bar.querySelector('#pb-cover').addEventListener('click', () => {
    const tr = getState().currentTrack;
    if (tr?.albumId) {
      navigate(`#/album/${encodeURIComponent(tr.albumId)}`);
    } else if (tr) {
      navigate('#/lyrics');
    }
  });

  bar.querySelector('#pb-title').addEventListener('click', () => {
    const tr = getState().currentTrack;
    if (tr?.id) {
      navigate(`#/track/${encodeURIComponent(tr.id)}`);
    }
  });

  bar.querySelector('#pb-artist').addEventListener('click', () => {
    const tr = getState().currentTrack;
    if (tr?.artistId) {
      navigate(`#/artist/${encodeURIComponent(tr.artistId)}`);
    }
  });

  bar.querySelector('#pb-like').addEventListener('click', async () => {
    const tr = getState().currentTrack;
    if (!tr) return;
    try {
      const liked = await toggleLike(tr);
      showToast(liked ? 'Saved to Liked Songs' : 'Removed from Liked Songs', liked ? 'success' : 'info');
    } catch (err) {
      showToast(err.message || 'Could not update like', 'error');
    }
  });

  // Seek bar scrubbing
  const seekBar = bar.querySelector('#pb-seek-bar');
  bindSlider(
    seekBar,
    (ratio) => {
      isDraggingSeek = true;
      dragSeekPercent = ratio;
      const dur = getState().duration || 0;
      updateSeekVisuals(ratio * dur, dur, getState().buffered);
    },
    (ratio) => {
      isDraggingSeek = false;
      const dur = getState().duration || 0;
      if (dur > 0) seekTo(ratio * dur);
    }
  );

  // Volume bar scrubbing
  const volBar = bar.querySelector('#pb-vol-bar');
  bindSlider(
    volBar,
    (ratio) => {
      isDraggingVol = true;
      setVolume(ratio);
    },
    (ratio) => {
      isDraggingVol = false;
      setVolume(ratio);
    }
  );

  mounted = true;
  updatePlayerBar();
}

function updateSeekVisuals(curTime, duration, buffered) {
  const pct = duration > 0 ? Math.max(0, Math.min(100, (curTime / duration) * 100)) : 0;
  const bufPct = duration > 0 ? Math.max(0, Math.min(100, (buffered / duration) * 100)) : 0;

  const curEl = document.getElementById('pb-time-cur');
  const durEl = document.getElementById('pb-time-dur');
  const fillEl = document.getElementById('pb-seek-fill');
  const bufEl = document.getElementById('pb-seek-buffered');
  const thumbEl = document.getElementById('pb-seek-thumb');
  const seekBar = document.getElementById('pb-seek-bar');

  if (curEl) curEl.textContent = formatTime(curTime);
  if (durEl) durEl.textContent = formatTime(duration);
  if (fillEl) fillEl.style.width = `${pct}%`;
  if (bufEl) bufEl.style.width = `${bufPct}%`;
  if (thumbEl) thumbEl.style.left = `${pct}%`;
  if (seekBar) seekBar.setAttribute('aria-valuenow', String(Math.round(pct)));
}

export function updatePlayerBar() {
  if (!mounted) return;
  const st = getState();
  const track = st.currentTrack;

  const coverEl = document.getElementById('pb-cover');
  const titleEl = document.getElementById('pb-title');
  const artistEl = document.getElementById('pb-artist');
  const badgeEl = document.getElementById('pb-stream-badge');
  const likeBtn = document.getElementById('pb-like');
  const playBtn = document.getElementById('pb-play');
  const shuffleBtn = document.getElementById('pb-shuffle');
  const repeatBtn = document.getElementById('pb-repeat');
  const repeatOneDot = document.getElementById('pb-repeat-one');
  const nowBtn = document.getElementById('pb-nowplaying-btn');
  const lyricsBtn = document.getElementById('pb-lyrics-btn');
  const queueBtn = document.getElementById('pb-queue-btn');
  const audioBtn = document.getElementById('pb-audio-btn');
  const fsBtn = document.getElementById('pb-fullscreen-btn');
  const sleepBadge = document.getElementById('pb-sleep-badge');
  const muteBtn = document.getElementById('pb-mute-btn');
  const volFill = document.getElementById('pb-vol-fill');
  const volThumb = document.getElementById('pb-vol-thumb');
  const volBar = document.getElementById('pb-vol-bar');

  if (track) {
    const art = coverUrlFor(track, false);
    if (coverEl && coverEl.getAttribute('src') !== art) coverEl.setAttribute('src', art);
    if (titleEl) titleEl.textContent = track.title || 'Untitled';
    if (artistEl) artistEl.textContent = track.artist || 'Unknown Artist';
    if (likeBtn) {
      const liked = st.likedIds.has(track.id);
      likeBtn.classList.toggle('liked', liked);
      likeBtn.innerHTML = liked ? ICONS.heartFilled : ICONS.heart;
      likeBtn.disabled = false;
    }
    if (badgeEl) {
      if (st.streamMode === 'offline-download') {
        badgeEl.textContent = 'Offline Download';
        badgeEl.className = 'stream-badge full';
      } else if (st.streamMode === 'full') {
        const srcLabel =
          st.streamSource === 'monochrome'
            ? 'Lossless FLAC'
            : String(st.streamSource || '').endsWith('-mp3')
            ? 'Compact MP3'
            : String(st.streamSource || '').endsWith('-compact')
            ? 'Compact M4A'
            : st.streamSource || '';
        badgeEl.textContent = `Full Track${srcLabel ? ` • ${srcLabel}` : ''}`;
        badgeEl.className = 'stream-badge full';
      } else if (st.streamMode === 'preview') {
        badgeEl.textContent = '30s Preview';
        badgeEl.className = 'stream-badge preview';
      } else if (st.streamMode === 'offline-synth') {
        badgeEl.textContent = 'Offline Studio Synth';
        badgeEl.className = 'stream-badge offline';
      } else if (st.streamMode === 'error') {
        badgeEl.textContent = 'Stream Unavailable';
        badgeEl.className = 'stream-badge error';
      } else if (st.isLoading) {
        badgeEl.textContent = 'Connecting Ad-Free Stream...';
        badgeEl.className = 'stream-badge offline';
      } else {
        badgeEl.className = 'stream-badge hidden';
      }
    }
  } else {
    if (titleEl) titleEl.textContent = 'Ready to Play';
    if (artistEl) artistEl.textContent = 'Choose any song to listen 100% ad-free';
    if (badgeEl) badgeEl.className = 'stream-badge hidden';
    if (likeBtn) {
      likeBtn.classList.remove('liked');
      likeBtn.innerHTML = ICONS.heart;
      likeBtn.disabled = true;
    }
  }

  if (playBtn) {
    playBtn.innerHTML = st.isPlaying ? ICONS.pause : ICONS.play;
  }

  if (shuffleBtn) shuffleBtn.classList.toggle('active', Boolean(st.shuffle));
  if (repeatBtn) {
    repeatBtn.classList.toggle('active', st.repeat !== 'off');
    repeatBtn.title = `Repeat: ${st.repeat} (R)`;
  }
  if (repeatOneDot) repeatOneDot.classList.toggle('hidden', st.repeat !== 'one');

  if (nowBtn) nowBtn.classList.toggle('active', st.drawerMode === 'now-playing');
  if (lyricsBtn) lyricsBtn.classList.toggle('active', st.drawerMode === 'lyrics');
  if (queueBtn) queueBtn.classList.toggle('active', st.drawerMode === 'queue');
  if (fsBtn) fsBtn.classList.toggle('active', Boolean(st.fullScreenMode));

  const eqActive =
    (Array.isArray(st.eqBands) && st.eqBands.some((b) => Math.abs(Number(b) || 0) >= 0.5)) ||
    (st.crossfade || 0) > 0 ||
    Math.abs((st.playbackRate || 1) - 1) > 0.01;
  if (audioBtn) audioBtn.classList.toggle('active', eqActive);

  if (sleepBadge) {
    if (st.sleepTimer?.mode === 'countdown') {
      sleepBadge.textContent = `⏱ ${formatTime(st.sleepTimer.remainingSec)}`;
      sleepBadge.classList.remove('hidden');
    } else if (st.sleepTimer?.mode === 'end-of-track') {
      sleepBadge.textContent = '⏱ End';
      sleepBadge.classList.remove('hidden');
    } else {
      sleepBadge.classList.add('hidden');
    }
  }

  if (!isDraggingSeek) {
    updateSeekVisuals(st.currentTime, st.duration, st.buffered);
  }

  const effectiveVol = st.muted ? 0 : st.volume;
  const volPct = Math.round(effectiveVol * 100);
  if (volFill) volFill.style.width = `${volPct}%`;
  if (volThumb) volThumb.style.left = `${volPct}%`;
  if (volBar) volBar.setAttribute('aria-valuenow', String(volPct));
  if (muteBtn) {
    muteBtn.innerHTML =
      effectiveVol === 0 ? ICONS.volumeMute : effectiveVol < 0.5 ? ICONS.volumeLow : ICONS.volumeHigh;
  }
}
