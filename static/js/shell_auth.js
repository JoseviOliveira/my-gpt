/*
 * shell_auth.js — Authentication and session management
 */
(function(global){
  const ShellAuth = global.ShellAuth || (global.ShellAuth = {});
  let store = null;
  let authBox = null, loggedBox = null, modeBox = null;
  let splash = {};
  let chats = null;
  let onResetSessions = () => {};
  let closeSettingsPanel = () => {};
  let focusComposer = () => {};

  const TOKEN_STORAGE_KEY = 'authToken';
  const AUTH_COOKIE_NAME = 'auth_token';
  const AUTH_COOKIE_MAX_AGE = 60 * 60 * 24;

  function init(options = {}){
    store = options.store || global.localStorage;
    authBox = options.authBox || null;
    loggedBox = options.loggedBox || null;
    modeBox = options.modeBox || null;
    splash = options.splash || {};
    chats = options.chats;
    onResetSessions = options.onResetSessions || (() => {});
    closeSettingsPanel = options.closeSettingsPanel || (() => {});
    focusComposer = options.focusComposer || (() => {});
  }

  function show(el){ if (el) { el.classList.remove('hidden'); el.style.display=''; } }
  function hide(el){ if (el) { el.classList.add('hidden'); el.style.display='none'; } }

  function getUser(){ return (store && store.getItem('u')) || ''; }
  function getToken(){ return (store && store.getItem(TOKEN_STORAGE_KEY)) || ''; }
  function getPass(){ return (store && store.getItem('p')) || ''; }
  function isLogged(){ return !!getToken(); }

  function credHeader(){
    const token = getToken();
    if (token) return { 'Authorization': `Bearer ${token}` };
    const legacyUser = getUser();
    const legacyPass = getPass();
    if (legacyUser && legacyPass) {
      const tok = btoa(`${legacyUser}:${legacyPass}`);
      return { 'Authorization': `Basic ${tok}` };
    }
    return {};
  }

  function setAuthCookie(value){
    try {
      if (typeof document === 'undefined') return;
      const token = value ? encodeURIComponent(value) : '';
      const maxAge = value ? AUTH_COOKIE_MAX_AGE : 0;
      document.cookie = `${AUTH_COOKIE_NAME}=${token}; Path=/; SameSite=Lax; Max-Age=${maxAge}`;
    } catch {}
  }

  function renderAuthUI(){
    const logged = isLogged();
    const username = getUser();
    if (authBox && loggedBox && modeBox) {
      if (logged) {
        hide(authBox);
        show(loggedBox);
        show(modeBox);
      } else {
        show(authBox);
        hide(loggedBox);
        hide(modeBox);
      }
    }
    const logoutLabel = document.getElementById('logoutLabel');
    if (logoutLabel) {
      logoutLabel.textContent = logged ? `Logout (${username || 'user'})` : 'Logout';
    }
    updateSplashUI();
  }

  function setSplashError(message = ''){
    const el = splash.error;
    if (!el) return;
    el.textContent = message || '';
    el.classList.toggle('hidden', !message);
  }

  function focusSplashControl(){
    if (!splash.container || splash.container.classList.contains('hidden')) return;
    (isLogged() ? splash.continueBtn : splash.userInput)?.focus();
  }

  function updateSplashUI(){
    if (!splash.container) return;
    const logged = isLogged(), username = getUser();
    if (splash.info) splash.info.textContent = logged ? `logged as ${username || 'user'}` : 'Enter your credentials to continue.';
    splash.loginForm?.classList.toggle('hidden', logged);
    splash.continueBtn?.classList.toggle('hidden', !logged);
    splash.switchBtn?.classList.toggle('hidden', !logged);
    if (logged) { setSplashError(''); if (splash.userInput) splash.userInput.value = ''; if (splash.passInput) splash.passInput.value = ''; }
    requestAnimationFrame(focusSplashControl);
  }

  function showSplash(){
    const splashEl = splash.container;
    if (!splashEl) return;
    splashEl.classList.remove('hidden');
    splashEl.setAttribute('aria-hidden', 'false');
    document.body.classList.add('splash-open');
    updateSplashUI();
  }

  function hideSplash(){
    const splashEl = splash.container;
    if (!splashEl) return;
    
    if (document.activeElement && document.activeElement.tagName === 'INPUT') {
      document.activeElement.blur();
    }
    
    splashEl.classList.add('hidden');
    splashEl.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('splash-open');
    setSplashError('');
  }

  async function completeLogin(u, p){
    const payload = { username: u, password: p };
    let token = '';
    let resolvedUser = u;
    try {
      const resp = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      let data = {};
      try {
        data = await resp.json();
      } catch {}
      if (!resp.ok) {
        const detail = data?.error || data?.message || 'Login failed.';
        throw new Error(detail);
      }
      token = data?.token || '';
      resolvedUser = data?.username || u;
      if (!token) throw new Error('Login failed.');
    } catch (err) {
      if (store) {
        store.removeItem(TOKEN_STORAGE_KEY);
      }
      setAuthCookie('');
      throw err;
    }

    if (store) {
      if (resolvedUser) store.setItem('u', resolvedUser);
      store.setItem(TOKEN_STORAGE_KEY, token);
      store.removeItem('p');
    }
    setAuthCookie(token);
    renderAuthUI();
    closeSettingsPanel();
    try {
      if (typeof global.getAppConfig === 'function') {
        await global.getAppConfig(true);
      }
    } catch {}
    try {
      await chats.ping();
      await chats.hydrateWorkspace();
      hideSplash();
    } catch (err) {
      if (store) {
        store.removeItem(TOKEN_STORAGE_KEY);
      }
      setAuthCookie('');
      renderAuthUI();
      throw err;
    }
  }

  function clearAuth() {
    if (store) { store.removeItem('p'); store.removeItem(TOKEN_STORAGE_KEY); }
    setAuthCookie('');
  }

  function doLogout(switchMode = false){
    if (store) store.removeItem('u');
    clearAuth();
    chats.setCurrentId('');
    onResetSessions();
    closeSettingsPanel();
    renderAuthUI();
    showSplash();
    if (switchMode) focusComposer();
  }

  function logout(){ doLogout(false); }
  function switchAccount(){ doLogout(true); }

  function forceLogout(message) {
    clearAuth(); renderAuthUI(); showSplash();
    if (message) setSplashError(message);
  }

  function attachSplashListeners(options){
    const loginBtn = options.loginBtn || null;
    const logoutBtn = options.logoutBtn || null;
    const splashLoginForm = splash.loginForm || null;
    const splashContinueBtn = splash.continueBtn || null;
    const splashSwitchBtn = splash.switchBtn || null;

    splashLoginForm?.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const u = (splash.userInput?.value || '').trim();
      const p = (splash.passInput?.value || '').trim();
      if (!u || !p) {
        setSplashError('Enter user and password');
        (u ? splash.passInput : splash.userInput)?.focus();
        return;
      }
      setSplashError('');
      if (splash.submitBtn) splash.submitBtn.disabled = true;
      try {
        await completeLogin(u, p);
        if (splash.userInput) splash.userInput.value = '';
        if (splash.passInput) splash.passInput.value = '';
      } catch (err) {
        setSplashError(err?.message || 'Login failed. Check your credentials and try again.');
      } finally {
        if (splash.submitBtn) splash.submitBtn.disabled = false;
      }
    });

    splashContinueBtn?.addEventListener('click', async () => {
      if (splashContinueBtn.disabled) return;
      if (!isLogged()) {
        setSplashError('Please log in first.');
        return;
      }
      splashContinueBtn.disabled = true;
      setSplashError('');
      try {
        await chats.hydrateWorkspace();
        hideSplash();
      } catch {
        setSplashError('Unable to load the workspace. Please try again.');
      } finally {
        splashContinueBtn.disabled = false;
      }
    });

    splashSwitchBtn?.addEventListener('click', () => {
      switchAccount();
    });

    loginBtn?.addEventListener('click', async () => {
      const u = (options.userInput?.value || '').trim();
      const p = (options.passInput?.value || '').trim();
      if (!u || !p) {
        const alertFn = chats && typeof chats.alertDialog === 'function'
          ? chats.alertDialog
          : (msg) => Promise.resolve(window.alert(msg));
        await alertFn('Enter user and password', { title: 'Login' });
        return;
      }
      try {
        await completeLogin(u, p);
      } catch (err) {
        const alertFn = chats && typeof chats.alertDialog === 'function'
          ? chats.alertDialog
          : (msg) => Promise.resolve(window.alert(msg));
        await alertFn(err?.message || 'Login failed. Check your credentials and try again.', { title: 'Login failed' });
      }
    });

    logoutBtn?.addEventListener('click', () => {
      logout();
    });
  }

  ShellAuth.init = init;
  ShellAuth.getUser = getUser;
  ShellAuth.getToken = getToken;
  ShellAuth.getPass = getPass;
  ShellAuth.isLogged = isLogged;
  ShellAuth.credHeader = credHeader;
  ShellAuth.renderAuthUI = renderAuthUI;
  ShellAuth.showSplash = showSplash;
  ShellAuth.hideSplash = hideSplash;
  ShellAuth.completeLogin = completeLogin;
  ShellAuth.forceLogout = forceLogout;
  ShellAuth.setAuthCookie = setAuthCookie;
  ShellAuth.attachSplashListeners = attachSplashListeners;

  global.ShellAuth = ShellAuth;
})(window);
