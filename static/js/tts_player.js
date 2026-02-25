/*
 * tts_player.js — Audio playback state management and controls
 */
(function(global){
  const STATE = global.__TTS_STATE || (global.__TTS_STATE = {});
  const TTSPlayer = global.TTSPlayer || (global.TTSPlayer = {});
  const UI = global.TTSUI;
  const Stream = global.TTSStream;
  const TTSVoices = global.TTSVoices || {};

  const BROWSER_CHUNK_LIMIT = 180;

  function stopAllAudio(){
    Stream.stopActiveAudio();
    Stream.reset();
    STATE.chunkStreamDone = false;
    STATE.isSpeaking = false;
    STATE.activeMode = null;
    STATE.activeObjectUrl = '';
    STATE.browserChunks = [];
    STATE.browserCancelled = false;
    if (STATE.pendingController) {
      try { STATE.pendingController.abort(); } catch {}
      STATE.pendingController = null;
    }
  }

  function finalizePlayback(forceSilent){
    if (!forceSilent) {
      Stream.stopActiveAudio();
    }
    Stream.reset();
    STATE.isSpeaking = false;
    STATE.activeMode = null;
    STATE.pendingController = null;
    STATE.browserChunks = [];
    STATE.browserCancelled = false;
    UI.setButtonsDefault();
  }

  function cancelPlayback(){
    STATE.browserCancelled = true;
    STATE.browserChunks = [];
    if (STATE.pendingController) {
      try { STATE.pendingController.abort(); } catch {}
      STATE.pendingController = null;
    }
    if (TTSVoices.supportsBrowserSpeech) {
      try { global.speechSynthesis.cancel(); } catch {}
    }
    try { window.logUserAction?.('Speech', 'TTS stop'); } catch {}
    Stream.cancel({
      finalize(forceSilent){
        finalizePlayback(forceSilent);
      },
    });
  }

  function splitSentences(text){
    const cleaned = (text || '').replace(/\s+/g, ' ').trim();
    if (!cleaned) return [];
    const parts = [];
    let current = '';
    const punct = new Set(['.', '!', '?', ';', ':', '…']);
    for (let i = 0; i < cleaned.length; i += 1) {
      const ch = cleaned[i];
      current += ch;
      if (punct.has(ch)) {
        let next = i + 1;
        while (next < cleaned.length && /\s/.test(cleaned[next])) {
          current += cleaned[next];
          next += 1;
        }
        parts.push(current.trim());
        current = '';
        i = next - 1;
      }
    }
    if (current.trim()) parts.push(current.trim());
    return parts.length ? parts : [cleaned];
  }

  function splitLongSegment(segment, maxLen){
    const out = [];
    const queue = [segment];
    while (queue.length) {
      let piece = (queue.shift() || '').trim();
      if (!piece) continue;
      if (piece.length <= maxLen) {
        out.push(piece);
        continue;
      }
      const window = Math.min(piece.length, maxLen);
      let splitIdx = piece.lastIndexOf('\n', window);
      if (splitIdx <= 0) {
        for (let idx = window - 1; idx >= 0; idx -= 1) {
          if ('.!?;:,'.includes(piece[idx])) {
            splitIdx = idx + 1;
            break;
          }
        }
      }
      if (splitIdx <= 0) splitIdx = piece.lastIndexOf(' ', window);
      if (splitIdx <= 0) splitIdx = window;
      const head = piece.slice(0, splitIdx).trim();
      const tail = piece.slice(splitIdx).trim();
      if (head) out.push(head);
      if (tail) queue.unshift(tail);
    }
    return out;
  }

  function chunkTextForBrowser(text, maxLen = BROWSER_CHUNK_LIMIT){
    const cleaned = (text || '').replace(/\s+/g, ' ').trim();
    if (!cleaned) return [];
    const sentences = splitSentences(cleaned);
    const expanded = [];
    sentences.forEach((part) => {
      if (part.length <= maxLen) expanded.push(part);
      else expanded.push(...splitLongSegment(part, maxLen));
    });

    const chunks = [];
    let current = '';
    expanded.forEach((part) => {
      if (!part) return;
      const tentative = current ? `${current} ${part}` : part;
      if (tentative.length <= maxLen) {
        current = tentative;
      } else {
        if (current) chunks.push(current);
        current = part;
      }
    });
    if (current) chunks.push(current);
    return chunks.length ? chunks : [cleaned];
  }

  function speakViaBrowser(text, lang, btn){
    if (!TTSVoices.supportsBrowserSpeech) {
      console.warn('[tts] SpeechSynthesis unavailable; cannot use browser mode.');
      return;
    }
    Stream.reset();
    if (STATE.pendingController) {
      try { STATE.pendingController.abort(); } catch {}
      STATE.pendingController = null;
    }
    try { global.speechSynthesis.cancel(); } catch {}

    STATE.browserCancelled = false;
    const voice = TTSVoices.pickVoiceFor(lang);
    const chunks = chunkTextForBrowser(text);
    console.debug('[tts] browser chunks prepared', { count: chunks.length, textLen: text.length });
    STATE.browserChunks = chunks.slice();
    let firstChunk = true;

    const speakNext = () => {
      if (!STATE.browserChunks.length || STATE.browserCancelled) {
        finalizePlayback(true);
        return;
      }
      const chunk = STATE.browserChunks.shift();
      const utt = new SpeechSynthesisUtterance(chunk);
      if (voice) {
        utt.voice = voice;
        utt.lang = voice.lang || lang;
      } else {
        const fallbacks = { en: 'en-US', fr: 'fr-FR', es: 'es-ES' };
        utt.lang = fallbacks[lang] || lang || 'en-US';
      }
      utt.rate = 1.0;
      utt.pitch = 1.0;
      utt.onstart = () => {
        if (firstChunk) {
          STATE.isSpeaking = true;
          STATE.activeMode = 'browser';
          UI.setButtonsPlaying(btn);
          const TTSLanguage = global.TTSLanguage || {};
          if (typeof TTSLanguage.reportLatency === 'function') {
            TTSLanguage.reportLatency(btn, 'browser', { lang, textLen: text.length });
          }
          firstChunk = false;
        }
      };
      utt.onend = () => {
        if (STATE.browserCancelled) {
          finalizePlayback(true);
          return;
        }
        if (!STATE.browserChunks.length) {
          finalizePlayback(true);
        } else {
          speakNext();
        }
      };
      utt.onerror = (ev) => {
        console.warn('[tts] browser synthesis error', ev);
        finalizePlayback(true);
      };
      global.speechSynthesis.speak(utt);
    };

    speakNext();
  }

  TTSPlayer.stopAllAudio = stopAllAudio;
  TTSPlayer.finalizePlayback = finalizePlayback;
  TTSPlayer.cancelPlayback = cancelPlayback;
  TTSPlayer.speakViaBrowser = speakViaBrowser;
  TTSPlayer.chunkTextForBrowser = chunkTextForBrowser;

  global.TTSPlayer = TTSPlayer;
})(window);
