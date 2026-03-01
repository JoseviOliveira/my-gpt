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

  const NAV_TEMPLATE = `
    <a href="/" class="docs-nav-back">← App</a>
    <div class="docs-nav-links">
      <a href="/docs/index.html">Home</a>
      <div class="docs-nav-group">
        <button class="docs-nav-summary" type="button">Learn</button>
        <div class="docs-nav-dropdown">
          <a href="/docs/user.html">User Guide</a>
          <a href="/docs/installation.html">Installation</a>
          <a href="/docs/audience.html">Who Is This For</a>
        </div>
      </div>
      <div class="docs-nav-group">
        <button class="docs-nav-summary" type="button">Project</button>
        <div class="docs-nav-dropdown">
          <a href="/docs/architecture.html">Architecture</a>
          <a href="/docs/engineering.html">Engineering</a>
          <a href="/docs/handwrite.html">About</a>
        </div>
      </div>
      <div class="docs-nav-group">
        <button class="docs-nav-summary" type="button">Perf</button>
        <div class="docs-nav-dropdown">
          <a href="/docs/benchmark_guided.html">Benchmark (Guided)</a>
          <a href="/docs/benchmark_autonomous_claude.html">Benchmark (Claude)</a>
          <a href="/docs/benchmark_monitor_guide.html">Live Monitor</a>
          <a href="/docs/dashboard.html">Analytics</a>
        </div>
      </div>
    </div>
  `;

  const DOC_ORDER = [
    '/docs/user.html',
    '/docs/installation.html',
    '/docs/audience.html',
    '/docs/architecture.html',
    '/docs/engineering.html',
    '/docs/handwrite.html',
    '/docs/benchmark_guided.html',
    '/docs/benchmark_autonomous_claude.html',
    '/docs/benchmark_monitor_guide.html',
    '/docs/dashboard.html',
  ];

  const DOC_LABELS = {
    '/docs/user.html': 'User Guide',
    '/docs/installation.html': 'Installation',
    '/docs/audience.html': 'Who Is This For',
    '/docs/architecture.html': 'Architecture',
    '/docs/engineering.html': 'Engineering',
    '/docs/handwrite.html': 'About',
    '/docs/benchmark_guided.html': 'Benchmark (Guided)',
    '/docs/benchmark_autonomous_claude.html': 'Benchmark (Claude)',
    '/docs/benchmark_monitor_guide.html': 'Live Monitor',
    '/docs/dashboard.html': 'Analytics',
    '/docs/index.html': 'Index',
  };

  function renderSharedNav() {
    const nav = document.querySelector('nav.docs-nav');
    if (!nav) return;
    nav.innerHTML = NAV_TEMPLATE;
  }

  function renderSharedFooter() {
    const footer = document.querySelector('footer.docs-footer');
    if (!footer) return;
    const currentPath = location.pathname;

    if (currentPath === '/docs/index.html') {
      footer.innerHTML = `<a href="/docs/user.html" class="docs-footer-next">User Guide →</a>`;
      return;
    }

    if (currentPath === '/docs/benchmark_monitor.html') {
      footer.innerHTML = `
        <a href="/docs/benchmark_autonomous_claude.html" class="docs-footer-prev">← Benchmark (Claude)</a>
        <a href="/docs/benchmark_monitor_guide.html" class="docs-footer-next">Live Monitor →</a>
      `;
      return;
    }

    const idx = DOC_ORDER.indexOf(currentPath);
    if (idx < 0) return;
    const prevPath = idx === 0 ? '/docs/index.html' : DOC_ORDER[idx - 1];
    const nextPath = idx === DOC_ORDER.length - 1 ? '/docs/index.html' : DOC_ORDER[idx + 1];
    const prevLabel = DOC_LABELS[prevPath] || 'Previous';
    const nextLabel = DOC_LABELS[nextPath] || 'Next';
    footer.innerHTML = `
      <a href="${prevPath}" class="docs-footer-prev">← ${prevLabel}</a>
      <a href="${nextPath}" class="docs-footer-next">${nextLabel} →</a>
    `;
  }

  renderSharedNav();
  renderSharedFooter();

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
