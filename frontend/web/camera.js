// open camera
export async function openCamera(videoEl) {
  if (!videoEl) throw new Error('Video element is required');
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('Camera API is not supported in this browser');
  }
  // Reuse the currently attached stream if it has at least one live video
  // track. This keeps the preview alive across mode switches (asl ↔ letter)
  // so the user doesn't see a 200–500ms black flash while a new MediaStream
  // is acquired.
  const existing = videoEl.srcObject;
  if (existing && typeof existing.getVideoTracks === 'function') {
    const liveVideo = existing.getVideoTracks().some((t) => t.readyState === 'live');
    if (liveVideo) {
      await videoEl.play().catch(() => {});
      return existing;
    }
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: 'user' },
    audio: true,
  });
  videoEl.srcObject = stream;
  await videoEl.play().catch(() => {});
  return stream;
}

// stop stream
export function stopStream(stream, videoEl) {
  if (stream) stream.getTracks().forEach((t) => t.stop());
  if (videoEl && videoEl.srcObject) videoEl.srcObject = null;
}

/**
 * Stream camera frames to a backend WebSocket as JPEG blobs.
 *
 * Opens a hidden canvas, captures JPEG frames from the video element,
 * and sends them over the WebSocket at the target FPS.
 *
 * Returns an object with a .stop() method to cease streaming.
 *
 * @param {WebSocket} ws  - Open WebSocket connection to /ws/frames
 * @param {HTMLVideoElement} videoEl - Video element with active camera stream
 * @param {Object} [opts]
 * @param {number} [opts.fps=15]     - Target frames per second
 * @param {number} [opts.quality=0.7] - JPEG quality (0-1)
 * @param {number} [opts.maxWidth=1280] - Max frame width (downscales if larger)
 */
export function streamFrames(ws, videoEl, opts = {}) {
  const fps = opts.fps ?? 15;
  const quality = opts.quality ?? 0.7;
  const maxWidth = opts.maxWidth ?? 1280;

  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  let stopped = false;
  let timer = null;

  function captureAndSend() {
    if (stopped) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    // Backpressure: skip frame if send buffer is backed up (>500KB)
    if (ws.bufferedAmount > 500_000) return;

    const vw = videoEl.videoWidth || 640;
    const vh = videoEl.videoHeight || 480;

    // Downscale if wider than maxWidth
    let w = vw, h = vh;
    if (w > maxWidth) {
      h = Math.round(h * (maxWidth / w));
      w = maxWidth;
    }

    canvas.width = w;
    canvas.height = h;
    ctx.drawImage(videoEl, 0, 0, w, h);

    canvas.toBlob(
      (blob) => {
        if (blob && ws.readyState === WebSocket.OPEN && !stopped) {
          ws.send(blob);
        }
      },
      'image/jpeg',
      quality,
    );
  }

  timer = setInterval(captureAndSend, Math.round(1000 / fps));

  return {
    stop() {
      stopped = true;
      if (timer) { clearInterval(timer); timer = null; }
    },
  };
}

/**
 * Stream camera frames to the backend via HTTP POST (HTTP/2-compatible).
 *
 * Works identically to streamFrames() but uses fetch() POST instead of
 * WebSocket, which is compatible with HTTP/2 reverse proxies (e.g. RunPod).
 *
 * @param {string} url  - URL to POST JPEG frames to (e.g. /asl-api/upload_frame)
 * @param {HTMLVideoElement} videoEl - Video element with active camera stream
 * @param {Object} [opts]
 * @param {number} [opts.fps=15]      - Target frames per second
 * @param {number} [opts.quality=0.7] - JPEG quality (0-1)
 * @param {number} [opts.maxWidth=1280] - Max frame width (downscales if larger)
 */
export function streamFramesHttp(url, videoEl, opts = {}) {
  const fps = opts.fps ?? 15;
  const quality = opts.quality ?? 0.7;
  const maxWidth = opts.maxWidth ?? 1280;

  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  let stopped = false;
  let timer = null;
  let inFlight = false;

  function captureAndSend() {
    if (stopped || inFlight) return;

    const vw = videoEl.videoWidth || 640;
    const vh = videoEl.videoHeight || 480;
    let w = vw, h = vh;
    if (w > maxWidth) {
      h = Math.round(h * (maxWidth / w));
      w = maxWidth;
    }

    canvas.width = w;
    canvas.height = h;
    ctx.drawImage(videoEl, 0, 0, w, h);

    canvas.toBlob(
      (blob) => {
        if (!blob || stopped) return;
        inFlight = true;
        fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'image/jpeg' },
          credentials: 'include',
          body: blob,
        })
          .then((res) => {
            if (res.status === 401 && !stopped) {
              stopped = true;
              if (timer) { clearInterval(timer); timer = null; }
              window.dispatchEvent(new CustomEvent('auth:expired'));
            }
          })
          .catch(() => {})
          .finally(() => { inFlight = false; });
      },
      'image/jpeg',
      quality,
    );
  }

  timer = setInterval(captureAndSend, Math.round(1000 / fps));

  return {
    stop() {
      stopped = true;
      if (timer) { clearInterval(timer); timer = null; }
    },
  };
}
