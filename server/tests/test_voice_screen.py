"""Synthetic mirror only: no device input, live services, or model calls."""
import io
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from hidagent.live_voice import VoiceBridge, router_for, voice_context

SID = 's' * 32


def fixture(size=(1646, 1080)):
    output = io.BytesIO()
    Image.new('RGB', size, 'blue').save(output, 'PNG')
    state = {'target': {'device': 'ipad'}}
    bridge = VoiceBridge(lambda: state, None, None)
    bridge.screen = SimpleNamespace(frame=output.getvalue(), live=True, updated=time.time())
    bridge.sessions[SID] = {'expires': time.monotonic() + 60, 'device_family': 'ipad'}
    app = FastAPI()
    app.include_router(router_for(bridge))
    return bridge, state, TestClient(app)


@pytest.mark.parametrize('size', [(1646, 1080), (1080, 1646), (400, 300)])
def test_normal_ipad_screen_is_jpeg_bounded_without_upscale(size):
    bridge, _, client = fixture(size)
    original = bridge.screen.frame
    result = client.post('/api/voice/screen/frame', json={'session': SID})
    assert result.status_code == 200
    assert result.headers['content-type'] == 'image/jpeg'
    assert result.headers['cache-control'] == 'no-store'
    with Image.open(io.BytesIO(result.content)) as image:
        assert image.format == 'JPEG'
        assert max(image.size) == min(768, max(size))
        assert abs(image.width / image.height - size[0] / size[1]) < .005
    assert bridge.screen.frame == original


def test_screen_session_and_local_origin_gates():
    bridge, state, client = fixture()
    post = lambda body, **kw: client.post('/api/voice/screen/frame', json=body, **kw)
    assert post({}).status_code == 422
    assert post({'session': 'unknown-session-id'}).status_code == 410
    assert post({'session': SID}, headers={'origin': 'https://evil.example'}).status_code == 403
    assert post({'session': SID}, headers={'sec-fetch-site': 'cross-site'}).status_code == 403
    assert post({'session': SID}, headers={'host': 'evil.example'}).status_code == 403
    remote = TestClient(client.app, client=('192.0.2.1', 123))
    assert remote.post('/api/voice/screen/frame', json={'session': SID}).status_code == 403
    bridge.sessions[SID]['camera'] = {'allowed': True}
    assert post({'session': SID}).status_code == 403
    del bridge.sessions[SID]['camera']
    bridge.sessions[SID]['expires'] = time.monotonic() - 1
    assert post({'session': SID}).status_code == 410
    bridge.sessions[SID]['expires'] = time.monotonic() + 60
    state['target']['device'] = 'iphone'
    assert post({'session': SID}).status_code == 403
    state['target']['device'] = 'ipad'
    bridge.sessions[SID]['device_family'] = 'iphone'
    assert post({'session': SID}).status_code == 403
    bridge.sessions.pop(SID)
    assert post({'session': SID}).status_code == 410


@pytest.mark.parametrize('change', ['missing', 'offline', 'stale', 'corrupt'])
def test_no_fresh_mirror_returns_no_content(change):
    bridge, _, client = fixture()
    if change == 'missing': bridge.screen.frame = None
    if change == 'offline': bridge.screen.live = False
    if change == 'stale': bridge.screen.updated = time.time() - 6
    if change == 'corrupt': bridge.screen.frame = b'not an image'
    result = client.post('/api/voice/screen/frame', json={'session': SID})
    assert result.status_code == 204 and result.content == b''
    assert result.headers['cache-control'] == 'no-store'


def test_ipad_prompt_describes_live_screen_and_keeps_action_tools():
    prompt, tools = voice_context({'target': {'device': 'ipad'}, 'exploration': {'available': True}})
    assert 'continuously see the live iPad screen' in prompt
    assert 'what do you see' in prompt
    assert 'Still use the tools for all device actions' in prompt
    assert 'NEVER device images' not in prompt
    assert 'has no video' not in prompt
    assert 'conversation has no video' not in str(tools)
