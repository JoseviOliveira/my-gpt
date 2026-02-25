(() => {
  const storageKey = 'theme';
  const root = document.documentElement;
  const prefersDark = typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null;

  function readStoredTheme() {
    try {
      const saved = localStorage.getItem(storageKey);
      return saved === 'dark' || saved === 'light' ? saved : null;
    } catch {
      return null;
    }
  }

  function applyTheme(theme) {
    const isDark = theme === 'dark';
    if (isDark) {
      root.dataset.theme = 'dark';
      root.classList.add('dark');
    } else {
      root.removeAttribute('data-theme');
      root.classList.remove('dark');
    }
  }

  function resolveTheme() {
    return readStoredTheme() || (prefersDark && prefersDark.matches ? 'dark' : 'light');
  }

  applyTheme(resolveTheme());

  if (prefersDark) {
    const handler = (event) => {
      if (readStoredTheme()) return;
      applyTheme(event.matches ? 'dark' : 'light');
    };
    if (typeof prefersDark.addEventListener === 'function') {
      prefersDark.addEventListener('change', handler);
    } else if (typeof prefersDark.addListener === 'function') {
      prefersDark.addListener(handler);
    }
  }

  const backLinks = document.querySelectorAll('a.docs-nav-back, a[href="/"]');
  if (backLinks.length) {
    backLinks.forEach((link) => {
      link.addEventListener('click', () => {
        try { sessionStorage.setItem('docsBack', '1'); } catch {}
      });
    });
  }
})();
