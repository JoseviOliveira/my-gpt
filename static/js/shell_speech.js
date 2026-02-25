/*
 * shell_speech.js — STT/TTS mode control wiring for settings panel
 * Extracted from shell.js to reduce file size
 */
(function(global){
  const SpeechControls = {};

  const LABEL_COPY = {
    stt: { browser: 'Web', whisper: 'Server' },
    tts: { browser: 'Web', coqui: 'Server' },
  };

  let speechModes = { stt: 'browser', tts: 'browser' };
  let sttModeCleanup = () => {};
  let ttsModeCleanup = () => {};

  SpeechControls.getSpeechModes = () => ({ ...speechModes });
  SpeechControls.setSpeechMode = (type, mode) => {
    if (type === 'stt' || type === 'tts') speechModes[type] = mode;
  };

  SpeechControls.attachSttModeControls = function(sttModeButtons, refreshSpeechHint){
    if (typeof sttModeCleanup === 'function') sttModeCleanup();
    sttModeCleanup = () => {};
    if (!sttModeButtons || !sttModeButtons.length) return;
    
    const observers = [];
    const getValue = (el) => (el.dataset.sttMode || el.value || '').toLowerCase();

    const getAllowedModes = () => {
      const STT = global.STT || {};
      if (typeof STT.getAvailableModes !== 'function') return null;
      try {
        const list = STT.getAvailableModes();
        if (!Array.isArray(list)) return null;
        return list.map((item) => String(item || '').toLowerCase()).filter(Boolean);
      } catch { return null; }
    };

    const syncAvailability = () => {
      const allowed = getAllowedModes();
      sttModeButtons.forEach((input) => {
        const value = getValue(input);
        const isAllowed = !allowed || allowed.includes(value);
        if (!isAllowed) {
          if (!input.disabled) input.disabled = true;
          input.dataset.sttAutoDisabled = 'true';
        } else if (input.dataset.sttAutoDisabled === 'true') {
          input.disabled = false;
          delete input.dataset.sttAutoDisabled;
        }
        input.setAttribute('aria-disabled', input.disabled ? 'true' : 'false');
        const wrapper = input.closest('[data-stt-option]');
        if (wrapper) {
          wrapper.classList.toggle('disabled', input.disabled);
          if (input.disabled) wrapper.setAttribute('aria-disabled', 'true');
          else wrapper.removeAttribute('aria-disabled');
        }
      });
    };

    const updateActive = (mode) => {
      syncAvailability();
      sttModeButtons.forEach((input) => {
        const value = getValue(input);
        const isActive = value === mode;
        if ('checked' in input && input.checked !== isActive) input.checked = isActive;
        input.setAttribute('aria-checked', isActive ? 'true' : 'false');
        const wrapper = input.closest('[data-stt-option]');
        if (wrapper) {
          const labelText = LABEL_COPY.stt[value] || null;
          if (labelText) {
            const span = wrapper.querySelector('span');
            if (span && span.textContent !== labelText) span.textContent = labelText;
          }
          wrapper.classList.toggle('active', isActive);
          wrapper.setAttribute('aria-checked', isActive ? 'true' : 'false');
          wrapper.setAttribute('role', 'radio');
        }
      });
      if (mode) speechModes.stt = mode;
      if (typeof refreshSpeechHint === 'function') refreshSpeechHint();
    };

    let modeCleanup = () => {};
    const activeSTT = global.STT || {};
    if (typeof activeSTT.onModeChange === 'function') {
      modeCleanup = activeSTT.onModeChange(updateActive, { immediate: true }) || (() => {});
    } else {
      updateActive(typeof activeSTT.getMode === 'function' ? activeSTT.getMode() : '');
    }

    sttModeButtons.forEach((input) => {
      const value = getValue(input);
      if (!value) return;
      const wrapper = input.closest('[data-stt-option]');
      if (wrapper) {
        const desired = LABEL_COPY.stt[value];
        if (desired) {
          const span = wrapper.querySelector('span');
          if (span && span.textContent !== desired) span.textContent = desired;
        }
      }
      if (input.dataset.sttModeHandlerAttached !== '1') {
        input.addEventListener('change', () => {
          if (input.disabled || !input.checked) return;
          const STT = global.STT || {};
          const prevMode = typeof STT.getMode === 'function' ? STT.getMode() : '';
          const nextMode = typeof STT.setMode === 'function' ? STT.setMode(value) : prevMode;
          if (typeof nextMode === 'string' && nextMode.toLowerCase() !== value) {
            const current = typeof STT.getMode === 'function' ? STT.getMode() : prevMode;
            updateActive(current);
          }
          try { window.logUserAction?.('Speech', `STT mode set: ${LABEL_COPY.stt[value] || value}`); } catch {}
        });
        input.dataset.sttModeHandlerAttached = '1';
      }
      if (typeof MutationObserver === 'function') {
        const observer = new MutationObserver(() => {
          const STT = global.STT || {};
          updateActive(typeof STT.getMode === 'function' ? STT.getMode() : '');
        });
        observer.observe(input, { attributes: true, attributeFilter: ['disabled'] });
        observers.push(observer);
      }
    });

    sttModeCleanup = () => {
      modeCleanup();
      observers.forEach((observer) => observer.disconnect());
    };
    syncAvailability();
  };

  SpeechControls.attachTtsModeControls = function(ttsModeButtons, refreshSpeechHint){
    if (typeof ttsModeCleanup === 'function') ttsModeCleanup();
    ttsModeCleanup = () => {};
    if (!ttsModeButtons || !ttsModeButtons.length) return;

    const observers = [];
    const getValue = (el) => (el.dataset.ttsMode || el.value || '').toLowerCase();

    const getAllowedModes = () => {
      const TTS = global.TTS || {};
      if (typeof TTS.getAvailableModes !== 'function') return null;
      try {
        const list = TTS.getAvailableModes();
        if (!Array.isArray(list)) return null;
        return list.map((item) => String(item || '').toLowerCase()).filter(Boolean);
      } catch { return null; }
    };

    const syncAvailability = () => {
      const allowed = getAllowedModes();
      ttsModeButtons.forEach((input) => {
        const value = getValue(input);
        const isAllowed = !allowed || allowed.includes(value);
        if (!isAllowed) {
          if (!input.disabled) input.disabled = true;
          input.dataset.ttsAutoDisabled = 'true';
        } else if (input.dataset.ttsAutoDisabled === 'true') {
          input.disabled = false;
          delete input.dataset.ttsAutoDisabled;
        }
        input.setAttribute('aria-disabled', input.disabled ? 'true' : 'false');
        const wrapper = input.closest('[data-tts-option]');
        if (wrapper) {
          wrapper.classList.toggle('disabled', input.disabled);
          if (input.disabled) wrapper.setAttribute('aria-disabled', 'true');
          else wrapper.removeAttribute('aria-disabled');
        }
      });
    };

    const updateActive = (mode) => {
      syncAvailability();
      ttsModeButtons.forEach((input) => {
        const value = getValue(input);
        const isActive = value === mode;
        if ('checked' in input && input.checked !== isActive) input.checked = isActive;
        input.setAttribute('aria-checked', isActive ? 'true' : 'false');
        const wrapper = input.closest('[data-tts-option]');
        if (wrapper) {
          const labelText = LABEL_COPY.tts[value] || null;
          if (labelText) {
            const span = wrapper.querySelector('span');
            if (span && span.textContent !== labelText) span.textContent = labelText;
          }
          wrapper.classList.toggle('active', isActive);
          wrapper.setAttribute('aria-checked', isActive ? 'true' : 'false');
          wrapper.setAttribute('role', 'radio');
        }
      });
      if (mode) speechModes.tts = mode;
      if (typeof refreshSpeechHint === 'function') refreshSpeechHint();
    };

    let modeCleanup = () => {};
    const activeTTS = global.TTS || {};
    if (typeof activeTTS.onModeChange === 'function') {
      modeCleanup = activeTTS.onModeChange(updateActive, { immediate: true }) || (() => {});
    } else {
      updateActive(typeof activeTTS.getMode === 'function' ? activeTTS.getMode() : '');
    }

    ttsModeButtons.forEach((input) => {
      const value = getValue(input);
      if (!value) return;
      const wrapper = input.closest('[data-tts-option]');
      if (wrapper) {
        const desired = LABEL_COPY.tts[value];
        if (desired) {
          const span = wrapper.querySelector('span');
          if (span && span.textContent !== desired) span.textContent = desired;
        }
      }
      if (input.dataset.ttsModeHandlerAttached !== '1') {
        input.addEventListener('change', () => {
          if (input.disabled || !input.checked) return;
          const TTS = global.TTS || {};
          const prevMode = typeof TTS.getMode === 'function' ? TTS.getMode() : '';
          const nextMode = typeof TTS.setMode === 'function' ? TTS.setMode(value) : prevMode;
          if (typeof nextMode === 'string' && nextMode.toLowerCase() !== value) {
            const current = typeof TTS.getMode === 'function' ? TTS.getMode() : prevMode;
            updateActive(current);
          }
          try { window.logUserAction?.('Speech', `TTS mode set: ${LABEL_COPY.tts[value] || value}`); } catch {}
        });
        input.dataset.ttsModeHandlerAttached = '1';
      }
      if (typeof MutationObserver === 'function') {
        const observer = new MutationObserver(() => {
          const TTS = global.TTS || {};
          updateActive(typeof TTS.getMode === 'function' ? TTS.getMode() : '');
        });
        observer.observe(input, { attributes: true, attributeFilter: ['disabled'] });
        observers.push(observer);
      }
    });

    ttsModeCleanup = () => {
      modeCleanup();
      observers.forEach((observer) => observer.disconnect());
    };
    syncAvailability();
  };

  global.ShellSpeech = SpeechControls;
})(window);
