/* chats_metadata.js — Metadata polling and refresh management */

const METADATA_POLL_INITIAL_DELAY = Number(window.APP_METADATA_INITIAL_DELAY || 5000);
const METADATA_POLL_BACKOFF = Number(window.APP_METADATA_BACKOFF || 1.5);
const METADATA_POLL_MAX_DELAY = Number(window.APP_METADATA_MAX_DELAY || 20000);
const METADATA_POLL_MAX_ATTEMPTS = Number(window.APP_METADATA_MAX_ATTEMPTS || 6);

const metadataTimers = {};

function scheduleMetadataRefresh(sid, apiFunc, updateFunc, opts = {}) {
  if (!sid) return;
  const attempt = opts.attempt || 0;
  const delay = opts.delay || METADATA_POLL_INITIAL_DELAY;
  const maxAttempts = Number.isFinite(opts.maxAttempts) ? opts.maxAttempts : METADATA_POLL_MAX_ATTEMPTS;
  
  metadataTimers[sid] = metadataTimers[sid] || [];
  const baseDelay = attempt === 0 ? METADATA_POLL_INITIAL_DELAY : delay;
  
  if (console && console.debug) {
    console.debug('[chats] schedule metadata', { sid, baseDelay, attempt, maxAttempts });
  }
  
  const timer = setTimeout(async () => {
    metadataTimers[sid] = (metadataTimers[sid] || []).filter((t) => t !== timer);
    try {
      const prevState = opts.prevState || {};
      const prevTitle = prevState.title || prevState.title_ai || '';
      const prevSummary = prevState.summary || prevState.summary_ai || '';
      
      const obj = await apiFunc(`/api/session/${sid}`);
      
      const displayTitle = obj.title || obj.title_ai || '';
      const summaryText = obj.summary || obj.summary_ai || '';
      const titleChanged = displayTitle !== prevTitle;
      const summaryChanged = summaryText !== prevSummary;
      
      if (typeof updateFunc === 'function') {
        updateFunc(obj, { titleChanged, summaryChanged });
      }
      
      const metaMissing = (!obj.title && !obj.title_ai) || (!obj.summary && !obj.summary_ai);
      const changed = titleChanged || summaryChanged;
      const shouldRetry = (metaMissing || !changed) && attempt + 1 < maxAttempts;
      
      if (shouldRetry) {
        const nextDelay = Math.min(baseDelay * METADATA_POLL_BACKOFF, METADATA_POLL_MAX_DELAY);
        scheduleMetadataRefresh(sid, apiFunc, updateFunc, {
          attempt: attempt + 1,
          maxAttempts,
          delay: nextDelay,
          prevState: obj,
        });
      }
    } catch (err) {
      console.warn('[chats] metadata refresh failed', err);
      if (attempt + 1 < maxAttempts) {
        const retryDelay = Math.min(baseDelay * METADATA_POLL_BACKOFF, METADATA_POLL_MAX_DELAY);
        scheduleMetadataRefresh(sid, apiFunc, updateFunc, {
          attempt: attempt + 1,
          maxAttempts,
          delay: retryDelay,
          prevState: opts.prevState,
        });
      }
    }
  }, baseDelay);
  
  metadataTimers[sid].push(timer);
}

function clearMetadataRefresh(sid) {
  if (!sid) return;
  const timers = metadataTimers[sid];
  if (!timers || !timers.length) return;
  timers.forEach((t) => clearTimeout(t));
  delete metadataTimers[sid];
}

function clearAllMetadataRefresh() {
  Object.keys(metadataTimers).forEach(clearMetadataRefresh);
}

// Export as global
window.ChatsMetadata = {
  scheduleMetadataRefresh,
  clearMetadataRefresh,
  clearAllMetadataRefresh,
  METADATA_POLL_INITIAL_DELAY,
  METADATA_POLL_BACKOFF,
  METADATA_POLL_MAX_DELAY,
  METADATA_POLL_MAX_ATTEMPTS,
};
