"""Camera conversation (front camera -> Gemini Live) gates.

Design: outputs/current-goal-20261028/design/camera-conversation.zh-CN.md §6.1.
All frames here are synthetic. No device, network, model API or disk writes.
"""
import asyncio
import builtins
import io
import json
import re
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from hidagent import camera_conversation as cc
from hidagent import hid_lease
from hidagent.live_voice import VoiceBridge, ToolCall, VoiceControl, router_for, MODEL

FIXTURES = Path(__file__).with_name('fixtures')
SID = 'c' * 32
LANDSCAPE = (1646, 1080)
PORTRAIT = (1080, 1646)
PREVIEW = (0.10, 0.20, 0.80, 0.90)  # fractional l,t,r,b; excludes status bar/banner/controls


def synthetic(size=LANDSCAPE, seed=0):
    """Preview area textured, status bar + banner band + control strip painted pure red."""
    w, h = size
    img = Image.new('RGB', size, (255, 0, 0))
    inner = Image.effect_noise((w, h), 60 + seed).convert('RGB')
    l, t, r, b = PREVIEW
    box = (int(l * w), int(t * h), int(r * w), int(b * h))
    img.paste(inner.crop(box), box)
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=95)
    return out.getvalue()


class FakeScreen:
    def __init__(self):
        self.frame, self.updated, self.live = None, 0.0, True
        self.health = 'session_connected'

    def mirror_health(self):
        return {'state': self.health}

    def show(self, frame, at=None):
        self.frame, self.updated = frame, (time.time() if at is None else at)


class FakeDetector:
    """Stands in for the measured templates; every predicate is switchable."""
    def __init__(self):
        self.ui = True
        self.front = True
        self.banner = False
        self.overlay = None
        self.chrome_seed = 0
        self.sizes = {LANDSCAPE, PORTRAIT}
        self.launch_calls = []

    def ready(self):
        return True, 'ready'

    def preview_region(self, frame_size, orientation):
        if tuple(frame_size) not in self.sizes:
            raise cc.CameraTemplatesPending('unknown layout')
        return PREVIEW

    def chrome_region(self, frame_size, orientation):
        if tuple(frame_size) not in self.sizes:
            raise cc.CameraTemplatesPending('unknown layout')
        return (0.85, 0.0, 1.0, 1.0)

    def chrome_fingerprint(self, image, box):
        return cc.region_fingerprint(image, box) if self.chrome_seed == 0 else 'changed-%d' % self.chrome_seed

    def verify_camera_ui(self, frame):
        return self.ui

    def verify_camera_front(self, frame):
        return self.front

    def banner_present(self, frame, orientation):
        return self.banner

    def known_overlay(self, frame):
        return self.overlay

    async def launch_camera(self):
        self.launch_calls.append('launch')

    async def flip_to_front(self):
        self.launch_calls.append('flip')

    async def leave_camera(self):
        self.launch_calls.append('leave')


def make(detector=None):
    screen = FakeScreen()
    cam = cc.CameraConversation(screen, detector=detector or FakeDetector())
    return cam, screen


def bridge_for(cam=None, monkeypatch=None):
    state = {'device': True, 'screen': True, 'calibrated': True, 'target': {'device': 'ipad'},
             'models': {'planner': 'p'}, 'agent': {'running': False, 'task': '', 'steps': []}}
    stops = []

    async def start(goal):
        state['agent']['task'] = goal

    async def stop():
        stops.append(time.monotonic())

    b = VoiceBridge(lambda: state, start, stop)
    b.camera = cam
    return b, state, stops


def streaming(cam, screen, sid=SID):
    gen = cam.begin(sid)
    screen.show(synthetic(seed=1), at=time.time() - 0.2)
    cam.mark_streaming(sid, gen)
    return gen


def camera_session(bridge, cam, screen, sid=SID):
    gen = streaming(cam, screen, sid)
    bridge.sessions[sid] = {'expires': time.monotonic() + 600, 'calls': {}, 'paused': False,
                            'cancelled_ids': set(), 'camera': {'allowed': True, 'generation': gen, 'max_s': 540}}
    return gen


def fresh(screen, seed):
    screen.show(synthetic(seed=seed))


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    monkeypatch.setenv('CAMERA_CONVERSATION', '1')
    yield
    cc.end_all_instances('test_cleanup')


# ---------------------------------------------------------------- 1 normal session

def _mint(monkeypatch, bridge, **kw):
    monkeypatch.setenv('GEMINI_LIVE_API_KEY', 'k')
    for name in ('SOURCE_NOTE_WORKFLOWS', 'DEVICE_MODE', 'AIRPLAY_NAME', 'CAMERA_LIVE_SETUP_OVERRIDES'):
        monkeypatch.delenv(name, raising=False)
    sent = []

    async def post(self, url, **kwargs):
        sent.append(kwargs['json'])
        return httpx.Response(200, json={'name': 'auth_tokens/x'})
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    result = asyncio.run(bridge.mint(**kw))
    return result, sent


def _normal_snapshot(monkeypatch, b):
    result, sent = _mint(monkeypatch, b)
    for key in ('expireTime', 'newSessionExpireTime'):
        sent[0].pop(key)
    got = json.dumps({'setup': result['setup'], 'model': result['model'], 'keys': sorted(result),
                      'token_request': sent[0]}, sort_keys=True, ensure_ascii=False)
    return result, got


class NotReadyDetector(FakeDetector):
    def ready(self):
        return False, 'camera_layout_unsupported'


@pytest.mark.parametrize('flag,ready', [('0', True), ('1', False)])
def test_normal_session_setup_is_byte_identical_snapshot(monkeypatch, flag, ready):
    # The continuous conversation has the same tools regardless of the legacy flag/readiness.
    monkeypatch.setenv('CAMERA_CONVERSATION', flag)
    cam, _ = make(FakeDetector() if ready else NotReadyDetector())
    b, _, _ = bridge_for(cam if flag == '1' else None)
    result, got = _normal_snapshot(monkeypatch, b)
    assert got == (FIXTURES / 'voice_setup_normal_ipad.json').read_text().strip()
    assert 'MEDIA_RESOLUTION_LOW' in got and 'contextWindowCompression' not in got
    assert 'start_camera_conversation' not in got
    assert 'camera' not in b.sessions[result['session']]
    assert not b.sessions[result['session']].get('camera_switch')






def _switch_session(monkeypatch, cam=None):
    cam = cam or make()[0]
    b, state, stops = bridge_for(cam)
    result, _ = _mint(monkeypatch, b)
    return b, state, result['session'], cam


def _switch_call(b, sid, utterance='Can you look at me with the camera?', cid='sw'):
    return asyncio.run(b.call(ToolCall(session=sid, id=cid, name='start_camera_conversation', args={}, utterance=utterance)))






class _Busy:
    busy = True
    current = {}

    def owns_voice(self, *a):
        return False








def test_normal_session_cannot_fetch_camera_frames(monkeypatch):
    cam, screen = make()
    b, _, _ = bridge_for(cam)
    result, _ = _mint(monkeypatch, b)
    sid = result['session']
    gen = cam.begin('z' * 32)  # another (camera) session exists; normal one still refused
    fresh(screen, 3)
    r = b.camera_frame(sid, gen)
    assert r.status == 403 and r.reason == 'not_camera_session' and r.body is None


def test_camera_mint_is_scoped_and_setup_fields_overridable(monkeypatch):
    cam, _ = make()
    b, _, _ = bridge_for(cam)
    result, _ = _mint(monkeypatch, b, camera=True)
    setup = result['setup']
    names = [d['name'] for d in setup['tools'][0]['functionDeclarations']]
    assert names == ['end_camera_conversation', 'stop_phone', 'flip_camera', 'take_photo', 'return_to_device_mode']
    # No memory on the bridge: no companion persona and no memory tools in the camera setup (the name still renders).
    assert 'You are Ode, an English AI companion' not in setup['systemInstruction']['parts'][0]['text']
    assert 'Your name is Ode' in setup['systemInstruction']['parts'][0]['text']
    assert setup['generationConfig']['mediaResolution'] == 'MEDIA_RESOLUTION_LOW'
    assert setup['contextWindowCompression']['slidingWindow']['targetTokens'] == 12000
    assert setup['realtimeInputConfig']['turnCoverage'] == 'TURN_INCLUDES_ONLY_ACTIVITY'
    assert setup['realtimeInputConfig']['automaticActivityDetection'] == {'silenceDurationMs': 450}
    session = b.sessions[result['session']]
    assert session['camera']['allowed'] is True and session['camera']['max_s'] == 540
    assert result['camera']['generation'] == session['camera']['generation']
    assert 'phone_command' not in setup['systemInstruction']['parts'][0]['text']
    # A second camera session is refused while one is active.
    b.last_mint = 0
    with pytest.raises(HTTPException) as e:
        asyncio.run(b.mint(camera=True))
    assert e.value.detail == 'camera_conversation_active'
    cam.end_all('test')
    # Overrides for pending live verification (P7): null removes a field.
    b.last_mint = 0
    monkeypatch.setenv('CAMERA_LIVE_SETUP_OVERRIDES', json.dumps({'mediaResolution': None, 'turnCoverage': 'TURN_INCLUDES_ALL_INPUT'}))
    monkeypatch.setattr(httpx.AsyncClient, 'post', lambda self, url, **kw: _ok())
    again = asyncio.run(b.mint(camera=True))['setup']
    assert 'mediaResolution' not in again['generationConfig']
    assert again['realtimeInputConfig']['turnCoverage'] == 'TURN_INCLUDES_ALL_INPUT'
    assert 'contextWindowCompression' in again


def _memory(tmp_path):
    from hidagent.companion import CompanionMemory
    memory = CompanionMemory(tmp_path)
    memory.remember('ESP32QA I prefer ocean animals.')
    return memory


def test_camera_session_carries_companion_persona_and_memory(monkeypatch, tmp_path):
    memory = _memory(tmp_path)
    cam, _ = make()
    b, _, _ = bridge_for(cam)
    b.memory = memory
    normal, _ = _mint(monkeypatch, b)
    normal_text = normal['setup']['systemInstruction']['parts'][0]['text']
    b.last_mint = 0
    result, _ = _mint(monkeypatch, b, camera=True)
    setup = result['setup']
    text = setup['systemInstruction']['parts'][0]['text']
    names = [d['name'] for d in setup['tools'][0]['functionDeclarations']]
    assert names == ['end_camera_conversation', 'stop_phone', 'flip_camera', 'take_photo', 'return_to_device_mode',
                     'companion_remember', 'companion_recall', 'set_ai_name']
    milo = normal_text[normal_text.index('\nYou are Ode'):normal_text.index('ESP32QA I prefer ocean animals.')]
    assert milo in text and 'ESP32QA I prefer ocean animals.' in text
    # Camera-specific rules survive; the iPad is the AI's own device (owner decision 2026-10-29).
    assert 'The iPad is your own device' in text and 'end_camera_conversation' in text
    assert 'return_to_device_mode' in text and 'take_photo' in text
    assert 'cannot operate' not in text
    assert 'phone_command' not in text
    normal_tools = {d['name']: d for d in normal['setup']['tools'][0]['functionDeclarations']}
    camera_tools = {d['name']: d for d in setup['tools'][0]['functionDeclarations']}
    for name in ('companion_remember', 'companion_recall', 'set_ai_name'):
        assert camera_tools[name] == normal_tools[name]


def test_camera_session_allows_memory_tools_but_no_device_tools(monkeypatch, tmp_path):
    memory = _memory(tmp_path)
    cam, screen = make()
    b, state, _ = bridge_for(cam)
    b.memory = memory
    camera_session(b, cam, screen)
    r = asyncio.run(b.call(ToolCall(session=SID, id='m1', name='companion_recall', args={})))
    assert r['status'] == 'memory_recall' and r['preferences'][0]['text'] == 'ESP32QA I prefer ocean animals.'
    r = asyncio.run(b.call(ToolCall(session=SID, id='m2', name='companion_remember', args={'text': 'x'})))
    assert r['status'] == 'rejected'  # explicit Remember that utterance still required
    r = asyncio.run(b.call(ToolCall(session=SID, id='m3', name='companion_remember', args={'text': 'x'},
                                    utterance='Remember that I like green tea.')))
    assert r['status'] == 'memory_saved'
    r = asyncio.run(b.call(ToolCall(session=SID, id='p1', name='phone_command', args={'goal': 'Open Notes'})))
    assert r['reason'] == 'camera_conversation_active' and state['agent']['task'] == ''
    # Without memory the companion tools stay refused in camera mode.
    b.memory = None
    r = asyncio.run(b.call(ToolCall(session=SID, id='m4', name='companion_recall', args={})))
    assert r['reason'] == 'camera_conversation_active'




async def _ok_async():
    return httpx.Response(200, json={'name': 'auth_tokens/x'})


def _ok():
    return _ok_async()


def test_camera_mint_refused_when_flag_off_or_templates_pending(monkeypatch):
    monkeypatch.setenv('CAMERA_CONVERSATION', '0')
    cam, _ = make()
    b, _, _ = bridge_for(cam)
    with pytest.raises(HTTPException) as e:
        _mint(monkeypatch, b, camera=True)
    assert e.value.status_code == 404
    monkeypatch.setenv('CAMERA_CONVERSATION', '1')
    stub = cc.CameraConversation(FakeScreen())  # default stub detector: fails closed
    b2, _, _ = bridge_for(stub)
    with pytest.raises(HTTPException) as e:
        _mint(monkeypatch, b2, camera=True)
    assert e.value.status_code == 409 and 'camera_templates_pending' in str(e.value.detail)


# ---------------------------------------------------------------- 2 phases

def test_only_streaming_state_serves_frames():
    cam, screen = make()
    gen = cam.begin(SID)
    fresh(screen, 4)
    assert cam.frame(SID, gen).status == 409 and cam.frame(SID, gen).reason == 'not_streaming'
    cam.sessions[SID]['state'] = 'front_check'
    assert cam.frame(SID, gen).reason == 'not_streaming'
    cam.mark_streaming(SID, gen)
    fresh(screen, 5)
    r = cam.frame(SID, gen)
    assert r.status == 200 and r.body[:2] == b'\xff\xd8'


# ---------------------------------------------------------------- 3 Stop is synchronous

def test_interrupt_ends_camera_before_awaiting_stop(monkeypatch):
    cam, screen = make()
    b, _, _ = bridge_for(cam)
    gen = camera_session(b, cam, screen)
    hang = asyncio.Event()

    async def stop():
        await hang.wait()
    b.stop = stop

    async def run():
        task = asyncio.create_task(b.interrupt(VoiceControl(session=SID, text='stop')))
        await asyncio.sleep(0)
        # Camera state itself flipped synchronously while stop() still hangs.
        assert cam.sessions[SID]['state'] == 'ended' and cam.sessions[SID]['reason'] == 'stop'
        assert not cc.active_any()
        fresh(screen, 6)
        r = b.camera_frame(SID, gen)
        assert r.status == 410, r
        assert not cc.active_any()
        task.cancel()
    asyncio.run(run())


def test_close_and_agent_stop_end_camera_first(monkeypatch):
    cam, screen = make()
    b, _, _ = bridge_for(cam)
    gen = camera_session(b, cam, screen)
    asyncio.run(b.close(SID))
    assert cam.sessions[SID]['reason'] == 'session_closed'
    fresh(screen, 7)
    assert b.camera_frame(SID, gen).status == 410
    cam2, screen2 = make()
    gen2 = streaming(cam2, screen2, 'd' * 32)
    from hidagent import app as app_module
    import inspect
    src = inspect.getsource(app_module.agent_stop)
    import ast, textwrap
    fn = ast.parse(textwrap.dedent(src)).body[0]
    first = fn.body[1]  # body[0] is the docstring
    assert ast.unparse(first).startswith('camera_end_all('), ast.unparse(first)
    cc.end_all_instances('emergency')
    fresh(screen2, 8)
    assert cam2.frame('d' * 32, gen2).status == 410


# ---------------------------------------------------------------- 4 old frames

def test_old_frames_generation_and_duplicates():
    cam, screen = make()
    gen = cam.begin(SID)
    screen.show(synthetic(seed=9), at=time.time() - 0.1)
    cam.mark_streaming(SID, gen)
    # The frame shown before streaming_since is never the first frame.
    r = cam.frame(SID, gen)
    assert r.status == 409 and r.reason == 'stale'
    # ...but the first NEW passing frame goes out at once (no 2-pass resume before the first frame).
    fresh(screen, 90)
    r = cam.frame(SID, gen)
    assert r.status == 200, r
    cam2, screen2 = make()
    gen2 = streaming(cam2, screen2)
    fresh(screen2, 10)
    assert cam2.frame(SID, gen2).status == 200
    assert cam2.frame(SID, gen2).status == 204  # same screen.updated twice
    assert cam2.frame(SID, 'old-generation').status == 410
    screen2.show(synthetic(seed=11), at=time.time() - 1.5)
    assert cam2.frame(SID, gen2).reason == 'stale'  # >= 1.0 s old


def test_mirror_not_session_connected_is_stale():
    cam, screen = make()
    gen = streaming(cam, screen)
    screen.health = 'source_session_unknown'
    fresh(screen, 12)
    assert cam.frame(SID, gen).reason == 'stale'


def test_identical_preview_bytes_pause_as_frozen():
    # Live = continuous new frames; frozen = byte-identical preview for >= 2 s
    # (a static scene still differs slightly, so small diffs never mean frozen).
    now = [time.time()]
    screen = FakeScreen()
    cam = cc.CameraConversation(screen, detector=FakeDetector(), clock=lambda: now[0])
    gen = cam.begin(SID)
    screen.show(synthetic(seed=12), at=now[0] - .2)
    cam.mark_streaming(SID, gen)
    same = synthetic(seed=13)
    for step, expected in ((.5, 200), (.9, 204), (.9, 204), (.5, 409)):
        now[0] += step
        screen.show(same, at=now[0])
        r = cam.frame(SID, gen)
        assert r.status == expected, (step, r)
    assert r.reason == 'preview_frozen'


def test_black_preview_is_withheld():
    cam, screen = make()
    gen = streaming(cam, screen)
    black = Image.new('RGB', LANDSCAPE, (0, 0, 0))
    out = io.BytesIO(); black.save(out, 'JPEG')
    screen.show(out.getvalue())
    assert cam.frame(SID, gen).reason == 'preview_black'


# ---------------------------------------------------------------- 5 gates

@pytest.mark.parametrize('attr,value,reason,hard', [
    ('overlay', 'notification_center_known_layout', 'overlay', False),
    ('banner', True, 'overlay', False),
    ('ui', False, 'camera_not_verified', False),
    ('front', False, 'not_front', True),
    ('chrome_seed', 1, 'chrome_changed', False),
])
def test_gate_failures_transient_vs_hard(attr, value, reason, hard):
    det = FakeDetector()
    cam, screen = make(det)
    gen = streaming(cam, screen)
    fresh(screen, 20)
    assert cam.frame(SID, gen).status == 200
    setattr(det, attr, value)
    fresh(screen, 21)
    r = cam.frame(SID, gen)
    assert r.status == 409 and r.reason == reason
    setattr(det, attr, FakeDetector().__dict__[attr])
    fresh(screen, 22)
    r = cam.frame(SID, gen)
    if hard:
        assert r.status == 410
    else:
        assert r.status == 409 and r.reason == 'resuming'
        fresh(screen, 23)
        assert cam.frame(SID, gen).status == 200


def test_hid_activity_is_hard_failure():
    cam, screen = make()
    gen = streaming(cam, screen)
    with pytest.raises(RuntimeError):
        hid_lease.check_hid('move 5 5')  # refused while streaming, and counted
    fresh(screen, 24)
    r = cam.frame(SID, gen)
    assert r.status == 409 and r.reason == 'hid_activity'
    assert cam.frame(SID, gen).status == 410


def test_release_is_always_allowed_while_streaming():
    cam, screen = make()
    gen = streaming(cam, screen)
    hid_lease.check_hid('release')
    fresh(screen, 25)
    assert cam.frame(SID, gen).status == 200


def test_time_limit_ends_session():
    cam, screen = make()
    gen = streaming(cam, screen)
    cam.sessions[SID]['started_at'] -= 541
    fresh(screen, 26)
    assert cam.frame(SID, gen).status == 410


# ---------------------------------------------------------------- 6 crop / scale

def test_crop_excludes_status_banner_and_controls_and_scales():
    cam, screen = make()
    gen = streaming(cam, screen)
    fresh(screen, 27)
    r = cam.frame(SID, gen)
    with Image.open(io.BytesIO(r.body)) as out:
        out = out.convert('RGB')
        w, h = LANDSCAPE
        l, t, rr, b = PREVIEW
        cw, ch = int(rr * w) - int(l * w), int(b * h) - int(t * h)
        scale = min(1.0, 768 / max(cw, ch))
        assert out.size == (round(cw * scale), round(ch * scale))
        assert max(out.size) <= 768
        raw = out.tobytes()
        red = sum(1 for i in range(0, len(raw), 3) if raw[i] > 200 and raw[i + 1] < 40 and raw[i + 2] < 40)
        assert red == 0


# ---------------------------------------------------------------- 7 orientations

def test_portrait_passes_and_unknown_size_fails_closed():
    cam, screen = make()
    gen = streaming(cam, screen)
    screen.show(synthetic(PORTRAIT, seed=28))
    assert cam.frame(SID, gen).status == 200
    screen.show(synthetic((1000, 700), seed=29))
    r = cam.frame(SID, gen)
    assert r.status == 409 and r.reason == 'unknown_layout'
    assert cam.frame(SID, gen).status == 410


def test_default_stubs_fail_closed():
    assert cc.verify_camera_front(synthetic()) is None  # controls cannot prove front: owner confirms
    assert cc.verify_camera_ui(synthetic()) is False
    with pytest.raises(cc.CameraTemplatesPending):
        cc.preview_region(PORTRAIT, 'portrait')  # only landscape 1646x1080 is measured
    with pytest.raises(cc.CameraTemplatesPending):
        asyncio.run(cc.launch_camera())
    with pytest.raises(cc.CameraTemplatesPending):
        asyncio.run(cc.flip_to_front())
    stub = cc.CameraConversation(FakeScreen())
    assert stub.ready() == (False, 'camera_templates_pending')
    gen = stub.begin(SID)
    stub.screen.show(synthetic(seed=30), at=time.time() - .2)
    stub.mark_streaming(SID, gen)
    stub.screen.show(synthetic(seed=31))
    assert stub.frame(SID, gen).status != 200


# ---------------------------------------------------------------- 8 frame privacy

def test_check_cloud_frame_refuses_during_camera_session():
    from hidagent.decision import DecisionUnavailable
    from hidagent.frame_privacy import check_cloud_frame, check_camera_frame
    frame = synthetic()
    check_cloud_frame(frame)  # nothing active
    cam, _ = make()
    cam.begin(SID)
    with pytest.raises(DecisionUnavailable):
        check_cloud_frame(frame)
    with pytest.raises(DecisionUnavailable):
        check_camera_frame(frame)
    cam.end(SID, 'owner_end')
    check_cloud_frame(frame)


def test_check_cloud_frame_refuses_recognised_camera_ui(monkeypatch):
    from hidagent.decision import DecisionUnavailable
    from hidagent.frame_privacy import check_cloud_frame
    monkeypatch.setattr(cc, 'recognised_camera_ui', lambda image: True)
    monkeypatch.setenv('WITHHOLD_CAMERA_UI', '1')
    with pytest.raises(DecisionUnavailable):
        check_cloud_frame(synthetic())


def test_check_cloud_frame_allows_camera_ui_by_default(monkeypatch):
    from hidagent.frame_privacy import check_cloud_frame
    monkeypatch.setattr(cc, 'recognised_camera_ui', lambda image: True)
    monkeypatch.delenv('WITHHOLD_CAMERA_UI', raising=False)
    check_cloud_frame(synthetic())


def test_local_meta_never_receives_frames_during_camera_session(monkeypatch):
    from hidagent.decision import DecisionUnavailable
    from hidagent.meta_model import LocalMetaLLM
    m = LocalMetaLLM('http://localhost:11434/v1', 'meta')
    posted = []

    async def post(*a, **k):
        posted.append(1)
        raise AssertionError('must not post')
    monkeypatch.setattr(m._http, 'post', post)
    cam, _ = make()
    cam.begin(SID)
    with pytest.raises(DecisionUnavailable):
        asyncio.run(m.chat('s', 't', synthetic()))
    assert posted == []


# ---------------------------------------------------------------- 9 tools / mutual exclusion

def test_camera_session_rejects_device_tools_without_tasks():
    cam, screen = make()
    b, state, _ = bridge_for(cam)
    camera_session(b, cam, screen)

    async def run():
        for name, args in [('phone_command', {'goal': 'Open Calculator'}), ('use_app_tool', {'tool_name': 'x', 'args': {}}),
                           ('phone_calculate', {'left': 1, 'right': 2, 'operator': 'add'}), ('phone_explore', {'goal': 'x'}),
                           ('start_app_goal', {'goal': 'x'}), ('companion_remember', {'text': 'x'})]:
            r = await b.call(ToolCall(session=SID, id=name, name=name, args=args, utterance='Remember that x'))
            assert r['status'] == 'rejected' and r['reason'] == 'camera_conversation_active'
        assert b.sessions[SID]['calls'] == {}
        assert state['agent']['task'] == ''
    asyncio.run(run())


def test_end_camera_conversation_tool_ends_streaming():
    cam, screen = make()
    b, _, _ = bridge_for(cam)
    gen = camera_session(b, cam, screen)
    r = asyncio.run(b.call(ToolCall(session=SID, id='e', name='end_camera_conversation', args={})))
    assert r['status'] == 'camera_ended'
    fresh(screen, 32)
    assert b.camera_frame(SID, gen).status == 410


def test_app_tools_and_agent_start_refuse_during_camera():
    from hidagent.app_tools import AppTools
    cam, _ = make()
    cam.begin(SID)
    tools = AppTools.__new__(AppTools)
    tools.server_instance = 'srv'
    with pytest.raises(HTTPException) as e:
        asyncio.run(AppTools.call(tools, 'srv_1', 'calculator.calculate', {}))
    assert e.value.status_code == 409 and e.value.detail == 'camera_conversation_active'
    from hidagent import app as app_module
    with pytest.raises(HTTPException) as e:
        asyncio.run(app_module.agent_start(app_module.Text(text='Open Calculator')))
    assert e.value.status_code == 409 and e.value.detail == 'camera_conversation_active'


# ---------------------------------------------------------------- 10 no disk, no logs of bytes

def test_full_flow_never_writes_frames_or_logs_bytes(monkeypatch, caplog, tmp_path):
    cam, screen = make()
    b, _, _ = bridge_for(cam)
    frames = []
    real_open = builtins.open

    def guarded_open(file, mode='r', *a, **k):
        if any(c in mode for c in 'wax+'):
            raise AssertionError('camera flow must not open files for writing: %r' % (file,))
        return real_open(file, mode, *a, **k)
    monkeypatch.setattr(builtins, 'open', guarded_open)
    monkeypatch.setattr(Path, 'write_bytes', lambda *a, **k: (_ for _ in ()).throw(AssertionError('write_bytes')))
    from hidagent.debug import Trace
    monkeypatch.setattr(Trace, 'write', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Trace.write')))
    caplog.set_level('DEBUG')
    gen = camera_session(b, cam, screen)
    for seed in range(40, 44):
        fresh(screen, seed)
        r = b.camera_frame(SID, gen)
        frames.append(r.body)
    asyncio.run(b.close(SID))
    assert all(frames)
    import base64
    text = caplog.text
    for body in frames:
        assert base64.b64encode(body)[:24].decode() not in text
    assert not re.search(r'[A-Za-z0-9+/]{120,}', text)


# ---------------------------------------------------------------- 11 HID / firmware

def test_streaming_refuses_all_non_release_hid():
    cam, screen = make()
    streaming(cam, screen)
    for command in ('key cmd+h', 'key space', 'key return', 'click', 'type x', 'move 1 1'):
        with pytest.raises(RuntimeError):
            hid_lease.check_hid(command)
    hid_lease.check_hid('release')


def test_launch_window_only_allows_whitelisted_hid():
    cam, _ = make()
    cam.begin(SID)
    with cc.camera_hid_window(cc.LAUNCH_WHITELIST):
        hid_lease.check_hid('key cmd+space')
        hid_lease.check_hid('type Camera')
        with pytest.raises(RuntimeError):
            hid_lease.check_hid('key space')
        with pytest.raises(RuntimeError):
            hid_lease.check_hid('key cmd+a')


def test_firmware_has_no_volume_or_consumer_keys():
    root = Path(__file__).resolve().parents[2] / 'devices/ipad/firmware/hid_bridge_ble_c3'
    sources = [p for p in root.iterdir() if p.suffix in ('.ino', '.h', '.cpp')]
    assert sources
    for p in sources:
        assert not re.search(r'volume|consumer', p.read_text(errors='ignore'), re.I), p


def test_hid_counter_unchanged_semantics_without_camera(monkeypatch):
    monkeypatch.setenv('CAMERA_CONVERSATION', '0')
    before = hid_lease.hid_count()
    hid_lease.check_hid('move 1 1')
    assert hid_lease.hid_count() == before + 1
    hid_lease.check_hid('release')
    assert hid_lease.hid_count() == before + 1


# ---------------------------------------------------------------- 12 launch workflow

def test_prepare_with_default_stubs_fails_closed_without_streaming():
    stub = cc.CameraConversation(FakeScreen())
    gen = stub.begin(SID)
    asyncio.run(stub.prepare(SID, gen))
    assert stub.sessions[SID]['state'] == 'ended'
    assert stub.sessions[SID]['reason'] == 'camera_templates_pending'
    assert not cc.active_any()


def test_cloud_vision_unreachable_once_camera_session_begins():
    from hidagent.decision import DecisionUnavailable
    from hidagent.frame_privacy import check_cloud_frame
    det = FakeDetector()
    judged = []

    async def launch():
        # A launcher that tried to verify the Return step with cloud vision.
        check_cloud_frame(synthetic())
        judged.append('cloud')
    det.launch_camera = launch
    cam, screen = make(det)
    gen = cam.begin(SID)
    asyncio.run(cam.prepare(SID, gen))
    assert judged == []
    assert cam.sessions[SID]['state'] == 'ended'


def test_prepare_flips_once_then_streams():
    det = FakeDetector()
    det.front = False

    async def flip():
        det.launch_calls.append('flip')
        det.front = True
    det.flip_to_front = flip
    cam, screen = make(det)
    screen.show(synthetic(seed=50))
    gen = cam.begin(SID)
    cam.flip_settle_s = 0
    asyncio.run(cam.prepare(SID, gen))
    assert det.launch_calls == ['launch', 'flip']
    assert cam.sessions[SID]['state'] == 'streaming'


def test_prepare_does_not_flip_twice():
    det = FakeDetector()
    det.front = False
    cam, screen = make(det)
    screen.show(synthetic(seed=51))
    gen = cam.begin(SID)
    cam.flip_settle_s = 0
    asyncio.run(cam.prepare(SID, gen))
    assert det.launch_calls == ['launch', 'flip']
    assert cam.sessions[SID]['reason'] == 'not_front'


# ---------------------------------------------------------------- 13 flip target vs shutter

def test_flip_target_too_close_to_shutter_is_refused():
    size = LANDSCAPE
    shutter = (0.90, 0.45, 0.98, 0.55)
    near = (0.90, 0.56, 0.98, 0.60)  # 0.01*1080 ~ 11 px away
    far = (0.90, 0.80, 0.98, 0.88)
    assert cc.flip_target_safe(near, [shutter], size) is False
    assert cc.flip_target_safe(far, [shutter], size) is True
    assert cc.flip_target_safe(shutter, [shutter], size) is False


# ---------------------------------------------------------------- router

def test_router_frame_endpoint_and_flag(monkeypatch):
    cam, screen = make()
    b, _, _ = bridge_for(cam)
    gen = camera_session(b, cam, screen)
    app = FastAPI()
    app.include_router(router_for(b))
    client = TestClient(app)
    fresh(screen, 60)
    r = client.post('/api/voice/camera/frame', json={'session': SID, 'generation': gen})
    assert r.status_code == 200 and r.headers['content-type'] == 'image/jpeg'
    assert r.headers['cache-control'] == 'no-store'
    r = client.post('/api/voice/camera/frame', json={'session': SID, 'generation': gen})
    assert r.status_code == 204
    status = client.get('/api/voice/status').json()
    assert status['camera_controls'] is True
    assert 'camera_conversation' not in status
    monkeypatch.setenv('CAMERA_CONVERSATION', '0')
    assert client.get('/api/voice/status').json()['camera_controls'] is True
    fresh(screen, 61)
    assert client.post('/api/voice/camera/frame', json={'session': SID, 'generation': gen}).status_code == 404
    monkeypatch.setenv('CAMERA_CONVERSATION', '1')
    r = client.post('/api/voice/camera/end', json={'session': SID})
    assert r.status_code == 200
    fresh(screen, 62)
    assert client.post('/api/voice/camera/frame', json={'session': SID, 'generation': gen}).status_code == 410


def test_camera_js_behaviour():
    import subprocess
    subprocess.run(['node', str(Path(__file__).with_name('camera_voice.test.cjs'))], check=True)


@pytest.mark.parametrize('reason', ['camera_not_live', 'camera_not_verified', 'camera_launch_failed', 'flip_failed', 'unknown_layout'])
def test_camera_failure_frame_response_resumes_voice(monkeypatch, reason):
    cam, screen = make()
    b, _, _ = bridge_for(cam)
    gen = camera_session(b, cam, screen)
    cam.end(SID, reason)
    app = FastAPI()
    app.include_router(router_for(b))
    monkeypatch.setenv('CAMERA_CONVERSATION', '1')
    result = TestClient(app).post('/api/voice/camera/frame', json={'session': SID, 'generation': gen})
    assert result.status_code == 410
    assert result.json()['resume_voice'] is True
    assert result.json()['reason'] == reason
    assert "staying in voice" in result.json()['instruction']
    assert b.voice_switch_granted is not None


def test_owner_stop_does_not_resume_voice():
    cam, screen = make()
    b, _, _ = bridge_for(cam)
    gen = camera_session(b, cam, screen)
    cam.end(SID, 'stop')
    assert not getattr(b.camera_frame(SID, gen), 'resume_voice', False)




def test_camera_foreground_is_in_voice_context(monkeypatch):
    cam, screen = make()
    screen.show(synthetic())
    b, _, _ = bridge_for(cam)
    result, _ = _mint(monkeypatch, b)
    prompt = result['setup']['systemInstruction']['parts'][0]['text']
    assert 'Current foreground app: Camera' in prompt
    assert 'flip_camera' in prompt


def test_camera_browser_recovery():
    import subprocess
    for script in ('camera_return_voice.test.cjs', 'camera_switch.test.cjs'):
        subprocess.run(['node', str(Path(__file__).with_name(script))], check=True)


def test_persistent_camera_pause_returns_to_voice():
    now = [time.time()]
    screen = FakeScreen()
    cam = cc.CameraConversation(screen, detector=FakeDetector(), clock=lambda: now[0])
    b, _, _ = bridge_for(cam)
    gen = camera_session(b, cam, screen)
    screen.health = 'source_disconnected'
    assert b.camera_frame(SID, gen).status == 409
    now[0] += cc.PAUSE_TIMEOUT_S + .1
    result = b.camera_frame(SID, gen)
    assert result.status == 410 and result.resume_voice


def test_recovery_mint_is_single_use_and_bound_to_camera_session(monkeypatch):
    cam, screen = make()
    b, _, _ = bridge_for(cam)
    gen = camera_session(b, cam, screen)
    cam.end(SID, 'camera_launch_failed')
    b.last_mint = time.monotonic()
    result, _ = _mint(monkeypatch, b, resume_camera=SID)
    assert 'camera' not in result
    with pytest.raises(HTTPException) as e:
        _mint(monkeypatch, b, resume_camera=SID)
    assert e.value.status_code == 429


def test_stale_generation_cannot_grant_voice_recovery():
    cam, screen = make()
    b, _, _ = bridge_for(cam)
    camera_session(b, cam, screen)
    cam.end(SID, 'camera_launch_failed')
    assert not b.camera_frame(SID, 'wrong-generation').resume_voice
    assert b.voice_switch_granted is None


def test_current_camera_foreground_is_exposed_in_tool_status():
    cam, screen = make()
    screen.show(synthetic())
    b, _, _ = bridge_for(cam)
    assert b.snapshot()['foreground_app'] == 'Camera'


def test_slower_fresh_frame_after_streaming_is_accepted():
    now = [time.time()]
    screen = FakeScreen()
    cam = cc.CameraConversation(screen, detector=FakeDetector(), clock=lambda: now[0])
    gen = streaming(cam, screen)
    now[0] += 2.5
    screen.show(synthetic(seed=74), at=now[0]-1.5)
    assert cam.frame(SID, gen).status == 200


def test_voice_recovery_closes_long_running_camera_even_if_end_receipt_is_lost(monkeypatch):
    cam, screen = make()
    b, _, _ = bridge_for(cam)
    camera_session(b, cam, screen)
    cam.sessions[SID]['started_at'] -= 100
    _mint(monkeypatch, b, resume_camera=SID)
    assert cam.status(SID)['camera_state'] == 'ended'


def test_normal_setup_has_same_camera_actions_with_legacy_flag_on(monkeypatch):
    cam, _ = make()
    b, _, _ = bridge_for(cam)
    result, got = _normal_snapshot(monkeypatch, b)
    assert got == (FIXTURES / 'voice_setup_normal_ipad_camera_switch.json').read_text().strip()
    assert got == (FIXTURES / 'voice_setup_normal_ipad.json').read_text().strip()
    names = {d['name'] for d in result['setup']['tools'][0]['functionDeclarations']}
    assert {'open_camera', 'flip_camera', 'take_photo', 'close_camera'} <= names
    assert not names & {'start_camera_conversation', 'return_to_device_mode'}
    assert not cam.active()
