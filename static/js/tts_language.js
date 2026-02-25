/*
 * tts_language.js — Language detection and resolution for TTS
 */
(function(global){
  const STATE = global.__TTS_STATE || (global.__TTS_STATE = {});
  STATE.langCache = STATE.langCache || {};
  STATE.langPromises = STATE.langPromises || {};
  const TTSLanguage = global.TTSLanguage || (global.TTSLanguage = {});

  const now = () => (global.performance && typeof global.performance.now === 'function'
    ? global.performance.now()
    : Date.now());

  function extractText(btn){
    if (!btn) return '';
    if (btn.dataset && btn.dataset.text) {
      return btn.dataset.text;
    }
    let text = '';
    const meta = btn.closest('.meta');
    if (meta) {
      const msgRef = meta._message;
      if (msgRef && msgRef.parentElement === meta.parentElement) {
        text = (msgRef.innerText || '').trim();
      }
      if (!text) {
        let candidate = meta.previousElementSibling;
        if (!(candidate && candidate.classList && candidate.classList.contains('msg'))) {
          candidate = meta.parentElement ? meta.parentElement.querySelector('.msg.assistant') : null;
        }
        if (candidate && candidate.classList.contains('assistant')) {
          text = (candidate.innerText || '').trim();
        }
      }
    }
    if (!text) {
      const nodes = Array.from(document.querySelectorAll('.msg.assistant'));
      if (nodes.length) text = (nodes[nodes.length - 1].innerText || '').trim();
    }
    return text;
  }

  function getMetaNode(btn){
    if (!btn) return null;
    if (typeof btn.closest === 'function') {
      const meta = btn.closest('.meta');
      if (meta) return meta;
    }
    let parent = btn.parentElement;
    while (parent) {
      if (parent.classList && parent.classList.contains('meta')) return parent;
      parent = parent.parentElement;
    }
    return null;
  }

  function getMessageLang(btn){
    if (!btn) return '';
    const meta = getMetaNode(btn);
    if (meta && meta.dataset && meta.dataset.lang) return meta.dataset.lang;
    if (meta && meta._message && meta._message.dataset && meta._message.dataset.lang) {
      return meta._message.dataset.lang;
    }
    return '';
  }

  function languageCacheKey(meta, text){
    if (meta?.dataset?.id) return meta.dataset.id;
    const cleaned = (text || '').trim();
    if (!cleaned) return '';
    const head = cleaned.slice(0, 64);
    return `text:${cleaned.length}:${head}`;
  }

  async function requestLanguageDetection(text){
    const snippet = (text || '').trim().slice(0, 2000);
    if (!snippet) return '';
    if (global.IS_GUEST) return '';
    const headers = Object.assign(
      { 'Content-Type': 'application/json' },
      typeof global.authHeader === 'function' ? (global.authHeader() || {}) : {}
    );
    const resp = await fetch('/api/detect-language', {
      method: 'POST',
      headers,
      body: JSON.stringify({ text: snippet }),
    });
    if (resp.status === 401 && global.Shell?.forceLogout) {
      global.Shell.forceLogout('Session expired. Please log in again.');
      return '';
    }
    if (!resp.ok) throw new Error(`lang status ${resp.status}`);
    const data = await resp.json().catch(() => ({}));
    return (data.lang || '').trim().toLowerCase();
  }

  function getChats(){
    return global.Chats || null;
  }

  async function resolveLanguage(btn, text){
    const existing = getMessageLang(btn);
    if (existing) return existing;
    const meta = getMetaNode(btn);
    const key = languageCacheKey(meta, text);
    if (key && STATE.langCache[key]) {
      const cached = STATE.langCache[key];
      if (cached && meta) {
        const chatsApi = getChats();
        if (chatsApi && typeof chatsApi.setMetaLanguage === 'function') {
          chatsApi.setMetaLanguage(meta, cached);
        }
      }
      return cached;
    }
    if (key && STATE.langPromises[key]) {
      return STATE.langPromises[key];
    }
    const promise = requestLanguageDetection(text)
      .then((detected) => {
        const normalized = (detected || '').trim().toLowerCase();
        if (normalized && key) STATE.langCache[key] = normalized;
        if (normalized && meta) {
          const chatsApi = getChats();
          if (chatsApi && typeof chatsApi.setMetaLanguage === 'function') {
            chatsApi.setMetaLanguage(meta, normalized);
          }
        }
        return normalized;
      })
      .catch((err) => {
        console.warn('[tts] on-demand language detection failed', err);
        return '';
      })
      .finally(() => {
        if (key) delete STATE.langPromises[key];
      });
    if (key) STATE.langPromises[key] = promise;
    return promise;
  }

  function markSpeakStart(btn, text, lang, mode){
    if (!btn) return;
    btn._ttsStartTime = now();
    btn._ttsMeta = { lang, textLen: (text || '').length, mode };
  }

  function reportLatency(btn, mode, overrides){
    if (!btn || typeof btn._ttsStartTime !== 'number') return;
    const startedAt = btn._ttsStartTime;
    const meta = btn._ttsMeta || {};
    const elapsed = Math.max(0, Math.round(now() - startedAt));
    const authHeader = typeof global.authHeader === 'function' ? (global.authHeader() || {}) : {};
    const payload = Object.assign({
      elapsed_ms: elapsed,
      mode,
      lang: overrides && overrides.lang ? overrides.lang : (meta.lang || ''),
      text_len: overrides && overrides.textLen != null ? overrides.textLen : (meta.textLen || 0),
    }, overrides || {});
    fetch('/api/tts/metrics', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeader),
      body: JSON.stringify(payload),
    }).catch((err) => {
      console.debug('[tts] latency metric failed', err);
    });
    delete btn._ttsStartTime;
    delete btn._ttsMeta;
  }

  TTSLanguage.extractText = extractText;
  TTSLanguage.getMetaNode = getMetaNode;
  TTSLanguage.getMessageLang = getMessageLang;
  TTSLanguage.resolveLanguage = resolveLanguage;
  TTSLanguage.markSpeakStart = markSpeakStart;
  TTSLanguage.reportLatency = reportLatency;

  global.TTSLanguage = TTSLanguage;
})(window);
