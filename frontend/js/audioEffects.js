// 5-Band Web Audio API Graphic Equalizer (Lazy-Attach & Zero-CORS-Silence Safe)

export const EQ_FREQUENCIES = [60, 230, 910, 3600, 14000];
export const EQ_LABELS = ['60Hz', '230Hz', '910Hz', '3.6kHz', '14kHz'];

export const EQ_PRESETS = {
  flat: { label: 'Flat', bands: [0, 0, 0, 0, 0] },
  bass_boost: { label: 'Bass Boost', bands: [6, 4, 0, 0, 1] },
  vocal: { label: 'Vocal', bands: [-2, 1, 4, 3, 1] },
  pop: { label: 'Pop', bands: [-1, 2, 4, 2, -1] },
  rock: { label: 'Rock', bands: [4, 2, -1, 3, 4] },
  electronic: { label: 'Electronic', bands: [5, 3, 0, 2, 5] },
  acoustic: { label: 'Acoustic', bands: [2, 1, 2, 3, 2] },
  late_night: { label: 'Late Night', bands: [-2, 1, 2, 1, -3] },
  custom: { label: 'Custom', bands: [0, 0, 0, 0, 0] },
};

let audioCtx = null;
let mediaSource = null;
let filters = [];
let attachedElement = null;
let currentBands = [0, 0, 0, 0, 0];

export function isEqNonFlat(bands = currentBands) {
  return Array.isArray(bands) && bands.some((v) => Math.abs(Number(v) || 0) > 0.05);
}

export function isEqGraphAttached() {
  return Boolean(mediaSource && audioCtx);
}

export function ensureEqGraph(audioEl) {
  if (!audioEl) return false;
  if (mediaSource && attachedElement === audioEl) return true;
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return false;
  try {
    audioCtx = new AudioCtx();
    mediaSource = audioCtx.createMediaElementSource(audioEl);
    filters = EQ_FREQUENCIES.map((freq, idx) => {
      const node = audioCtx.createBiquadFilter();
      node.type = idx === 0 ? 'lowshelf' : idx === EQ_FREQUENCIES.length - 1 ? 'highshelf' : 'peaking';
      node.frequency.value = freq;
      node.Q.value = 1.0;
      node.gain.value = Number(currentBands[idx]) || 0;
      return node;
    });
    let prev = mediaSource;
    for (const f of filters) {
      prev.connect(f);
      prev = f;
    }
    prev.connect(audioCtx.destination);
    attachedElement = audioEl;
    return true;
  } catch {
    return false;
  }
}

export function applyEqBands(audioEl, bands) {
  if (!Array.isArray(bands) || bands.length !== 5) return;
  currentBands = bands.map((v) => Math.max(-12, Math.min(12, Number(v) || 0)));

  // Lazy attach: NEVER attach Web Audio graph unless user has enabled non-flat EQ
  if (!isEqGraphAttached()) {
    if (!isEqNonFlat(currentBands)) return;
    if (!ensureEqGraph(audioEl)) return;
  }

  for (let i = 0; i < filters.length; i += 1) {
    try {
      filters[i].gain.value = currentBands[i];
    } catch {
      // Ignore Web Audio state errors
    }
  }
}

export function resumeEqContext() {
  if (audioCtx && audioCtx.state === 'suspended') {
    audioCtx.resume().catch(() => {});
  }
}
