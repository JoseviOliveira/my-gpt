(function initAppSidebar(){
  const side = document.getElementById('side');
  if (!side) return;

  const isMobile = () => window.matchMedia('(max-width: 900px)').matches;
  
  // Only apply custom width on desktop when sidebar is open
  const applyStoredWidth = () => {
    if (isMobile()) {
      side.style.width = '';
      side.style.minWidth = '';
      return;
    }
    const saved = localStorage.getItem('sideWidth');
    if (saved && !document.body.classList.contains('sidebar-closed')) {
      const w = Math.max(180, Math.min(600, +saved || 256));
      side.style.width = `${w}px`;
      side.style.minWidth = `${w}px`;
    }
  };
  
  applyStoredWidth();

  const handle = document.createElement('div');
  handle.className = 'side-resizer';
  side.appendChild(handle);

  let startX = 0;
  let startW = 0;
  let dragging = false;

  const onMove = (ev) => {
    if (!dragging) return;
    const clientX = ev.touches ? ev.touches[0].clientX : ev.clientX;
    const dx = clientX - startX;
    const w = Math.max(180, Math.min(640, startW + dx));
    side.style.width = `${w}px`;
    side.style.minWidth = `${w}px`;
    localStorage.setItem('sideWidth', String(w));
  };

  const onUp = () => {
    if (!dragging) return;
    dragging = false;
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    document.removeEventListener('touchmove', onMove);
    document.removeEventListener('touchend', onUp);
    document.body.style.cursor = '';
    if (typeof window.logUserAction === 'function') {
      const current = Math.round(side.getBoundingClientRect().width || 0);
      if (current && Math.abs(current - startW) >= 8) {
        try { window.logUserAction('Settings & Navigation', `Sidebar resize: ${current}px`); } catch {}
      }
    }
  };

  const onDown = (ev) => {
    if (isMobile()) return;
    // Don't resize if sidebar is closed
    if (document.body.classList.contains('sidebar-closed')) return;
    ev.preventDefault();
    ev.stopPropagation();
    dragging = true;
    startX = ev.touches ? ev.touches[0].clientX : ev.clientX;
    startW = side.offsetWidth;
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    document.addEventListener('touchmove', onMove, { passive: false });
    document.addEventListener('touchend', onUp);
    document.body.style.cursor = 'col-resize';
  };

  handle.addEventListener('mousedown', onDown);
  handle.addEventListener('touchstart', onDown, { passive: false });
  
  // Reapply width when sidebar opens
  const observer = new MutationObserver(() => {
    if (!document.body.classList.contains('sidebar-closed') && !isMobile()) {
      applyStoredWidth();
    } else if (document.body.classList.contains('sidebar-closed')) {
      // Clear inline styles when closing so CSS transition works
      side.style.width = '';
      side.style.minWidth = '';
    }
  });
  observer.observe(document.body, { attributes: true, attributeFilter: ['class'] });
})();
