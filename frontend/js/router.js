// Lightweight Hash Router for Sonify SPA

import { setState } from './store.js';

export function parseHash(hashStr = window.location.hash) {
  const cleaned = (hashStr || '#/').replace(/^#/, '') || '/';
  const [rawPath, rawQuery = ''] = cleaned.split('?');
  const path = rawPath.startsWith('/') ? rawPath : `/${rawPath}`;
  const query = {};
  const sp = new URLSearchParams(rawQuery);
  for (const [k, v] of sp.entries()) {
    query[k] = v;
  }

  if (path === '/' || path === '/home') {
    return { name: 'home', path: '/', params: {}, query };
  }
  if (path === '/search') {
    return { name: 'search', path: '/search', params: {}, query };
  }
  if (path === '/liked') {
    return { name: 'liked', path: '/liked', params: {}, query };
  }
  if (path === '/history') {
    return { name: 'history', path: '/history', params: {}, query };
  }
  if (path === '/lyrics') {
    return { name: 'lyrics', path: '/lyrics', params: {}, query };
  }
  if (path === '/stats') {
    return { name: 'stats', path: '/stats', params: {}, query };
  }
  if (path === '/downloads') {
    return { name: 'downloads', path: '/downloads', params: {}, query };
  }
  if (path.startsWith('/genre/')) {
    return {
      name: 'genre',
      path,
      params: { slug: decodeURIComponent(path.slice('/genre/'.length)) },
      query,
    };
  }
  if (path.startsWith('/track/')) {
    return {
      name: 'track',
      path,
      params: { id: decodeURIComponent(path.slice('/track/'.length)) },
      query,
    };
  }
  if (path.startsWith('/mix/')) {
    return {
      name: 'mix',
      path,
      params: { id: decodeURIComponent(path.slice('/mix/'.length)) },
      query,
    };
  }
  if (path.startsWith('/playlist/')) {
    return {
      name: 'playlist',
      path,
      params: { id: decodeURIComponent(path.slice('/playlist/'.length)) },
      query,
    };
  }
  if (path.startsWith('/artist/')) {
    return {
      name: 'artist',
      path,
      params: { id: decodeURIComponent(path.slice('/artist/'.length)) },
      query,
    };
  }
  if (path.startsWith('/album/')) {
    return {
      name: 'album',
      path,
      params: { id: decodeURIComponent(path.slice('/album/'.length)) },
      query,
    };
  }
  return { name: 'home', path: '/', params: {}, query };
}

export function navigate(targetHash) {
  const normalized = targetHash.startsWith('#') ? targetHash : `#${targetHash}`;
  if (window.location.hash === normalized) {
    setState({ route: parseHash(normalized) });
  } else {
    window.location.hash = normalized;
  }
}

export function initRouter() {
  const onChange = () => {
    const route = parseHash(window.location.hash);
    setState({ route });
  };
  window.addEventListener('hashchange', onChange);
  onChange();
}
