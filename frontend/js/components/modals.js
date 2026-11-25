// Modals (Create/Edit/Import Playlist, Delete Confirmation) and Toast Notifications

import { api } from '../api.js';
import { escapeHtml } from '../lrc.js';
import { navigate } from '../router.js';
import { refreshPlaylists } from '../store.js';

let modalOpen = false;

const COVER_SWATCHES = [
  '',
  '#1db954',
  '#1e3264',
  '#8d67ab',
  '#e8115b',
  '#dc148c',
  '#bc5900',
  '#0d73ec',
  '#006450',
  '#503750',
];

export function showToast(message, variant = 'success', durationMs = 2600) {
  const root = document.getElementById('toast-root');
  if (!root) return;
  const el = document.createElement('div');
  el.className = `toast ${variant}`;
  el.textContent = message;
  root.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity 180ms ease';
    setTimeout(() => el.remove(), 200);
  }, durationMs);
}

export function isModalOpen() {
  return modalOpen;
}

export function closeModal() {
  const root = document.getElementById('modal-root');
  if (!root) return;
  root.classList.add('hidden');
  root.innerHTML = '';
  modalOpen = false;
}

export function openModal(html, onMount, cardClass = '') {
  const root = document.getElementById('modal-root');
  if (!root) return;
  const cls = cardClass ? `modal-card ${cardClass}` : 'modal-card';
  root.innerHTML = `<div class="${cls}" role="document">${html}</div>`;
  root.classList.remove('hidden');
  modalOpen = true;

  root.onclick = (e) => {
    if (e.target === root) closeModal();
  };
  if (onMount) onMount(root);
}

export function openCreatePlaylistModal(initialTrack = null) {
  openModal(
    `
    <h2 class="modal-title">Create Playlist</h2>
    <form id="playlist-modal-form">
      <div class="form-group">
        <label class="form-label" for="pl-name-input">Name</label>
        <input id="pl-name-input" class="form-input" type="text" maxlength="120" placeholder="My Chill Mix" required />
      </div>
      <div class="form-group">
        <label class="form-label" for="pl-desc-input">Description (optional)</label>
        <textarea id="pl-desc-input" class="form-textarea" rows="3" maxlength="400" placeholder="Add a short vibe description..."></textarea>
      </div>
      <div class="modal-actions">
        <button type="button" class="btn-subtle" id="modal-cancel-btn">Cancel</button>
        <button type="submit" class="btn-primary">Create</button>
      </div>
    </form>
    `,
    (root) => {
      const nameInput = root.querySelector('#pl-name-input');
      const descInput = root.querySelector('#pl-desc-input');
      nameInput?.focus();
      root.querySelector('#modal-cancel-btn')?.addEventListener('click', closeModal);
      root.querySelector('#playlist-modal-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = nameInput.value.trim();
        const desc = descInput.value.trim();
        if (!name) return;
        try {
          const res = await api.createPlaylist(name, desc);
          const pl = res.playlist;
          if (initialTrack && pl?.id) {
            await api.addTrackToPlaylist(pl.id, initialTrack);
          }
          await refreshPlaylists();
          closeModal();
          showToast(
            initialTrack
              ? `Created "${pl.name}" and added "${initialTrack.title}"`
              : `Playlist "${pl.name}" created`
          );
          if (!initialTrack && pl?.id) {
            navigate(`#/playlist/${encodeURIComponent(pl.id)}`);
          }
        } catch (err) {
          showToast(err.message || 'Could not create playlist', 'error');
        }
      });
    }
  );
}

export function openEditPlaylistModal(playlist, onUpdated = null) {
  if (!playlist) return;
  let selectedColor = playlist.coverColor || '';
  openModal(
    `
    <h2 class="modal-title">Edit Playlist Details</h2>
    <form id="playlist-edit-form">
      <div class="form-group">
        <label class="form-label" for="pl-edit-name">Name</label>
        <input id="pl-edit-name" class="form-input" type="text" maxlength="120" value="${escapeHtml(playlist.name)}" required />
      </div>
      <div class="form-group">
        <label class="form-label" for="pl-edit-desc">Description</label>
        <textarea id="pl-edit-desc" class="form-textarea" rows="2" maxlength="400">${escapeHtml(playlist.description || '')}</textarea>
      </div>
      <div class="form-group">
        <label class="form-label">Custom Cover Color</label>
        <div class="swatch-row" id="pl-color-swatches">
          ${COVER_SWATCHES.map(
            (col) => `
            <button
              type="button"
              class="color-swatch ${selectedColor === col ? 'active' : ''}"
              data-swatch="${col}"
              title="${col || 'Default Mosaic'}"
              style="background:${col || 'linear-gradient(135deg,#2a2a2a,#181818)'};"
            >${col ? '' : '×'}</button>
          `
          ).join('')}
        </div>
      </div>
      <div class="form-row-2">
        <div class="form-group">
          <label class="form-label" for="pl-edit-emoji">Cover Emoji (optional)</label>
          <input id="pl-edit-emoji" class="form-input" type="text" maxlength="8" placeholder="🔥 🎧 🌙" value="${escapeHtml(playlist.coverEmoji || '')}" />
        </div>
        <div class="form-group">
          <label class="form-label" for="pl-edit-url">Cover Image URL (optional)</label>
          <input id="pl-edit-url" class="form-input" type="url" maxlength="400" placeholder="https://..." value="${escapeHtml(playlist.coverUrl || '')}" />
        </div>
      </div>
      <div class="modal-actions">
        <button type="button" class="btn-subtle" id="modal-cancel-btn">Cancel</button>
        <button type="submit" class="btn-primary">Save</button>
      </div>
    </form>
    `,
    (root) => {
      const nameInput = root.querySelector('#pl-edit-name');
      const descInput = root.querySelector('#pl-edit-desc');
      const emojiInput = root.querySelector('#pl-edit-emoji');
      const urlInput = root.querySelector('#pl-edit-url');
      nameInput?.focus();
      nameInput?.select();
      root.querySelector('#modal-cancel-btn')?.addEventListener('click', closeModal);

      root.querySelectorAll('[data-swatch]').forEach((btn) => {
        btn.addEventListener('click', () => {
          selectedColor = btn.dataset.swatch || '';
          root.querySelectorAll('[data-swatch]').forEach((b) => b.classList.toggle('active', b === btn));
        });
      });

      root.querySelector('#playlist-edit-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = nameInput.value.trim();
        const description = descInput.value.trim();
        const coverEmoji = emojiInput.value.trim();
        const coverUrl = urlInput.value.trim();
        if (!name) return;
        try {
          const res = await api.updatePlaylist(playlist.id, {
            name,
            description,
            coverColor: selectedColor,
            coverEmoji,
            coverUrl,
          });
          await refreshPlaylists();
          closeModal();
          showToast(`Updated "${res.playlist.name}"`);
          if (onUpdated) onUpdated(res.playlist);
        } catch (err) {
          showToast(err.message || 'Could not update playlist', 'error');
        }
      });
    }
  );
}

export function openImportPlaylistModal() {
  openModal(
    `
    <h2 class="modal-title">Import Playlist (JSON / M3U)</h2>
    <form id="playlist-import-form">
      <div class="form-group">
        <label class="form-label" for="pl-import-name">Playlist Name</label>
        <input id="pl-import-name" class="form-input" type="text" maxlength="120" placeholder="Imported Mix" required />
      </div>
      <div class="form-group">
        <label class="form-label" for="pl-import-file">Load from file (.json or .m3u)</label>
        <input id="pl-import-file" class="form-input" type="file" accept=".json,.m3u,.m3u8,.txt" />
      </div>
      <div class="form-group">
        <label class="form-label" for="pl-import-text">Or paste JSON / M3U content</label>
        <textarea id="pl-import-text" class="form-textarea" rows="6" placeholder="#EXTM3U&#10;#EXTINF:200,Artist - Song Title&#10;... or Sonify exported JSON"></textarea>
      </div>
      <div class="modal-actions">
        <button type="button" class="btn-subtle" id="modal-cancel-btn">Cancel</button>
        <button type="submit" class="btn-primary">Import</button>
      </div>
    </form>
    `,
    (root) => {
      const nameInput = root.querySelector('#pl-import-name');
      const fileInput = root.querySelector('#pl-import-file');
      const textInput = root.querySelector('#pl-import-text');
      nameInput?.focus();
      root.querySelector('#modal-cancel-btn')?.addEventListener('click', closeModal);

      fileInput?.addEventListener('change', async () => {
        const file = fileInput.files?.[0];
        if (!file) return;
        const text = await file.text();
        textInput.value = text;
        if (!nameInput.value.trim()) {
          nameInput.value = file.name.replace(/\.(json|m3u8?|txt)$/i, '');
        }
      });

      root.querySelector('#playlist-import-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = nameInput.value.trim() || 'Imported Playlist';
        const raw = textInput.value.trim();
        if (!raw) {
          showToast('Please paste JSON/M3U text or choose a file', 'error');
          return;
        }
        let payload = { name, m3u: raw };
        if (raw.startsWith('{') || raw.startsWith('[')) {
          try {
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed)) {
              payload = { name, tracks: parsed };
            } else {
              payload = {
                name: name || parsed.name || 'Imported Playlist',
                description: parsed.description || '',
                tracks: Array.isArray(parsed.tracks) ? parsed.tracks : [],
              };
            }
          } catch {
            // Treat as M3U if invalid JSON
          }
        }
        try {
          const res = await api.importPlaylist(payload);
          await refreshPlaylists();
          closeModal();
          showToast(`Imported "${res.playlist?.name || name}" (${res.importedCount || 0} songs)`);
          if (res.playlist?.id) {
            navigate(`#/playlist/${encodeURIComponent(res.playlist.id)}`);
          }
        } catch (err) {
          showToast(err.message || 'Could not import playlist', 'error');
        }
      });
    }
  );
}

export function openDeletePlaylistModal(playlist, onDeleted = null) {
  if (!playlist) return;
  openModal(
    `
    <h2 class="modal-title">Delete Playlist?</h2>
    <p style="color: var(--text-secondary); margin: 0 0 8px;">
      This will permanently remove <strong style="color: #fff;">${escapeHtml(playlist.name)}</strong> from Your Library.
    </p>
    <div class="modal-actions">
      <button type="button" class="btn-subtle" id="modal-cancel-btn">Cancel</button>
      <button type="button" class="btn-danger" id="modal-confirm-delete">Delete</button>
    </div>
    `,
    (root) => {
      root.querySelector('#modal-cancel-btn')?.addEventListener('click', closeModal);
      root.querySelector('#modal-confirm-delete')?.addEventListener('click', async () => {
        try {
          await api.deletePlaylist(playlist.id);
          await refreshPlaylists();
          closeModal();
          showToast(`Deleted "${playlist.name}"`, 'info');
          if (onDeleted) onDeleted();
        } catch (err) {
          showToast(err.message || 'Could not delete playlist', 'error');
        }
      });
    }
  );
}
