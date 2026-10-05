import { openCamera, stopStream, streamFrames, streamFramesHttp } from './camera.js';
import { initCaptions } from './captions.js';
import { createGoogleSttSource } from './googleSttSource.js';
import { authFetch, buildSseUrl, getAuthHeaders } from './supabase-auth.js';

document.addEventListener('DOMContentLoaded', () => {
  const videoEl = document.getElementById('video');
  const captionsEl = document.getElementById('captions');

  const startBtn = document.getElementById('start');
  const pauseBtn = document.getElementById('pause');
  const resumeBtn = document.getElementById('resume');
  const stopBtn = document.getElementById('stop');
  const clearBtn = document.getElementById('clear');
  const saveBtn = document.getElementById('save');
  const newChatBtn = document.getElementById('new-chat');

  const modeAslBtn = document.getElementById('mode-asl');
  const modeSpeechBtn = document.getElementById('mode-speech');
  const modeLetterBtn = document.getElementById('mode-letter');

  const sizeSlider = document.getElementById('captionSize');
  const sizeLabel = document.getElementById('captionSizeLabel');

  const modal = document.getElementById('consent-modal');
  const acceptBtn = document.getElementById('consent-accept');
  const declineBtn = document.getElementById('consent-decline');

  const uiStatusText = document.getElementById('ui-status-text');

  const historyListEl = document.getElementById('history-list');
  const historyEmptyEl = document.getElementById('history-empty');
  const historyMenuButton = document.getElementById('history-menu-button');
  const historyMenu = document.getElementById('history-menu');
  const historyClearAllBtn = document.getElementById('history-clear-all');
  const historyItemTemplate = document.getElementById('history-item-template');

  const authRequiredBanner = document.getElementById('auth-required-banner');

  // ---- helpers ----
  const pickEl = (...ids) => {
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) return el;
    }
    return null;
  };

  // Prevent "pointerup + click" from toggling twice (used for TTS only)
  function wireTap(el, fn) {
    if (!el) return;
    let last = 0;
    const handler = (e) => {
      const now = Date.now();
      if (now - last < 350) {
        e.preventDefault?.();
        e.stopPropagation?.();
        return;
      }
      last = now;
      fn(e);
    };
    el.addEventListener('pointerup', handler);
    el.addEventListener('click', handler);
  }

  // ---- Language selection ----
  const langFromSel = pickEl('lang-from', 'language-from', 'langFrom', 'fromLang');
  const langToSel = pickEl('lang-to', 'language-to', 'langTo', 'toLang');
  const langModeHint = document.getElementById('lang-mode-hint');

  // ---- Speaker toggle button ----
  const ttsToggleBtn = pickEl('tts-toggle', 'speaker-toggle', 'ttsToggle', 'speakerToggle');

  const VOICE_LANGS = [
    { key: 'en', label: 'English', stt: 'en-US' },
    { key: 'es', label: 'Spanish', stt: 'es-ES' },
    { key: 'ar', label: 'Arabic', stt: 'ar-SA' },
    { key: 'hy', label: 'Armenian', stt: 'hy-AM' },
  ];
  const VOICE_LANG_KEYS = new Set(VOICE_LANGS.map(l => l.key));

  const captions = initCaptions(captionsEl);

  const appState = {
    mode: 'speech',
    running: false,
    paused: false,
    micStream: null,
    camStream: null,
    abort: null,
    busy: false,
    switching: false,
    speechSource: null,
    sessionStartedAt: null,
    voiceFromLang: 'en',
    voiceToLang: 'en',
    letterToLang: 'en',
    aslToLang: 'en',
  };

  // ── ASL backend state (port 8000) ──
  const aslState = {
    eventSource: null,   // SSE EventSource
    mjpegImg: null,      // <img> element for MJPEG feed
    connected: false,
    frameStreamer: null,  // streamFramesHttp() handle
    cameraStream: null,  // getUserMedia stream (remote camera mode)
  };

  // ── Letter backend state (port 8002) ──
  const letterState = {
    eventSource: null,   // SSE EventSource
    connected: false,
    frameStreamer: null,  // streamFramesHttp() handle
    cameraStream: null,  // getUserMedia stream
    awaitingFreshStart: false,
    lastAcceptedWord: '', // backend correction floor only
    liveCaptionWord: '',  // current live Letter word buffer
    lastRenderedDetectedLetter: '',
    repeatUnlockTimer: null,
    _pendingDetectedLetter: '',
    _pendingDetectedCount: 0,
    debugEnabled: true,
  };

  let dirty = false;
  const SESSION_DIAG_ENABLED = false;
  const sessionState = {
    startedAt: null,
    committedText: '',
    liveComposition: { source: null, text: '' },
    usedModes: new Set(),
    generation: 0,
  };
  const speechState = {
    finalSeq: 0,
    pendingFinals: new Set(),
  };

  // -----------------------------
  // Auth state (UI is handled by index.html)
  // -----------------------------
  const authState = {
    checked: true,
    user: window.__AUTH_USER__ || null, // index.html sets this
    lastToastAt: 0,
  };
  let transientStatusToken = 0;

  function isAuthDisabled() {
    return !window.APP_CONFIG?.SUPABASE_URL || !window.APP_CONFIG?.SUPABASE_ANON_KEY;
  }

  function isAuthed() {
    if (isAuthDisabled()) return true;
    return !!authState.user;
  }

  function syncAuthFromWindow() {
    authState.user = window.__AUTH_USER__ || null;
  }

  window.addEventListener('auth:changed', (ev) => {
    authState.user = ev?.detail?.user || null;
    onAuthScopeMaybeChanged();
    updateModeUI();
    setStatusForState();
  });

  window.addEventListener('auth:expired', () => {
    authState.user = null;
    setAuthRequiredBanner(true);
    showAuthToast('Session expired. Please sign in again.');
    if (appState.running) stopPipeline({ suppressFeedback: true });
  });

  function setAuthRequiredBanner(show) {
    if (isAuthDisabled()) show = false;
    if (window.AUTH?.showRequiredBanner && window.AUTH?.hideRequiredBanner) {
      show ? window.AUTH.showRequiredBanner() : window.AUTH.hideRequiredBanner();
      return;
    }
    if (!authRequiredBanner) return;
    authRequiredBanner.classList.toggle('hidden', !show);
  }

  function showAuthToast(msg, ms = 2500) {
    const now = Date.now();
    if (now - authState.lastToastAt < 400) return;
    authState.lastToastAt = now;

    if (uiStatusText && msg) {
      uiStatusText.textContent = msg;
      setTimeout(() => {
        if (uiStatusText.textContent === msg) setStatusForState();
      }, ms);
    } else {
      captions.setStatus(msg);
      setTimeout(() => setStatusForState(), ms);
    }
  }

  function showTransientStatus(msg, ms = 2200) {
    const token = ++transientStatusToken;

    if (msg) {
      captions.setStatus(msg);
      if (uiStatusText) uiStatusText.textContent = msg;
    }

    setTimeout(() => {
      if (token !== transientStatusToken) return;
      setStatusForState();
    }, ms);
  }

  function authLoginRedirect() {
    // Auth lives in stt_backend → use BACKEND_HTTP_BASE (port 8001)
    const backend =
      window.AUTH?.backend ||
      window.APP_CONFIG?.BACKEND ||
      document.querySelector('meta[name="backend-base"]')?.content ||
      BACKEND_HTTP_BASE;

    // Open the sign-in menu instead of redirecting (Supabase handles auth client-side)
    const profileBtn = document.getElementById('profile-button');
    if (profileBtn) profileBtn.click();
  }

  async function ensureAuthOrPrompt() {
    syncAuthFromWindow();

    if (isAuthed()) return true;

    // try a refresh via index.html helper (if available)
    try {
      if (window.AUTH?.refresh) await window.AUTH.refresh();
    } catch { }

    syncAuthFromWindow();
    if (isAuthed()) return true;

    setAuthRequiredBanner(true);
    showAuthToast('Please sign in to continue.');
    return false;
  }

  // -----------------------------
  // Per-user localStorage keys
  // -----------------------------
  const KEY_BASE = {
    HISTORY: 'aslHistorySessions',
    TTS: 'ttsEnabled',
    LANG_FROM: 'voiceFromLang',
    LANG_TO: 'voiceToLang',
    LETTER_LANG_TO: 'letterToLang',
    ASL_LANG_TO: 'aslToLang',
    CAPTION_SIZE: 'captionSize',
  };

  function userScope() {
    const sub = authState.user?.sub || window.__AUTH_USER__?.sub;
    return sub ? String(sub) : 'anon';
  }

  function scopedKey(base) {
    return `${base}:${userScope()}`;
  }

  function migrateIfMissing(oldKey, newKey) {
    try {
      const hasNew = localStorage.getItem(newKey);
      if (hasNew != null) return;
      const old = localStorage.getItem(oldKey);
      if (old == null) return;
      localStorage.setItem(newKey, old);
    } catch { }
  }

  function onAuthScopeMaybeChanged() {
    const scope = userScope();
    if (scope !== 'anon') {
      migrateIfMissing(KEY_BASE.HISTORY, scopedKey(KEY_BASE.HISTORY));
      migrateIfMissing(KEY_BASE.TTS, scopedKey(KEY_BASE.TTS));
      migrateIfMissing(KEY_BASE.LANG_FROM, scopedKey(KEY_BASE.LANG_FROM));
      migrateIfMissing(KEY_BASE.LANG_TO, scopedKey(KEY_BASE.LANG_TO));
      migrateIfMissing(KEY_BASE.CAPTION_SIZE, scopedKey(KEY_BASE.CAPTION_SIZE));
    }

    loadSavedVoiceLangs();
    applyCaptionSizeFromStorage();
    setTtsEnabled(loadTtsEnabled(), false);
    updateTtsButtonUI();

    renderHistory(loadHistory());
    if (isAuthed()) {
      loadHistoryAsync().then(merged => {
        renderHistory(merged);
        migrateLocalToBackend(merged.filter(e => !e._backendId));
      });
    }
  }

  function langLabel(key) {
    if (key === 'asl') return 'ASL';
    if (key === 'letter') return 'Letter';
    const found = VOICE_LANGS.find(l => l.key === key);
    return found ? found.label : String(key || '');
  }

  function sttCodeForVoiceLang(key) {
    const found = VOICE_LANGS.find(l => l.key === key);
    return found?.stt || 'en-US';
  }

  // -----------------------------
  // Backend endpoints
  // -----------------------------
  function normalizeBaseUrl(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    if (raw === '/') return '/';
    return raw.replace(/\/+$/, '');
  }

  function readPort(value, fallback) {
    const parsed = Number.parseInt(String(value ?? ''), 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
  }

  function deriveBackendBase(defaultPort, portOverride) {
    const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
    const host = window.location.hostname || '127.0.0.1';
    return `${protocol}//${host}:${readPort(portOverride, defaultPort)}`;
  }

  function joinServiceUrl(base, path) {
    const normalizedBase = normalizeBaseUrl(base);
    const normalizedPath = `/${String(path || '').replace(/^\/+/, '')}`;
    if (!normalizedBase || normalizedBase === '/') return normalizedPath;
    return `${normalizedBase}${normalizedPath}`;
  }

  function currentReturnPath() {
    return `${window.location.pathname || '/'}${window.location.search || ''}${window.location.hash || ''}`;
  }

  function deriveBackendWsUrl(base, explicitWs) {
    const configuredWs = normalizeBaseUrl(explicitWs);
    if (configuredWs) {
      try {
        const resolved = new URL(configuredWs, window.location.href);
        if (resolved.protocol === 'https:') resolved.protocol = 'wss:';
        else if (resolved.protocol === 'http:') resolved.protocol = 'ws:';
        return resolved.toString().replace(/\/+$/, '');
      } catch {
        return configuredWs.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:');
      }
    }

    const resolvedBase = new URL(normalizeBaseUrl(base) || '/', window.location.href);
    resolvedBase.protocol = resolvedBase.protocol === 'https:' ? 'wss:' : 'ws:';
    resolvedBase.pathname = joinServiceUrl(resolvedBase.pathname || '/', 'ws/stt');
    resolvedBase.search = '';
    resolvedBase.hash = '';
    return resolvedBase.toString();
  }

  // Voice backend (STT, TTS, translate, auth) -> same host by default, port 8001
  const BACKEND_HTTP_BASE = normalizeBaseUrl(
    window.APP_CONFIG?.BACKEND ||
    document.querySelector('meta[name="backend-base"]')?.content ||
    document.body.dataset.backendBase ||
    deriveBackendBase(8001, window.APP_CONFIG?.VOICE_PORT)
  );
  window.BACKEND_HTTP_BASE = BACKEND_HTTP_BASE;

  const BACKEND_WS_URL = (() => {
    return deriveBackendWsUrl(
      BACKEND_HTTP_BASE,
      window.APP_CONFIG?.BACKEND_WS_URL || window.BACKEND_WS_URL
    );
  })();
  window.BACKEND_WS_URL = BACKEND_WS_URL;

  const TRANSLATE_URL = joinServiceUrl(BACKEND_HTTP_BASE, '/api/translate');
  const TTS_URL = joinServiceUrl(BACKEND_HTTP_BASE, '/api/tts');
  const HEALTH_URL = joinServiceUrl(BACKEND_HTTP_BASE, '/health');
  const TRANSCRIPTS_URL = joinServiceUrl(BACKEND_HTTP_BASE, '/api/transcripts');

  // ASL backend (inference, camera, SSE) -> same host by default, port 8000
  const ASL_API = normalizeBaseUrl(
    window.APP_CONFIG?.ASL_BACKEND ||
    document.querySelector('meta[name="asl-backend-base"]')?.content ||
    document.body.dataset.aslBackendBase ||
    deriveBackendBase(8000, window.APP_CONFIG?.ASL_PORT)
  );

  // Same-origin proxy/cloud mode: ASL_API is either a relative path
  // (e.g. "/asl-api") or an absolute URL whose origin matches the page's
  // origin (e.g. https://public.site/asl-api fronted by tunnel_proxy.py).
  // In both cases we prefer WebSocket transport and browser-native preview.
  function isSameOriginAslApi(value) {
    if (typeof value !== 'string' || value === '') return false;
    if (value.startsWith('/')) return true;
    try {
      return new URL(value, window.location.href).origin === window.location.origin;
    } catch {
      return false;
    }
  }
  const ASL_IS_PROXY = isSameOriginAslApi(ASL_API);

  // Build an absolute ws(s):// URL for an ASL path regardless of whether
  // ASL_API is relative (proxy) or absolute (direct per-port).
  function joinServiceWs(base, path) {
    const httpUrl = joinServiceUrl(base, path);
    try {
      const resolved = new URL(httpUrl, window.location.href);
      resolved.protocol = resolved.protocol === 'https:' ? 'wss:' : 'ws:';
      return resolved.toString();
    } catch {
      return httpUrl;
    }
  }

  // Resolve on successful 'open', reject on error/close-before-open or timeout.
  // The resulting socket is handed off to streamFrames() on success.
  function openWsWithTimeout(url, timeoutMs) {
    return new Promise((resolve, reject) => {
      let settled = false;
      let ws;
      try {
        ws = new WebSocket(url);
      } catch (err) {
        reject(err);
        return;
      }
      ws.binaryType = 'blob';
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        try { ws.close(); } catch { }
        reject(new Error('ws open timeout'));
      }, timeoutMs);
      ws.addEventListener('open', () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(ws);
      });
      ws.addEventListener('error', () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        try { ws.close(); } catch { }
        reject(new Error('ws error'));
      });
      ws.addEventListener('close', () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(new Error('ws closed before open'));
      });
    });
  }

  // Letter backend (letter recognition, SSE) -> same host by default, port 8002
  const LETTER_API = normalizeBaseUrl(
    window.APP_CONFIG?.LETTER_BACKEND ||
    document.querySelector('meta[name="letter-backend-base"]')?.content ||
    document.body.dataset.letterBackendBase ||
    deriveBackendBase(8002, window.APP_CONFIG?.LETTER_PORT)
  );

  // ---- HISTORY ----
  let selectedHistoryId = null;
  let lastHistoryFingerprint = '';

  function sessionDebug(message, details) {
    if (!SESSION_DIAG_ENABLED) return;
    if (details === undefined) console.warn('[SESSION-DIAG]', message);
    else console.warn('[SESSION-DIAG]', message, details);
  }

  function normalizeSessionSegment(text) {
    return String(text ?? '').replace(/\s+/g, ' ').trim();
  }

  function normalizeCommittedSessionText(text) {
    return String(text ?? '').replace(/\r\n/g, '\n').trim();
  }

  function joinSessionText(baseText, nextText) {
    const base = normalizeCommittedSessionText(baseText);
    const next = normalizeSessionSegment(nextText);

    if (!next) return base;
    if (!base) return next;
    if (/[([{'"-]$/.test(base) || /^[,.;:!?)]/.test(next)) return `${base}${next}`;
    return `${base} ${next}`;
  }

  function setSessionStartedAt(value) {
    sessionState.startedAt = value || null;
    appState.sessionStartedAt = sessionState.startedAt;
  }

  function ensureSessionStartedAt() {
    if (!sessionState.startedAt) setSessionStartedAt(new Date().toISOString());
    return sessionState.startedAt;
  }

  function hasCommittedSessionText() {
    return !!normalizeCommittedSessionText(sessionState.committedText);
  }

  function hasLiveComposition() {
    return !!normalizeSessionSegment(sessionState.liveComposition.text);
  }

  function hasSessionDisplayText() {
    return !!normalizeCommittedSessionText(getRenderedSessionText());
  }

  function clearSelectedHistoryMarker() {
    if (selectedHistoryId == null) return;
    selectedHistoryId = null;
    renderHistory(loadHistory());
  }

  function recordSessionMode(source) {
    if (source === 'asl' || source === 'letter' || source === 'speech') {
      sessionState.usedModes.add(source);
    }
  }

  function getRenderedSessionText() {
    const committedText = normalizeCommittedSessionText(sessionState.committedText);
    const liveText = normalizeSessionSegment(sessionState.liveComposition.text);
    return liveText ? joinSessionText(committedText, liveText) : committedText;
  }

  function renderSharedSession() {
    captions.setText(getRenderedSessionText());
    sessionDebug('render shared session', {
      mode: appState.mode,
      committedText: sessionState.committedText,
      liveComposition: sessionState.liveComposition,
    });
  }

  function setLiveComposition(source, text, {
    markDirty = true,
    reason = 'update',
  } = {}) {
    const normalizedText = normalizeSessionSegment(text);
    const prevSource = sessionState.liveComposition.source;
    const prevText = sessionState.liveComposition.text;

    if (prevSource === source && prevText === normalizedText) return false;

    sessionState.liveComposition = normalizedText
      ? { source, text: normalizedText }
      : { source: null, text: '' };

    if (normalizedText) {
      clearSelectedHistoryMarker();
      if (markDirty) dirty = true;
    } else if (!hasCommittedSessionText()) {
      dirty = false;
    }

    renderSharedSession();
    sessionDebug('live composition updated', {
      reason,
      mode: appState.mode,
      liveComposition: sessionState.liveComposition,
      committedText: sessionState.committedText,
    });
    return true;
  }

  function clearLiveComposition(source = null, reason = 'clear') {
    if (source && sessionState.liveComposition.source !== source) return false;
    return setLiveComposition(null, '', { markDirty: false, reason });
  }

  function clearVoiceInterimLiveText(reason = 'voice-clear') {
    const hadSpeechLive = sessionState.liveComposition.source === 'speech' && hasLiveComposition();
    if (!hadSpeechLive) return false;

    clearLiveComposition('speech', reason);
    sessionDebug('voice live cleared', {
      reason,
      mode: appState.mode,
      committedText: sessionState.committedText,
    });
    return true;
  }

  function clearVoiceLiveTextIfMatches(expectedText, reason = 'voice-clear-match') {
    const expected = normalizeSessionSegment(expectedText);
    if (sessionState.liveComposition.source !== 'speech') return false;
    if (expected && normalizeSessionSegment(sessionState.liveComposition.text) !== expected) return false;
    return clearVoiceInterimLiveText(reason);
  }

  async function waitForPendingSpeechFinals(timeoutMs = 2500) {
    if (!speechState.pendingFinals.size) return;
    await Promise.race([
      Promise.allSettled([...speechState.pendingFinals]),
      new Promise((resolve) => setTimeout(resolve, timeoutMs)),
    ]);
  }

  function appendCommittedSessionText(source, text, {
    markDirty = true,
    reason = 'append',
    trackMode = true,
  } = {}) {
    const clean = normalizeSessionSegment(text);
    if (!clean) return '';

    ensureSessionStartedAt();
    if (trackMode) recordSessionMode(source);
    clearSelectedHistoryMarker();

    sessionState.committedText = joinSessionText(sessionState.committedText, clean);
    if (markDirty) dirty = true;

    renderSharedSession();
    sessionDebug('session commit appended', {
      source,
      reason,
      committedText: sessionState.committedText,
      liveComposition: sessionState.liveComposition.text,
    });
    return sessionState.committedText;
  }

  function flushPendingLetterLiveText(reason = 'letter-flush') {
    const liveText = normalizeSessionSegment(_currentLetterCaptionText());
    if (!liveText) return '';

    appendCommittedSessionText('letter', liveText, { reason });
    _clearLetterCaptionState({ render: false });
    sessionDebug('letter live flushed', {
      reason,
      committedText: sessionState.committedText,
    });
    return liveText;
  }

  function getPersistedSessionText() {
    if (sessionState.liveComposition.source === 'letter' && hasLiveComposition()) {
      return joinSessionText(sessionState.committedText, sessionState.liveComposition.text);
    }
    return normalizeCommittedSessionText(sessionState.committedText);
  }

  function deriveHistoryMode() {
    const modes = [...sessionState.usedModes];
    if (!modes.length) return appState.mode;
    return modes.length === 1 ? modes[0] : 'mixed';
  }

  function modesFromHistoryMode(mode) {
    if (mode === 'mixed') return ['asl', 'letter', 'speech'];
    if (mode === 'asl' || mode === 'letter' || mode === 'speech') return [mode];
    return [];
  }

  function cancelPendingSessionAsyncWork(reason = 'cancel') {
    speechState.finalSeq++;
    abortInFlight();
    sessionDebug('session async work canceled', { reason });
  }

  function replaceSessionFromHistory(item) {
    cancelPendingSessionAsyncWork('history-replace');
    sessionState.generation++;
    sessionState.committedText = normalizeCommittedSessionText(item?.text);
    sessionState.liveComposition = { source: null, text: '' };
    sessionState.usedModes = new Set(modesFromHistoryMode(item?.mode));
    setSessionStartedAt(item?.startedAt || new Date().toISOString());
    dirty = false;
    selectedHistoryId = item?.id || null;
    renderSharedSession();
    sessionDebug('history session replaced active session', {
      selectedHistoryId,
      mode: item?.mode,
      committedText: sessionState.committedText,
    });
  }

  function resetSharedSession(reason = 'reset') {
    cancelPendingSessionAsyncWork(reason);
    sessionState.generation++;
    sessionState.committedText = '';
    sessionState.liveComposition = { source: null, text: '' };
    sessionState.usedModes = new Set();
    setSessionStartedAt(null);
    dirty = false;
    selectedHistoryId = null;
    renderSharedSession();
    renderHistory(loadHistory());
    sessionDebug('shared session reset', { reason });
  }

  function loadHistory() {
    try { return JSON.parse(localStorage.getItem(scopedKey(KEY_BASE.HISTORY)) || '[]'); }
    catch { return []; }
  }

  function saveHistory(list) {
    try { localStorage.setItem(scopedKey(KEY_BASE.HISTORY), JSON.stringify(list)); }
    catch { }
  }

  // -- Backend transcript sync helpers (Task 4) --

  function backendRowToEntry(row) {
    return {
      id: row.client_history_id,
      mode: row.mode || 'asl',
      startedAt: row.started_at || row.created_at || '',
      text: row.text || '',
      _backendId: row.id,
    };
  }

  function entryToBackendPayload(entry) {
    return {
      client_history_id: entry.id,
      mode: entry.mode || 'asl',
      started_at: entry.startedAt || null,
      text: entry.text || '',
    };
  }

  // Once the backend reports transcript storage is unavailable (503),
  // skip further calls so we don't spam the server every save/delete.
  let _transcriptBackendDisabled = false;
  function _noteTranscriptResponse(res) {
    if (res && res.status === 503) {
      _transcriptBackendDisabled = true;
      try { console.warn('Transcript storage unavailable; disabling backend sync.'); } catch {}
    }
  }

  async function fetchTranscripts() {
    if (!isAuthed() || _transcriptBackendDisabled) return null;
    try {
      const res = await authFetch(TRANSCRIPTS_URL + '?limit=20', {
        method: 'GET',
        cache: 'no-store',
      });
      _noteTranscriptResponse(res);
      if (!res.ok) return null;
      const rows = await res.json();
      return Array.isArray(rows) ? rows.map(backendRowToEntry) : null;
    } catch { return null; }
  }

  async function postTranscript(entry) {
    if (!isAuthed() || _transcriptBackendDisabled) return null;
    try {
      const res = await authFetch(TRANSCRIPTS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(entryToBackendPayload(entry)),
      });
      _noteTranscriptResponse(res);
      if (!res.ok) return null;
      const row = await res.json();
      return row?.id ?? null;
    } catch { return null; }
  }

  async function deleteTranscript(backendId) {
    if (!isAuthed() || !backendId || _transcriptBackendDisabled) return false;
    try {
      const res = await authFetch(`${TRANSCRIPTS_URL}/${backendId}`, { method: 'DELETE' });
      _noteTranscriptResponse(res);
      return res.ok;
    } catch { return false; }
  }

  async function deleteAllTranscripts() {
    if (!isAuthed() || _transcriptBackendDisabled) return false;
    try {
      const res = await authFetch(TRANSCRIPTS_URL, { method: 'DELETE' });
      _noteTranscriptResponse(res);
      return res.ok;
    } catch { return false; }
  }

  async function loadHistoryAsync() {
    const local = loadHistory();
    if (!isAuthed()) return local;
    const backend = await fetchTranscripts();
    if (!backend) return local;
    const backendClientIds = new Set(backend.map(e => e.id));
    const localFiltered = local.filter(e => !e._backendId || backendClientIds.has(e.id));
    const seen = new Set();
    const merged = [];
    for (const e of backend) { if (!seen.has(e.id)) { seen.add(e.id); merged.push(e); } }
    for (const e of localFiltered) { if (!seen.has(e.id)) { seen.add(e.id); merged.push(e); } }
    merged.sort((a, b) => (b.startedAt || '').localeCompare(a.startedAt || ''));
    if (merged.length > 20) merged.length = 20;
    saveHistory(merged);
    return merged;
  }

  function migrateLocalToBackend(entries) {
    if (!isAuthed() || !entries.length) return;
    for (const entry of entries) {
      postTranscript(entry).then(backendId => {
        if (backendId != null) {
          const refreshed = loadHistory();
          const idx = refreshed.findIndex(e => e.id === entry.id);
          if (idx >= 0) { refreshed[idx]._backendId = backendId; saveHistory(refreshed); }
        }
      });
    }
  }

  // -- End backend transcript sync helpers --

  function formatTimestamp(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const now = new Date();

    const isToday = d.toDateString() === now.toDateString();
    const yest = new Date();
    yest.setDate(now.getDate() - 1);
    const isYesterday = d.toDateString() === yest.toDateString();

    const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    if (isToday) return `Today, ${time}`;
    if (isYesterday) return `Yesterday, ${time}`;
    return `${d.toLocaleDateString()} ${time}`;
  }

  function closeAllHistoryItemMenus(except = null) {
    document.querySelectorAll('.history-item-menu').forEach(menu => {
      if (menu !== except) menu.classList.add('hidden');
    });
  }

  function closeAllMenus(except = null) {
    if (historyMenu && historyMenu !== except) historyMenu.classList.add('hidden');
    closeAllHistoryItemMenus(except);
  }

  async function viewHistoryEntry(item) {
    if (!item || !item.text) return;

    if (hasSessionDisplayText()) {
      const confirmed = confirm('Replace the current session with this history item?');
      if (!confirmed) {
        sessionDebug('history replace canceled', {
          committedText: sessionState.committedText,
          liveComposition: sessionState.liveComposition,
        });
        return;
      }
    }

    if (appState.mode === 'letter') flushPendingLetterLiveText('history-replace');
    if (appState.mode === 'speech') clearVoiceInterimLiveText('history-replace');

    if (appState.running) await stopPipeline({ suppressFeedback: true });

    replaceSessionFromHistory(item);
    appState.running = false;
    appState.paused = false;
    setStatusForState();
    renderHistory(loadHistory());
  }

  function renderHistory(list) {
    if (!historyListEl || !historyEmptyEl || !historyItemTemplate) return;

    const sessions = list || loadHistory();
    historyListEl.innerHTML = '';

    if (!sessions.length) {
      historyEmptyEl.style.display = 'block';
      return;
    }
    historyEmptyEl.style.display = 'none';

    const tplRoot = historyItemTemplate.content.firstElementChild;
    if (!tplRoot) return;

    sessions.forEach((item) => {
      const root = tplRoot.cloneNode(true);
      root.dataset.id = String(item.id);

      root.classList.remove('bg-white/5', 'bg-white/10', 'ring-1', 'ring-white/20');
      if (item.id === selectedHistoryId) root.classList.add('bg-white/10', 'ring-1', 'ring-white/20');
      else root.classList.add('bg-white/5');

      const snippetEl = root.querySelector('.history-snippet');
      const metaEl = root.querySelector('.history-meta');
      const menuBtn = root.querySelector('.history-item-menu-button');
      const menuEl = root.querySelector('.history-item-menu');
      const deleteBtn = root.querySelector('.history-item-delete');

      const firstLine = (item.text || '').split(/\r?\n/).find((l) => l.trim()) || '(empty session)';
      const preview = firstLine.length > 80 ? firstLine.slice(0, 77) + '...' : firstLine;

      if (snippetEl) snippetEl.textContent = preview;
      if (metaEl) {
        const modeLabel =
          item.mode === 'asl' ? 'ASL' :
          item.mode === 'letter' ? 'Letter' :
          item.mode === 'mixed' ? 'Mixed' :
          'Voice';
        metaEl.textContent = `${modeLabel} • ${formatTimestamp(item.startedAt)}`;
      }

      root.addEventListener('click', (e) => {
        const target = e.target;
        if (target instanceof Element &&
          (target.closest('.history-item-menu-button') || target.closest('.history-item-menu'))) return;

        viewHistoryEntry(item).catch((err) => console.error(err));
        closeAllMenus();
      });

      if (menuBtn && menuEl) {
        menuBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          const isOpen = !menuEl.classList.contains('hidden');
          closeAllMenus();
          if (!isOpen) menuEl.classList.remove('hidden');
        });
      }

      if (deleteBtn && menuEl) {
        deleteBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          const current = loadHistory();
          const filtered = current.filter(sess => sess.id !== item.id);
          saveHistory(filtered);
          if (isAuthed() && item._backendId) {
            deleteTranscript(item._backendId);
          }

          if (selectedHistoryId === item.id) {
            selectedHistoryId = null;
            renderHistory(filtered);
            renderSharedSession();
            closeAllMenus();
            return;
          }

          renderHistory(filtered);
          closeAllMenus();
        });
      }

      historyListEl.appendChild(root);
    });

    closeAllHistoryItemMenus();
  }

  function addHistoryEntry(text) {
    const clean = (text || '').trim();
    if (!clean) return false;

    const historyMode = deriveHistoryMode();
    const fp = `${historyMode}|${sessionState.startedAt || ''}|${clean.length}|${clean.slice(0, 64)}`;
    if (fp === lastHistoryFingerprint) return false;
    lastHistoryFingerprint = fp;

    const list = loadHistory();
    const entry = {
      id: `${Date.now()}_${Math.random().toString(16).slice(2)}`,
      mode: historyMode,
      startedAt: sessionState.startedAt || new Date().toISOString(),
      text: clean,
    };
    list.unshift(entry);

    const MAX_SESSIONS = 20;
    if (list.length > MAX_SESSIONS) list.length = MAX_SESSIONS;

    saveHistory(list);
    if (isAuthed()) {
      postTranscript(entry).then(backendId => {
        if (backendId != null) {
          const refreshed = loadHistory();
          const idx = refreshed.findIndex(e => e.id === entry.id);
          if (idx >= 0) { refreshed[idx]._backendId = backendId; saveHistory(refreshed); }
        }
      });
    }
    renderHistory(list);
    return true;
  }

  function playFeedback() {
    try {
      navigator.vibrate?.(150);
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'square';
      osc.frequency.setValueAtTime(600, ctx.currentTime);
      gain.gain.setValueAtTime(0.05, ctx.currentTime);
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.15);
      setTimeout(() => { try { ctx.close?.(); } catch { } }, 300);
    } catch { }
  }

  // ---------------------------
  // Language + translation logic
  // ---------------------------
  function loadSavedVoiceLangs() {
    const savedFrom = (localStorage.getItem(scopedKey(KEY_BASE.LANG_FROM)) || '').trim();
    const savedTo = (localStorage.getItem(scopedKey(KEY_BASE.LANG_TO)) || '').trim();
    const savedLetterTo = (localStorage.getItem(scopedKey(KEY_BASE.LETTER_LANG_TO)) || '').trim();
    const savedAslTo = (localStorage.getItem(scopedKey(KEY_BASE.ASL_LANG_TO)) || '').trim();

    appState.voiceFromLang = VOICE_LANG_KEYS.has(savedFrom) ? savedFrom : 'en';
    appState.voiceToLang = VOICE_LANG_KEYS.has(savedTo) ? savedTo : 'en';
    appState.letterToLang = VOICE_LANG_KEYS.has(savedLetterTo) ? savedLetterTo : 'en';
    appState.aslToLang = VOICE_LANG_KEYS.has(savedAslTo) ? savedAslTo : 'en';
  }

  function persistVoiceLangs() {
    try {
      localStorage.setItem(scopedKey(KEY_BASE.LANG_FROM), appState.voiceFromLang);
      localStorage.setItem(scopedKey(KEY_BASE.LANG_TO), appState.voiceToLang);
      localStorage.setItem(scopedKey(KEY_BASE.LETTER_LANG_TO), appState.letterToLang);
      localStorage.setItem(scopedKey(KEY_BASE.ASL_LANG_TO), appState.aslToLang);
    } catch { }
  }

  function getEffectiveLangs() {
    if (appState.mode === 'asl') return { from: 'asl', to: appState.aslToLang };
    if (appState.mode === 'letter') return { from: 'en', to: appState.letterToLang };
    return { from: appState.voiceFromLang, to: appState.voiceToLang };
  }

  function updateLanguageUI() {
    if (!langFromSel || !langToSel) return;

    const { from, to } = getEffectiveLangs();
    const lockNow = appState.running || appState.busy;
    let modeHintText = '';

    if (appState.mode === 'asl') {
      langFromSel.disabled = true;
      langToSel.disabled = !!lockNow;
      langFromSel.value = 'asl';
      langToSel.value = to;
    } else if (appState.mode === 'letter') {
      langFromSel.disabled = true;
      langToSel.disabled = !!lockNow;
      langFromSel.value = 'letter';
      langToSel.value = to;
    } else {
      langFromSel.disabled = !!lockNow;
      langToSel.disabled = !!lockNow;
      langFromSel.value = from;
      langToSel.value = to;
    }

    if (langModeHint) {
      langModeHint.textContent = modeHintText;
      langModeHint.classList.toggle('hidden', !modeHintText);
    }
  }

  const _translateCache = new Map();
  const MAX_TRANSLATE_CACHE = 300;
  let _translateSeq = 0;

  function shouldTranslate() {
    if (appState.mode === 'asl') {
      const { to } = getEffectiveLangs();
      return !!to && to !== 'en';
    }
    if (appState.mode !== 'speech' && appState.mode !== 'letter') return false;
    const { from, to } = getEffectiveLangs();
    return !!from && !!to && from !== to;
  }

  function cacheSetBounded(key, val) {
    _translateCache.set(key, val);
    if (_translateCache.size > MAX_TRANSLATE_CACHE) {
      const firstKey = _translateCache.keys().next().value;
      if (firstKey !== undefined) _translateCache.delete(firstKey);
    }
  }

  async function translateViaBackend(text, fromKey, toKey) {
    const clean = (text || '').trim();
    if (!clean) return '';

    const cacheKey = `${fromKey}|${toKey}|${clean}`;
    if (_translateCache.has(cacheKey)) return _translateCache.get(cacheKey);

    if (!appState.abort) appState.abort = new AbortController();

    const res = await authFetch(TRANSLATE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: appState.abort.signal,
      body: JSON.stringify({
        text: clean,
        from_lang: fromKey,
        to_lang: toKey,
        include_source: false,
      }),
    });

    if (!res.ok) throw new Error(`translate ${res.status}`);

    const data = await res.json();
    const out = (data?.translatedText || '').trim() || clean;

    cacheSetBounded(cacheKey, out);
    return out;
  }

  async function backendHealthCheck(timeoutMs = 1800) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await authFetch(HEALTH_URL, {
        method: 'GET',
        signal: ctrl.signal,
        cache: 'no-store',
      });
      if (!res.ok) return { ok: false, status: res.status };
      const data = await res.json().catch(() => ({}));
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: e };
    } finally {
      clearTimeout(t);
    }
  }

  // ---- Caption size (scoped) ----
  function applyCaptionSizeFromStorage() {
    const savedSize = parseInt(localStorage.getItem(scopedKey(KEY_BASE.CAPTION_SIZE)) || '16', 10);
    if (sizeSlider) sizeSlider.value = savedSize;
    if (sizeLabel) sizeLabel.textContent = `${savedSize}pt`;
    captions.setTextSize(savedSize);
  }

  // ---- TTS ----
  const tts = {
    enabled: true,
    queue: [],
    playing: false,
    token: 0,
    audio: new Audio(),
    currentUrl: null,
    currentResolve: null,
    fetchAbort: null,
    lastQueued: '',
    lastSpoken: '',
  };

  function loadTtsEnabled() {
    const raw = (localStorage.getItem(scopedKey(KEY_BASE.TTS)) || '').trim().toLowerCase();
    if (raw === 'false' || raw === '0' || raw === 'off') return false;
    if (raw === 'true' || raw === '1' || raw === 'on') return true;
    return true;
  }

  function updateTtsButtonUI() {
    if (!ttsToggleBtn) return;
    ttsToggleBtn.classList.toggle('tts-off', !tts.enabled);
    ttsToggleBtn.setAttribute('aria-pressed', String(tts.enabled));
    ttsToggleBtn.setAttribute('title', tts.enabled ? 'Sound on' : 'Silent');

    const icon = ttsToggleBtn.querySelector('.material-symbols-outlined');
    if (icon) icon.textContent = tts.enabled ? 'volume_up' : 'volume_off';
  }

  function stopTtsNow() {
    tts.token++;
    tts.queue.length = 0;

    try { tts.fetchAbort?.abort(); } catch { }
    tts.fetchAbort = null;

    try {
      tts.audio.pause();
      tts.audio.currentTime = 0;
    } catch { }

    try { tts.audio.src = ''; } catch { }

    if (tts.currentUrl) {
      try { URL.revokeObjectURL(tts.currentUrl); } catch { }
      tts.currentUrl = null;
    }

    if (tts.currentResolve) {
      try { tts.currentResolve(); } catch { }
      tts.currentResolve = null;
    }

    tts.playing = false;
  }

  function setTtsEnabled(next, persist = true) {
    tts.enabled = !!next;
    if (persist) {
      try { localStorage.setItem(scopedKey(KEY_BASE.TTS), tts.enabled ? 'true' : 'false'); } catch { }
    }
    updateTtsButtonUI();
    if (!tts.enabled) stopTtsNow();
  }

  async function _playNextTtsIfAny() {
    if (!tts.enabled) return;
    if (tts.playing) return;
    if (!tts.queue.length) return;
    if (!appState.running || appState.paused) return;

    const item = tts.queue.shift();
    if (!item || !item.text) return;

    const myToken = ++tts.token;
    tts.playing = true;
    tts.lastSpoken = (item.text || '').trim();

    const ctrl = new AbortController();
    tts.fetchAbort = ctrl;

    try {
      const res = await authFetch(TTS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: ctrl.signal,
        body: JSON.stringify({ text: item.text, lang: item.lang }),
      });

      if (!res.ok) throw new Error(`tts ${res.status}`);

      const audioBytes = await res.arrayBuffer();
      if (myToken !== tts.token || !tts.enabled) return;

      try { tts.audio.pause(); } catch { }
      try { tts.audio.currentTime = 0; } catch { }

      if (tts.currentUrl) {
        try { URL.revokeObjectURL(tts.currentUrl); } catch { }
        tts.currentUrl = null;
      }

      const blob = new Blob([audioBytes], { type: 'audio/mpeg' });
      const url = URL.createObjectURL(blob);
      tts.currentUrl = url;

      tts.audio.src = url;
      tts.audio.preload = 'auto';

      await new Promise((resolve) => {
        tts.currentResolve = resolve;

        const done = () => {
          tts.audio.removeEventListener('ended', done);
          tts.audio.removeEventListener('error', done);
          if (tts.currentResolve === resolve) tts.currentResolve = null;
          resolve();
        };

        tts.audio.addEventListener('ended', done, { once: true });
        tts.audio.addEventListener('error', done, { once: true });

        tts.audio.play().catch(() => done());
      });

      if (tts.currentUrl) {
        try { URL.revokeObjectURL(tts.currentUrl); } catch { }
        tts.currentUrl = null;
      }
    } catch {
      // ignore
    } finally {
      if (myToken === tts.token) {
        tts.fetchAbort = null;
        tts.playing = false;
      }
      if (tts.enabled && appState.running && !appState.paused) {
        _playNextTtsIfAny().catch(() => { });
      }
    }
  }

  function enqueueTts(text, lang) {
    if (!tts.enabled) return;
    if (!appState.running || appState.paused) return;
    if (!shouldTranslate() && appState.mode !== 'asl') return;

    const clean = (text || '').trim();
    if (!clean) return;

    const MAX_QUEUE = 3;
    if (clean === tts.lastQueued) return;
    tts.lastQueued = clean;

    if (tts.queue.length >= MAX_QUEUE) {
      tts.queue.splice(0, tts.queue.length - (MAX_QUEUE - 1));
    }

    tts.queue.push({ text: clean, lang: lang || 'en' });
    _playNextTtsIfAny().catch(() => { });
  }

  function handleSpeechInterimTranscript(text) {
    if (appState.mode !== 'speech' || !appState.running || appState.paused) return;

    const clean = normalizeSessionSegment(text);
    if (!clean) {
      clearVoiceInterimLiveText('speech-interim-empty');
      return;
    }

    setLiveComposition('speech', clean, {
      markDirty: true,
      reason: 'speech-interim',
    });
  }

  async function handleSpeechFinalTranscript(text) {
    const clean = normalizeSessionSegment(text);
    if (!clean) return;

    const finalTask = (async () => {
      const cancelToken = speechState.finalSeq;
      const generation = sessionState.generation;
      const from = appState.voiceFromLang;
      const to = appState.voiceToLang;
      const shouldTranslateFinal = !!from && !!to && from !== to;

      setLiveComposition('speech', clean, {
        markDirty: true,
        reason: 'speech-final-pending',
      });

      const commitFinalText = (finalText) => {
        if (generation !== sessionState.generation) return false;
        if (cancelToken !== speechState.finalSeq) return false;

        clearVoiceLiveTextIfMatches(clean, 'speech-final-commit');
        appendCommittedSessionText('speech', finalText, {
          reason: 'speech-final',
        });

        if (shouldTranslateFinal) enqueueTts(finalText, to);
        return true;
      };

      if (!shouldTranslateFinal) {
        commitFinalText(clean);
        return;
      }

      const translateSeq = _translateSeq;
      try {
        const translated = await translateViaBackend(clean, from, to);
        if (translateSeq !== _translateSeq) return;
        commitFinalText(translated);
      } catch {
        if (translateSeq !== _translateSeq) return;
        commitFinalText(clean);
      }
    })();

    speechState.pendingFinals.add(finalTask);
    try {
      await finalTask;
    } finally {
      speechState.pendingFinals.delete(finalTask);
    }
  }

  function updateControls() {
    const lock = appState.busy;

    if (startBtn) startBtn.disabled = lock || appState.running;
    if (pauseBtn) pauseBtn.disabled = lock || !appState.running || appState.paused;
    if (resumeBtn) resumeBtn.disabled = lock || !appState.running || !appState.paused;
    if (stopBtn) stopBtn.disabled = lock || !appState.running;
    if (clearBtn) clearBtn.disabled = lock || (!appState.running && !hasSessionDisplayText());
    if (newChatBtn) newChatBtn.disabled = lock || (!appState.running && !hasSessionDisplayText());

    updateLanguageUI();
  }

  function updateModeUI() {
    const mode = appState.mode;
    if (modeAslBtn) modeAslBtn.classList.toggle('active', mode === 'asl');
    if (modeSpeechBtn) modeSpeechBtn.classList.toggle('active', mode === 'speech');
    if (modeLetterBtn) modeLetterBtn.classList.toggle('active', mode === 'letter');
    if (modeAslBtn) modeAslBtn.setAttribute('aria-pressed', String(mode === 'asl'));
    if (modeSpeechBtn) modeSpeechBtn.setAttribute('aria-pressed', String(mode === 'speech'));
    if (modeLetterBtn) modeLetterBtn.setAttribute('aria-pressed', String(mode === 'letter'));

    setAuthRequiredBanner(!isAuthed());
    updateLanguageUI();
  }

  function stopAllMedia() {
    try { appState.micStream?.getTracks().forEach(t => t.stop()); } catch { }
    // During an asl↔letter mode transition we keep the camera stream alive so
    // the preview doesn't flash black while the new pipeline acquires its own
    // handle. openCamera() in camera.js is idempotent — it will reuse the
    // already-attached MediaStream when its video tracks are still 'live'.
    if (appState.camStream && !appState._modeTransitionKeepCamera) {
      try { stopStream(appState.camStream, videoEl); } catch { }
      appState.camStream = null;
    }
    appState.micStream = null;
  }

  function abortInFlight() {
    try { appState.abort?.abort(); } catch { }
    appState.abort = null;
    _translateSeq++;
  }

  function setStatusForState() {
    const { from, to } = getEffectiveLangs();

    let captionStatus;
    if (!appState.running) captionStatus = '● Ready';
    else if (appState.paused) captionStatus = '■ Paused';
    else {
      if (appState.mode === 'speech') captionStatus = `▲ Listening (Voice: ${langLabel(from)})`;
      else if (appState.mode === 'letter') captionStatus = shouldTranslate()
        ? `▲ Capturing (Letter \u2192 ${langLabel(to)})`
        : '▲ Capturing (Letter)';
      else captionStatus = '▲ Capturing (ASL)';
    }
    captions.setStatus(captionStatus);

    if (uiStatusText) {
      let uiText;
      if (!appState.running) uiText = 'Ready • Press Start to begin';
      else if (appState.paused) uiText = 'Paused • Press Resume to continue';
      else {
        if (appState.mode === 'speech') {
          uiText = shouldTranslate()
            ? `Translating… (Voice: ${langLabel(from)} → ${langLabel(to)})`
            : `Listening… (Voice: ${langLabel(from)})`;
        } else if (appState.mode === 'letter') {
          uiText = shouldTranslate()
            ? `Recognizing… (Letter \u2192 ${langLabel(to)})`
            : 'Recognizing… (Letter \u2192 English)';
        } else {
          uiText = 'Translating… (ASL → English)';
        }
      }
      uiStatusText.textContent = uiText;
    }

    updateControls();
  }

  // ═══════════════════════════════════════════════════════
  //  SPEECH PIPELINE (Voice mode → port 8001)
  // ═══════════════════════════════════════════════════════

  async function startSpeechPipeline() {
    const health = await backendHealthCheck(1800);
    if (!health.ok) throw new Error('Backend not reachable.');

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    appState.micStream = stream;
    if (videoEl && videoEl.srcObject) videoEl.srcObject = null;

    const { from } = getEffectiveLangs();
    const sttLang = sttCodeForVoiceLang(from);

    appState.abort = new AbortController();
    _translateCache.clear();
    clearVoiceInterimLiveText('speech-start');

    appState.speechSource = createGoogleSttSource({
      stream,
      lang: sttLang,
      wsUrl: await buildSseUrl(BACKEND_WS_URL),
      onInterim: (line) => { handleSpeechInterimTranscript(line); },
      onFinal: (line) => { handleSpeechFinalTranscript(line).catch(() => { }); },
      onStatus: (s) => {
        if (!appState.running) return;
        if (uiStatusText && typeof s === 'string' && s.trim()) uiStatusText.textContent = s;
      },
    });

    await appState.speechSource.start();
  }

  function pauseSpeechPipeline() {
    clearVoiceInterimLiveText('speech-pause');
    appState.speechSource?.pause();
  }

  function resumeSpeechPipeline() { appState.speechSource?.resume(); }

  function stopSpeechPipeline() {
    clearVoiceInterimLiveText('speech-stop');
    try { appState.speechSource?.stop(); } catch { }
    appState.speechSource = null;
    try { appState.micStream?.getTracks().forEach(t => t.stop()); } catch { }
    appState.micStream = null;
  }

  // ═══════════════════════════════════════════════════════
  //  KEYPOINTS SKELETON OVERLAY
  // ═══════════════════════════════════════════════════════

  // RTMPose-133 bone pairs: [joint_a_idx, joint_b_idx]
  const _SKELETON_BONES = [
    // Body (COCO-17)
    [0,1],[0,2],[1,3],[2,4],[5,6],[5,7],[7,9],[6,8],[8,10],
    [5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16],
    // Left hand (joints 91-111)
    [91,92],[92,93],[93,94],[94,95],
    [91,96],[96,97],[97,98],[98,99],
    [91,100],[100,101],[101,102],[102,103],
    [91,104],[104,105],[105,106],[106,107],
    [91,108],[108,109],[109,110],[110,111],
    // Right hand (joints 112-132)
    [112,113],[113,114],[114,115],[115,116],
    [112,117],[117,118],[118,119],[119,120],
    [112,121],[121,122],[122,123],[123,124],
    [112,125],[125,126],[126,127],[127,128],
    [112,129],[129,130],[130,131],[131,132],
  ];

  const _kpCanvas = document.getElementById('kp-canvas');

  // Returns the rect inside the canvas where the camera image is *actually*
  // drawn — accounts for object-fit:contain letterboxing on either the <video>
  // element or the MJPEG <img> overlay. Without this, normalized 0..1
  // keypoints get smeared across the full canvas including the empty bars,
  // causing a few-percent misalignment at edges.
  function _videoFitRect(canvas) {
    const w = canvas.width;
    const h = canvas.height;
    let nw = 0, nh = 0;
    if (videoEl && videoEl.videoWidth && videoEl.videoHeight && videoEl.style.display !== 'none') {
      nw = videoEl.videoWidth; nh = videoEl.videoHeight;
    } else if (aslState && aslState.mjpegImg && aslState.mjpegImg.naturalWidth) {
      nw = aslState.mjpegImg.naturalWidth; nh = aslState.mjpegImg.naturalHeight;
    }
    if (!nw || !nh) return { x: 0, y: 0, w, h };
    const scale = Math.min(w / nw, h / nh);
    const dw = nw * scale, dh = nh * scale;
    return { x: (w - dw) / 2, y: (h - dh) / 2, w: dw, h: dh };
  }

  function clearSkeletonCanvas() {
    if (!_kpCanvas) return;
    const ctx = _kpCanvas.getContext('2d');
    ctx.clearRect(0, 0, _kpCanvas.width, _kpCanvas.height);
    _kpCanvas.style.display = 'none';
  }

  // Per-region thresholds. Real hands form a tight cluster of 21 joints.
  // Phantom hands (RTMPose hallucinating when hands aren't in frame) scatter
  // those joints across the image — we use the bounding-box spread to detect
  // and drop those. This is a much stronger signal than per-joint confidence.
  const _BODY_CONF = 0.3;
  const _HAND_CONF = 0.3;
  const _HAND_MIN_VISIBLE = 6;         // min confident hand joints needed before we consider the hand
  const _HAND_MAX_SPREAD = 0.35;       // max bbox width/height the hand can span (normalized)
  const _HAND_OVERLAP_DIST = 0.08;     // if both hand wrists collapse within this distance, keep only the higher-confidence one

  function _handStats(kps, scores, startIdx) {
    let visible = 0;
    let sumConf = 0;
    let minX = 1, minY = 1, maxX = 0, maxY = 0;
    for (let i = 0; i < 21; i++) {
      const s = scores[startIdx + i] || 0;
      sumConf += s;
      if (s >= _HAND_CONF) {
        visible++;
        const [x, y] = kps[startIdx + i];
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
    return {
      visible,
      avgConf: sumConf / 21,
      spreadX: visible >= 2 ? maxX - minX : 0,
      spreadY: visible >= 2 ? maxY - minY : 0,
      wrist: kps[startIdx] || [0, 0],
      wristConf: scores[startIdx] || 0,
    };
  }

  function _handIsReal(stats) {
    if (stats.visible < _HAND_MIN_VISIBLE) return false;
    if (stats.spreadX > _HAND_MAX_SPREAD) return false;
    if (stats.spreadY > _HAND_MAX_SPREAD) return false;
    return true;
  }

  function drawSkeleton(data) {
    if (!_kpCanvas || !data?.kps?.length) { clearSkeletonCanvas(); return; }
    if (!data.has_person) { clearSkeletonCanvas(); return; }
    // The OURS backend pipeline (ASL_PIPELINE=ours) emits 86-keypoint
    // MediaPipe skeletons with a layout marker. Render with the matching
    // layout-aware branch instead of the RTMPose-133 indices below.
    if (data.layout === 'mp86') { drawSkeleton86(data); return; }

    const kps = data.kps;        // [[x_norm, y_norm], ...]
    const scores = data.scores || [];

    // Match canvas size to video element
    const parent = _kpCanvas.parentElement;
    const w = parent ? parent.clientWidth : 640;
    const h = parent ? parent.clientHeight : 480;
    _kpCanvas.width = w;
    _kpCanvas.height = h;
    _kpCanvas.style.display = '';

    const ctx = _kpCanvas.getContext('2d');
    ctx.clearRect(0, 0, w, h);
    const fit = _videoFitRect(_kpCanvas);
    const px = (nx) => fit.x + nx * fit.w;
    const py = (ny) => fit.y + ny * fit.h;

    // Decide whether each hand is visible enough to draw.
    const left = _handStats(kps, scores, 91);
    const right = _handStats(kps, scores, 112);
    let showLeft = _handIsReal(left);
    let showRight = _handIsReal(right);

    // If both hands collapse onto the same location (model sees one hand twice),
    // keep only the stronger one.
    if (showLeft && showRight) {
      const dx = left.wrist[0] - right.wrist[0];
      const dy = left.wrist[1] - right.wrist[1];
      if (Math.hypot(dx, dy) < _HAND_OVERLAP_DIST) {
        if (left.avgConf >= right.avgConf) showRight = false;
        else showLeft = false;
      }
    }

    const isLeftHand = (i) => i >= 91 && i <= 111;
    const isRightHand = (i) => i >= 112 && i <= 132;

    function jointThresh(i) {
      return (isLeftHand(i) || isRightHand(i)) ? _HAND_CONF : _BODY_CONF;
    }

    function keep(i) {
      if (i >= kps.length) return false;
      if (isLeftHand(i) && !showLeft) return false;
      if (isRightHand(i) && !showRight) return false;
      return (scores[i] || 0) >= jointThresh(i);
    }

    // Draw bones
    ctx.strokeStyle = 'rgba(0, 200, 255, 0.7)';
    ctx.lineWidth = 2;
    for (const [a, b] of _SKELETON_BONES) {
      if (!keep(a) || !keep(b)) continue;
      ctx.beginPath();
      ctx.moveTo(px(kps[a][0]), py(kps[a][1]));
      ctx.lineTo(px(kps[b][0]), py(kps[b][1]));
      ctx.stroke();
    }

    // Draw joints
    for (let i = 0; i < kps.length; i++) {
      if (!keep(i)) continue;
      const x = px(kps[i][0]);
      const y = py(kps[i][1]);
      ctx.beginPath();
      ctx.arc(x, y, 3, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255, 80, 80, 0.9)';
      ctx.fill();
    }
  }

  // 86-kp MediaPipe layout (ours backend): [0:8 pose] [8:44 face] [44:65 L-hand] [65:86 R-hand].
  // Hand bones use the standard MediaPipe-21 wrist→tip topology.
  const _MP86_HAND_BONES = [
    [0, 1], [1, 2], [2, 3], [3, 4],         // thumb
    [0, 5], [5, 6], [6, 7], [7, 8],         // index
    [0, 9], [9, 10], [10, 11], [11, 12],    // middle
    [0, 13], [13, 14], [14, 15], [15, 16],  // ring
    [0, 17], [17, 18], [18, 19], [19, 20],  // pinky
    [5, 9], [9, 13], [13, 17],              // palm crossbeams
  ];
  // Pose-8 layout: [11=L-shoulder, 12=R-shoulder, 13=L-elbow, 14=R-elbow,
  //                15=L-wrist, 16=R-wrist, 23=L-hip, 24=R-hip] → indices 0..7.
  const _MP86_POSE_BONES = [
    [0, 1], [0, 6], [1, 7], [6, 7], // shoulders + hips rectangle
    [0, 2], [2, 4],                   // L arm: shoulder → elbow → wrist
    [1, 3], [3, 5],                   // R arm
  ];

  function drawSkeleton86(data) {
    if (!_kpCanvas) return;
    const kps = data.kps;
    const scores = data.scores || [];
    if (!kps || !kps.length) { clearSkeletonCanvas(); return; }

    const parent = _kpCanvas.parentElement;
    const w = parent ? parent.clientWidth : 640;
    const h = parent ? parent.clientHeight : 480;
    _kpCanvas.width = w;
    _kpCanvas.height = h;
    _kpCanvas.style.display = '';
    const ctx = _kpCanvas.getContext('2d');
    ctx.clearRect(0, 0, w, h);

    const POSE_END = 8;
    const FACE_END = 44;
    const HAND_L_START = 44;
    const HAND_R_START = 65;
    const TOTAL = 86;
    const fit = _videoFitRect(_kpCanvas);
    const px = (nx) => fit.x + nx * fit.w;
    const py = (ny) => fit.y + ny * fit.h;

    function visible(i, thr) {
      return (scores[i] || 0) >= thr && kps[i] && kps[i][0] >= 0 && kps[i][0] <= 1;
    }

    function drawBones(bones, baseIdx, color, thr) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      for (const [a, b] of bones) {
        const ia = baseIdx + a, ib = baseIdx + b;
        if (ia >= TOTAL || ib >= TOTAL) continue;
        if (!visible(ia, thr) || !visible(ib, thr)) continue;
        ctx.beginPath();
        ctx.moveTo(px(kps[ia][0]), py(kps[ia][1]));
        ctx.lineTo(px(kps[ib][0]), py(kps[ib][1]));
        ctx.stroke();
      }
    }

    // Match mobile palette: cyan bones + red joints across pose and hands.
    const BONE_COLOR = 'rgba(0, 200, 255, 0.7)';
    const JOINT_COLOR = 'rgba(255, 80, 80, 0.9)';
    drawBones(_MP86_POSE_BONES, 0, BONE_COLOR, 0.4);
    drawBones(_MP86_HAND_BONES, HAND_L_START, BONE_COLOR, 0.05);
    drawBones(_MP86_HAND_BONES, HAND_R_START, BONE_COLOR, 0.05);

    for (let i = 0; i < TOTAL; i++) {
      const isHand = i >= HAND_L_START;
      const isFace = i >= POSE_END && i < FACE_END;
      const thr = isHand ? 0.05 : (isFace ? 0.3 : 0.4);
      if (!visible(i, thr)) continue;
      ctx.beginPath();
      ctx.arc(px(kps[i][0]), py(kps[i][1]), isHand ? 2 : 3, 0, Math.PI * 2);
      ctx.fillStyle = JOINT_COLOR;
      ctx.fill();
    }
  }

  // ═══════════════════════════════════════════════════════
  //  ASL PIPELINE (ASL mode → port 8000)
  // ═══════════════════════════════════════════════════════

  async function _openAslEventSource() {
    if (aslState.eventSource) { aslState.eventSource.close(); aslState.eventSource = null; }
    const sseUrl = await buildSseUrl(joinServiceUrl(ASL_API, '/stream'));
    const es = new EventSource(sseUrl);
    aslState._connectedAt = Date.now();
    aslState.eventSource = es;

    es.onmessage = (ev) => {
      let parsed;
      try { parsed = JSON.parse(ev.data); } catch { return; }
      const { event, data } = parsed;

      switch (event) {
        case 'translation': {
          const english = data?.english;
          if (!english) break;
          const { to } = getEffectiveLangs();
          const suppressTts = !!data?.tts_suppress;
          if (!to || to === 'en') {
            appendCommittedSessionText('asl', english, { reason: 'asl-translation' });
            if (!suppressTts) enqueueTts(english, 'en');
          } else {
            const gen = sessionState.generation;
            translateViaBackend(english, 'en', to).then((translated) => {
              if (gen !== sessionState.generation) return;
              appendCommittedSessionText('asl', translated, { reason: 'asl-translated' });
              if (!suppressTts) enqueueTts(translated, to);
            }).catch(() => {
              if (gen !== sessionState.generation) return;
              appendCommittedSessionText('asl', english, { reason: 'asl-translate-fallback' });
              if (!suppressTts) enqueueTts(english, 'en');
            });
          }
          break;
        }
        case 'sign_accepted':
          if (uiStatusText && data?.label != null)
            uiStatusText.textContent = `Detected: ${data.label} (${Math.round((data.confidence ?? 0) * 100)}%)`;
          break;
        case 'gloss_updated':
          if (uiStatusText && data?.gloss_str)
            uiStatusText.textContent = `Gloss: ${data.gloss_str}`;
          break;
        case 'state_change':
          if (uiStatusText && data?.state)
            uiStatusText.textContent = `ASL: ${data.state}`;
          break;
        case 'pipeline_started':
          if (uiStatusText) uiStatusText.textContent = 'ASL pipeline running';
          break;
        case 'pipeline_stopped':
            if (aslState._connectedAt && (Date.now() - aslState._connectedAt < 2000)) {
              console.debug('[ASL SSE] Ignoring stale pipeline_stopped');
              break;
            }
          stopPipeline();
          break;
        case 'keypoints':
          drawSkeleton(data);
          break;
        case 'error':
          if (uiStatusText) uiStatusText.textContent = `Error: ${data?.message ?? 'unknown'}`;
          stopPipeline();
          break;
        default:
          console.debug('[ASL SSE]', event, data);
      }
    };

    es.onerror = () => {
      console.warn('[ASL SSE] connection error');
      aslState.connected = false;
    };
  }

  async function startAslPipeline() {
    // Start backend pipeline — request remote camera source so the
    // backend doesn't try to open a local webcam (RunPod has none).
    // In same-origin proxy/cloud mode, also request skip_preview so the
    // backend stops running unused MJPEG annotate/encode work.
    const startPayload = { camera_source: 'remote' };
    if (ASL_IS_PROXY) startPayload.skip_preview = true;

    const res = await authFetch(joinServiceUrl(ASL_API, '/start'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(startPayload),
    });
    if (res.status === 401) {
      authState.user = null;
      setAuthRequiredBanner(true);
      showAuthToast('Session expired. Please sign in again.');
      throw new Error('Authentication required');
    }
    if (!res.ok) throw new Error(`ASL backend /start failed: ${res.status}`);

    const startData = await res.json().catch(() => ({}));
    const cameraSource = startData?.config?.camera_source ?? 'remote';

    if (cameraSource === 'remote') {
      // ── Remote mode: browser-native preview + WS-first frame transport ──

      // 1. Open browser camera and show local preview
      aslState.cameraStream = await openCamera(videoEl);

      // 2. Choose transport: WebSocket first; HTTP POST fallback.
      //    Lower capture cost only on the proxy/cloud path.
      // 30 fps matches the training data (ASL Citizen, MediaPipe-86 at ~30fps)
      // and the clip_studio Live tab. quality=0.92 minimises JPEG artifacts
      // around fingertips — at 0.7 hand landmarks were noticeably noisier
      // than clip_studio's uncompressed-BGR path (same model gave inconsistent
      // 5–75% confidences vs clip_studio's stable 60–90%).
      const opts = ASL_IS_PROXY
        ? { fps: 15, quality: 0.6, maxWidth: 640 }
        : { fps: 30, quality: 0.92, maxWidth: 1280 };
      const httpUrl = await buildSseUrl(joinServiceUrl(ASL_API, '/upload_frame'));
      const wsUrl = await buildSseUrl(joinServiceWs(ASL_API, '/ws/frames'));

      let transportMode = 'http';
      try {
        const ws = await openWsWithTimeout(wsUrl, 1500);
        aslState.frameWs = ws;
        aslState.frameStreamer = streamFrames(ws, videoEl, opts);
        transportMode = 'ws';
      } catch (err) {
        console.warn('[ASL] WebSocket frame transport unavailable, falling back to HTTP POST', err);
        aslState.frameWs = null;
        aslState.frameStreamer = streamFramesHttp(httpUrl, videoEl, opts);
        transportMode = 'http';
      }
      aslState.remoteTransport = transportMode;
      aslState.frameHttpUrl = httpUrl;
      aslState.frameOpts = opts;

      // 3. Preview strategy:
      //    - Proxy/cloud: browser-native <video> is the main preview; no MJPEG
      //      overlay (backend skip_preview is on; /video_feed would be empty).
      //    - Direct (non-proxy) remote mode: keep legacy behavior with MJPEG
      //      overlay so existing direct-port deployments are unchanged.
      if (ASL_IS_PROXY) {
        aslState.previewMode = 'browser-native';
        if (videoEl) videoEl.style.display = '';
        aslState.mjpegImg = null;
      } else {
        aslState.previewMode = 'mjpeg';
        const img = document.createElement('img');
        img.crossOrigin = 'use-credentials';
        img.src = await buildSseUrl(joinServiceUrl(ASL_API, '/video_feed'));
        img.alt = 'ASL annotated feed';
        img.style.cssText =
          'position:absolute;inset:0;width:100%;height:100%;object-fit:contain;' +
          'border-radius:0.5rem;opacity:1;pointer-events:none;z-index:1;';
        img.onerror = () => { img.style.display = 'none'; };
        if (videoEl) videoEl.style.display = 'none';
        if (videoEl) videoEl.parentElement.appendChild(img);
        aslState.mjpegImg = img;
      }

    } else {
      // ── Local mode (original): backend owns the camera ──

      // Hide <video>, show MJPEG <img> in its place
      if (videoEl) videoEl.style.display = 'none';
      const img = document.createElement('img');
      img.crossOrigin = 'use-credentials';
      img.src = await buildSseUrl(joinServiceUrl(ASL_API, '/video_feed'));
      img.alt = 'ASL camera feed';
      img.style.cssText =
        'position:absolute;inset:0;width:100%;height:100%;object-fit:contain;border-radius:0.5rem;z-index:1;';
      img.onerror = () => { img.style.display = 'none'; };
      if (videoEl) videoEl.parentElement.appendChild(img);
      aslState.mjpegImg = img;
    }

    // Open SSE connection for events (same for both modes)
    _openAslEventSource();
    aslState.connected = true;
  }

  function stopAslPipeline() {
    // Close SSE
    try { aslState.eventSource?.close(); } catch { }
    aslState.eventSource = null;

    // Stop frame streaming (remote mode)
    try { aslState.frameStreamer?.stop(); } catch { }
    aslState.frameStreamer = null;

    // Close WebSocket frame transport if it was used
    try { aslState.frameWs?.close(); } catch { }
    aslState.frameWs = null;
    aslState.remoteTransport = null;

    // Stop browser camera (remote mode) — stop tracks but preserve srcObject to avoid black flash
    if (aslState.cameraStream) {
      // Keep tracks alive across an asl↔letter mode switch (avoids black flash).
      if (!appState._modeTransitionKeepCamera) {
        aslState.cameraStream.getTracks().forEach(t => t.stop());
      }
      aslState.cameraStream = null;
    }
    clearSkeletonCanvas();

    // Remove MJPEG img
    try { aslState.mjpegImg?.remove(); } catch { }
    aslState.mjpegImg = null;

    // Restore <video> element
    if (videoEl) videoEl.style.display = '';

    aslState.connected = false;

    // Tell backend to stop (fire-and-forget)
    authFetch(joinServiceUrl(ASL_API, '/stop'), { method: 'POST' }).catch(() => {});
  }

  function pauseAslPipeline() {
    // Pause: close SSE, stop frame streaming, freeze MJPEG
    try { aslState.eventSource?.close(); } catch { }
    aslState.eventSource = null;
    try { aslState.frameStreamer?.stop(); } catch { }
    aslState.frameStreamer = null;
    try { aslState.frameWs?.close(); } catch { }
    aslState.frameWs = null;
    if (aslState.mjpegImg) aslState.mjpegImg.src = '';
    if (uiStatusText) uiStatusText.textContent = 'ASL paused';
  }

  async function resumeAslPipeline() {
    // Resume: restart frame streaming if in remote mode, restore MJPEG, reopen SSE.
    // Keep the transport choice made at start; fall back to HTTP on WS failure.
    if (videoEl && aslState.cameraStream) {
      const opts = aslState.frameOpts
        || (ASL_IS_PROXY ? { fps: 15, quality: 0.6, maxWidth: 640 } : { fps: 30, quality: 0.92, maxWidth: 1280 });
      const httpUrl = aslState.frameHttpUrl || joinServiceUrl(ASL_API, '/upload_frame');
      if (aslState.remoteTransport === 'ws') {
        try {
          const ws = await openWsWithTimeout(await buildSseUrl(joinServiceWs(ASL_API, '/ws/frames')), 1500);
          aslState.frameWs = ws;
          aslState.frameStreamer = streamFrames(ws, videoEl, opts);
        } catch {
          aslState.frameWs = null;
          aslState.frameStreamer = streamFramesHttp(httpUrl, videoEl, opts);
          aslState.remoteTransport = 'http';
        }
      } else {
        aslState.frameStreamer = streamFramesHttp(httpUrl, videoEl, opts);
      }
    }
    if (aslState.mjpegImg) aslState.mjpegImg.src = await buildSseUrl(joinServiceUrl(ASL_API, '/video_feed'));
    _openAslEventSource();
    if (uiStatusText) uiStatusText.textContent = 'ASL pipeline running';
  }

  // ═══════════════════════════════════════════════════════
  //  LETTER PIPELINE (Letter mode → port 8002)
  // ═══════════════════════════════════════════════════════

  let _letterLineActive = false;
  const LETTER_LIVE_CONFIDENCE_THRESHOLD = 0.70;
  const LETTER_REPEAT_UNLOCK_MS = 450;

  function _letterDebug(message, details) {
    if (!letterState.debugEnabled) return;
    if (details === undefined) console.debug('[Letter]', message);
    else console.debug('[Letter]', message, details);
  }

  function _clearLetterRepeatUnlockTimer() {
    if (!letterState.repeatUnlockTimer) return;
    clearTimeout(letterState.repeatUnlockTimer);
    letterState.repeatUnlockTimer = null;
  }

  function _scheduleLetterRepeatUnlock() {
    _clearLetterRepeatUnlockTimer();
    letterState.repeatUnlockTimer = setTimeout(() => {
      letterState.lastRenderedDetectedLetter = '';
      letterState.repeatUnlockTimer = null;
      _letterDebug('Letter repeat unlock reset');
    }, LETTER_REPEAT_UNLOCK_MS);
  }

  function _currentLetterCaptionText() {
    return String(letterState.liveCaptionWord ?? '');
  }

  function _setLetterCaptionText(nextText, {
    render = true,
    markDirty = false,
    reason = 'update',
    forceRender = false,
  } = {}) {
    const normalizedText = String(nextText ?? '');
    const prevText = _currentLetterCaptionText();
    const changed = normalizedText !== prevText;

    if (changed) {
      letterState.liveCaptionWord = normalizedText;
    }

    if (changed || forceRender) {
      setLiveComposition('letter', normalizedText, {
        markDirty,
        reason: `letter-${reason}`,
      });
    }

    if (render && (changed || forceRender)) {
      _renderLetterCaptionLine();
    }

    if (markDirty && normalizedText.trim()) {
      dirty = true;
    }

    if (changed || forceRender) {
      console.warn('[LETTER-DIAG] caption source updated', {
        reason,
        prevText,
        nextText: normalizedText,
        render,
        forceRender,
        dirty,
      });
    }

    return changed;
  }

  function _renderLetterCaptionLine() {
    const renderedText = _currentLetterCaptionText();
    const shouldShowLine = renderedText.trim().length > 0;

    if (!shouldShowLine) {
      _letterLineActive = false;
      console.warn('[LETTER-DIAG] render letter caption cleared', {
        sharedCaptionText: renderedText,
      });
      return;
    }

    _letterLineActive = true;
    renderSharedSession();
    const lastLineAfter = getRenderedSessionText();
    console.warn('[LETTER-DIAG] render letter caption synced', {
      sharedCaptionText: renderedText,
      renderedLineText: lastLineAfter,
      used: 'shared-session-render',
      match: lastLineAfter.endsWith(renderedText.trim()),
    });
  }

  function _resetLetterLiveComposition({
    clearCaption = false,
    render = false,
  } = {}) {
    const hadCaption = Boolean(_currentLetterCaptionText());
    _clearLetterRepeatUnlockTimer();
    letterState.lastRenderedDetectedLetter = '';
    letterState._pendingDetectedLetter = '';
    letterState._pendingDetectedCount = 0;
    if (clearCaption) {
      _setLetterCaptionText('', {
        render,
        reason: 'reset-clear',
        forceRender: render || hadCaption || _letterLineActive,
      });
    } else if (render && (hadCaption || _letterLineActive)) {
      _renderLetterCaptionLine();
    }

    _letterDebug('Reset Letter live composition', {
      clearCaption,
      liveCaptionWord: _currentLetterCaptionText(),
    });
  }

  function _clearLetterCaptionState({ render = false } = {}) {
    letterState.lastAcceptedWord = '';
    _resetLetterLiveComposition({ clearCaption: true, render });
    if (!render) _letterLineActive = false;
  }

  function _appendDetectedLetterToLiveCaption(letter) {
    const normalizedLetter = String(letter ?? '').trim().toLowerCase();
    if (!normalizedLetter) return;

    // Same letter as already rendered → keep timer for repeat-unlock, skip
    if (normalizedLetter === letterState.lastRenderedDetectedLetter) {
      console.warn('[LETTER-DIAG] skip: repeat guard', { letter: normalizedLetter, lastRendered: letterState.lastRenderedDetectedLetter });
      _scheduleLetterRepeatUnlock();
      return;
    }

    // Guard: do not let live caption run more than 1 char ahead of accepted word
    const liveLen = _currentLetterCaptionText().length;
    const acceptedLen = (letterState.lastAcceptedWord || '').length;
    if (liveLen > acceptedLen + 1) {
      console.warn('[LETTER-DIAG] skip: live too far ahead of accepted', { liveLen, acceptedLen });
      return;
    }

    // Track consecutive detections of this candidate letter
    if (normalizedLetter === letterState._pendingDetectedLetter) {
      letterState._pendingDetectedCount++;
    } else {
      letterState._pendingDetectedLetter = normalizedLetter;
      letterState._pendingDetectedCount = 1;
    }

    // Require 2+ consecutive frames before appending (filters transition noise)
    if (letterState._pendingDetectedCount < 2) {
      console.warn('[LETTER-DIAG] skip: pending count', { letter: normalizedLetter, count: letterState._pendingDetectedCount });
      _letterDebug('Pending new letter (need more frames)', {
        letter: normalizedLetter,
        count: letterState._pendingDetectedCount,
      });
      return;
    }

    // Commit: cancel old timer, clear pending, append
    _clearLetterRepeatUnlockTimer();
    letterState._pendingDetectedLetter = '';
    letterState._pendingDetectedCount = 0;

    const prevWord = _currentLetterCaptionText();
    letterState.lastRenderedDetectedLetter = normalizedLetter;
    _setLetterCaptionText(`${prevWord}${normalizedLetter}`, {
      render: true,
      markDirty: true,
      reason: 'letter_detected',
    });
    console.warn('[LETTER-DIAG] commit letter', {
      letter: normalizedLetter,
      prevWord,
      liveCaptionWord: _currentLetterCaptionText(),
    });

    _letterDebug('Appended live detected letter', {
      letter: normalizedLetter,
      liveCaptionWord: _currentLetterCaptionText(),
    });
  }

  function _syncLiveCaptionToAcceptedWord(wordSoFar) {
    const acceptedWord = String(wordSoFar ?? '').toLowerCase();
    if (!acceptedWord) return;
    const liveCaptionText = _currentLetterCaptionText();

    // If the shared caption already starts with the accepted backend word,
    // the frontend is consistent with (and possibly ahead of) the backend.
    // Keep any correct trailing live letters already rendered.
    if (liveCaptionText.startsWith(acceptedWord)) {
      console.warn('[LETTER-DIAG] sync skip: live consistent', {
        acceptedWord,
        liveCaptionWord: liveCaptionText,
      });
      return;
    }

    // Otherwise: accepted is ahead of live, or live has diverged due to noise.
    // Adopt the accepted word as the authoritative floor.
    const prevWord = liveCaptionText;
    _setLetterCaptionText(acceptedWord, {
      render: true,
      markDirty: true,
      reason: 'letter_accepted',
    });

    // Reset raw-detection state to align with the corrected word:
    // - repeat guard set to last accepted char (prevents re-committing it)
    // - pending cleared (stale candidate from before correction)
    const lastChar = acceptedWord.slice(-1);
    letterState.lastRenderedDetectedLetter = lastChar;
    letterState._pendingDetectedLetter = '';
    letterState._pendingDetectedCount = 0;
    _clearLetterRepeatUnlockTimer();

    console.warn('[LETTER-DIAG] sync corrected', {
      acceptedWord,
      prevWord,
      liveCaptionWord: _currentLetterCaptionText(),
    });
    _letterDebug('Synced live caption from accepted word (corrective)', {
      acceptedWord,
      liveCaptionWord: _currentLetterCaptionText(),
    });
  }

  async function _openLetterEventSource() {
    if (letterState.eventSource) { letterState.eventSource.close(); letterState.eventSource = null; }
    const sseUrl = await buildSseUrl(joinServiceUrl(LETTER_API, '/stream'));
    const es = new EventSource(sseUrl);
    letterState._connectedAt = Date.now();
    letterState.eventSource = es;

    es.onmessage = (ev) => {
      let parsed;
      try { parsed = JSON.parse(ev.data); } catch { return; }
      const { event, data } = parsed;

        switch (event) {
        case 'keypoints':
          drawSkeleton(data);
          break;
        case 'letter_detected':
          if (uiStatusText && data?.letter != null)
            uiStatusText.textContent = `Detected: ${data.letter} (${Math.round((data.confidence ?? 0) * 100)}%)`;
          break;
        case 'letter_accepted': {
          if (letterState.awaitingFreshStart) break;
          const letter = typeof data?.letter === 'string' ? data.letter.toLowerCase() : '';
          if (!letter) break;

          const prevWord = _currentLetterCaptionText();
          _setLetterCaptionText(`${prevWord}${letter}`, {
            render: true,
            markDirty: true,
            reason: 'letter_accepted',
          });

          letterState.lastAcceptedWord = typeof data?.word_so_far === 'string' ? data.word_so_far.toLowerCase() : '';
          dirty = true;
          if (uiStatusText) uiStatusText.textContent = `Letter: ${letter.toUpperCase()} → ${_currentLetterCaptionText()}`;
          break;
        }
        case 'word_completed': {
          if (letterState.awaitingFreshStart) break;
          if (!shouldTranslate()) {
            flushPendingLetterLiveText('letter-word-completed');
          } else {
            const englishWord = normalizeSessionSegment(_currentLetterCaptionText());
            _clearLetterCaptionState({ render: true });
            if (englishWord) {
              const { to } = getEffectiveLangs();
              const gen = sessionState.generation;
              translateViaBackend(englishWord, 'en', to).then((translated) => {
                if (gen !== sessionState.generation) return;
                appendCommittedSessionText('letter', translated, { reason: 'letter-translated' });
                enqueueTts(translated, to);
              }).catch(() => {
                if (gen !== sessionState.generation) return;
                appendCommittedSessionText('letter', englishWord, { reason: 'letter-translate-fallback' });
                enqueueTts(englishWord, 'en');
              });
            }
          }
          letterState.lastAcceptedWord = '';
          break;
        }
        case 'pipeline_started':
          if (!letterState.awaitingFreshStart) {
            _letterDebug('Ignoring replayed pipeline_started on active Letter session');
            break;
          }
          _letterLineActive = false;
          letterState.awaitingFreshStart = false;
          letterState.lastAcceptedWord = '';
          _resetLetterLiveComposition({ clearCaption: true, render: false });
          _letterDebug('Fresh Letter session ready after pipeline_started');
          if (uiStatusText) uiStatusText.textContent = 'Letter pipeline running';
          break;
        case 'pipeline_stopped':
          if (letterState._connectedAt && (Date.now() - letterState._connectedAt < 2000)) {
            console.debug('[Letter SSE] Ignoring stale pipeline_stopped');
            break;
          }
          flushPendingLetterLiveText('letter-pipeline-stopped');
          _resetLetterLiveComposition({ clearCaption: false, render: false });
          letterState.awaitingFreshStart = false;
          stopPipeline();
          break;
        case 'error':
          _resetLetterLiveComposition({ clearCaption: false, render: false });
          letterState.awaitingFreshStart = false;
          if (uiStatusText) uiStatusText.textContent = `Error: ${data?.message ?? 'unknown'}`;
          stopPipeline();
          break;
        default:
          console.debug('[Letter SSE]', event, data);
      }
    };

    es.onerror = () => {
      console.warn('[Letter SSE] connection error');
      letterState.connected = false;
    };
  }

  async function startLetterPipeline() {
    _letterLineActive = false;
    letterState.lastAcceptedWord = '';
    letterState.awaitingFreshStart = true;
    _resetLetterLiveComposition({ clearCaption: true, render: false });
    // Start backend pipeline
    const res = await authFetch(joinServiceUrl(LETTER_API, '/start'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    if (res.status === 401) {
      authState.user = null;
      setAuthRequiredBanner(true);
      showAuthToast('Session expired. Please sign in again.');
      throw new Error('Authentication required');
    }
    if (!res.ok) throw new Error(`Letter backend /start failed: ${res.status}`);

    // Open browser camera and show local preview
    letterState.cameraStream = await openCamera(videoEl);

    // Stream frames via HTTP POST to letter backend
    const uploadUrl = await buildSseUrl(joinServiceUrl(LETTER_API, '/upload_frame'));
    letterState.frameStreamer = streamFramesHttp(uploadUrl, videoEl, { fps: 15, quality: 0.7 });

    // Open SSE connection for letter detection events
    await _openLetterEventSource();
    letterState.connected = true;
  }

  function stopLetterPipeline() {
    _resetLetterLiveComposition({ clearCaption: false, render: false });
    letterState.awaitingFreshStart = false;
    // Close SSE
    try { letterState.eventSource?.close(); } catch { }
    letterState.eventSource = null;

    // Stop frame streaming
    try { letterState.frameStreamer?.stop(); } catch { }
    letterState.frameStreamer = null;

    // Stop browser camera tracks but preserve srcObject to avoid black flash.
    // During an asl↔letter mode transition keep tracks alive so the new pipeline
    // can reuse the same MediaStream (camera.js openCamera is idempotent).
    if (letterState.cameraStream) {
      if (!appState._modeTransitionKeepCamera) {
        letterState.cameraStream.getTracks().forEach(t => t.stop());
      }
      letterState.cameraStream = null;
    }

    letterState.connected = false;

    // Tell backend to stop (fire-and-forget)
    authFetch(joinServiceUrl(LETTER_API, '/stop'), { method: 'POST' }).catch(() => {});
  }

  function pauseLetterPipeline() {
    _resetLetterLiveComposition({ clearCaption: false, render: false });
    try { letterState.eventSource?.close(); } catch { }
    letterState.eventSource = null;
    try { letterState.frameStreamer?.stop(); } catch { }
    letterState.frameStreamer = null;
    letterState.connected = false;
    if (uiStatusText) uiStatusText.textContent = 'Letter paused';
  }

  async function resumeLetterPipeline() {
    letterState.awaitingFreshStart = false;
    _resetLetterLiveComposition({ clearCaption: false, render: false });
    if (videoEl && letterState.cameraStream) {
      const uploadUrl = await buildSseUrl(joinServiceUrl(LETTER_API, '/upload_frame'));
      letterState.frameStreamer = streamFramesHttp(uploadUrl, videoEl, { fps: 15, quality: 0.7 });
    }
    _openLetterEventSource();
    letterState.connected = true;
    if (uiStatusText) uiStatusText.textContent = 'Letter pipeline running';
  }

  // ═══════════════════════════════════════════════════════
  //  PIPELINE CONTROL (shared)
  // ═══════════════════════════════════════════════════════

  async function switchMode(next) {
    if (appState.switching || appState.busy || next === appState.mode) return;
    appState.switching = true;
    // When BOTH the previous and next modes use the camera (asl ↔ letter),
    // keep the underlying MediaStream alive across the stop/start cycle so
    // the preview doesn't flash black. Speech mode has no camera, so any
    // transition into or out of speech still tears the stream down normally.
    const cameraModes = new Set(['asl', 'letter']);
    appState._modeTransitionKeepCamera = (
      cameraModes.has(appState.mode) && cameraModes.has(next)
    );
    try {
      const wasRunning = appState.running;
      const wasPaused = appState.paused;

      sessionDebug('mode switch requested', {
        from: appState.mode,
        to: next,
        committedText: sessionState.committedText,
        liveComposition: sessionState.liveComposition,
      });

      if (appState.mode === 'letter') flushPendingLetterLiveText('mode-switch');
      if (appState.mode === 'speech') clearVoiceInterimLiveText('mode-switch');

      if (wasRunning) await stopPipeline({ suppressFeedback: true });

      appState.mode = next;
      stopTtsNow();

      updateModeUI();
      setStatusForState();
      sessionDebug('mode switched', {
        mode: appState.mode,
        preservedTranscript: sessionState.committedText,
        liveComposition: sessionState.liveComposition,
      });

      if (wasRunning && !wasPaused) await startPipeline({ suppressFeedback: true });
    } finally {
      appState._modeTransitionKeepCamera = false;
      appState.switching = false;
    }
  }

  async function startPipeline({ suppressFeedback = false } = {}) {
    if (appState.busy || appState.running) return;

    const ok = await ensureAuthOrPrompt();
    if (!ok) {
      authLoginRedirect();
      return;
    }

    const consentGiven = localStorage.getItem('consentAccepted') === 'true';
    if (!consentGiven) {
      if (modal) modal.style.display = 'flex';
      return;
    }

    try {
      appState.busy = true;
      setStatusForState();

      stopAllMedia();

      // Null the video srcObject when entering speech mode (no camera needed).
      // Camera modes leave srcObject alive to avoid a black flash during transition.
      if (appState.mode === 'speech' && videoEl && videoEl.srcObject) {
        videoEl.srcObject = null;
      }

      stopTtsNow();

      if (appState.mode === 'speech') await startSpeechPipeline();
      else if (appState.mode === 'letter') await startLetterPipeline();
      else await startAslPipeline();

      appState.running = true;
      appState.paused = false;
      ensureSessionStartedAt();

      setStatusForState();
      if (!suppressFeedback) playFeedback();
    } catch (err) {
      console.error(err);
      alert('Unable to start: ' + (err?.message || String(err)));
      await stopPipeline({ suppressFeedback: true });
    } finally {
      appState.busy = false;
      setStatusForState();
    }
  }

  function pausePipeline() {
    if (!appState.running || appState.paused) return;
    if (appState.mode === 'speech') pauseSpeechPipeline();
    else if (appState.mode === 'letter') pauseLetterPipeline();
    else pauseAslPipeline();
    appState.paused = true;

    stopTtsNow();
    setStatusForState();
  }

  function resumePipeline() {
    if (!appState.running || !appState.paused) return;
    if (appState.mode === 'speech') resumeSpeechPipeline();
    else if (appState.mode === 'letter') resumeLetterPipeline();
    else resumeAslPipeline();
    appState.paused = false;
    setStatusForState();
  }

  async function stopPipeline({ suppressFeedback = false } = {}) {
    try {
      stopTtsNow();

      if (appState.mode === 'speech') stopSpeechPipeline();
      else if (appState.mode === 'letter') stopLetterPipeline();
      else stopAslPipeline();
    } finally {
      stopAllMedia();
      appState.running = false;
      appState.paused = false;
      setStatusForState();
      if (!suppressFeedback) playFeedback();
    }
  }

  function getPersistedCaptionText() {
    const text = getPersistedSessionText();
    sessionDebug('persisted caption text requested', {
      mode: appState.mode,
      textLength: text.length,
      text,
    });
    return text;
  }

  async function saveTranscript() {
    await waitForPendingSpeechFinals();
    const text = getPersistedCaptionText();
    if (!text.trim()) { alert('Nothing to save yet.'); return; }

    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const file = `asl_transcript_${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}.txt`;

    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);

    const a = document.createElement('a');
    a.href = url;
    a.download = file;
    document.body.appendChild(a);
    a.click();

    setTimeout(() => {
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }, 0);

    dirty = false;
    playFeedback();
  }

  async function handleStopRequested() {
    stopTtsNow();
    if (appState.mode === 'letter') flushPendingLetterLiveText('stop-requested');
    if (appState.mode === 'speech') clearVoiceInterimLiveText('stop-requested');

    await stopPipeline();
    await waitForPendingSpeechFinals();

    const text = getPersistedCaptionText();
    if (text.trim()) addHistoryEntry(text);
  }

  function clearCaptions() {
    if (!hasSessionDisplayText()) return;
    if (confirm('Clear captions?')) {
      if (appState.mode === 'letter') _clearLetterCaptionState({ render: false });
      if (appState.mode === 'speech') clearVoiceInterimLiveText('clear-action');
      resetSharedSession('clear-action');
      setStatusForState();
    }
  }

  function handleNewChat() {
    if (appState.mode === 'letter') flushPendingLetterLiveText('new-chat');
    if (appState.mode === 'speech') clearVoiceInterimLiveText('new-chat');

    const text = getPersistedCaptionText().trim();
    const isPristineSelectedHistorySession = selectedHistoryId != null && !dirty;
    let archived = false;

    if (text && !isPristineSelectedHistorySession) {
      archived = addHistoryEntry(text);
    }

    resetSharedSession('new-chat');
    showTransientStatus(
      archived
        ? 'Saved to history. Started a new chat.'
        : 'Started a new chat.'
    );
  }

  // ---- History menu ----
  if (historyMenuButton && historyMenu) {
    historyMenuButton.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = !historyMenu.classList.contains('hidden');
      closeAllMenus();
      if (!isOpen) historyMenu.classList.remove('hidden');
    });
  }

  if (historyClearAllBtn) {
    historyClearAllBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('Clear all saved sessions?')) return;

      if (isAuthed()) {
        const ok = await deleteAllTranscripts();
        if (!ok) {
          showAuthToast('Failed to clear server history. Please try again.');
          return;
        }
      }

      saveHistory([]);
      selectedHistoryId = null;
      renderHistory([]);
      closeAllMenus();
    });
  }

  // ---- Global click to close history menus ----
  document.addEventListener('click', (e) => {
    const target = e.target;
    if (!(target instanceof Element)) return;

    if (
      target.closest('#history-menu-button') ||
      target.closest('#history-menu') ||
      target.closest('.history-item-menu-button') ||
      target.closest('.history-item-menu')
    ) return;

    closeAllMenus();
  });

  // ---- Controls ----
  if (startBtn) startBtn.addEventListener('click', startPipeline);
  if (pauseBtn) pauseBtn.addEventListener('click', pausePipeline);
  if (resumeBtn) resumeBtn.addEventListener('click', resumePipeline);
  if (stopBtn) stopBtn.addEventListener('click', handleStopRequested);
  if (clearBtn) clearBtn.addEventListener('click', clearCaptions);
  if (saveBtn) saveBtn.addEventListener('click', saveTranscript);
  if (newChatBtn) newChatBtn.addEventListener('click', handleNewChat);

  if (modeAslBtn) modeAslBtn.addEventListener('click', () => switchMode('asl'));
  if (modeSpeechBtn) modeSpeechBtn.addEventListener('click', () => switchMode('speech'));
  if (modeLetterBtn) modeLetterBtn.addEventListener('click', () => switchMode('letter'));

  // ---- Consent modal ----
  if (acceptBtn) {
    acceptBtn.addEventListener('click', () => {
      localStorage.setItem('consentAccepted', 'true');
      if (modal) modal.style.display = 'none';
      startPipeline();
    });
  }
  if (declineBtn) {
    declineBtn.addEventListener('click', () => {
      localStorage.setItem('consentAccepted', 'false');
      if (modal) modal.style.display = 'none';
      if (appState.mode === 'letter') {
        _clearLetterCaptionState({ render: false });
      }
      appendCommittedSessionText(appState.mode, 'Permission declined. Cannot start.', {
        reason: 'permission-declined',
        trackMode: false,
      });
    });
  }

  // ---- Caption size (scoped) ----
  applyCaptionSizeFromStorage();
  if (sizeSlider) {
    sizeSlider.addEventListener('input', () => {
      const size = parseInt(sizeSlider.value, 10);
      captions.setTextSize(size);
      if (sizeLabel) sizeLabel.textContent = `${size}pt`;
      localStorage.setItem(scopedKey(KEY_BASE.CAPTION_SIZE), String(size));
      setStatusForState();
    });
  }

  // ---- Language prefs (scoped) ----
  loadSavedVoiceLangs();
  updateLanguageUI();
  persistVoiceLangs();

  if (langFromSel) {
    langFromSel.addEventListener('change', () => {
      if (appState.mode !== 'speech') { updateLanguageUI(); return; }
      const v = (langFromSel.value || '').trim();
      appState.voiceFromLang = VOICE_LANG_KEYS.has(v) ? v : 'en';
      persistVoiceLangs();
      setStatusForState();
    });
  }

  if (langToSel) {
    langToSel.addEventListener('change', () => {
      const v = (langToSel.value || '').trim();
      if (appState.mode === 'asl') {
        appState.aslToLang = VOICE_LANG_KEYS.has(v) ? v : 'en';
        persistVoiceLangs();
        updateLanguageUI();
        setStatusForState();
        return;
      }
      if (appState.mode === 'letter') {
        appState.letterToLang = VOICE_LANG_KEYS.has(v) ? v : 'en';
        persistVoiceLangs();
        updateLanguageUI();
        setStatusForState();
        return;
      }
      if (appState.mode !== 'speech') { updateLanguageUI(); return; }
      appState.voiceToLang = VOICE_LANG_KEYS.has(v) ? v : 'en';
      persistVoiceLangs();
      setStatusForState();
    });
  }

  // ---- TTS toggle (works like a real button) ----
  setTtsEnabled(loadTtsEnabled(), false);
  updateTtsButtonUI();

  if (ttsToggleBtn) {
    wireTap(ttsToggleBtn, (e) => {
      e.preventDefault();
      e.stopPropagation();
      setTtsEnabled(!tts.enabled, true);
    });
  }

  // ---- Keyboard shortcuts ----
  document.addEventListener('keydown', (ev) => {
    const tag = (ev.target instanceof HTMLElement) ? ev.target.tagName.toLowerCase() : '';
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    if (ev.target?.isContentEditable) return;

    if (ev.code === 'Space' || ev.key === ' ') {
      ev.preventDefault();
      return;
    }
    if (ev.key === 'p' || ev.key === 'P') {
      if (appState.running && !appState.paused) pausePipeline();
      else if (appState.running && appState.paused) resumePipeline();
    }
    if (ev.key === 'c' || ev.key === 'C') clearCaptions();
  });

  // ---- B-key: switch between ASL and Letter (focus-robust) ----
  // Dedicated window + capture-phase listener so focused <select> / <button>
  // can't swallow the shortcut via the main keydown's early-return guards,
  // and uses ev.code so keyboard layout / case don't matter.
  window.addEventListener('keydown', (ev) => {
    if (ev.code !== 'KeyB') return;
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const t = ev.target;
    const tag = (t instanceof HTMLElement) ? t.tagName.toLowerCase() : '';
    if (tag === 'input' || tag === 'textarea') return;
    if (t && t.isContentEditable) return;
    if (appState.mode === 'asl') switchMode('letter');
    else if (appState.mode === 'letter') switchMode('asl');
  }, true);

  // ---- Before unload ----
  window.addEventListener('beforeunload', (e) => {
    const text = getPersistedCaptionText();
    if (text.trim()) {
      addHistoryEntry(text);
      if (isAuthed() && !_transcriptBackendDisabled) {
        const newest = loadHistory()[0];
        if (newest) {
          try {
            navigator.sendBeacon(
              TRANSCRIPTS_URL,
              new Blob([JSON.stringify(entryToBackendPayload(newest))], { type: 'application/json' })
            );
          } catch { /* best-effort; localStorage has the entry */ }
        }
      }
    }

    if (appState.running) {
      e.preventDefault();
      e.returnValue = '';
      return '';
    }
  });

  // ---- Initial render ----
  onAuthScopeMaybeChanged();
  renderSharedSession();
  updateModeUI();
  setStatusForState();
});
