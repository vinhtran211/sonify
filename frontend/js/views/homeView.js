// Home / Discover View — Greeting, 6-Card Quick Picks, Live Charts Carousels, Recently Played

import { api } from '../api.js';
import { openTrackContextMenu } from '../components/contextMenu.js';
import { renderMosaicHtml } from '../components/trackList.js';
import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml } from '../lrc.js';
import { playList, playTrack, togglePlay } from '../player.js';
import { navigate } from '../router.js';
import { getState, setState } from '../store.js';

let homeFetchInFlight = false;

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

  const sections = st.homeData.sections || [];
  const currentId = st.currentTrack?.id || null;
  const isPlaying = st.isPlaying;

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
      artHtml: `<div class="quick-pick-art">${renderMosaicHtml(pl.covers)}</div>`,
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
    <div class="quick-picks-grid">${quickHtml}</div>
    ${shelvesHtml}
  `;

  // Bind carousel scroll arrows
  container.querySelectorAll('.btn-carousel-prev, .btn-carousel-next').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = document.getElementById(btn.dataset.target);
      if (!target) return;
      const dir = btn.classList.contains('btn-carousel-next') ? 1 : -1;
      target.scrollBy({ left: dir * 560, behavior: 'smooth' });
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
    const card = e.target.closest('.media-card');
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
