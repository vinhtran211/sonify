// Smart iOS / Phone Install App Modal & Friend Sharing Helper

import { api } from '../api.js';
import { ICONS } from '../icons.js';
import { escapeHtml } from '../lrc.js';
import { closeModal, openModal, showToast } from './modals.js';

export function isStandaloneApp() {
  try {
    return (
      window.matchMedia('(display-mode: standalone)').matches ||
      window.navigator.standalone === true
    );
  } catch {
    return false;
  }
}

export function detectClientEnvironment() {
  const ua = navigator.userAgent || '';
  const isIPadOS = navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1;
  const isIOS = /iPad|iPhone|iPod/i.test(ua) || isIPadOS;
  const isAndroid = /Android/i.test(ua);
  const isInAppBrowser =
    isIOS &&
    /(Zalo|FBAN|FBAV|FB_IAB|Messenger|Instagram|Line\/|TikTok|CriOS|FxiOS|EdgiOS|OPiOS|Twitter)/i.test(ua);
  const isIOSSafari = isIOS && !isInAppBrowser && /Safari/i.test(ua);
  return { isIOS, isAndroid, isInAppBrowser, isIOSSafari };
}

async function copyTextToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to execCommand fallback
    }
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

export async function shareSonifyApp(customUrl = '') {
  const shareUrl = customUrl || `${window.location.origin}/`;
  const payload = {
    title: 'Sonify — Ad-Free Music',
    text: 'Listen to 100% ad-free full tracks & synced lyrics on Sonify!',
    url: shareUrl,
  };

  if (typeof navigator.share === 'function') {
    try {
      await navigator.share(payload);
      return true;
    } catch (err) {
      if (err && err.name === 'AbortError') return false;
    }
  }

  const copied = await copyTextToClipboard(shareUrl);
  if (copied) {
    showToast('Sonify link copied! Send it to your friends');
    return true;
  }
  showToast(shareUrl, 'info', 4000);
  return false;
}

export async function openInstallAppModal({ deferredInstallPrompt = null, onConsumedPrompt = null } = {}) {
  const env = detectClientEnvironment();
  const fallbackOrigin = `${window.location.origin}/`;

  let info = {
    appUrl: fallbackOrigin,
    mobileconfigUrl: '/api/ios/install.mobileconfig',
    testflightUrl: null,
    otaManifestUrl: null,
    otaInstallUrl: null,
    ipaDownloadUrl: 'https://github.com/vinhtran211/sonify/actions/workflows/ios-build.yml',
    altstoreSourceUrl: `${window.location.origin}/api/ios/altstore-source.json`,
    hasDirectNativeInstall: false,
    version: '1.0.0',
    bundleId: 'com.sonify.music',
  };

  try {
    const remote = await api.getIosInstallInfo();
    if (remote && typeof remote === 'object') {
      info = { ...info, ...remote };
    }
  } catch {
    // Keep safe defaults
  }

  const inAppNoticeHtml = env.isInAppBrowser
    ? `
      <div class="install-callout warning">
        <div class="install-callout-title">Open in Safari to install on iPhone</div>
        <p>You're viewing Sonify inside an in-app or non-Safari browser. Copy the link below and open it in <strong>Safari</strong> so iOS can install the app profile directly.</p>
        <button type="button" class="btn-subtle btn-xs" id="install-copy-safari-btn">Copy Link for Safari</button>
      </div>
    `
    : '';

  const nativePromptHtml =
    deferredInstallPrompt && !env.isIOS
      ? `
      <div class="install-section install-section-highlight">
        <div class="install-section-head">
          <span class="install-badge">Instant</span>
          <h3>Install App Directly</h3>
        </div>
        <p class="install-desc">Install Sonify straight to your home screen or desktop in one click.</p>
        <button type="button" class="btn-primary install-cta-btn" id="install-native-pwa-btn">
          ${ICONS.download}<span>1-Tap Install Now</span>
        </button>
      </div>
    `
      : '';

  let nativeIosActionHtml = '';
  if (info.testflightUrl) {
    nativeIosActionHtml = `
      <a class="btn-primary install-cta-btn" href="${escapeHtml(info.testflightUrl)}" target="_blank" rel="noopener noreferrer">
        ${ICONS.smartphone}<span>1-Tap Install via TestFlight</span>
      </a>
    `;
  } else if (info.otaInstallUrl) {
    nativeIosActionHtml = `
      <a class="btn-primary install-cta-btn" href="${escapeHtml(info.otaInstallUrl)}">
        ${ICONS.smartphone}<span>1-Tap Direct iOS Install</span>
      </a>
    `;
  } else {
    nativeIosActionHtml = `
      <div class="install-btn-row">
        <a class="btn-subtle install-secondary-btn" href="${escapeHtml(info.ipaDownloadUrl)}" target="_blank" rel="noopener noreferrer">
          ${ICONS.download}<span>Download Native iOS App (.ipa)</span>
        </a>
        <button type="button" class="btn-subtle btn-xs" id="install-copy-altstore-btn" title="Copy AltStore / SideStore source URL">
          Copy AltStore URL
        </button>
      </div>
      <p class="install-footnote">
        Apple requires a signed TestFlight/Developer certificate or Sideloadly / AltStore to install native <code>.ipa</code> binaries on iPhone.
      </p>
    `;
  }

  openModal(
    `
    <div class="install-modal-header">
      <div class="install-brand-row">
        <span class="install-brand-icon">${ICONS.brand}</span>
        <div>
          <h2 class="modal-title" style="margin:0;">Install Sonify on Phone</h2>
          <div class="install-subtitle">100% Ad-Free &bull; Full Tracks &bull; Synced Lyrics</div>
        </div>
      </div>
      <button type="button" class="icon-btn" id="install-modal-close" aria-label="Close install dialog">${ICONS.close}</button>
    </div>

    ${inAppNoticeHtml}
    ${nativePromptHtml}

    <div class="install-section">
      <div class="install-section-head">
        <span class="install-badge">iPhone &bull; Free &bull; Never Expires</span>
        <h3>Instant iPhone Install</h3>
      </div>
      <p class="install-desc">Install Sonify directly to your iPhone Home Screen as a full-screen app — $0, no computer required, and never expires.</p>
      <a class="btn-primary install-cta-btn" id="install-mobileconfig-btn" href="${escapeHtml(info.mobileconfigUrl)}">
        ${ICONS.download}<span>Install Directly to iPhone</span>
      </a>
      <ol class="install-steps">
        <li>Tap <strong>Allow</strong> when Safari asks to download the profile</li>
        <li>Open iPhone <strong>Settings</strong> &rarr; <strong>Profile Downloaded</strong></li>
        <li>Tap <strong>Install</strong> &mdash; Sonify appears on your Home Screen</li>
      </ol>
      <div class="install-alt-tip">
        Or 2-tap in Safari: tap <strong>Share &uarr;</strong> &rarr; <strong>Add to Home Screen</strong> &rarr; <strong>Add</strong>.
      </div>
    </div>

    <div class="install-section">
      <div class="install-section-head">
        <span class="install-badge muted">SwiftUI Native</span>
        <h3>Native iOS App (Background Audio &amp; Lock Screen)</h3>
      </div>
      <p class="install-desc">Full native SwiftUI player with Lock Screen controls, Dynamic Island scrubbing, and AirPlay.</p>
      ${nativeIosActionHtml}
    </div>

    <div class="install-section install-share-box">
      <div class="install-section-head">
        <h3>Share with Friends</h3>
      </div>
      <div class="install-share-row">
        <code class="install-url-chip truncate">${escapeHtml(info.appUrl)}</code>
        <button type="button" class="btn-primary btn-xs install-share-cta" id="install-share-link-btn">
          ${ICONS.share}<span>Share Link</span>
        </button>
      </div>
    </div>
    `,
    (root) => {
      root.querySelector('#install-modal-close')?.addEventListener('click', closeModal);

      root.querySelector('#install-copy-safari-btn')?.addEventListener('click', async () => {
        const ok = await copyTextToClipboard(info.appUrl);
        showToast(ok ? 'Link copied! Paste it into Safari' : info.appUrl, ok ? 'success' : 'info');
      });

      root.querySelector('#install-native-pwa-btn')?.addEventListener('click', async () => {
        if (!deferredInstallPrompt) return;
        try {
          deferredInstallPrompt.prompt();
          const choice = await deferredInstallPrompt.userChoice;
          if (onConsumedPrompt) onConsumedPrompt();
          if (choice?.outcome === 'accepted') {
            closeModal();
            showToast('Installing Sonify...');
          }
        } catch {
          showToast('Use your browser menu to install Sonify', 'info');
        }
      });

      root.querySelector('#install-mobileconfig-btn')?.addEventListener('click', () => {
        showToast('Profile downloading — tap Allow, then Settings → Profile Downloaded', 'info', 4200);
      });

      root.querySelector('#install-copy-altstore-btn')?.addEventListener('click', async () => {
        const ok = await copyTextToClipboard(info.altstoreSourceUrl);
        showToast(ok ? 'AltStore source URL copied!' : info.altstoreSourceUrl, ok ? 'success' : 'info');
      });

      root.querySelector('#install-share-link-btn')?.addEventListener('click', () => {
        shareSonifyApp(info.appUrl);
      });
    },
    'install-modal-card'
  );
}
