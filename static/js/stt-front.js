/*
 * stt-front.js — browser Web Speech API integration
 * - Activates when the user selects the 'browser' STT mode
 * - Handles mic button UX (click to toggle, flag click cycles language)
 * - Streams interim/final transcripts into the composer without auto-submit
 * - Persists language choice via shared STT helpers
 */
(function initBrowserSTT(global){
  const micBtn = document.getElementById('micBtn');
  let micLangBtn = document.getElementById('micLang');
  const textarea = document.getElementById('t');
  const statusEl = document.getElementById('status');
  if (!micBtn || !textarea) return;

  const MIC_IDLE_LABEL = 'mic';
  const MIC_RECORDING_LABEL = 'recording ●';

  const micLangAnchor = micBtn ? micBtn : null;
  function mountMicLang() {
    if (micLangBtn && micLangAnchor && !micLangBtn.isConnected) {
      micLangAnchor.insertAdjacentElement('afterend', micLangBtn);
    }
  }
  function unmountMicLang() {
    if (micLangBtn && micLangBtn.isConnected) {
      micLangBtn.remove();
    }
  }

  if (!micLangBtn && typeof document !== 'undefined') {
    micLangBtn = document.createElement('button');
    micLangBtn.id = 'micLang';
    micLangBtn.type = 'button';
    micLangBtn.className = 'button mic-lang hidden';
    micLangBtn.textContent = '🌐';
    micLangBtn.setAttribute('aria-label', 'Speech language');
    micLangBtn.title = 'Speech language';
    mountMicLang();
  } else if (micLangBtn) {
    micLangBtn.classList.add('hidden');
  }

  const STT = global.STT || {};
  const SR = global.SpeechRecognition || global.webkitSpeechRecognition;
  console.log('[stt-front] SpeechRecognition availability', {
    supported: !!SR,
    api: global.SpeechRecognition ? 'SpeechRecognition' : global.webkitSpeechRecognition ? 'webkitSpeechRecognition' : 'none'
  });
  if (!SR) {
    console.debug('[stt-front] SpeechRecognition not available in this browser.');
    if (micLangBtn) {
      micLangBtn.disabled = true;
      micLangBtn.title = 'Speech input unavailable';
      micLangBtn.dataset.sttMode = 'unsupported';
      micLangBtn.classList.add('hidden');
      unmountMicLang();
    }
    const browserOption = document.querySelector('[data-settings-stt][data-stt-mode="browser"]');
    if (browserOption) {
      browserOption.setAttribute('disabled', 'disabled');
      browserOption.setAttribute('aria-disabled', 'true');
    }
    const getMode = typeof STT.getMode === 'function' ? STT.getMode.bind(STT) : null;
    const setMode = typeof STT.setMode === 'function' ? STT.setMode.bind(STT) : null;
    const getOptions = typeof STT.getAvailableModes === 'function'
      ? STT.getAvailableModes.bind(STT)
      : null;
    if (getMode && setMode) {
      const current = getMode();
      if (current === 'browser') {
        const whisperOption = document.querySelector('[data-settings-stt][data-stt-mode="whisper"]');
        const fallback = getOptions ? (getOptions().find(mode => mode !== 'browser') || 'whisper') : 'whisper';
        if (whisperOption && whisperOption.hasAttribute('disabled') && fallback === 'whisper') {
          micBtn.dataset.sttMode = 'unsupported';
          applyMicState('disabled', 'Speech input unavailable');
        } else {
          setMode(fallback);
        }
      }
    }
    return;
  }

  const SUPPORTED_LANGS = ['en-US', 'fr-FR', 'es-ES'];
  const LANG_FLAGS = {
    'en-US': '🇺🇸',
    'fr-FR': '🇫🇷',
    'es-ES': '🇪🇸'
  };
  const LANG_LABELS = {
    'en-US': 'English (US)',
    'fr-FR': 'Français',
    'es-ES': 'Español'
  };
  const DEFAULT_LANG = SUPPORTED_LANGS[0];
  const RECORDING_PULSE_COLOR = '#f87171';

  const PLACEHOLDERS = {
    'en-US': 'Ask anything…',
    'fr-FR': 'Demandez-moi n\u2019importe quoi…',
    'es-ES': 'Pregunta lo que quieras…'
  };

  const basePlaceholder = textarea.getAttribute('placeholder') || PLACEHOLDERS['en-US'];

  let rec = null;
  let listening = false;
  let baseBefore = '';
  let enabled = false;
  let statusTimer = null;
  let currentLang = normalizeLang(typeof STT.getStoredLang === 'function' ? STT.getStoredLang() : '') || DEFAULT_LANG;
  let activeMode = 'browser';

  function setComposerPlaceholder(lang){
    const ph = PLACEHOLDERS[lang] || PLACEHOLDERS['en-US'];
    textarea.setAttribute('placeholder', ph);
  }

  function normalizeLang(value){
    const raw = String(value || '').trim();
    if (!raw) return '';
    const direct = SUPPORTED_LANGS.find((code) => code.toLowerCase() === raw.toLowerCase());
    if (direct) return direct;
    const short = raw.toLowerCase();
    const loose = SUPPORTED_LANGS.find((code) => code.toLowerCase().startsWith(short));
    return loose || '';
  }

  function nextLang(current){
    const normalized = normalizeLang(current) || DEFAULT_LANG;
    const idx = SUPPORTED_LANGS.indexOf(normalized);
    const nextIdx = idx === -1 ? 0 : (idx + 1) % SUPPORTED_LANGS.length;
    return SUPPORTED_LANGS[nextIdx];
  }

  function getLang(){
    const stored = typeof STT.getStoredLang === 'function'
      ? STT.getStoredLang()
      : currentLang;
    const normalized = normalizeLang(stored) || currentLang || DEFAULT_LANG;
    if (normalized !== currentLang) currentLang = normalized;
    return normalized;
  }

  function getLangLabel(lang){
    return LANG_LABELS[lang] || lang;
  }

  function renderLangIndicator(lang){
    if (!micLangBtn) return;
    const normalized = normalizeLang(lang) || DEFAULT_LANG;
    const label = getLangLabel(normalized);
    const display = activeMode === 'browser'
      ? (normalized.split('-')[0] || '').toUpperCase()
      : (LANG_FLAGS[normalized] || '🌐');
    micLangBtn.textContent = display;
    micLangBtn.dataset.lang = normalized;
    micLangBtn.setAttribute('aria-label', `Speech language: ${label}`);
    micLangBtn.title = micLangBtn.disabled
      ? `Speech language (${label})`
      : `Speech language: ${label} (click to change)`;
    micLangBtn.classList.toggle('using-code', activeMode === 'browser');
  }

  function setLang(lang, { source = 'manual' } = {}){
    const previous = getLang();
    const candidate = normalizeLang(lang);
    const normalized = candidate || previous || DEFAULT_LANG;
    currentLang = normalized;
    if (typeof STT.setStoredLang === 'function') {
      try { STT.setStoredLang(normalized); }
      catch (err) { console.warn('[stt-front] failed to persist language', err); }
    }
    renderLangIndicator(normalized);
    if (enabled) {
      setComposerPlaceholder(normalized);
    }
    setMicVisual(listening);
    if (previous !== normalized) {
      if (statusEl) {
        statusEl.textContent = `STT: ${getLangLabel(normalized)}`;
        if (statusTimer) clearTimeout(statusTimer);
        statusTimer = setTimeout(() => {
          if (statusEl.textContent.startsWith('STT:')) statusEl.textContent = '';
        }, 1500);
      }
      try {
        console.log('[stt-front] language change', { from: previous, to: normalized, source });
      } catch {}
    }
    return normalized;
  }

  renderLangIndicator(getLang());

  function applyMicState(state, titleText){
    micBtn.classList.remove('recording', 'decoding', 'disabled');
    if (state === 'recording') {
      micBtn.classList.add('recording');
    } else if (state === 'decoding') {
      micBtn.classList.add('decoding');
    } else if (state === 'disabled') {
      micBtn.classList.add('disabled');
    } else {
    }
    micBtn.disabled = state === 'disabled';
    micBtn.title = titleText || 'Microphone';
  }

  function setMicVisual(active){
    const lang = getLang();
    const label = getLangLabel(lang);
    if (!enabled) {
      applyMicState('disabled', 'Speech input unavailable');
      return;
    }
    if (active) {
      applyMicState('recording', `Tap to stop — ${label}`);
    } else {
      applyMicState('idle', `Tap to talk — ${label}`);
    }
  }

  function setMicDecoding(){
    if (!enabled) {
      applyMicState('disabled', 'Speech input unavailable');
      return;
    }
    const lang = getLang();
    const label = getLangLabel(lang);
    applyMicState('decoding', `Decoding speech — ${label}`);
  }

  function resetComposer(){
    textarea.style.height = 'auto';
    textarea.style.height = textarea.scrollHeight + 'px';
  }

  function stopRec(){
    if (!rec) return;
    setMicDecoding();
    try { window.logUserAction?.('Speech', 'STT stop'); } catch {}
    try { rec.stop(); }
    catch (err) { console.warn('[stt-front] stop error', err); }
  }

  function startRec(){
    if (!enabled || listening) return;
    baseBefore = textarea.value;
    rec = new SR();
    rec.lang = getLang();
    setComposerPlaceholder(rec.lang);
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.continuous = false;

    let interim = '';
    let final = '';

    rec.onstart = () => { listening = true; setMicVisual(true); };
    rec.onend = () => {
      listening = false;
      setMicDecoding();
      const text = (final || '').trim();
      const currentValue = textarea.value;
      // Respect last user interaction: if the user submitted or edited the text, don't overwrite.
      if (currentValue !== baseBefore) {
        resetComposer();
        textarea.focus();
        setMicVisual(false);
        return;
      }
      if (!text) {
        textarea.value = baseBefore;
      } else {
        const head = baseBefore.replace(/\s*$/, text ? ' ' : '');
        textarea.value = head + text;
        try { window.logUserAction?.('Speech', 'STT result accepted'); } catch {}
      }
      resetComposer();
      textarea.focus();
      setMicVisual(false);
    };

    rec.onerror = (event) => {
      listening = false;
      setMicVisual(false);
      const text = (final || '').trim();
      if (!text && textarea.value === baseBefore) textarea.value = baseBefore;
      resetComposer();
      textarea.focus();
      if (event && event.error !== 'aborted') {
        console.warn('[stt-front] error', event.error || event);
      }
    };

    rec.onresult = (event) => {
      interim = '';
      final = final || '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const res = event.results[i];
        if (res.isFinal) final += res[0].transcript;
        else interim += res[0].transcript;
      }
      const ghost = (final + (interim ? ' ' + interim : '')).trim();
      const head = baseBefore.replace(/\s*$/, ghost ? ' ' : '');
      textarea.value = head + ghost;
      resetComposer();
    };

    try {
      try { window.logUserAction?.('Speech', 'STT start'); } catch {}
      rec.start();
    } catch (err) {
      console.warn('[stt-front] start error', err);
      try { rec.stop(); } catch {}
    }
  }

  micBtn.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!enabled) return;
    if (!listening) startRec();
    else stopRec();
  });

  if (micLangBtn) {
    micLangBtn.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (micLangBtn.disabled) return;
      const current = getLang();
      const next = typeof STT.cycleLang === 'function'
        ? STT.cycleLang(current)
        : nextLang(current);
      const normalized = normalizeLang(next) || nextLang(current);
      setLang(normalized, { source: 'flag-click' });
    });
  }

  function applyMode(mode){
    activeMode = mode;
    const nextEnabled = mode === 'browser';
    micBtn.dataset.sttMode = mode;
    if (micLangBtn) {
      micLangBtn.dataset.sttMode = mode;
      if (!nextEnabled) {
        micLangBtn.disabled = true;
        micLangBtn.classList.add('hidden');
        unmountMicLang();
      } else {
        mountMicLang();
        micLangBtn.disabled = false;
        micLangBtn.classList.remove('hidden');
        micLangBtn.style.opacity = '';
        renderLangIndicator(getLang());
      }
    }
    if (nextEnabled === enabled) return;
    enabled = nextEnabled;
    if (!enabled) {
      if (listening) stopRec();
      setMicVisual(false);
      textarea.setAttribute('placeholder', basePlaceholder);
      return;
    }
    const syncedLang = getLang();
    setLang(syncedLang, { source: 'mode:activate' });
    setMicVisual(false);
  }

  if (typeof STT.onModeChange === 'function') {
    STT.onModeChange(applyMode, { immediate: true });
  } else {
    applyMode('browser');
  }
})(window);
