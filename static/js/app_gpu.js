(function initGpuGaugeModule(global){
  const gpuGaugeEl = document.getElementById('gpuGauge');
  if (!gpuGaugeEl) return;
  const gpuGaugeValueEls = Array.from(gpuGaugeEl.querySelectorAll('[data-gpu-value]'));
  const store = global.localStorage;
  const forceShow = Boolean(global.GPU_GAUGE_ALWAYS_SHOW);

  const isLogged = () => {
    const shell = global.Shell;
    if (shell && typeof shell.isLogged === 'function') {
      try { return shell.isLogged(); } catch {}
    }
    return true;
  };

  const credHeader = () => {
    const shell = global.Shell;
    if (shell && typeof shell.credHeader === 'function') {
      try { return shell.credHeader(); } catch {}
    }
    return {};
  };

  function setGpuGaugeLevel(value){
    const GPU_LEVEL_WARM = 31;
    const GPU_LEVEL_HOT = 76;
    let level = 'cool';
    if (value >= GPU_LEVEL_HOT) level = 'hot';
    else if (value >= GPU_LEVEL_WARM) level = 'warm';
    gpuGaugeEl.dataset.level = level;
  }

  function updateGpuGaugeDisplay(data){
    if (!data || !data.available || typeof data.utilization !== 'number') {
      if (forceShow) {
        gpuGaugeEl.classList.remove('hidden');
        gpuGaugeValueEls.forEach((el) => {
          el.textContent = `--%`;
        });
        gpuGaugeEl.setAttribute('aria-label', 'GPU busy');
        setGpuGaugeLevel(0);
        return true;
      }
      gpuGaugeEl.classList.add('hidden');
      return false;
    }
    const rawValue = Math.max(0, Math.min(100, Math.round(data.utilization)));
    if (rawValue < 6 && !forceShow) {
      return false;
    }
    const displayValue = Math.max(1, Math.min(99, rawValue));
    gpuGaugeEl.classList.remove('hidden');
    gpuGaugeValueEls.forEach((el) => {
      el.textContent = `${displayValue}%`;
    });
    gpuGaugeEl.setAttribute('aria-label', `GPU busy ${displayValue}%`);
    setGpuGaugeLevel(rawValue);
    return true;
  }

  function initGpuGauge(){
    let hideTimer = null;
    let fadeTimer = null;
    const GPU_VARIANT_STORAGE_KEY = 'gpuGaugeVariant.v1';
    const GPU_VARIANTS = ['pill', 'text'];
    const normalizeVariant = (value) => {
      const key = String(value || '').toLowerCase();
      return GPU_VARIANTS.includes(key) ? key : null;
    };
    const getStoredVariant = () => {
      try { return normalizeVariant(store?.getItem(GPU_VARIANT_STORAGE_KEY)); } catch {}
      return null;
    };
    const setStoredVariant = (value) => {
      try { store?.setItem(GPU_VARIANT_STORAGE_KEY, value); } catch {}
    };
    const getDefaultVariant = () => {
      const ua = navigator.userAgent || '';
      const platform = navigator.platform || '';
      const maxTouchPoints = navigator.maxTouchPoints || 0;
      const isIpad = /iPad/i.test(ua) || (platform === 'MacIntel' && maxTouchPoints > 1);
      const isIphone = /iPhone|iPod/i.test(ua);
      const isAndroid = /Android/i.test(ua);
      const isMobile = isIphone || (isAndroid && /Mobile/i.test(ua));
      if (isMobile) return 'pill';
      return 'text';
    };
    const setVariant = (value) => {
      const next = normalizeVariant(value) || 'ring';
      gpuGaugeEl.dataset.variant = next;
      setStoredVariant(next);
    };
    const cycleVariant = () => {
      const current = normalizeVariant(gpuGaugeEl.dataset.variant) || 'ring';
      const idx = GPU_VARIANTS.indexOf(current);
      const next = GPU_VARIANTS[(idx + 1) % GPU_VARIANTS.length];
      setVariant(next);
    };
    const showGauge = () => {
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
      if (fadeTimer) {
        clearTimeout(fadeTimer);
        fadeTimer = null;
      }
      if (gpuGaugeEl.classList.contains('hidden')) {
        gpuGaugeEl.classList.remove('hidden');
        requestAnimationFrame(() => gpuGaugeEl.classList.add('is-visible'));
      } else {
        gpuGaugeEl.classList.add('is-visible');
      }
    };
    const scheduleHide = () => {
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(() => {
        gpuGaugeEl.classList.remove('is-visible');
        if (fadeTimer) clearTimeout(fadeTimer);
        fadeTimer = setTimeout(() => {
          gpuGaugeEl.classList.add('hidden');
        }, 1000);
      }, 5000);
    };
    setVariant(getStoredVariant() || getDefaultVariant());
    gpuGaugeEl.setAttribute('role', 'button');
    gpuGaugeEl.tabIndex = 0;
    gpuGaugeEl.addEventListener('click', cycleVariant);
    gpuGaugeEl.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        cycleVariant();
      }
    });
    let gaugeTimer = null;
    const stopGauge = () => {
      if (gaugeTimer) {
        clearInterval(gaugeTimer);
        gaugeTimer = null;
      }
    };
    const pollInterval = () => (window.IS_GUEST ? 30000 : 5000);
    const tick = async () => {
      if (document.hidden) {
        return;
      }
      if (!isLogged()) {
        if (forceShow) {
          updateGpuGaugeDisplay({ available: false, utilization: null });
          showGauge();
        } else {
          scheduleHide();
        }
        return;
      }
      try {
        const headers = Object.assign({}, credHeader());
        const resp = await fetch('/api/gpu', { headers, credentials: 'same-origin' });
        if (!resp.ok) {
          if (resp.status === 404) stopGauge();
          if (forceShow) {
            updateGpuGaugeDisplay({ available: false, utilization: null });
            showGauge();
          } else {
            scheduleHide();
          }
          window.APP_GPU_UTIL = null;
          try {
            window.dispatchEvent(new CustomEvent('gpu:updated', { detail: { util: null } }));
          } catch {}
          return;
        }
        const data = await resp.json();
        window.APP_GPU_UTIL = Number.isFinite(data?.utilization) ? data.utilization : null;
        try {
          window.dispatchEvent(new CustomEvent('gpu:updated', { detail: { util: window.APP_GPU_UTIL } }));
        } catch {}
        if (updateGpuGaugeDisplay(data)) {
          showGauge();
        } else if (!forceShow) {
          scheduleHide();
        } else {
          showGauge();
        }
      } catch {
        if (forceShow) {
          updateGpuGaugeDisplay({ available: false, utilization: null });
          showGauge();
        } else {
          scheduleHide();
        }
        window.APP_GPU_UTIL = null;
        try {
          window.dispatchEvent(new CustomEvent('gpu:updated', { detail: { util: null } }));
        } catch {}
      }
    };
    const startGauge = () => {
      stopGauge();
      tick();
      gaugeTimer = setInterval(tick, pollInterval());
    };

    startGauge();
    window.addEventListener('guest:ready', startGauge);
  }

  initGpuGauge();
})(window);
