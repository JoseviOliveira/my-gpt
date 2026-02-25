/*
 * stt.js — shared speech-to-text utilities
 * - Detects language heuristically across STT/TTS modules
 * - Maintains stored microphone language + mode preferences
 * - Provides cycling helpers and mode-change pub/sub for STT flows
 */
(function(global){
  const store = global.localStorage;
  const STT_LANG_KEY = 'sttLang';
  const STT_MODE_KEY = 'sttMode';
  const STT_CYCLE = ['en-US','fr-FR','es-ES'];
  const DEFAULT_MODES = ['browser', 'whisper'];
  let allowedModes = DEFAULT_MODES.slice();
  const modeListeners = new Set();
  let hasStoredMode = false;

  const configPromise = typeof global.getAppConfig === 'function'
    ? global.getAppConfig()
    : Promise.resolve(global.APP_CONFIG || {});

  function getStoredLang(){
    const v = store ? store.getItem(STT_LANG_KEY) : null;
    return STT_CYCLE.includes(v) ? v : STT_CYCLE[0];
  }

  function setStoredLang(value){
    if (store) store.setItem(STT_LANG_KEY, value);
  }

  function cycleLang(current){
    const idx = STT_CYCLE.indexOf(current);
    const nextIdx = idx === -1 ? 0 : (idx + 1) % STT_CYCLE.length;
    return STT_CYCLE[nextIdx];
  }

  function setAllowedModes(list){
    if (!Array.isArray(list) || !list.length) {
      allowedModes = DEFAULT_MODES.slice();
      return;
    }
    const next = [];
    for (const item of list) {
      const candidate = String(item || '').trim().toLowerCase();
      if (!candidate) continue;
      if (next.includes(candidate)) continue;
      next.push(candidate);
    }
    allowedModes = next.length ? next : DEFAULT_MODES.slice();
  }

  function normalizeMode(value){
    const v = String(value || '').trim().toLowerCase();
    return allowedModes.includes(v) ? v : '';
  }

  function readStoredMode(){
    if (!store) return '';
    try {
      const raw = store.getItem(STT_MODE_KEY);
      const normalized = normalizeMode(raw);
      hasStoredMode = !!normalized;
      return normalized;
    } catch {
      return '';
    }
  }

  function readConfigMode(){
    const cfg = global.APP_CONFIG || {};
    return normalizeMode(cfg.stt_mode ?? cfg.sttMode);
  }

  let currentMode = readStoredMode() || readConfigMode() || DEFAULT_MODES[0];
  function logModeChange(prevMode, nextMode, source){
    if (prevMode === nextMode) return;
    try {
      console.log('[stt] mode change', { from: prevMode || null, to: nextMode, source });
    } catch {}
  }

  function notifyModeChange(mode){
    modeListeners.forEach((fn) => {
      try { fn(mode); }
      catch (err) { console.warn('[stt] mode listener error', err); }
    });
  }

  function getMode(){
    return currentMode;
  }

  function setMode(next, { persist = true } = {}){
    const normalized = normalizeMode(next);
    if (!normalized) {
      try {
        console.warn('[stt] setMode ignored invalid mode', {
          requested: next,
          allowed: allowedModes.slice()
        });
      } catch {}
      return currentMode;
    }
    if (normalized === currentMode) return currentMode;
    const prevMode = currentMode;
    currentMode = normalized;
    logModeChange(prevMode, currentMode, persist ? 'setMode:persistent' : 'setMode:transient');
    if (persist && store) {
      try { store.setItem(STT_MODE_KEY, currentMode); }
      catch {}
      hasStoredMode = true;
    }
    notifyModeChange(currentMode);
    return currentMode;
  }

  function onModeChange(listener, { immediate = true } = {}){
    if (typeof listener !== 'function') return () => {};
    modeListeners.add(listener);
    if (immediate) {
      try { listener(currentMode); }
      catch (err) { console.warn('[stt] mode listener error', err); }
    }
    return () => {
      modeListeners.delete(listener);
    };
  }

  configPromise.then((cfg) => {
    let allowedChanged = false;
    if (cfg && Array.isArray(cfg.stt_modes) && cfg.stt_modes.length) {
      const prevAllowed = allowedModes.slice().join('|');
      setAllowedModes(cfg.stt_modes);
      allowedChanged = allowedModes.slice().join('|') !== prevAllowed;
    }
    let modeChanged = false;
    const cfgMode = normalizeMode(cfg && (cfg.stt_mode ?? cfg.sttMode));
    if (!hasStoredMode && cfgMode) {
      if (cfgMode !== currentMode) {
        const prevMode = currentMode;
        currentMode = cfgMode;
        logModeChange(prevMode, currentMode, 'config:preferred');
        notifyModeChange(currentMode);
        modeChanged = true;
      }
    } else if (!normalizeMode(currentMode)) {
      const prevMode = currentMode;
      currentMode = allowedModes[0];
      logModeChange(prevMode, currentMode, 'config:fallback');
      notifyModeChange(currentMode);
      modeChanged = true;
    }
    if (allowedChanged && !modeChanged) {
      notifyModeChange(currentMode);
    }
  }).catch(() => {
    if (!normalizeMode(currentMode)) {
      const prevMode = currentMode;
      currentMode = DEFAULT_MODES[0];
      logModeChange(prevMode, currentMode, 'config:error-fallback');
      notifyModeChange(currentMode);
    }
  });

  global.STT = Object.assign(global.STT || {}, {
    getStoredLang,
    setStoredLang,
    cycleLang,
    LANG_KEY: STT_LANG_KEY,
    CYCLE: STT_CYCLE,
    getMode,
    setMode,
    onModeChange,
    getAvailableModes: () => allowedModes.slice(),
    MODE_KEY: STT_MODE_KEY,
  });
  try {
    if (typeof document !== 'undefined' && document) {
      document.dispatchEvent(new CustomEvent('stt:ready', {
        detail: {
          mode: currentMode,
          modes: allowedModes.slice(),
        }
      }));
    }
  } catch {}
})(window);
