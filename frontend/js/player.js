// HTML5 Audio Engine + Queue / Shuffle / Repeat / MediaSession Controller

import { api } from './api.js';
import { showToast } from './components/modals.js';
import { coverUrlFor } from './lrc.js';
import { getState, persistPrefs, refreshHistory, setState } from './store.js';

let audioEl = null;
let infoController = null;
let playbackSeq = 0;
let lastHistoryTrackId = null;
let lastHistoryTime = 0;
let activeProxyUrl = '';
let activeUsingDirect = false;
let didFallbackToProxy = false;
let prefetchTimer = null;
const PREFETCH_INFO_TTL_MS = 10 * 60 * 1000;
const prefetchedInfoCache = new Map();

function getCachedStreamInfo(trackId) {
  const entry = prefetchedInfoCache.get(trackId);
  if (!entry) return null;
  if (Date.now() - entry.fetchedAt > PREFETCH_INFO_TTL_MS) {
    prefetchedInfoCache.delete(trackId);
    return null;
  }
  return entry.info;
}

function setCachedStreamInfo(trackId, info) {
  if (!trackId || !info || info.streamMode === 'preview') return;
  if (prefetchedInfoCache.size >= 12) {
    const oldestKey = prefetchedInfoCache.keys().next().value;
    if (oldestKey) prefetchedInfoCache.delete(oldestKey);
  }
  prefetchedInfoCache.set(trackId, { info, fetchedAt: Date.now() });
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

function scheduleNextTrackPrefetch(seq) {
  clearTimeout(prefetchTimer);
  prefetchTimer = setTimeout(() => {
    if (seq !== playbackSeq) return;
    const nextTrack = peekNextTrackForPrefetch();
    if (!nextTrack || !nextTrack.id || getCachedStreamInfo(nextTrack.id)) return;
    api
      .getStreamInfo(nextTrack.id)
      .then((info) => {
        if (info) setCachedStreamInfo(nextTrack.id, info);
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

export function getAudioElement() {
  return audioEl;
}

export function syncAudioSettings() {
  if (!audioEl) return;
  const st = getState();
  audioEl.muted = Boolean(st.muted);
  audioEl.volume = st.muted ? 0 : Math.max(0, Math.min(1, Number(st.volume) || 0));
}

export function initPlayer() {
  audioEl = document.getElementById('audio-engine');
  const st = getState();
  audioEl.volume = st.muted ? 0 : st.volume;
  audioEl.muted = st.muted;

  audioEl.addEventListener('play', () => {
    setState({ isPlaying: true, isLoading: false });
    if ('mediaSession' in navigator) {
      navigator.mediaSession.playbackState = 'playing';
    }
  });

  audioEl.addEventListener('playing', () => {
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
    const dur = Number.isFinite(audioEl.duration) ? audioEl.duration : getState().currentTrack?.duration || 0;
    setState({ duration: dur });
  });

  audioEl.addEventListener('durationchange', () => {
    if (Number.isFinite(audioEl.duration) && audioEl.duration > 0) {
      setState({ duration: audioEl.duration });
    }
  });

  audioEl.addEventListener('timeupdate', () => {
    let buffered = 0;
    if (audioEl.buffered.length > 0 && audioEl.duration > 0) {
      buffered = audioEl.buffered.end(audioEl.buffered.length - 1);
    }
    setState({
      currentTime: audioEl.currentTime || 0,
      buffered,
    });
  });

  audioEl.addEventListener('progress', () => {
    if (audioEl.buffered.length > 0) {
      setState({ buffered: audioEl.buffered.end(audioEl.buffered.length - 1) });
    }
  });

  audioEl.addEventListener('ended', () => {
    const cur = getState();
    if (cur.repeat === 'one' && cur.currentTrack) {
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
    if (!isCurrentProxy && !activeUsingDirect) return;
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

async function startTrackPlayback(track) {
  if (!track || !track.id || !audioEl) return;

  const seq = ++playbackSeq;
  clearTimeout(prefetchTimer);
  activeProxyUrl = '';
  activeUsingDirect = false;
  didFallbackToProxy = false;
  if (infoController) infoController.abort();
  infoController = new AbortController();
  const signal = infoController.signal;

  // Stop any previous track immediately so the old song never keeps playing while resolving
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

  // Resolve and verify stream metadata FIRST (or use instant prefetched info cache)
  let info = getCachedStreamInfo(track.id);
  if (!info) {
    try {
      info = await api.getStreamInfo(track.id, signal);
      if (info) setCachedStreamInfo(track.id, info);
    } catch (err) {
      if (err.name === 'AbortError' || seq !== playbackSeq) return;
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

  if (seq !== playbackSeq || signal.aborted || getState().currentTrack?.id !== track.id) {
    return;
  }

  setState({
    streamMode: info?.streamMode || 'full',
    streamSource: info?.streamSource || '',
    isLoading: true,
  });

  const proxyUrl = `/api/stream/${encodeURIComponent(track.id)}?s=${seq}`;
  activeProxyUrl = proxyUrl;

  // Prefer IP-agnostic edge CDN (Safari native HLS or direct JioSaavn/Audius/SC progressive)
  // with automatic 1-shot silent fallback to our backend proxy on error.
  let chosenUrl = proxyUrl;
  const canPlayNativeHls = Boolean(audioEl.canPlayType && audioEl.canPlayType('application/vnd.apple.mpegurl'));
  if (info?.hlsUrl && canPlayNativeHls) {
    chosenUrl = info.hlsUrl;
    activeUsingDirect = true;
  } else if (info?.directUrl) {
    chosenUrl = info.directUrl;
    activeUsingDirect = true;
  }

  audioEl.src = chosenUrl;
  audioEl.currentTime = 0;
  syncAudioSettings();
  recordPlay(track);

  try {
    await audioEl.play();
  } catch (err) {
    if (err.name === 'AbortError' || seq !== playbackSeq) return;
    if (err.name === 'NotAllowedError') {
      // Autoplay blocked by browser policy; keep resolved stream ready for manual Play click
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

  setState({ queue, queueIndex: qIndex });
  startTrackPlayback(track);
}

export function playList(tracks, startIndex = 0, shuffleStart = false) {
  if (!Array.isArray(tracks) || tracks.length === 0) return;
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
  const st = getState();
  if (!st.currentTrack) {
    // Auto-start first home track if user presses Space before picking a song
    const firstSection = st.homeData?.sections?.find((s) => s.type === 'tracks' && s.items?.length);
    if (firstSection) {
      playList(firstSection.items, 0);
    }
    return;
  }

  const shouldPlay = forcePlay === null ? audioEl.paused : Boolean(forcePlay);
  if (shouldPlay) {
    if (st.streamMode === 'error' || !audioEl.getAttribute('src')) {
      startTrackPlayback(st.currentTrack);
      return;
    }
    audioEl.play().catch(() => {});
  } else {
    audioEl.pause();
  }
}

export function playNextTrack(fromEnded = false) {
  const st = getState();

  // 1. Always consume explicit userQueue first
  if (st.userQueue.length > 0) {
    const [nextTrack, ...rest] = st.userQueue;
    setState({ userQueue: rest });
    startTrackPlayback(nextTrack);
    return;
  }

  const len = st.queue.length;
  if (len === 0) {
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
  } else {
    // End of queue with repeat off
    setState({ isPlaying: false });
    return;
  }

  setState({ queueIndex: nextIndex });
  startTrackPlayback(st.queue[nextIndex]);
}

export function playPrevTrack() {
  if (!audioEl) return;
  const st = getState();
  if (!st.currentTrack) return;

  // If > 3 seconds into track, restart current track (standard Spotify behavior)
  if (audioEl.currentTime > 3) {
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
  const dur = Number.isFinite(audioEl.duration) ? audioEl.duration : getState().duration || 0;
  const clamped = Math.max(0, dur > 0 ? Math.min(seconds, dur) : seconds);
  audioEl.currentTime = clamped;
  setState({ currentTime: clamped });
  if (autoPlay && audioEl.paused) {
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

export function clearUserQueue() {
  setState({ userQueue: [] });
}

export function jumpToQueueIndex(index) {
  const st = getState();
  if (index < 0 || index >= st.queue.length) return;
  setState({ queueIndex: index });
  startTrackPlayback(st.queue[index]);
}

export function playFromUserQueue(index) {
  const st = getState();
  if (index < 0 || index >= st.userQueue.length) return;
  const chosen = st.userQueue[index];
  const rest = st.userQueue.slice(index + 1);
  setState({ userQueue: rest });
  startTrackPlayback(chosen);
}
