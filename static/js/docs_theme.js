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

  // Dropdown toggle — on small screens use position:fixed so no parent
  // overflow or stacking context can clip the panel.
  const navLinks = document.querySelector('.docs-nav-links');
  const MOBILE_BP = 640;

  function positionFixed(group) {
    if (window.innerWidth >= MOBILE_BP) return;
    const btn = group.querySelector('.docs-nav-summary');
    const drop = group.querySelector('.docs-nav-dropdown');
    if (!btn || !drop) return;
    const r = btn.getBoundingClientRect();
    const dropW = drop.offsetWidth || 118;
    let left = r.left + r.width / 2 - dropW / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - dropW - 8));
    drop.style.cssText = `position:fixed;top:${r.bottom + 6}px;left:${left}px;transform:none;`;
  }

  function resetPosition(group) {
    const drop = group.querySelector('.docs-nav-dropdown');
    if (drop) drop.style.cssText = '';
  }

  function closeAll() {
    document.querySelectorAll('.docs-nav-group.open').forEach((g) => {
      g.classList.remove('open');
      resetPosition(g);
    });
    if (navLinks) navLinks.classList.remove('has-open-dropdown');
  }

  function handleToggle(e, btn) {
    if (!btn) return;
    if (e && typeof e.preventDefault === 'function') e.preventDefault();
    const group = btn.closest('.docs-nav-group');
    if (!group) return;
    const isOpen = group.classList.contains('open');
    closeAll();
    if (!isOpen) {
      group.classList.add('open');
      if (navLinks) navLinks.classList.add('has-open-dropdown');
      positionFixed(group);
    }
    if (e && typeof e.stopPropagation === 'function') e.stopPropagation();
  }

  document.querySelectorAll('.docs-nav-summary').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      handleToggle(e, btn);
    });
  });

  // Close when tapping outside
  document.addEventListener('click', closeAll);

  // Highlight the active nav item and its parent group
  const currentPath = location.pathname;
  document.querySelectorAll('.docs-nav-links > a, .docs-nav-dropdown a').forEach((a) => {
    if (a.getAttribute('href') === currentPath) {
      a.classList.add('active');
      const group = a.closest('.docs-nav-group');
      if (group) group.classList.add('active-group');
    }
  });

  // Optional transient orientation tip for table-heavy docs pages.
  const orientationTip = document.body?.dataset?.orientationTip;
  if (orientationTip) {
    const isTouch = navigator.maxTouchPoints > 0 || 'ontouchstart' in window;
    const isPortrait = window.matchMedia && window.matchMedia('(orientation: portrait)').matches;
    const isSmallScreen = window.innerWidth <= 1024;
    if (isTouch && isPortrait && isSmallScreen) {
      const toast = document.createElement('div');
      toast.className = 'docs-orientation-toast';
      toast.setAttribute('role', 'status');
      toast.setAttribute('aria-live', 'polite');
      toast.innerHTML = `
<svg width="120" height="120" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
  <style>
    .phone-container {
      transform-origin: center;
      animation: rotate-device 3s ease-in-out infinite;
    }

    @keyframes rotate-device {
      0%, 15% { transform: rotate(0deg); }
      45%, 70% { transform: rotate(-90deg); }
      95%, 100% { transform: rotate(0deg); }
    }

    .frame {
      fill: none;
      stroke: #1a1a1a;
      stroke-width: 2.5;
    }

    .island {
      fill: #1a1a1a;
    }
  </style>

  <g class="phone-container">
    <rect class="frame" x="33" y="20" width="34" height="60" rx="7" />
    
    <rect class="island" x="43" y="24" width="14" height="3" rx="1.5" />
  </g>
</svg>
        <span class="docs-orientation-toast-text">${orientationTip}</span>
      `;
      document.body.appendChild(toast);
      requestAnimationFrame(() => toast.classList.add('show'));
      setTimeout(() => {
        toast.classList.remove('show');
        toast.classList.add('hide');
      }, 3400);
      setTimeout(() => toast.remove(), 3900);
    }
  }
})();
