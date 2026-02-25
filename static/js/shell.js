/*
 * shell.js — UI chrome orchestration (delegates to auth/theme/settings modules)
 */
(function(global){
  const shell = {};
  const ShellAuth = global.ShellAuth || {};
  const ShellTheme = global.ShellTheme || {};
  const ShellSettings = global.ShellSettings || {};
  
  let store, chats;
  let toggleBtn, closeSideBtn, side;
  let updateModeButtons = () => {};
  let sttModeButtons = [], ttsModeButtons = [];
  let speechHintInputEl = null, speechHintOutputEl = null;
  let speechModes = { stt: 'browser', tts: 'browser' };
  const STT_HINT_TEXT = { browser: 'realtime audio decoding, may produce errors.', whisper: 'better quality for longer/multilingual dictation.' };
  const TTS_HINT_TEXT = { browser: 'instant streaming, but non english may be poor.', coqui: 'slower, but streams higher-fidelity audio.' };

  function init(options = {}){
    store = options.store || global.localStorage;
    chats = options.chats;
    toggleBtn = options.toggleBtn || null;
    closeSideBtn = options.closeSideBtn || null;
    side = options.side || null;
    updateModeButtons = options.updateModeButtons || (() => {});
    sttModeButtons = Array.isArray(options.sttModeButtons) ? options.sttModeButtons : [];
    ttsModeButtons = Array.isArray(options.ttsModeButtons) ? options.ttsModeButtons : [];

    ShellAuth.init({
      store,
      chats,
      authBox: options.authBox,
      loggedBox: options.loggedBox,
      modeBox: options.modeBox,
      splash: options.splash,
      onResetSessions: options.onResetSessions || (() => {}),
      closeSettingsPanel: () => ShellSettings.closeSettingsPanel(),
      focusComposer: options.focusComposer || (() => {}),
    });

    ShellTheme.init({
      store,
      themeToggleBtn: options.themeToggleBtn,
    });

    ShellSettings.init({
      settingsToggleBtn: options.settingsToggleBtn,
      settingsPanel: options.settingsPanel,
    });

    speechHintInputEl = document.getElementById('speechHintInput');
    speechHintOutputEl = document.getElementById('speechHintOutput');
    
    attachSttModeControls();
    attachTtsModeControls();
    if (typeof document !== 'undefined' && document) {
      document.addEventListener('stt:ready', attachSttModeControls, { once: true });
      document.addEventListener('tts:ready', attachTtsModeControls, { once: true });
    }
    attachSidebarListeners();
    ShellAuth.attachSplashListeners(options);
    ShellAuth.renderAuthUI();
    updateModeButtons();
    refreshSpeechHint();
    ShellAuth.setAuthCookie(ShellAuth.getToken());

    return shell;
  }

  function refreshSpeechHint(){
    const sttTarget = speechHintInputEl;
    const ttsTarget = speechHintOutputEl;
    if (!sttTarget && !ttsTarget) return;
    let sttMode = speechModes.stt;
    let ttsMode = speechModes.tts;
    const SpeechCtrl = global.ShellSpeech;
    if (SpeechCtrl && typeof SpeechCtrl.getSpeechModes === 'function') {
      const modes = SpeechCtrl.getSpeechModes() || {};
      sttMode = modes.stt || sttMode;
      ttsMode = modes.tts || ttsMode;
    }
    const sttText = STT_HINT_TEXT[String(sttMode || '').toLowerCase()] || '';
    const ttsText = TTS_HINT_TEXT[String(ttsMode || '').toLowerCase()] || '';
    if (sttTarget) sttTarget.textContent = sttText;
    if (ttsTarget) ttsTarget.textContent = ttsText;
  }

  function attachSttModeControls(){
    const SpeechCtrl = global.ShellSpeech;
    if (SpeechCtrl && typeof SpeechCtrl.attachSttModeControls === 'function') {
      SpeechCtrl.attachSttModeControls(sttModeButtons, refreshSpeechHint);
      speechModes.stt = SpeechCtrl.getSpeechModes().stt;
    }
  }

  function attachTtsModeControls(){
    const SpeechCtrl = global.ShellSpeech;
    if (SpeechCtrl && typeof SpeechCtrl.attachTtsModeControls === 'function') {
      SpeechCtrl.attachTtsModeControls(ttsModeButtons, refreshSpeechHint);
      speechModes.tts = SpeechCtrl.getSpeechModes().tts;
    }
  }

  function isSidebarOpen(){
    if (!side) return false;
    if (isMobileLayout()) {
      return document.body.classList.contains('sidebar-open');
    }
    return !document.body.classList.contains('sidebar-closed');
  }

  function isMobileLayout(){
    return global.matchMedia ? global.matchMedia('(max-width: 900px)').matches : false;
  }

  function setSidebar(open){
    if (open) {
      document.body.classList.add('sidebar-open');
      document.body.classList.remove('sidebar-closed');
    } else {
      document.body.classList.remove('sidebar-open');
      document.body.classList.add('sidebar-closed');
    }
    if (store) store.setItem('sideHidden', open ? '0' : '1');
  }

  function toggleSidebar(){
    if (!side) return;
    setSidebar(!isSidebarOpen());
    try { window.logUserAction?.('Settings & Navigation', isSidebarOpen() ? 'Sidebar open' : 'Sidebar close'); } catch {}
  }

  function setMobileSidebar(open){
    setSidebar(open);
  }

  function attachSidebarListeners(){
    if (toggleBtn) {
      toggleBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        toggleSidebar();
      });
    }
    if (closeSideBtn) {
      closeSideBtn.addEventListener('click', () => {
        if (!side) return;
        setSidebar(false);
        try { window.logUserAction?.('Settings & Navigation', 'Sidebar close'); } catch {}
      });
    }

    if (store && store.getItem('sideHidden') === '1') {
      document.body.classList.add('sidebar-closed');
      document.body.classList.remove('sidebar-open');
    } else if (!isMobileLayout()) {
      document.body.classList.remove('sidebar-closed');
    }
  }

  shell.init = init;
  shell.getUser = () => ShellAuth.getUser();
  shell.getPass = () => ShellAuth.getPass();
  shell.isLogged = () => ShellAuth.isLogged();
  shell.credHeader = () => ShellAuth.credHeader();
  shell.renderAuthUI = () => ShellAuth.renderAuthUI();
  shell.showSplash = () => ShellAuth.showSplash();
  shell.hideSplash = () => ShellAuth.hideSplash();
  shell.completeLogin = (u, p) => ShellAuth.completeLogin(u, p);
  shell.forceLogout = (msg) => ShellAuth.forceLogout(msg);
  shell.alert = (message, opts) => chats && typeof chats.alertDialog === 'function'
    ? chats.alertDialog(message, opts)
    : Promise.resolve(true);
  shell.toggleSidebar = toggleSidebar;
  shell.closeSettingsPanel = () => ShellSettings.closeSettingsPanel();
  shell.setMobileSidebar = setMobileSidebar;

  global.Shell = shell;
})(window);
