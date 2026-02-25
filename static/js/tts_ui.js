(function initTTSUI(global){
  const state = global.__TTS_STATE || {};

  function clearBlink(btn){
    if (!btn) return;
    if (btn._blinkInterval) {
      clearInterval(btn._blinkInterval);
      btn._blinkInterval = null;
    }
    btn.style.opacity = '';
  }

  function setButtonsDefault(){
    document.querySelectorAll('#speakBtn').forEach((b) => {
      clearBlink(b);
      b.classList.remove('encoding', 'playing');
      b.title = 'Play audio';
      b.style.opacity = '';
    });
  }

  function setButtonsLoading(target){
    document.querySelectorAll('#speakBtn').forEach((b) => {
      clearBlink(b);
      if (b === target) {
        b.classList.add('encoding');
        b.classList.remove('playing');
        b.title = 'Encoding audio';
        b.style.opacity = '';
      } else {
        b.classList.remove('encoding', 'playing');
        b.title = 'Play audio';
        b.style.opacity = '';
      }
    });
  }

  function setButtonsPlaying(target){
    document.querySelectorAll('#speakBtn').forEach((b) => {
      clearBlink(b);
      if (b === target) {
        let step = 0;
        b.classList.add('playing');
        b.classList.remove('encoding');
        b.title = 'Stop audio';
        b._blinkInterval = setInterval(() => {
          step = (step + 1) % 60;
          const phase = step / 60;
          const eased = phase < 0.5 ? (phase * 2) : (2 - phase * 2);
          b.style.opacity = (0.55 + eased * 0.45).toFixed(2);
        }, 1000 / 30);
      } else {
        b.classList.remove('playing', 'encoding');
        b.title = 'Play audio';
        b.style.opacity = '';
      }
    });
  }

  function resetBlink(btn){
    clearBlink(btn);
  }

  global.TTSUI = {
    clearBlink,
    setButtonsDefault,
    setButtonsLoading,
    setButtonsPlaying,
    resetBlink,
  };

  // Keep defaults applied on load.
  setButtonsDefault();
})(window);
