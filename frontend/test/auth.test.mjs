import test from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { configuration, connectorId, createAuth, seal, unseal, SCOPES } from '../server/auth.mjs';

const config = {
  origin: 'https://app.example.com', secure: true, enabled: true, key: randomBytes(32),
  clientId: 'client', clientSecret: 'secret',
  project: 'demo-project', serviceAccount: 'connector@demo-project.iam.gserviceaccount.com',
};
function fixture(options = {}) {
  const saved = [];
  let verifier, tokenOptions, exchangeCount = 0;
  const oauth = {
    async generateCodeVerifierAsync() { return { codeVerifier: 'verifier', codeChallenge: 'challenge' }; },
    generateAuthUrl(values) { tokenOptions = values; return 'https://accounts.google.com/o/oauth2/v2/auth?' + new URLSearchParams({ ...values, scope: values.scope.join(' ') }); },
    async getToken(values) { exchangeCount++; verifier = values.codeVerifier; return { tokens: { access_token: 'access', refresh_token: 'refresh', id_token: 'identity', ...options.tokens } }; },
    async verifyIdToken() { return { getPayload: () => ({ email: options.email || 'person@example.com', email_verified: options.verified ?? true, nonce: options.nonce || tokenOptions.nonce }) }; },
    async getTokenInfo() { return { scopes: options.scopes || SCOPES }; },
  };
  const handle = createAuth(options.config || config, { oauth, connect: async (id, credentials) => {
    if (options.failSave) throw new Error('storage unavailable');
    saved.push({ id, credentials });
  }, onSignup: options.onSignup, logger: { warn() {}, error() {} } });
  async function request(path, cookie = '', headers = {}) {
    const response = { writeHead(status, headers) { this.status = status; this.headers = headers; }, end(body) { this.body = body; } };
    await handle({ headers: { cookie, ...headers } }, response, new URL(path, (options.config || config).origin));
    return response;
  }
  async function start() {
    const response = await request('/api/auth/google/start');
    return { response, cookie: response.headers['Set-Cookie'][0].split(';')[0], state: tokenOptions.state };
  }
  return { request, start, saved, get verifier() { return verifier; }, get exchangeCount() { return exchangeCount; } };
}

test('OAuth starts on the canonical callback host before issuing its transaction cookie', async () => {
  const f = fixture();
  const redirected = await f.request('/api/auth/google/start', '', { host: 'frontend-123.europe-west2.run.app' });
  assert.equal(redirected.headers.Location, 'https://app.example.com/api/auth/google/start');
  assert.deepEqual(redirected.headers['Set-Cookie'], []);
  assert.equal(f.exchangeCount, 0);

  const started = await f.request('/api/auth/google/start', '', { host: 'app.example.com' });
  assert.equal(new URL(started.headers.Location).hostname, 'accounts.google.com');
  assert.match(started.headers['Set-Cookie'][0], /^oauth_transaction=/);
});

test('one consent flow requests both integrations with offline access and PKCE', async () => {
  const f = fixture();
  const { response } = await f.start();
  const url = new URL(response.headers.Location);
  assert.deepEqual(url.searchParams.get('scope').split(' '), SCOPES);
  assert.equal(url.searchParams.get('access_type'), 'offline');
  assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
  assert.match(response.headers['Set-Cookie'][0], /HttpOnly; SameSite=Lax; Max-Age=600; Secure/);
  assert.ok(!response.headers['Set-Cookie'][0].includes('verifier'));
});
test('successful callback saves compatible credentials before reporting success', async () => {
  const f = fixture();
  const { cookie, state } = await f.start();
  const result = await f.request(`/api/auth/google/callback?code=code&state=${state}`, cookie);
  assert.equal(result.headers.Location, '/?connection=success');
  assert.equal(f.verifier, 'verifier');
  assert.equal(f.saved.length, 1);
  assert.deepEqual(f.saved.map(s => s.id), [connectorId('person@example.com')]);
  assert.equal(f.saved[0].credentials.type, 'authorized_user');
  assert.equal(f.saved[0].credentials.refresh_token, 'refresh');
  const sessionCookie = result.headers['Set-Cookie'][1].split(';')[0];
  const session = await f.request('/api/session', sessionCookie);
  assert.deepEqual(JSON.parse(session.body), { connected: true, email: 'person@example.com',
    connector: connectorId('person@example.com'), configured: true });
  assert.ok(!session.body.includes('refresh'));
});

test('verified signup queues welcome after saving the connector', async () => {
  const emails = [];
  const f = fixture({ onSignup: async email => { assert.equal(f.saved.length, 1); emails.push(email); } });
  const { cookie, state } = await f.start();
  const result = await f.request(`/api/auth/google/callback?code=code&state=${state}`, cookie);
  assert.equal(result.headers.Location, '/?connection=success');
  assert.deepEqual(emails, ['person@example.com']);
});

test('unverified account never queues a welcome', async () => {
  let called = false;
  const f = fixture({ verified: false, onSignup: async () => { called = true; } });
  const { cookie, state } = await f.start();
  await f.request(`/api/auth/google/callback?code=code&state=${state}`, cookie);
  assert.equal(called, false);
});
test('invalid state never exchanges a code or writes credentials', async () => {
  const f = fixture();
  const { cookie } = await f.start();
  const result = await f.request('/api/auth/google/callback?code=code&state=attacker', cookie);
  assert.equal(result.headers.Location, '/?connection=expired');
  assert.equal(f.exchangeCount, 0);
  assert.equal(f.saved.length, 0);
});
test('cancellation returns a recoverable result', async () => {
  const f = fixture();
  const { cookie, state } = await f.start();
  const result = await f.request(`/api/auth/google/callback?error=access_denied&state=${state}`, cookie);
  assert.equal(result.headers.Location, '/?connection=denied');
  assert.equal(f.exchangeCount, 0);
});
test('declining Drive write still connects; only the read scopes are required', async () => {
  const f = fixture({ scopes: SCOPES.filter(scope => !scope.endsWith('drive.file')) });
  const { cookie, state } = await f.start();
  const result = await f.request(`/api/auth/google/callback?code=code&state=${state}`, cookie);
  assert.equal(result.headers.Location, '/?connection=success');
});
for (const [name, options, reason] of [
  ['partial consent', { scopes: [SCOPES[2]] }, 'permissions'],
  ['missing refresh token', { tokens: { refresh_token: undefined } }, 'permissions'],
  ['unverified email', { verified: false }, 'account'],
  ['mismatched OpenID nonce', { nonce: 'wrong-nonce' }, 'account'],
  ['persistence failure', { failSave: true }, 'failed'],
]) {
  test(`${name} cannot claim a successful connection`, async () => {
    const f = fixture(options);
    const { cookie, state } = await f.start();
    const result = await f.request(`/api/auth/google/callback?code=code&state=${state}`, cookie);
    assert.equal(result.headers.Location, '/?connection=' + reason);
    assert.equal(f.saved.length, 0);
  });
}
test('a newly seen account gets its own connector rather than another account\'s', async () => {
  const f = fixture({ email: 'other@example.com' });
  const { cookie, state } = await f.start();
  const result = await f.request(`/api/auth/google/callback?code=code&state=${state}`, cookie);
  assert.equal(result.headers.Location, '/?connection=success');
  assert.deepEqual(f.saved.map(s => s.id), [connectorId('other@example.com')]);
  assert.notEqual(connectorId('other@example.com'), connectorId('person@example.com'));
});

test('expired and tampered cookies cannot create a session', () => {
  const expired = seal({ expires: Date.now() - 1 }, config.key);
  assert.equal(unseal(expired, config.key), null);
  const valid = seal({ expires: Date.now() + 1000 }, config.key);
  // The first character encodes random IV bits, so it is 'X' about one run in 64.
  // Flipping to a character that differs makes this a tampered cookie every time.
  assert.equal(unseal((valid[0] === 'X' ? 'Y' : 'X') + valid.slice(1), config.key), null);
});
test('missing setup shows setup status, not fake authorization', async () => {
  const f = fixture({ config: { ...config, enabled: false } });
  assert.equal((await f.request('/api/auth/google/start')).headers.Location, '/?connection=setup');
  assert.deepEqual(JSON.parse((await f.request('/api/session')).body), { connected: false, configured: false });
});
test('configuration rejects unsafe origins and malformed connector targets', () => {
  assert.throws(() => configuration({ PUBLIC_ORIGIN: 'http://example.com' }));
  assert.throws(() => configuration({ CONNECTOR_PROJECT: 'Bad_Project' }));
  assert.throws(() => configuration({ CONNECTOR_SERVICE_ACCOUNT: 'someone@example.com' }));
  assert.throws(() => configuration({ SESSION_KEY: 'tooshort' }));
});

test('each account resolves to its own stable connector, and no other', () => {
  assert.equal(connectorId('person@example.com'), connectorId('PERSON@Example.com'));
  assert.notEqual(connectorId('person@example.com'), connectorId('other@example.com'));
  assert.match(connectorId('person@example.com'), /^u-[0-9a-f]{16}$/);
});

test('an unconfigured deployment is never reported as enabled', () => {
  assert.equal(configuration({}).enabled, false);
  assert.equal(configuration({ CONNECTOR_PROJECT: 'demo-project' }).enabled, false);
});
