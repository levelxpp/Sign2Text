// init captions
export function initCaptions(container) {
  if (!container) throw new Error('Captions container element is required');

  const statusEl = document.createElement('div');
  statusEl.className = 'status';
  container.appendChild(statusEl);

  const listEl = document.createElement('div');
  listEl.className = 'list';
  container.appendChild(listEl);

  const entries = [];
  const MAX_ENTRIES = 4000;

  function render() {
    listEl.innerHTML = '';

    for (const entry of entries) {
      const line = document.createElement('div');
      line.className = 'line';
      line.textContent = entry;
      listEl.appendChild(line);
    }

    listEl.scrollTop = listEl.scrollHeight;
  }

  function setStatus(text) {
    statusEl.textContent = text;
  }

  function appendLine(text) {
    const clean = String(text ?? '').trim();
    if (!clean) return;

    setLines([...entries, clean]);
  }

  function replaceLastLine(text) {
    const clean = String(text ?? '').trim();
    if (!clean) return;

    if (entries.length === 0) {
      appendLine(clean);
      return;
    }

    const next = entries.slice();
    next[next.length - 1] = clean;
    setLines(next);
  }

  function clearAll() {
    setLines([]);
  }

  function setTextSize(size) {
    container.style.setProperty('--caption-size', `${size}px`);
  }

  function getText() {
    return entries.join('\n');
  }

  function setLines(lines) {
    entries.length = 0;
    for (const line of lines || []) {
      const clean = String(line ?? '').trim();
      if (!clean) continue;
      entries.push(clean);
    }

    if (entries.length > MAX_ENTRIES) {
      entries.splice(0, entries.length - MAX_ENTRIES);
    }

    render();
  }

  function setText(text) {
    const raw = String(text ?? '');
    if (!raw.trim()) {
      setLines([]);
      return;
    }

    setLines(raw.split(/\r?\n/));
  }

  return { appendLine, replaceLastLine, clearAll, setStatus, setTextSize, getText, setLines, setText };
}
