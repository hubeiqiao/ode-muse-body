"""No device, provider or external JWKS calls: exercise the complete ASGI gate."""
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient

from hidagent import access
from hidagent.live_voice import check_local

TOKEN = 'synthetic-owner-token-for-tests-only'


@pytest.fixture
def client(monkeypatch):
    for key in ('ODE_OWNER_TOKEN', 'ODE_PUBLIC_ORIGIN', 'ODE_ACCESS_ISSUER', 'ODE_ACCESS_AUD'):
        monkeypatch.delenv(key, raising=False)
    app = FastAPI()
    app.add_middleware(access.OwnerAccessMiddleware)
    @app.get('/api/control')
    def control(request: Request):
        check_local(request)
        return {'ok': True}
    @app.get('/frame')
    def frame():
        return {'frame': 'synthetic'}
    @app.websocket('/ws')
    async def socket(ws: WebSocket):
        await ws.accept()
        await ws.send_text('ok')
        await ws.close()
    return TestClient(app)


def remote(monkeypatch):
    monkeypatch.setenv('ODE_PUBLIC_ORIGIN', 'https://ode.example.com')
    monkeypatch.setenv('ODE_OWNER_TOKEN', TOKEN)
    return {'host': 'ode.example.com', 'authorization': 'Bearer '+TOKEN,
            'x-forwarded-for': '203.0.113.10', 'origin': 'https://ode.example.com'}


def test_local_default(client):
    assert client.get('/api/control').status_code == 200


@pytest.mark.parametrize('headers', [
    {'host': 'ode.example.com'}, {'x-forwarded-for': '127.0.0.1'},
    {'cf-access-jwt-assertion': 'unverified'}, {'forwarded': 'for=127.0.0.1'},
    {'origin': 'https://foreign.example.com'}, {'sec-fetch-site': 'cross-site'},
])
def test_default_denies_proxy_spoof_and_cross_site(client, headers):
    assert client.get('/api/control', headers=headers).status_code == 403
    assert client.get('/frame', headers=headers).status_code == 403


def test_remote_token_and_origin(client, monkeypatch):
    headers = remote(monkeypatch)
    assert client.get('/api/control', headers=headers).status_code == 200
    for changes in ({'authorization': 'Bearer wrong'}, {'host': 'evil.example.com'},
                    {'origin': 'https://evil.example.com'}, {'sec-fetch-site': 'cross-site'}):
        assert client.get('/api/control', headers={**headers, **changes}).status_code == 403
    monkeypatch.setenv('ODE_OWNER_TOKEN', 'short')
    assert client.get('/frame', headers=headers).status_code == 403


def test_token_requires_configured_origin(client, monkeypatch):
    headers = remote(monkeypatch)
    monkeypatch.delenv('ODE_PUBLIC_ORIGIN')
    assert client.get('/frame', headers=headers).status_code == 403


def test_non_loopback_peer_requires_auth(client):
    external = TestClient(client.app, client=('203.0.113.10', 1234))
    assert external.get('/frame').status_code == 403


def test_jwt_signature_claims_and_fail_closed(client, monkeypatch):
    headers = remote(monkeypatch)
    headers.pop('authorization')
    issuer = 'https://example.cloudflareaccess.com'
    monkeypatch.setenv('ODE_ACCESS_ISSUER', issuer)
    monkeypatch.setenv('ODE_ACCESS_AUD', 'test-audience')
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(access, 'key_client', lambda _: SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key())))
    claims = {'iss': issuer, 'aud': 'test-audience', 'sub': 'test-owner',
              'iat': int(time.time()), 'exp': int(time.time())+60}
    def request(payload, key=private):
        assertion = jwt.encode(payload, key, algorithm='RS256')
        return client.get('/api/control', headers={**headers, 'cf-access-jwt-assertion': assertion})
    assert request(claims).status_code == 200
    for changes in ({'exp': 1}, {'aud': 'wrong'}, {'iss': 'https://wrong.example.com'}, {'iat': int(time.time())+3600}):
        assert request({**claims, **changes}).status_code == 403
    assert request({k: v for k, v in claims.items() if k != 'exp'}).status_code == 403
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert request(claims, other).status_code == 403
    monkeypatch.setenv('ODE_ACCESS_ISSUER', 'http://localhost')
    assert request(claims).status_code == 403


def test_websocket_gate(client, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/ws', headers={'host': 'ode.example.com'}):
            pass
    with client.websocket_connect('/ws', headers=remote(monkeypatch)) as ws:
        assert ws.receive_text() == 'ok'


def test_test_host_is_not_a_production_loopback_name(client):
    local = TestClient(client.app, client=('127.0.0.1', 1234))
    assert local.get('/frame', headers={'host': 'testserver'}).status_code == 403


def test_remote_header_cannot_inject_asgi_authorization(client):
    assert client.get('/api/control', headers={'host': 'ode.example.com',
        'ode_remote_authorized': 'true'}).status_code == 403
