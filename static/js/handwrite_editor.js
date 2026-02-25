(() => {
  const editor = document.getElementById('editor');
  const statusEl = document.getElementById('status');
  const saveBtn = document.getElementById('saveBtn');
  const loadBtn = document.getElementById('loadBtn');
  const linkBtn = document.getElementById('linkBtn');
  const fontMinusBtn = document.getElementById('fontMinusBtn');
  const fontPlusBtn = document.getElementById('fontPlusBtn');

  let sourceHtml = '';

  function authHeaders() {
    const headers = {};
    try {
      const token = localStorage.getItem('authToken');
      // Ignore malformed tokens to avoid browser header parsing errors.
      if (token && /^[A-Za-z0-9._~-]+$/.test(token)) {
        headers.Authorization = `Bearer ${token}`;
      }
    } catch {}
    return headers;
  }

  function setStatus(message, isError = false) {
    statusEl.textContent = message;
    statusEl.style.color = isError ? 'var(--danger-ink, #b42318)' : 'var(--muted-ink)';
  }

  function parseMainContent(htmlText) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(htmlText, 'text/html');
    const main = doc.querySelector('main.docs-content');
    return main ? main.innerHTML : '';
  }

  function composeHtml(updatedMainInnerHtml) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(sourceHtml, 'text/html');
    const main = doc.querySelector('main.docs-content');
    if (!main) throw new Error('main.docs-content was not found in handwrite.html');
    main.innerHTML = updatedMainInnerHtml;
    return `<!doctype html>\n${doc.documentElement.outerHTML}`;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function selectedRangeInEditor() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    const range = sel.getRangeAt(0);
    if (!editor.contains(range.commonAncestorContainer)) return null;
    return range;
  }

  function baseSizeFromNode(node) {
    const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    if (!element) return 16;
    const px = Number.parseFloat(window.getComputedStyle(element).fontSize || '16');
    return Number.isFinite(px) ? px : 16;
  }

  function adjustSelectionFontSize(deltaPx) {
    const range = selectedRangeInEditor();
    if (!range) {
      setStatus('Select text in the editor first.', true);
      return;
    }
    const nextSize = clamp(Math.round(baseSizeFromNode(range.startContainer) + deltaPx), 10, 72);
    const span = document.createElement('span');
    span.style.fontSize = `${nextSize}px`;

    if (range.collapsed) {
      span.textContent = '\u200b';
      range.insertNode(span);
      const caret = document.createRange();
      caret.setStart(span.firstChild, 1);
      caret.collapse(true);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(caret);
      return;
    }

    const content = range.extractContents();
    span.appendChild(content);
    range.insertNode(span);
    const sel = window.getSelection();
    const newRange = document.createRange();
    newRange.selectNodeContents(span);
    sel.removeAllRanges();
    sel.addRange(newRange);
  }

  async function loadFromServer() {
    setStatus('Loading handwrite.html...');
    try {
      const res = await fetch('/api/docs/handwrite', {
        method: 'GET',
        headers: authHeaders(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.ok) {
        if (res.status === 404) {
          throw new Error('Editor API not found (404). Restart the Flask server to load the new route.');
        }
        throw new Error(data?.error || `HTTP ${res.status}`);
      }
      sourceHtml = data.html || '';
      editor.innerHTML = parseMainContent(sourceHtml);
      setStatus('Loaded. You can edit and click Save.');
    } catch (err) {
      setStatus(`Load failed: ${err.message}`, true);
    }
  }

  async function saveToServer() {
    if (!sourceHtml) {
      setStatus('Load file first.', true);
      return;
    }

    saveBtn.disabled = true;
    setStatus('Saving to static/docs/handwrite.html...');

    try {
      const html = composeHtml(editor.innerHTML);
      const res = await fetch('/api/docs/handwrite', {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ html }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.ok) {
        if (res.status === 404) {
          throw new Error('Editor API not found (404). Restart the Flask server to load the new route.');
        }
        throw new Error(data?.error || `HTTP ${res.status}`);
      }
      sourceHtml = html;
      setStatus('Saved successfully. Refresh /docs/handwrite.html to verify.');
    } catch (err) {
      setStatus(`Save failed: ${err.message}`, true);
    } finally {
      saveBtn.disabled = false;
    }
  }

  document.querySelectorAll('[data-cmd]').forEach((btn) => {
    btn.addEventListener('click', () => {
      editor.focus();
      const cmd = btn.dataset.cmd;
      const value = btn.dataset.value || null;
      document.execCommand(cmd, false, value);
    });
  });

  linkBtn.addEventListener('click', () => {
    editor.focus();
    const url = window.prompt('Enter URL');
    if (!url) return;
    document.execCommand('createLink', false, url);
  });
  fontMinusBtn.addEventListener('click', () => {
    editor.focus();
    adjustSelectionFontSize(-2);
  });
  fontPlusBtn.addEventListener('click', () => {
    editor.focus();
    adjustSelectionFontSize(2);
  });

  loadBtn.addEventListener('click', loadFromServer);
  saveBtn.addEventListener('click', saveToServer);

  loadFromServer();
})();
