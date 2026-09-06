const escape = value => String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);

const LABELS = { drive: 'Google Drive', gmail: 'Gmail' };
// Every terminal state except "completed" leaves the graph unready. advance()
// reports "unknown" rather than guessing, so the UI must not round it up.
const READY = 'completed';
const COUNTS = { ingested: 'ingested', unchanged_ingested: 'already up to date', unchanged: 'unchanged',
  archived: 'archived', archive_only: 'archived', metadata_only: 'metadata only', shortcut: 'shortcuts', failed: 'failed' };

/** Real progress only: a provider reaches its full share when it actually
 *  finishes. While running it approaches, but never touches, that share. */
export function fraction(providers) {
  if (!providers.length) return 0;
  const share = 1 / providers.length;
  return providers.reduce((total, provider) => {
    if (['completed', 'partial', 'failed', 'empty'].includes(provider.status)) return total + share;
    if (provider.status === 'queued') return total;
    return total + share * (1 - 1 / (1 + provider.checked / 20));
  }, 0);
}

/** One trace line per real change the backend reported. No invented steps. */
export function traceLines(previous, report) {
  const lines = [];
  for (const provider of report.providers) {
    const before = previous.find(item => item.provider === provider.provider);
    const name = LABELS[provider.provider] || provider.provider;
    if (!before || before.status !== provider.status) lines.push(`${name}: ${provider.status}`);
    if (before && provider.checked > before.checked) {
      lines.push(`${name}: ${provider.checked} items checked`);
    }
    // Only against a known previous state: a first sighting would otherwise
    // announce every category at once and bury the real status line.
    if (before) {
      const seen = new Set(Object.keys(before.counts || {}));
      for (const [kind, count] of Object.entries(provider.counts || {})) {
        if (!seen.has(kind)) lines.push(`${name}: first ${COUNTS[kind] || kind} item (${count})`);
      }
    }
  }
  return lines;
}

export async function mountIngesting(shell, { poll = 3000, fetcher = fetch, navigate = path => { location.href = path; } } = {}) {
  const nav = shell.querySelector('nav')?.outerHTML || '';
  shell.innerHTML = `${nav}<main><section class="ingesting" aria-labelledby="ingest-headline">
    <p class="eyebrow">READING YOUR DOCUMENTS</p>
    <h1 id="ingest-headline">Building your terms register.</h1>
    <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
    <div class="progress-meta"><span id="progress-percent">0%</span><span id="progress-state">Starting…</span></div>
    <p class="ingest-summary" id="ingest-summary" role="status" aria-live="polite">Reading your Drive and Gmail…</p>
    <ul class="provider-list" id="provider-list"></ul>
    <p class="eyebrow trace-heading">AGENT ACTIVITY</p>
    <ol class="trace-log" id="trace-log" aria-live="polite"><li class="trace-line">Requested a read of Drive and Gmail.</li></ol>
    <div id="ingest-action"></div>
  </section></main>`;

  const fill = shell.querySelector('#progress-fill');
  const percent = shell.querySelector('#progress-percent');
  const stateLabel = shell.querySelector('#progress-state');
  const summary = shell.querySelector('#ingest-summary');
  const list = shell.querySelector('#provider-list');
  const log = shell.querySelector('#trace-log');
  const action = shell.querySelector('#ingest-action');

  let providers = [];
  let stopped = false;
  let timer;

  const trace = line => {
    const item = document.createElement('li');
    item.className = 'trace-line';
    item.textContent = `${new Date().toLocaleTimeString()} · ${line}`;
    log.append(item);
    while (log.children.length > 40) log.firstElementChild.remove();
    log.scrollTop = log.scrollHeight;
  };

  const paint = report => {
    const value = fraction(report.providers);
    const shown = report.done && report.state === READY ? 100 : Math.min(99, Math.round(value * 100));
    fill.style.width = `${shown}%`;
    percent.textContent = `${shown}%`;
    stateLabel.textContent = report.done ? report.state : 'Ingesting…';
    summary.textContent = report.summary;
    list.innerHTML = report.providers.map(provider => {
      const detail = Object.entries(provider.counts || {})
        .map(([kind, count]) => `${count} ${escape(COUNTS[kind] || kind)}`).join(', ');
      return `<li class="provider-row"><span class="provider-name">${escape(LABELS[provider.provider] || provider.provider)}</span>
        <span class="provider-status status-${escape(provider.status)}">${escape(provider.status)}</span>
        <span class="provider-detail">${detail || `${provider.checked} checked`}</span></li>`;
    }).join('');
    for (const line of traceLines(providers, report)) trace(line);
    providers = report.providers;
  };

  const finish = report => {
    stopped = true;
    if (report.state === READY) {
      trace('Terms register ready.');
      action.innerHTML = '<a class="google-button" id="view-graph" href="/graphs">View your terms register</a>';
      timer = setTimeout(() => navigate('/graphs'), 1500);
      return;
    }
    // Not ready: say so plainly and offer the graph only as a partial view.
    trace(`Finished without a ready graph (${report.state}).`);
    action.innerHTML = '<a class="retry-link" href="/graphs">See what was read so far</a>';
  };

  const tick = async () => {
    try {
      const response = await fetcher('/api/ingestion/status', { credentials: 'same-origin' });
      if (response.status === 401) {
        stopped = true;
        summary.textContent = 'Your session expired. Reconnect your Google account to continue.';
        action.innerHTML = '<a class="google-button" href="/api/auth/google/start">Connect to Google</a>';
        return;
      }
      if (!response.ok) throw new Error('unavailable');
      const report = await response.json();
      paint(report);
      if (report.done) { finish(report); return; }
    } catch {
      // A failed poll is not a failed ingestion; keep watching and say so once.
      if (!summary.dataset.warned) { summary.dataset.warned = '1'; trace('Progress temporarily unavailable; still watching.'); }
    }
    if (!stopped) timer = setTimeout(tick, poll);
  };

  await tick();
  return () => { stopped = true; clearTimeout(timer); };
}
