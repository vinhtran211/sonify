// Right Drawer Queue Panel (Now Playing, User Queue, Context Next Up)

import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml } from '../lrc.js';
import {
  clearUserQueue,
  jumpToQueueIndex,
  moveUserQueueItem,
  playFromUserQueue,
  removeFromUserQueue,
} from '../player.js';
import { getState } from '../store.js';

export function renderQueueBody(container) {
  const st = getState();
  const nowTrack = st.currentTrack;
  const userQueue = st.userQueue || [];
  const contextUpNext =
    st.queueIndex >= 0 ? (st.queue || []).slice(st.queueIndex + 1) : st.queue || [];

  if (!nowTrack && userQueue.length === 0 && contextUpNext.length === 0) {
    container.innerHTML = `
      <div class="empty-state" style="padding: 36px 10px;">
        <div class="lyrics-state-icon" style="margin: 0 auto;">${ICONS.queue}</div>
        <h3>Your queue is empty</h3>
        <p style="font-size: 13px;">Play a track or right-click any song and choose <strong>Add to Queue</strong>.</p>
      </div>
    `;
    return;
  }

  const nowHtml = nowTrack
    ? `
      <div class="queue-section-label"><span>Now Playing</span></div>
      <div class="queue-item now-playing">
        <img src="${escapeHtml(coverUrlFor(nowTrack, false))}" alt="" />
        <div class="queue-item-meta">
          <div class="queue-item-title truncate">${escapeHtml(nowTrack.title)}</div>
          <div class="queue-item-artist truncate">${escapeHtml(nowTrack.artist)}</div>
        </div>
        <div class="equalizer-bars ${st.isPlaying ? '' : 'paused'}"><span></span><span></span><span></span></div>
      </div>
    `
    : '';

  const userHtml =
    userQueue.length > 0
      ? `
        <div class="queue-section-label">
          <span>Next in Queue (${userQueue.length})</span>
          <button id="btn-clear-uqueue" class="btn-subtle btn-xs">Clear</button>
        </div>
        ${userQueue
          .map(
            (t, idx) => `
          <div class="queue-item" data-uq-idx="${idx}">
            <img src="${escapeHtml(coverUrlFor(t, false))}" alt="" loading="lazy" />
            <div class="queue-item-meta">
              <div class="queue-item-title truncate">${escapeHtml(t.title)}</div>
              <div class="queue-item-artist truncate">${escapeHtml(t.artist)}</div>
            </div>
            <div class="queue-item-btns">
              <button class="icon-btn btn-uq-up" data-idx="${idx}" title="Move up" ${idx === 0 ? 'disabled style="opacity:0.3;"' : ''}>${ICONS.arrowUp}</button>
              <button class="icon-btn btn-uq-down" data-idx="${idx}" title="Move down" ${idx === userQueue.length - 1 ? 'disabled style="opacity:0.3;"' : ''}>${ICONS.arrowDown}</button>
              <button class="icon-btn btn-uq-rm" data-idx="${idx}" title="Remove">${ICONS.close}</button>
            </div>
          </div>
        `
          )
          .join('')}
      `
      : '';

  const nextUpHtml =
    contextUpNext.length > 0
      ? `
        <div class="queue-section-label"><span>Next Up</span></div>
        ${contextUpNext
          .slice(0, 40)
          .map((t, relIdx) => {
            const absIdx = (st.queueIndex >= 0 ? st.queueIndex + 1 : 0) + relIdx;
            return `
              <div class="queue-item" data-ctx-idx="${absIdx}">
                <img src="${escapeHtml(coverUrlFor(t, false))}" alt="" loading="lazy" />
                <div class="queue-item-meta">
                  <div class="queue-item-title truncate">${escapeHtml(t.title)}</div>
                  <div class="queue-item-artist truncate">${escapeHtml(t.artist)}</div>
                </div>
              </div>
            `;
          })
          .join('')}
      `
      : '';

  container.innerHTML = `${nowHtml}${userHtml}${nextUpHtml}`;

  container.querySelector('#btn-clear-uqueue')?.addEventListener('click', () => clearUserQueue());

  container.onclick = (e) => {
    const up = e.target.closest('.btn-uq-up');
    if (up) {
      e.stopPropagation();
      moveUserQueueItem(Number(up.dataset.idx), -1);
      return;
    }
    const down = e.target.closest('.btn-uq-down');
    if (down) {
      e.stopPropagation();
      moveUserQueueItem(Number(down.dataset.idx), 1);
      return;
    }
    const rm = e.target.closest('.btn-uq-rm');
    if (rm) {
      e.stopPropagation();
      removeFromUserQueue(Number(rm.dataset.idx));
      return;
    }
    const uqItem = e.target.closest('[data-uq-idx]');
    if (uqItem) {
      playFromUserQueue(Number(uqItem.dataset.uqIdx));
      return;
    }
    const ctxItem = e.target.closest('[data-ctx-idx]');
    if (ctxItem) {
      jumpToQueueIndex(Number(ctxItem.dataset.ctxIdx));
    }
  };
}
