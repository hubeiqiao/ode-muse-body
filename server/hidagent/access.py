"""Fail-closed origin authentication for optional remote control.

Forwarding headers never make a request local. A valid token authorizes the
configured remote origin; it does not authorize any particular device action.
"""
import asyncio
import hmac
import ipaddress
import os
from functools import lru_cache
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient
from starlette.responses import JSONResponse

LOOPBACK_NAMES = {'localhost'}
FORWARD_HEADERS = {b'forwarded', b'x-forwarded-for', b'x-forwarded-host',
                   b'x-forwarded-proto', b'cf-connecting-ip', b'cf-access-jwt-assertion'}


def loopback(host):
    if host in LOOPBACK_NAMES or host == 'testclient':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@lru_cache(maxsize=4)
def key_client(issuer):
    return PyJWKClient(issuer + '/cdn-cgi/access/certs', timeout=5)


def verify_access(token, issuer, audience):
    parsed = urlsplit(issuer)
    if (parsed.scheme != 'https' or not parsed.hostname or
            not parsed.hostname.endswith('.cloudflareaccess.com') or parsed.path or
            parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port):
        raise ValueError('Invalid Access issuer')
    signing_key = key_client(issuer).get_signing_key_from_jwt(token).key
    jwt.decode(token, signing_key, algorithms=['RS256'], audience=audience,
               issuer=issuer, options={'require': ['exp', 'iat', 'iss', 'aud', 'sub']})


class OwnerAccessMiddleware:
    """Protect every HTTP/WS path, including static UI, frames and task state."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope.get('headers', [])}
        host = headers.get(b'host', b'').decode('latin1')
        try:
            hostname = urlsplit('//'+host).hostname
        except ValueError:
            hostname = None
        peer = (scope.get('client') or ('', 0))[0]
        local_host = loopback(hostname) or (peer == 'testclient' and hostname == 'testserver')
        local = loopback(peer) and local_host and not FORWARD_HEADERS.intersection(headers)
        remote_ok = False
        if not local:
            origin = os.environ.get('ODE_PUBLIC_ORIGIN', '').rstrip('/')
            parsed = urlsplit(origin)
            # No wildcard hosts, query tokens or client-controlled issuer/JWKS URLs.
            if (parsed.scheme == 'https' and parsed.netloc == host and
                    not parsed.path and not parsed.query and not parsed.fragment and
                    not parsed.username and not parsed.password):
                configured = os.environ.get('ODE_OWNER_TOKEN', '')
                supplied = headers.get(b'authorization', b'').decode('latin1')
                remote_ok = (len(configured) >= 32 and
                             hmac.compare_digest(supplied.encode('latin1'), ('Bearer '+configured).encode('utf-8')))
                issuer = os.environ.get('ODE_ACCESS_ISSUER', '').rstrip('/')
                audience = os.environ.get('ODE_ACCESS_AUD', '')
                assertion = headers.get(b'cf-access-jwt-assertion', b'').decode('latin1')
                if not remote_ok and issuer and audience and assertion and len(assertion) <= 16384:
                    try:
                        await asyncio.to_thread(verify_access, assertion, issuer, audience)
                        remote_ok = True
                    except (jwt.PyJWTError, ValueError, OSError):
                        pass
            if not remote_ok:
                return await self.deny(scope, receive, send, 403)
        # Reject cross-site browser requests even when cookies/headers authenticate.
        expected = ('https://' + host) if remote_ok else scope.get('scheme', 'http')+'://'+host
        origin_header = headers.get(b'origin', b'').decode('latin1')
        if (origin_header and origin_header != expected) or headers.get(b'sec-fetch-site') == b'cross-site':
            return await self.deny(scope, receive, send, 403)
        scope['ode_remote_authorized'] = remote_ok
        return await self.app(scope, receive, send)

    @staticmethod
    async def deny(scope, receive, send, status):
        if scope['type'] == 'websocket':
            return await send({'type': 'websocket.close', 'code': 1008})
        response = JSONResponse({'detail': 'Owner access required'}, status_code=status)
        await response(scope, receive, send)
