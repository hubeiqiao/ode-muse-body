"""Offline first-tap regressions. All input and mirror transport is mocked."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageDraw

from hidagent import vision
from hidagent.phone import Phone, PhoneUnavailable


def frames(start=(300, 240), end=(312, 240)):
    result = []
    for point in (start, end):
        frame = Image.new('RGB', (1000, 800), 'black')
        if point is not None:
            x, y = point
            ImageDraw.Draw(frame).ellipse((x-9, y-9, x+9, y+9), fill='white')
        result.append(frame)
    return result


def make_phone(tmp_path, monkeypatch):
    monkeypatch.setenv('POINTER_HOME_LOCAL_REACQUIRE', '1')
    monkeypatch.setattr('hidagent.phone.POINTER_WAIT', .15)
    before, after = frames()
    screen = SimpleNamespace(live=True, frame=before, settled=AsyncMock(),
                             fresh=AsyncMock(return_value=after))
    p = Phone(SimpleNamespace(links=1, connected=True), tmp_path/'cal.json', screen)
    p.span = {'w': 1000, 'h': 800}
    p._pos = (300, 240)
    p._link = 1
    p.target = {'device': 'ipad', 'orientation': 'landscape', 'width': 1000,
                'height': 800, 'calibration_key': 'ipad-landscape-test'}
    p.target_info = lambda: p.target
    p._sync_profile = lambda: False
    p._home_fraction = lambda: 1
    p._send = AsyncMock()
    p._move_xy = AsyncMock()
    p.ensure_live = AsyncMock()
    p.ensure_keyboard_live = AsyncMock()
    p._active_point_timing = {}
    # Execute the real convergence/learning path, not a fabricated cached hint.
    asyncio.run(p._aim(.3, .3))
    p._send.reset_mock()
    p.screen.fresh.reset_mock()
    return p


def home(p):
    asyncio.run(p.key('cmd+h'))
    assert p._pos is None
    p._send.reset_mock()


def test_home_launch_rechecks_local_pointer_before_short_move(tmp_path, monkeypatch):
    p = make_phone(tmp_path, monkeypatch)
    home(p)
    asyncio.run(p.key('cmd+space'))
    asyncio.run(p.key('enter'))
    p._send.reset_mock()
    asyncio.run(p._move_to(320, 240))
    assert [c.args[0] for c in p._send.await_args_list] == ['move 12 0', 'move -12 0']
    assert p._move_xy.await_count == 1
    dx, dy = p._move_xy.await_args.args
    assert dx == pytest.approx(20, abs=3) and dy == 0
    assert p._move_xy.await_args.kwargs == {'point_move': True}
    assert p._pos == (320, 240)
    assert p._active_point_timing['position_source'] == 'home_local_observation'
    assert set(p.last_pointer_probe['cv_latest']) == {'near'}
    left, top, right, bottom = p.last_pointer_probe['cv_latest']['near']['roi']
    assert left < 300 < right and top < 240 < bottom
    assert right-left <= 80 and bottom-top <= 64


@pytest.mark.parametrize('reason', ['missing', 'relocated', 'far_blob', 'single_blob'])
def test_unconfirmed_hint_falls_back_to_existing_corner(tmp_path, monkeypatch, reason):
    p = make_phone(tmp_path, monkeypatch)
    home(p)
    if reason == 'missing':
        p.screen.fresh.return_value = None
    else:
        start, end = {'relocated': ((400, 240), (412, 240)),
                      'far_blob': ((800, 640), (812, 640)),
                      'single_blob': (None, (300, 240))}[reason]
        p.screen.frame, p.screen.fresh.return_value = frames(start, end)
    asyncio.run(p._move_to(320, 240))
    assert [c.args[0] for c in p._send.await_args_list] == ['move 12 0', 'move -12 0', 'home']
    p._move_xy.assert_awaited_once_with(-680, -560, point_move=True)
    assert p._active_point_timing['home_local_outcome'] == 'not_confirmed_use_existing_path'
    assert set(p.last_pointer_probe.get('cv_latest', {})) <= {'near'}


@pytest.mark.parametrize('change', ['off', 'link', 'size', 'orientation', 'span', 'axis',
                                    'stale', 'manual_forget', 'stop', 'reset', 'moved', 'iphone'])
def test_ineligible_home_hint_uses_legacy_path(tmp_path, monkeypatch, change):
    p = make_phone(tmp_path, monkeypatch)
    if change == 'moved':
        p._pos = (600, 240)
    home(p)
    if change == 'off': monkeypatch.delenv('POINTER_HOME_LOCAL_REACQUIRE')
    if change == 'link': p.device.links += 1
    if change == 'size': p.target['width'] += 1
    if change == 'orientation': p.target['orientation'] = 'portrait'
    if change == 'span': p.span['w'] += 1
    if change == 'axis': p.axes['x'] = -1
    if change == 'stale':
        monkeypatch.setattr('hidagent.phone.time.monotonic', lambda: time.perf_counter()+121)
    if change == 'manual_forget': p.forget_position()
    if change == 'stop': asyncio.run(p.release_input())
    if change == 'reset': asyncio.run(p.reset()); p._pos = None
    if change == 'iphone': p.target['device'] = 'iphone'
    p._send.reset_mock()
    asyncio.run(p._move_to(320, 240))
    commands = [c.args[0] for c in p._send.await_args_list]
    assert 'home' in commands and 'move 12 0' not in commands


def test_opt_out_does_not_record_hint(tmp_path, monkeypatch):
    p = make_phone(tmp_path, monkeypatch)
    p.forget_position()
    monkeypatch.delenv('POINTER_HOME_LOCAL_REACQUIRE')
    p._pos = (300, 240)
    asyncio.run(p._aim(.3, .3))
    monkeypatch.setenv('POINTER_HOME_LOCAL_REACQUIRE', '1')
    home(p)
    asyncio.run(p._move_to(320, 240))
    assert [c.args[0] for c in p._send.await_args_list] == ['home']


@pytest.mark.parametrize('failure', ['cancel', 'link', 'geometry', 'uncertain_restore'])
def test_interrupted_local_probe_cannot_move_or_click(tmp_path, monkeypatch, failure):
    p = make_phone(tmp_path, monkeypatch)
    home(p)
    async def fresh(*args):
        if failure == 'cancel': raise asyncio.CancelledError()
        if failure == 'link': p.device.links += 1
        if failure == 'geometry': p.target['width'] += 1
        return frames()[1]
    p.screen.fresh.side_effect = fresh
    if failure == 'uncertain_restore':
        from hidagent.device import DeviceError
        p._send.side_effect = [None, DeviceError('Uncertain receipt')]
    with pytest.raises((asyncio.CancelledError, PhoneUnavailable, RuntimeError)):
        asyncio.run(p._move_to(320, 240))
    p._move_xy.assert_not_awaited()
    assert p._pos is None
    commands = [c.args[0] for c in p._send.await_args_list]
    assert 'home' not in commands and 'click' not in commands
    assert commands == (['move 12 0', 'move -12 0'] if failure == 'uncertain_restore' else ['move 12 0'])


def test_local_reacquisition_still_requires_final_aim(tmp_path, monkeypatch):
    p = make_phone(tmp_path, monkeypatch)
    home(p)
    p._aim = AsyncMock(side_effect=PhoneUnavailable('Target not confirmed'))
    with pytest.raises(PhoneUnavailable, match='Target not confirmed'):
        asyncio.run(p.tap(.32, .3, aim=True))
    assert p.last_point_timing['position_source'] == 'home_local_observation'
    assert 'click' not in [c.args[0] for c in p._send.await_args_list]


@pytest.mark.parametrize('mode', ['off', 'iphone'])
def test_predicted_position_never_accepts_far_global_map_blob(tmp_path, monkeypatch, mode):
    p = make_phone(tmp_path, monkeypatch)
    monkeypatch.delenv('POINTER_HOME_LOCAL_REACQUIRE')
    monkeypatch.setenv('HID_POINT_LOCAL_FIRST', '0')
    if mode == 'iphone': p.target['device'] = 'iphone'
    p.screen.frame, far = frames((800, 640), (812, 640))
    initial, local = frames((300, 240), (312, 240))
    p.screen.frame.paste(initial.crop((270, 210, 340, 270)), (270, 210))
    far.paste(initial.crop((270, 210, 340, 270)), (270, 210))
    # First pair: only a distant map animation. Second pair: the actual local pointer.
    second = p.screen.frame.copy()
    second.paste(local.crop((270, 210, 340, 270)), (270, 210))
    p.screen.fresh.side_effect = [far, second]
    found = asyncio.run(p._locate_after_nudge(p.screen.frame, 0, (.3, .3), .012))
    assert found == pytest.approx((.3, .3), abs=.002)
    assert p.screen.fresh.await_count == 2


@pytest.mark.parametrize('start,end,accepted', [((300, 240), (312, 240), True),
    (None, (300, 240), False), ((312, 240), (300, 240), False)])
def test_local_motion_confirmation_is_explicit_and_temporal(start, end, accepted):
    before, after = frames(start, end)
    found = vision.pointer(before, after, (.3, .3), .012, reach=(.04, .04), confirm_motion=True)
    assert (found is not None) is accepted
