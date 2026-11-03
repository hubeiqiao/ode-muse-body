"""Calibrated iPad tap fallback: fake HID/frames only, never a live device."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from test_tap_reobservation import frame

from hidagent.agent import Agent, _key
from hidagent.device import DeviceError
from hidagent.phone import Phone, PhoneUnavailable

FIXTURES = Path(__file__).parent / 'fixtures/landscape_apps_20261101'
TARGET = dict(device='ipad', orientation='landscape', width=1646, height=1080,
              calibration_key='ipad-landscape-66')


def phone(tmp_path, monkeypatch, *, visible=False):
    monkeypatch.delenv('POINTER_CALIBRATED_FALLBACK', raising=False)
    for name in ('HID_POINTER_MS', 'HID_POINTER_PROFILE_MS', 'POINTER_AXIS_SEPARATE',
                 'HID_POINT_LEARNING_V2', 'HID_POINT_LEARNING_TRANSACTION',
                 'POINTER_HOME_LOCAL_REACQUIRE', 'IPAD_VISUAL_REACQUIRE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('hidagent.phone.TAP_GAP', 0)
    monkeypatch.setattr('hidagent.phone.SETTLE_AFTER_LONG_MOVE', 0)
    monkeypatch.setattr('hidagent.phone.POINTER_WAIT', .001)
    target = dict(TARGET)
    profile = tmp_path / f"calibration-{target['calibration_key']}.json"
    profile.write_text(json.dumps(dict(w=1646, h=1080)))
    screen = SimpleNamespace(live=True, frame=frame(), settled=AsyncMock(),
                             mirror_health=lambda: {'state': 'session_connected'})
    device = SimpleNamespace(links=1, connected=True)
    device.readiness = lambda: dict(connected=device.connected, stable=True,
                                   link_generation=device.links)
    device.send = AsyncMock(return_value='ok')
    p = Phone(device, tmp_path/'base.json', screen,
              profiles=SimpleNamespace(describe=lambda _: target))
    assert p.calibrated
    p._link = 1
    p._pos = (823, 540)
    p.ensure_live = AsyncMock()
    p._save = Mock()
    p._locate_after_nudge = AsyncMock(return_value=(.5, .5) if visible else None)
    screen.fresh = AsyncMock(return_value=screen.frame)
    return p, target


def commands(p):
    return [call.args[0] for call in p.device.send.await_args_list]


@pytest.mark.parametrize('guard', ['valid', 'disabled', 'disconnected', 'mirror_lost', 'binding_changed'])
def test_static_probe_uses_guarded_calibration_once(tmp_path, monkeypatch, guard):
    p, target = phone(tmp_path, monkeypatch)
    if guard == 'disabled':
        monkeypatch.setenv('POINTER_CALIBRATED_FALLBACK', '0')

    async def no_frame(*args, **kwargs):
        p.last_pointer_probe = {'outcome': 'no_fresh_frame', 'fresh_frames': 0}
        if guard == 'disconnected': p.device.connected = False
        if guard == 'mirror_lost': p.screen.live = False
        if guard == 'binding_changed': target['height'] += 1
        return None

    p._locate_after_nudge.side_effect = no_frame
    if guard == 'valid':
        asyncio.run(p.tap(.5, .5, aim=True))
        assert commands(p).count('click') == 1
        assert commands(p).count('home -1 -1') == 1
        assert p.last_tap_timing['aim'] == 'calibrated_unconfirmed'
        p._save.assert_not_called()
    else:
        with pytest.raises(PhoneUnavailable):
            asyncio.run(p.tap(.5, .5, aim=True))
        assert 'click' not in commands(p)


@pytest.mark.parametrize('changed', [True, False])
def test_invisible_pointer_rehomes_moves_clicks_once_and_records_effect(tmp_path, monkeypatch, caplog, changed):
    p, _ = phone(tmp_path, monkeypatch)
    p.screen.fresh.return_value = frame(stale=changed)
    with caplog.at_level('INFO', logger='hidagent.phone'):
        asyncio.run(p.tap(.5, .5, aim=True))
    assert commands(p) == ['release', 'move 0 0', 'move 12 0', 'move -12 0',
                           'home -1 -1', 'move -823 0', 'move 0 -540', 'click']
    receipt = p.last_tap_timing
    assert receipt['aim'] == 'calibrated_unconfirmed'
    assert receipt['screen_changed'] is changed
    assert receipt['screen_observed'] is True
    assert receipt['rehome_used'] is True and receipt['outcome'] == 'ok'
    assert p.action_timings[-1] == receipt
    p.screen.fresh.assert_awaited_once()
    p._save.assert_not_called()
    lines = [r.message for r in caplog.records if 'calibrated tap fallback' in r.message]
    assert len(lines) == 1
    assert '0.500' in lines[0] and 'rehome_used=true' in lines[0]
    assert f'screen_changed={str(changed).lower()}' in lines[0]


def test_visible_pointer_keeps_legacy_command_and_wait_sequence(tmp_path, monkeypatch):
    p, _ = phone(tmp_path, monkeypatch, visible=True)
    asyncio.run(p.tap(.5, .5, aim=True))
    assert commands(p) == ['release', 'move 0 0', 'move 12 0', 'move -12 0', 'click']
    assert p.screen.settled.await_args_list == [
        (( ), dict(quiet=.15, least=.1, most=.6)),
        (( ), dict(quiet=.2, least=.25, most=.8))]
    p.screen.fresh.assert_not_awaited()
    assert 'aim' not in p.last_tap_timing


def test_disabled_fallback_preserves_original_refusal(tmp_path, monkeypatch):
    p, _ = phone(tmp_path, monkeypatch)
    monkeypatch.setenv('POINTER_CALIBRATED_FALLBACK', '0')
    with pytest.raises(PhoneUnavailable, match='could not be visually confirmed'):
        asyncio.run(p.tap(.5, .5, aim=True))
    assert 'click' not in commands(p) and 'home -1 -1' not in commands(p)


def test_unprofiled_mapping_preserves_original_refusal(tmp_path, monkeypatch):
    p, target = phone(tmp_path, monkeypatch)
    p.profiles = None
    p.target_info = lambda: target
    with pytest.raises(PhoneUnavailable, match='could not be visually confirmed'):
        asyncio.run(p.tap(.5, .5, aim=True))
    assert 'click' not in commands(p) and not any(c.startswith('home') for c in commands(p))


@pytest.mark.parametrize('unsafe', ['uncalibrated', 'key', 'profile', 'link', 'unstable',
                                    'disconnected', 'mirror', 'mirror_session', 'geometry', 'axes', 'span'])
def test_missing_or_changed_fallback_binding_never_clicks(tmp_path, monkeypatch, unsafe):
    p, target = phone(tmp_path, monkeypatch)
    if unsafe == 'uncalibrated':
        p.span = None
    async def missing(*args, **kwargs):
        if unsafe == 'key': target['calibration_key'] = 'another-key'
        if unsafe == 'profile': p._profile_key = 'another-key'
        if unsafe == 'link': p.device.links += 1
        if unsafe == 'unstable': p.device.readiness = lambda: dict(connected=True, stable=False, link_generation=1)
        if unsafe == 'disconnected': p.device.connected = False
        if unsafe == 'mirror': p.screen.live = False
        if unsafe == 'mirror_session': p.screen.mirror_health = lambda: {'state': 'source_disconnected'}
        if unsafe == 'geometry': target['width'] += 1
        if unsafe == 'axes': p.axes['x'] = -1
        if unsafe == 'span': p.span['w'] += 1
        return None
    p._locate_after_nudge.side_effect = missing
    with pytest.raises((PhoneUnavailable, RuntimeError)):
        asyncio.run(p.tap(.5, .5, aim=True))
    assert 'click' not in commands(p) and 'home -1 -1' not in commands(p)


@pytest.mark.parametrize('failure', ['context', 'binding_in_guard', 'unstable_in_guard',
                                     'cancel', 'lost_home_ack', 'lost_move_ack', 'camera', 'privacy', 'overlay'])
def test_fallback_keeps_guards_cancellation_and_delivery_refusals(tmp_path, monkeypatch, failure):
    p, target = phone(tmp_path, monkeypatch)
    original_send = p.device.send
    async def send(command):
        if command.startswith('home'):
            if failure == 'cancel': raise asyncio.CancelledError()
            if failure == 'lost_home_ack': raise DeviceError('ACK lost', delivery='unknown')
        if command == 'move -823 0' and failure == 'lost_move_ack':
            raise DeviceError('ACK lost', delivery='unknown')
        return await original_send(command)
    p.device.send = AsyncMock(side_effect=send)
    def guard():
        if failure == 'binding_in_guard': target['width'] += 1
        if failure == 'unstable_in_guard': p.device.readiness = lambda: dict(connected=True, stable=False)
        if failure in ('camera', 'privacy', 'overlay'): raise PhoneUnavailable(f'{failure} guard refused')
        return failure != 'context'
    with pytest.raises((PhoneUnavailable, DeviceError, asyncio.CancelledError)):
        asyncio.run(p.tap(.5, .5, aim=True, pre_click=guard))
    assert 'click' not in commands(p)
    assert sum(c.startswith('home') for c in commands(p)) == 1


def test_real_camera_lease_blocks_fallback_home(tmp_path, monkeypatch):
    from hidagent.hid_lease import set_camera_block
    p, _ = phone(tmp_path, monkeypatch)
    set_camera_block(lambda command: command.startswith('home'))
    try:
        with pytest.raises(RuntimeError, match='camera_conversation_active'):
            asyncio.run(p.tap(.5, .5, aim=True))
    finally:
        set_camera_block(None)
    assert 'click' not in commands(p) and not any(c.startswith('home') for c in commands(p))


def test_nonconverging_aim_discards_failed_learning_before_calibrated_move(tmp_path, monkeypatch):
    p, _ = phone(tmp_path, monkeypatch)
    # Repeated plausible misses hit the actual correction limit, after legacy
    # learning has tentatively appended samples. Those samples must be rolled back.
    p._locate_after_nudge.return_value = (.52, .52)
    before = {k: list(v) for k, v in p._seen.items()}
    asyncio.run(p.tap(.5, .5, aim=True))
    assert commands(p)[-4:] == ['home -1 -1', 'move -823 0', 'move 0 -540', 'click']
    assert p.last_tap_timing['aim'] == 'calibrated_unconfirmed'
    assert {k: list(v) for k, v in p._seen.items()} == before
    p._save.assert_not_called()
    assert commands(p).count('click') == 1


def test_fallback_uses_saved_offsets_axes_and_landscape_bottom_right_anchor(tmp_path, monkeypatch):
    p, _ = phone(tmp_path, monkeypatch)
    for axis in ('x', 'y'):
        p._seen[axis].extend(((.2, .22), (.8, .82)))
    p.axes = dict(x=-1, y=1)
    asyncio.run(p.tap(.5, .5, aim=True))
    assert p._home_fraction() == 1
    assert commands(p)[-4:] == ['home 1 -1', 'move 856 0', 'move 0 -562', 'click']


@pytest.mark.parametrize('where', ['home', 'move', 'settle'])
def test_binding_change_during_fallback_never_clicks(tmp_path, monkeypatch, where):
    p, target = phone(tmp_path, monkeypatch)
    async def send(command):
        if (where == 'home' and command.startswith('home') or
                where == 'move' and command == 'move -823 0'):
            target['height'] += 1
        return 'ok'
    p.device.send.side_effect = send
    if where == 'settle':
        async def settled(**kwargs):
            if kwargs.get('least') == .25: target['height'] += 1
        p.screen.settled.side_effect = settled
    with pytest.raises(PhoneUnavailable):
        asyncio.run(p.tap(.5, .5, aim=True))
    assert 'click' not in commands(p)


@pytest.mark.parametrize('refusal', ['pointer is too far from the target; recalibrate before retrying',
                                     'no fresh mirror frame after pointer probe; no click sent',
                                     'Excluded overlay; no click sent'])
def test_other_point_refusals_never_fall_back(tmp_path, monkeypatch, refusal):
    p, _ = phone(tmp_path, monkeypatch)
    p._aim = AsyncMock(side_effect=PhoneUnavailable(refusal))
    with pytest.raises(PhoneUnavailable, match=refusal):
        asyncio.run(p.tap(.5, .5, aim=True))
    assert 'click' not in commands(p) and 'home -1 -1' not in commands(p)


def test_lost_click_ack_is_never_retried(tmp_path, monkeypatch):
    p, _ = phone(tmp_path, monkeypatch)
    async def send(command):
        if command == 'click': raise DeviceError('click ACK lost', delivery='unknown')
        return 'ok'
    p.device.send.side_effect = send
    with pytest.raises(DeviceError): asyncio.run(p.tap(.5, .5, aim=True))
    assert commands(p).count('click') == 1
    assert p.last_tap_timing['outcome'] == 'failed'


def test_no_fresh_postclick_frame_records_no_observed_change_without_retry(tmp_path, monkeypatch):
    p, _ = phone(tmp_path, monkeypatch)
    p.screen.fresh.return_value = None
    asyncio.run(p.tap(.5, .5, aim=True))
    assert p.last_tap_timing['screen_changed'] is False
    assert p.last_tap_timing['screen_observed'] is False
    assert p.last_tap_timing['outcome'] == 'ok'
    assert commands(p).count('click') == 1


@pytest.mark.parametrize('method', ['point', 'long_press'])
def test_only_tap_can_fall_back(tmp_path, monkeypatch, method):
    p, _ = phone(tmp_path, monkeypatch)
    with pytest.raises(PhoneUnavailable):
        asyncio.run(p.point(.5, .5, aim=True) if method == 'point' else p.long_press(.5, .5))
    assert 'click' not in commands(p) and 'press' not in commands(p)
    assert 'home -1 -1' not in commands(p)


@pytest.mark.parametrize('pair,point', [
    (('joespeaking-white-before', 'joespeaking-white-after-nudge12'), (958/1646, 48/1080)),
    (('app-pointer-pairs/freetalk-dot-before', 'app-pointer-pairs/freetalk-dot-after-dy15'), (1113/1646, 347/1080)),
    (('app-pointer-pairs/library-before', 'app-pointer-pairs/library-after-dx12'), (958/1646, 48/1080)),
])
@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_real_joespeaking_animation_decoys_allow_one_calibrated_click(tmp_path, monkeypatch, pair, point):
    p, _ = phone(tmp_path, monkeypatch)
    frames = [(FIXTURES/f'{name}.jpg').read_bytes() for name in pair]
    p.screen.frame = frames[0]
    del p._locate_after_nudge  # Exercise the frozen detector with the original bytes.
    state = dict(nudged=False, clicked=False)
    async def send(command):
        if command == 'move 12 0': state['nudged'] = True
        if command == 'move -12 0': state['nudged'] = False
        if command == 'click': state['clicked'] = True
        return 'ok'
    async def fresh(*args):
        p.screen.frame = frames[int(state['nudged'] and not state['clicked'])]
        return p.screen.frame
    p.device.send.side_effect = send
    p.screen.fresh.side_effect = fresh
    asyncio.run(p.tap(*point, aim=True))
    assert p.last_tap_timing['aim'] == 'calibrated_unconfirmed'
    assert commands(p).count('click') == 1
    assert commands(p).count('home -1 -1') == 1
    p._save.assert_not_called()


@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_fixture_copies_match_owner_evidence():
    manifest = json.loads((FIXTURES/'app-pointer-pairs/manifest.json').read_text())
    for name, digest in manifest['sha256'].items():
        assert hashlib.sha256((FIXTURES/'app-pointer-pairs'/name).read_bytes()).hexdigest() == digest


def test_agent_step_and_planner_keep_fallback_effect_after_later_animation(tmp_path, monkeypatch):
    p, _ = phone(tmp_path, monkeypatch)
    trace = SimpleNamespace(write=Mock())
    a = Agent(p, p.screen, None, None, trace)
    a.task, a.plan = 'tap once', 'tap once'
    a._next_actions = AsyncMock(return_value=[dict(action='tap', x=500, y=500, precise=True)])
    # Later app animation must not turn a no-change receipt into a successful tap.
    async def observe(*args): p.screen.frame = frame(stale=True)
    a._observe = observe
    asyncio.run(a._step())
    step = a.steps[-1]
    assert step['aim'] == 'calibrated_unconfirmed'
    assert step['screen_changed'] is False and step['changed'] is False
    assert _key(step) == dict(action='tap', x=500, y=500, precise=True)
    del a._next_actions
    a._ask = AsyncMock(return_value=('THINK: Wait.\nDO: wait', 0, p.screen.frame))
    asyncio.run(a._next_actions())
    assert '-> screen did NOT change' in a._ask.await_args.args[3]
