import test from 'node:test';
import assert from 'node:assert/strict';
import { Readable } from 'node:stream';
import { createMail } from '../server/mail.mjs';

function response() {
  return { writeHead(status) { this.status = status; }, end() {} };
}
function request(body, headers = {}) {
  const req = Readable.from([Buffer.from(body)]);
  req.method = 'POST'; req.headers = headers;
  return req;
}
const url = new URL('https://app.example.com/api/agentmail/webhook');
test('webhook preserves signed raw bytes and limits forwarded headers', async () => {
  let forwarded;
  const mail = createMail({}, { backend: 'https://mail.example.com', request: async options => {
    forwarded = options; return { status: 202 };
  } });
  const raw = '{ "event": "hello" }\n';
  const res = response();
  await mail.webhook(request(raw, { 'svix-id': 'one', 'svix-timestamp': '123', 'svix-signature': 'sig', cookie: 'private' }), res, url);
  assert.equal(res.status, 202);
  assert.equal(forwarded.data.toString(), raw);
  assert.equal(forwarded.headers.cookie, undefined);
});
test('missing signatures and oversized webhooks never reach backend', async () => {
  const mail = createMail({}, { backend: 'https://mail.example.com', request: async () => { throw new Error('must not call'); } });
  let res = response();
  await mail.webhook(request('{}'), res, url);
  assert.equal(res.status, 401);
  res = response();
  await mail.webhook(request('x'.repeat(512 * 1024 + 1), { 'svix-id': 'a', 'svix-timestamp': 'b', 'svix-signature': 'c' }), res, url);
  assert.equal(res.status, 413);
});
test('webhook propagates retriable backend failure', async () => {
  const mail = createMail({}, { backend: 'https://mail.example.com', request: async () => { throw new Error('down'); } });
  const res = response();
  await mail.webhook(request('{}', { 'svix-id': 'a', 'svix-timestamp': 'b', 'svix-signature': 'c' }), res, url);
  assert.equal(res.status, 503);
});
