import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequest } from '../server/upstream.mjs';

function stubFetch(handler) {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return handler(url, options); };
  return { calls, restore: () => { globalThis.fetch = original; } };
}
const json = body => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });

test('a plain-http backend is called directly, with objects sent as JSON', async () => {
  const stub = stubFetch(() => json({ state: 'running' }));
  try {
    const result = await createRequest('http://ingestion:8080')({
      url: 'http://ingestion:8080/status', method: 'POST', data: { email: 'person@example.com' },
    });
    assert.equal(result.status, 200);
    assert.deepEqual(result.data, { state: 'running' });
    assert.equal(stub.calls[0].options.headers['Content-Type'], 'application/json');
    assert.equal(stub.calls[0].options.body, '{"email":"person@example.com"}');
  } finally { stub.restore(); }
});

test('binary responses stay Buffers and preset content types are preserved', async () => {
  const stub = stubFetch(() => new Response(Buffer.from([1, 2, 3]), { headers: { 'Content-Type': 'application/pdf' } }));
  try {
    const result = await createRequest('http://ingestion:8080')({
      url: 'http://ingestion:8080/doc', method: 'POST', responseType: 'arraybuffer',
      headers: { 'Content-Type': 'multipart/form-data' }, data: Buffer.from('raw'),
    });
    assert.ok(Buffer.isBuffer(result.data));
    assert.deepEqual([...result.data], [1, 2, 3]);
    // A Buffer body must go through untouched: signed bytes cannot be re-encoded.
    assert.equal(stub.calls[0].options.body.toString(), 'raw');
    assert.equal(stub.calls[0].options.headers['Content-Type'], 'multipart/form-data');
  } finally { stub.restore(); }
});

test('upstream errors surface as a status, not a thrown request', async () => {
  const stub = stubFetch(() => new Response('nope', { status: 503 }));
  try {
    const result = await createRequest('http://ingestion:8080')({ url: 'http://ingestion:8080/x' });
    assert.equal(result.status, 503);
  } finally { stub.restore(); }
});

test('an https backend never bypasses the IAM identity token', async () => {
  const stub = stubFetch(() => { throw new Error('plain fetch must not be used for Cloud Run'); });
  try {
    // No metadata server here, so this must fail in GoogleAuth rather than
    // quietly falling back to an unauthenticated call.
    await assert.rejects(createRequest('https://ingestion.run.app')({ url: 'https://ingestion.run.app/x' }));
    assert.deepEqual(stub.calls, []);
  } finally { stub.restore(); }
});
