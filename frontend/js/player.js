// HTML5 Audio Engine + Queue / Shuffle / Repeat / Crossfade / Autoplay / EQ / Sleep Timer / MediaSession

import { api } from './api.js';
import {
  applyEqBands,
  isEqGraphAttached,
  isEqNonFlat,
  resumeEqContext,
  unlockAudioContextFromGesture,
} from './audioEffects.js';
import { showToast } from './components/modals.js';
import { coverUrlFor } from './lrc.js';
import { getBlobUrl } from './offlineDownloads.js';
import {
  getState,
  persistPrefs,
  refreshHistory,
  setState,
  updateAudioSettings,
} from './store.js';

let audioEl = null;
let infoController = null;
let playbackSeq = 0;
let lastHistoryTrackId = null;
let lastHistoryTime = 0;
let activeProxyUrl = '';
let activeUsingDirect = false;
let didFallbackToProxy = false;
let activeBlobUrl = '';
let prefetchTimer = null;
let sleepTimerInterval = null;
let crossfadeTriggeredForSeq = -1;
let fadeInRaf = null;
let autoplayFetching = false;
let consecutiveSkips = 0;
let isCrossfadeRampingOut = false;

const _ALT_REJECT_WORDS = [
  'remix', 'cover', 'karaoke', 'instrumental', 'sped up', 'speed up',
  'slowed', 'slower', 'nightcore', 'mashup', 'edit', 'live',
];

function _normSongKey(text) {
  return String(text || '')
    .replace(/[đĐ]/g, 'd')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9\u00c0-\uffff\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function _stripTitleMeta(title) {
  return String(title || '')
    .replace(/\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with|official[^)\]]*|remaster[^)\]]*|mv|audio|video|lyrics?)[\)\]]/gi, '')
    .replace(/\s+(?:feat\.?|ft\.?|featuring)\s+.*$/i, '')
    .trim();
}

function isSameSongCandidate(alt, target) {
  if (!alt || !alt.id || !target || alt.id === target.id) return false;
  const rawAltTitle = _normSongKey(alt.title);
  const rawTargetTitle = _normSongKey(target.title);
  if (!rawAltTitle || !rawTargetTitle) return false;

  const padAlt = ` ${rawAltTitle} `;
  const padTarget = ` ${rawTargetTitle} `;
  for (const w of _ALT_REJECT_WORDS) {
    if (w === 'edit' && padAlt.includes(' radio edit ')) continue;
    if (padAlt.includes(` ${w} `) && !padTarget.includes(` ${w} `)) return false;
  }

  const altToks = _normSongKey(_stripTitleMeta(alt.title)).split(' ').filter(Boolean);
  const targetToks = _normSongKey(_stripTitleMeta(target.title)).split(' ').filter(Boolean);
  if (!altToks.length || !targetToks.length) return false;
  const altSet = new Set(altToks);
  const targetSet = new Set(targetToks);
  let inter = 0;
  for (const tok of targetSet) {
    if (altSet.has(tok)) inter += 1;
  }
  const overlap = inter / Math.max(1, Math.min(altSet.size, targetSet.size));
  if (overlap < 0.8) return false;

  const primTargetArt = _normSongKey(String(target.artist || '').split(/,|&|\bfeat\.?\b|\bft\.?\b/i)[0]);
  const fullAltArt = _normSongKey(alt.artist);
  if (primTargetArt && primTargetArt !== 'unknown artist' && primTargetArt !== 'various artists') {
    if (fullAltArt !== primTargetArt && !` ${fullAltArt} `.includes(` ${primTargetArt} `)) {
      const altArtWords = new Set(fullAltArt.split(' ').filter(Boolean));
      const sharedArt = primTargetArt.split(' ').some((tok) => tok.length >= 3 && altArtWords.has(tok));
      if (!sharedArt) return false;
    }
  }

  const aDur = Number(alt.duration) || 0;
  const tDur = Number(target.duration) || 0;
  if (aDur > 0 && aDur < 60 && tDur >= 60) return false;
  if (aDur > 30 && tDur > 30) {
    if (aDur < tDur - 12 || aDur > tDur + 35) return false;
  }
  return true;
}

const PREFETCH_INFO_TTL_MS = 10 * 60 * 1000;
const PREFETCH_HLS_TTL_MS = 210_000;
const prefetchedInfoCache = new Map();

function streamCacheKey(trackId, quality) {
  return `${trackId}::${quality || 'auto'}`;
}

function getCachedStreamInfo(trackId, quality) {
  const key = streamCacheKey(trackId, quality);
  const entry = prefetchedInfoCache.get(key);
  if (!entry) return null;
  const ttl = entry.info?.hlsUrl ? PREFETCH_HLS_TTL_MS : PREFETCH_INFO_TTL_MS;
  if (Date.now() - entry.fetchedAt > ttl) {
    prefetchedInfoCache.delete(key);
    return null;
  }
  return entry.info;
}

function setCachedStreamInfo(trackId, quality, info) {
  if (!trackId || !info || info.streamMode === 'preview') return;
  const key = streamCacheKey(trackId, quality);
  if (prefetchedInfoCache.size >= 14) {
    const oldestKey = prefetchedInfoCache.keys().next().value;
    if (oldestKey) prefetchedInfoCache.delete(oldestKey);
  }
  prefetchedInfoCache.set(key, { info, fetchedAt: Date.now() });
}

function revokeActiveBlob() {
  if (activeBlobUrl) {
    try {
      URL.revokeObjectURL(activeBlobUrl);
    } catch {
      // Ignore revoke errors
    }
    activeBlobUrl = '';
  }
}

function peekNextTrackForPrefetch() {
  const st = getState();
  if (Array.isArray(st.userQueue) && st.userQueue.length > 0) {
    return st.userQueue[0];
  }
  if (!st.shuffle && Array.isArray(st.queue) && st.queueIndex >= 0 && st.queueIndex + 1 < st.queue.length) {
    return st.queue[st.queueIndex + 1];
  }
  return null;
}

function hasUpcomingTrack(st) {
  if (st.userQueue.length > 0) return true;
  if (st.repeat === 'all' && st.queue.length > 0) return true;
  if (st.shuffle && st.queue.length > 1) return true;
  if (st.queueIndex + 1 < st.queue.length) return true;
  return Boolean(st.autoplay && st.currentTrack);
}

function scheduleNextTrackPrefetch(seq) {
  clearTimeout(prefetchTimer);
  prefetchTimer = setTimeout(() => {
    if (seq !== playbackSeq) return;
    const nextTrack = peekNextTrackForPrefetch();
    const qual = getState().audioQuality || 'auto';
    if (!nextTrack || !nextTrack.id || getCachedStreamInfo(nextTrack.id, qual)) return;
    api
      .getStreamInfo(nextTrack.id, undefined, qual, true)
      .then((info) => {
        if (info) setCachedStreamInfo(nextTrack.id, qual, info);
      })
      .catch(() => {});
  }, 2000);
}

function attemptProxyFallback(seq) {
  if (!audioEl || !activeUsingDirect || didFallbackToProxy || !activeProxyUrl || seq !== playbackSeq) {
    return false;
  }
  didFallbackToProxy = true;
  activeUsingDirect = false;
  setState({ isLoading: true });
  audioEl.src = activeProxyUrl;
  audioEl.currentTime = 0;
  syncAudioSettings();
  audioEl.play().catch(() => {});
  const curTrack = getState().currentTrack;
  if (curTrack?.id) {
    api
      .getStreamInfo(curTrack.id, undefined, getState().audioQuality || 'auto', false)
      .then((fresh) => {
        if (seq === playbackSeq && fresh?.streamSource) {
          setState({
            streamMode: fresh.streamMode || 'full',
            streamSource: fresh.streamSource,
          });
        }
      })
      .catch(() => {});
  }
  return true;
}

function updateMediaSession(track) {
  if (!('mediaSession' in navigator) || !track) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: track.title || 'Unknown Track',
      artist: track.artist || 'Unknown Artist',
      album: track.album || 'Sonify',
      artwork: [
        { src: coverUrlFor(track, false), sizes: '256x256', type: 'image/jpeg' },
        { src: coverUrlFor(track, true), sizes: '512x512', type: 'image/jpeg' },
      ],
    });
  } catch {
    // Ignore unsupported MediaMetadata environments
  }
}

function recordPlay(track) {
  if (!track || !track.id) return;
  const now = Date.now();
  if (lastHistoryTrackId === track.id && now - lastHistoryTime < 15000) return;
  lastHistoryTrackId = track.id;
  lastHistoryTime = now;

  api.recordHistory(track)
    .then(() => refreshHistory())
    .catch(() => {});
  persistPrefs({ lastTrackId: track.id, lastPosition: 0 });
}

function cancelFadeIn() {
  if (fadeInRaf) {
    cancelAnimationFrame(fadeInRaf);
    fadeInRaf = null;
  }
}

function startFadeIn(durationSec) {
  cancelFadeIn();
  if (!audioEl || durationSec <= 0) return;
  const st = getState();
  if (st.muted) return;
  const targetVol = Math.max(0, Math.min(1, Number(st.volume) || 0.8));
  const startTs = performance.now();
  const durMs = Math.min(durationSec, 3) * 1000;
  audioEl.volume = Math.min(targetVol, 0.08);
  const step = (now) => {
    const cur = getState();
    if (!audioEl || cur.muted) {
      fadeInRaf = null;
      return;
    }
    const progress = Math.min(1, (now - startTs) / durMs);
    const liveTarget = Math.max(0, Math.min(1, Number(cur.volume) || 0));
    audioEl.volume = liveTarget * (0.08 + 0.92 * progress);
    if (progress < 1) {
      fadeInRaf = requestAnimationFrame(step);
    } else {
      audioEl.volume = liveTarget;
      fadeInRaf = null;
    }
  };
  fadeInRaf = requestAnimationFrame(step);
}

export function getAudioElement() {
  return audioEl;
}

export function syncAudioSettings() {
  if (!audioEl) return;
  const st = getState();
  audioEl.muted = Boolean(st.muted);
  audioEl.volume = st.muted ? 0 : Math.max(0, Math.min(1, Number(st.volume) || 0));
  const rate = Math.max(0.5, Math.min(2.0, Number(st.playbackRate) || 1.0));
  if (Math.abs((audioEl.playbackRate || 1) - rate) > 0.001) {
    try {
      audioEl.playbackRate = rate;
    } catch {
      // Ignore unsupported rate
    }
  }
  if (isEqNonFlat(st.eqBands) || isEqGraphAttached()) {
    applyEqBands(audioEl, st.eqBands);
  }
}

export function setPlaybackRate(rate) {
  const clamped = Math.max(0.5, Math.min(2.0, Math.round(Number(rate) * 100) / 100));
  updateAudioSettings({ playbackRate: clamped });
  if (audioEl) {
    try {
      audioEl.playbackRate = clamped;
    } catch {
      // Ignore
    }
  }
}

export function applyEqSettings(bands, preset = 'custom') {
  const normalized = Array.isArray(bands) && bands.length === 5
    ? bands.map((v) => Math.max(-12, Math.min(12, Math.round(Number(v) || 0))))
    : [0, 0, 0, 0, 0];
  updateAudioSettings({ eqBands: normalized, eqPreset: preset });
  if (!audioEl) return;

  // If EQ is becoming non-flat while streaming from a cross-origin direct CDN URL,
  // switch audioEl.src to same-origin proxy BEFORE attaching createMediaElementSource so audio is never CORS-muted.
  if (isEqNonFlat(normalized) && !isEqGraphAttached() && activeUsingDirect && activeProxyUrl) {
    const resumeTime = audioEl.currentTime || 0;
    const wasPlaying = !audioEl.paused;
    activeUsingDirect = false;
    didFallbackToProxy = true;
    audioEl.src = activeProxyUrl;
    unlockAudioContextFromGesture(audioEl);
    applyEqBands(audioEl, normalized);
    resumeEqContext();
    audioEl.addEventListener(
      'loadedmetadata',
      () => {
        try {
          if (resumeTime > 0 && Number.isFinite(audioEl.duration) && resumeTime < audioEl.duration - 1) {
            audioEl.currentTime = resumeTime;
          }
        } catch {
          // Ignore seek error
        }
        if (wasPlaying) audioEl.play().catch(() => {});
      },
      { once: true }
    );
    return;
  }

  unlockAudioContextFromGesture(audioEl);
  applyEqBands(audioEl, normalized);
  resumeEqContext();
}

export function initPlayer() {
  audioEl = document.getElementById('audio-engine');
  const st = getState();
  audioEl.volume = st.muted ? 0 : st.volume;
  audioEl.muted = st.muted;
  try {
    audioEl.playbackRate = st.playbackRate || 1.0;
  } catch {
    // Ignore
  }

  const onUserGestureUnlock = () => {
    if (isEqGraphAttached() || isEqNonFlat(getState().eqBands)) {
      unlockAudioContextFromGesture(audioEl);
    } else {
      resumeEqContext();
    }
  };
  document.addEventListener('pointerdown', onUserGestureUnlock, { capture: true, passive: true });
  document.addEventListener('touchend', onUserGestureUnlock, { capture: true, passive: true });
  document.addEventListener('keydown', onUserGestureUnlock, { capture: true, passive: true });

  audioEl.addEventListener('play', () => {
    resumeEqContext();
    setState({ isPlaying: true, isLoading: false });
    if ('mediaSession' in navigator) {
      navigator.mediaSession.playbackState = 'playing';
    }
  });

  audioEl.addEventListener('playing', () => {
    consecutiveSkips = 0;
    scheduleNextTrackPrefetch(playbackSeq);
  });

  audioEl.addEventListener('pause', () => {
    setState({ isPlaying: false });
    if ('mediaSession' in navigator) {
      navigator.mediaSession.playbackState = 'paused';
    }
  });

  audioEl.addEventListener('waiting', () => {
    setState({ isLoading: true });
  });

  audioEl.addEventListener('canplay', () => {
    setState({ isLoading: false });
  });

  audioEl.addEventListener('loadedmetadata', () => {
    const cur = getState();
    const dur = Number.isFinite(audioEl.duration) ? audioEl.duration : cur.currentTrack?.duration || 0;
    try {
      audioEl.playbackRate = cur.playbackRate || 1.0;
    } catch {
      // Ignore
    }
    setState({ duration: dur });
  });

  audioEl.addEventListener('durationchange', () => {
    if (Number.isFinite(audioEl.duration) && audioEl.duration > 0) {
      setState({ duration: audioEl.duration });
    }
  });

  audioEl.addEventListener('timeupdate', () => {
    const cur = getState();
    const curTime = audioEl.currentTime || 0;
    const dur = Number.isFinite(audioEl.duration) ? audioEl.duration : cur.duration || 0;
    let buffered = 0;
    if (audioEl.buffered.length > 0 && dur > 0) {
      buffered = audioEl.buffered.end(audioEl.buffered.length - 1);
    }
    setState({
      currentTime: curTime,
      buffered,
    });

    // Crossfade smooth ramp-out when approaching end of track
    const cf = Number(cur.crossfade) || 0;
    let cfActive = false;
    if (
      cf > 0 &&
      !audioEl.paused &&
      cur.repeat !== 'one' &&
      cur.sleepTimer?.mode !== 'end-of-track' &&
      dur > 15 &&
      hasUpcomingTrack(cur)
    ) {
      const fadeSec = Math.min(cf, 6);
      const rem = dur - curTime;
      if (rem > 0 && rem <= fadeSec && !cur.muted) {
        cfActive = true;
        isCrossfadeRampingOut = true;
        cancelFadeIn();
        const baseVol = Math.max(0, Math.min(1, Number(cur.volume) || 0));
        audioEl.volume = Math.max(0.05, baseVol * (rem / fadeSec));
        if (rem <= 0.45 && crossfadeTriggeredForSeq !== playbackSeq) {
          crossfadeTriggeredForSeq = playbackSeq;
          playNextTrack(true, true);
        }
      }
    }
    if (!cfActive && isCrossfadeRampingOut) {
      isCrossfadeRampingOut = false;
      audioEl.volume = cur.muted ? 0 : Math.max(0, Math.min(1, Number(cur.volume) || 0));
    }
  });

  audioEl.addEventListener('progress', () => {
    if (audioEl.buffered.length > 0) {
      setState({ buffered: audioEl.buffered.end(audioEl.buffered.length - 1) });
    }
  });

  audioEl.addEventListener('ended', () => {
    const cur = getState();
    if (cur.sleepTimer?.mode === 'end-of-track') {
      cancelSleepTimer(false);
      if (audioEl) audioEl.pause();
      setState({ isPlaying: false });
      showToast('Sleep timer paused playback at end of track', 'info');
      return;
    }
    if (cur.repeat === 'one' && cur.currentTrack) {
      isCrossfadeRampingOut = false;
      if (!fadeInRaf) {
        audioEl.volume = cur.muted ? 0 : Math.max(0, Math.min(1, Number(cur.volume) || 0));
      }
      audioEl.currentTime = 0;
      audioEl.play().catch(() => {});
      return;
    }
    playNextTrack(true);
  });

  audioEl.addEventListener('error', () => {
    const src = audioEl.getAttribute('src') || '';
    if (!src) return;
    const isCurrentProxy = src.includes(`?s=${playbackSeq}`);
    if (!isCurrentProxy && !activeUsingDirect && !activeBlobUrl) return;
    if (attemptProxyFallback(playbackSeq)) return;
    const cur = getState().currentTrack;
    if (!cur) return;
    setState({ isLoading: false, isPlaying: false, streamMode: 'error', streamSource: '' });
    showToast(
      `Could not play "${cur.title}" — try another result or check connection`,
      'error',
      3600
    );
  });

  if ('mediaSession' in navigator) {
    try {
      navigator.mediaSession.setActionHandler('play', () => togglePlay(true));
      navigator.mediaSession.setActionHandler('pause', () => togglePlay(false));
      navigator.mediaSession.setActionHandler('previoustrack', () => playPrevTrack());
      navigator.mediaSession.setActionHandler('nexttrack', () => playNextTrack(false));
      navigator.mediaSession.setActionHandler('seekto', (details) => {
        if (typeof details.seekTime === 'number') seekTo(details.seekTime);
      });
    } catch {
      // Ignore partial MediaSession support
    }
  }
}

async function startTrackPlayback(track, withFadeIn = false) {
  if (!track || !track.id || !audioEl) return;

  const seq = ++playbackSeq;
  clearTimeout(prefetchTimer);
  cancelFadeIn();
  isCrossfadeRampingOut = false;
  revokeActiveBlob();
  activeProxyUrl = '';
  activeUsingDirect = false;
  didFallbackToProxy = false;
  if (infoController) infoController.abort();
  infoController = new AbortController();
  const signal = infoController.signal;

  audioEl.pause();
  audioEl.removeAttribute('src');
  audioEl.load();

  setState({
    currentTrack: track,
    isLoading: true,
    isPlaying: false,
    currentTime: 0,
    duration: Number(track.duration) || 0,
    buffered: 0,
    streamMode: null,
    streamSource: '',
  });

  updateMediaSession(track);

  // 1. Check Offline Downloads Cache API first for instant offline playback
  try {
    const offlineUrl = await getBlobUrl(track.id);
    if (seq !== playbackSeq || signal.aborted) {
      if (offlineUrl) URL.revokeObjectURL(offlineUrl);
      return;
    }
    if (offlineUrl) {
      activeBlobUrl = offlineUrl;
      setState({
        streamMode: 'offline-download',
        streamSource: 'offline-cache',
        isLoading: true,
      });
      audioEl.src = offlineUrl;
      audioEl.currentTime = 0;
      syncAudioSettings();
      if (withFadeIn && getState().crossfade > 0) {
        startFadeIn(getState().crossfade);
      }
      recordPlay(track);
      try {
        await audioEl.play();
      } catch (err) {
        if (err.name === 'AbortError' || seq !== playbackSeq) return;
        if (err.name === 'NotAllowedError') {
          setState({ isPlaying: false, isLoading: false });
        }
      }
      return;
    }
  } catch {
    // Fall through to online streaming
  }

  // 2. Resolve online stream metadata (respecting audioQuality preference)
  const st = getState();
  const quality = st.audioQuality || 'auto';
  let streamTrackId = track.id;
  let info = getCachedStreamInfo(track.id, quality);
  if (!info) {
    try {
      info = await api.getStreamInfo(track.id, signal, quality);
      if (info) setCachedStreamInfo(track.id, quality, info);
    } catch (err) {
      if (err.name === 'AbortError' || seq !== playbackSeq) return;

      // 2a. 1-Shot Alternate Track Rescue: search for another catalog version of the same song
      if (track.title) {
        try {
          const rescueQuery = `${track.title} ${track.artist || ''}`.trim();
          const searchRes = await api.search(rescueQuery, 'tracks', 5, signal);
          if (seq !== playbackSeq || signal.aborted) return;
          const alt = (searchRes?.tracks || []).find((t) => isSameSongCandidate(t, track));
          if (alt) {
            const altInfo = await api.getStreamInfo(alt.id, signal, quality);
            if (altInfo) {
              info = altInfo;
              streamTrackId = alt.id;
              setCachedStreamInfo(alt.id, quality, altInfo);
            }
          }
        } catch (rescueErr) {
          if (rescueErr?.name === 'AbortError' || seq !== playbackSeq) return;
        }
      }

      // 2b. Smart Queue Auto-Skip (Spotify behavior, capped at 3 consecutive skips)
      if (!info) {
        if (seq !== playbackSeq || signal.aborted) return;
        const curSt = getState();
        const inMultiQueue =
          (Array.isArray(curSt.queue) && curSt.queue.length > 1) ||
          (Array.isArray(curSt.userQueue) && curSt.userQueue.length > 0);
        if (inMultiQueue && consecutiveSkips < 3) {
          consecutiveSkips += 1;
          showToast(
            `Full track unavailable for '${track.title}' — skipping to next`,
            'info',
            2800
          );
          playNextTrack(true);
          return;
        }
        consecutiveSkips = 0;
        setState({
          isPlaying: false,
          isLoading: false,
          streamMode: 'error',
          streamSource: '',
        });
        showToast(
          err.message || `Could not stream "${track.title}" right now — try another result`,
          'error',
          3600
        );
        return;
      }
    }
  }

  if (seq !== playbackSeq || signal.aborted || getState().currentTrack?.id !== track.id) {
    return;
  }

  setState({
    streamMode: info?.streamMode || 'full',
    streamSource: info?.streamSource || '',
    streamContentType: info?.contentType || '',
    isLoading: true,
  });

  const qualParam = quality && quality !== 'auto' ? `&quality=${encodeURIComponent(quality)}` : '';
  const proxyUrl = `/api/stream/${encodeURIComponent(streamTrackId)}?s=${seq}${qualParam}`;
  activeProxyUrl = proxyUrl;

  // Prefer IP-agnostic edge CDN unless Web Audio EQ is attached/non-flat (same-origin proxy required for CORS-safe Web Audio)
  // Restrict raw .m3u8 hlsUrl to genuine Apple WebKit/Safari (Desktop Chrome/Edge/Firefox use seekable /api/stream proxy!)
  const forceProxyForEq = isEqGraphAttached() || isEqNonFlat(getState().eqBands);
  let chosenUrl = proxyUrl;
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent || '' : '';
  const isAppleNativeHls =
    Boolean(audioEl.canPlayType && audioEl.canPlayType('application/vnd.apple.mpegurl')) &&
    (/iPad|iPhone|iPod/.test(ua) || (/Safari/i.test(ua) && !/Chrome|Chromium|CriOS|Edg|OPR|Firefox/i.test(ua)));
  if (!forceProxyForEq) {
    if (info?.hlsUrl && isAppleNativeHls) {
      chosenUrl = info.hlsUrl;
      activeUsingDirect = true;
    } else if (info?.directUrl) {
      chosenUrl = info.directUrl;
      activeUsingDirect = true;
    }
  }

  audioEl.src = chosenUrl;
  audioEl.currentTime = 0;
  syncAudioSettings();
  if (withFadeIn && getState().crossfade > 0) {
    startFadeIn(getState().crossfade);
  }
  recordPlay(track);

  try {
    await audioEl.play();
  } catch (err) {
    if (err.name === 'AbortError' || seq !== playbackSeq) return;
    if (err.name === 'NotAllowedError') {
      setState({ isPlaying: false, isLoading: false });
      return;
    }
    if (attemptProxyFallback(seq)) return;
    setState({
      isPlaying: false,
      isLoading: false,
      streamMode: 'error',
      streamSource: '',
    });
    showToast(
      `Could not play "${track.title}" — try another result or check connection`,
      'error',
      3600
    );
  }
}

export function playTrack(track, contextList = null, contextIndex = -1) {
  if (!track || !track.id) return;
  unlockAudioContextFromGesture(audioEl);
  const st = getState();
  let queue = st.queue;
  let qIndex = st.queueIndex;

  if (Array.isArray(contextList) && contextList.length > 0) {
    queue = contextList.slice();
    qIndex = contextIndex >= 0 ? contextIndex : queue.findIndex((t) => t.id === track.id);
    if (qIndex < 0) {
      queue.unshift(track);
      qIndex = 0;
    }
  } else {
    const existingIdx = queue.findIndex((t) => t.id === track.id);
    if (existingIdx >= 0) {
      qIndex = existingIdx;
    } else {
      queue = [track];
      qIndex = 0;
    }
  }

  consecutiveSkips = 0;
  setState({ queue, queueIndex: qIndex });
  startTrackPlayback(track);
}

export function playList(tracks, startIndex = 0, shuffleStart = false) {
  if (!Array.isArray(tracks) || tracks.length === 0) return;
  unlockAudioContextFromGesture(audioEl);
  consecutiveSkips = 0;
  const list = tracks.slice();
  let idx = Math.max(0, Math.min(startIndex, list.length - 1));
  if (shuffleStart) {
    setState({ shuffle: true });
    persistPrefs({ shuffle: true });
    idx = Math.floor(Math.random() * list.length);
  }
  setState({ queue: list, queueIndex: idx });
  startTrackPlayback(list[idx]);
}

export function togglePlay(forcePlay = null) {
  if (!audioEl) return;
  unlockAudioContextFromGesture(audioEl);
  const st = getState();
  if (!st.currentTrack) {
    const firstSection = st.homeData?.sections?.find((s) => s.type === 'tracks' && s.items?.length);
    if (firstSection) {
      playList(firstSection.items, 0);
    }
    return;
  }

  const shouldPlay = forcePlay === null ? audioEl.paused : Boolean(forcePlay);
  if (shouldPlay) {
    resumeEqContext();
    if (st.streamMode === 'error' || !audioEl.getAttribute('src')) {
      startTrackPlayback(st.currentTrack);
      return;
    }
    audioEl.play().catch(() => {});
  } else {
    audioEl.pause();
  }
}

async function triggerAutoplayRadio(curTrack, withFadeIn = false) {
  if (!curTrack || !curTrack.id || autoplayFetching) return;
  autoplayFetching = true;
  try {
    const res = await api.getRadio({ trackId: curTrack.id, limit: 15 });
    const st = getState();
    const existingIds = new Set(st.queue.map((t) => t.id));
    existingIds.add(curTrack.id);
    const fresh = (res?.tracks || []).filter((t) => t && t.id && !existingIds.has(t.id));
    if (fresh.length === 0) {
      setState({ isPlaying: false });
      return;
    }
    const nextQueue = [...st.queue, ...fresh];
    const nextIdx = st.queue.length;
    setState({ queue: nextQueue, queueIndex: nextIdx });
    showToast(`Autoplay queued ${fresh.length} similar songs`, 'info', 2600);
    startTrackPlayback(nextQueue[nextIdx], withFadeIn);
  } catch {
    setState({ isPlaying: false });
  } finally {
    autoplayFetching = false;
  }
}

export function playNextTrack(fromEnded = false, withFadeIn = false) {
  if (!fromEnded) unlockAudioContextFromGesture(audioEl);
  const st = getState();

  // 1. Always consume explicit userQueue first
  if (st.userQueue.length > 0) {
    const [nextTrack, ...rest] = st.userQueue;
    setState({ userQueue: rest });
    startTrackPlayback(nextTrack, withFadeIn);
    return;
  }

  const len = st.queue.length;
  if (len === 0) {
    if (st.autoplay && st.currentTrack) {
      triggerAutoplayRadio(st.currentTrack, withFadeIn);
      return;
    }
    if (audioEl) audioEl.pause();
    return;
  }

  let nextIndex = -1;
  if (st.shuffle && len > 1) {
    do {
      nextIndex = Math.floor(Math.random() * len);
    } while (nextIndex === st.queueIndex);
  } else if (st.queueIndex + 1 < len) {
    nextIndex = st.queueIndex + 1;
  } else if (st.repeat === 'all' || !fromEnded) {
    nextIndex = 0;
  } else if (st.autoplay && st.currentTrack) {
    triggerAutoplayRadio(st.currentTrack, withFadeIn);
    return;
  } else {
    setState({ isPlaying: false });
    return;
  }

  setState({ queueIndex: nextIndex });
  startTrackPlayback(st.queue[nextIndex], withFadeIn);
}

export function playPrevTrack() {
  if (!audioEl) return;
  unlockAudioContextFromGesture(audioEl);
  const st = getState();
  if (!st.currentTrack) return;

  if (audioEl.currentTime > 3) {
    isCrossfadeRampingOut = false;
    if (!fadeInRaf) {
      audioEl.volume = st.muted ? 0 : Math.max(0, Math.min(1, Number(st.volume) || 0));
    }
    audioEl.currentTime = 0;
    setState({ currentTime: 0 });
    return;
  }

  const len = st.queue.length;
  if (len === 0) {
    audioEl.currentTime = 0;
    return;
  }
  const prevIndex = st.queueIndex > 0 ? st.queueIndex - 1 : len - 1;
  setState({ queueIndex: prevIndex });
  startTrackPlayback(st.queue[prevIndex]);
}

export function seekTo(seconds, autoPlay = false) {
  if (!audioEl) return;
  const st = getState();
  const dur = Number.isFinite(audioEl.duration) ? audioEl.duration : st.duration || 0;
  const clamped = Math.max(0, dur > 0 ? Math.min(seconds, dur) : seconds);
  isCrossfadeRampingOut = false;
  if (!fadeInRaf) {
    audioEl.volume = st.muted ? 0 : Math.max(0, Math.min(1, Number(st.volume) || 0));
  }
  audioEl.currentTime = clamped;
  setState({ currentTime: clamped });
  if (autoPlay && audioEl.paused) {
    unlockAudioContextFromGesture(audioEl);
    audioEl.play().catch(() => {});
  }
}

export function seekBy(deltaSec) {
  if (!audioEl || !getState().currentTrack) return;
  seekTo((audioEl.currentTime || 0) + deltaSec);
}

export function setVolume(vol) {
  const clamped = Math.max(0, Math.min(1, Number(vol)));
  const muted = clamped === 0;
  cancelFadeIn();
  if (audioEl) {
    audioEl.volume = clamped;
    audioEl.muted = muted;
  }
  setState({ volume: clamped, muted });
  persistPrefs({ volume: clamped, muted });
}

export function toggleMute() {
  const st = getState();
  const nextMuted = !st.muted;
  const nextVol = !nextMuted && st.volume === 0 ? 0.6 : st.volume;
  cancelFadeIn();
  if (audioEl) {
    audioEl.muted = nextMuted;
    audioEl.volume = nextMuted ? 0 : nextVol;
  }
  setState({ muted: nextMuted, volume: nextVol });
  persistPrefs({ muted: nextMuted, volume: nextVol });
}

export function toggleShuffle() {
  const next = !getState().shuffle;
  setState({ shuffle: next });
  persistPrefs({ shuffle: next });
}

export function cycleRepeat() {
  const cur = getState().repeat;
  const next = cur === 'off' ? 'all' : cur === 'all' ? 'one' : 'off';
  setState({ repeat: next });
  persistPrefs({ repeat: next });
}

export function enqueueNext(track) {
  if (!track || !track.id) return;
  const st = getState();
  setState({ userQueue: [track, ...st.userQueue] });
}

export function enqueueLater(track) {
  if (!track || !track.id) return;
  const st = getState();
  setState({ userQueue: [...st.userQueue, track] });
}

export function removeFromUserQueue(index) {
  const st = getState();
  if (index < 0 || index >= st.userQueue.length) return;
  const next = st.userQueue.slice();
  next.splice(index, 1);
  setState({ userQueue: next });
}

export function moveUserQueueItem(index, delta) {
  const st = getState();
  const target = index + delta;
  if (index < 0 || target < 0 || target >= st.userQueue.length) return;
  const next = st.userQueue.slice();
  const [item] = next.splice(index, 1);
  next.splice(target, 0, item);
  setState({ userQueue: next });
}

export function reorderUserQueue(fromIdx, toIdx) {
  const st = getState();
  if (
    fromIdx === toIdx ||
    fromIdx < 0 ||
    toIdx < 0 ||
    fromIdx >= st.userQueue.length ||
    toIdx >= st.userQueue.length
  ) {
    return;
  }
  const next = st.userQueue.slice();
  const [moved] = next.splice(fromIdx, 1);
  next.splice(toIdx, 0, moved);
  setState({ userQueue: next });
}

export function clearUserQueue() {
  setState({ userQueue: [] });
}

export function clearAllQueue() {
  const st = getState();
  const cur = st.currentTrack;
  setState({
    userQueue: [],
    queue: cur ? [cur] : [],
    queueIndex: cur ? 0 : -1,
  });
}

export function jumpToQueueIndex(index) {
  const st = getState();
  if (index < 0 || index >= st.queue.length) return;
  unlockAudioContextFromGesture(audioEl);
  setState({ queueIndex: index });
  startTrackPlayback(st.queue[index]);
}

export function playFromUserQueue(index) {
  const st = getState();
  if (index < 0 || index >= st.userQueue.length) return;
  unlockAudioContextFromGesture(audioEl);
  const chosen = st.userQueue[index];
  const rest = st.userQueue.slice(index + 1);
  setState({ userQueue: rest });
  startTrackPlayback(chosen);
}

// ── Song Radio & Artist Radio ──────────────────────────────────────────────

export async function startSongRadio(track) {
  if (!track || !track.id) return;
  showToast(`Starting radio based on "${track.title}"...`, 'info', 2200);
  try {
    const res = await api.getRadio({ trackId: track.id, limit: 20 });
    const tracks = res?.tracks || [];
    if (tracks.length === 0) {
      showToast('Could not build radio station right now', 'error');
      return;
    }
    const list = [track, ...tracks.filter((t) => t.id !== track.id)];
    setState({ queue: list, queueIndex: 0 });
    startTrackPlayback(list[0]);
    showToast(`${track.title} Radio — ${list.length} tracks queued`, 'success');
  } catch (err) {
    showToast(err.message || 'Could not start song radio', 'error');
  }
}

export async function startArtistRadio(artistId, artistName = '') {
  if (!artistId) return;
  showToast(`Starting ${artistName || 'Artist'} Radio...`, 'info', 2200);
  try {
    const res = await api.getRadio({ artistId, limit: 20 });
    const tracks = res?.tracks || [];
    if (tracks.length === 0) {
      showToast('Could not build artist radio right now', 'error');
      return;
    }
    playList(tracks, 0);
    showToast(`${artistName || 'Artist'} Radio — ${tracks.length} tracks queued`, 'success');
  } catch (err) {
    showToast(err.message || 'Could not start artist radio', 'error');
  }
}

// ── Sleep Timer ────────────────────────────────────────────────────────────

export function cancelSleepTimer(notify = true) {
  if (sleepTimerInterval) {
    clearInterval(sleepTimerInterval);
    sleepTimerInterval = null;
  }
  setState({ sleepTimer: { mode: 'off', endsAt: null, remainingSec: 0 } });
  if (notify) showToast('Sleep timer turned off', 'info', 1800);
}

export function setSleepTimerEndOfTrack() {
  if (sleepTimerInterval) {
    clearInterval(sleepTimerInterval);
    sleepTimerInterval = null;
  }
  setState({ sleepTimer: { mode: 'end-of-track', endsAt: null, remainingSec: 0 } });
  showToast('Sleep timer set: End of current track', 'success', 2200);
}

export function setSleepTimerMinutes(minutes) {
  const mins = Math.max(1, Math.min(240, Number(minutes) || 15));
  if (sleepTimerInterval) {
    clearInterval(sleepTimerInterval);
    sleepTimerInterval = null;
  }
  const endsAt = Date.now() + mins * 60 * 1000;
  setState({ sleepTimer: { mode: 'countdown', endsAt, remainingSec: mins * 60 } });
  showToast(`Sleep timer set for ${mins} min`, 'success', 2200);

  sleepTimerInterval = setInterval(() => {
    const st = getState();
    if (st.sleepTimer?.mode !== 'countdown' || !st.sleepTimer.endsAt) {
      clearInterval(sleepTimerInterval);
      sleepTimerInterval = null;
      return;
    }
    const rem = Math.max(0, Math.ceil((st.sleepTimer.endsAt - Date.now()) / 1000));
    if (rem <= 0) {
      clearInterval(sleepTimerInterval);
      sleepTimerInterval = null;
      setState({ sleepTimer: { mode: 'off', endsAt: null, remainingSec: 0 } });
      if (audioEl && !audioEl.paused) {
        audioEl.pause();
      }
      showToast('Sleep timer finished — playback paused', 'info', 3000);
    } else {
      setState({ sleepTimer: { mode: 'countdown', endsAt: st.sleepTimer.endsAt, remainingSec: rem } });
    }
  }, 1000);
}
