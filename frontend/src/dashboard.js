import './dashboard.css';
import { escape, gatePage, picker, request } from './dashboard-render.js';

export async function mountDashboard() {
  let controller;
  const shell = document.querySelector('.shell');
  // Keep the site nav, the way the graphs route does: otherwise the dashboard is a
  // dead end with no way back to the rest of the app.
  const nav = shell.querySelector('nav')?.outerHTML || '';
  const navigate = path => { history.pushState({}, '', path); render(); };
  const onClick = event => {
    const link = event.target.closest('a[data-route]');
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); navigate(link.getAttribute('href'));
  };
  shell.addEventListener('click', onClick);
  window.addEventListener('popstate', render);

  async function render() {
    controller?.abort(); controller = new AbortController();
    const { signal } = controller;
    const projectId = location.pathname.replace(/\/$/, '').split('/')[2];
    document.body.classList.add('gate-view');
    document.body.dataset.theme = projectId ? '' : 'dark';
    document.title = projectId ? 'QC gate' : 'QC gate · your projects';
    shell.innerHTML = '<main><div class="wrap"><div class="notice" role="status">Loading…</div></div></main>';
    const message = shell.querySelector('[role=status]');
    try {
      if (!projectId) {
        const data = await request('/api/graph/views', signal);
        if (signal.aborted) return;
        shell.innerHTML = `<main>${picker(data.graphs, nav)}</main>`;
        return;
      }
      const data = await request(`/api/projects/${encodeURIComponent(projectId)}/dashboard`, signal);
      if (signal.aborted) return;
      const project = { ...data.project, key: projectId };
      if (!data.runs.length) {
        shell.innerHTML = `<main><div class="wrap"><div class="gate-bar"><a href="/dashboard" data-route>← All projects</a></div><h1>${escape(project.name)}</h1><div class="notice">No gate has run on this project yet. Ask the agent to run the QC gate, then this page fills in.</div></div></main>`;
        return;
      }
      const wanted = new URLSearchParams(location.search).get('run');
      // Default to the newest run that actually produced checks: a blocked run is
      // worth reading, but it is not what someone opening the gate page came for.
      const run = data.runs.find(item => item.run_id === wanted)
        || data.runs.find(item => item.checks.length && item.mode !== 'arithmetic-only')
        || data.runs.find(item => item.checks.length) || data.runs[0];
      const options = data.runs.map(item => `<option value="${escape(item.run_id)}"${item.run_id === run.run_id ? ' selected' : ''}>${escape(item.gate === item.mode ? item.mode : item.gate + ' · ' + item.mode)} · ${escape(String(item.started_at).slice(0, 16).replace('T', ' '))}${item.status === 'completed' ? '' : ' · ' + escape(item.status)}</option>`).join('');
      shell.innerHTML = `<main>${gatePage(project, run, data.runs)}</main>`;
      if (data.runs.length > 1) {
        shell.querySelector('.gate-bar').insertAdjacentHTML('beforeend',
          `<label class="muted">Run <select id="run-picker">${options}</select></label>`);
        shell.querySelector('#run-picker').addEventListener('change', event => {
          navigate(`${location.pathname}?run=${encodeURIComponent(event.target.value)}`);
        });
      }
    } catch (error) {
      if (signal.aborted) return;
      message.innerHTML = `<b>${error.status === 401 ? 'Your results, privately connected.' : 'Unable to open the QC dashboard'}</b>`
        + `<p style="margin:6px 0 0">${escape(error.message)}</p>`
        + (error.status === 401 ? '<p style="margin:10px 0 0"><a class="btn" href="/api/auth/google/start">Connect to Google</a></p>' : '');
    }
  }
  if (import.meta.hot) import.meta.hot.dispose(() => { controller?.abort(); window.removeEventListener('popstate', render); shell.removeEventListener('click', onClick); });
  await render();
}
