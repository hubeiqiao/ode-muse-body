"""Offline API regressions: a static mirror must never turn keys into mouse input."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from hidagent import clipboard
from hidagent.device import DeviceError, InputTimelines
from hidagent.phone import Phone, PhoneUnavailable, NUDGE
from hidagent.screen import Screen
from test_input_latency import api


@pytest.fixture
def keyboard_phone(tmp_path, monkeypatch):
    # Construct diagnostics only. Never start a receiver, socket or device.
    screen = Screen(name='iPad Agent', stream='offline.test:1')
    screen._sock = object()
    screen.frame, screen.updated = b'observed frame', time.time() - 10
    screen._receiver_health = dict(process_running=True, peer_connected=True,
                                   frame_ready=True, source_frame_age_s=10)
    screen._receiver_health_at = time.monotonic()
    screen.fresh = AsyncMock(return_value=None)
    screen.restart = Mock(side_effect=AssertionError('No receiver restart'))
    device = SimpleNamespace(connected=True, links=1, send=AsyncMock(return_value='ok'))
    device.readiness = lambda: dict(connected=device.connected, stable=device.connected,
                                     reason='ready' if device.connected else 'disconnected')
    phone = Phone(device, tmp_path/'map.json', screen, profiles=object())
    phone.target_info = lambda: dict(device='ipad', orientation='landscape',
                                     calibration_key='ipad-landscape', width=1646, height=1080)
    phone._sync_profile = lambda: False
    phone.span = dict(w=1646, h=1080)
    phone._link = 1
    phone._save = Mock()
    monkeypatch.delenv('HID_KEY_MS', raising=False)
    monkeypatch.delenv('HID_POINTER_MS', raising=False)
    monkeypatch.setattr(clipboard, 'supported', lambda: True)
    monkeypatch.setattr(clipboard, 'write', lambda text: None)
    monkeypatch.setenv('CLIPBOARD_HANDOFF_WAIT', '0')
    return phone


KEYBOARD_CASES = [
    ('/api/key', {'text': key}, ['key '+key])
    for key in ('cmd+h', 'cmd+space', 'cmd+a', 'cmd+f', 'enter', 'tab', 'esc')
] + [('/api/type', {'text': 'IELTS speaking'}, ['type IELTS speaking']),
     ('/api/type', {'text': 'Hello \U0001f600'}, ['key cmd+v']),
     ('/api/home', {}, ['key esc', 'key esc', 'key cmd+h']),
     ('/api/recover-home', {}, ['key esc', 'key esc', 'key cmd+h'])]


@pytest.mark.parametrize('path,payload,expected', KEYBOARD_CASES)
@pytest.mark.parametrize('age', [0, 10, 300])
def test_keyboard_api_accepts_connected_still_mirror_without_pointer(
        api, keyboard_phone, tmp_path, monkeypatch, path, payload, expected, age):
    module, _ = api
    from hidagent import phone as phone_module
    monkeypatch.setattr(phone_module, 'HOME_RECOVERY_WAIT', .001)
    phone = keyboard_phone
    phone.screen.updated = time.time() - age
    monkeypatch.setattr(module, 'phone', phone)
    monkeypatch.setattr(module, 'device', phone.device)
    store = InputTimelines(tmp_path/'traces')
    monkeypatch.setattr(module, 'input_timelines', store)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                     base_url='http://127.0.0.1') as client:
            response = await client.post(path, json=payload)
        assert response.status_code == 200, response.text
        if path == '/api/recover-home':
            assert response.json() == dict(ok=True, home_reached=None, attempts=1, retries=0)
    asyncio.run(run())
    assert [call.args[0] for call in phone.device.send.await_args_list] == expected
    phone.screen.fresh.assert_not_awaited()
    phone.screen.restart.assert_not_called()
    timings = list(phone.action_timings)
    assert timings and all(row['pointer_probe_sent'] is False for row in timings)
    assert all(row['outcome'] in ('ok', 'acknowledged', 'home_not_verified') for row in timings)
    checks = [event for event in store.recent[-1]['events'] if event['event'] == 'ensure_live']
    assert checks and all(event['pointer_probe_sent'] is False for event in checks)
    assert payload.get('text', 'PRIVATE_SENTINEL') not in repr(timings)


@pytest.mark.parametrize('state', [
    'source_offline', 'source_disconnected', 'receiver_disconnected',
    'receiver_heartbeat_lost', 'receiver_waiting', 'waiting_for_first_frame',
    'source_session_unknown', 'legacy_stream', 'legacy_stream_no_recent_frame',
])
@pytest.mark.parametrize('path,payload,_', KEYBOARD_CASES[-4:] + [KEYBOARD_CASES[0]])
def test_keyboard_api_rejects_bad_mirror_without_input(
        api, keyboard_phone, monkeypatch, state, path, payload, _):
    module, _ = api
    phone = keyboard_phone
    monkeypatch.setattr(phone.screen, 'mirror_health', lambda: dict(state=state))
    monkeypatch.setattr(module, 'phone', phone)
    monkeypatch.setattr(module, 'device', phone.device)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                     base_url='http://127.0.0.1') as client:
            response = await client.post(path, json=payload)
        assert response.status_code == 409
        assert state in response.json()['detail']
        assert 're-select iPad Agent' in response.json()['detail']
    asyncio.run(run())
    phone.device.send.assert_not_awaited()
    phone.screen.fresh.assert_not_awaited()
    assert all(row['pointer_probe_sent'] is False for row in phone.action_timings)


@pytest.mark.parametrize('problem', ['disconnected', 'unstable', 'missing_readiness', 'missing_mirror'])
@pytest.mark.parametrize('method', ['key', 'type', 'go_home', 'recover_home'])
def test_keyboard_requires_passive_controller_and_mirror_readiness(
        keyboard_phone, problem, method):
    phone = keyboard_phone
    if problem == 'disconnected':
        phone.device.connected = False
    elif problem == 'unstable':
        phone.device.readiness = lambda: dict(connected=True, stable=False, reason='waiting_for_stable_acknowledgements')
    elif problem == 'missing_readiness':
        del phone.device.readiness
    else:
        phone.screen = None
    args = ('tab',) if method == 'key' else ('IELTS',) if method == 'type' else ()
    with pytest.raises(PhoneUnavailable):
        asyncio.run(getattr(phone, method)(*args))
    # An unacknowledged link may be pinged to refresh its ACK evidence; nothing else is sent.
    assert [call.args[0] for call in phone.device.send.await_args_list] == (
        ['ping', 'ping'] if problem == 'unstable' else [])


@pytest.mark.parametrize('method,args', [('tap', (.5, .5)), ('point', (.5, .5)),
                                         ('swipe', (.5, .8, .5, .2))])
def test_pointer_actions_keep_still_screen_probe_and_refusal(keyboard_phone, method, args, tmp_path):
    phone = keyboard_phone
    store = InputTimelines(tmp_path/'pointer-traces')
    with store.capture('/api/'+method) as trace:
        with pytest.raises(PhoneUnavailable, match='the pointer did not move'):
            asyncio.run(getattr(phone, method)(*args))
    assert [call.args[0] for call in phone.device.send.await_args_list] == [
        f'move {NUDGE} 0', f'move {-NUDGE} 0']
    assert phone.screen.fresh.await_count == 1
    assert phone.last_point_timing['pointer_probe_sent'] is True
    assert any(event.get('pointer_probe_sent') is True for event in trace['events'])


@pytest.mark.parametrize('problem,expected', [
    ('offline', 'source_offline'), ('disconnected', 'source_disconnected'),
    ('heartbeat', 'receiver_heartbeat_lost'), ('receiver', 'receiver_disconnected'),
])
def test_real_mirror_diagnostics_override_recent_cached_frame(keyboard_phone, problem, expected):
    phone = keyboard_phone
    phone.screen.updated = time.time()
    if problem == 'offline':
        phone.screen._receiver_health['source_offline_reason'] = 'client_feedback_timeout'
    elif problem == 'disconnected':
        phone.screen._receiver_health['peer_connected'] = False
    elif problem == 'heartbeat':
        phone.screen._receiver_health_at = time.monotonic() - 100
    else:
        phone.screen._sock = None
    assert phone.screen.mirror_health()['state'] == expected
    with pytest.raises(PhoneUnavailable, match=expected):
        asyncio.run(phone.key('tab'))
    phone.device.send.assert_not_awaited()


def test_offline_mirror_hint_survives_simultaneous_controller_disconnect(api, keyboard_phone, monkeypatch):
    module, _ = api
    phone = keyboard_phone
    phone.device.connected = False
    phone.screen._receiver_health['source_offline_reason'] = 'client_feedback_timeout'
    monkeypatch.setattr(module, 'phone', phone)
    monkeypatch.setattr(module, 'device', phone.device)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                     base_url='http://127.0.0.1') as client:
            response = await client.post('/api/key', json={'text': 'cmd+h'})
        assert response.status_code == 409
        assert 'source_offline' in response.json()['detail']
        assert 're-select iPad Agent' in response.json()['detail']
    asyncio.run(run())
    phone.device.send.assert_not_awaited()


@pytest.mark.parametrize('method', ['_aim', '_find_pointer'])
def test_observation_probes_are_recorded_even_without_stale_liveness_probe(keyboard_phone, method, tmp_path):
    phone = keyboard_phone
    phone.screen.updated = time.time()
    phone.screen.settled = AsyncMock()
    phone._locate_after_nudge = AsyncMock(return_value=(.5, .5))
    phone._active_point_timing = {'pointer_probe_sent': False}
    store = InputTimelines(tmp_path/'probe-traces')
    with store.capture(method) as trace:
        asyncio.run(phone._aim(.5, .5) if method == '_aim' else phone._find_pointer())
    assert phone._active_point_timing['pointer_probe_sent'] is True
    assert any(event.get('pointer_probe_sent') is True for event in trace['events'])


@pytest.mark.parametrize('delivery,possible', [('unknown', True), ('not_sent', False)])
def test_probe_delivery_failure_never_claims_zero_pointer_after_unknown_write(keyboard_phone, delivery, possible):
    phone = keyboard_phone
    phone.device.send.side_effect = DeviceError('lost probe receipt', delivery=delivery, command_name='move')
    with pytest.raises(DeviceError):
        asyncio.run(phone.point(.5, .5))
    assert phone.last_point_timing['pointer_probe_sent'] is possible


@pytest.mark.parametrize('method,payload,expected', [
    ('key', 'cmd+space', 'key cmd+space'), ('type', 'Camera', 'type Camera'),
])
def test_passive_keyboard_check_preserves_exact_command_lease(keyboard_phone, monkeypatch, method, payload, expected):
    from hidagent import camera_conversation as camera, hid_lease
    monkeypatch.setattr(camera, 'active_any', lambda: True)
    monkeypatch.setattr(hid_lease, '_camera_block', camera._hid_blocked)
    with camera.camera_hid_window(camera.LAUNCH_WHITELIST):
        asyncio.run(getattr(keyboard_phone, method)(payload))
    assert [call.args[0] for call in keyboard_phone.device.send.await_args_list] == [expected]
