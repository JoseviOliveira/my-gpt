/* chats_refactored.js — Chat management orchestration (simplified) */
(function(global){
  const store = window.localStorage;
  const DEFAULT_TITLE = '— awaiting AI title —';
  const state = {
    history: [],
    sessionCache: {},
    get currentId() { return store.getItem('currentId') || ''; },
    set currentId(val) {
      if (val) store.setItem('currentId', val);
      else store.removeItem('currentId');
    }
  };

  let chatEl = null;
  let listEl = null;
  let newBtn = null;
  let delBtn = null;
  let currentTitleEl = null;
  let renderTablesFn = (s) => s;
  let setCurrentTitleFn = (title) => {
    if (currentTitleEl) currentTitleEl.textContent = title || DEFAULT_TITLE;
  };
  let setCurrentSummaryFn = () => {};
  let credHeaderFn = () => ({});
  let isLoggedFn = () => false;
  let focusComposerFn = () => {};
  let composerEl = null;
  let initialized = false;
  const metaStore = {};
  const GUEST_PING_MIN = 30000;
  let lastGuestPingAt = 0;
  let toastTimer = null;

  function getMetaEl(target){
    if (!target) return null;
    if (typeof target === 'string') return metaStore[target] || null;
    if (target.nodeType === 1) return target;
    return null;
  }
  
  function setMetaLanguage(target, lang){
    const metaEl = getMetaEl(target);
    if (!metaEl) return;
    const normalized = (lang || '').trim().toLowerCase();
    metaEl.dataset.lang = normalized;
    if (metaEl._message && metaEl._message.dataset) {
      metaEl._message.dataset.lang = normalized;
    }
  }

  function isGuest(){
    return Boolean(window.IS_GUEST);
  }

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

  async function warnGuestReadOnly(){
    try { window.logUserAction?.('Limits & Guards', 'Guest read-only blocked'); } catch {}
    await alertDialog('Guest is read-only.', { title: 'Read-only guest' });
  }

  function scheduleMetadataRefresh(sid, delay = 1500, opts = {}){
    if (!sid) return;
    if (isGuest()) return;
    if (typeof isLoggedFn === 'function' && !isLoggedFn()) return;
    
    const metadata = window.ChatsMetadata;
    if (!metadata) return;
    
    const updateFunc = (obj, { titleChanged, summaryChanged }) => {
      state.sessionCache[sid] = obj;
      const displayTitle = obj.title || obj.title_ai || DEFAULT_TITLE;
      const summaryText = obj.summary || obj.summary_ai || '';
      
      if (sid === state.currentId) {
        setCurrentTitleFn(displayTitle);
        setCurrentSummaryFn(summaryText, { animate: summaryChanged });
        if (obj.messages) {
          state.sessionCache[sid].messages = obj.messages;
          renderMessages(obj.messages);
        }
      }
      
      const titleNode = listEl?.querySelector(`.item[data-id="${sid}"] .title`);
      if (titleNode) titleNode.textContent = displayTitle;
    };
    
    metadata.scheduleMetadataRefresh(sid, api, updateFunc, {
      ...opts,
      prevState: state.sessionCache[sid] || {},
    });
  }

  function clearMetadataRefresh(sid){
    window.ChatsMetadata?.clearMetadataRefresh?.(sid);
  }

  function requestMetadataRefresh(){
    if (!state.currentId) return;
    if (isGuest()) return;
    if (typeof isLoggedFn === 'function' && !isLoggedFn()) return;
    clearMetadataRefresh(state.currentId);
    scheduleMetadataRefresh(state.currentId, window.ChatsMetadata?.METADATA_POLL_INITIAL_DELAY || 5000);
  }

  const getDialogs = () => window.ChatsDialogs || {};
  const confirmDialog = (...args) => getDialogs().confirmDialog?.(...args) || Promise.resolve(false);
  const promptDialog = (...args) => getDialogs().promptDialog?.(...args) || Promise.resolve(null);
  const alertDialog = (...args) => getDialogs().alertDialog?.(...args) || Promise.resolve(true);

  async function api(path, opts = {}){
    const headers = Object.assign(
      { 'Content-Type': 'application/json' },
      typeof credHeaderFn === 'function' ? (credHeaderFn() || {}) : {}
    );
    const merged = Object.assign({ headers }, opts);
    merged.headers = Object.assign({}, headers, opts.headers || {});
    const resp = await fetch(path, merged);
    if (resp.status === 401) {
      if (window.Shell && typeof window.Shell.forceLogout === 'function') {
        window.Shell.forceLogout('Session expired. Please log in again.');
      }
      throw new Error('401');
    }
    if (!resp.ok) throw new Error(resp.status);
    const text = await resp.text();
    if (!text) return {};
    try { return JSON.parse(text); } catch { return {}; }
  }

  async function ping(){
    if (isGuest()) {
      const now = Date.now();
      if (now - lastGuestPingAt < GUEST_PING_MIN) return;
      lastGuestPingAt = now;
    }
    try { await api('/health'); } catch {}
  }

  function createSpeakBtn(){
    const btn = document.createElement('button');
    btn.id = 'speakBtn';
    btn.type = 'button';
    btn.className = 'btn icon subtle speak-btn';
    btn.title = 'Read last answer';
    btn.style.marginLeft = '4px';
    btn.innerHTML = `
      <span class="speaker-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="currentColor">
          <path d="M11 5l-5 4H3v6h3l5 4z"/>
          <path d="M15 9.5a3.5 3.5 0 010 5M18 7a7 7 0 010 10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </span>
    `;
    btn.dataset.text = '';
    return btn;
  }

  function renderMetaWithSpeak(metaEl, text, msgEl){
    if (!metaEl) return;
    if (msgEl) {
      metaEl._message = msgEl;
      if (msgEl.dataset && msgEl.dataset.lang) {
        metaEl.dataset.lang = msgEl.dataset.lang;
      } else {
        metaEl.dataset.lang = '';
      }
    } else if (!metaEl._message) {
      const sibling = metaEl.previousElementSibling;
      if (sibling && sibling.classList && sibling.classList.contains('msg')) {
        metaEl._message = sibling;
        if (sibling.dataset && sibling.dataset.lang) {
          metaEl.dataset.lang = sibling.dataset.lang;
        } else {
          metaEl.dataset.lang = '';
        }
      }
    } else if (!metaEl.dataset.lang) {
      metaEl.dataset.lang = metaEl._message?.dataset?.lang || '';
    }
    metaEl.innerHTML = '';
    const span = document.createElement('span');
    span.className = 'metrics-text';
    const baseText = (text || '').trim();
    span.textContent = baseText ? `${baseText} •` : '';
    metaEl.appendChild(span);
    const speakBtn = createSpeakBtn();
    if (msgEl && msgEl.textContent) {
      speakBtn.dataset.text = msgEl.textContent;
    }
    metaEl.appendChild(speakBtn);
    if (metaEl.dataset && metaEl.dataset.id) {
      metaStore[metaEl.dataset.id] = metaEl;
    }
    setMetaLanguage(metaEl, metaEl.dataset?.lang || '');
  }

  function addMessage(role, text, lang='', opts = {}){
    if (!chatEl) return { msg: null, meta: null };
    const autoScroll = opts.autoScroll !== false;
    const wrap = document.createElement('div');
    const msg = document.createElement('div');
    msg.className = 'msg '+(role==='user'?'user':'assistant');
    msg.textContent = text;
    if (lang) msg.dataset.lang = lang;
    let meta = null;
    wrap.appendChild(msg);
    if (role !== 'user') {
      meta = document.createElement('div');
      meta.className = 'meta';
      wrap.appendChild(meta);
    }
    chatEl.appendChild(wrap);
    if (autoScroll) {
      chatEl.scrollTop = chatEl.scrollHeight;
    }
    return { msg, meta };
  }

  function renderMessages(msgs){
    if (!chatEl) return;
    if (window.__chatStreaming) return;
    const distanceFromBottom = chatEl.scrollHeight - chatEl.scrollTop;
    const atBottom = distanceFromBottom <= chatEl.clientHeight + 4;
    chatEl.innerHTML='';
    Object.keys(metaStore).forEach((key) => delete metaStore[key]);
    state.history.length = 0;
    let lastLang = '';
    (msgs || []).forEach(m => {
      state.history.push(m);
      let currentLang = (m.language_ai || '').trim().toLowerCase();
      if (m.role === 'user') {
        if (currentLang) lastLang = currentLang;
        else currentLang = lastLang;
      } else if (!currentLang) {
        currentLang = lastLang;
      }
      const { msg, meta } = addMessage(m.role, m.content, currentLang, { autoScroll: false });
      if (m.role !== 'user' && meta) {
        msg.innerHTML = renderTablesFn ? renderTablesFn(m.content) : m.content;
        if (window.MathJax?.typesetPromise) { window.MathJax.typesetPromise([msg]).catch(()=>{}); }
        meta.dataset.id = m.id || `msg-${state.history.length}`;
        renderMetaWithSpeak(meta, m.meta || '', msg);
      }
    });
    if (atBottom) {
      chatEl.scrollTop = chatEl.scrollHeight;
    } else {
      const target = chatEl.scrollHeight - distanceFromBottom;
      chatEl.scrollTop = Math.max(0, Math.min(target, chatEl.scrollHeight - chatEl.clientHeight));
    }
  }

  function openChatMenu(anchorEl, actions = []){
    document.querySelectorAll('.ctxmenu').forEach(el => el.remove());
    const rect = anchorEl.getBoundingClientRect();
    const menu = document.createElement('div');
    menu.className = 'ctxmenu';
    const list = document.createElement('ul');
    menu.appendChild(list);
    actions.forEach(a => {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'menuitem';
      btn.textContent = a.label;
      if (a.disabled) {
        btn.disabled = true;
        btn.title = a.disabledTitle || 'Guest is read-only';
      }
      btn.onclick = async (e) => {
        e.preventDefault(); e.stopPropagation();
        menu.remove();
        try { await a.onClick(); } catch (err) { console.error(err); }
      };
      li.appendChild(btn);
      list.appendChild(li);
    });
    document.body.appendChild(menu);
    const mRect = menu.getBoundingClientRect();
    let left = rect.right - mRect.width;
    let top = rect.bottom + 6;
    if (left + mRect.width > window.innerWidth) left = window.innerWidth - mRect.width - 8;
    if (left < 8) left = 8;
    if (top + mRect.height > window.innerHeight) top = rect.top - mRect.height - 6;
    if (top < 8) top = 8;
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    setTimeout(() => {
      const outside = (ev) => { if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('click', outside); } };
      document.addEventListener('click', outside);
    }, 0);
  }

  async function loadList(){
    if (!listEl) return [];
    const sessionsResp = await api('/api/sessions');
    const sessions = sessionsResp.sessions || [];
    const pinned = [];
    const regular = [];
    sessions.forEach((session) => {
      if (session.pinned) pinned.push(session);
      else regular.push(session);
    });
    const orderedSessions = pinned.concat(regular);
    const activeId = state.currentId;

    if (!orderedSessions.some(s => s.id === activeId)) {
      if (orderedSessions.length) {
        state.currentId = orderedSessions[0].id;
        store.setItem('currentId', state.currentId);
      } else {
        state.currentId = '';
        store.removeItem('currentId');
      }
    }

    listEl.innerHTML = '';
    orderedSessions.forEach((s) => {
      const isPinned = s.pinned === true || s.pinned === 'true' || s.pinned === 1;
      const div=document.createElement('div');
      div.className='item'+(s.id===state.currentId?' active':'')+(isPinned?' pinned':'');
      div.dataset.id = s.id;

      const titleSpan=document.createElement('span');
      titleSpan.className='title';
      const displayTitle = s.title || s.title_ai || '(untitled)';
      titleSpan.textContent=displayTitle;

      const menuBtn = document.createElement('button');
      menuBtn.type = 'button';
      menuBtn.textContent = '⋮';
      menuBtn.className = 'btn icon chat-menu';
      menuBtn.title = 'More';
      menuBtn.onclick = (ev) => {
        if (!div.classList.contains('active')) return;
        ev.preventDefault(); ev.stopPropagation();
        const actions = [];
        actions.push({
          label: isPinned ? 'Unpin' : 'Pin',
          disabled: isGuest(),
          onClick: async () => {
            if (isGuest()) return warnGuestReadOnly();
            await api('/api/save', {
              method: 'POST',
              body: JSON.stringify({ id: s.id, pinned: !isPinned })
            });
            state.sessionCache[s.id] = state.sessionCache[s.id] || { id: s.id, messages: [], title: null, title_ai: null, summary_ai: null };
            state.sessionCache[s.id].pinned = !isPinned;
            s.pinned = !isPinned;
            try { window.logUserAction?.('Chat', isPinned ? 'Unpin chat' : 'Pin chat'); } catch {}
            await loadList();
          }
        });
        actions.push({
          label: 'Rename',
          disabled: isGuest(),
          onClick: async () => {
            if (isGuest()) return warnGuestReadOnly();
            const oldTitle = titleSpan.textContent || '';
            const newTitle = await promptDialog('Rename chat', oldTitle, { help: 'Pick a short, descriptive title.' });
            if (newTitle === null) return;
            const trimmed = newTitle.trim();
            if (!trimmed || trimmed === oldTitle.trim()) return;
            const msgs = (state.sessionCache[s.id]?.messages || []);
            await api('/api/save',{ method:'POST', body: JSON.stringify({ id: s.id, messages: msgs, title: trimmed }) });
            titleSpan.textContent = trimmed;
            state.sessionCache[s.id] = state.sessionCache[s.id] || { id: s.id, messages: msgs, title: null, title_ai: null, summary_ai: null };
            state.sessionCache[s.id].title = trimmed;
            if (s.id === state.currentId) {
              setCurrentTitleFn(trimmed);
            }
            try { window.logUserAction?.('Chat', 'Rename chat'); } catch {}
          }
        });
        actions.push({
          label: 'Delete',
          disabled: isGuest(),
          onClick: async () => {
            if (isGuest()) return warnGuestReadOnly();
            const ok = await confirmDialog('Delete this chat? This cannot be undone.', { title:'Delete chat', okText:'Delete', okTone:'danger' });
            if (!ok) return;
            await fetch(`/api/session/${s.id}`, { method:'DELETE', headers: credHeaderFn() });
            if (s.id === state.currentId) {
              state.currentId = '';
              store.removeItem('currentId');
              state.history.length=0;
              if (chatEl) chatEl.innerHTML='';
              setCurrentTitleFn(DEFAULT_TITLE);
            }
            delete state.sessionCache[s.id];
            await loadList();
            try { window.logUserAction?.('Chat', 'Delete chat'); } catch {}
          }
        });
        openChatMenu(menuBtn, actions);
      };

      div.onclick = async () => {
        state.currentId = s.id;
        store.setItem('currentId', state.currentId);
        listEl.querySelectorAll('.item').forEach(x=>{
          x.classList.remove('active');
          const act = x.querySelector('.item-actions');
          if (act) act.style.visibility = 'hidden';
        });
        div.classList.add('active');
        const currentActions = div.querySelector('.item-actions');
        if (currentActions) currentActions.style.visibility = 'visible';
        const obj=await api(`/api/session/${state.currentId}`);
        state.sessionCache[state.currentId]=obj;
        const displayTitle = obj.title || obj.title_ai || DEFAULT_TITLE;
        setCurrentTitleFn(displayTitle);
        setCurrentSummaryFn(obj.summary || obj.summary_ai || '', { animate: false });
        renderMessages(obj.messages||[]);
        try { window.logUserAction?.('Chat', 'Open chat'); } catch {}
      };

      const row = document.createElement('div');
      row.style.display = 'flex';
      row.style.alignItems = 'center';
      row.style.gap = '6px';
      row.style.width = '100%';
      const pinIndicator = document.createElement('div');
      pinIndicator.className = 'pin-indicator';
      pinIndicator.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M6 7h8M8 3h4v4l1.5 3H6.5L8 7V3M10 10v7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
      pinIndicator.style.visibility = isPinned ? 'visible' : 'hidden';
      titleSpan.style.flex = '1';
      row.appendChild(pinIndicator);
      row.appendChild(titleSpan);
      const right = document.createElement('div');
      right.className = 'item-actions';
      right.appendChild(menuBtn);
      right.style.visibility = s.id === state.currentId ? 'visible' : 'hidden';
      row.appendChild(right);
      div.appendChild(row);
      listEl.appendChild(div);
    });
    listEl.scrollTop = 0;
    return orderedSessions;
  }

  async function ensureSession(){
    if (state.currentId) return state.currentId;
    if (isGuest()) {
      await warnGuestReadOnly();
      return '';
    }
    const j = await api('/api/session',{method:'POST',body:JSON.stringify({})});
    state.currentId=j.id;
    store.setItem('currentId', state.currentId);
    await loadList();
    setCurrentTitleFn(DEFAULT_TITLE);
    setCurrentSummaryFn('');
    state.sessionCache[state.currentId]={id:state.currentId, title:null, title_ai:null, summary_ai:null, messages:[]};
    return state.currentId;
  }

  async function saveSession(title=null, opts={}){
    if(!state.currentId) return;
    if (isGuest()) {
      await warnGuestReadOnly();
      return;
    }
    const payload = { id: state.currentId, messages: state.history };
    if (title !== undefined) payload.title = title;
    const resp = await api('/api/save',{method:'POST', body:JSON.stringify(payload)});
    if(!state.sessionCache[state.currentId]) state.sessionCache[state.currentId]={id:state.currentId, title:null, title_ai:null, summary_ai:null, messages:[]};
    state.sessionCache[state.currentId].messages = [...state.history];
    if(title!==null && title!==undefined){
      const trimmed = title.trim();
      state.sessionCache[state.currentId].title = trimmed;
      setCurrentTitleFn(trimmed);
      const active = listEl?.querySelector(`.item[data-id="${state.currentId}"] .title`);
      if(active) active.textContent = trimmed;
    }
    return resp;
  }

  async function hydrateWorkspace(){
    if (!isLoggedFn()) {
      state.history.length = 0;
      if (chatEl) chatEl.innerHTML = '';
      if (listEl) listEl.innerHTML = '';
      Object.keys(state.sessionCache).forEach(k => delete state.sessionCache[k]);
      state.currentId = '';
      store.removeItem('currentId');
      setCurrentTitleFn(DEFAULT_TITLE);
      setCurrentSummaryFn('');
      return;
    }

    const sessions = await loadList();
    if (!state.currentId) {
      state.history.length = 0;
      if (chatEl) chatEl.innerHTML = '';
      setCurrentTitleFn('(untitled)');
      setCurrentSummaryFn('');
      return;
    }

    try {
      const obj = await api(`/api/session/${state.currentId}`);
      state.sessionCache[state.currentId] = obj;
      const displayTitle = obj.title || obj.title_ai || DEFAULT_TITLE;
      setCurrentTitleFn(displayTitle);
      setCurrentSummaryFn(obj.summary || obj.summary_ai || '', { animate: false });
      renderMessages(obj.messages || []);
    } catch (err) {
      console.error(err);
    }
  }

  async function newChat(){
    if (isGuest()) {
      await warnGuestReadOnly();
      return;
    }
    const prevId = state.currentId;
    let j = null;
    try {
      const headers = Object.assign(
        { 'Content-Type': 'application/json' },
        typeof credHeaderFn === 'function' ? (credHeaderFn() || {}) : {}
      );
      const resp = await fetch('/api/session', { method:'POST', headers, body: JSON.stringify({}) });
      if (!resp.ok) {
        let err = {};
        try { err = await resp.json(); } catch {}
        const errCode = err?.error || '';
        const errMsg = err?.detail || errCode || 'Chat limit reached.';
        if (errCode === 'chat_count_limit') {
          try { window.logUserAction?.('Limits & Guards', 'Chat cap reached'); } catch {}
          showToast(errMsg);
          return;
        }
        throw new Error(errMsg);
      }
      j = await resp.json();
    } catch (err) {
      await alertDialog('Failed to create a new chat.', { title: 'New chat error' });
      return;
    }
    state.currentId=j.id;
    store.setItem('currentId',state.currentId);
    state.sessionCache[state.currentId]={id:state.currentId, title:null, title_ai:null, summary_ai:null, messages:[]};
    state.history.length=0;
    if (chatEl) {
      chatEl.innerHTML='';
      chatEl.scrollTop = 0;
    }
    setCurrentTitleFn(DEFAULT_TITLE);
    setCurrentSummaryFn('');
    if (typeof focusComposerFn === 'function') focusComposerFn();
    await loadList();
    if (prevId && prevId !== state.currentId) {
      const prev = listEl?.querySelector(`.item[data-id="${prevId}"] .summary`);
      if (prev) prev.textContent = '';
    }
    try { window.logUserAction?.('Chat', 'New chat'); } catch {}
  }

  async function deleteCurrentChat(){
    if(!state.currentId) return;
    if (isGuest()) {
      await warnGuestReadOnly();
      return;
    }
    const ok = await confirmDialog('Delete this chat? This cannot be undone.', {title:'Delete chat', okText:'Delete', okTone:'danger'});
    if(!ok) return;
    await fetch(`/api/session/${state.currentId}`,{method:'DELETE', headers:credHeaderFn()});
    delete state.sessionCache[state.currentId];
    state.currentId='';
    store.removeItem('currentId');
    await loadList();
    state.history.length=0;
    if (chatEl) chatEl.innerHTML='';
    setCurrentTitleFn(DEFAULT_TITLE);
    setCurrentSummaryFn('');
    try { window.logUserAction?.('Chat', 'Delete chat'); } catch {}
  }

  function init(opts = {}){
    if (initialized) return;
    chatEl = opts.chatEl || document.getElementById('chat');
    listEl = opts.listEl || document.getElementById('chatlist');
    newBtn = opts.newBtn || document.getElementById('newchat');
    delBtn = opts.delBtn || document.getElementById('delchat');
    currentTitleEl = opts.currentTitleEl || document.getElementById('currentTitle');
    renderTablesFn = opts.renderTables || renderTablesFn;
    setCurrentTitleFn = opts.setCurrentTitle || setCurrentTitleFn;
    setCurrentSummaryFn = opts.setCurrentSummary || setCurrentSummaryFn;
    credHeaderFn = opts.credHeader || credHeaderFn;
    isLoggedFn = opts.isLogged || isLoggedFn;
    focusComposerFn = opts.focusComposer || (() => {});
    composerEl = opts.composer || null;

    const applyGuestLock = () => {
      if (!newBtn && !delBtn) return;
      const locked = isGuest();
      if (newBtn) {
        newBtn.disabled = locked;
        newBtn.title = locked ? 'Guest is read-only' : 'New chat';
      }
      if (delBtn) {
        delBtn.disabled = locked;
        delBtn.title = locked ? 'Guest is read-only' : 'Delete chat';
      }
    };

    newBtn?.addEventListener('click', async (ev) => {
      ev.preventDefault();
      if (isGuest()) return warnGuestReadOnly();
      newChat();
    });
    delBtn?.addEventListener('click', async (ev) => {
      ev.preventDefault();
      if (isGuest()) return warnGuestReadOnly();
      deleteCurrentChat();
    });

    applyGuestLock();
    window.addEventListener('guest:ready', applyGuestLock);

    initialized = true;
  }

  Object.defineProperty(global, 'Chats', {
    value: {
      init,
      api,
      ping,
      get history(){ return state.history; },
      get sessionCache(){ return state.sessionCache; },
      getCurrentId: () => state.currentId,
      setCurrentId: (id) => { state.currentId = id || ''; },
      addMessage,
      renderMessages,
      renderMetaWithSpeak,
      setMetaLanguage,
      loadList,
      ensureSession,
      saveSession,
      hydrateWorkspace,
      newChat,
      deleteCurrentChat,
      confirmDialog,
      promptDialog,
      alertDialog,
      setCurrentTitle: (title) => setCurrentTitleFn(title || '(untitled)'),
      scheduleMetadataRefresh,
      requestMetadataRefresh,
      get composer(){ return composerEl; }
    },
    writable: false,
    configurable: false
  });
})(window);
