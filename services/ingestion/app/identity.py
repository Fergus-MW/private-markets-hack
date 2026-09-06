"""Short-lived identity assertions from IAM-authenticated frontend/connector services."""
import base64
from contextvars import ContextVar
import hashlib
import hmac
import json
import os
import re
import time

current_identity = ContextVar('graph_identity', default=None)


def verify(value, method, path):
    secret = os.environ.get('GRAPH_IDENTITY_SECRET', '')
    if len(secret) < 32 or len(value) > 4096:
        raise ValueError('Invalid identity configuration or assertion')
    payload, signature = value.split('.')
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError('Invalid identity signature')
    claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    if not isinstance(claims, dict):
        raise ValueError("Invalid identity claims")
    now = int(time.time())
    if (claims.get('aud') != 'knowledge-graph' or claims.get('method') != method
            or claims.get('path') != path or type(claims.get('exp')) is not int
            or type(claims.get('iat')) is not int or not now - 90 <= claims['iat'] <= now + 5
            or not now < claims['exp'] <= claims['iat'] + 90
            or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', claims.get('tenant', ''))
            or claims.get('kind') not in {'user', 'connector'}
            or not isinstance(claims.get('actor'), str) or not 0 < len(claims['actor']) <= 256):
        raise ValueError('Invalid identity claims')
    # A connector may deposit sources and carry them into the projects they relate
    # to. Both stay inside the asserted tenant and copy only already-ingested data.
    if claims['kind'] == 'connector' and (method, path) not in {
            ('POST', '/sources'), ('POST', '/mail/refresh-projects')}:
        raise ValueError('Connector identity is restricted to source ingestion')
    return claims


def tenant():
    identity = current_identity.get()
    return identity['tenant'] if identity else None


class IdentityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        from starlette.responses import JSONResponse
        value = dict(scope['headers']).get(b'x-graph-identity', b'')
        required = os.environ.get('GRAPH_MULTI_USER', 'false').lower() == 'true'
        public = scope['path'] in {'/healthz', '/formats'}
        identity = None
        if value:
            try:
                identity = verify(value.decode('ascii'), scope['method'], scope['path'])
            except (ValueError, TypeError, KeyError, UnicodeError):
                return await JSONResponse({'detail': 'Invalid graph identity'}, status_code=401)(scope, receive, send)
        elif required and not public:
            return await JSONResponse({'detail': 'Authenticated graph identity required'}, status_code=401)(scope, receive, send)
        token = current_identity.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            current_identity.reset(token)
