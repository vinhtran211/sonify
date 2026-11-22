// Synced & Plain Lyrics Engine (Right Drawer + Full-Stage #/lyrics View)
// Features: binary-search active line highlight, rAF loop, 4s manual-scroll pause + Re-sync pill,
// click-to-seek, plain/instrumental/not-found states, and manual Title/Artist override search.

import { api } from '../api.js';
import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml, findActiveLineIndex } from '../lrc.js';
import { getAudioElement, seekTo } from '../player.js';
import { getState, setState } from '../store.js';

let lyricsAbort = null;
let rafId = null;
let activeLineIdx = -1;
let autoScrollPausedUntil = 0;
let isProgrammaticScroll = false;

function getCurrentPlaybackTime() {
  const audio = getAudioElement();
  if (audio && Number.isFinite(audio.currentTime)) {
    return audio.currentTime;
  }
  return getState().currentTime || 0;
}

export async function loadLyricsForCurrentTrack(override = null, refresh = false) {
  const st = getState();
  const track = st.currentTrack;
  if (!track) {
    setState({ lyricsTrackId: null, lyricsData: null, lyricsLoading: false });
    return;
  }

  // Skip redundant fetch if already loaded for same track and not forced
  if (!override && !refresh && st.lyricsTrackId === track.id && (st.lyricsData || st.lyricsLoading)) {
    return;
  }

  if (lyricsAbort) lyricsAbort.abort();
  lyricsAbort = new AbortController();
  const signal = lyricsAbort.signal;

  activeLineIdx = -1;
  autoScrollPausedUntil = 0;
  setState({ lyricsTrackId: track.id, lyricsLoading: true });

  try {
    const data = await api.getLyrics(
      {
        trackId: override ? '' : track.id,
        title: override?.title || track.title || '',
        artist: override?.artist || track.artist || '',
        album: override ? '' : track.album || '',
        duration: override ? 0 : track.duration || 0,
        refresh: Boolean(refresh || override),
      },
      signal
    );
    if (getState().currentTrack?.id === track.id) {
      setState({ lyricsData: data, lyricsLoading: false });
    }
  } catch (err) {
    if (err.name === 'AbortError') return;
    if (getState().currentTrack?.id === track.id) {
      setState({
        lyricsData: {
          trackId: track.id,
          title: track.title,
          artist: track.artist,
          status: 'not_found',
          source: '',
          instrumental: false,
          synced: false,
          lines: [],
          plainLyrics: '',
        },
        lyricsLoading: false,
      });
    }
  }
}

function buildLyricsInnerHtml(st, isFullStage) {
  const track = st.currentTrack;
  if (!track) {
    return `
      <div class="lyrics-state-box">
        <div class="lyrics-state-icon">${ICONS.lyrics}</div>
        <h3>No song playing</h3>
        <p style="max-width: 280px; font-size: 13.5px;">Start playing any track to view real-time synced lyrics and tap any line to jump straight to that moment.</p>
      </div>
    `;
  }

  if (st.lyricsLoading) {
    return `
      <div style="display:flex; flex-direction:column; gap:14px; padding: 12px 4px;">
        <div class="skeleton" style="height:26px; width:72%;"></div>
        <div class="skeleton" style="height:26px; width:88%;"></div>
        <div class="skeleton" style="height:26px; width:64%;"></div>
        <div class="skeleton" style="height:26px; width:82%;"></div>
        <div class="skeleton" style="height:26px; width:56%;"></div>
      </div>
    `;
  }

  const data = st.lyricsData;
  if (!data || data.status === 'not_found') {
    return `
      <div class="lyrics-state-box">
        <div class="lyrics-state-icon">${ICONS.musicNote}</div>
        <h3>Lyrics not found</h3>
        <p style="font-size: 13px; max-width: 300px; margin: 0;">
          We couldn't find lyrics automatically for <strong>${escapeHtml(track.title)}</strong>. Try adjusting the title or artist below:
        </p>
        <form class="lyrics-manual-form" data-lyrics-search-form>
          <input class="form-input" name="title" placeholder="Song title" value="${escapeHtml(track.title)}" required />
          <input class="form-input" name="artist" placeholder="Artist name" value="${escapeHtml(track.artist)}" />
          <button type="submit" class="btn-primary" style="width:100%;">Search Lyrics</button>
        </form>
      </div>
    `;
  }

  if (data.status === 'instrumental' || data.instrumental) {
    return `
      <div class="lyrics-state-box">
        <div class="lyrics-state-icon">${ICONS.musicNote}</div>
        <h3>Instrumental Track</h3>
        <p style="font-size: 13.5px; max-width: 290px;">Sit back and enjoy the music — this track is marked as instrumental.</p>
      </div>
    `;
  }

  if (data.status === 'synced' && Array.isArray(data.lines) && data.lines.length > 0) {
    const linesHtml = data.lines
      .map((line, i) => {
        if (line.interlude || !line.text) {
          return `<div class="lyric-interlude" data-lyric-idx="${i}" data-time="${line.time}" title="Musical interlude"><span></span><span></span><span></span></div>`;
        }
        return `<button type="button" class="lyric-line" data-lyric-idx="${i}" data-time="${line.time}">${escapeHtml(line.text)}</button>`;
      })
      .join('');

    return `
      <div class="lyrics-lines-list" data-synced-list>
        ${linesHtml}
      </div>
      <div style="text-align:center;">
        <button type="button" class="resync-pill hidden" data-resync-btn>Re-sync to playback</button>
      </div>
    `;
  }

  // Plain unsynced lyrics fallback
  return `
    <div style="margin-bottom: 14px; display: flex; align-items: center; gap: 8px;">
      <span class="lyrics-badge">Unsynced lyrics</span>
      ${data.source ? `<span style="font-size:12px; opacity:0.65;">Source: ${escapeHtml(data.source)}</span>` : ''}
    </div>
    <div class="plain-lyrics-body">${escapeHtml(data.plainLyrics || '')}</div>
  `;
}

function attachLyricsInteractions(scrollArea) {
  if (!scrollArea) return;

  // Pause auto-scroll for 4 seconds on manual user scroll
  const pauseAutoScroll = () => {
    if (isProgrammaticScroll) return;
    autoScrollPausedUntil = Date.now() + 4000;
    const btn = scrollArea.querySelector('[data-resync-btn]');
    if (btn) btn.classList.remove('hidden');
  };

  scrollArea.addEventListener('wheel', pauseAutoScroll, { passive: true });
  scrollArea.addEventListener('touchmove', pauseAutoScroll, { passive: true });

  scrollArea.onclick = (e) => {
    const resyncBtn = e.target.closest('[data-resync-btn]');
    if (resyncBtn) {
      autoScrollPausedUntil = 0;
      resyncBtn.classList.add('hidden');
      syncActiveLineDOM(true);
      return;
    }

    const lineBtn = e.target.closest('[data-lyric-idx]');
    if (lineBtn) {
      const time = parseFloat(lineBtn.dataset.time);
      if (Number.isFinite(time)) {
        autoScrollPausedUntil = 0;
        const btn = scrollArea.querySelector('[data-resync-btn]');
        if (btn) btn.classList.add('hidden');
        seekTo(time, true);
        syncActiveLineDOM(true);
      }
    }
  };

  const form = scrollArea.querySelector('[data-lyrics-search-form]');
  if (form) {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const title = String(fd.get('title') || '').trim();
      const artist = String(fd.get('artist') || '').trim();
      if (title) {
        loadLyricsForCurrentTrack({ title, artist }, true);
      }
    });
  }
}

export function renderLyricsDrawer(container) {
  const st = getState();
  const data = st.lyricsData;
  const badgeHtml =
    data?.synced
      ? `<span class="lyrics-badge synced">Synced &bull; ${escapeHtml(data.source || 'LRCLIB')}</span>`
      : data?.status === 'plain'
      ? `<span class="lyrics-badge">Plain Lyrics</span>`
      : '';

  container.innerHTML = `
    <div class="drawer-lyrics-wrap">
      ${
        st.currentTrack
          ? `<div style="display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:10px;">
               <div class="truncate" style="font-size:12.5px; color:var(--text-secondary);">
                 <strong style="color:#fff;">${escapeHtml(st.currentTrack.title)}</strong> &mdash; ${escapeHtml(st.currentTrack.artist)}
               </div>
               ${badgeHtml}
             </div>`
          : ''
      }
      <div class="lyrics-scroll-area" data-lyrics-scroll>
        ${buildLyricsInnerHtml(st, false)}
      </div>
    </div>
  `;

  attachLyricsInteractions(container.querySelector('[data-lyrics-scroll]'));
  activeLineIdx = -1;
  syncActiveLineDOM(true);
}

export function renderFullStageLyrics(mainContainer) {
  const st = getState();
  const track = st.currentTrack;
  const data = st.lyricsData;
  const art = track ? coverUrlFor(track, true) : '/api/artwork/sonify';

  mainContainer.classList.add('no-padding');
  mainContainer.innerHTML = `
    <div class="lyrics-stage">
      <div class="lyrics-stage-ambient" style="background-image: url('${escapeHtml(art)}');"></div>
      <div class="lyrics-stage-inner">
        <div class="lyrics-stage-header">
          <div class="lyrics-track-banner">
            <img src="${escapeHtml(art)}" alt="" />
            <div class="truncate">
              <h2 class="truncate">${escapeHtml(track ? track.title : 'Synced Lyrics')}</h2>
              <p class="truncate">${escapeHtml(track ? `${track.artist}${track.album ? ` • ${track.album}` : ''}` : 'Play a track to follow along in real time')}</p>
            </div>
          </div>
          <div class="lyrics-meta-badges">
            ${
              data?.synced
                ? `<span class="lyrics-badge synced">Synced Lyrics &bull; Click any line to seek</span>`
                : data?.status === 'plain'
                ? `<span class="lyrics-badge">Unsynced Lyrics</span>`
                : ''
            }
            ${
              track
                ? `<button id="btn-refresh-lyrics" class="btn-subtle btn-xs">Refresh</button>`
                : ''
            }
          </div>
        </div>
        <div class="lyrics-scroll-area" data-lyrics-scroll>
          ${buildLyricsInnerHtml(st, true)}
        </div>
      </div>
    </div>
  `;

  mainContainer.querySelector('#btn-refresh-lyrics')?.addEventListener('click', () => {
    loadLyricsForCurrentTrack(null, true);
  });

  attachLyricsInteractions(mainContainer.querySelector('[data-lyrics-scroll]'));
  activeLineIdx = -1;
  syncActiveLineDOM(true);
}

export function syncActiveLineDOM(forceScroll = false) {
  const st = getState();
  const lines = st.lyricsData?.synced ? st.lyricsData.lines : null;
  if (!lines || !lines.length) return;

  const t = getCurrentPlaybackTime();
  const nextIdx = findActiveLineIndex(lines, t);

  const containers = document.querySelectorAll('[data-lyrics-scroll]');
  if (!containers.length) return;

  const now = Date.now();
  const canAutoScroll = forceScroll || now >= autoScrollPausedUntil;

  // Hide Re-sync button once pause expires naturally
  if (now >= autoScrollPausedUntil && autoScrollPausedUntil !== 0) {
    autoScrollPausedUntil = 0;
    containers.forEach((c) => c.querySelector('[data-resync-btn]')?.classList.add('hidden'));
  }

  if (nextIdx === activeLineIdx && !forceScroll) return;
  activeLineIdx = nextIdx;

  containers.forEach((scrollArea) => {
    const nodes = scrollArea.querySelectorAll('[data-lyric-idx]');
    let activeEl = null;

    nodes.forEach((node) => {
      const idx = Number(node.dataset.lyricIdx);
      const isAct = idx === nextIdx;
      const isPast = nextIdx >= 0 && idx < nextIdx;
      node.classList.toggle('active', isAct);
      node.classList.toggle('past', isPast);
      if (isAct) activeEl = node;
    });

    if (activeEl && canAutoScroll) {
      const targetTop =
        activeEl.offsetTop - scrollArea.clientHeight / 2 + activeEl.clientHeight / 2;
      isProgrammaticScroll = true;
      scrollArea.scrollTo({ top: Math.max(0, targetTop), behavior: 'smooth' });
      setTimeout(() => {
        isProgrammaticScroll = false;
      }, 320);
    }
  });
}

export function ensureLyricsRafLoop() {
  const tick = () => {
    const st = getState();
    const visible = st.drawerMode === 'lyrics' || st.route?.name === 'lyrics';
    if (visible && st.lyricsData?.synced) {
      syncActiveLineDOM(false);
    }
    if (st.isPlaying && visible) {
      rafId = requestAnimationFrame(tick);
    } else {
      rafId = null;
    }
  };

  const st = getState();
  const visible = st.drawerMode === 'lyrics' || st.route?.name === 'lyrics';
  if (st.isPlaying && visible && !rafId) {
    rafId = requestAnimationFrame(tick);
  } else if ((!st.isPlaying || !visible) && rafId) {
    cancelAnimationFrame(rafId);
    rafId = null;
  }
}
