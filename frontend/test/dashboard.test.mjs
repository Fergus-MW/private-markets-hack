import test from 'node:test';
import assert from 'node:assert/strict';
import { counts, gatePage, picker } from '../src/dashboard-render.js';

const check = (id, tier, status, amount = 0, detail = '') =>
  ({ id, tier, status, name: 'Check ' + id, who: amount ? 'Trentcombe Fund Investors LLC' : '', amount, detail });

const terms = {
  run_id: 'r'.repeat(64), gate: 'terms', mode: 'terms', as_of: '2026-06-30', status: 'completed',
  started_at: '2026-09-06T09:00:00+00:00', turn: 1, entity: 'Kestrel Lammwick Co-Invest LP',
  terms_rows_in_force: 19, amount_at_stake: 22149.55, runtime: { python: '3.12.6' },
  inputs: { draft: { filename: 'q2_schedule.xlsx', sha256: 'abcdef1234' }, terms: { filename: 'terms.csv', sha256: '9876543210' } },
  artifacts: { report: 'report-id' },
  checks: [check('TC03', 'a', 'FAIL', 22149.55, 'unfunded_overstated_by=22149.55'), check('TC01', 'b', 'FAIL', 900),
    check('TC08', 'b', 'PASS'), check('TC00', 'a', 'DECISION', 0, 'no register row')],
};
const arithmetic = {
  ...terms, run_id: 'n'.repeat(64), mode: 'arithmetic-only', amount_at_stake: 0,
  started_at: '2026-09-06T08:00:00+00:00', entity: '', terms_rows_in_force: 0,
  inputs: { draft: { filename: 'q2_schedule.xlsx', sha256: 'abcdef1234' } },
  checks: [check('TC08', 'b', 'PASS'), check('TC00', 'a', 'SKIPPED')],
};
const project = { key: 'p'.repeat(64), name: 'Kestrel Q2', quarter: '2026Q2' };

test('the scoreboard separates tiers, decisions and passes, and only tier a is money', () => {
  const k = counts(terms);
  assert.deepEqual([k.a, k.b, k.c, k.dec, k.passes, k.run, k.fails], [1, 1, 0, 1, 1, 4, 2]);
  assert.equal(k.amount, 22149.55);
  assert.equal(counts(arithmetic).run, 1); // SKIPPED checks are not "run"
});

test('an arithmetic-only run of the same draft becomes the no-brain comparison', () => {
  const page = gatePage(project, terms, [terms, arithmetic]);
  assert.match(page, /No brain/);
  assert.match(page, /Brain on/);
  // Labelled by mode and ordered no-brain first, whichever run the page was opened on.
  const columns = [...page.matchAll(/score-h">([^<]+)<span class="muted mono">([^<]+)</g)].map(m => [m[1].trim(), m[2]]);
  assert.deepEqual(columns, [['No brain', 'arithmetic-only'], ['Brain on', 'terms']]);
  assert.deepEqual([...gatePage(project, arithmetic, [terms, arithmetic])
    .matchAll(/score-h">([^<]+)<span class="muted mono">([^<]+)</g)].map(m => [m[1].trim(), m[2]]),
    [['No brain', 'arithmetic-only'], ['Brain on', 'terms']]);
  assert.match(page, /USD 22,149\.55/);
  assert.match(page, /19 facts in force/);
  // Turn history is per mode, as render_results.py does it: the arithmetic-only
  // run is the comparison column, not an earlier turn of the terms run.
  assert.match(page, /Turn 1<\/b> of this draft/);
  assert.doesNotMatch(gatePage(project, terms, [terms]), /No brain/);
});

test('passes and decisions are shown, not hidden, and evidence carries decision controls', () => {
  const page = gatePage(project, terms, [terms]);
  assert.match(page, /Passes <span class="sub">shown, not hidden/);
  assert.match(page, /Decisions owed/);
  assert.match(page, /name="d-TC03"/);
  assert.match(gatePage(project, arithmetic, [arithmetic]), /Not run in this mode/);
});

test('a blocked run is not counted as a turn that found nothing', () => {
  const blocked = { ...terms, run_id: 'b'.repeat(64), status: 'blocked', checks: [], reason: 'needs ratification' };
  const page = gatePage(project, terms, [terms, blocked]);
  assert.match(page, /turn 1: 2 findings/);
  assert.doesNotMatch(page, /turn 2/);
});

test('a blocked run says so instead of scoring an empty checklist', () => {
  // A blocked run never opened the draft, so the checker reported no entity either.
  const stopped = { ...terms, status: 'blocked', checks: [], entity: '', reason: 'A named reviewer must ratify terms' };
  // A loader run over the same draft must never be paired as the "brain on" half of
  // a terms comparison, and a run with no checks gets no scoreboard, turn or strip.
  const page = gatePage(project, { ...stopped, gate: 'loader', mode: 'loader' }, [stopped, arithmetic]);
  assert.match(page, /This run is blocked/);
  assert.match(page, /must ratify terms/);
  assert.doesNotMatch(page, /errors caught/);
  assert.doesNotMatch(page, /No brain|Brain on/);
  assert.doesNotMatch(page, /Turn \d/);
  assert.doesNotMatch(page, /checks run/);
  // The project is not the entity the checker read from the draft cover sheet.
  assert.match(page, /<b>Entity<\/b> —/);
});

test('rendered content is escaped and the picker lists only projects', () => {
  const hostile = { ...terms, entity: '<img src=x onerror=alert(1)>' };
  assert.doesNotMatch(gatePage(project, hostile, []), /<img src=x/);
  const menu = picker([{ id: 'workspace', kind: 'workspace', name: 'Your knowledge', description: '' },
    { id: 'p'.repeat(64), kind: 'project', name: 'Kestrel Q2', description: '2026Q2 · terms' }]);
  assert.match(menu, /\/dashboard\/p{64}/);
  assert.doesNotMatch(menu, /Your knowledge/);
});
