import { createHmac } from 'node:crypto';
import { GoogleAuth } from 'google-auth-library';
import { connectorId, unseal } from './auth.mjs';

export function assertion(session, method, path, secret, now = Math.floor(Date.now() / 1000)) {
  if (!secret || secret.length < 32) throw new Error('Graph identity signing is not configured');
  const payload = Buffer.from(JSON.stringify({ tenant: session.connector, actor: session.email, kind: 'user',
    aud: 'knowledge-graph', iat: now, exp: now + 60, method, path })).toString('base64url');
  return payload + '.' + createHmac('sha256', secret).update(payload).digest('hex');
}

export function createGraphProxy(config, dependencies = {}) {
  const backend = process.env.INGESTION_URL;
  const secret = process.env.GRAPH_IDENTITY_SECRET;
  const auth = new GoogleAuth();
  const request = dependencies.request || (async options => {
    const client = await auth.getIdTokenClient(backend);
    return client.request(options);
  });
  return async (req, res, url) => {
    if (!/^\/api\/(graph|projects|sources|documents)(\/|$)/.test(url.pathname)) return false;
    const reply = (status, detail) => {
      res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify({ detail }));
    };
    if (!['GET', 'POST'].includes(req.method)) { reply(405, 'Method not allowed'); return true; }
    const cookie = (req.headers.cookie || '').split(';').map(s => s.trim()).find(s => s.startsWith('connection='))?.slice(11);
    const session = unseal(cookie, config.key);
    if (session?.kind !== 'connection' || typeof session.email !== 'string' || session.connector !== connectorId(session.email)) {
      reply(401, 'Sign in to access your knowledge graph'); return true;
    }
    if (req.method === 'POST' && req.headers.origin !== config.origin) {
      reply(403, 'Origin does not match'); return true;
    }
    if (!backend || !secret) { reply(503, 'Knowledge graph is not configured'); return true; }
    const path = url.pathname.slice(4);
    const chunks = [];
    let size = 0;
    for await (const chunk of req) {
      size += chunk.length;
      if (size > 21 * 1024 * 1024) { reply(413, 'Request too large'); return true; }
      chunks.push(chunk);
    }
    try {
      const upstream = await request({ url: backend + path + url.search, method: req.method,
        headers: { 'X-Graph-Identity': assertion(session, req.method, path, secret),
          ...(req.headers['content-type'] ? { 'Content-Type': req.headers['content-type'] } : {}) },
        data: chunks.length ? Buffer.concat(chunks) : undefined, responseType: 'arraybuffer',
        timeout: 900000, maxRedirects: 0, maxContentLength: 128 * 1024 * 1024, validateStatus: () => true });
      const headers = { 'Cache-Control': 'no-store', 'Content-Type': upstream.headers.get('content-type') || 'application/octet-stream' };
      const disposition = upstream.headers.get('content-disposition');
      if (disposition) headers['Content-Disposition'] = disposition;
      res.writeHead(upstream.status, headers);
      res.end(Buffer.from(upstream.data));
    } catch { reply(503, 'Knowledge graph unavailable'); }
    return true;
  };
}
