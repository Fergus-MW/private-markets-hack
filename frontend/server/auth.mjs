import { createCipheriv, createDecipheriv, createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { OAuth2Client, GoogleAuth } from 'google-auth-library';

export const SCOPES = ['openid', 'email', 'https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/drive.readonly'];
const SOURCE_SCOPES = SCOPES.slice(2);

export function configuration(env = process.env) {
  const origin = new URL(env.PUBLIC_ORIGIN || 'http://localhost:5173');
  if (origin.origin !== (env.PUBLIC_ORIGIN || 'http://localhost:5173') ||
      (origin.protocol !== 'https:' && !['localhost', '127.0.0.1'].includes(origin.hostname))) throw new Error('Invalid PUBLIC_ORIGIN');
  const project = env.CONNECTOR_PROJECT || '';
  if (project && !/^[a-z][a-z0-9-]{4,28}[a-z0-9]$/.test(project)) throw new Error('Invalid CONNECTOR_PROJECT');
  const serviceAccount = env.CONNECTOR_SERVICE_ACCOUNT || '';
  if (serviceAccount && !/^[^\s@]+@[^\s@]+\.iam\.gserviceaccount\.com$/.test(serviceAccount)) throw new Error('Invalid CONNECTOR_SERVICE_ACCOUNT');
  const key = env.SESSION_KEY ? Buffer.from(env.SESSION_KEY, 'hex') : null;
  if (key && (key.length !== 32 || !/^[a-f0-9]{64}$/i.test(env.SESSION_KEY))) throw new Error('SESSION_KEY must be 32 random bytes as hex');
  return {
    origin: origin.origin, secure: origin.protocol === 'https:', project, serviceAccount, key,
    clientId: env.GOOGLE_OAUTH_CLIENT_ID, clientSecret: env.GOOGLE_OAUTH_CLIENT_SECRET,
    enabled: Boolean(env.GOOGLE_OAUTH_CLIENT_ID && env.GOOGLE_OAUTH_CLIENT_SECRET && key && project && serviceAccount),
  };
}

// One connector per verified account, derived rather than stored: the same
// address always resolves to the same secret, and no address can reach another's.
export function connectorId(email) {
  return 'u-' + createHash('sha256').update(email.toLowerCase()).digest('hex').slice(0, 16);
}

export function seal(payload, key) {
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  const encrypted = Buffer.concat([cipher.update(JSON.stringify(payload), 'utf8'), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), encrypted]).toString('base64url');
}

export function unseal(value, key) {
  try {
    if (!key || !value || value.length > 4096) return null;
    const bytes = Buffer.from(value, 'base64url');
    const decipher = createDecipheriv('aes-256-gcm', key, bytes.subarray(0, 12));
    decipher.setAuthTag(bytes.subarray(12, 28));
    const decoded = JSON.parse(Buffer.concat([decipher.update(bytes.subarray(28)), decipher.final()]).toString());
    return decoded.expires > Date.now() ? decoded : null;
  } catch { return null; }
}

function cookie(req, name) {
  return (req.headers.cookie || '').split(';').map(s => s.trim()).find(s => s.startsWith(name + '='))?.slice(name.length + 1);
}
function cookieHeader(config, name, value, seconds) {
  return `${name}=${value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${seconds}${config.secure ? '; Secure' : ''}`;
}
function equals(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  const left = Buffer.from(a), right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}
class ConnectionError extends Error {}

export function createAuth(config, dependencies = {}) {
  const redirectUri = `${config.origin}/api/auth/google/callback`;
  const oauth = dependencies.oauth || new OAuth2Client(config.clientId, config.clientSecret, redirectUri);
  const cloud = new GoogleAuth({ scopes: ['https://www.googleapis.com/auth/cloud-platform'] });
  // Create-if-absent, grant the connector job read access, then write the version.
  // Ordering matters: a version must never exist that the job cannot read.
  const connect = dependencies.connect || (async (id, credentials) => {
    const client = await cloud.getClient();
    const parent = `projects/${config.project}`;
    const name = `${parent}/secrets/connector-${id}-oauth`;
    const call = (url, data) => client.request({ url: `https://secretmanager.googleapis.com/v1/${url}`, method: 'POST', data });
    try {
      await call(`${parent}/secrets?secretId=connector-${id}-oauth`, { replication: { automatic: {} } });
    } catch (error) {
      // Reconnecting an existing account is normal; anything else is fatal.
      if (error?.response?.status !== 409) throw error;
    }
    await call(`${name}:setIamPolicy`, { policy: { bindings: [
      { role: 'roles/secretmanager.secretAccessor', members: [`serviceAccount:${config.serviceAccount}`] }] } });
    await call(`${name}:addVersion`, { payload: { data: Buffer.from(JSON.stringify(credentials)).toString('base64') } });
    return name;
  });
  const redirect = (res, path, cookies = []) => {
    res.writeHead(303, { Location: path, 'Set-Cookie': cookies, 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer' });
    res.end();
  };
  return async function auth(req, res, url) {
    if (url.pathname === '/api/session') {
      const session = unseal(cookie(req, 'connection'), config.key);
      res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify(session?.kind === 'connection'
        ? { connected: true, email: session.email, connector: session.connector, configured: config.enabled }
        : { connected: false, configured: config.enabled }));
      return true;
    }
    if (!['/api/auth/google/start', '/api/auth/google/callback'].includes(url.pathname)) return false;
    if (!config.enabled) { redirect(res, '/?connection=setup'); return true; }
    if (url.pathname.endsWith('/start')) {
      const state = randomBytes(32).toString('hex');
      const nonce = randomBytes(32).toString('hex');
      const { codeVerifier, codeChallenge } = await oauth.generateCodeVerifierAsync();
      const transaction = seal({ kind: 'oauth', state, nonce, codeVerifier, expires: Date.now() + 600_000 }, config.key);
      redirect(res, oauth.generateAuthUrl({
        access_type: 'offline', prompt: 'consent select_account', scope: SCOPES, state, nonce,
        code_challenge: codeChallenge, code_challenge_method: 'S256',
      }), [cookieHeader(config, 'oauth_transaction', transaction, 600)]);
      return true;
    }
    const clear = cookieHeader(config, 'oauth_transaction', '', 0);
    try {
      const transaction = unseal(cookie(req, 'oauth_transaction'), config.key);
      if (transaction?.kind !== 'oauth' || !equals(transaction.state, url.searchParams.get('state'))) throw new ConnectionError('expired');
      if (url.searchParams.has('error')) throw new ConnectionError('denied');
      const code = url.searchParams.get('code');
      if (!code) throw new ConnectionError('expired');
      const { tokens } = await oauth.getToken({ code, codeVerifier: transaction.codeVerifier, redirect_uri: redirectUri });
      if (!tokens.id_token || !tokens.access_token || !tokens.refresh_token) throw new ConnectionError('permissions');
      const ticket = await oauth.verifyIdToken({ idToken: tokens.id_token, audience: config.clientId });
      const identity = ticket.getPayload();
      if (!identity?.email_verified || !equals(identity.nonce, transaction.nonce)) throw new ConnectionError('account');
      if (!identity.email) throw new ConnectionError('account');
      const info = await oauth.getTokenInfo(tokens.access_token);
      if (!SOURCE_SCOPES.every(scope => info.scopes.includes(scope))) throw new ConnectionError('permissions');
      const credentials = { type: 'authorized_user', client_id: config.clientId, client_secret: config.clientSecret,
        refresh_token: tokens.refresh_token, token_uri: 'https://oauth2.googleapis.com/token', scopes: info.scopes };
      // The credential must persist before the UI reports a successful connection.
      const id = connectorId(identity.email);
      await connect(id, credentials);
      const session = seal({ kind: 'connection', email: identity.email, connector: id, expires: Date.now() + 3_600_000 }, config.key);
      redirect(res, '/?connection=success', [clear, cookieHeader(config, 'connection', session, 3600)]);
    } catch (error) {
      // Never log token exchange responses, codes, cookies, identities or credentials.
      if (!(error instanceof ConnectionError)) console.error('Google connection failed');
      redirect(res, `/?connection=${error instanceof ConnectionError ? error.message : 'failed'}`, [clear, cookieHeader(config, 'connection', '', 0)]);
    }
    return true;
  };
}
