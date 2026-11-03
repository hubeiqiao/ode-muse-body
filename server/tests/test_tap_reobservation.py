"""Offline stale-baseline regressions through tap and the real pixel detector."""
import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image, ImageDraw

from hidagent import phone as module
from hidagent.phone import Phone, PhoneUnavailable


def frame(*, pointer=None, stale=False):
    im = Image.new('RGB', (1646, 1080), 'white')
    draw = ImageDraw.Draw(im)
    if stale:
        draw.rectangle((610, 440, 1030, 640), fill='gray')
    if pointer is not None:
        x, y = pointer
        draw.ellipse((x-13, y-13, x+13, y+13), fill='gray')
    out = io.BytesIO()
    im.save(out, 'PNG')
    return out.getvalue()


def phone(tmp_path, monkeypatch, *, missing=False):
    monkeypatch.setattr(module, 'POINTER_WAIT', .001)
    monkeypatch.setattr(module, 'TAP_GAP', 0)
    monkeypatch.setenv('HID_POINT_LEARNING_V2', '1')
    monkeypatch.delenv('POINTER_TWO_BLOB_CONFIRM', raising=False)
    screen = SimpleNamespace(frame=frame(stale=True), settled=AsyncMock())
    p = Phone(SimpleNamespace(links=1), tmp_path/'unused.json', screen)
    p.span = {'w': 1646, 'h': 1080}
    p._pos = (823, 540)
    p._link = 1
    p._sync_profile = lambda: False
    p.target_info = lambda: dict(device='ipad', orientation='landscape', width=1646,
                                 height=1080, calibration_key='synthetic-landscape')
    p.ensure_live = AsyncMock()
    p._move_to = AsyncMock()
    p._save = Mock()
    state = {'nudged': False, 'probes': 0}

    async def send(command):
        if command == 'move 12 0':
            state.update(nudged=True, probes=state['probes']+1)
        elif command == 'move -12 0':
            state['nudged'] = False
        elif command == 'click':
            assert state['probes'] == 2 and not missing

    async def fresh(since, timeout):
        screen.frame = frame(pointer=None if missing else
                             (835 if state['nudged'] else 823, 540))
        return screen.frame

    p._send = AsyncMock(side_effect=send)
    screen.fresh = AsyncMock(side_effect=fresh)
    return p, state


def test_stale_baseline_then_fresh_confirmation_clicks_exactly_once(tmp_path, monkeypatch):
    p, state = phone(tmp_path, monkeypatch)
    asyncio.run(p.tap(.5, .5, aim=True))
    assert state['probes'] == 2
    assert [c.args[0] for c in p._send.await_args_list] == [
        'release', 'move 12 0', 'move -12 0', 'move 12 0', 'move -12 0', 'click']
    assert p.last_point_timing['aim_reobservations'] == 1


def test_never_confirmed_pointer_refuses_after_bounded_reobservation(tmp_path, monkeypatch):
    p, state = phone(tmp_path, monkeypatch, missing=True)
    with pytest.raises(PhoneUnavailable, match='visually confirmed'):
        asyncio.run(p.tap(.5, .5, aim=True))
    assert state['probes'] == 2
    assert all(c.args[0] != 'click' for c in p._send.await_args_list)
    p._save.assert_not_called()
    assert p._pos is None


def test_link_change_during_reobservation_never_nudges_new_link(tmp_path, monkeypatch):
    p, state = phone(tmp_path, monkeypatch)
    original = p.screen.fresh.side_effect

    async def change_link(*args):
        result = await original(*args)
        if not state['nudged']:
            p.device.links += 1
        return result

    p.screen.fresh.side_effect = change_link
    with pytest.raises(PhoneUnavailable, match='binding changed'):
        asyncio.run(p.tap(.5, .5, aim=True))
    assert state['probes'] == 1
    assert all(c.args[0] != 'click' for c in p._send.await_args_list)


def test_reobservation_does_not_bypass_final_context_guard(tmp_path, monkeypatch):
    p, state = phone(tmp_path, monkeypatch)
    guard = Mock(return_value=False)
    with pytest.raises(PhoneUnavailable, match='context changed'):
        asyncio.run(p.tap(.5, .5, aim=True, pre_click=guard))
    assert state['probes'] == 2
    guard.assert_called_once()
    assert all(c.args[0] != 'click' for c in p._send.await_args_list)


def test_cancel_during_reobservation_sends_no_retry_or_click(tmp_path, monkeypatch):
    p, state = phone(tmp_path, monkeypatch)
    original = p.screen.fresh.side_effect
    async def cancelled(*args):
        if not state['nudged']:
            raise asyncio.CancelledError()
        return await original(*args)
    p.screen.fresh.side_effect = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(p.tap(.5, .5, aim=True))
    assert state['probes'] == 1
    assert all(c.args[0] != 'click' for c in p._send.await_args_list)


def test_no_fresh_restore_frame_refuses_without_second_probe(tmp_path, monkeypatch):
    p, state = phone(tmp_path, monkeypatch)
    original = p.screen.fresh.side_effect
    async def no_restore(*args):
        return await original(*args) if state['nudged'] else None
    p.screen.fresh.side_effect = no_restore
    with pytest.raises(PhoneUnavailable, match='visually confirmed'):
        asyncio.run(p.tap(.5, .5, aim=True))
    assert state['probes'] == 1
    assert all(c.args[0] != 'click' for c in p._send.await_args_list)
