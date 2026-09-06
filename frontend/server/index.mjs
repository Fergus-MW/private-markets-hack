import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { configuration, createAuth } from './auth.mjs';
import { createGraphProxy } from './graph.mjs';
import { createMail } from './mail.mjs';

const config = configuration();
const mail = createMail(config);
const auth = createAuth(config, { onSignup: mail.signup });
const graph = createGraphProxy(config);
const root = fileURLToPath(new URL('../dist/', import.meta.url));
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' };
createServer(async (req, res) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.setHeader('X-Frame-Options', 'DENY');
  try {
    const url = new URL(req.url, config.origin);
    if (await mail.webhook(req, res, url)) return;
    if (await mail.status(req, res, url)) return;
    if (await graph(req, res, url)) return;
    if (req.method !== 'GET') { res.writeHead(405, { Allow: 'GET' }); res.end(); return; }
    if (await auth(req, res, url)) return;
    if (url.pathname === '/healthz') { res.end('ok'); return; }
    const appRoute = url.pathname === '/' || /^\/(?:graphs|dashboard)(?:\/[^/]+)?\/?$/.test(url.pathname);
    const path = resolve(root, '.' + decodeURIComponent(appRoute ? '/index.html' : url.pathname));
    if (!path.startsWith(root.endsWith(sep) ? root : root + sep)) { res.writeHead(404); res.end(); return; }
    const file = await readFile(path);
    res.writeHead(200, { 'Content-Type': types[extname(path)] || 'application/octet-stream',
      'Cache-Control': url.pathname.startsWith('/assets/') ? 'public, max-age=31536000, immutable' : 'no-cache' });
    res.end(file);
  } catch { res.writeHead(404); res.end('Not found'); }
}).listen(Number(process.env.PORT || 8080), '0.0.0.0', () => console.log('Frontend ready'));
