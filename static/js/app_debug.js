/*
 * app_debug.js — Debug panel and console logging overlay
 * Extracted from app.js to reduce file size
 */
(function(global){
  const store = global.localStorage;
  const DEBUG_STORAGE_KEY = 'debugMode.v1';

  const debugToggle = document.getElementById('debugToggle');
  const debugRadios = Array.from(document.querySelectorAll('input[name="debug-panel"]'));
  const logPanel = document.getElementById('logPanel');
  const logContent = document.getElementById('logContent');
  const logClearBtn = document.getElementById('logClearBtn');
  const logHideBtn = document.getElementById('logHideBtn');
  const techToggleBtn = document.getElementById('techToggle');
  const techPanel = document.getElementById('techPanel');

  let debugEnabled = false;
  let guestLock = false;

  const originalConsole = {
    log: console.log,
    warn: console.warn,
    error: console.error,
    info: console.info,
  };

  function formatArg(arg){
    try {
      if (arg instanceof Error) {
        return `${arg.name}: ${arg.message}\n${arg.stack || ''}`;
      }
      if (typeof arg === 'object') {
        return JSON.stringify(arg, null, 2);
      }
    } catch {}
    return String(arg);
  }

  function formatTimestamp(date = new Date()){
    const pad = (num) => String(num).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }

  function broadcastLog(level, args){
    if (!debugEnabled) return;
    if (!logContent) return;
    const payload = args.map(formatArg).join(' ');
    const time = formatTimestamp();
    logContent.textContent += `[${time}] [${level}] ${payload}\n`;
    logContent.scrollTop = logContent.scrollHeight;
  }

  function attachDebugProxy(){
    ['log', 'warn', 'error', 'info'].forEach((level) => {
      console[level] = (...args) => {
        try { broadcastLog(level, args); } catch {}
        try { originalConsole[level](...args); } catch {}
      };
    });
  }

  function setDebugState(enabled){
    if (guestLock) {
      enabled = false;
    }
    debugEnabled = !!enabled;
    if (store) {
      try { store.setItem(DEBUG_STORAGE_KEY, debugEnabled ? '1' : '0'); } catch {}
    }
    if (debugToggle) debugToggle.checked = debugEnabled;
    if (debugRadios.length) {
      debugRadios.forEach((radio) => {
        const shouldCheck = radio.value === (debugEnabled ? 'on' : 'off');
        radio.checked = shouldCheck;
        radio.closest('.settings-item')?.classList.toggle('active', shouldCheck);
      });
    }
    if (logPanel) {
      logPanel.classList.toggle('visible', debugEnabled);
      logPanel.classList.toggle('hidden', !debugEnabled);
    }
    document.body.classList.toggle('debug-panel-open', debugEnabled);
    try { originalConsole.log(`[debug] client logs ${debugEnabled ? 'enabled' : 'disabled'}`); } catch {}
  }

  function isEnabled(){ return debugEnabled; }

  function setGuestLock(enabled){
    guestLock = !!enabled;
    if (guestLock) {
      setDebugState(false);
    }
    if (debugToggle) {
      debugToggle.disabled = guestLock;
      debugToggle.title = guestLock ? 'Guest is read-only' : '';
    }
    debugRadios.forEach((radio) => {
      radio.disabled = guestLock;
      const item = radio.closest('.settings-item');
      if (item) {
        item.classList.toggle('disabled', guestLock);
        if (guestLock) item.title = 'Guest is read-only';
        else item.removeAttribute('title');
      }
    });
  }

  // Initialize
  attachDebugProxy();
  let initialDebug = false;
  if (store) {
    try { initialDebug = store.getItem(DEBUG_STORAGE_KEY) === '1'; } catch {}
  }
  setDebugState(initialDebug);
  if (global.IS_GUEST) {
    setGuestLock(true);
  }

  // Event listeners
  if (debugToggle) {
    debugToggle.addEventListener('change', () => {
      setDebugState(debugToggle.checked);
      if (typeof window.logUserAction === 'function') {
        window.logUserAction('Settings & Navigation', debugToggle.checked ? 'Debug on' : 'Debug off');
      }
    });
  }

  debugRadios.forEach((radio) => {
    radio.addEventListener('change', () => {
      if (!radio.checked) return;
      setDebugState(radio.value === 'on');
      if (typeof window.logUserAction === 'function') {
        window.logUserAction('Settings & Navigation', radio.value === 'on' ? 'Debug on' : 'Debug off');
      }
    });
  });

  if (logClearBtn && logContent) {
    logClearBtn.addEventListener('click', () => {
      logContent.textContent = '';
    });
  }

  if (logHideBtn) {
    logHideBtn.addEventListener('click', () => setDebugState(false));
  }

  if (techToggleBtn && techPanel) {
    techToggleBtn.addEventListener('click', () => {
      const isVisible = techPanel.classList.toggle('visible');
      techPanel.classList.toggle('hidden', !isVisible);
      techToggleBtn.classList.toggle('active', isVisible);
      techToggleBtn.setAttribute('aria-expanded', isVisible ? 'true' : 'false');
    });
  }

  // Export to global
  global.AppDebug = {
    setEnabled: setDebugState,
    setGuestLock,
    isEnabled,
    STORAGE_KEY: DEBUG_STORAGE_KEY,
  };
})(window);
