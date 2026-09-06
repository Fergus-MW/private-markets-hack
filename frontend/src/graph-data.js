import Graph from 'graphology';

export const COLORS = { person: '#00c6ff', company: '#9b72ff', fund: '#ff00ae', project: '#ff8262', email: '#00dcc4', file: '#f6c66b', attachment: '#f6c66b', record: '#8c9aa9' };

export function buildGraph(data) {
  const graph = new Graph({ multi: true, type: 'directed' });
  data.nodes.forEach((node, index) => {
    const angle = index * Math.PI * (3 - Math.sqrt(5));
    const radius = Math.sqrt(index + 1);
    graph.addNode(node.id, {
      label: node.label, kind: node.kind, color: COLORS[node.kind] || '#ad91c8', size: 3,
      x: Number.isFinite(node.x) ? node.x : Math.cos(angle) * radius,
      y: Number.isFinite(node.y) ? node.y : Math.sin(angle) * radius,
    });
  });
  data.edges.forEach(edge => {
    if (graph.hasNode(edge.source) && graph.hasNode(edge.target)) {
      graph.addEdgeWithKey(edge.id, edge.source, edge.target, { label: edge.label, color: '#34303f', size: .5 });
    }
  });
  graph.forEachNode(node => graph.setNodeAttribute(node, 'size', Math.min(9, 2.5 + Math.log2(1 + graph.degree(node)))));
  return graph;
}
