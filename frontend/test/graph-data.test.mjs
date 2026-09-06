import test from 'node:test';
import assert from 'node:assert/strict';
import { buildGraph } from '../src/graph-data.js';

test('viewer preserves parallel directed relationships and excludes missing endpoints', () => {
  const graph = buildGraph({ nodes: [{ id: 'a', label: '<Fund>', kind: 'fund' }, { id: 'b', label: 'Manager', kind: 'company' }],
    edges: [{ id: 'one', source: 'a', target: 'b', label: 'manages' }, { id: 'two', source: 'a', target: 'b', label: 'invests_in' },
      { id: 'missing', source: 'a', target: 'absent', label: 'mentions' }] });
  assert.equal(graph.order, 2);
  assert.equal(graph.size, 2);
  assert.equal(graph.getNodeAttribute('a', 'label'), '<Fund>');
  assert.equal(graph.hasDirectedEdge('a', 'b'), true);
  assert.equal(graph.hasDirectedEdge('b', 'a'), false);
});

test('viewer retains supplied positions and seeds missing positions deterministically', () => {
  const data = { nodes: [{ id: 'a', label: 'A', kind: 'fund', x: 12, y: -3 }, { id: 'b', label: 'B', kind: 'file' }], edges: [] };
  const first = buildGraph(data), second = buildGraph(data);
  assert.equal(first.getNodeAttribute('a', 'x'), 12);
  assert.equal(first.getNodeAttribute('a', 'y'), -3);
  assert.deepEqual(first.getNodeAttributes('b'), second.getNodeAttributes('b'));
  assert.ok(Number.isFinite(first.getNodeAttribute('b', 'x')));
});
