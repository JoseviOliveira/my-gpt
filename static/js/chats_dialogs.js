/*
 * chats_dialogs.js — Modal dialog helpers (confirm, prompt, alert)
 * Extracted from chats.js for file size management
 */
(function(global){
  const Dialogs = {};

  async function confirmDialog(message, opts = {}) {
    const modal = document.getElementById('appModal');
    const msgEl = document.getElementById('dlgMessage');
    const titleEl = document.getElementById('dlgTitle');
    const okBtn = document.getElementById('dlgOk');
    const cancelBtn = document.getElementById('dlgCancel');
    titleEl.textContent = opts.title || 'Confirm';
    msgEl.textContent = message || 'Are you sure?';
    okBtn.textContent = opts.okText || 'OK';
    okBtn.classList.toggle('danger', (opts.okTone || 'danger') === 'danger');
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    return await new Promise((resolve) => {
      const close = (val) => {
        modal.classList.add('hidden');
        modal.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        document.removeEventListener('keydown', onKey);
        resolve(val);
      };
      const onOk = () => close(true);
      const onCancel = () => close(false);
      const onKey = (e) => { if (e.key === 'Escape') close(false); if (e.key === 'Enter') close(true); };
      okBtn.addEventListener('click', onOk, { once: true });
      cancelBtn.addEventListener('click', onCancel, { once: true });
      document.addEventListener('keydown', onKey);
      cancelBtn.focus();
    });
  }

  async function promptDialog(title, initial = '', opts = {}) {
    const modal = document.getElementById('appPrompt');
    const titleEl = document.getElementById('prTitle');
    const inputEl = document.getElementById('prInput');
    const helpEl = document.getElementById('prHelp');
    const okBtn = document.getElementById('prOk');
    const cancelBtn = document.getElementById('prCancel');
    titleEl.textContent = title || 'Input';
    inputEl.value = initial || '';
    helpEl.textContent = opts.help || '';
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    inputEl.focus(); inputEl.select();
    return await new Promise((resolve) => {
      const close = (val) => {
        modal.classList.add('hidden');
        modal.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        inputEl.removeEventListener('keydown', onKey);
        resolve(val);
      };
      const onOk = () => close(inputEl.value.trim());
      const onCancel = () => close(null);
      const onKey = (e) => { if (e.key === 'Escape') close(null); if (e.key === 'Enter') onOk(); };
      okBtn.addEventListener('click', onOk, { once: true });
      cancelBtn.addEventListener('click', onCancel, { once: true });
      inputEl.addEventListener('keydown', onKey);
    });
  }

  async function alertDialog(message, opts = {}){
    const modal = document.getElementById('appModal');
    const msgEl = document.getElementById('dlgMessage');
    const titleEl = document.getElementById('dlgTitle');
    const okBtn = document.getElementById('dlgOk');
    const cancelBtn = document.getElementById('dlgCancel');
    titleEl.textContent = opts.title || 'Notice';
    msgEl.textContent = message || '';
    okBtn.textContent = opts.okText || 'OK';
    okBtn.classList.remove('danger');
    cancelBtn.style.display = 'none';
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    return await new Promise((resolve)=>{
      const close = () => {
        modal.classList.add('hidden');
        modal.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
        okBtn.removeEventListener('click', onOk);
        document.removeEventListener('keydown', onKey);
        cancelBtn.style.display = '';
        resolve(true);
      };
      const onOk = () => close();
      const onKey = (e) => { if(e.key==='Escape' || e.key==='Enter') close(); };
      okBtn.addEventListener('click', onOk, {once:true});
      document.addEventListener('keydown', onKey);
      okBtn.focus();
    });
  }

  Dialogs.confirmDialog = confirmDialog;
  Dialogs.promptDialog = promptDialog;
  Dialogs.alertDialog = alertDialog;

  global.ChatsDialogs = Dialogs;
})(window);
