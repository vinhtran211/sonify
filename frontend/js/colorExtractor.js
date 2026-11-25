// Dynamic Dominant-Color Header Gradient Generator (CORS-Safe Palette Hash + Optional Sampling)

const SPOTIFY_HERO_PALETTE = [
  '#1DB954',
  '#7C3AED',
  '#E11D48',
  '#0284C7',
  '#D97706',
  '#0D9488',
  '#9333EA',
  '#DC2626',
  '#2563EB',
  '#059669',
  '#C026D3',
  '#EA580C',
];

export function hashColorForSeed(seed = '') {
  const str = String(seed || 'sonify');
  let h = 2166136261;
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return SPOTIFY_HERO_PALETTE[Math.abs(h) % SPOTIFY_HERO_PALETTE.length];
}

export function applyDynamicHeroGradient(heroEl, seed, explicitColor = '') {
  if (!heroEl) return;
  const tint = explicitColor && /^#[0-9a-fA-F]{3,8}$/.test(explicitColor.trim())
    ? explicitColor.trim()
    : hashColorForSeed(seed);
  heroEl.style.setProperty('--hero-tint', tint);
  heroEl.style.background = `linear-gradient(180deg, ${tint}66 0%, rgba(18, 18, 18, 0.25) 100%)`;
}
