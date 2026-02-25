/*
 * shell_settings.js — Settings panel management
 */
(function(global){
  const ShellSettings = global.ShellSettings || (global.ShellSettings = {});
  let settingsToggleBtn = null;
  let settingsPanel = null;
  let removeSettingsOutsideHandler = () => {};
  let removeSettingsKeyHandler = () => {};

  function init(options = {}){
    settingsToggleBtn = options.settingsToggleBtn || null;
    settingsPanel = options.settingsPanel || null;
    attachSettingsPanel();
  }

  function isSettingsOpen(){
    return settingsPanel && !settingsPanel.classList.contains('hidden');
  }

  function handleSettingsOutside(event){
    if (!settingsPanel || settingsPanel.classList.contains('hidden')) return;
    const card = settingsPanel.querySelector('.settings-modal-card');
    if (card && card.contains(event.target)) return;
    if (settingsToggleBtn && settingsToggleBtn.contains(event.target)) return;
    closeSettingsPanel();
  }

  function handleSettingsKeydown(event){
    if (event.key !== 'Escape') return;
    if (!isSettingsOpen()) return;
    event.preventDefault();
    closeSettingsPanel({ returnFocus: true });
  }

  function openSettingsPanel({ focusFirst = false } = {}){
    if (!settingsPanel) return;
    if (!settingsPanel.classList.contains('hidden')) return;
    settingsPanel.classList.remove('hidden');
    settingsPanel.setAttribute('aria-hidden', 'false');
    settingsToggleBtn?.setAttribute('aria-expanded', 'true');
    settingsToggleBtn?.classList.add('active');
    try { window.logUserAction?.('Settings & Navigation', 'Open settings'); } catch {}

    const onPointer = (ev) => handleSettingsOutside(ev);
    const onKeydown = (ev) => handleSettingsKeydown(ev);
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKeydown);
    removeSettingsOutsideHandler = () => {
      document.removeEventListener('pointerdown', onPointer);
    };
    removeSettingsKeyHandler = () => {
      document.removeEventListener('keydown', onKeydown);
    };

    if (focusFirst) {
      const firstItem = settingsPanel.querySelector('button, [href], [tabindex]:not([tabindex="-1"])');
      if (firstItem) firstItem.focus();
    }
  }

  function closeSettingsPanel({ returnFocus = false } = {}){
    if (!settingsPanel) return;
    if (settingsPanel.classList.contains('hidden')) return;
    settingsPanel.classList.add('hidden');
    settingsPanel.setAttribute('aria-hidden', 'true');
    settingsToggleBtn?.setAttribute('aria-expanded', 'false');
    settingsToggleBtn?.classList.remove('active');
    try { window.logUserAction?.('Settings & Navigation', 'Close settings'); } catch {}
    removeSettingsOutsideHandler();
    removeSettingsKeyHandler();
    removeSettingsOutsideHandler = () => {};
    removeSettingsKeyHandler = () => {};
    if (returnFocus && settingsToggleBtn) settingsToggleBtn.focus();
  }

  function toggleSettingsPanel(){
    const focusFirst = document.activeElement === settingsToggleBtn;
    if (isSettingsOpen()) closeSettingsPanel({ returnFocus: false });
    else openSettingsPanel({ focusFirst });
  }

  function attachSettingsPanel(){
    if (!settingsToggleBtn || !settingsPanel) return;
    settingsPanel.classList.add('hidden');
    settingsPanel.setAttribute('aria-hidden', 'true');
    settingsToggleBtn.setAttribute('aria-expanded', 'false');
    settingsToggleBtn.addEventListener('click', () => {
      toggleSettingsPanel();
    });
    const closeBtn = document.getElementById('settingsClose');
    closeBtn?.addEventListener('click', () => closeSettingsPanel({ returnFocus: true }));
  }

  ShellSettings.init = init;
  ShellSettings.isSettingsOpen = isSettingsOpen;
  ShellSettings.openSettingsPanel = openSettingsPanel;
  ShellSettings.closeSettingsPanel = closeSettingsPanel;
  ShellSettings.toggleSettingsPanel = toggleSettingsPanel;

  global.ShellSettings = ShellSettings;
})(window);
