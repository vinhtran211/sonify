// Listening Stats / Sound Capsule View (#/stats)

import { api } from '../api.js';
import { renderTrackList, wireTrackListEvents } from '../components/trackList.js';
import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml } from '../lrc.js';
import { playList } from '../player.js';
import { navigate } from '../router.js';

let statsReqId = 0;

function formatHourLabel(h) {
  if (h === 0) return '12 AM';
  if (h < 12) return `${h} AM`;
  if (h === 12) return '12 PM';
  return `${h - 12} PM`;
}

export async function renderStatsView(container) {
  if (!container) return;
  const reqId = ++statsReqId;

  container.innerHTML = `
    <div style="padding:20px 0; display:flex; flex-direction:column; gap:16px;">
      <div class="skeleton" style="height:160px; border-radius:12px;"></div>
      <div class="skeleton" style="height:120px; border-radius:10px;"></div>
      <div class="skeleton" style="height:220px; border-radius:10px;"></div>
    </div>
  `;

  try {
    const stats = await api.getStats();
    if (reqId !== statsReqId) return;

    const hourly = Array.isArray(stats?.hourlyCounts) && stats.hourlyCounts.length === 24
      ? stats.hourlyCounts
      : new Array(24).fill(0);
    const maxHourVal = Math.max(1, ...hourly);
    const topTracks = stats?.topTracks || [];
    const topArtists = stats?.topArtists || [];

    container.innerHTML = `
      <section class="hero-banner stats-hero">
        <div class="hero-cover-placeholder" style="background:linear-gradient(135deg,#1db954,#1e3264);">
          ${ICONS.stats}
        </div>
        <div class="hero-meta">
          <span class="hero-type">YOUR SOUND CAPSULE</span>
          <h1 class="hero-title">Listening Stats</h1>
          <div class="hero-sub">
            <span><strong>${Number(stats.totalMinutes || 0).toLocaleString()}</strong> minutes listened</span>
            <span>• <strong>${Number(stats.totalPlays || 0).toLocaleString()}</strong> plays</span>
            <span>• Peak hour: <strong>${formatHourLabel(stats.mostActiveHour || 0)}</strong></span>
          </div>
        </div>
      </section>

      <div class="stats-kpi-grid">
        <div class="stats-kpi-card">
          <div class="stats-kpi-val">${Number(stats.totalMinutes || 0).toLocaleString()}</div>
          <div class="stats-kpi-label">Minutes Listened</div>
        </div>
        <div class="stats-kpi-card">
          <div class="stats-kpi-val">${Number(stats.totalPlays || 0).toLocaleString()}</div>
          <div class="stats-kpi-label">Total Plays</div>
        </div>
        <div class="stats-kpi-card">
          <div class="stats-kpi-val">${Number(stats.streakDays || 0)}d</div>
          <div class="stats-kpi-label">Listening Streak</div>
        </div>
        <div class="stats-kpi-card">
          <div class="stats-kpi-val">${Number(stats.likedCount || 0)}</div>
          <div class="stats-kpi-label">Liked Songs</div>
        </div>
        <div class="stats-kpi-card">
          <div class="stats-kpi-val">${Number(stats.playlistCount || 0)}</div>
          <div class="stats-kpi-label">Playlists</div>
        </div>
        <div class="stats-kpi-card">
          <div class="stats-kpi-val">${Number(stats.followedArtistsCount || 0)}</div>
          <div class="stats-kpi-label">Following Artists</div>
        </div>
      </div>

      <section class="section">
        <div class="section-header">
          <h2 class="section-title">Listening Activity by Hour</h2>
        </div>
        <div class="stats-hourly-chart" role="img" aria-label="24-hour listening distribution">
          ${hourly
            .map((cnt, h) => {
              const pct = Math.max(4, Math.round((cnt / maxHourVal) * 100));
              return `
                <div class="stats-bar-col" title="${formatHourLabel(h)}: ${cnt} plays">
                  <div class="stats-bar-track">
                    <div class="stats-bar-fill ${cnt > 0 ? 'has-val' : ''}" style="height:${cnt > 0 ? pct : 4}%;"></div>
                  </div>
                  <span class="stats-bar-lbl">${h % 4 === 0 ? h : ''}</span>
                </div>
              `;
            })
            .join('')}
        </div>
      </section>

      ${
        topArtists.length
          ? `
        <section class="section">
          <div class="section-header">
            <h2 class="section-title">Your Top Artists</h2>
          </div>
          <div class="stats-artists-grid">
            ${topArtists
              .map(
                (a, idx) => `
              <div class="stats-artist-card" data-artist-id="${escapeHtml(a.artistId || '')}">
                <span class="stats-rank">#${idx + 1}</span>
                <img class="stats-artist-img" src="${escapeHtml(a.coverSmall || '/api/artwork/' + encodeURIComponent(a.artist || 'artist'))}" alt="${escapeHtml(a.artist)}" />
                <div style="min-width:0; flex:1;">
                  <div class="stats-artist-name">${escapeHtml(a.artist)}</div>
                  <div class="stats-artist-plays">${a.playCount} ${a.playCount === 1 ? 'play' : 'plays'}</div>
                </div>
              </div>
            `
              )
              .join('')}
          </div>
        </section>
      `
          : ''
      }

      ${
        topTracks.length
          ? `
        <section class="section">
          <div class="section-header">
            <h2 class="section-title">Your Top Tracks</h2>
            <button id="stats-play-top" class="btn-subtle btn-xs">Play Top Tracks</button>
          </div>
          <div id="stats-top-tracks-slot">
            ${renderTrackList(topTracks, { showAlbum: true })}
          </div>
        </section>
      `
          : `
        <div class="empty-state">
          <h3>Start listening to build your Sound Capsule</h3>
          <p>Play tracks across Sonify and your top songs, favorite artists, and peak listening hours will appear here automatically.</p>
          <button class="btn-primary" id="stats-go-home">Explore Music</button>
        </div>
      `
      }
    `;

    container.querySelector('#stats-play-top')?.addEventListener('click', () => playList(topTracks, 0));
    container.querySelector('#stats-go-home')?.addEventListener('click', () => navigate('#/'));

    const trackSlot = container.querySelector('#stats-top-tracks-slot');
    if (trackSlot) wireTrackListEvents(trackSlot, topTracks);

    container.querySelectorAll('.stats-artist-card').forEach((card) => {
      const aid = card.dataset.artistId;
      if (aid) {
        card.style.cursor = 'pointer';
        card.addEventListener('click', () => navigate(`#/artist/${encodeURIComponent(aid)}`));
      }
    });
  } catch (err) {
    if (reqId !== statsReqId) return;
    container.innerHTML = `
      <div class="empty-state">
        <h3>Could not load Listening Stats</h3>
        <p>${escapeHtml(err.message || 'Unknown error')}</p>
      </div>
    `;
  }
}
