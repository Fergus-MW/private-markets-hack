import test from 'node:test';
import assert from 'node:assert/strict';
import { counts, gatePage, picker } from '../src/dashboard-render.js';

const check = (id, tier, status, amount = 0, detail = '') =>
  ({ id, tier, status, name: `Check ${id}`, who: amount ? 'Investor A' : '', amount, detail });
const terms = {
  run_id: 'r'.repeat(64), gate: 'terms', mode: 'terms', as_of: '2026-06-30', status: 'completed',
  started_at: '2026-09-06T09:00:00+00:00', amount_at_stake: 22149.55,
  inputs: { draft: { artifact_id: 'draft-id', filename: 'schedule.xlsx', sha256: 'abcdef1234' }, terms: { artifact_id: 'terms-id', filename: 'terms.csv', sha256: '9876543210' } },
  artifacts: { report: 'report-id' },
  checks: [check('TC03', 'a', 'FAIL', 22149.55, 'unfunded_overstated_by=22149.55'), check('TC08', 'b', 'PASS'), check('TC00', 'a', 'DECISION', 0, 'no register row')],
};
const arithmetic = { ...terms, run_id: 'n'.repeat(64), mode: 'arithmetic-only', amount_at_stake: 0,
  started_at: '2026-09-06T08:00:00+00:00', inputs: { draft: terms.inputs.draft }, checks: [check('TC08', 'b', 'PASS'), check('TC00', 'a', 'SKIPPED')] };
const project = { key: 'p'.repeat(64), name: 'Kestrel Q2', quarter: '2026-Q2' };

test('summary counts findings, decisions and applicable checks independently', () => {
  assert.deepEqual(counts(terms), { findings: 1, decisions: 1, passes: 1, run: 3, amount: 22149.55 });
  assert.equal(counts(arithmetic).run, 1);
});

test('matching arithmetic and terms runs render as an ordered comparison', () => {
  const page = gatePage(project, terms, [terms, arithmetic]);
  assert.ok(page.indexOf('Without terms') < page.indexOf('With terms'));
  assert.match(page, /USD 22,149\.55/);
  assert.match(page, /Needs attention/);
});

test('real navigation and downloads are rendered without dead decision controls', () => {
  const page = gatePage(project, terms, [terms]);
  assert.match(page, /\/graphs\/p{64}/);
  assert.match(page, /\/artifacts\/draft-id/);
  assert.match(page, /\/artifacts\/report-id/);
  assert.doesNotMatch(page, /type="radio"|visual only/);
});

test('a blocked run has no misleading score', () => {
  const blocked = { ...terms, status: 'blocked', checks: [], reason: 'Ratification required' };
  const page = gatePage(project, blocked, [blocked]);
  assert.match(page, /Blocked/);
  assert.doesNotMatch(page, /qc-score/);
});

test('picker lists only projects and escapes content', () => {
  const page = picker([{ id: 'workspace', kind: 'workspace', name: 'Workspace', description: '' },
    { id: 'p'.repeat(64), kind: 'project', name: '<Kestrel>', description: '2026-Q2 · terms' }]);
  assert.match(page, /\/dashboard\/p{64}/);
  assert.doesNotMatch(page, /Workspace|<Kestrel>/);
  assert.match(page, /&lt;Kestrel&gt;/);
});
