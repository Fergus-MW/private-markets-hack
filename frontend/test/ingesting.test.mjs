import test from 'node:test';
import { randomBytes } from 'node:crypto';
import assert from 'node:assert/strict';
import { connectorId, seal } from '../server/auth.mjs';
import { createMail } from '../server/mail.mjs';
import { fraction, traceLines } from '../src/ingesting.js';

const KEY = randomBytes(32);
const EMAIL = 'person@example.com';
const config = { key: KEY, origin: 'https://app.example.com' };
const url = new URL('https://app.example.com/api/ingestion/status');

function response() {
  return {
    writeHead(status) { this.status = status; },
    end(body) { this.body = body ? JSON.parse(body) : undefined; },
  };
}
function session(payload = { kind: 'connection', email: EMAIL, connector: connectorId(EMAIL) }) {
  return 'connection=' + seal({ expires: Date.now() + 60_000, ...payload }, KEY);
}
const running = [{ provider: 'drive', status: 'running', checked: 20, counts: { ingested: 20 } },
  { provider: 'gmail', status: 'queued', checked: 0, counts: {} }];

test('progress never reaches full until a provider actually finishes', () => {
  assert.equal(fraction([]), 0);
  assert.equal(fraction([{ provider: 'drive', status: 'queued', checked: 0, counts: {} }]), 0);
  // A busy provider approaches its share but must not claim it.
  const busy = fraction([{ provider: 'drive', status: 'running', checked: 100_000, counts: {} }]);
  assert.ok(busy > 0.9 && busy < 1, `expected just under 1, got ${busy}`);
  assert.equal(fraction([{ provider: 'drive', status: 'completed', checked: 3, counts: {} }]), 1);
  // More work checked must never move the bar backwards.
  assert.ok(fraction(running) > fraction([{ ...running[0], checked: 1 }, running[1]]));
});

test('every terminal provider state counts as finished work, not stalled', () => {
  for (const status of ['completed', 'partial', 'failed', 'empty']) {
    assert.equal(fraction([{ provider: 'drive', status, checked: 0, counts: {} }]), 1, status);
  }
});

test('traces are emitted only for real reported changes', () => {
  assert.deepEqual(traceLines([], { providers: running }), ['Google Drive: running', 'Gmail: queued']);
  // Identical report: nothing new to say.
  assert.deepEqual(traceLines(running, { providers: running }), []);
  const advanced = [{ ...running[0], checked: 25 }, running[1]];
  assert.deepEqual(traceLines(running, { providers: advanced }), ['Google Drive: 25 items checked']);
  // A newly seen count category is announced once, with its friendly name.
  const withFailure = [{ ...running[0], counts: { ingested: 20, failed: 1 } }, running[1]];
  assert.deepEqual(traceLines(running, { providers: withFailure }), ['Google Drive: first failed item (1)']);
});

test('status is scoped to the signed-in session and rejects everything else', async () => {
  const calls = [];
  const mail = createMail(config, { backend: 'https://mail.example.com', request: async options => {
    calls.push(options); return { status: 200, data: { state: 'running', done: false } };
  } });

  assert.equal(await mail.status({ method: 'GET', headers: {} }, response(), new URL('https://app.example.com/other')), false);

  let res = response();
  await mail.status({ method: 'GET', headers: {} }, res, url);
  assert.equal(res.status, 401, 'no cookie must not reach the backend');

  res = response();
  await mail.status({ method: 'GET', headers: { cookie: session({ kind: 'connection', email: EMAIL, connector: 'forged' }) } }, res, url);
  assert.equal(res.status, 401, 'connector must match the session email');

  res = response();
  await mail.status({ method: 'POST', headers: { cookie: session() } }, res, url);
  assert.equal(res.status, 405);

  assert.deepEqual(calls, [], 'no rejected request reached the backend');

  res = response();
  await mail.status({ method: 'GET', headers: { cookie: session() } }, res, url);
  assert.equal(res.status, 200);
  assert.equal(res.body.state, 'running');
  // The browser never chooses the address; the sealed session does.
  assert.deepEqual(calls[0].data, { email: EMAIL });
});

test('an unavailable backend reports unavailable rather than a fake run', async () => {
  const mail = createMail(config, { backend: 'https://mail.example.com', request: async () => { throw new Error('down'); } });
  const res = response();
  await mail.status({ method: 'GET', headers: { cookie: session() } }, res, url);
  assert.equal(res.status, 503);
  assert.equal(res.body.detail, 'Ingestion progress unavailable');
});
