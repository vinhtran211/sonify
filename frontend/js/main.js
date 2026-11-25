// Sonify Frontend Bootstrap — Wires Router, Store, Player, 3-Tab Drawer, Full-Screen Overlay, Views & Keyboard Shortcuts

import { closeContextMenu, initContextMenuGlobalClose, isContextMenuOpen } from './components/contextMenu.js';
import { renderFullScreenPlayer, syncFullScreenProgress } from './components/fullScreenPlayer.js';
import {
  detectClientEnvironment,
  isStandaloneApp,
  openInstallAppModal,
  shareSonifyApp,
} from './components/installModal.js';
import {
  ensureLyricsRafLoop,
  loadLyricsForCurrentTrack,
  renderFullStageLyrics,
  renderLyricsDrawer,
  syncActiveLineDOM,
} from './components/lyricsPanel.js';
import { closeModal, isModalOpen } from './components/modals.js';
import { renderNowPlayingDrawer } from './components/nowPlayingDrawer.js';
import { initPlayerBar, updatePlayerBar } from './components/playerBar.js';
import { renderQueueBody } from './components/queuePanel.js';
import { renderSidebar } from './components/sidebar.js';
import { ICONS } from './icons.js';
import {
  cycleRepeat,
  initPlayer,
  playNextTrack,
  playPrevTrack,
  seekBy,
  setVolume,
  syncAudioSettings,
  toggleMute,
  togglePlay,
  toggleShuffle,
} from './player.js';
import { initRouter, navigate } from './router.js';
import {
  closeDrawer,
  getState,
  hydrateFromBackend,
  setFullScreenMode,
  setRegion,
  setState,
  subscribe,
  toggleDrawer,
} from './store.js';
import {
  renderAlbumView,
  renderArtistView,
  renderMixView,
  renderTrackView,
} from './views/detailView.js';
import { renderGenreView } from './views/genreView.js';
import { renderHistoryView } from './views/historyView.js';
import { loadHomeData, renderHomeView } from './views/homeView.js';
import {
  renderDownloadsView,
  renderLikedView,
  renderPlaylistView,
} from './views/libraryView.js';
import { renderSearchView } from './views/searchView.js';
import { renderStatsView } from './views/statsView.js';

let searchDebounce = null;
let lastRenderedTrackId = null;
let deferredInstallPrompt = null;

function initTopbar() {
  const backBtn = document.getElementById('btn-nav-back');
  const fwdBtn = document.getElementById('btn-nav-forward');
  const searchIcon = document.getElementById('topbar-search-icon');
  const searchInput = document.getElementById('global-search-input');
  const clearBtn = document.getElementById('global-search-clear');
  const retryBtn = document.getElementById('btn-retry-online');
  const installBtn = document.getElementById('btn-install-app');
  const installIcon = document.getElementById('topbar-install-icon');
  const shareBtn = document.getElementById('btn-share-app');

  if (backBtn) {
    backBtn.innerHTML = ICONS.chevronLeft;
    backBtn.addEventListener('click', () => window.history.back());
  }
  if (fwdBtn) {
    fwdBtn.innerHTML = ICONS.chevronRight;
    fwdBtn.addEventListener('click', () => window.history.forward());
  }
  if (searchIcon) searchIcon.innerHTML = ICONS.search;
  if (installIcon) installIcon.innerHTML = ICONS.download;
  if (shareBtn) {
    shareBtn.innerHTML = ICONS.share;
    shareBtn.addEventListener('click', () => shareSonifyApp());
  }

  if (installBtn) {
    if (isStandaloneApp()) {
      installBtn.classList.add('hidden');
    }
    installBtn.addEventListener('click', async () => {
      const env = detectClientEnvironment();
      if (deferredInstallPrompt && !env.isIOS) {
        try {
          deferredInstallPrompt.prompt();
          const choice = await deferredInstallPrompt.userChoice;
          deferredInstallPrompt = null;
          if (choice?.outcome === 'accepted') {
            installBtn.classList.add('hidden');
            return;
          }
        } catch {
          deferredInstallPrompt = null;
        }
      }
      openInstallAppModal({
        deferredInstallPrompt,
        onConsumedPrompt: () => {
          deferredInstallPrompt = null;
        },
      });
    });
  }

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    if (installBtn && !isStandaloneApp()) {
      installBtn.classList.remove('hidden');
    }
  });

  window.addEventListener('appinstalled', () => {
    deferredInstallPrompt = null;
    installBtn?.classList.add('hidden');
  });

  if (searchInput) {
    searchInput.addEventListener('input', () => {
      const q = searchInput.value;
      clearBtn?.classList.toggle('hidden', !q);
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => {
        const trimmed = q.trim();
        if (trimmed) {
          navigate(`#/search?q=${encodeURIComponent(trimmed)}`);
        } else if (getState().route?.name === 'search') {
          navigate('#/search');
        }
      }, 300);
    });

    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        clearTimeout(searchDebounce);
        const trimmed = searchInput.value.trim();
        navigate(trimmed ? `#/search?q=${encodeURIComponent(trimmed)}` : '#/search');
      }
    });
  }

  clearBtn?.addEventListener('click', () => {
    if (searchInput) {
      searchInput.value = '';
      searchInput.focus();
    }
    clearBtn.classList.add('hidden');
    if (getState().route?.name === 'search') {
      navigate('#/search');
    }
  });

  document.querySelectorAll('.region-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      setRegion(btn.dataset.region);
    });
  });

  retryBtn?.addEventListener('click', () => {
    loadHomeData(true);
  });
}

function updateTopbar() {
  const st = getState();
  document.querySelectorAll('.region-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.region === st.region);
  });

  const banner = document.getElementById('offline-banner');
  if (banner) banner.classList.toggle('hidden', !st.offlineMode);

  const searchInput = document.getElementById('global-search-input');
  const clearBtn = document.getElementById('global-search-clear');
  if (searchInput && st.route?.name === 'search') {
    const rq = st.route.query?.q || '';
    if (document.activeElement !== searchInput && searchInput.value !== rq) {
      searchInput.value = rq;
    }
    clearBtn?.classList.toggle('hidden', !searchInput.value);
  }
}

function renderRightDrawer() {
  const drawer = document.getElementById('right-drawer');
  if (!drawer) return;
  const st = getState();

  if (!st.drawerMode) {
    drawer.classList.add('hidden');
    drawer.innerHTML = '';
    return;
  }

  drawer.classList.remove('hidden');
  drawer.innerHTML = `
    <div class="drawer-header">
      <div class="drawer-tabs">
        <button class="pill-btn ${st.drawerMode === 'now-playing' ? 'active' : ''}" data-drawer-tab="now-playing">Now Playing</button>
        <button class="pill-btn ${st.drawerMode === 'queue' ? 'active' : ''}" data-drawer-tab="queue">Queue</button>
        <button class="pill-btn ${st.drawerMode === 'lyrics' ? 'active' : ''}" data-drawer-tab="lyrics">Lyrics</button>
      </div>
      <div style="display:flex; gap:4px; align-items:center;">
        ${
          st.drawerMode === 'lyrics'
            ? `<button id="drawer-expand-lyrics" class="icon-btn" title="Open Full-Stage Lyrics">${ICONS.expand}</button>`
            : ''
        }
        <button id="drawer-close-btn" class="icon-btn" title="Close drawer" aria-label="Close drawer">${ICONS.close}</button>
      </div>
    </div>
    <div id="drawer-body-slot" class="drawer-body"></div>
  `;

  drawer.querySelectorAll('[data-drawer-tab]').forEach((btn) => {
    btn.addEventListener('click', () => setState({ drawerMode: btn.dataset.drawerTab }));
  });
  drawer.querySelector('#drawer-close-btn')?.addEventListener('click', () => closeDrawer());
  drawer.querySelector('#drawer-expand-lyrics')?.addEventListener('click', () => {
    closeDrawer();
    navigate('#/lyrics');
  });

  const bodySlot = drawer.querySelector('#drawer-body-slot');
  if (st.drawerMode === 'now-playing') {
    renderNowPlayingDrawer(bodySlot);
  } else if (st.drawerMode === 'queue') {
    renderQueueBody(bodySlot);
  } else if (st.drawerMode === 'lyrics') {
    renderLyricsDrawer(bodySlot);
  }
}

function renderMainView() {
  const main = document.getElementById('main-content');
  if (!main) return;
  main.onclick = null;
  main.oncontextmenu = null;
  const route = getState().route || { name: 'home', params: {}, query: {} };

  if (route.name === 'home') {
    renderHomeView(main);
  } else if (route.name === 'search') {
    renderSearchView(main);
  } else if (route.name === 'liked') {
    renderLikedView(main);
  } else if (route.name === 'downloads') {
    renderDownloadsView(main);
  } else if (route.name === 'playlist') {
    renderPlaylistView(main, route.params.id);
  } else if (route.name === 'artist') {
    renderArtistView(main, route.params.id);
  } else if (route.name === 'album') {
    renderAlbumView(main, route.params.id);
  } else if (route.name === 'track') {
    renderTrackView(main, route.params.id);
  } else if (route.name === 'mix') {
    renderMixView(main, route.params.id);
  } else if (route.name === 'genre') {
    renderGenreView(main, route.params.slug);
  } else if (route.name === 'stats') {
    renderStatsView(main);
  } else if (route.name === 'history') {
    renderHistoryView(main);
  } else if (route.name === 'lyrics') {
    renderFullStageLyrics(main);
  } else {
    renderHomeView(main);
  }
}

function initKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    const tag = (e.target?.tagName || '').toLowerCase();
    const isTyping = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target?.isContentEditable;

    if (e.key === 'Escape') {
      if (isModalOpen()) closeModal();
      else if (isContextMenuOpen()) closeContextMenu();
      else if (getState().fullScreenMode) setFullScreenMode(false);
      return;
    }

    if (isTyping || e.ctrlKey || e.metaKey || e.altKey) return;

    if (e.code === 'Space') {
      e.preventDefault();
      togglePlay();
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      if (e.shiftKey) playNextTrack(false);
      else seekBy(5);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      if (e.shiftKey) playPrevTrack();
      else seekBy(-5);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setVolume(getState().volume + 0.05);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setVolume(getState().volume - 0.05);
    } else if (e.key === 'm' || e.key === 'M') {
      toggleMute();
    } else if (e.key === 's' || e.key === 'S') {
      toggleShuffle();
    } else if (e.key === 'r' || e.key === 'R') {
      cycleRepeat();
    } else if (e.key === 'l' || e.key === 'L') {
      toggleDrawer('lyrics');
    } else if (e.key === 'q' || e.key === 'Q') {
      toggleDrawer('queue');
    } else if (e.key === 'f' || e.key === 'F') {
      if (getState().currentTrack) {
        setFullScreenMode(!getState().fullScreenMode);
      }
    } else if (e.key === '/') {
      e.preventDefault();
      const inp = document.getElementById('global-search-input');
      inp?.focus();
      inp?.select();
    }
  });
}

async function bootstrap() {
  initTopbar();
  initPlayer();
  initPlayerBar();
  initContextMenuGlobalClose();
  initKeyboardShortcuts();

  subscribe((st, changed) => {
    // Always keep player bar synced (lightweight incremental DOM updates)
    updatePlayerBar();

    // Update topbar when region/offline/route changes
    if (changed.has('region') || changed.has('offlineMode') || changed.has('route')) {
      updateTopbar();
    }

    // Update sidebar when library/route/filter/sort/search/pins/follows/albums/downloads change
    if (
      changed.has('route') ||
      changed.has('playlists') ||
      changed.has('likedIds') ||
      changed.has('followedArtists') ||
      changed.has('savedAlbums') ||
      changed.has('pinnedItems') ||
      changed.has('downloadedIds') ||
      changed.has('libraryFilter') ||
      changed.has('librarySort') ||
      changed.has('librarySearch')
    ) {
      renderSidebar();
    }

    // Trigger lyrics fetch when active track changes
    const curId = st.currentTrack?.id || null;
    if (curId && curId !== lastRenderedTrackId) {
      lastRenderedTrackId = curId;
      loadLyricsForCurrentTrack();
    }

    // Keep rAF lyrics highlighter active whenever playing & lyrics visible
    if (changed.has('isPlaying') || changed.has('drawerMode') || changed.has('route') || changed.has('lyricsData')) {
      ensureLyricsRafLoop();
    }
    if (changed.has('currentTime')) {
      syncActiveLineDOM(false);
      syncFullScreenProgress();
    }

    // Full-Screen Player overlay
    if (
      changed.has('fullScreenMode') ||
      (st.fullScreenMode &&
        (changed.has('currentTrack') ||
          changed.has('isPlaying') ||
          changed.has('likedIds') ||
          changed.has('lyricsData') ||
          changed.has('lyricsLoading') ||
          changed.has('sleepTimer')))
    ) {
      renderFullScreenPlayer();
    }

    // Re-render right drawer only on changes relevant to its active tab
    const shouldRenderDrawer =
      changed.has('drawerMode') ||
      (st.drawerMode === 'now-playing' &&
        (changed.has('currentTrack') ||
          changed.has('likedIds') ||
          changed.has('followedArtistIds') ||
          changed.has('downloadedIds') ||
          changed.has('queue') ||
          changed.has('queueIndex') ||
          changed.has('userQueue') ||
          changed.has('streamMode') ||
          changed.has('streamSource'))) ||
      (st.drawerMode === 'queue' &&
        (changed.has('queueSubTab') ||
          changed.has('currentTrack') ||
          changed.has('isPlaying') ||
          changed.has('queue') ||
          changed.has('queueIndex') ||
          changed.has('userQueue') ||
          changed.has('history'))) ||
      (st.drawerMode === 'lyrics' &&
        (changed.has('currentTrack') ||
          changed.has('lyricsData') ||
          changed.has('lyricsLoading')));

    if (shouldRenderDrawer) {
      renderRightDrawer();
    }

    // Re-render active main view on relevant data/route changes
    const routeName = st.route?.name || 'home';
    let shouldRenderMain = changed.has('route');
    if (!shouldRenderMain) {
      if (routeName === 'lyrics') {
        shouldRenderMain =
          changed.has('currentTrack') ||
          changed.has('lyricsData') ||
          changed.has('lyricsLoading');
      } else if (routeName === 'liked') {
        const filterFocused = document.activeElement?.id === 'liked-filter-input';
        shouldRenderMain =
          !filterFocused &&
          (changed.has('likedIds') ||
            changed.has('likedTracks') ||
            changed.has('downloadedIds') ||
            changed.has('currentTrack') ||
            changed.has('isPlaying'));
      } else if (routeName === 'downloads') {
        shouldRenderMain =
          changed.has('downloadedIds') ||
          changed.has('likedIds') ||
          changed.has('currentTrack') ||
          changed.has('isPlaying');
      } else if (routeName === 'playlist') {
        const inlineFocused = document.activeElement?.id === 'pl-add-search';
        shouldRenderMain =
          !inlineFocused &&
          (changed.has('likedIds') ||
            changed.has('playlists') ||
            changed.has('downloadedIds') ||
            changed.has('currentTrack') ||
            changed.has('isPlaying'));
      } else if (routeName === 'artist') {
        shouldRenderMain =
          changed.has('followedArtistIds') ||
          changed.has('likedIds') ||
          changed.has('currentTrack') ||
          changed.has('isPlaying');
      } else if (routeName === 'album') {
        shouldRenderMain =
          changed.has('savedAlbumIds') ||
          changed.has('likedIds') ||
          changed.has('currentTrack') ||
          changed.has('isPlaying');
      } else if (routeName === 'history') {
        shouldRenderMain =
          changed.has('history') ||
          changed.has('likedIds') ||
          changed.has('currentTrack') ||
          changed.has('isPlaying');
      } else if (routeName === 'stats' || routeName === 'genre' || routeName === 'track' || routeName === 'mix') {
        shouldRenderMain =
          changed.has('likedIds') ||
          changed.has('downloadedIds') ||
          changed.has('currentTrack') ||
          changed.has('isPlaying');
      } else {
        shouldRenderMain =
          changed.has('homeData') ||
          changed.has('madeForYou') ||
          changed.has('region') ||
          changed.has('likedIds') ||
          changed.has('likedTracks') ||
          changed.has('playlists') ||
          (routeName === 'home' && changed.has('history')) ||
          changed.has('currentTrack') ||
          changed.has('isPlaying');
      }
    }

    if (shouldRenderMain) {
      renderMainView();
    }
  });

  // Initial render + backend hydration
  renderSidebar();
  initRouter();
  updateTopbar();
  await hydrateFromBackend();
  syncAudioSettings();
  await loadHomeData(false);
}

bootstrap().catch((err) => {
  console.error('[Sonify] Bootstrap failed:', err);
});

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
