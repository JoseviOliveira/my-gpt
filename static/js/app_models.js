/*
 * app_models.js — Model selection and preferences management
 * Extracted from app.js to reduce file size
 */
(function(global){
  const store = global.localStorage;
  const rootEl = document.documentElement;

  // Font preferences
  const FONT_PREF_KEY = 'ui.fontFamily.v1';
  const FONT_SIZE_PREF_KEY = 'ui.fontSize.v1';
  const FONT_STACKS = {
    inter: '"Inter", system-ui, sans-serif',
    roboto: '"Roboto", "Inter", system-ui, sans-serif',
    'inter-ra': '"Inter", system-ui, sans-serif',
    'ibm-plex': '"IBM Plex Sans", "Inter", system-ui, sans-serif',
  };
  const DEFAULT_FONT = 'inter';
  const FONT_SCALE = {
    small: { size: '13px', lineHeight: '1.5' },
    medium: { size: '14px', lineHeight: '1.55' },
    large: { size: '16px', lineHeight: '1.6' },
  };
  const DEFAULT_FONT_SIZE = 'medium';

  // Model preferences
  const MODEL_STORAGE_KEY = 'modeModels.v1';
  const MODEL_OPTIONS = ['deepseek-r1:8b', 'deepseek-r1:14b', 'gemma3:4b', 'gemma3:12b', 'qwen3:8b', 'qwen3:14b', 'magistral:24b', 'gpt-oss:20b'];
  const MODEL_DETAILS = {
    'deepseek-r1:8b': { owner: 'DeepSeek', country: 'CN', date: '2025-01', vramGB: 4.7 },
    'deepseek-r1:14b': { owner: 'DeepSeek', country: 'CN', date: '2025-01', vramGB: 8.0 },
    'gemma3:4b': { owner: 'Google', country: 'US', date: '2025-03', vramGB: 2.4 },
    'gemma3:12b': { owner: 'Google', country: 'US', date: '2025-03', vramGB: 6.8 },
    'qwen3:8b': { owner: 'Alibaba', country: 'CN', date: '2025-05', vramGB: 4.7 },
    'qwen3:14b': { owner: 'Alibaba', country: 'CN', date: '2025-05', vramGB: 8.0 },
    'magistral:24b': { owner: 'Mistral AI', country: 'FR', date: '2025-06', vramGB: 13.5 },
    'gpt-oss:20b': { owner: 'OpenAI', country: 'US', date: '2025-08', vramGB: 11.5 },
  };
  const DEFAULT_MODELS = {
    fast: 'deepseek-r1:8b',
    normal: 'gemma3:4b',
    deep: 'magistral:24b',
  };
  const SYSTEM_VRAM_GB = 24;  // Mac Mini M4 Pro unified memory

  const fontRadios = Array.from(document.querySelectorAll('input[name="settings-font"]'));
  const fontSizeRadios = Array.from(document.querySelectorAll('input[name="settings-font-size"]'));
  const modelSelects = Array.from(document.querySelectorAll('select[data-model-mode]'));
  const modelHintEl = document.getElementById('modelHintCombined');

  const modelSelectMap = new Map();
  modelSelects.forEach((selectEl) => {
    const key = (selectEl?.dataset?.modelMode || '').toLowerCase();
    if (key) modelSelectMap.set(key, selectEl);
  });

  let fontPref = DEFAULT_FONT;
  let fontSizePref = DEFAULT_FONT_SIZE;
  let modelSelections = {};
  let modelOptionAllowlist = null;
  let allowedModeSet = null;

  function applyFontFamilyPref(value){
    const key = Object.prototype.hasOwnProperty.call(FONT_STACKS, value) ? value : DEFAULT_FONT;
    const stack = FONT_STACKS[key];
    if (rootEl && stack) rootEl.style.setProperty('--font-sans', stack);
    fontRadios.forEach((radio) => {
      const match = radio.value === key;
      radio.checked = match;
      radio.closest('.toggle-option')?.classList.toggle('active', match);
    });
    return key;
  }

  function applyFontSizePref(value){
    const key = Object.prototype.hasOwnProperty.call(FONT_SCALE, value) ? value : DEFAULT_FONT_SIZE;
    const { size, lineHeight } = FONT_SCALE[key];
    if (rootEl) {
      rootEl.style.setProperty('--app-font-size', size);
      rootEl.style.setProperty('--app-line-height', lineHeight);
    }
    fontSizeRadios.forEach((radio) => {
      const match = radio.value === key;
      radio.checked = match;
      radio.closest('.toggle-option')?.classList.toggle('active', match);
    });
    return key;
  }

  function normalizeModelValue(value){
    const clean = (value || '').trim();
    if (!clean) return null;
    if (modelOptionAllowlist) {
      return modelOptionAllowlist.includes(clean) ? clean : null;
    }
    return MODEL_OPTIONS.includes(clean) ? clean : null;
  }

  function loadModelPrefs(){
    const merged = { ...DEFAULT_MODELS };
    if (!store) return merged;
    let saved = null;
    try {
      saved = JSON.parse(store.getItem(MODEL_STORAGE_KEY) || '{}');
    } catch {
      saved = null;
    }
    if (saved && typeof saved === 'object') {
      Object.keys(saved).forEach((key) => {
        const normalizedMode = (key || '').toLowerCase();
        const normalizedValue = normalizeModelValue(saved[key]);
        if (normalizedMode && normalizedValue) {
          merged[normalizedMode] = normalizedValue;
        }
      });
    }
    return merged;
  }

  function persistModelPrefs(){
    if (!store) return;
    try { store.setItem(MODEL_STORAGE_KEY, JSON.stringify(modelSelections)); }
    catch {}
  }

  function syncModelSelects(){
    if (!modelSelects.length) return;
    modelSelects.forEach((selectEl) => {
      const key = (selectEl?.dataset?.modelMode || '').toLowerCase();
      if (!key) return;
      const preferred = modelSelections[key] || DEFAULT_MODELS[key];
      if (preferred) selectEl.value = preferred;
    });
  }

  function getModelDetailText(model){
    const detail = MODEL_DETAILS[model];
    if (!detail) return 'Owner/date unavailable.';
    const country = detail.country || '??';
    return `${detail.owner} (${country}) ${detail.date}`;
  }

  function calculateTotalVRAM(){
    const fastModel = getModelForMode('fast');
    const normalModel = getModelForMode('normal');
    const deepModel = getModelForMode('deep');
    
    const uniqueModels = new Set([fastModel, normalModel, deepModel]);
    let totalVRAM = 0;
    uniqueModels.forEach(model => {
      const detail = MODEL_DETAILS[model];
      if (detail && detail.vramGB) {
        totalVRAM += detail.vramGB;
      }
    });
    return { totalVRAM: totalVRAM, uniqueModels: uniqueModels };
  }

  function refreshModelHint(modeKey){
    if (!modelHintEl) return;
    const finalModel = getModelForMode(modeKey);
    if (!finalModel) {
      modelHintEl.textContent = '';
      return;
    }
    
    // Calculate VRAM usage across all modes
    const vramCalc = calculateTotalVRAM();
    const totalVRAM = vramCalc.totalVRAM;
    const uniqueModels = vramCalc.uniqueModels;
    const detail = MODEL_DETAILS[finalModel];
    const modelInfo = finalModel + ', by ' + getModelDetailText(finalModel);
    
    // Show warning/hint without extra model info to keep it short.
    if (uniqueModels.size > 1 && totalVRAM > SYSTEM_VRAM_GB) {
      const overageGB = (totalVRAM - SYSTEM_VRAM_GB).toFixed(1);
      modelHintEl.innerHTML = '<span style="color: var(--error-text, #ff6b6b);">⚠️ VRAM ' + totalVRAM.toFixed(1) + 'GB (+' + overageGB + '). Mode switch reloads (15-45s).</span>';
    } else if (uniqueModels.size > 1) {
      modelHintEl.innerHTML = '<span style="color: var(--toggle-ink, #888);">💡 VRAM ' + totalVRAM.toFixed(1) + 'GB / ' + SYSTEM_VRAM_GB + 'GB (' + uniqueModels.size + ' models).</span>';
    } else {
      modelHintEl.textContent = modelInfo;
    }
  }

  function getModelForMode(modeKey){
    const normalized = (modeKey || '').toLowerCase();
    const selectEl = modelSelectMap.get(normalized);
    const selectValue = normalizeModelValue(selectEl?.value);
    if (selectValue) return selectValue;
    if (modelSelections[normalized]) return modelSelections[normalized];
    if (DEFAULT_MODELS[normalized]) return DEFAULT_MODELS[normalized];
    return MODEL_OPTIONS[0];
  }

  function applyRestrictions({ allowedModels, allowedModes, modelDefaults } = {}){
    if (Array.isArray(allowedModels) && allowedModels.length) {
      const filtered = allowedModels.filter((entry) => entry && MODEL_OPTIONS.includes(entry));
      modelOptionAllowlist = filtered.length ? filtered : null;
    } else {
      modelOptionAllowlist = null;
    }
    if (Array.isArray(allowedModes) && allowedModes.length) {
      allowedModeSet = new Set(allowedModes.map((entry) => String(entry).toLowerCase()));
    } else {
      allowedModeSet = null;
    }

    if (modelOptionAllowlist && modelSelects.length) {
      modelSelects.forEach((selectEl) => {
        Array.from(selectEl.options).forEach((opt) => {
          if (!modelOptionAllowlist.includes(opt.value)) opt.remove();
        });
      });
    }

    modelSelects.forEach((selectEl) => {
      const key = (selectEl?.dataset?.modelMode || '').toLowerCase();
      const allowed = !allowedModeSet || allowedModeSet.has(key);
      selectEl.disabled = !allowed;
      if (!allowed) {
        selectEl.title = 'Mode not available';
        return;
      }
      const preferredDefault = (modelDefaults && modelDefaults[key]) || DEFAULT_MODELS[key] || MODEL_OPTIONS[0];
      const preferred = modelSelections[key] || preferredDefault;
      const normalized = normalizeModelValue(preferred) || (modelOptionAllowlist?.[0] || MODEL_OPTIONS[0]);
      selectEl.value = normalized;
      modelSelections[key] = normalized;
    });

    persistModelPrefs();
    refreshModelHint('normal');
  }

  function attachModelSelectHandlers(updateModeButtonsFn){
    if (!modelSelects.length) return;
    modelSelects.forEach((selectEl) => {
      selectEl.addEventListener('change', () => {
        const key = (selectEl?.dataset?.modelMode || '').toLowerCase();
        if (!key) return;
        let next = normalizeModelValue(selectEl.value) || DEFAULT_MODELS[key] || MODEL_OPTIONS[0];
        const prev = modelSelections[key] || DEFAULT_MODELS[key] || MODEL_OPTIONS[0];
        if (selectEl.value !== next) selectEl.value = next;
        if (next === prev) return;
        modelSelections[key] = next;
        persistModelPrefs();
        console.log('[models] selection change', { mode: key, from: prev, to: next });
        if (typeof updateModeButtonsFn === 'function') updateModeButtonsFn();
        refreshModelHint(key);
        const modeLabel = key ? key.charAt(0).toUpperCase() + key.slice(1) : 'mode';
        if (typeof window.logUserAction === 'function') {
          window.logUserAction('Modes & Models', `Set ${modeLabel} model: ${next}`);
        }
      });
    });
  }

  // Initialize
  fontPref = applyFontFamilyPref(store?.getItem(FONT_PREF_KEY) || DEFAULT_FONT);
  fontSizePref = applyFontSizePref(store?.getItem(FONT_SIZE_PREF_KEY) || DEFAULT_FONT_SIZE);
  modelSelections = loadModelPrefs();
  syncModelSelects();

  // Font event listeners
  fontRadios.forEach((radio) => {
    radio.addEventListener('change', () => {
      if (!radio.checked) return;
      fontPref = applyFontFamilyPref(radio.value);
      try { store?.setItem(FONT_PREF_KEY, fontPref); } catch {}
      if (typeof window.logUserAction === 'function') {
        window.logUserAction('Settings & Navigation', `Font family: ${radio.value}`);
      }
    });
  });

  fontSizeRadios.forEach((radio) => {
    radio.addEventListener('change', () => {
      if (!radio.checked) return;
      fontSizePref = applyFontSizePref(radio.value);
      try { store?.setItem(FONT_SIZE_PREF_KEY, fontSizePref); } catch {}
      if (typeof window.logUserAction === 'function') {
        window.logUserAction('Settings & Navigation', `Font size: ${radio.value}`);
      }
    });
  });

  // Initialize model selections on load
  modelSelections = loadModelPrefs();

  function getModelSelections(){
    return { ...modelSelections };
  }

  // Export to global
  global.AppModels = {
    getModelForMode,
    getModelDetailText,
    refreshModelHint,
    attachSelectHandlers: attachModelSelectHandlers,
    getModelSelections,
    syncModelSelects,
    MODEL_OPTIONS,
    MODEL_DETAILS,
    DEFAULT_MODELS,
    applyRestrictions,
  };
})(window);
