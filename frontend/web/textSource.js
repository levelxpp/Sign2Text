// text source
export function createTextSource(options = {}) {
  const { lang = 'en-US', onLine, onStatus } = options;
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition;
  let running = false;
  let paused  = false;
  let fallbackTimer;

  // emit line
  function emitLine(text) {
    onLine?.(text);
  }
  // emit status
  function emitStatus(status) {
    onStatus?.(status);
  }

  // setup recognition
  function setup() {
    recognition = new SpeechRec();
    recognition.lang = lang;
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.onresult = (evt) => {
      for (let i = evt.resultIndex; i < evt.results.length; i++) {
        const res = evt.results[i];
        if (res.isFinal) {
          const text = res[0]?.transcript?.trim();
          if (text) emitLine(text);
        }
      }
    };
    recognition.onstart = () => emitStatus('listening');
    recognition.onerror = (e) => {
      console.error('Speech recognition error:', e);
      emitStatus('error');
    };
    recognition.onend = () => {
      if (running && !paused) {
        try { recognition.start(); } catch {}
      }
    };
  }

  // start
  function start() {
    if (running) return;
    running = true;
    paused  = false;
    if (SpeechRec) {
      if (!recognition) setup();
      try {
        recognition.start();
        emitStatus('listening');
      } catch (e) {
        console.warn('Speech recognition start failed:', e);
      }
    } else {
      const demo = [
        'Demo mode: Web Speech API not supported.',
        'Captions will display sample lines every few seconds.',
        'Try using Chrome or Edge on a desktop to see live speech captions.',
      ];
      let idx = 0;
      fallbackTimer = setInterval(() => {
        emitLine(demo[idx % demo.length]);
        idx++;
      }, 2000);
      emitStatus('demo');
    }
  }

  // pause
  function pause() {
    if (!running || paused) return;
    paused = true;
    if (SpeechRec) {
      try { recognition.stop(); emitStatus('paused'); } catch {}
    } else {
      clearInterval(fallbackTimer);
      fallbackTimer = null;
    }
  }

  // resume
  function resume() {
    if (!running || !paused) return;
    paused = false;
    if (SpeechRec) {
      try { recognition.start(); emitStatus('listening'); } catch {}
    } else {
      const demo = [
        'Demo mode: Web Speech API not supported.',
        'Captions will display sample lines every few seconds.',
        'Try using Chrome or Edge on a desktop to see live speech captions.',
      ];
      let idx = 0;
      fallbackTimer = setInterval(() => {
        emitLine(demo[idx % demo.length]);
        idx++;
      }, 2000);
      emitStatus('demo');
    }
  }

  // stop
  function stop() {
    if (!running) return;
    running = false;
    paused  = false;
    if (SpeechRec) {
      try { recognition.stop(); emitStatus('stopped'); } catch {}
    } else {
      clearInterval(fallbackTimer);
      fallbackTimer = null;
    }
  }

  return { start, pause, resume, stop };
}
