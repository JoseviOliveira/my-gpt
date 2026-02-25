/*
 * tts-settings.js — shared text-to-speech preferences
 * - Stores speech output mode (browser SpeechSynthesis vs backend Coqui)
 * - Mirrors the STT helper for consistent pub/sub + config loading
 */
(function(global){
  const store = global.localStorage;
  const TTS_MODE_KEY = 'ttsMode';
  const DEFAULT_MODES = ['browser', 'coqui'];

  const modeListeners = new Set();
  let allowedModes = DEFAULT_MODES.slice();
  let hasStoredMode = false;

  const configPromise = typeof global.getAppConfig === 'function'
    ? global.getAppConfig()
    : Promise.resolve(global.APP_CONFIG || {});

  const normalizeMode = (value) => {
    const v = String(value || '').trim().toLowerCase();
    return allowedModes.includes(v) ? v : '';
  };

  const setAllowedModes = (list) => {
    if (!Array.isArray(list) || !list.length) {
      allowedModes = DEFAULT_MODES.slice();
      return;
    }
    const next = [];
    for (const item of list) {
      const v = String(item || '').trim().toLowerCase();
      if (!v || next.includes(v)) continue;
      if (!DEFAULT_MODES.includes(v)) continue;
      next.push(v);
    }
    allowedModes = next.length ? next : DEFAULT_MODES.slice();
  };

  const readStoredMode = () => {
    if (!store) return '';
    try {
      const raw = store.getItem(TTS_MODE_KEY);
      const normalized = normalizeMode(raw);
      hasStoredMode = !!normalized;
      return normalized;
    } catch {
      return '';
    }
  };

  const readConfigMode = () => {
    const cfg = global.APP_CONFIG || {};
    return normalizeMode(cfg.tts_mode ?? cfg.ttsMode);
  };

  let currentMode = readStoredMode() || readConfigMode() || DEFAULT_MODES[0];

  const logModeChange = (prevMode, nextMode, source) => {
    if (prevMode === nextMode) return;
    try {
      console.log('[tts] mode change', { from: prevMode || null, to: nextMode, source });
    } catch {}
  };

  const notifyModeChange = (value) => {
    modeListeners.forEach((fn) => {
      try { fn(value); }
      catch (err) { console.warn('[tts] mode listener error', err); }
    });
  };

  const getMode = () => currentMode;

  const setMode = (next, { persist = true } = {}) => {
    const normalized = normalizeMode(next);
    if (!normalized) {
      try {
        console.warn('[tts] setMode ignored invalid value', {
          requested: next,
          allowed: allowedModes.slice()
        });
      } catch {}
      return currentMode;
    }
    if (normalized === currentMode) return currentMode;
    const prev = currentMode;
    currentMode = normalized;
    logModeChange(prev, currentMode, persist ? 'setMode:persistent' : 'setMode:transient');
    if (persist && store) {
      try { store.setItem(TTS_MODE_KEY, currentMode); }
      catch {}
      hasStoredMode = true;
    }
    notifyModeChange(currentMode);
    return currentMode;
  };

  const onModeChange = (listener, { immediate = true } = {}) => {
    if (typeof listener !== 'function') return () => {};
    modeListeners.add(listener);
    if (immediate) {
      try { listener(currentMode); }
      catch (err) { console.warn('[tts] mode listener error', err); }
    }
    return () => modeListeners.delete(listener);
  };

  configPromise.then((cfg) => {
    if (cfg && Array.isArray(cfg.tts_modes) && cfg.tts_modes.length) {
      const prevAllowed = allowedModes.slice().join('|');
      setAllowedModes(cfg.tts_modes);
      if (prevAllowed !== allowedModes.slice().join('|')) {
        if (!normalizeMode(currentMode)) {
          const prev = currentMode;
          currentMode = allowedModes[0];
          logModeChange(prev, currentMode, 'config:allowed-update');
          notifyModeChange(currentMode);
        }
      }
    }
    const cfgMode = normalizeMode(cfg && (cfg.tts_mode ?? cfg.ttsMode));
    if (!hasStoredMode && cfgMode && cfgMode !== currentMode) {
      const prev = currentMode;
      currentMode = cfgMode;
      logModeChange(prev, currentMode, 'config:preferred');
      notifyModeChange(currentMode);
    } else if (!normalizeMode(currentMode)) {
      const prev = currentMode;
      currentMode = allowedModes[0];
      logModeChange(prev, currentMode, 'config:fallback');
      notifyModeChange(currentMode);
    }
  }).catch(() => {
    if (!normalizeMode(currentMode)) {
      const prev = currentMode;
      currentMode = DEFAULT_MODES[0];
      logModeChange(prev, currentMode, 'config:error-fallback');
      notifyModeChange(currentMode);
    }
  });

  global.TTS = Object.assign(global.TTS || {}, {
    getMode,
    setMode,
    onModeChange,
    getAvailableModes: () => allowedModes.slice(),
    MODE_KEY: TTS_MODE_KEY,
  });

  try {
    if (typeof document !== 'undefined' && document) {
      document.dispatchEvent(new CustomEvent('tts:ready', {
        detail: { modes: allowedModes.slice() }
      }));
    }
  } catch {}
})(window);
