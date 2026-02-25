/*
 * app_tour.js — Welcome tour/walkthrough system
 * Extracted from app.js to reduce file size
 */
(function(global){
  const store = global.localStorage;
  const TOUR_STORAGE_KEY = 'welcomeTourSeen.v1';
  const TOUR_BTN_SUBTLE = 'tour-btn';
  const TOUR_BTN_PRIMARY = 'tour-btn tour-btn-primary';

  const tourSteps = [
    {
      selector: '#newchat',
      title: 'Start a new chat',
      body: 'Use "＋ New chat" whenever you want a blank conversation without losing your history.',
    },
    {
      selector: '#chatlist',
      title: 'Browse your sessions',
      body: 'All of your saved chats live here. Click a title to jump back into that conversation.',
      highlightSelector: '#chatlist',
      anchor: 'side',
      preferSide: 'right',
      highlightRadius: '12px',
      highlightOffset: { x: 12, y: 0 },
    },
    {
      selector: '#modeBox',
      title: 'Response modes',
      body: 'Fast is concise, Normal is balanced, Deep provides thorough answers. Switch any time.',
      highlightClosest: '#modeBox',
    },
    {
      selector: '#settingsToggle',
      title: 'Config & docs',
      body: 'Open the gear to change speech options, toggle dark mode, or read the docs.',
      highlightSelector: '#settingsToggle',
    },
    {
      selector: '#micBtn',
      title: 'Speech tools',
      body: 'Use the mic to dictate prompts or tap the speaker button (after replies) to hear them aloud.',
      offsetHalfWidth: true,
      highlightClosest: '.composer',
      secondaryHighlight: '#speakBtn',
      secondaryOffset: { x: 0, y: -10 },
    },
  ];

  let tourOverlayEl = null;
  let tourCardEl = null;
  let tourTitleEl = null;
  let tourBodyEl = null;
  let tourBackBtn = null;
  let tourNextBtn = null;
  let tourSkipBtn = null;
  let tourActive = false;
  let tourIndex = -1;
  let tourTarget = null;
  let tourHighlightEl = null;
  let tourCurrentStep = null;
  let tourRafScheduled = false;
  let tourOverlayExtraClass = '';
  let tourHighlightClone = null;
  let tourSecondaryClone = null;

  function ensureTourElements(){
    if (tourOverlayEl) return;
    tourOverlayEl = document.createElement('div');
    tourOverlayEl.className = 'tour-overlay hidden';
    tourCardEl = document.createElement('div');
    tourCardEl.className = 'tour-card';
    tourCardEl.innerHTML = `
      <div class="tour-card-body">
        <div class="tour-badge">
          <span class="tour-badge-dot"></span>
          <span>Quick tour</span>
        </div>
        <h3></h3>
        <p></p>
      </div>
      <div class="tour-actions">
        <div class="tour-actions-left">
          <button type="button" class="${TOUR_BTN_SUBTLE}" data-tour="back">Back</button>
          <button type="button" class="${TOUR_BTN_SUBTLE}" data-tour="skip">Skip</button>
        </div>
        <button type="button" class="${TOUR_BTN_PRIMARY}" data-tour="next">Next</button>
      </div>
    `;
    tourTitleEl = tourCardEl.querySelector('h3');
    tourBodyEl = tourCardEl.querySelector('p');
    tourBackBtn = tourCardEl.querySelector('[data-tour="back"]');
    tourSkipBtn = tourCardEl.querySelector('[data-tour="skip"]');
    tourNextBtn = tourCardEl.querySelector('[data-tour="next"]');
    tourOverlayEl.appendChild(tourCardEl);
    document.body.appendChild(tourOverlayEl);

    tourBackBtn.addEventListener('click', () => showTourStep(tourIndex - 1));
    tourSkipBtn.addEventListener('click', () => endTour(true));
    tourNextBtn.addEventListener('click', () => {
      if (tourIndex >= tourSteps.length - 1) {
        endTour(true);
      } else {
        showTourStep(tourIndex + 1);
      }
    });
    document.addEventListener('keydown', handleTourHotkeys);
    tourOverlayEl.addEventListener('click', (event) => {
      if (event.target === tourOverlayEl) {
        endTour(false);
      }
    });
  }

  function clearTourHighlight(){
    if (tourHighlightClone) {
      tourHighlightClone.remove();
      tourHighlightClone = null;
    }
    if (tourSecondaryClone) {
      tourSecondaryClone.remove();
      tourSecondaryClone = null;
    }
    if (tourHighlightEl) {
      tourHighlightEl.classList.remove('tour-highlight', 'tour-rounded');
      tourHighlightEl = null;
    }
    tourTarget = null;
  }

  function applyTourOverlayClass(nextClass){
    if (!tourOverlayEl) return;
    if (tourOverlayExtraClass) {
      tourOverlayEl.classList.remove(tourOverlayExtraClass);
    }
    tourOverlayExtraClass = nextClass || '';
    if (tourOverlayExtraClass) {
      tourOverlayEl.classList.add(tourOverlayExtraClass);
    }
  }

  function normalizeGhost(ghost){
    if (!ghost || !ghost.style) return;
    ghost.style.margin = '0';
    ghost.style.transform = 'none';
    ghost.style.position = 'relative';
    ghost.style.top = '0';
    ghost.style.left = '0';
    // Ensure the ghost button matches the original's dimensions exactly
    if (ghost.id === 'newchat' || ghost.classList.contains('btn-newchat')) {
      ghost.style.width = '100%';
      ghost.style.height = '100%';
      ghost.style.marginTop = '0';
    }
  }

  function mountHighlightClone(){
    if (!tourOverlayEl || !tourHighlightEl) return;
    if (tourHighlightClone) {
      tourHighlightClone.remove();
      tourHighlightClone = null;
    }
    const rect = tourHighlightEl.getBoundingClientRect();
    const wrapper = document.createElement('div');
    wrapper.className = 'tour-highlight-clone tour-highlight-outline';
    wrapper.setAttribute('aria-hidden', 'true');
    const ghost = tourHighlightEl.cloneNode(true);
    ghost.removeAttribute('id');
    ghost.querySelectorAll('[id]').forEach((node) => node.removeAttribute('id'));
    normalizeGhost(ghost);
    wrapper.appendChild(ghost);
    const styles = window.getComputedStyle(tourHighlightEl);
    const radius = tourCurrentStep?.highlightRadius || styles.borderRadius || '12px';
    wrapper.style.borderRadius = radius;
    const bg = styles.backgroundColor;
    const rootStyles = window.getComputedStyle(document.documentElement);
    const fallbackBg = rootStyles.getPropertyValue('--panel').trim() || '#fff';
    wrapper.style.background = !bg || bg === 'transparent' || bg === 'rgba(0, 0, 0, 0)' ? fallbackBg : bg;
    wrapper.style.overflow = 'hidden';
    tourOverlayEl.appendChild(wrapper);
    tourHighlightClone = wrapper;
    updateHighlightClonePosition(rect);
    if (tourCurrentStep?.secondaryHighlight) {
      mountSecondaryHighlight();
    }
  }

  function updateHighlightClonePosition(rect){
    if (!tourHighlightClone || !tourHighlightEl) return;
    const bounds = rect || tourHighlightEl.getBoundingClientRect();
    tourHighlightClone.style.position = 'absolute';
    tourHighlightClone.style.pointerEvents = 'none';
    const padding = tourCurrentStep?.highlightPadding || 0;
    const offset = tourCurrentStep?.highlightOffset || { x: 0, y: 0 };
    tourHighlightClone.style.top = `${bounds.top - padding + (offset.y || 0)}px`;
    tourHighlightClone.style.left = `${bounds.left - padding + (offset.x || 0)}px`;
    tourHighlightClone.style.width = `${bounds.width + padding * 2}px`;
    tourHighlightClone.style.height = `${bounds.height + padding * 2}px`;
  }

  function mountSecondaryHighlight(){
    const selector = tourCurrentStep.secondaryHighlight;
    let secondaryTarget = null;
    if (typeof selector === 'function') {
      secondaryTarget = selector();
    } else if (selector) {
      const nodes = document.querySelectorAll(selector);
      secondaryTarget = nodes.length ? nodes[nodes.length - 1] : null;
    }
    if (!secondaryTarget) return;
    if (tourSecondaryClone) {
      tourSecondaryClone.remove();
      tourSecondaryClone = null;
    }
    const rect = secondaryTarget.getBoundingClientRect();
    const wrapper = document.createElement('div');
    wrapper.className = 'tour-highlight-clone tour-highlight-outline';
    wrapper.setAttribute('aria-hidden', 'true');
    const ghost = secondaryTarget.cloneNode(true);
    ghost.removeAttribute('id');
    ghost.querySelectorAll('[id]').forEach((node) => node.removeAttribute('id'));
    normalizeGhost(ghost);
    wrapper.appendChild(ghost);
    const styles = window.getComputedStyle(secondaryTarget);
    const radius = tourCurrentStep?.secondaryRadius || styles.borderRadius || '999px';
    wrapper.style.borderRadius = radius;
    const bg = styles.backgroundColor;
    wrapper.style.background = !bg || bg === 'transparent' || bg === 'rgba(0, 0, 0, 0)' ? '#fff' : bg;
    wrapper.style.overflow = 'hidden';
    tourOverlayEl.appendChild(wrapper);
    tourSecondaryClone = wrapper;
    updateSecondaryClonePosition(rect);
  }

  function updateSecondaryClonePosition(rect){
    if (!tourSecondaryClone || !tourCurrentStep?.secondaryHighlight) return;
    let target = null;
    const selector = tourCurrentStep.secondaryHighlight;
    if (typeof selector === 'function') target = selector();
    else if (selector) {
      const nodes = document.querySelectorAll(selector);
      target = nodes.length ? nodes[nodes.length - 1] : null;
    }
    if (!target) return;
    const bounds = rect || target.getBoundingClientRect();
    tourSecondaryClone.style.position = 'absolute';
    tourSecondaryClone.style.pointerEvents = 'none';
    const offset = tourCurrentStep.secondaryOffset || { x: 0, y: 0 };
    tourSecondaryClone.style.top = `${bounds.top + (offset.y || 0)}px`;
    tourSecondaryClone.style.left = `${bounds.left + (offset.x || 0)}px`;
    tourSecondaryClone.style.width = `${bounds.width}px`;
    tourSecondaryClone.style.height = `${bounds.height}px`;
  }

  function positionTourCard(){
    const anchor = tourCurrentStep?.anchor
      ? document.getElementById(tourCurrentStep.anchor)
      : (tourHighlightEl || tourTarget);
    if (!tourActive || !anchor || !tourCardEl) return;
    updateHighlightClonePosition();
    updateSecondaryClonePosition();
    const rect = anchor.getBoundingClientRect();
    tourCardEl.style.top = '0px';
    tourCardEl.style.left = '0px';
    const cardRect = tourCardEl.getBoundingClientRect();
    let top;
    if (tourCurrentStep?.anchor === 'side') {
      top = rect.top + rect.height / 2 - cardRect.height / 2;
    } else {
      const viewportTop = rect.bottom + 16;
      const viewportBottom = rect.top - cardRect.height - 16;
      const maxTop = window.innerHeight - cardRect.height - 12;
      top = Math.min(viewportTop, maxTop);
      if (top < 12) {
        top = Math.max(viewportBottom, 12);
      }
      if (top + cardRect.height > rect.top && rect.top > cardRect.height + 24) {
        top = rect.top - cardRect.height - 24;
      }
    }
    top = Math.min(Math.max(top, 12), window.innerHeight - cardRect.height - 12);
    let left;
    if (tourCurrentStep?.preferSide === 'right') {
      left = rect.right + 16;
    } else if (tourCurrentStep?.preferSide === 'left') {
      left = rect.left - cardRect.width - 16;
    } else {
      left = rect.left + rect.width / 2 - cardRect.width / 2;
    }
    if (left + cardRect.width > window.innerWidth - 12) {
      left = window.innerWidth - cardRect.width - 12;
    }
    if (left < 12) {
      left = 12;
    }
    tourCardEl.style.top = `${top}px`;
    tourCardEl.style.left = `${left}px`;
  }

  function scheduleTourReposition(){
    if (tourRafScheduled) return;
    tourRafScheduled = true;
    requestAnimationFrame(() => {
      tourRafScheduled = false;
      positionTourCard();
    });
  }

  function settleHighlightPosition(duration = 1000){
    const start = performance.now();
    const tick = () => {
      if (!tourActive) return;
      updateHighlightClonePosition();
      updateSecondaryClonePosition();
      positionTourCard();
      if (performance.now() - start < duration) {
        requestAnimationFrame(tick);
      }
    };
    requestAnimationFrame(tick);
  }

  window.addEventListener('resize', scheduleTourReposition);
  window.addEventListener('scroll', scheduleTourReposition, true);

  function showTourStep(index){
    ensureTourElements();
    if (!tourSteps.length) return;
    let idx = Math.max(0, index);
    while (idx < tourSteps.length) {
      const exists = document.querySelector(tourSteps[idx].selector);
      if (exists) break;
      idx += 1;
    }
    if (idx >= tourSteps.length) {
      endTour(true);
      return;
    }
    const step = tourSteps[idx];
    tourCurrentStep = step;
    const target = document.querySelector(step.selector);
    if (!target) {
      showTourStep(idx + 1);
      return;
    }
    tourActive = true;
    tourIndex = idx;
    clearTourHighlight();
    tourTarget = target;
    applyTourOverlayClass(step.overlayClass || '');
    
    // Manage sidebar visibility on mobile based on step index
    const isMobile = window.matchMedia && window.matchMedia('(max-width: 900px)').matches;
    if (isMobile) {
      if (idx <= 1) {
        // Show sidebar for first 2 steps (need sidebar items)
        if (window?.Shell?.setMobileSidebar) {
          window.Shell.setMobileSidebar(true);
        }
        document.body.classList.add('sidebar-open');
        document.body.classList.remove('sidebar-closed');
      } else {
        // Hide sidebar for remaining steps
        if (window?.Shell?.setMobileSidebar) {
          window.Shell.setMobileSidebar(false);
        }
        document.body.classList.remove('sidebar-open');
        document.body.classList.add('sidebar-closed');
      }
    }
    
    const highlightEl = step.highlightSelector
      ? document.querySelector(step.highlightSelector)
      : step.highlightClosest
        ? target.closest(step.highlightClosest)
        : target;
    tourHighlightEl = highlightEl || target;
    mountHighlightClone();
    tourOverlayEl.classList.remove('hidden');
    tourTitleEl.textContent = step.title;
    tourBodyEl.textContent = step.body;
    tourBackBtn.disabled = idx === 0;
    tourNextBtn.textContent = idx >= tourSteps.length - 1 ? 'Finish' : 'Next';
    const anchorRect = (tourHighlightEl || target).getBoundingClientRect();
    const fullyVisible = anchorRect.top >= 0 && anchorRect.bottom <= window.innerHeight;
    if (!fullyVisible) {
      (tourHighlightEl || target).scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
      setTimeout(() => positionTourCard(), 350);
    } else {
      positionTourCard();
    }
    setTimeout(() => {
      updateHighlightClonePosition();
      updateSecondaryClonePosition();
      positionTourCard();
    }, 120);
    settleHighlightPosition(1100);
  }

  function endTour(saveProgress){
    if (!tourActive) return;
    tourActive = false;
    tourIndex = -1;
    tourCurrentStep = null;
    tourOverlayEl?.classList.add('hidden');
    clearTourHighlight();
    applyTourOverlayClass('');
    if (saveProgress && store) {
      try { store.setItem(TOUR_STORAGE_KEY, 'done'); } catch {}
    }
    document.removeEventListener('keydown', handleTourHotkeys);
  }

  function startTour(force, attempt = 0){
    if (tourActive) return;
    const sidebarWasClosed = !document.body.classList.contains('sidebar-open');
    if (window?.Shell?.closeSettingsPanel) {
      window.Shell.closeSettingsPanel();
    }
    if (window?.Shell?.setMobileSidebar) {
      window.Shell.setMobileSidebar(true);
    }
    document.body.classList.remove('sidebar-closed');
    document.body.classList.add('sidebar-open');
    if (!force && store) {
      try {
        if (store.getItem(TOUR_STORAGE_KEY) === 'done') return;
      } catch {}
    }
    if (document.body.classList.contains('splash-open')) {
      if (attempt > 6) return;
      setTimeout(() => startTour(force, attempt + 1), 600);
      return;
    }
    ensureTourElements();
    if (sidebarWasClosed) {
      setTimeout(() => {
        if (!tourActive) {
          showTourStep(0);
        }
      }, 1100);
      return;
    }
    showTourStep(0);
  }

  function handleTourHotkeys(event){
    if (!tourActive) return;
    if (event.key === 'ArrowRight' || event.key === 'Enter') {
      event.preventDefault();
      tourNextBtn?.click();
      return;
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      tourBackBtn?.click();
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      endTour(false);
    }
  }

  function isActive(){ return tourActive; }
  function hasSeen(){
    if (!store) return false;
    try { return store.getItem(TOUR_STORAGE_KEY) === 'done'; } catch { return false; }
  }

  // Export to global
  global.AppTour = {
    start: startTour,
    end: endTour,
    isActive,
    hasSeen,
    STORAGE_KEY: TOUR_STORAGE_KEY,
  };
})(window);
