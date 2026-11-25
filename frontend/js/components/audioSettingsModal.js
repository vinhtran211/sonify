// Audio Settings Modal — 5-Band Equalizer, Crossfade, Autoplay, Audio Quality, Speed & Sleep Timer

import { EQ_LABELS, EQ_PRESETS } from '../audioEffects.js';
import { ICONS } from '../icons.js';
import { escapeHtml, formatTime } from '../lrc.js';
import {
  applyEqSettings,
  cancelSleepTimer,
  setPlaybackRate,
  setSleepTimerEndOfTrack,
  setSleepTimerMinutes,
} from '../player.js';
import { getState, updateAudioSettings } from '../store.js';
import { closeModal, openModal, showToast } from './modals.js';

const SPEED_OPTIONS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0];
const SLEEP_OPTIONS = [
  { label: 'Off', mode: 'off' },
  { label: '5 min', mins: 5 },
  { label: '15 min', mins: 15 },
  { label: '30 min', mins: 30 },
  { label: '45 min', mins: 45 },
  { label: '1 hr', mins: 60 },
  { label: 'End of Track', mode: 'end-of-track' },
];

export function openAudioSettingsModal() {
  const render = () => {
    const st = getState();
    const bands = Array.isArray(st.eqBands) && st.eqBands.length === 5 ? st.eqBands : [0, 0, 0, 0, 0];
    const sleepLabel =
      st.sleepTimer?.mode === 'countdown'
        ? `Active — ${formatTime(st.sleepTimer.remainingSec)} left`
        : st.sleepTimer?.mode === 'end-of-track'
        ? 'Active — End of current track'
        : 'Off';

    openModal(
      `
      <div class="audio-modal-head">
        <div>
          <h2 class="modal-title" style="margin:0;">Audio & Playback Settings</h2>
          <p class="audio-modal-sub">100% Ad-Free · Equalizer, Crossfade, Autoplay & Sleep Timer</p>
        </div>
        <button id="audio-modal-close" class="icon-btn" aria-label="Close">${ICONS.close}</button>
      </div>

      <div class="audio-settings-sections">
        <!-- 5-Band Equalizer -->
        <section class="audio-sec">
          <div class="audio-sec-header">
            <h3>Equalizer (5-Band)</h3>
            <span class="audio-sec-badge">${escapeHtml(EQ_PRESETS[st.eqPreset]?.label || 'Custom')}</span>
          </div>
          <div class="eq-presets-row">
            ${Object.entries(EQ_PRESETS)
              .map(
                ([key, p]) => `
              <button type="button" class="pill-btn eq-preset-btn ${st.eqPreset === key ? 'active' : ''}" data-eq-preset="${key}">
                ${escapeHtml(p.label)}
              </button>
            `
              )
              .join('')}
          </div>
          <div class="eq-sliders-grid">
            ${EQ_LABELS.map(
              (freq, i) => `
              <div class="eq-band-col">
                <span class="eq-band-val" data-eq-val="${i}">${bands[i] > 0 ? `+${bands[i]}` : bands[i]} dB</span>
                <input
                  type="range"
                  min="-12"
                  max="12"
                  step="1"
                  value="${bands[i]}"
                  class="eq-slider"
                  data-eq-band="${i}"
                  aria-label="${freq} band"
                />
                <span class="eq-band-freq">${freq}</span>
              </div>
            `
            ).join('')}
          </div>
        </section>

        <!-- Crossfade & Autoplay -->
        <section class="audio-sec">
          <div class="audio-toggle-row">
            <div>
              <strong>Autoplay Similar Songs</strong>
              <div class="audio-help">Automatically queue radio tracks when your playlist ends</div>
            </div>
            <label class="switch-toggle">
              <input type="checkbox" id="toggle-autoplay" ${st.autoplay ? 'checked' : ''} />
              <span class="switch-track"></span>
            </label>
          </div>

          <div class="audio-slider-row">
            <div class="audio-sec-header" style="margin-bottom:6px;">
              <strong>Crossfade Songs</strong>
              <span id="crossfade-val" class="audio-sec-badge">${st.crossfade > 0 ? `${st.crossfade}s` : 'Off'}</span>
            </div>
            <input type="range" id="crossfade-slider" min="0" max="12" step="1" value="${st.crossfade || 0}" class="range-full" />
          </div>
        </section>

        <!-- Audio Quality & Playback Speed -->
        <section class="audio-sec">
          <div class="audio-sec-header">
            <h3>Streaming Quality</h3>
          </div>
          <div class="eq-presets-row">
            ${[
              { id: 'auto', label: 'Auto (Recommended)' },
              { id: 'lossless', label: 'Lossless FLAC' },
              { id: 'fast', label: 'Fast Standard' },
            ]
              .map(
                (q) => `
              <button type="button" class="pill-btn ${st.audioQuality === q.id ? 'active' : ''}" data-quality="${q.id}">
                ${q.label}
              </button>
            `
              )
              .join('')}
          </div>

          <div class="audio-sec-header" style="margin-top:14px;">
            <h3>Playback Speed</h3>
            <span class="audio-sec-badge">${Number(st.playbackRate || 1).toFixed(2).replace(/\.00$/, '')}x</span>
          </div>
          <div class="eq-presets-row">
            ${SPEED_OPTIONS.map(
              (spd) => `
              <button type="button" class="pill-btn ${Math.abs((st.playbackRate || 1) - spd) < 0.01 ? 'active' : ''}" data-speed="${spd}">
                ${spd}x
              </button>
            `
            ).join('')}
          </div>
        </section>

        <!-- Sleep Timer -->
        <section class="audio-sec">
          <div class="audio-sec-header">
            <h3>${ICONS.moon} Sleep Timer</h3>
            <span class="audio-sec-badge">${escapeHtml(sleepLabel)}</span>
          </div>
          <div class="eq-presets-row">
            ${SLEEP_OPTIONS.map((opt) => {
              const isAct =
                (opt.mode === 'off' && st.sleepTimer?.mode === 'off') ||
                (opt.mode === 'end-of-track' && st.sleepTimer?.mode === 'end-of-track');
              return `<button type="button" class="pill-btn ${isAct ? 'active' : ''}" data-sleep-mode="${opt.mode || 'mins'}" data-sleep-mins="${opt.mins || 0}">${opt.label}</button>`;
            }).join('')}
          </div>
        </section>
      </div>
      `,
      (root) => {
        root.querySelector('#audio-modal-close')?.addEventListener('click', closeModal);

        root.querySelectorAll('[data-eq-preset]').forEach((btn) => {
          btn.addEventListener('click', () => {
            const key = btn.dataset.eqPreset;
            const preset = EQ_PRESETS[key];
            if (!preset) return;
            applyEqSettings(preset.bands, key);
            render();
          });
        });

        root.querySelectorAll('[data-eq-band]').forEach((slider) => {
          slider.addEventListener('input', () => {
            const idx = Number(slider.dataset.eqBand);
            const val = Number(slider.value);
            const next = [...getState().eqBands];
            next[idx] = val;
            const lbl = root.querySelector(`[data-eq-val="${idx}"]`);
            if (lbl) lbl.textContent = `${val > 0 ? `+${val}` : val} dB`;
            applyEqSettings(next, 'custom');
          });
        });

        root.querySelector('#toggle-autoplay')?.addEventListener('change', (e) => {
          updateAudioSettings({ autoplay: Boolean(e.target.checked) });
          showToast(e.target.checked ? 'Autoplay enabled' : 'Autoplay disabled', 'info', 1800);
        });

        const cfSlider = root.querySelector('#crossfade-slider');
        const cfVal = root.querySelector('#crossfade-val');
        cfSlider?.addEventListener('input', () => {
          const secs = Number(cfSlider.value);
          if (cfVal) cfVal.textContent = secs > 0 ? `${secs}s` : 'Off';
          updateAudioSettings({ crossfade: secs });
        });

        root.querySelectorAll('[data-quality]').forEach((btn) => {
          btn.addEventListener('click', () => {
            const qual = btn.dataset.quality;
            updateAudioSettings({ audioQuality: qual });
            showToast(`Audio quality set to ${qual}`, 'success', 1800);
            render();
          });
        });

        root.querySelectorAll('[data-speed]').forEach((btn) => {
          btn.addEventListener('click', () => {
            const spd = Number(btn.dataset.speed);
            setPlaybackRate(spd);
            render();
          });
        });

        root.querySelectorAll('[data-sleep-mode]').forEach((btn) => {
          btn.addEventListener('click', () => {
            const mode = btn.dataset.sleepMode;
            if (mode === 'off') cancelSleepTimer(true);
            else if (mode === 'end-of-track') setSleepTimerEndOfTrack();
            else setSleepTimerMinutes(Number(btn.dataset.sleepMins));
            render();
          });
        });
      },
      'modal-card-lg'
    );
  };

  render();
}
