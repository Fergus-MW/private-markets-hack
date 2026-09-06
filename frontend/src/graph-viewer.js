import Sigma from 'sigma';
import FA2Layout from 'graphology-layout-forceatlas2/worker';
import { buildGraph } from './graph-data.js';

export function mountViewer(shell, data, reduced) {
  const graph = buildGraph(data);
  const container = shell.querySelector('#graph-canvas');
  let renderer;
  try {
    renderer = new Sigma(graph, container, {
      hideEdgesOnMove: true, hideLabelsOnMove: true, labelColor: { color: '#f5f1ec' },
      labelFont: 'DM Sans, sans-serif', labelSize: 12, labelDensity: .08, labelRenderedSizeThreshold: 6,
      defaultEdgeColor: '#34303f', stagePadding: 70, minCameraRatio: .02, maxCameraRatio: 5,
    });
  } catch {
    container.replaceChildren();
    throw new Error('The graph needs WebGL. Enable hardware acceleration in your browser and try again.');
  }
  shell.insertAdjacentHTML('beforeend', '<aside class="node-detail glass" hidden aria-live="polite"><button class="detail-close" aria-label="Close node details">×</button><p class="eyebrow" id="node-kind"></p><h2 id="node-name"></h2><p id="node-connections"></p></aside><div class="viewer-footer"><p class="graph-count glass"></p><div class="camera-controls glass" aria-label="Graph controls"><button aria-label="Zoom in">+</button><button aria-label="Zoom out">−</button><button aria-label="Fit graph">⤢</button></div></div>');
  shell.querySelector('.graph-count').textContent = `${graph.order.toLocaleString()} nodes · ${graph.size.toLocaleString()} connections`;
  const detail = shell.querySelector('.node-detail');
  function select(node) {
    detail.hidden = false;
    shell.querySelector('#node-kind').textContent = graph.getNodeAttribute(node, 'kind').replaceAll('_', ' ');
    shell.querySelector('#node-name').textContent = graph.getNodeAttribute(node, 'label');
    shell.querySelector('#node-connections').textContent = `${graph.degree(node).toLocaleString()} connections`;
    const neighbors = new Set(graph.neighbors(node));
    renderer.setSetting('nodeReducer', (key, attrs) => key === node || neighbors.has(key)
      ? { ...attrs, highlighted: key === node } : { ...attrs, color: '#302c35', label: '', zIndex: 0 });
    renderer.setSetting('edgeReducer', (key, attrs) => ({ ...attrs, hidden: !graph.hasExtremity(key, node), color: '#94789e' }));
  }
  function clear() { detail.hidden = true; renderer.setSetting('nodeReducer', null); renderer.setSetting('edgeReducer', null); }
  renderer.on('clickNode', ({ node }) => select(node));
  renderer.on('clickStage', clear);
  detail.querySelector('button').onclick = clear;
  const camera = renderer.getCamera();
  const options = { duration: reduced ? 0 : 200 };
  const controls = shell.querySelectorAll('.camera-controls button');
  controls[0].onclick = () => camera.animatedZoom(options);
  controls[1].onclick = () => camera.animatedUnzoom(options);
  controls[2].onclick = () => camera.animatedReset(options);
  const onKey = event => { if (event.key === 'Escape') clear(); };
  window.addEventListener('keydown', onKey);
  let layout, timer;
  if (graph.size && !data.nodes.every(node => Number.isFinite(node.x) && Number.isFinite(node.y))) {
    try {
      layout = new FA2Layout(graph, { settings: { barnesHutOptimize: true, gravity: 1, scalingRatio: 10, slowDown: 5 } });
      // Reduced-motion users see only the settled layout; computation stays in a worker.
      if (reduced) container.style.opacity = '0';
      layout.start();
      timer = setTimeout(() => { layout.stop(); container.style.opacity = ''; }, 2500);
    } catch { layout?.kill(); container.style.opacity = ''; /* Deterministic initial positions remain usable. */ }
  }
  return () => { clearTimeout(timer); layout?.kill(); renderer.kill(); container.style.opacity = ''; window.removeEventListener('keydown', onKey); };
}
