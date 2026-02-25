(function initComposerModule(global){
  const chatEl = document.getElementById('chat');
  const form = document.getElementById('f');
  const textarea = document.getElementById('t');
  const sendBtn = document.querySelector('.composer .send');
  if (!form || !textarea || !sendBtn) return;

  const chats = global.Chats;
  if (!chats) return;
  const history = chats.history;
  const store = global.localStorage;
  const models = global.AppModels || {};
  const getModelForMode = models.getModelForMode || (() => null);
  const DEFAULT_MODELS = models.DEFAULT_MODELS || {};

  let BASE_T_HEIGHT = null;
  global.__chatStreaming = global.__chatStreaming || false;
  let toastTimer = null;

  function showToast(message, { duration = 1500 } = {}){
    if (!message) return;
    let toastEl = document.getElementById('appToast');
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.id = 'appToast';
      toastEl.className = 'app-toast toast-surface';
      const host = document.getElementById('chat-messages') || document.body;
      if (host.firstChild) {
        host.insertBefore(toastEl, host.firstChild);
      } else {
        host.appendChild(toastEl);
      }
    }
    toastEl.textContent = message;
    toastEl.classList.add('show');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastEl.classList.remove('show');
    }, duration);
  }

  const prev = textarea.value;
  textarea.value = '';
  textarea.style.height = 'auto';
  const baseHeight = textarea.scrollHeight;
  textarea.value = prev;
  textarea.style.height = 'auto';
  textarea.style.height = Math.max(baseHeight, textarea.scrollHeight) + 'px';
  BASE_T_HEIGHT = baseHeight;

  function resizeTextarea() {
    textarea.style.height = 'auto';
    // Account for footer padding (form 6px + composerShell ~44px bottom = ~50px total overhead)
    // Plus composer padding ~20px = ~70px total. So max textarea should be 50vh - 70px
    const maxHeight = Math.floor(window.innerHeight * 0.5) - 70;
    const newHeight = Math.min(textarea.scrollHeight, maxHeight);
    textarea.style.height = newHeight + 'px';
    textarea.style.overflow = textarea.scrollHeight > maxHeight ? 'auto' : 'hidden';
  }

  textarea.addEventListener('input', resizeTextarea);
  textarea.addEventListener('paste', () => setTimeout(resizeTextarea, 0));
  textarea.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.dispatchEvent(new Event('submit', { cancelable: true }));
    }
  });

  // Initialize textarea height
  setTimeout(resizeTextarea, 0);

  let inflight = false;
  let abortCtrl = null;
  let currentReader = null;
  let currentReqId = null;
  let stopRequested = false;
  let streamStarted = false;
  let currentThinkingLabel = '';
  let currentAssistantMsg = null;

  const ui = { form, textarea, sendBtn };
  const guestTitle = 'Guest is read-only';
  const gpuTitle = 'GPU busy. Try again shortly.';

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

  function applyGuestLock(){
    const locked = Boolean(global.IS_GUEST);
    ui.sendBtn.disabled = locked;
    ui.sendBtn.title = locked ? guestTitle : 'Send';
  }

  function shouldBlockForGpu(){
    if (global.IS_GUEST || global.IS_ADMIN) return false;
    const util = Number(global.APP_GPU_UTIL);
    if (!Number.isFinite(util)) return false;
    return util > 33;
  }

  function applySendGuard(){
    if (ui.sendBtn.classList.contains('stop')) return;
    if (global.IS_GUEST) {
      applyGuestLock();
      return;
    }
    if (shouldBlockForGpu()) {
      ui.sendBtn.disabled = true;
      ui.sendBtn.title = gpuTitle;
      return;
    }
    ui.sendBtn.disabled = false;
    ui.sendBtn.title = 'Send';
  }

  function clearComposer(){
    ui.textarea.value = '';
    resizeTextarea();
  }

  function setSendBtn(mode) {
    if (mode === 'stop') {
      ui.sendBtn.textContent = 'Stop';
      ui.sendBtn.classList.add('stop');
      ui.sendBtn.title = 'Stop current response';
    } else {
      ui.sendBtn.textContent = 'Send';
      ui.sendBtn.classList.remove('stop');
      ui.sendBtn.title = 'Send';
    }
  }

  function requestStreamStop(id){
    if (!id) return;
    try {
      const headers = Object.assign({ 'Content-Type': 'application/json' }, credHeader());
      fetch('/api/stop', { method: 'POST', headers, body: JSON.stringify({ id }) })
        .catch(() => {});
    } catch {}
  }

  function fadeOutAssistantMessage(msg, abortedText){
    if (!msg) return;
    const wrap = msg.parentElement;
    msg.classList.remove('thinking', 'streaming');
    msg.classList.add('fading-out');
    if (wrap) {
      const meta = wrap.querySelector('.meta');
      if (meta) meta.classList.add('fading-out');
    }
    setTimeout(() => {
      const fallback = abortedText || 'Request aborted';
      msg.textContent = fallback;
      msg.classList.remove('fading-out');
      if (wrap) {
        const meta = wrap.querySelector('.meta');
        if (meta) meta.remove();
      }
    }, 1500);
  }

  function stopInFlight() {
    const abortedText = currentThinkingLabel
      ? `${currentThinkingLabel}, request aborted`
      : 'Request aborted';
    stopRequested = true;
    try { window.logUserAction?.('Compose', 'Stop response'); } catch {}
    if (currentReqId) requestStreamStop(currentReqId);
    try { if (currentReader) currentReader.cancel().catch(() => {}); } catch {}
    try { if (abortCtrl) abortCtrl.abort(); } catch {}
    if (currentAssistantMsg) {
      if (currentAssistantMsg.classList.contains('thinking')) {
        currentAssistantMsg.textContent = abortedText;
        currentAssistantMsg.classList.remove('thinking', 'streaming');
      } else {
        fadeOutAssistantMessage(currentAssistantMsg, abortedText);
      }
    }
    inflight = false;
    global.__chatStreaming = false;
    currentReader = null;
    abortCtrl = null;
    setSendBtn('send');
  }

  function getMode(){
    return (store && store.getItem('mode')) || 'fast';
  }

  sendBtn.addEventListener('click', (ev) => {
    if (inflight && sendBtn.classList.contains('stop')) {
      ev.preventDefault();
      stopInFlight();
    }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (inflight) return;
    if (global.IS_GUEST) {
      try { window.logUserAction?.('Limits & Guards', 'Guest read-only blocked'); } catch {}
      await chats.alertDialog('Guest is read-only.', { title: 'Read-only guest' });
      return;
    }
    if (shouldBlockForGpu()) {
      try { window.logUserAction?.('Limits & Guards', 'GPU guard blocked'); } catch {}
      await chats.alertDialog('GPU busy. Try again shortly.', { title: 'Busy' });
      return;
    }
    if (!isLogged()) {
      await chats.alertDialog('Please log in first.', { title: 'Authentication required' });
      return;
    }

    const content = ui.textarea.value.trim();
    if (!content) return;
    try { window.logUserAction?.('Compose', 'Send message'); } catch {}

    history.push({ role: 'user', content });
    const userWrap = chats.addMessage('user', content);
    const userMsg = userWrap?.msg || null;
    clearComposer();

    try {
      await chats.ensureSession();
    } catch (err) {
      await chats.alertDialog('Failed to open a session. Please try again.', { title: 'Session error' });
      return;
    }

    const wrap = chats.addMessage('assistant', '');
    const assistantMsg = wrap.msg, assistantMeta = wrap.meta;
    if (assistantMeta && !assistantMeta.dataset.id) {
      assistantMeta.dataset.id = `live-${Date.now().toString(36)}`;
    }

    currentAssistantMsg = assistantMsg;

    assistantMsg.classList.add('thinking', 'streaming');
    const mode = getMode();
    const selectedModel = getModelForMode(mode);
    const displayModel = selectedModel || DEFAULT_MODELS[mode] || 'model';
    currentThinkingLabel = `Thinking ${mode} using ${displayModel}`;
    assistantMsg.innerHTML = `${currentThinkingLabel} <span class="arrow-flow" aria-hidden="true">⇢</span>`;
    const thinkingAt = performance.now();

    inflight = true;
    global.__chatStreaming = true;
    setSendBtn('stop');

    abortCtrl = new AbortController();
    currentReader = null;
    stopRequested = false;
    streamStarted = false;

    const reqId = (Date.now().toString(36) + Math.random().toString(36).slice(2,7));
    currentReqId = reqId;
    const payload = { id: reqId, mode, messages: history };
    if (selectedModel) payload.model = selectedModel;
    const start = performance.now();

    try {
    const resp = await fetch('/api/stream', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, credHeader()),
      body: JSON.stringify(payload),
      signal: abortCtrl.signal
    });
      if (resp.status === 401) {
        if (global.Shell && typeof global.Shell.forceLogout === 'function') {
          global.Shell.forceLogout('Session expired. Please log in again.');
        }
        assistantMsg.classList.remove('thinking', 'streaming');
        assistantMsg.textContent = 'Please log in again.';
        history.push({ role: 'assistant', content: 'Please log in again.' });
        return;
      }
      if (!resp.ok) {
        let errMsg = '(stream error)';
        let errCode = '';
        try {
          const err = await resp.json();
          errMsg = err?.detail || err?.error || errMsg;
          errCode = err?.error || '';
        } catch {}
        const limitErrors = new Set([
          'daily_prompt_limit',
          'chat_prompt_limit',
          'mode_not_allowed',
          'model_not_allowed',
        ]);
        if (limitErrors.has(errCode)) {
          try {
            const label = errCode === 'daily_prompt_limit'
              ? 'Prompt cap reached'
              : errCode === 'chat_prompt_limit'
                ? 'Chat prompt cap reached'
                : errCode === 'mode_not_allowed'
                  ? 'Mode not allowed'
                  : 'Model not allowed';
            window.logUserAction?.('Limits & Guards', label);
          } catch {}
          if (assistantMsg?.parentElement) assistantMsg.parentElement.remove();
          if (userMsg?.parentElement) userMsg.parentElement.remove();
          if (history.length && history[history.length - 1].role === 'user' && history[history.length - 1].content === content) {
            history.pop();
          }
          showToast(errMsg || 'Limit reached.');
          return;
        }
        assistantMsg.classList.remove('thinking', 'streaming');
        assistantMsg.textContent = errMsg;
        history.push({ role: 'assistant', content: errMsg, meta: errMsg });
        return;
      }
      if (!resp.body) { assistantMsg.textContent = '(stream error)'; return; }

      let detectedLang = '';

      const reader = resp.body.getReader();
      currentReader = reader;
      const dec = new TextDecoder();
      let full = '';
      let first = true;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (stopRequested) break;
        const chunk = dec.decode(value, { stream: true });

        if (first) {
          const since = performance.now() - thinkingAt;
          if (since < 250) { await new Promise(r => setTimeout(r, 250 - since)); }
          assistantMsg.classList.remove('thinking');
          assistantMsg.textContent = '';
          first = false;
          streamStarted = true;
        }

        full += chunk;
        assistantMsg.textContent = full;
        chatEl.scrollTop = chatEl.scrollHeight;
      }

      if (stopRequested) return;

      assistantMsg.innerHTML = renderTables(full);
      if (global.MathJax?.typesetPromise) { MathJax.typesetPromise([assistantMsg]).catch(() => {}); }
      assistantMsg.classList.remove('streaming');

      try {
        const m = await chats.api(`/api/metrics?id=${encodeURIComponent(reqId)}`);
        const s = m.ollama || {};
        const elapsed = (m.elapsed_s ?? (performance.now() - start) / 1000).toFixed(1);
        const outTok = (s.output_tokens ?? 0);
        const tps = s.tokens_per_s ? s.tokens_per_s.toFixed(0) : null;
        const pTok = (s.prompt_tokens ?? 0);
        const parts = [];
        if (selectedModel && mode) parts.push(`${selectedModel}, ${mode}`);
        else if (selectedModel) parts.push(selectedModel);
        else if (mode) parts.push(mode);
        const elapsedLabel = `${elapsed}s`;
        const tokenSummary = `${pTok || 0}tk↑, ${outTok || 0}tk↓, ${tps ? `${tps}tk/s` : '-'}, ${elapsedLabel}`;
        parts.push(tokenSummary);
        const metaStr = parts.join(' • ');
        chats.renderMetaWithSpeak(assistantMeta, metaStr, assistantMsg);
        history.push({ role: 'assistant', content: full, meta: metaStr });
      } catch {
        const elapsed = Number((performance.now() - start) / 1000).toFixed(1);
        const fallbackParts = [];
        if (selectedModel) fallbackParts.push(selectedModel);
        const fallbackElapsed = `${elapsed}s`;
        const fallbackSummary = `${mode}, ${fallbackElapsed}`;
        fallbackParts.push(fallbackSummary);
        const metaStr = fallbackParts.join(' • ');
        chats.renderMetaWithSpeak(assistantMeta, metaStr, assistantMsg);
        history.push({ role: 'assistant', content: full, meta: metaStr });
      }

      const saveResult = await chats.saveSession();
      detectedLang = ((saveResult && saveResult.last_assistant_lang) || detectedLang || '').trim().toLowerCase();
      if (detectedLang) {
        if (assistantMsg) assistantMsg.dataset.lang = detectedLang;
        if (assistantMeta && typeof chats.setMetaLanguage === 'function') {
          chats.setMetaLanguage(assistantMeta, detectedLang);
        }
        const lastEntry = history[history.length - 1];
        if (lastEntry && lastEntry.role === 'assistant') {
          lastEntry.language_ai = detectedLang;
        }
      } else if (assistantMeta && typeof chats.setMetaLanguage === 'function') {
        chats.setMetaLanguage(assistantMeta, '');
      }
      if (typeof chats.requestMetadataRefresh === 'function') {
        chats.requestMetadataRefresh();
      }

    } catch (err) {
      if (err?.name === 'AbortError') {
        if (!stopRequested && assistantMsg) {
          assistantMsg.textContent = '(stopped)';
        }
      } else {
        console.error(err);
        assistantMsg.textContent = 'Error contacting local server.';
      }
    } finally {
      inflight = false;
      global.__chatStreaming = false;
      currentAssistantMsg = null;
      currentReader = null;
      abortCtrl = null;
      currentReqId = null;
      stopRequested = false;
      streamStarted = false;
      currentThinkingLabel = '';
      setSendBtn('send');

      if (ui && ui.textarea) {
        ui.textarea.value = '';
        if (BASE_T_HEIGHT) ui.textarea.style.height = BASE_T_HEIGHT + 'px';
        else ui.textarea.style.height = 'auto';
      }
    }
  });

  applyGuestLock();
  applySendGuard();
  window.addEventListener('guest:ready', applyGuestLock);
  window.addEventListener('role:ready', applySendGuard);
  window.addEventListener('gpu:updated', applySendGuard);
})(window);
