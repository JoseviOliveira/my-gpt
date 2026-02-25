(function initTTSStream(global){
  const state = global.__TTS_STATE || {};
  const ui = global.TTSUI || {};

  function revokeUrls(){
    (state.chunkObjectUrls || []).forEach((url) => {
      try { URL.revokeObjectURL(url); } catch {}
    });
    state.chunkObjectUrls = [];
  }

  function reset(){
    state.chunkQueue = [];
    state.chunkObjectUrls = [];
    state.chunkStreamState = null;
    state.chunkStreamDone = false;
  }

  function init(btn, lang, text){
    revokeUrls();
    state.chunkQueue = [];
    state.chunkObjectUrls = [];
    state.chunkStreamDone = false;
    state.chunkStreamState = {
      btn,
      lang,
      text,
      reported: false,
      playing: false,
    };
  }

  function stopActiveAudio(){
    if (state.activeAudio) {
      try { state.activeAudio.pause(); } catch {}
      try { state.activeAudio.src = ''; } catch {}
      state.activeAudio = null;
    }
    if (state.activeObjectUrl) {
      try { URL.revokeObjectURL(state.activeObjectUrl); } catch {}
      state.activeObjectUrl = '';
    }
  }

  function enqueue(blob, meta, callbacks){
    if (!state.chunkStreamState) return;
    state.chunkQueue.push({ blob, meta, callbacks });
    if (!state.chunkStreamState.playing) {
      playNext();
    }
  }

  function markDone(callbacks){
    state.chunkStreamDone = true;
    if (!state.chunkStreamState || (!state.chunkStreamState.playing && !state.chunkQueue.length)) {
      callbacks.finalize();
    }
  }

  function handlePlaybackStart(btn, callbacks){
    if (!state.chunkStreamState) return;
    if (!state.chunkStreamState.reported) {
      callbacks.onPlay(btn, state.chunkStreamState.lang, state.chunkStreamState.text);
      state.chunkStreamState.reported = true;
    } else {
      ui.setButtonsPlaying(btn);
    }
    state.isSpeaking = true;
    state.activeMode = 'coqui';
  }

  function cleanupAudio(audio, url){
    audio.removeAttribute('src');
    try { URL.revokeObjectURL(url); } catch {}
    state.chunkObjectUrls = state.chunkObjectUrls.filter((candidate) => candidate !== url);
    if (state.activeAudio === audio) {
      state.activeAudio = null;
    }
  }

  function playNext(){
    if (!state.chunkStreamState) return;
    if (!state.chunkQueue.length) {
      state.chunkStreamState.playing = false;
      return;
    }

    const next = state.chunkQueue.shift();
    if (!next) return;

    const { blob, callbacks } = next;
    if (!callbacks) return;

    const url = URL.createObjectURL(blob);
    state.chunkObjectUrls.push(url);

    stopActiveAudio();
    const audio = new Audio(url);
    state.activeAudio = audio;
    state.chunkStreamState.playing = true;
    const btn = state.chunkStreamState.btn;

    const finalizeOrQueue = () => {
      if (state.chunkQueue.length) {
        playNext();
      } else if (state.chunkStreamDone) {
        callbacks.finalize();
      } else if (state.chunkStreamState) {
        state.chunkStreamState.playing = false;
      }
    };

    const onEnded = () => {
      audio.removeEventListener('ended', onEnded);
      audio.removeEventListener('error', onError);
      cleanupAudio(audio, url);
      finalizeOrQueue();
    };

    const onError = (ev) => {
      audio.removeEventListener('ended', onEnded);
      audio.removeEventListener('error', onError);
      cleanupAudio(audio, url);
      if (state.chunkStreamState) {
        state.chunkStreamState.playing = false;
      }
      callbacks.onError(ev);
    };

    audio.addEventListener('ended', onEnded);
    audio.addEventListener('error', onError);

    const playPromise = audio.play();
    const startPlayback = () => handlePlaybackStart(btn, callbacks);

    if (playPromise && typeof playPromise.then === 'function') {
      playPromise.then(startPlayback).catch((err) => callbacks.onNotAllowed(err));
    } else {
      startPlayback();
    }
  }

  function cancel(callbacks){
    stopActiveAudio();
    revokeUrls();
    reset();
    callbacks.finalize(true);
  }

  global.TTSStream = {
    init,
    enqueue,
    markDone,
    playNext: () => {},
    cancel,
    stopActiveAudio,
    reset,
  };
})(window);
