import { connectorId, unseal } from './auth.mjs';
import { createRequest } from './upstream.mjs';

// Forward only signed AgentMail webhooks. Their raw bytes are verified by the
// private mail service; the browser never receives mail or model credentials.
export function createMail(config, dependencies = {}) {
  const backend = dependencies.backend ?? process.env.MAIL_SERVICE_URL;
  const request = dependencies.request || createRequest(backend);
  return {
    async signup(email) {
      if (!backend) return;
      await request({ url: backend + '/signup', method: 'POST', data: { email }, timeout: 15000 });
    },
    // Browser-facing: the session cookie decides whose progress is returned, so
    // a signed-in user can never poll another account's ingestion.
    async status(req, res, url) {
      if (url.pathname !== '/api/ingestion/status') return false;
      const reply = (code, body) => {
        res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
        res.end(JSON.stringify(body));
      };
      if (req.method !== 'GET') { reply(405, { detail: 'Method not allowed' }); return true; }
      const cookie = (req.headers.cookie || '').split(';').map(s => s.trim()).find(s => s.startsWith('connection='))?.slice(11);
      const session = unseal(cookie, config.key);
      if (session?.kind !== 'connection' || typeof session.email !== 'string' || session.connector !== connectorId(session.email)) {
        reply(401, { detail: 'Sign in to see your ingestion progress' }); return true;
      }
      if (!backend) { reply(503, { detail: 'Ingestion progress is not configured' }); return true; }
      try {
        const result = await request({ url: backend + '/ingestion/status', method: 'POST',
          data: { email: session.email }, timeout: 15000, validateStatus: () => true });
        reply(result.status === 200 ? 200 : result.status === 404 ? 404 : 503,
          result.status === 200 ? result.data : { detail: 'Ingestion progress unavailable' });
      } catch { reply(503, { detail: 'Ingestion progress unavailable' }); }
      return true;
    },

    async webhook(req, res, url) {
      if (url.pathname !== '/api/agentmail/webhook') return false;
      if (req.method !== 'POST') { res.writeHead(405); res.end(); return true; }
      if (!backend) { res.writeHead(503); res.end(); return true; }
      const headers = { 'Content-Type': 'application/json' };
      for (const name of ['svix-id', 'svix-timestamp', 'svix-signature']) {
        if (typeof req.headers[name] !== 'string') { res.writeHead(401); res.end(); return true; }
        headers[name] = req.headers[name];
      }
      const chunks = [];
      let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 512 * 1024) { res.writeHead(413); res.end(); return true; }
        chunks.push(chunk);
      }
      try {
        const result = await request({ url: backend + '/webhook', method: 'POST', headers,
          data: Buffer.concat(chunks), timeout: 20000, maxRedirects: 0, validateStatus: () => true });
        res.writeHead(result.status); res.end();
      } catch { res.writeHead(503); res.end(); }
      return true;
    },
  };
}
