(function initTTSState(global){
  if (global.__TTS_STATE) return;
  global.__TTS_STATE = {
    voicesCache: [],
    isSpeaking: false,
    activeMode: null,
    activeAudio: null,
    activeObjectUrl: '',
    pendingController: null,
    chunkQueue: [],
    chunkObjectUrls: [],
    chunkStreamState: null,
    chunkStreamDone: false,
  };
})(window);
