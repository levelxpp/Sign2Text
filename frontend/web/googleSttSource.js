function downsampleTo16k(float32, inputRate) {
  const outputRate = 16000;
  if (inputRate === outputRate) return float32;

  // if some weird environment gives <16k, don't upsample here
  if (inputRate < outputRate) return float32;

  const ratio = inputRate / outputRate;
  const newLen = Math.round(float32.length / ratio);
  const out = new Float32Array(newLen);

  let offsetResult = 0;
  let offsetBuffer = 0;

  while (offsetResult < out.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);

    let sum = 0;
    let count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < float32.length; i++) {
      sum += float32[i];
      count++;
    }
    out[offsetResult] = count ? (sum / count) : 0;

    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }

  return out;
}

function floatTo16BitPCM(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
  }
  return out;
}

function normalizeUrlLike(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  if (raw === '/') return '/';
  return raw.replace(/\/+$/, '');
}

// Common ports the static frontend is served from in development.
// When the page is loaded from one of these, a relative `BACKEND_WS_URL`
// like "/voice-api/ws/stt" can't be resolved against window.location
// (the dev server doesn't speak WebSocket / proxy paths). It needs to go
// to the reverse-proxy port instead, where /voice-api/* is actually
// routed to the voice backend by tunnel_proxy.py.
const _DEV_FRONTEND_PORTS = new Set(['5500', '3000', '5173', '8000']);
const _PROXY_PORT = '8080';

// Modern browsers resolve "localhost" to ::1 (IPv6 loopback) by default.
// Our backends bind to 0.0.0.0 (IPv4 only) by default, so a WS to
// ws://localhost:8001 ends up trying ws://[::1]:8001 and fails with
// ECONNREFUSED. Forcing the literal IPv4 loopback dodges the surprise
// without needing a server-side bind change. Public hostnames (real
// LAN IPs, ngrok / cloudflare URLs) pass through unchanged.
function _pinToIPv4Loopback(host) {
  if (!host) return host;
  return host === 'localhost' ? '127.0.0.1' : host;
}

function toAbsoluteWsUrl(value) {
  const normalized = normalizeUrlLike(value);
  if (!normalized) return '';

  // Path-only URL (e.g. "/voice-api/ws/stt" — what runtime-config.js
  // contains in tunnel/proxy mode). Resolve against the proxy port if
  // the page itself is being served from the dev frontend; otherwise
  // resolve against the page origin (page IS the proxy or a public
  // ngrok/cloudflare URL fronting it, so /voice-api/* will route there).
  if (normalized.startsWith('/')) {
    const proto = (location.protocol === 'https:') ? 'wss' : 'ws';
    const host = _pinToIPv4Loopback((location.hostname || 'localhost').trim());
    const pagePort = location.port;
    const targetPort = _DEV_FRONTEND_PORTS.has(pagePort)
      ? _PROXY_PORT
      : (pagePort || (proto === 'wss' ? '443' : '80'));
    return `${proto}://${host}:${targetPort}${normalized}`;
  }

  try {
    const resolved = new URL(normalized, window.location.href);
    if (resolved.protocol === 'https:') resolved.protocol = 'wss:';
    else if (resolved.protocol === 'http:') resolved.protocol = 'ws:';
    resolved.hostname = _pinToIPv4Loopback(resolved.hostname);
    return resolved.toString().replace(/\/+$/, '');
  } catch {
    return normalized.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:');
  }
}

function deriveWsUrlFallback() {
  // Priority:
  // 1) window.BACKEND_WS_URL
  // 2) window.BACKEND_HTTP_BASE -> convert http(s) to ws(s) and append /ws/stt
  // 3) same-host default on port 8001
  // 4) final local fallback (127.0.0.1) if the page host is unavailable

  const w = toAbsoluteWsUrl(window.BACKEND_WS_URL);
  if (w) return w;

  const httpBase = normalizeUrlLike(window.BACKEND_HTTP_BASE);
  if (httpBase) {
    try {
      const u = new URL(httpBase, window.location.href);
      if (u.protocol === 'https:') u.protocol = 'wss:';
      else if (u.protocol === 'http:') u.protocol = 'ws:';
      u.pathname = `${u.pathname.replace(/\/+$/, '')}/ws/stt`;
      u.search = '';
      u.hash = '';
      return u.toString();
    } catch {
      // fall through
    }
  }

  const proto = (location.protocol === 'https:') ? 'wss' : 'ws';
  const host = _pinToIPv4Loopback((location.hostname || '').trim());
  if (host) return `${proto}://${host}:8001/ws/stt`;
  return `${proto}://127.0.0.1:8001/ws/stt`;
}

function isAuthClose(code, reason) {
  const r = String(reason || '').toLowerCase();
  if (code === 4401 || code === 4001) return true;
  if (code === 1008 && (r.includes('unauth') || r.includes('forbid') || r.includes('auth'))) return true;
  if (r.includes('unauth') || r.includes('forbid') || r.includes('not logged') || r.includes('signin') || r.includes('sign in')) return true;
  return false;
}

function isAuthErrorText(text) {
  const t = String(text || '').toLowerCase();
  return (
    t.includes('unauthorized') ||
    t.includes('forbidden') ||
    t.includes('auth required') ||
    t.includes('not logged') ||
    t.includes('please sign in') ||
    t.includes('sign in')
  );
}

export function createGoogleSttSource({
  stream,
  lang = 'en-US',
  wsUrl = null,
  onLine = null,
  onStatus = null,
  onInterim = null,
  onFinal = null,
  onAuthRequired = null,
} = {}) {
  if (!stream) throw new Error('createGoogleSttSource: missing mic MediaStream');

  let ws = null;
  let audioCtx = null;
  let sourceNode = null;
  let processor = null;
  let silenceGain = null;

  let paused = false;
  let stopped = false;
  let started = false;

  let wsOnMsg = null;
  let wsOnClose = null;
  let wsOnErr = null;

  function sendJson(obj) {
    try { ws?.send(JSON.stringify(obj)); } catch {}
  }

  function emitStatus(text) {
    const clean = String(text || '').trim();
    if (!clean) return;
    try { onStatus?.(clean); } catch {}
  }

  function emitLine(text) {
    const clean = String(text || '').trim();
    if (!clean) return;
    try {
      if (onLine) onLine(clean);
      else emitStatus(clean);
    } catch {}
  }

  function emitFinal(text) {
    const clean = String(text || '').trim();
    if (!clean) return;
    try {
      if (onFinal) onFinal(clean);
      else emitLine(clean);
    } catch {}
  }

  function emitInterim(text) {
    const clean = String(text || '').trim();
    if (!clean) return;
    try {
      if (onInterim) onInterim(clean);
      else emitStatus(clean);
    } catch {}
  }

  function handleAuthRequired(hint = '') {
    try { onAuthRequired?.(); } catch {}
    const msg = hint && String(hint).trim()
      ? `Sign in required. ${String(hint).trim()}`
      : 'Sign in required to use Voice mode.';
    emitLine(msg);
  }

  function handleMessage(evt) {
    let msg;
    try { msg = JSON.parse(evt.data); } catch { return; }
    if (!msg || typeof msg !== 'object') return;

    if (msg.type === 'final' && msg.text) {
      emitFinal(String(msg.text));
      return;
    }
    if (msg.type === 'interim' && msg.text) {
      emitInterim(String(msg.text));
      return;
    }
    if (msg.type === 'status' && msg.text) {
      emitStatus(String(msg.text));
      return;
    }
    if (msg.type === 'error') {
      const text = String(msg.text || '');
      const code = String(msg.code || '').toLowerCase();
      if (code === 'unauthorized' || code === 'forbidden' || isAuthErrorText(text)) {
        handleAuthRequired(text);
        return;
      }
      if (text) emitLine(`STT error: ${text}`);
    }
  }

  async function waitForOpen(socket, timeoutMs = 6000) {
    if (socket.readyState === WebSocket.OPEN) return;

    await new Promise((resolve, reject) => {
      let settled = false;

      const t = setTimeout(() => {
        if (settled) return;
        settled = true;
        cleanup();
        reject(new Error(`STT backend timeout (WS not open). URL: ${socket.url}`));
      }, timeoutMs);

      function cleanup() {
        clearTimeout(t);
        socket.removeEventListener('open', onOpen);
        socket.removeEventListener('close', onClose);
        socket.removeEventListener('error', onError);
      }

      function onOpen() {
        if (settled) return;
        settled = true;
        cleanup();
        resolve();
      }

      function onClose(e) {
        if (settled) return;
        settled = true;
        cleanup();

        const code = (e && typeof e.code === 'number') ? e.code : 0;
        const reason = (e && typeof e.reason === 'string') ? e.reason : '';

        const err = new Error(`STT backend closed. URL: ${socket.url} (code ${code}${reason ? `, reason: ${reason}` : ''})`);
        if (isAuthClose(code, reason)) err.authRequired = true;
        reject(err);
      }

      function onError() {
        if (settled) return;
        settled = true;
        cleanup();
        reject(new Error(`STT backend error (WS). URL: ${socket.url}`));
      }

      socket.addEventListener('open', onOpen);
      socket.addEventListener('close', onClose);
      socket.addEventListener('error', onError);
    });
  }

  function cleanupAudio() {
    try { processor?.disconnect(); } catch {}
    try { sourceNode?.disconnect(); } catch {}
    try { silenceGain?.disconnect(); } catch {}

    processor = null;
    sourceNode = null;
    silenceGain = null;

    try { audioCtx?.close(); } catch {}
    audioCtx = null;
  }

  function cleanupWs() {
    if (ws) {
      try {
        if (wsOnMsg) ws.removeEventListener('message', wsOnMsg);
        if (wsOnClose) ws.removeEventListener('close', wsOnClose);
        if (wsOnErr) ws.removeEventListener('error', wsOnErr);
      } catch {}
      wsOnMsg = null;
      wsOnClose = null;
      wsOnErr = null;

      try { ws.close(); } catch {}
    }
    ws = null;
  }

  async function connectWs(url) {
    cleanupWs();

    ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';

    wsOnMsg = (e) => handleMessage(e);

    wsOnClose = (e) => {
      if (stopped) return;

      const code = (e && typeof e.code === 'number') ? e.code : 0;
      const reason = (e && typeof e.reason === 'string') ? e.reason : '';

      if (isAuthClose(code, reason)) {
        handleAuthRequired(reason);
        return;
      }
      emitLine('STT disconnected.');
    };

    wsOnErr = () => {
      if (!stopped) emitLine('STT connection error.');
    };

    ws.addEventListener('message', wsOnMsg);
    ws.addEventListener('close', wsOnClose);
    ws.addEventListener('error', wsOnErr);

    try {
      await waitForOpen(ws, 6000);
    } catch (e) {
      if (e && e.authRequired) {
        handleAuthRequired('');
      }
      throw e;
    }

    sendJson({
      type: 'config',
      lang,
      sampleRate: 16000,
      encoding: 'LINEAR16',
    });
  }

  async function connectWithFallbacks() {
    const preferred = (wsUrl && String(wsUrl).trim())
      ? toAbsoluteWsUrl(String(wsUrl).trim())
      : '';
    const base = preferred || deriveWsUrlFallback();

    const candidates = [base];
    const s = String(base);

    // Try a localhost <-> 127.0.0.1 swap as a defensive fallback for
    // environments where one is misconfigured. Skip the IPv6 [::1]
    // candidate entirely — backends bind 0.0.0.0 (IPv4 only) by default,
    // so [::1] never connects and only surfaces a misleading URL in the
    // error message when the real attempt fails for an unrelated reason.
    if (s.includes('localhost')) candidates.push(s.replace('localhost', '127.0.0.1'));
    if (s.includes('127.0.0.1')) candidates.push(s.replace('127.0.0.1', 'localhost'));

    const uniq = [...new Set(candidates)];

    let firstErr = null;
    let lastErr = null;
    for (const u of uniq) {
      try {
        emitStatus('Connecting...');
        await connectWs(u);
        return;
      } catch (e) {
        if (!firstErr) firstErr = e;
        lastErr = e;
        if (e && e.authRequired) break;
      }
    }
    throw firstErr || lastErr || new Error('STT connect failed');
  }

  async function start() {
    if (started || stopped) return;
    started = true;

    await connectWithFallbacks();
    emitStatus('Listening...');

    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    } catch {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }

    try {
      if (audioCtx.state === 'suspended') await audioCtx.resume();
    } catch {}

    sourceNode = audioCtx.createMediaStreamSource(stream);

    const bufferSize = 4096;
    processor = audioCtx.createScriptProcessor(bufferSize, 1, 1);

    silenceGain = audioCtx.createGain();
    silenceGain.gain.value = 0;

    processor.onaudioprocess = (e) => {
      if (stopped || paused) return;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (ws.bufferedAmount > 1_000_000) return;

      const input = e.inputBuffer.getChannelData(0);
      const down = downsampleTo16k(input, audioCtx.sampleRate);
      const pcm16 = floatTo16BitPCM(down);

      try { ws.send(pcm16.buffer); } catch {}
    };

    sourceNode.connect(processor);
    processor.connect(silenceGain);
    silenceGain.connect(audioCtx.destination);
  }

  function pause() {
    paused = true;
    emitStatus('Paused');
  }

  function resume() {
    paused = false;
    emitStatus('Listening...');
  }

  function stop() {
    if (stopped) return;
    stopped = true;

    try { sendJson({ type: 'stop' }); } catch {}
    cleanupAudio();
    cleanupWs();
  }

  return { start, pause, resume, stop };
}
