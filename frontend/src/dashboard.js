import './dashboard.css';
import { escape, gatePage, picker, request } from './dashboard-render.js';

export async function mountDashboard() {
  let controller;
  const shell = document.querySelector('.shell');
  const nav = shell.querySelector('nav')?.outerHTML || '';
  const navigate = path => { history.pushState({}, '', path); render(); };
  const onClick = event => {
    const link = event.target.closest('a[data-route]');
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(link.getAttribute('href'));
  };
  shell.addEventListener('click', onClick);
  window.addEventListener('popstate', render);

  async function render() {
    controller?.abort();
    controller = new AbortController();
    const { signal } = controller;
    const projectId = location.pathname.replace(/\/$/, '').split('/')[2];
    document.body.classList.add('gate-view');
    document.title = projectId ? 'QC result · 60x' : 'QC · 60x';
    shell.innerHTML = `${nav}<main class="qc-main"><div class="qc-loading" role="status">Loading…</div></main>`;
    const main = shell.querySelector('main');
    try {
      if (!projectId) {
        const data = await request('/api/graph/views', signal);
        if (!signal.aborted) main.innerHTML = picker(data.graphs);
        return;
      }
      const data = await request(`/api/projects/${encodeURIComponent(projectId)}/dashboard`, signal);
      if (signal.aborted) return;
      const project = { ...data.project, key: projectId };
      if (!data.runs.length) {
        main.innerHTML = `<div class="qc-page"><a class="qc-back" href="/dashboard" data-route>← Projects</a><p class="eyebrow">${escape(project.quarter || 'QC')}</p><h1>${escape(project.name)}</h1><div class="qc-empty">No QC runs yet.</div></div>`;
        return;
      }
      const wanted = new URLSearchParams(location.search).get('run');
      const run = data.runs.find(item => item.run_id === wanted)
        || data.runs.find(item => item.checks.length && item.mode !== 'arithmetic-only')
        || data.runs.find(item => item.checks.length)
        || data.runs[0];
      main.innerHTML = gatePage(project, run, data.runs);
      main.querySelector('#run-picker')?.addEventListener('change', event => {
        navigate(`${location.pathname}?run=${encodeURIComponent(event.target.value)}`);
      });
    } catch (error) {
      if (signal.aborted) return;
      main.innerHTML = `<div class="qc-message" role="alert"><h1>${error.status === 401 ? 'Connect to view QC.' : 'QC is unavailable.'}</h1><p>${escape(error.message)}</p>${error.status === 401 ? '<a class="google-button" href="/api/auth/google/start">Connect to Google</a>' : '<button class="retry-button" type="button">Try again</button>'}</div>`;
      main.querySelector('button')?.addEventListener('click', render, { once: true });
    }
  }

  if (import.meta.hot) import.meta.hot.dispose(() => {
    controller?.abort();
    window.removeEventListener('popstate', render);
    shell.removeEventListener('click', onClick);
  });
  await render();
}
