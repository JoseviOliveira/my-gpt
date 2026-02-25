/*
 * tts.js — text-to-speech orchestration layer
 * Delegates voice selection, player controls, and language detection to helper modules.
 */
(function initTTS(global){
  const STATE = global.__TTS_STATE || (global.__TTS_STATE = {});
  const UI = global.TTSUI;
  const Stream = global.TTSStream;
  const TTS = global.TTS || (global.TTS = {});
  const TTSVoices = global.TTSVoices || {};
  const TTSPlayer = global.TTSPlayer || {};
  const TTSLanguage = global.TTSLanguage || {};

  function handleChunkMessage(msg, btn, lang, text){
    const callbacks = {
      onPlay: (targetBtn, resolvedLang, originalText) => {
        UI.setButtonsPlaying(targetBtn);
        TTSLanguage.reportLatency(targetBtn, 'coqui', { lang: resolvedLang, textLen: originalText.length });
        STATE.isSpeaking = true;
        STATE.activeMode = 'coqui';
      },
      onError: (ev) => {
        console.warn('[tts] chunk playback error', ev);
        TTSPlayer.finalizePlayback();
      },
      onNotAllowed: (err) => {
        console.info('[tts] falling back to browser speech due to NotAllowedError');
        TTSPlayer.finalizePlayback(true);
        TTSPlayer.speakViaBrowser(text, lang, btn);
      },
      finalize(forceSilent = false){
        TTSPlayer.finalizePlayback(forceSilent);
      },
    };

    if (msg.type === 'chunk') {
      if (!msg.audio) return;
      const binary = atob(msg.audio);
      const len = binary.length;
      const buffer = new Uint8Array(len);
      for (let i = 0; i < len; i += 1) buffer[i] = binary.charCodeAt(i);
      Stream.enqueue(new Blob([buffer], { type: 'audio/wav' }), msg, callbacks);
      return;
    }
    if (msg.type === 'done') {
      Stream.markDone(callbacks);
      return;
    }
    if (msg.type === 'error') {
      console.warn('[tts] backend streaming error', msg.detail);
      TTSPlayer.finalizePlayback();
      if (TTSVoices.supportsBrowserSpeech) {
        TTSPlayer.speakViaBrowser(text, lang, btn);
      }
    }
  }

  async function speakViaCoqui(text, lang, btn){
    const authHeader = typeof global.authHeader === 'function' ? (global.authHeader() || {}) : {};
    const headers = Object.assign({ 'Content-Type': 'application/json', 'Accept': 'application/x-ndjson' }, authHeader);
    const controller = new AbortController();
    STATE.pendingController = controller;
    STATE.activeMode = 'coqui';
    UI.setButtonsLoading(btn);
    Stream.init(btn, lang, text);

    console.debug('[tts] coqui synthesis request', { lang, textLen: text.length });
    try {
      const resp = await fetch('/api/tts/speak', {
        method: 'POST',
        headers,
        body: JSON.stringify({ text, lang }),
        signal: controller.signal,
      });
      STATE.pendingController = null;

      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({}));
        console.warn('[tts] coqui request failed', resp.status, errBody);
        if (resp.status === 422 && TTSVoices.supportsBrowserSpeech) {
          TTSPlayer.speakViaBrowser(text, lang, btn);
          return;
        }
        throw new Error(`tts status ${resp.status}`);
      }

      if (!resp.body || typeof resp.body.getReader !== 'function') {
        console.warn('[tts] streaming unsupported by browser; falling back to buffered playback');
        const blob = await resp.blob();
        Stream.reset();
        const url = URL.createObjectURL(blob);
        STATE.activeObjectUrl = url;
        const audio = new Audio(url);
        STATE.activeAudio = audio;
        audio.addEventListener('ended', () => TTSPlayer.finalizePlayback());
        audio.addEventListener('error', () => TTSPlayer.finalizePlayback());
        const playPromise = audio.play();
        if (playPromise && typeof playPromise.then === 'function') {
          playPromise.then(() => {
            UI.setButtonsPlaying(btn);
            TTSLanguage.reportLatency(btn, 'coqui', { lang, textLen: text.length });
            STATE.isSpeaking = true;
          }).catch((err) => {
            console.warn('[tts] coqui playback error', err);
            if (err && (err.name === 'NotAllowedError' || ('' + err).includes('NotAllowed'))) {
              TTSPlayer.speakViaBrowser(text, lang, btn);
              return;
            }
            TTSPlayer.finalizePlayback();
          });
        } else {
          UI.setButtonsPlaying(btn);
          TTSLanguage.reportLatency(btn, 'coqui', { lang, textLen: text.length });
          STATE.isSpeaking = true;
        }
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      const streamCallbacks = {
        finalize(forceSilent = false){
          TTSPlayer.finalizePlayback(forceSilent);
        },
      };

      Stream.reset();
      Stream.init(btn, lang, text);

      const processStream = async () => {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buffer.indexOf('\n')) >= 0) {
            const line = buffer.slice(0, idx).trim();
            buffer = buffer.slice(idx + 1);
            if (!line) continue;
            let msg;
            try {
              msg = JSON.parse(line);
            } catch (parseErr) {
              console.warn('[tts] invalid chunk payload', parseErr, line);
              continue;
            }
            if (msg.type === 'meta') continue;
            handleChunkMessage(msg, btn, lang, text);
          }
        }
        Stream.markDone(streamCallbacks);
      };

      processStream().catch((err) => {
        if (err && err.name === 'AbortError') return;
        console.warn('[tts] coqui stream failure', err);
        TTSPlayer.finalizePlayback();
        if (TTSVoices.supportsBrowserSpeech) TTSPlayer.speakViaBrowser(text, lang, btn);
      });
    } catch (err) {
      if (controller.signal.aborted) {
        console.info('[tts] coqui request aborted by user');
        TTSPlayer.finalizePlayback(true);
        return;
      }
      console.warn('[tts] coqui synthesis failed', err);
      if (TTSVoices.supportsBrowserSpeech) {
        TTSPlayer.speakViaBrowser(text, lang, btn);
        return;
      }
      TTSPlayer.finalizePlayback();
    }
  }

  async function handleSpeak(btn){
    try {
      if (!STATE.audioCtx && (global.AudioContext || global.webkitAudioContext)) {
        const Ctx = global.AudioContext || global.webkitAudioContext;
        STATE.audioCtx = new Ctx();
      }
      if (STATE.audioCtx && STATE.audioCtx.state === 'suspended') {
        await STATE.audioCtx.resume();
      }
    } catch (e) {
      // non-fatal if AudioContext is unavailable
    }

    const text = TTSLanguage.extractText(btn);
    if (!text) return;
    const mode = (typeof TTS.getMode === 'function' ? TTS.getMode() : 'browser').toLowerCase();

    if (STATE.pendingController || STATE.isSpeaking) {
      if (mode === STATE.activeMode) {
        TTSPlayer.cancelPlayback();
        return;
      }
      if (STATE.isSpeaking && STATE.activeMode === 'browser') {
        TTSPlayer.cancelPlayback();
        return;
      }
    }

    UI.setButtonsLoading(btn);

    let lang = TTSLanguage.getMessageLang(btn);
    if (!lang) {
      try {
        lang = await TTSLanguage.resolveLanguage(btn, text);
      } catch (err) {
        console.warn('[tts] language resolution failed', err);
      }
    }
    lang = (lang || '').trim().toLowerCase() || 'en';

    try { window.logUserAction?.('Speech', 'TTS play'); } catch {}
    TTSLanguage.markSpeakStart(btn, text, lang, mode);

    if (mode === 'coqui') {
      await speakViaCoqui(text, lang, btn);
      return;
    }

    if (!TTSVoices.supportsBrowserSpeech) {
      console.warn('[tts] falling back to backend because SpeechSynthesis is unavailable');
      await speakViaCoqui(text, lang, btn);
      return;
    }

    UI.setButtonsLoading(btn);
    TTSPlayer.speakViaBrowser(text, lang, btn);
  }

  document.addEventListener('click', (ev) => {
    const target = ev.target;
    const btn = target && target.closest ? target.closest('#speakBtn') : (target && target.id === 'speakBtn' ? target : null);
    if (!btn) return;
    console.log('[tts] speak button clicked', {
      textPreview: (btn.dataset?.text || '').slice(0, 80) || TTSLanguage.extractText(btn)?.slice(0, 80) || '<empty>',
      mode: typeof TTS.getMode === 'function' ? TTS.getMode() : 'unknown'
    });
    ev.preventDefault();
    ev.stopPropagation();
    handleSpeak(btn).catch((err) => {
      console.warn('[tts] unhandled speak error', err);
      TTSPlayer.finalizePlayback();
    });
  });
})(window);
