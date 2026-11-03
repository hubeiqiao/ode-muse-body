"""Synthetic edge-gain regression, using the unchanged image detector."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image, ImageDraw

from hidagent import vision
from hidagent.phone import AIM_TOLERANCE, NUDGE, Phone, PhoneUnavailable

W, H = 1646, 1080


def simulator(tmp_path, monkeypatch, *, target=(.52, .866), error=(0, .03), gain=1.7,
              device='ipad', orientation='landscape', missing_at=None):
    # No transport, clocks, calibration files, or device frames are consulted.
    monkeypatch.setenv('HID_POINT_LEARNING_V2', '1')
    monkeypatch.delenv('POINTER_TWO_BLOB_CONFIRM', raising=False)
    p = Phone(SimpleNamespace(links=1), tmp_path/'never-written.json',
              SimpleNamespace(frame=b'synthetic', settled=AsyncMock()))
    p.span = {'w': 1000, 'h': 1000}
    p._pos = (round(target[0]*1000), round(target[1]*1000))
    p.target_info = lambda: dict(device=device, orientation=orientation,
                                width=W, height=H, calibration_key='synthetic')
    p._send = AsyncMock()
    p._save = Mock()
    p._active_point_timing = {}
    actual = [target[i]+error[i] for i in (0, 1)]
    observations, corrections = [], []

    async def locate(before, sent, near, shift, **kwargs):
        if missing_at == len(observations):
            return None
        frames = []
        for offset in (0, shift):
            im = Image.new('L', (W, H), 245)
            cx, cy = round((actual[0]+offset)*W), round(actual[1]*H)
            ImageDraw.Draw(im).ellipse((cx-13, cy-13, cx+13, cy+13), fill=110)
            frames.append(im)
        found = vision.pointer(*frames, near=near, shift=shift, top_margin=.025)
        assert found is not None, 'Synthetic round pointer must remain detectable'
        observations.append(found)
        return found

    async def move(dx, dy):
        corrections.append((dx, dy))
        actual[0] += dx/1000*gain
        actual[1] += dy/1000*gain

    p._locate_after_nudge = AsyncMock(side_effect=locate)
    p._move_xy = AsyncMock(side_effect=move)
    return p, observations, corrections


@pytest.mark.parametrize('target,error', [
    ((.52, .866), (0, .03)), ((.964, .6), (-.03, 0)),
    ((.05, .1), (.03, 0)), ((.5, .08), (0, .03)),
])
def test_observed_edge_overshoot_converges_within_existing_three_probes(tmp_path, monkeypatch, target, error):
    monkeypatch.setenv('POINTER_EDGE_CORRECTION', '1')
    p, observed, corrections = simulator(tmp_path, monkeypatch, target=target, error=error)
    asyncio.run(p._aim(*target))
    assert len(observed) == 3 and len(corrections) == 2
    assert all(abs(observed[-1][i]-target[i]) <= AIM_TOLERANCE for i in (0, 1))
    axis = 1 if error[1] else 0
    assert abs(corrections[1][axis]) < abs(round((target[axis]-observed[1][axis])*1000))
    assert [c.args[0] for c in p._send.await_args_list] == [
        f'move {NUDGE if target[0] < .9 else -NUDGE} 0',
        f'move {-NUDGE if target[0] < .9 else NUDGE} 0',
    ]*3
    assert all(c.args[0] != 'click' for c in p._send.await_args_list)


@pytest.mark.parametrize('flag', [None, '0', 'true'])
def test_default_off_preserves_full_corrections_and_refusal(tmp_path, monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv('POINTER_EDGE_CORRECTION', raising=False)
    else:
        monkeypatch.setenv('POINTER_EDGE_CORRECTION', flag)
    p, observed, corrections = simulator(tmp_path, monkeypatch)
    with pytest.raises(PhoneUnavailable, match='did not converge'):
        asyncio.run(p._aim(.52, .866))
    assert len(observed) == 3 and corrections == [(0, -30), (0, 21)]
    assert p._pos is None
    p._save.assert_not_called()


@pytest.mark.parametrize('kwargs', [
    {'device': 'iphone'}, {'orientation': 'portrait'},
    {'target': (.5, .5)},
])
def test_flag_does_not_change_other_devices_or_interior_targets(tmp_path, monkeypatch, kwargs):
    monkeypatch.setenv('POINTER_EDGE_CORRECTION', '1')
    p, observed, corrections = simulator(tmp_path, monkeypatch, **kwargs)
    target = kwargs.get('target', (.52, .866))
    if kwargs.get('device') == 'iphone':
        asyncio.run(p._aim(*target))  # Preserve the pre-existing iPhone single pass.
        assert len(observed) == 1
    else:
        with pytest.raises(PhoneUnavailable, match='did not converge'):
            asyncio.run(p._aim(*target))
        assert corrections == [(0, -30), (0, 21)]


@pytest.mark.parametrize('gain', [1., .7, 3.1])
def test_no_speculative_gain_for_nonovershoot_or_implausible_response(tmp_path, monkeypatch, gain):
    monkeypatch.setenv('POINTER_EDGE_CORRECTION', '1')
    p, observed, corrections = simulator(tmp_path, monkeypatch, gain=gain, error=(0, .01))
    try:
        asyncio.run(p._aim(.52, .866))
    except PhoneUnavailable:
        assert gain == 3.1
    if len(corrections) == 2:
        assert corrections[1][1] == round((.866-observed[1][1])*1000)


def test_lost_pointer_after_correction_still_refuses_without_click(tmp_path, monkeypatch):
    monkeypatch.setenv('POINTER_EDGE_CORRECTION', '1')
    p, _, corrections = simulator(tmp_path, monkeypatch, missing_at=1)
    with pytest.raises(PhoneUnavailable, match='visually confirmed'):
        asyncio.run(p._aim(.52, .866))
    assert corrections == [(0, -30)] and p._pos is None
    assert all(c.args[0] != 'click' for c in p._send.await_args_list)


def test_opt_in_records_per_round_vectors_and_actual_correction(tmp_path, monkeypatch):
    monkeypatch.setenv('POINTER_EDGE_CORRECTION', '1')
    p, observed, corrections = simulator(tmp_path, monkeypatch)
    try:
        asyncio.run(p._aim(.52, .866))
    except PhoneUnavailable:
        pass
    rounds = p._active_point_timing['aim_edge_rounds']
    assert len(rounds) == len(observed) == 3
    assert rounds[0]['error'] == pytest.approx([observed[0][0]-.52, observed[0][1]-.866])
    assert rounds[1]['correction_units'] == list(corrections[1])
    assert 1.25 <= rounds[1]['gain'][1] <= 3
    assert 'correction_units' not in rounds[-1]


def test_changed_gain_still_requires_final_observed_hit(tmp_path, monkeypatch):
    monkeypatch.setenv('POINTER_EDGE_CORRECTION', '1')
    p, observed, corrections = simulator(tmp_path, monkeypatch)
    original = p._move_xy.side_effect
    async def unstable(dx, dy):
        await original(dx, dy)
        if len(corrections) == 2:
            # Add an unmodelled disturbance after the damped command.
            p._locate_after_nudge.side_effect = AsyncMock(return_value=(.52, .9))
    p._move_xy.side_effect = unstable
    with pytest.raises(PhoneUnavailable, match='did not converge'):
        asyncio.run(p._aim(.52, .866))
    assert p._pos is None and len(corrections) == 2
    assert all(c.args[0] != 'click' for c in p._send.await_args_list)
    p._save.assert_not_called()


def test_changed_binding_cannot_reuse_local_gain(tmp_path, monkeypatch):
    monkeypatch.setenv('POINTER_EDGE_CORRECTION', '1')
    p, _, corrections = simulator(tmp_path, monkeypatch)
    original = p._move_xy.side_effect
    async def change_link(dx, dy):
        await original(dx, dy)
        p.device.links += 1
    p._move_xy.side_effect = change_link
    with pytest.raises(PhoneUnavailable, match='binding changed'):
        asyncio.run(p._aim(.52, .866))
    assert len(corrections) == 1 and p._pos is None


@pytest.mark.parametrize('flag,missing_at,accepted', [('1', None, True), ('0', None, False), ('1', 2, False)])
def test_tap_click_is_sent_only_after_final_pointer_verification(tmp_path, monkeypatch, flag, missing_at, accepted):
    from hidagent import phone as module
    monkeypatch.setattr(module, 'TAP_GAP', 0)
    monkeypatch.setenv('POINTER_EDGE_CORRECTION', flag)
    p, observed, _ = simulator(tmp_path, monkeypatch, missing_at=missing_at)
    p._sync_profile = lambda: False
    p._link = 1
    p.ensure_live = AsyncMock()
    p._move_to = AsyncMock()
    if accepted:
        asyncio.run(p.tap(.52, .866, aim=True))
        assert all(abs(observed[-1][i]-(.52, .866)[i]) <= AIM_TOLERANCE for i in (0, 1))
    else:
        with pytest.raises(PhoneUnavailable):
            asyncio.run(p.tap(.52, .866, aim=True))
    assert [c.args[0] for c in p._send.await_args_list].count('click') == int(accepted)


def test_binding_change_inside_probe_never_restores_or_corrects_new_link(tmp_path, monkeypatch):
    monkeypatch.setenv('POINTER_EDGE_CORRECTION', '1')
    p, observed, corrections = simulator(tmp_path, monkeypatch)
    original = p._locate_after_nudge.side_effect
    async def change_link(*args, **kwargs):
        found = await original(*args, **kwargs)
        if len(observed) == 2:
            p.device.links += 1
        return found
    p._locate_after_nudge.side_effect = change_link
    with pytest.raises(PhoneUnavailable, match='binding changed'):
        asyncio.run(p._aim(.52, .866))
    assert len(corrections) == 1 and p._pos is None
    assert [c.args[0] for c in p._send.await_args_list] == ['move 12 0', 'move -12 0', 'move 12 0']
