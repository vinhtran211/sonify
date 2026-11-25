// Home / Discover View — Greeting, Quick Genre Pills, 6-Card Quick Picks, Made For You Mixes, Live Charts Carousels, Recently Played

import { api } from '../api.js';
import { openTrackContextMenu } from '../components/contextMenu.js';
import { renderPlaylistCoverHtml } from '../components/trackList.js';
import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml } from '../lrc.js';
import { playList, playTrack, togglePlay } from '../player.js';
import { navigate } from '../router.js';
import { getState, setState } from '../store.js';

let homeFetchInFlight = false;
let mixesFetchInFlight = false;

const QUICK_GENRES = [
  { slug: 'pop', label: 'Pop' },
  { slug: 'vpop', label: 'V-Pop' },
  { slug: 'hiphop', label: 'Hip-Hop' },
  { slug: 'kpop', label: 'K-Pop' },
  { slug: 'chill', label: 'Chill' },
  { slug: 'workout', label: 'Workout' },
  { slug: 'focus', label: 'Focus' },
  { slug: 'lofi', label: 'Lo-Fi' },
];

function getGreeting() {
  const hr = new Date().getHours();
  if (hr < 12) return 'Good morning';
  if (hr < 18) return 'Good afternoon';
  return 'Good evening';
}

function renderSkeletons(container) {
  container.innerHTML = `
    <h1 class="home-greeting">${getGreeting()}</h1>
    <div class="quick-picks-grid">
      ${Array.from({ length: 6 }, () => '<div class="skeleton" style="height:62px;"></div>').join('')}
    </div>
    ${Array.from(
      { length: 2 },
      () => `
      <div class="shelf-section">
        <div class="skeleton" style="height:26px; width:220px; margin-bottom:14px;"></div>
        <div class="card-carousel">
          ${Array.from({ length: 6 }, () => '<div class="skeleton" style="width:178px; height:242px; flex:0 0 178px;"></div>').join('')}
        </div>
      </div>`
    ).join('')}
  `;
}

export async function loadMadeForYou(forceRefresh = false) {
  const st = getState();
  const targetRegion = st.region;
  if (mixesFetchInFlight && !forceRefresh) return;
  if (!forceRefresh && Array.isArray(st.madeForYou) && st.madeForYou.length > 0) return;

  mixesFetchInFlight = true;
  try {
    const res = await api.getMadeForYou(targetRegion);
    if (getState().region !== targetRegion) return;
    setState({ madeForYou: res?.mixes || [] });
  } catch {
    // Non-fatal
  } finally {
    mixesFetchInFlight = false;
  }
}

export async function loadHomeData(forceRefresh = false) {
  const st = getState();
  const targetRegion = st.region;
  if (homeFetchInFlight && !forceRefresh && st.homeData?.region === targetRegion) return;
  if (!forceRefresh && st.homeData && st.homeData.region === targetRegion) return;

  homeFetchInFlight = true;
  try {
    const data = await api.getHome(targetRegion, forceRefresh);
    if (getState().region !== targetRegion) return;
    setState({
      homeData: data,
      offlineMode: data.mode === 'offline',
    });
    loadMadeForYou(forceRefresh);
  } catch (err) {
    console.error('[Sonify Home] Failed to fetch home:', err);
  } finally {
    homeFetchInFlight = false;
  }
}

export function renderHomeView(container) {
  container.classList.remove('no-padding');
  const st = getState();

  if (!st.homeData || st.homeData.region !== st.region) {
    renderSkeletons(container);
    loadHomeData(false);
    return;
  }

  if (!st.madeForYou || st.madeForYou.length === 0) {
    loadMadeForYou(false);
  }

  const sections = st.homeData.sections || [];
  const currentId = st.currentTrack?.id || null;
  const isPlaying = st.isPlaying;
  const mixes = st.madeForYou || [];

  const genrePillsHtml = `
    <div class="home-genre-pills">
      ${QUICK_GENRES.map(
        (g) => `<button class="pill-btn home-genre-pill" data-genre-slug="${g.slug}">${escapeHtml(g.label)}</button>`
      ).join('')}
      <button class="pill-btn home-stats-pill" id="home-open-stats">${ICONS.stats} Sound Capsule</button>
    </div>
  `;

  // Build 6 Quick Picks: Liked Songs + user playlists + top trending tracks
  const quickPicks = [];
  quickPicks.push({
    kind: 'liked',
    id: 'liked',
    title: 'Liked Songs',
    artHtml: `<div class="quick-pick-art" style="background:var(--liked-gradient); display:flex; align-items:center; justify-content:center; color:#fff;">${ICONS.heartFilled}</div>`,
  });

  for (const pl of (st.playlists || []).slice(0, 3)) {
    quickPicks.push({
      kind: 'playlist',
      id: pl.id,
      title: pl.name,
      artHtml: `<div class="quick-pick-art">${renderPlaylistCoverHtml(pl)}</div>`,
    });
  }

  const primaryTrackSec = sections.find((s) => s.type === 'tracks' && s.items?.length);
  if (primaryTrackSec) {
    for (const tr of primaryTrackSec.items) {
      if (quickPicks.length >= 6) break;
      quickPicks.push({
        kind: 'track',
        id: tr.id,
        title: tr.title,
        track: tr,
        list: primaryTrackSec.items,
        artHtml: `<div class="quick-pick-art"><img src="${escapeHtml(coverUrlFor(tr, false))}" alt="" loading="lazy" /></div>`,
      });
    }
  }

  const quickHtml = quickPicks
    .map(
      (qp, idx) => `
      <div class="quick-pick-card" data-qp-idx="${idx}">
        ${qp.artHtml}
        <span class="quick-pick-title truncate">${escapeHtml(qp.title)}</span>
        <button class="quick-pick-play" data-qp-play="${idx}" title="Play" aria-label="Play ${escapeHtml(qp.title)}">${ICONS.play}</button>
      </div>
    `
    )
    .join('');

  const mixesHtml = mixes.length
    ? `
      <section class="shelf-section" data-shelf-id="made-for-you">
        <div class="shelf-header">
          <div>
            <h2 class="shelf-title">Made For You</h2>
            <p class="shelf-subtitle">Personalized Daily Mixes, Discover Weekly &amp; Release Radar</p>
          </div>
        </div>
        <div class="card-carousel">
          ${mixes
            .map(
              (m, idx) => `
            <div class="media-card mix-card" data-mix-idx="${idx}" data-mix-id="${escapeHtml(m.id)}">
              <div class="media-card-art mix-card-art" style="background:linear-gradient(135deg, ${escapeHtml(m.color || '#1e3264')}, #121212);">
                ${
                  m.cover
                    ? `<img src="${escapeHtml(m.cover)}" alt="${escapeHtml(m.title)}" loading="lazy" />`
                    : `<div class="mix-art-label">${escapeHtml(m.title)}</div>`
                }
                <span class="mix-badge">${escapeHtml(m.title)}</span>
                <button class="play-fab" data-mix-play="${idx}" title="Play ${escapeHtml(m.title)}">${ICONS.play}</button>
              </div>
              <div class="media-card-title truncate">${escapeHtml(m.title)}</div>
              <div class="media-card-sub">${escapeHtml(m.subtitle || `${(m.tracks || []).length} tracks`)}</div>
            </div>
          `
            )
            .join('')}
        </div>
      </section>
    `
    : '';

  // Optional Recently Played shelf derived from play history (unique latest 12 tracks)
  const recentTracks = [];
  const seenRecent = new Set();
  for (const h of st.history || []) {
    if (h.track && !seenRecent.has(h.track.id)) {
      seenRecent.add(h.track.id);
      recentTracks.push(h.track);
      if (recentTracks.length >= 12) break;
    }
  }

  const allShelves = [...sections];
  if (recentTracks.length > 0) {
    allShelves.push({
      id: 'recently-played',
      title: 'Recently Played',
      subtitle: 'Jump back into what you listened to',
      type: 'tracks',
      items: recentTracks,
    });
  }

  const shelvesHtml = allShelves
    .map((sec, sIdx) => {
      const items = sec.items || [];
      if (!items.length) return '';

      const cardsHtml = items
        .map((item, iIdx) => {
          if (sec.type === 'artists') {
            return `
              <div class="media-card artist-card" data-shelf="${sIdx}" data-item="${iIdx}" data-kind="artist">
                <div class="media-card-art">
                  <img src="${escapeHtml(coverUrlFor(item, true))}" alt="${escapeHtml(item.name)}" loading="lazy" />
                  <button class="play-fab" data-fab="1" title="Open artist" aria-label="Open ${escapeHtml(item.name)}">${ICONS.play}</button>
                </div>
                <div class="media-card-title truncate">${escapeHtml(item.name)}</div>
                <div class="media-card-sub">Artist${item.fans ? ` &bull; ${Number(item.fans).toLocaleString()} fans` : ''}</div>
              </div>
            `;
          }
          if (sec.type === 'albums') {
            return `
              <div class="media-card" data-shelf="${sIdx}" data-item="${iIdx}" data-kind="album">
                <div class="media-card-art">
                  <img src="${escapeHtml(coverUrlFor(item, true))}" alt="${escapeHtml(item.title)}" loading="lazy" />
                  <button class="play-fab" data-fab="1" title="Play album" aria-label="Play ${escapeHtml(item.title)}">${ICONS.play}</button>
                </div>
                <div class="media-card-title truncate">${escapeHtml(item.title)}</div>
                <div class="media-card-sub">${escapeHtml(item.artist || 'Album')}${item.releaseDate ? ` &bull; ${escapeHtml(item.releaseDate.slice(0, 4))}` : ''}</div>
              </div>
            `;
          }
          const isAct = item.id === currentId;
          return `
            <div class="media-card ${isAct ? 'playing' : ''}" data-shelf="${sIdx}" data-item="${iIdx}" data-kind="track">
              <div class="media-card-art">
                <img src="${escapeHtml(coverUrlFor(item, true))}" alt="${escapeHtml(item.title)}" loading="lazy" />
                <button class="play-fab" data-fab="1" title="${isAct && isPlaying ? 'Pause' : 'Play'}" aria-label="Play ${escapeHtml(item.title)}">
                  ${isAct && isPlaying ? ICONS.pause : ICONS.play}
                </button>
              </div>
              <div class="media-card-title truncate">${escapeHtml(item.title)}</div>
              <div class="media-card-sub">${escapeHtml(item.artist || '')}</div>
            </div>
          `;
        })
        .join('');

      return `
        <section class="shelf-section" data-shelf-id="${escapeHtml(sec.id)}">
          <div class="shelf-header">
            <div>
              <h2 class="shelf-title">${escapeHtml(sec.title)}</h2>
              ${sec.subtitle ? `<p class="shelf-subtitle">${escapeHtml(sec.subtitle)}</p>` : ''}
            </div>
            <div class="shelf-arrows">
              <button class="icon-circle-btn btn-carousel-prev" data-target="carousel-${sIdx}" title="Scroll left" aria-label="Scroll left">${ICONS.chevronLeft}</button>
              <button class="icon-circle-btn btn-carousel-next" data-target="carousel-${sIdx}" title="Scroll right" aria-label="Scroll right">${ICONS.chevronRight}</button>
            </div>
          </div>
          <div id="carousel-${sIdx}" class="card-carousel">${cardsHtml}</div>
        </section>
      `;
    })
    .join('');

  container.innerHTML = `
    <h1 class="home-greeting">${getGreeting()}</h1>
    ${genrePillsHtml}
    <div class="quick-picks-grid">${quickHtml}</div>
    ${mixesHtml}
    ${shelvesHtml}
  `;

  container.querySelectorAll('.home-genre-pill').forEach((btn) => {
    btn.addEventListener('click', () => navigate(`#/genre/${encodeURIComponent(btn.dataset.genreSlug)}`));
  });
  container.querySelector('#home-open-stats')?.addEventListener('click', () => navigate('#/stats'));

  // Bind carousel scroll arrows
  container.querySelectorAll('.btn-carousel-prev, .btn-carousel-next').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = document.getElementById(btn.dataset.target);
      if (!target) return;
      const dir = btn.classList.contains('btn-carousel-next') ? 1 : -1;
      target.scrollBy({ left: dir * 560, behavior: 'smooth' });
    });
  });

  // Bind Made For You mixes
  container.querySelectorAll('.mix-card').forEach((card) => {
    card.addEventListener('click', (e) => {
      const idx = Number(card.dataset.mixIdx);
      const mix = mixes[idx];
      if (!mix) return;
      if (e.target.closest('[data-mix-play]') && mix.tracks?.length) {
        e.stopPropagation();
        playList(mix.tracks, 0);
        return;
      }
      navigate(`#/mix/${encodeURIComponent(mix.id)}`);
    });
  });

  // Bind Quick Picks clicks
  container.querySelectorAll('.quick-pick-card').forEach((card) => {
    card.addEventListener('click', async (e) => {
      const idx = Number(card.dataset.qpIdx);
      const qp = quickPicks[idx];
      if (!qp) return;
      const isPlayBtn = Boolean(e.target.closest('[data-qp-play]'));

      if (qp.kind === 'liked') {
        if (isPlayBtn && st.likedTracks.length) {
          playList(st.likedTracks, 0);
        } else {
          navigate('#/liked');
        }
      } else if (qp.kind === 'playlist') {
        if (isPlayBtn) {
          try {
            const res = await api.getPlaylist(qp.id);
            if (res.tracks?.length) playList(res.tracks, 0);
            else navigate(`#/playlist/${encodeURIComponent(qp.id)}`);
          } catch {
            navigate(`#/playlist/${encodeURIComponent(qp.id)}`);
          }
        } else {
          navigate(`#/playlist/${encodeURIComponent(qp.id)}`);
        }
      } else if (qp.kind === 'track' && qp.track) {
        playTrack(qp.track, qp.list);
      }
    });
  });

  // Bind Media Cards click & context menu
  container.onclick = async (e) => {
    const card = e.target.closest('.media-card[data-shelf]');
    if (!card) return;
    const shelf = allShelves[Number(card.dataset.shelf)];
    const item = shelf?.items?.[Number(card.dataset.item)];
    if (!item) return;

    const kind = card.dataset.kind;
    const clickedFab = Boolean(e.target.closest('[data-fab]'));

    if (kind === 'track') {
      if (getState().currentTrack?.id === item.id) {
        togglePlay();
      } else {
        playTrack(item, shelf.items, Number(card.dataset.item));
      }
    } else if (kind === 'album') {
      if (clickedFab) {
        try {
          const res = await api.getAlbum(item.id);
          if (res.tracks?.length) playList(res.tracks, 0);
          else navigate(`#/album/${encodeURIComponent(item.id)}`);
        } catch {
          navigate(`#/album/${encodeURIComponent(item.id)}`);
        }
      } else {
        navigate(`#/album/${encodeURIComponent(item.id)}`);
      }
    } else if (kind === 'artist') {
      if (clickedFab) {
        try {
          const res = await api.getArtist(item.id);
          if (res.topTracks?.length) playList(res.topTracks, 0);
          else navigate(`#/artist/${encodeURIComponent(item.id)}`);
        } catch {
          navigate(`#/artist/${encodeURIComponent(item.id)}`);
        }
      } else {
        navigate(`#/artist/${encodeURIComponent(item.id)}`);
      }
    }
  };

  container.oncontextmenu = (e) => {
    const card = e.target.closest('.media-card[data-kind="track"]');
    if (!card) return;
    e.preventDefault();
    const shelf = allShelves[Number(card.dataset.shelf)];
    const item = shelf?.items?.[Number(card.dataset.item)];
    if (item) {
      openTrackContextMenu(item, e.clientX, e.clientY, { contextTracks: shelf.items });
    }
  };
}
