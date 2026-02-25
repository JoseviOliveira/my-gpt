/*
 * app.js — core client runtime for gpt-20b chat
 * Bootstraps DOM references, handles streaming, delegates to chats.js and shell.js
 */

// --- DOM Elements ---
const chatEl = document.getElementById('chat');
const t = document.getElementById('t');

const statusEl = document.getElementById('status');
const uEl = document.getElementById('u');
const pEl = document.getElementById('p');
const loginBtn = document.getElementById('login');
const logoutBtn = document.getElementById('logout');
const docsBtn = document.getElementById('docsBtn');
const adminLink = document.getElementById('statsLink');
const settingsToggleBtn = document.getElementById('settingsToggle');
const settingsPanel = document.getElementById('settingsPanel');
const guestBadge = document.getElementById('guestBadge');
const debugToggle = document.getElementById('debugToggle');
const debugRadios = Array.from(document.querySelectorAll('input[name="debug-panel"]'));
const logPanel = document.getElementById('logPanel');
const logContent = document.getElementById('logContent');
const logClearBtn = document.getElementById('logClearBtn');
const logHideBtn = document.getElementById('logHideBtn');
const sttModeButtons = Array.from(document.querySelectorAll('input[data-settings-stt]'));
const modelSelects = Array.from(document.querySelectorAll('select[data-model-mode]'));
const modelSelectMap = new Map();
modelSelects.forEach((selectEl) => {
  const key = (selectEl?.dataset?.modelMode || '').toLowerCase();
  if (key) modelSelectMap.set(key, selectEl);
});
const modelHintEl = document.getElementById('modelHintCombined');
const ttsModeButtons = Array.from(document.querySelectorAll('input[data-settings-tts]'));
const themeToggleBtn = document.getElementById('themeToggle');
const fontRadios = Array.from(document.querySelectorAll('input[name="settings-font"]'));
const fontSizeRadios = Array.from(document.querySelectorAll('input[name="settings-font-size"]'));
const splashEl = document.getElementById('splash');
const splashInfo = document.getElementById('splashInfo');
const splashLoginForm = document.getElementById('splashLogin');
const splashUserInput = document.getElementById('splashUser');
const splashPassInput = document.getElementById('splashPass');
const splashError = document.getElementById('splashError');
const splashSubmitBtn = document.getElementById('splashSubmit');
const splashContinueBtn = document.getElementById('splashContinue');
const splashSwitchBtn = document.getElementById('splashSwitch');
const serverModeRow = document.getElementById('serverModeRow');
const autoEnter = (() => {
  try {
    const params = new URLSearchParams(window.location.search || '');
    return params.get('enter') === '1';
  } catch {
    return false;
  }
})();
const hasStoredToken = (() => {
  try {
    return !!window.localStorage.getItem('authToken');
  } catch {
    return false;
  }
})();

const setAdminLinkAccess = (isAdmin) => {
  if (!adminLink) return;
  if (!adminLink.dataset.href) {
    adminLink.dataset.href = adminLink.getAttribute('href') || '';
  }
  if (isAdmin) {
    if (adminLink.dataset.href) {
      adminLink.setAttribute('href', adminLink.dataset.href);
    }
    adminLink.removeAttribute('aria-disabled');
    adminLink.classList.remove('is-disabled');
    adminLink.removeAttribute('tabindex');
    adminLink.removeAttribute('title');
  } else {
    adminLink.removeAttribute('href');
    adminLink.setAttribute('aria-disabled', 'true');
    adminLink.classList.add('is-disabled');
    adminLink.setAttribute('tabindex', '-1');
    adminLink.setAttribute('title', 'Admin access required');
  }
};

if (splashEl && !splashEl.classList.contains('hidden')) {
  if (autoEnter && hasStoredToken) {
    splashEl.classList.add('hidden');
    splashEl.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('splash-open');
  } else {
    document.body.classList.add('splash-open');
    splashEl.setAttribute('aria-hidden', 'false');
  }
}
const authBox = document.getElementById('authBox');
const loggedBox = document.getElementById('loggedBox');
const side = document.getElementById('side');
const toggleBtn = document.getElementById('toggle');
const closeSideBtn = document.getElementById('closeSidebar');

// --- Request state ---

// Runtime config (e.g., STT mode) fetched once and shared across modules
const APP_CONFIG_URL = '/config';
window.APP_CONFIG = window.APP_CONFIG || {};
const setServerModeStatus = (value) => {
  if (!serverModeRow) return;
  serverModeRow.dataset.eco = value ? 'true' : 'false';
};
let appConfigPromise = null;
const fetchAppConfig = async () => {
  try {
    const resp = await fetch(APP_CONFIG_URL, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error(`config status ${resp.status}`);
    const data = await resp.json();
    window.APP_CONFIG = data;
    window.APP_GUEST_USER = data?.guest_user || window.APP_GUEST_USER || 'guest';
    window.IS_GUEST = Boolean(data && data.guest_mode);
    window.IS_ADMIN = Boolean(data && data.is_admin);
    setAdminLinkAccess(window.IS_ADMIN);
    if (guestBadge) {
      guestBadge.classList.toggle('hidden', !window.IS_GUEST);
    }
    try {
      window.dispatchEvent(new CustomEvent('guest:ready', { detail: { guest: window.IS_GUEST } }));
      window.dispatchEvent(new CustomEvent('role:ready', { detail: { admin: window.IS_ADMIN } }));
    } catch {}
    if (window.IS_GUEST) {
      sttModeButtons.forEach((btn) => {
        btn.disabled = true;
        btn.setAttribute('aria-disabled', 'true');
        const wrap = btn.closest('[data-stt-option]');
        if (wrap) {
          wrap.classList.add('disabled');
          wrap.title = 'Guest is read-only';
        }
      });
      ttsModeButtons.forEach((btn) => {
        btn.disabled = true;
        btn.setAttribute('aria-disabled', 'true');
        const wrap = btn.closest('[data-tts-option]');
        if (wrap) {
          wrap.classList.add('disabled');
          wrap.title = 'Guest is read-only';
        }
      });
      if (window.AppDebug && typeof window.AppDebug.setGuestLock === 'function') {
        window.AppDebug.setGuestLock(true);
      }
    }
    if (!window.IS_GUEST && !window.IS_ADMIN) {
      const allowedModes = Array.isArray(data?.allowed_modes) ? data.allowed_modes.map((m) => String(m).toLowerCase()) : [];
      if (allowedModes.length) {
        const allowedSet = new Set(allowedModes);
        const applyModeLock = (btn, modeKey) => {
          const allowed = allowedSet.has(modeKey);
          btn.disabled = !allowed;
          btn.setAttribute('aria-disabled', allowed ? 'false' : 'true');
          btn.classList.toggle('is-disabled', !allowed);
          btn.title = allowed ? btn.title : 'Mode not available';
        };
        if (modeFast) applyModeLock(modeFast, 'fast');
        if (modeNormal) applyModeLock(modeNormal, 'normal');
        if (modeDeep) applyModeLock(modeDeep, 'deep');
        if (!allowedSet.has(mode)) {
          mode = allowedSet.has('normal') ? 'normal' : allowedModes[0] || 'fast';
          store.setItem('mode', mode);
          updateModeButtons();
        }
      }
      if (window.AppModels && typeof window.AppModels.applyRestrictions === 'function') {
        window.AppModels.applyRestrictions({
          allowedModels: Array.isArray(data?.allowed_models) ? data.allowed_models : [],
          allowedModes,
          modelDefaults: data?.model_defaults || {},
        });
      }
    }
    setServerModeStatus(Boolean(data && data.eco_mode));
    return data;
  } catch (err) {
    console.warn('[app] config fetch failed', err);
    setServerModeStatus(Boolean(window.APP_CONFIG && window.APP_CONFIG.eco_mode));
    return window.APP_CONFIG;
  }
};
const getAppConfig = (force = false) => {
  if (!appConfigPromise || force) {
    appConfigPromise = fetchAppConfig();
  }
  return appConfigPromise;
};
getAppConfig();
window.getAppConfig = getAppConfig;

const chats = window.Chats;
const history = chats.history;

const listEl = document.getElementById('chatlist');
const newBtn = document.getElementById('newchat');
const delBtn = document.getElementById('delchat');
const currentTitleEl = document.getElementById('currentTitle');
const currentSummaryEl = document.getElementById('currentSummary');
const modeFast = document.getElementById('modeFast');
const modeNormal = document.getElementById('modeNormal');
const modeDeep = document.getElementById('modeDeep');
const modeBoxEl = document.getElementById('modeBox');

const store = window.localStorage;
const rootEl = document.documentElement;
let mode = store.getItem('mode') || 'fast';

// --- Model & Font Preferences (from app_models.js) ---
const { getModelForMode, getModelSelections, syncModelSelects, DEFAULT_MODELS, MODEL_OPTIONS } = window.AppModels;
const modelSelections = getModelSelections();
syncModelSelects();
window.AppModels.attachSelectHandlers();

function triggerSummaryFade(){
  if (!currentSummaryEl || typeof requestAnimationFrame !== 'function') return;
  currentSummaryEl.classList.add('summary-fade');
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      currentSummaryEl.classList.remove('summary-fade');
    });
  });
}

function setCurrentTitle(text){ currentTitleEl.textContent = text || '<blank>'; }
function setCurrentSummary(text, opts = {}){
  if (!currentSummaryEl) return;
  const summary = (text || '').trim();
  let preserveOffset = null;
  if (chatEl) {
    preserveOffset = chatEl.scrollHeight - chatEl.scrollTop;
  }
  if (summary) {
    if (currentSummaryEl.dataset.fullText === summary || opts.animate === false) {
      currentSummaryEl.dataset.fullText = summary;
      currentSummaryEl.textContent = summary;
      currentSummaryEl.classList.remove('hidden');
      currentSummaryEl.classList.remove('updating');
      triggerSummaryFade();
    } else {
      currentSummaryEl.dataset.fullText = summary;
      currentSummaryEl.classList.add('updating');
      currentSummaryEl.classList.remove('hidden');
      typeSummary(summary, 2000);
    }
  } else {
    currentSummaryEl.textContent = '';
    currentSummaryEl.classList.add('hidden');
    currentSummaryEl.dataset.fullText = '';
  }
  if (preserveOffset !== null && chatEl) {
    chatEl.scrollTop = chatEl.scrollHeight - preserveOffset;
  }
}

let summaryTypingTimer = null;
function typeSummary(text, duration){
  if (!currentSummaryEl) return;
  if (summaryTypingTimer) {
    clearInterval(summaryTypingTimer);
    summaryTypingTimer = null;
  }
  const words = text.split(/\s+/).filter(Boolean);
  if (!words.length) {
    currentSummaryEl.textContent = '';
    currentSummaryEl.classList.remove('updating');
    return;
  }
  const interval = Math.max(60, Math.floor(duration / words.length));
  let index = 0;
  currentSummaryEl.textContent = '';
  summaryTypingTimer = setInterval(() => {
    if (index >= words.length) {
      clearInterval(summaryTypingTimer);
      summaryTypingTimer = null;
      currentSummaryEl.classList.remove('updating');
      triggerSummaryFade();
      return;
    }
    const chunk = words.slice(0, index + 1).join(' ');
    currentSummaryEl.textContent = chunk;
    index += 1;
  }, interval);
}

function updateModeButtons(){
  const fastModel = getModelForMode('fast');
  const normalModel = getModelForMode('normal');
  const deepModel = getModelForMode('deep');
  modeFast.classList.remove('on');
  modeNormal.classList.remove('on');
  modeDeep.classList.remove('on');
  modeFast.title = fastModel ? `Fast — ${fastModel}` : 'Fast mode';
  modeNormal.title = normalModel ? `Normal — ${normalModel}` : 'Normal mode';
  modeDeep.title = deepModel ? `Deep — ${deepModel}` : 'Deep mode';
  if (mode === 'fast') modeFast.classList.add('on');
  else if (mode === 'deep') modeDeep.classList.add('on');
  else modeNormal.classList.add('on');
}

// --- In-app modal helpers (confirm / prompt / alert) ---
// Shows a confirmation-style modal and resolves with the user's choice.

const shell = window.Shell.init({
  store,
  chats,
  themeToggleBtn,
  settingsToggleBtn,
  settingsPanel,
  sttModeButtons,
  ttsModeButtons,
  authBox,
  loggedBox,
  modeBox: modeBoxEl,
  splash: {
    container: splashEl,
    info: splashInfo,
    loginForm: splashLoginForm,
    userInput: splashUserInput,
    passInput: splashPassInput,
    submitBtn: splashSubmitBtn,
    continueBtn: splashContinueBtn,
    switchBtn: splashSwitchBtn,
    error: splashError
  },
  toggleBtn,
  closeSideBtn,
  side,
  updateModeButtons,
  focusComposer: () => { t?.focus(); },
  onResetSessions: () => {
    history.length = 0;
    chatEl.innerHTML = '';
    listEl.innerHTML = '';
    setCurrentTitle('(untitled)');
    setCurrentSummary('');
  },
  loginBtn,
  logoutBtn,
  userInput: uEl,
  passInput: pEl
});

const isLogged = shell.isLogged;
const credHeader = shell.credHeader;

window.authHeader = credHeader;

function logUserAction(group, action, detail = ''){
  if (!group || !action) return;
  try {
    if (typeof isLogged === 'function' && !isLogged()) return;
  } catch {}
  const headers = Object.assign({ 'Content-Type': 'application/json' }, credHeader() || {});
  try {
    fetch('/api/analytics/action', {
      method: 'POST',
      headers,
      body: JSON.stringify({ group, action, detail })
    }).catch(() => {});
  } catch {}
}
window.logUserAction = logUserAction;

// --- Tour System (from app_tour.js) ---
const { start: startTour, end: endTour } = window.AppTour;
const tourBtn = document.getElementById('tourBtn');
if (tourBtn) {
  tourBtn.addEventListener('click', () => {
    try { window.logUserAction?.('Settings & Navigation', 'Launch tour'); } catch {}
    startTour(true);
  });
}

// Tech panel toggle
const techToggleBtn = document.getElementById('techToggle');
const techPanel = document.getElementById('techPanel');
if (techToggleBtn && techPanel) {
  techToggleBtn.addEventListener('click', () => {
    const isVisible = techPanel.classList.toggle('visible');
    techPanel.classList.toggle('hidden', !isVisible);
    techToggleBtn.classList.toggle('active', isVisible);
    techToggleBtn.setAttribute('aria-expanded', isVisible ? 'true' : 'false');
  });
}

// --- Debug Panel (from app_debug.js) ---
const { setDebugState } = window.AppDebug;

function maybeLaunchTourOnce(){
  if (!tourBtn) return;
  if (!isLogged()) return;
  const TOUR_STORAGE_KEY = 'welcomeTourSeen.v1';
  if (store) {
    try {
      if (store.getItem(TOUR_STORAGE_KEY) === 'done') return;
    } catch {}
  }
  setTimeout(() => {
    if (isLogged()) startTour(false);
  }, 1200);
}

if (docsBtn) {
  docsBtn.addEventListener('click', () => {
    try { window.logUserAction?.('Settings & Navigation', 'Open resources'); } catch {}
    try { window.location.href = '/docs/index.html'; }
    catch { window.location.assign('/docs/index.html'); }
  });
}

const fromDocs = typeof sessionStorage !== 'undefined' ? sessionStorage.getItem('docsBack') : null;
if (fromDocs) {
  try { sessionStorage.removeItem('docsBack'); } catch {}
  if (isLogged()) {
    shell.hideSplash?.();
    shell.renderAuthUI?.();
  }
}

chats.init({
  chatEl,
  listEl,
  newBtn,
  delBtn,
  currentTitleEl,
  setCurrentTitle,
  setCurrentSummary,
  renderTables,
  credHeader,
  isLogged,
  focusComposer: () => { t?.focus(); },
  composer: t
});

// Switches to the "Normal" generation profile.
modeNormal.addEventListener('click', ()=>{ mode='normal'; store.setItem('mode','normal'); updateModeButtons(); chats.ping(); try { window.logUserAction?.('Modes & Models', 'Switch to Normal'); } catch {} });
// Switches to the "Deep" generation profile.
modeDeep.addEventListener('click', ()=>{ mode='deep'; store.setItem('mode','deep'); updateModeButtons(); chats.ping(); try { window.logUserAction?.('Modes & Models', 'Switch to Deep'); } catch {} });
// Switches to the "Fast" generation profile.
modeFast.addEventListener('click', ()=>{
  mode='fast';
  store.setItem('mode','fast');
  updateModeButtons();
  chats.ping();
  try { window.logUserAction?.('Modes & Models', 'Switch to Fast'); } catch {}
});

// Boot
(async function init(){
  try {
    updateModeButtons();
    shell.renderAuthUI();
    setCurrentSummary('');
    await chats.ping();
    await chats.hydrateWorkspace();
    if (autoEnter && shell.isLogged()) {
      shell.hideSplash();
      try {
        const url = new URL(window.location.href);
        url.searchParams.delete('enter');
        window.history.replaceState({}, '', url.toString());
      } catch {}
    }
    maybeLaunchTourOnce();
  } catch (err) {
    // Ignore 401 errors on boot - user may not be logged in yet
    if (err?.message !== '401') {
      console.error('[app] boot error:', err);
    }
  }
})();
