/*
 * shell_theme.js — Theme switching and persistence
 */
(function(global){
  const ShellTheme = global.ShellTheme || (global.ShellTheme = {});
  let store = null;
  let themeToggleBtn = null;
  let prefersDarkScheme = null;
  let themeMetaTag = null;
  let colorTheme = 'light';
  let themeRadios = [];

  const themeStorageKey = 'theme';

  function init(options = {}){
    store = options.store || global.localStorage;
    themeToggleBtn = options.themeToggleBtn || null;
    themeMetaTag = document.querySelector('meta[name="theme-color"]');
    themeRadios = Array.from(document.querySelectorAll('input[name="settings-theme"]'));
    prefersDarkScheme = typeof global.matchMedia === 'function'
      ? global.matchMedia('(prefers-color-scheme: dark)')
      : null;
    initTheme();
    attachThemeToggle();
  }

  function readStoredTheme(){
    if (!store) return null;
    const saved = store.getItem(themeStorageKey);
    return saved === 'dark' || saved === 'light' ? saved : null;
  }

  function systemTheme(){
    return (prefersDarkScheme && prefersDarkScheme.matches) ? 'dark' : 'light';
  }

  function syncThemeMeta(){
    if (!themeMetaTag) return;
    const root = document.documentElement;
    const color = getComputedStyle(root).getPropertyValue('--bg').trim();
    if (color) themeMetaTag.setAttribute('content', color);
  }

  function renderThemeToggle(){
    const isDark = colorTheme === 'dark';
    if (themeRadios.length) {
      themeRadios.forEach((radio) => {
        const match = radio.value === (isDark ? 'dark' : 'light');
        radio.checked = match;
        radio.closest('.toggle-option')?.classList.toggle('active', match);
      });
      return;
    }
    if (!themeToggleBtn) return;
    const label = isDark ? 'Switch to light mode' : 'Switch to dark mode';
    themeToggleBtn.textContent = isDark ? '☀️ Light mode' : '🌙 Dark mode';
    themeToggleBtn.setAttribute('aria-label', label);
    themeToggleBtn.setAttribute('title', label);
  }

  function applyColorTheme(next, { persist = false } = {}){
    colorTheme = next === 'dark' ? 'dark' : 'light';
    const root = document.documentElement;
    if (colorTheme === 'dark') {
      root.dataset.theme = 'dark';
      root.classList.add('dark');
    } else {
      root.removeAttribute('data-theme');
      root.classList.remove('dark');
    }
    if (persist && store) store.setItem(themeStorageKey, colorTheme);
    syncThemeMeta();
    renderThemeToggle();
  }

  function initTheme(){
    applyColorTheme(readStoredTheme() || systemTheme());
    if (prefersDarkScheme) {
      const onPrefChange = (event) => {
        if (readStoredTheme()) return;
        applyColorTheme(event.matches ? 'dark' : 'light');
      };
      if (typeof prefersDarkScheme.addEventListener === 'function') {
        prefersDarkScheme.addEventListener('change', onPrefChange);
      } else if (typeof prefersDarkScheme.addListener === 'function') {
        prefersDarkScheme.addListener(onPrefChange);
      }
    }
  }

  function attachThemeToggle(){
    if (themeRadios.length) {
      themeRadios.forEach((radio) => {
        radio.addEventListener('change', () => {
          if (!radio.checked) return;
          const next = radio.value === 'dark' ? 'dark' : 'light';
          applyColorTheme(next, { persist: true });
          try { window.logUserAction?.('Settings & Navigation', `Theme: ${next}`); } catch {}
        });
      });
      renderThemeToggle();
      return;
    }
    if (!themeToggleBtn) return;
    themeToggleBtn.addEventListener('click', () => {
      const next = colorTheme === 'dark' ? 'light' : 'dark';
      applyColorTheme(next, { persist: true });
      try { window.logUserAction?.('Settings & Navigation', `Theme: ${next}`); } catch {}
    });
  }

  ShellTheme.init = init;
  ShellTheme.applyColorTheme = applyColorTheme;
  ShellTheme.getColorTheme = () => colorTheme;

  global.ShellTheme = ShellTheme;
})(window);
