/*
 * tts_voices.js — Voice selection and browser voice management
 */
(function(global){
  const STATE = global.__TTS_STATE || (global.__TTS_STATE = {});
  const TTSVoices = global.TTSVoices || (global.TTSVoices = {});

  const supportsBrowserSpeech = !!global.speechSynthesis;

  function loadVoices(){
    if (!supportsBrowserSpeech) return [];
    const vs = speechSynthesis.getVoices();
    if (vs && vs.length) STATE.voicesCache = vs;
    return STATE.voicesCache || [];
  }

  if (supportsBrowserSpeech) {
    loadVoices();
    const handler = () => loadVoices();
    if (speechSynthesis.addEventListener) {
      speechSynthesis.addEventListener('voiceschanged', handler);
    } else {
      speechSynthesis.onvoiceschanged = handler;
    }
  } else {
    console.info('[tts] SpeechSynthesis API unavailable; defaulting to backend mode.');
  }

  function pickVoiceFor(langCode){
    const voices = loadVoices();
    if (!voices.length) return null;
    const code = (langCode || '').toLowerCase();
    const exact = voices.find(v => (v.lang || '').toLowerCase() === code);
    if (exact) return exact;
    const pref = voices.find(v => (v.lang || '').toLowerCase().startsWith(code));
    if (pref) return pref;
    const hints = {
      es: ['Monica','Paulina','Jorge','Google español','Spanish','Luciana','Diego'],
      fr: ['Amélie','Thomas','Google français','French','Aurélie'],
      en: ['Samantha','Alex','Victoria','Daniel','Google US English','English'],
    }[code] || [];
    return voices.find(v => hints.some(h => (v.name || '').includes(h))) || null;
  }

  TTSVoices.loadVoices = loadVoices;
  TTSVoices.pickVoiceFor = pickVoiceFor;
  TTSVoices.supportsBrowserSpeech = supportsBrowserSpeech;

  global.TTSVoices = TTSVoices;
})(window);
