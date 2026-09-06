import { GoogleAuth } from 'google-auth-library';

/** Outbound caller for a private backend.
 *
 * Cloud Run is always https, and reaching it needs an IAM identity token. A
 * plain-http backend can only be local compose, where no metadata server
 * exists, so the token is skipped rather than failing every call. This changes
 * transport auth only: the ingestion service still requires the signed
 * X-Graph-Identity assertion, so identity is never weakened.
 */
export function createRequest(backend) {
  if (/^https:/i.test(backend || '')) {
    const auth = new GoogleAuth();
    return async options => {
      const client = await auth.getIdTokenClient(backend);
      return client.request(options);
    };
  }
  return async ({ url, method = 'GET', headers = {}, data, responseType, timeout = 30000 }) => {
    const body = data === undefined || Buffer.isBuffer(data) || typeof data === 'string'
      ? data
      : JSON.stringify(data);
    const sent = { ...headers };
    if (body !== undefined && !Buffer.isBuffer(data) && typeof data !== 'string' && !sent['Content-Type']) {
      sent['Content-Type'] = 'application/json';
    }
    const response = await fetch(url, { method, headers: sent, body, redirect: 'manual',
      signal: AbortSignal.timeout(timeout) });
    const buffer = Buffer.from(await response.arrayBuffer());
    return {
      status: response.status,
      headers: response.headers,
      data: responseType === 'arraybuffer' ? buffer
        : (response.headers.get('content-type') || '').includes('json') && buffer.length
          ? JSON.parse(buffer.toString()) : buffer.toString(),
    };
  };
}
