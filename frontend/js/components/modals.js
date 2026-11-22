// Modals (Create/Edit Playlist, Delete Confirmation) and Toast Notifications

import { api } from '../api.js';
import { escapeHtml } from '../lrc.js';
import { navigate } from '../router.js';
import { refreshPlaylists } from '../store.js';

let modalOpen = false;

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

function openModal(html, onMount) {
  const root = document.getElementById('modal-root');
  if (!root) return;
  root.innerHTML = `<div class="modal-card" role="document">${html}</div>`;
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
        <textarea id="pl-edit-desc" class="form-textarea" rows="3" maxlength="400">${escapeHtml(playlist.description || '')}</textarea>
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
      nameInput?.focus();
      nameInput?.select();
      root.querySelector('#modal-cancel-btn')?.addEventListener('click', closeModal);
      root.querySelector('#playlist-edit-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = nameInput.value.trim();
        const description = descInput.value.trim();
        if (!name) return;
        try {
          const res = await api.updatePlaylist(playlist.id, { name, description });
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
