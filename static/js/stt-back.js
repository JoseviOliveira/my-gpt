/*
 * stt-back.js — Whisper backend recording + upload
 * - Activates when the user selects the 'whisper' STT mode
 * - Records audio via MediaRecorder, converts to WAV, posts /api/stt
 * - Appends transcriptions into the composer without auto-send
 * - Shares mic button UX conventions with browser STT
 */
(function initWhisperSTT(global){
  const micBtn = document.getElementById('micBtn');
  const input = document.getElementById('t');
  if (!micBtn || !input) return;

  const STT = global.STT || {};
  const supportsMedia = !!(global.navigator?.mediaDevices?.getUserMedia);
  if (!supportsMedia) {
    console.debug('[stt-back] mediaDevices.getUserMedia not available.');
    const whisperOption = document.querySelector('[data-settings-stt][data-stt-mode="whisper"]');
    if (whisperOption) {
      whisperOption.setAttribute('disabled', 'disabled');
      whisperOption.setAttribute('aria-disabled', 'true');
    }
    const getMode = typeof STT.getMode === 'function' ? STT.getMode.bind(STT) : null;
    const setMode = typeof STT.setMode === 'function' ? STT.setMode.bind(STT) : null;
    const getOptions = typeof STT.getAvailableModes === 'function'
      ? STT.getAvailableModes.bind(STT)
      : null;
    if (getMode && setMode) {
      const current = getMode();
      if (current === 'whisper') {
        const browserOption = document.querySelector('[data-settings-stt][data-stt-mode="browser"]');
        const fallback = getOptions ? (getOptions().find(mode => mode !== 'whisper') || 'browser') : 'browser';
        if (browserOption && browserOption.hasAttribute('disabled') && fallback === 'browser') {
          micBtn.dataset.sttMode = 'unsupported';
          micBtn.disabled = true;
          micBtn.title = 'Speech input unavailable';
          micBtn.style.opacity = '0.5';
        } else {
          setMode(fallback);
        }
      }
    }
    return;
  }

  let enabled = false;
  let mediaStream = null;
  let mediaRecorder = null;
  let chunks = [];
  let isRecording = false;
  let toggling = false;

  const MIC_IDLE_LABEL = 'mic';
  const MIC_RECORDING_LABEL = 'recording ●';

  function applyMicState(state, titleText){
    micBtn.classList.remove('recording', 'decoding', 'disabled');
    micBtn.disabled = false;
    if (state === 'recording') {
      micBtn.classList.add('recording');
    } else if (state === 'decoding') {
      micBtn.classList.add('decoding');
    } else if (state === 'disabled') {
      micBtn.classList.add('disabled');
      micBtn.disabled = true;
    } else {
    }
    micBtn.title = titleText || 'Microphone';
  }

  function setMicVisual(active){
    if (!enabled) {
      applyMicState('disabled', 'Speech input unavailable');
      return;
    }
    if (active) applyMicState('recording', 'Tap to stop');
    else applyMicState('idle', 'Tap to talk');
  }

  function setMicDecoding(){
    if (!enabled) {
      applyMicState('disabled', 'Speech input unavailable');
      return;
    }
    applyMicState('decoding', 'Decoding audio');
  }

  async function startRec(){
    if (!supportsMedia) {
      console.warn('[stt-back] getUserMedia not supported in this environment.');
      return;
    }
    try { window.logUserAction?.('Speech', 'STT start'); } catch {}
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
    mediaRecorder = new MediaRecorder(mediaStream, mime ? { mimeType: mime } : {});
    chunks = [];
    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size) {
        chunks.push(e.data);
        console.debug('[stt-back] dataavailable', e.data.type, e.data.size);
      } else {
        console.debug('[stt-back] dataavailable: empty chunk');
      }
    };
    mediaRecorder.onerror = (e) => {
      console.warn('[stt-back] MediaRecorder error', e.error || e.name || e);
    };
    mediaRecorder.start();
    console.debug('[stt-back] recording started', { mime: mediaRecorder.mimeType });
  }

  async function stopRec(){
    if (!mediaRecorder) return;
    try { window.logUserAction?.('Speech', 'STT stop'); } catch {}
    return new Promise((resolve) => {
      mediaRecorder.onstop = async () => {
        try {
          const blob = new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' });
          console.debug('[stt-back] recorder stopped', { chunkCount: chunks.length, size: blob.size });
          const wavBlob = await toWav(blob);
          console.debug('[stt-back] wav ready', { size: wavBlob.size });
          await sendToSTT(wavBlob);
        } catch (err) {
          console.warn('[stt-back] voice stop error', err);
        } finally {
          if (mediaStream) {
            mediaStream.getTracks().forEach(track => { try { track.stop(); } catch {} });
          }
          mediaStream = null;
          mediaRecorder = null;
          chunks = [];
        }
        resolve();
      };
      try {
        mediaRecorder.stop();
        console.debug('[stt-back] recording stop requested');
      } catch (err) {
        console.warn('[stt-back] mediaRecorder stop failed', err);
        resolve();
      }
    });
  }

  async function toWav(blob){
    const ac = new (global.AudioContext || global.webkitAudioContext)();
    const buf = await blob.arrayBuffer();
    const audio = await ac.decodeAudioData(buf);
    try { ac.close(); } catch {}
    const ch0 = audio.getChannelData(0);
    let mono = ch0;
    if (audio.numberOfChannels > 1) {
      const ch1 = audio.getChannelData(1);
      mono = new Float32Array(ch0.length);
      for (let i = 0; i < mono.length; i++) mono[i] = 0.5 * (ch0[i] + ch1[i]);
    }
    const pcm = new Int16Array(mono.length);
    for (let i = 0; i < mono.length; i++) {
      const s = Math.max(-1, Math.min(1, mono[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    const wav = buildWav(pcm, audio.sampleRate);
    return new Blob([wav.header, wav.data], { type: 'audio/wav' });
  }

  function buildWav(pcm16, sampleRate){
    const numCh = 1;
    const bps = 16;
    const header = new ArrayBuffer(44);
    const dv = new DataView(header);
    const dataLen = pcm16.byteLength;
    const byteRate = sampleRate * numCh * (bps / 8);
    const blockAlign = numCh * (bps / 8);
    dv.setUint32(0, 0x52494646, false);
    dv.setUint32(4, 36 + dataLen, true);
    dv.setUint32(8, 0x57415645, false);
    dv.setUint32(12, 0x666d7420, false);
    dv.setUint32(16, 16, true);
    dv.setUint16(20, 1, true);
    dv.setUint16(22, numCh, true);
    dv.setUint32(24, sampleRate, true);
    dv.setUint32(28, byteRate, true);
    dv.setUint16(32, blockAlign, true);
    dv.setUint16(34, bps, true);
    dv.setUint32(36, 0x64617461, false);
    dv.setUint32(40, dataLen, true);
    return { header, data: pcm16.buffer };
  }

  async function sendToSTT(wavBlob){
    const fd = new FormData();
    fd.append('file', wavBlob, 'audio.wav');
    const headers = typeof global.authHeader === 'function' ? global.authHeader() : {};
    if (!headers || !headers.Authorization) {
      console.warn('[stt-back] Whisper STT missing Authorization header. Make sure you are logged in.');
    }
    try {
      console.debug('[stt-back] Whisper STT upload', {
        size: typeof wavBlob?.size === 'number' ? wavBlob.size : null,
        auth: !!(headers && headers.Authorization)
      });
    } catch {}
    let resp;
    try {
      resp = await fetch('/api/stt', { method: 'POST', headers, body: fd, credentials: 'same-origin' });
    } catch (err) {
      console.warn('[stt-back] Whisper STT request failed', { error: err });
      return;
    }
    if (!resp.ok) {
      let detail = '';
      try { detail = await resp.text(); } catch {}
      let trace = null;
      try { trace = resp.headers.get('x-request-id') || resp.headers.get('x-trace-id') || resp.headers.get('x-correlation-id'); }
      catch {}
      console.warn('[stt-back] Whisper STT error', {
        status: resp.status,
        statusText: resp.statusText,
        trace,
        detail
      });
      return;
    }
    let data;
    try {
      data = await resp.json();
    } catch (err) {
      console.warn('[stt-back] Whisper STT invalid JSON', { error: err });
      return;
    }
    const text = data && data.text;
    try {
      console.debug('[stt-back] Whisper STT success', {
        duration: data?.duration,
        rtf: data?.rtf,
        textLength: text ? text.length : 0
      });
    } catch {}
    if (text && text.trim()) {
      const existing = input.value || '';
      const glue = existing && !/\s$/.test(existing) ? ' ' : '';
      input.value = existing + glue + text.trim();
      input.dispatchEvent(new Event('input'));
      input.focus();
      try { window.logUserAction?.('Speech', 'STT result accepted'); } catch {}
    } else {
      console.debug('[stt-back] Whisper STT responded without text', data);
    }
  }

  async function toggleRecording(){
    if (!enabled || toggling) return;
    toggling = true;
    try {
      if (!isRecording) {
        setMicVisual(true);
        try {
          await startRec();
          isRecording = true;
        } catch (err) {
          setMicVisual(false);
          throw err;
        }
      } else {
        try {
          setMicDecoding();
          await stopRec();
        } finally {
          isRecording = false;
          setMicVisual(false);
        }
      }
    } catch (err) {
      console.warn('[stt-back] voice toggle error', err);
    } finally {
      toggling = false;
    }
  }

  micBtn.addEventListener('click', (event) => {
    if (!enabled) return;
    event.preventDefault();
    event.stopPropagation();
    toggleRecording();
  });

  function applyMode(mode){
    const nextEnabled = mode === 'whisper';
    micBtn.dataset.sttMode = mode;
    if (nextEnabled === enabled) return;
    enabled = nextEnabled;
    if (!enabled) {
      if (isRecording) {
        stopRec().catch(() => {});
        isRecording = false;
      }
      micBtn.classList.remove('recording', 'decoding', 'disabled');
      micBtn.disabled = false;
      return;
    }
    setMicVisual(false);
  }

  if (typeof STT.onModeChange === 'function') {
    STT.onModeChange(applyMode, { immediate: true });
  } else {
    applyMode('whisper');
  }

  global.addEventListener('beforeunload', () => {
    if (mediaStream) {
      mediaStream.getTracks().forEach(track => { try { track.stop(); } catch {} });
      mediaStream = null;
    }
  });
})(window);
