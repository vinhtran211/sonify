// Right Drawer Queue Panel (Queue | Recently Played sub-tabs, Drag-and-Drop Reorder, Clear Queue)

import { ICONS } from '../icons.js';
import { coverUrlFor, escapeHtml, formatRelativeTime } from '../lrc.js';
import {
  clearAllQueue,
  clearUserQueue,
  jumpToQueueIndex,
  moveUserQueueItem,
  playFromUserQueue,
  playTrack,
  removeFromUserQueue,
  reorderUserQueue,
} from '../player.js';
import { getState, setState } from '../store.js';

export function renderQueueBody(container) {
  const st = getState();
  const subTab = st.queueSubTab || 'queue';
  const nowTrack = st.currentTrack;
  const userQueue = st.userQueue || [];
  const contextUpNext =
    st.queueIndex >= 0 ? (st.queue || []).slice(st.queueIndex + 1) : st.queue || [];
  const history = st.history || [];

  const subTabsHtml = `
    <div class="queue-subtabs">
      <button class="queue-subtab-btn ${subTab === 'queue' ? 'active' : ''}" data-q-subtab="queue">Queue</button>
      <button class="queue-subtab-btn ${subTab === 'recent' ? 'active' : ''}" data-q-subtab="recent">Recently Played</button>
    </div>
  `;

  if (subTab === 'recent') {
    const recentBody =
      history.length === 0
        ? `
          <div class="empty-state" style="padding: 36px 10px;">
            <div class="lyrics-state-icon" style="margin: 0 auto;">${ICONS.history}</div>
            <h3>No recent plays</h3>
            <p style="font-size: 13px;">Songs you play will show up here for quick replay.</p>
          </div>
        `
        : history
            .slice(0, 40)
            .map(
              (item, idx) => `
            <div class="queue-item" data-recent-idx="${idx}">
              <img src="${escapeHtml(coverUrlFor(item.track, false))}" alt="" loading="lazy" />
              <div class="queue-item-meta">
                <div class="queue-item-title truncate">${escapeHtml(item.track.title)}</div>
                <div class="queue-item-artist truncate">${escapeHtml(item.track.artist)} &bull; ${escapeHtml(formatRelativeTime(item.playedAt))}</div>
              </div>
            </div>
          `
            )
            .join('');

    container.innerHTML = `${subTabsHtml}${recentBody}`;
    container.querySelectorAll('[data-q-subtab]').forEach((b) => {
      b.addEventListener('click', () => setState({ queueSubTab: b.dataset.qSubtab }));
    });
    container.onclick = (e) => {
      const rec = e.target.closest('[data-recent-idx]');
      if (rec) {
        const entry = history[Number(rec.dataset.recentIdx)];
        if (entry?.track) playTrack(entry.track, history.map((h) => h.track));
      }
    };
    return;
  }

  if (!nowTrack && userQueue.length === 0 && contextUpNext.length === 0) {
    container.innerHTML = `
      ${subTabsHtml}
      <div class="empty-state" style="padding: 36px 10px;">
        <div class="lyrics-state-icon" style="margin: 0 auto;">${ICONS.queue}</div>
        <h3>Your queue is empty</h3>
        <p style="font-size: 13px;">Play a track or right-click any song and choose <strong>Add to Queue</strong>.</p>
      </div>
    `;
    container.querySelectorAll('[data-q-subtab]').forEach((b) => {
      b.addEventListener('click', () => setState({ queueSubTab: b.dataset.qSubtab }));
    });
    return;
  }

  const nowHtml = nowTrack
    ? `
      <div class="queue-section-label">
        <span>Now Playing</span>
        ${
          userQueue.length > 0 || contextUpNext.length > 0
            ? '<button id="btn-clear-all-queue" class="btn-subtle btn-xs">Clear Queue</button>'
            : ''
        }
      </div>
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
          <div class="queue-item draggable-queue-item" draggable="true" data-uq-idx="${idx}">
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

  container.innerHTML = `${subTabsHtml}${nowHtml}${userHtml}${nextUpHtml}`;

  container.querySelectorAll('[data-q-subtab]').forEach((b) => {
    b.addEventListener('click', () => setState({ queueSubTab: b.dataset.qSubtab }));
  });
  container.querySelector('#btn-clear-uqueue')?.addEventListener('click', () => clearUserQueue());
  container.querySelector('#btn-clear-all-queue')?.addEventListener('click', () => clearAllQueue());

  // HTML5 Drag-and-Drop reordering for user queue
  let dragFromIdx = -1;
  container.querySelectorAll('.draggable-queue-item').forEach((item) => {
    item.addEventListener('dragstart', (e) => {
      dragFromIdx = Number(item.dataset.uqIdx);
      item.classList.add('dragging');
      if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
    });
    item.addEventListener('dragover', (e) => {
      e.preventDefault();
      item.classList.add('drag-over');
    });
    item.addEventListener('dragleave', () => {
      item.classList.remove('drag-over');
    });
    item.addEventListener('drop', (e) => {
      e.preventDefault();
      item.classList.remove('drag-over');
      const toIdx = Number(item.dataset.uqIdx);
      if (dragFromIdx >= 0 && toIdx >= 0 && dragFromIdx !== toIdx) {
        reorderUserQueue(dragFromIdx, toIdx);
      }
      dragFromIdx = -1;
    });
    item.addEventListener('dragend', () => {
      item.classList.remove('dragging');
      dragFromIdx = -1;
    });
  });

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
