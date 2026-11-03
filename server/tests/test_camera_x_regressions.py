"""Offline camera -> X regressions. No device, receiver or external HTTP."""
import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from hidagent.ipad_demo_flows import DemoFlows, DemoRequest
from hidagent.live_voice import ToolCall
from test_ipad_demo_flows import make, run
from test_camera_photo import _bridge, _mint, PhotoDetector, SID


def test_full_screen_survives_reconnect_reload_and_rotation(tmp_path, monkeypatch):
    monkeypatch.setenv('HIDAGENT_DATA', str(tmp_path))
    f, p, v, state, clock = make(tmp_path)
    assert run(f, 'preflight')['status'] == 'ready'
    state['full_screen_apps'] = False
    p.device.links += 1
    p.target_info = lambda: dict(device='ipad', orientation='portrait', calibration_key='mini', width=500, height=800)
    state['target'] = p.target_info()
    reloaded = DemoFlows(p, f.screen, lambda: state, f.memory, vision=v, clock=clock, sleep=clock.sleep)
    assert run(reloaded, 'preflight')['status'] == 'ready'
    p.go_home.assert_not_awaited(); p.type.assert_not_awaited()


def test_manual_full_screen_verification(tmp_path, monkeypatch):
    monkeypatch.setenv('HIDAGENT_DATA', str(tmp_path))
    monkeypatch.setenv('IPAD_FULL_SCREEN_VERIFIED', '1')
    f, p, _, state, _ = make(tmp_path); state['full_screen_apps'] = False
    assert run(f, 'preflight')['status'] == 'ready'
    p.type.assert_not_awaited()


@pytest.mark.parametrize('action', ['camera_photo', 'camera_front', 'camera_rear'])
def test_camera_foreground_never_launches_again(tmp_path, action):
    f, p, v, _, _ = make(tmp_path)
    v.camera = type('Detector', (), {'verify_camera_ui': lambda self, frame: True,
                                   'camera_still': lambda self, frame: True})()
    f.open = AsyncMock(side_effect=AssertionError('Camera already foreground'))
    assert run(f, action)['status'] in ('captured', 'ready', 'uncertain')
    f.open.assert_not_awaited(); p.go_home.assert_not_awaited()


def test_camera_not_foreground_launches_once(tmp_path):
    f, p, v, _, _ = make(tmp_path)
    v.camera = type('Detector', (), {'verify_camera_ui': lambda self, frame: False,
                                   'camera_still': lambda self, frame: True})()
    f.open = AsyncMock()
    assert run(f, 'camera_photo')['status'] == 'captured'
    f.open.assert_awaited_once_with('Camera')


def test_handoff_is_owner_bound_expiring_and_never_replays_photo(monkeypatch):
    async def scenario():
        cam, _, b, gen, state = _bridge(PhotoDetector())
        await cam.prepare(SID, gen)
        b.demos = type('Demos', (), {'busy': False, 'run': AsyncMock(return_value={
            'status': 'awaiting_confirmation', 'draft_id': 'draft', 'readback': 'Orange hoodie in front of the camera.'})})()
        await b.call(ToolCall(session=SID, id='photo', name='take_photo', utterance='take a photo'))
        switched = await b.call(ToolCall(session=SID, id='return', name='return_to_device_mode',
            utterance='post this picture on X, describe the scene',
            args={'scene_description': 'Orange hoodie in front of the camera.'}))
        assert switched['status'] == 'switch_to_voice'
        return b, state
    b, state = asyncio.run(scenario())
    # An unrelated mint must not steal the intent.
    other = _mint(monkeypatch, b)
    assert 'startup' not in other
    b.last_mint = 0
    normal = _mint(monkeypatch, b, voice_handoff=SID)
    assert normal['startup']['name'] == 'resume_camera_intent'
    session = normal['session']
    r = asyncio.run(b.call(ToolCall(session=session, **normal['startup'])))
    assert r['status'] == 'awaiting_confirmation'
    req = b.demos.run.await_args.args[-1]
    assert req.action == 'x_photo_draft' and req.text == 'Orange hoodie in front of the camera.'
    assert state['agent']['task'] == ''
    asyncio.run(b.call(ToolCall(session=session, **normal['startup'])))
    assert b.demos.run.await_count == 1
    b.last_mint = 0
    assert 'startup' not in _mint(monkeypatch, b, voice_handoff=SID)


def test_stale_greeting_transcription_does_not_gate_device_tools():
    _, _, b, _, state = _bridge(PhotoDetector())
    b.sessions[SID].pop('camera')
    b.execute = AsyncMock(return_value={'status': 'started'})
    b.call_demo = AsyncMock(return_value={'status': 'started'})
    for name, args in [('phone_command', {'goal': 'Open Camera and take a photo'}),
                       ('ipad_demo', {'action': 'camera_photo'})]:
        r = asyncio.run(b.call(ToolCall(session=SID, id=name, name=name, args=args, utterance='Hello?', owner_turn=1)))
        assert r['status'] == 'started'
    b.execute.assert_awaited_once()
    b.call_demo.assert_awaited_once()
    assert state['agent']['task'] == ''


@pytest.mark.parametrize('confirmation', ['post it', '发送', 'send it', 'yes post'])
def test_photo_draft_stops_then_confirm_once(tmp_path, confirmation):
    f, p = photo_flow(tmp_path)
    r = run(f, 'x_photo_draft', text='Orange hoodie in front of the camera.', account='@example_creator')
    assert r['status'] == 'awaiting_confirmation' and r['image_attached'] is True
    assert not any(round(c.args[0]*1646) == 1428 for c in p.tap.await_args_list)
    assert f.mark_readback('session', r['draft_id'], r['readback'])
    original = f.photo_vision.check
    async def checked(screen, kind, **kw):
        return False if kind == 'sheet_closed' else await original(screen, kind, **kw)
    f.photo_vision.check = checked
    req = DemoRequest(action='x_post', draft_id=r['draft_id'])
    result = asyncio.run(f.run('session', 'send', confirmation, 2, req))
    assert result['status'] == 'uncertain'
    count = p.tap.await_count
    assert asyncio.run(f.run('session', 'retry', confirmation, 3, req))['status'] == 'rejected'
    assert p.tap.await_count == count


def test_full_screen_positive_settings_pixels_persist(tmp_path, monkeypatch):
    monkeypatch.setenv('HIDAGENT_DATA', str(tmp_path))
    monkeypatch.setattr('hidagent.ipad_demo_vision.selected_checkmark', lambda *a: True)
    f, p, v, state, clock = make(tmp_path); state['full_screen_apps'] = False
    v.observe = AsyncMock(return_value={'text': 'Multitasking', 'rows': [
        {'text': text, 'box': dict(left=.4, top=y, right=.7, bottom=y+.05)}
        for text, y in [('Full Screen Apps', .3), ('Windowed Apps', .4), ('Stage Manager', .5)]]})
    assert run(f, 'preflight')['status'] == 'ready'
    v.observe = AsyncMock(side_effect=AssertionError('Should trust saved proof'))
    p.device.links += 5
    f = DemoFlows(p, f.screen, lambda: state, f.memory, vision=v, clock=clock, sleep=clock.sleep)
    assert run(f, 'preflight')['status'] == 'ready'
    p.type.assert_not_awaited()


def test_env_proof_wins_over_windowed_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv('HIDAGENT_DATA', str(tmp_path))
    monkeypatch.setenv('IPAD_FULL_SCREEN_VERIFIED', '1')
    f, p, v, state, clock = make(tmp_path); state['full_screen_apps'] = False
    assert run(f, 'preflight')['status'] == 'ready'
    state['full_screen_layout'] = 'stage_manager'
    assert run(f, 'preflight')['status'] == 'ready'
    state.pop('full_screen_layout')
    f = DemoFlows(p, f.screen, lambda: state, f.memory, vision=v, clock=clock, sleep=clock.sleep)
    assert run(f, 'preflight')['status'] == 'ready'


def test_full_screen_different_calibration_identity_is_not_trusted(tmp_path, monkeypatch):
    monkeypatch.setenv('HIDAGENT_DATA', str(tmp_path))
    f, p, v, state, clock = make(tmp_path)
    assert run(f, 'preflight')['status'] == 'ready'
    state['full_screen_apps'] = False
    old = p.target_info()
    p.target_info = lambda: {**old, 'calibration_key': 'different-ipad-landscape-75'}
    assert run(f, 'preflight')['status'] == 'not_ready'


def test_handoff_expiry_binding_stop_and_reoffer():
    from hidagent.camera_handoff import CameraHandoff
    now = [100.0]; h = CameraHandoff(clock=lambda: now[0])
    owner = {'last_camera_photo': 'photo_taken'}
    state = {'target': {'device': 'ipad', 'calibration_key': 'ipad-landscape-66'}}
    c = ToolCall(session=SID, id='return', name='return_to_device_mode', utterance='post this photo on X',
                 scene_description='Orange hoodie.')
    assert h.offer(c, owner, state)
    assert h.claim('another-owner-session', {}, state) is None
    now[0] += 121
    assert h.claim(SID, {}, state) is None
    assert not h.offer(c.model_copy(update={'id': 'retry'}), owner, state), 'expired requests cannot be renewed by model retries'
    fresh = {}
    assert h.offer(c, fresh, state)
    assert h.claim(SID, {}, {'target': {'device': 'iphone'}}) is None
    assert h.offer(c, {}, state)
    h.cancel(SID, {})
    assert h.claim(SID, {}, state) is None


def test_all_tool_paths_log_name_and_status(tmp_path, caplog):
    from test_ipad_demo_voice import bridge
    import logging
    b, f, p = bridge(tmp_path)
    with caplog.at_level(logging.INFO, logger='hidagent.live_voice'):
        asyncio.run(b.call(ToolCall(session='offline-session-123', id='bad-demo', name='ipad_demo', args={'action':'preflight'})))
        with pytest.raises(Exception):
            asyncio.run(b.call(ToolCall(session='unknown-session-123', id='expired', name='phone_command')))
    assert 'voice tool result name=ipad_demo id=bad-demo status=' in caplog.text
    assert 'name=phone_command id=expired status=rejected' in caplog.text
    p.key.assert_not_awaited()


def photo_flow(tmp_path):
    from test_x_photo_landscape import flow
    f, p, _, _ = flow(tmp_path, selected=False)
    return f, p


def test_photo_observer_rejects_wrong_geometry_before_cloud():
    from hidagent.x_photo_flow import PhotoPostVision
    from test_ipad_demo_flows import png
    factory = AsyncMock()
    observer = PhotoPostVision(client_factory=factory)
    screen = type('Screen', (), {'frame': png(size=(710, 1080))})()
    with pytest.raises(RuntimeError, match='1646x1080'):
        asyncio.run(observer.check(screen, 'foreground'))
    factory.assert_not_called()


@pytest.mark.parametrize('utterance,scene,expected', [
    ('Post this photo on X and describe the scene', 'Orange hoodie.', 'Orange hoodie.'),
    ('Post this photo on X', 'Orange hoodie.', 'Orange hoodie.'),
    ('Post the photo on X with caption "yes"', '', 'yes'),
    ("Post the photo on X with caption 'Hi, Joe!'", 'Orange hoodie.', 'Hi, Joe!'),
    ('Post this photo on X and type yes', 'Orange hoodie.', 'yes'),
    ('Post this photo on X with caption say cheese', '', 'say cheese'),
    ('Post this photo on X and type say hello', '', 'say hello'),
    ('Post this picture on X and type yes? Type something to describe the scene.', 'Orange hoodie.', 'Orange hoodie.'),
])
def test_caption_uses_owner_literal_or_observed_scene(utterance, scene, expected):
    from hidagent.camera_handoff import caption_draft
    assert caption_draft(utterance, scene) == expected


@pytest.mark.parametrize('phrase', [
    'say Joe asked Ode to take it',
    'saying Joe asked Ode to take it',
    'tell them Joe asked Ode to take it',
    'and write that Joe asked Ode to take it',
    'say this was Joe controlling Ode to take the photo',
])
def test_spoken_caption_intent(phrase):
    from hidagent.camera_handoff import caption_draft
    assert caption_draft('Post the photo I just took on X and '+phrase, '') == (
        'Joe asked Ode, his AI on an old iPad, to take this photo.')


def test_chinese_spoken_caption():
    from hidagent.camera_handoff import caption_draft
    assert caption_draft('把照片发到X，说这是Joe让Ode拍的照片', '') == '这是Joe让Ode拍的照片'


@pytest.mark.parametrize('scene', ['', '这是一张照片。'])
def test_caption_defaults_without_ascii_scene(scene):
    from hidagent.camera_handoff import caption_draft
    assert caption_draft('Post the photo I just took on X', scene) == (
        'Taken by Ode, my AI living on an old iPad.')


def spotlight_vision(labels):
    from hidagent.ipad_demo_vision import DemoVision
    v = DemoVision()
    def row(text, left, top):
        return dict(text=text, confidence=1, box=dict(left=left, top=top, right=left+.15, bottom=top+.03))
    rows = [row('Top Hit', .3, .15)]
    rows += [row(text, .3+i*.18, .30) for i, text in enumerate(labels)]
    rows += [row('Suggestions', .3, .37), row('X', .3, .42)]
    v.observe = AsyncMock(return_value={'text': '\n'.join(r['text'] for r in rows), 'rows': rows})
    return v


@pytest.mark.parametrize('labels,accepted', [
    (['X', 'XChat', 'Quantumult X'], True),
    (['X XChat Quantumult X'], True),
    (['XChat', 'X', 'Quantumult X'], False),
    (['XChat X Quantumult X'], False),
    (['XChat', 'Quantumult X'], False),
])
def test_x_multi_icon_top_hit_requires_first_exact_label(labels, accepted):
    from types import SimpleNamespace
    v = spotlight_vision(labels)
    assert bool(asyncio.run(v.check(SimpleNamespace(frame=b'offline'), 'top_hit', text='X'))) is accepted


def test_x_already_foreground_skips_spotlight(tmp_path):
    f, p = photo_flow(tmp_path)
    f.show_spotlight = AsyncMock(side_effect=AssertionError('X already foreground'))
    assert run(f, 'x_photo_draft', text='A photo.', account='@example_creator')['status'] == 'awaiting_confirmation'
    f.show_spotlight.assert_not_awaited()
    p.go_home.assert_awaited_once()
    assert ('cmd+space',) not in [c.args for c in p.key.await_args_list]
    assert 'timeline' in f.photo_vision.calls


def test_x_launch_uses_dock_without_top_hit_ocr(tmp_path):
    from hidagent.ipad_demo_flows import DemoFlows
    f, p = photo_flow(tmp_path)
    original = f.photo_vision.check
    async def check(screen, kind, **kw):
        if kind == 'foreground': return any(c.args == ('enter',) for c in p.key.await_args_list)
        return await original(screen, kind, **kw)
    f.photo_vision.check = check
    f.open = DemoFlows.open.__get__(f)
    f.vision.check = AsyncMock(side_effect=AssertionError('No top_hit or layout checks'))
    assert run(f, 'x_photo_draft', text='A photo.', account='@example_creator')['status'] == 'awaiting_confirmation'
    p.key.assert_not_awaited()
    assert p.type.await_args_list[0].args == ('A photo.',)
