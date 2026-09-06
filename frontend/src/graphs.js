import { createGraph } from './graph.js';

const escape = value => String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

async function request(path, signal) {
  const response = await fetch(path, { credentials: 'same-origin', signal });
  if (!response.ok) {
    const error = new Error(response.status === 401 ? 'Connect your Google account to explore your private knowledge graphs.'
      : response.status === 404 ? 'This graph is not available in your connected workspace.'
      : 'Your graphs are unavailable right now. Please try again.');
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export async function mountGraphs() {
  let cleanup = () => {};
  let controller;
  const shell = document.querySelector('.shell');
  const backdrop = document.querySelector('#graph');
  const shade = document.querySelector('.shade');
  const nav = shell.querySelector('nav').outerHTML;
  const navigate = path => { history.pushState({}, '', path); render(); };
  const onClick = event => {
    const link = event.target.closest('a[data-route]');
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); navigate(link.getAttribute('href'));
  };
  shell.addEventListener('click', onClick);
  window.addEventListener('popstate', render);

  async function render() {
    cleanup(); cleanup = () => {};
    controller?.abort(); controller = new AbortController();
    const { signal } = controller;
    const part = location.pathname.replace(/\/$/, '').split('/')[2];
    const viewer = Boolean(part);
    document.title = viewer ? 'Graph explorer · 60x' : 'Your knowledge graphs · 60x';
    document.body.classList.toggle('graph-view', viewer);
    backdrop.hidden = viewer; shade.hidden = viewer;
    shell.innerHTML = viewer
      ? '<div id="graph-canvas" aria-label="Interactive knowledge graph. Drag to pan and scroll to zoom."></div><header class="viewer-header"><a class="back-link glass" href="/graphs" data-route>← <span>All graphs</span></a><div class="viewer-title glass"><span class="eyebrow">KNOWLEDGE GRAPH</span><strong id="view-name">Loading graph…</strong></div></header><div id="graph-message" class="graph-message" role="status">Loading your graph…</div>'
      : `${nav}<main><section class="graph-selection"><p class="eyebrow">YOUR CONNECTED WORKSPACE</p><h1>Explore your knowledge.</h1><p class="intro">Every connection. A clearer picture.</p><div id="graph-menu" class="graph-menu" aria-label="Choose a knowledge graph"><p role="status">Loading your graphs…</p></div></section></main>`;
    if (!viewer) {
      try {
        const background = createGraph(backdrop);
        background.setPaused(reduced);
        cleanup = () => background.dispose();
      } catch { /* The menu also works without WebGL. */ }
    }
    const message = shell.querySelector(viewer ? '#graph-message' : '#graph-menu');
    try {
      if (!viewer) {
        const data = await request('/api/graph/views', signal);
        if (signal.aborted) return;
        message.innerHTML = data.graphs.length ? data.graphs.map(item => `<a class="graph-option" href="/graphs/${encodeURIComponent(item.id)}" data-route><span class="graph-icon" aria-hidden="true">${item.kind === 'workspace' ? '✳' : '⌘'}</span><span class="graph-option-copy"><span class="eyebrow">${item.kind === 'workspace' ? 'PERSONAL GRAPH' : 'PROJECT GRAPH'}</span><strong>${escape(item.name)}</strong><span>${escape(item.description)}</span></span><span class="option-arrow" aria-hidden="true">↗</span></a>`).join('') : '<p>No graphs yet. Connect your sources to get started.</p>';
      } else {
        const data = await request(`/api/graph/views/${encodeURIComponent(decodeURIComponent(part))}`, signal);
        if (signal.aborted) return;
        shell.querySelector('#view-name').textContent = data.name;
        document.title = `${data.name} · 60x`;
        if (!data.nodes.length) {
          message.innerHTML = '<h2>Your graph is taking shape.</h2><p>Connections will appear here as your sources are processed.</p>';
          return;
        }
        const { mountViewer } = await import('./graph-viewer.js');
        if (signal.aborted) return;
        cleanup = mountViewer(shell, data, reduced);
        message.remove();
      }
    } catch (error) {
      if (signal.aborted) return;
      message.innerHTML = `<h2>${error.status === 401 ? 'Your knowledge, privately connected.' : 'Unable to open graph'}</h2><p>${escape(error.message)}</p>${error.status === 401 ? '<a class="google-button" href="/api/auth/google/start">Connect to Google</a>' : '<button class="retry-button" type="button">Try again</button>'}`;
      message.querySelector('button')?.addEventListener('click', render, { once: true });
    }
  }
  if (import.meta.hot) import.meta.hot.dispose(() => { cleanup(); controller?.abort(); window.removeEventListener('popstate', render); shell.removeEventListener('click', onClick); });
  await render();
}
