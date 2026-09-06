import './style.css';
import { createGraph } from './graph.js';

if (/^\/graphs(?:\/|$)/.test(location.pathname)) {
  const { mountGraphs } = await import('./graphs.js');
  await mountGraphs();
} else if (/^\/dashboard(?:\/|$)/.test(location.pathname)) {
  const { mountDashboard } = await import('./dashboard.js');
  await mountDashboard();
} else {

const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
let graph;
try { graph = createGraph(document.querySelector('#graph')); }
catch { /* Connection remains usable without WebGL. */ }
graph?.setPaused(reduced.matches);
reduced.addEventListener('change', (event) => graph?.setPaused(event.matches));
if (import.meta.hot) import.meta.hot.dispose(() => graph?.dispose());

const messages = {
  setup: 'Google sign-in is not configured for this workspace yet. Ask your workspace administrator to enable it.',
  denied: 'Connection cancelled. You can connect whenever you’re ready.',
  permissions: 'Please allow both Gmail and Google Drive access to connect your workspace.',
  account: 'This Google account hasn’t been enabled for this workspace. Please use your invited account.',
  expired: 'Your connection request expired. Please try again.',
  failed: 'We couldn’t finish connecting your workspace. Please try again.',
};
const status = document.querySelector('#auth-status');
const button = document.querySelector('#connect-google');
let configured = true;
const params = new URLSearchParams(location.search);
const result = params.get('connection');
if (result && result !== 'success') status.textContent = messages[result] || messages.failed;
if (result) history.replaceState({}, '', location.pathname);
try {
  const response = await fetch('/api/session', { credentials: 'same-origin' });
  if (response.ok) {
    const session = await response.json();
    configured = session.configured !== false;
    if (session.connected && (!result || result === 'success')) {
      // Just signed up, or still ingesting: watch the real run instead of a
      // dead "connected" button. A finished run keeps the original page.
      const { mountIngesting } = await import('./ingesting.js');
      const shell = document.querySelector('.shell');
      if (result === 'success') { await mountIngesting(shell); }
      else {
        const report = await fetch('/api/ingestion/status', { credentials: 'same-origin' })
          .then(response => (response.ok ? response.json() : null)).catch(() => null);
        if (report && !report.done) await mountIngesting(shell);
        else {
          document.querySelector('#button-label').textContent = 'Google connected';
          button.setAttribute('aria-label', 'Reconnect your Google account');
        }
      }
    }
  }
} catch { /* The connect endpoint provides a recoverable error if unavailable. */ }
button.addEventListener('click', (event) => {
  if (!configured) {
    event.preventDefault();
    status.textContent = messages.setup;
    return;
  }
  button.setAttribute('aria-disabled', 'true');
  document.querySelector('#button-label').textContent = 'Connecting…';
});
window.addEventListener('pageshow', (event) => { if (event.persisted) location.reload(); });
}
