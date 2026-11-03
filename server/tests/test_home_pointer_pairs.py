"""Real Nov 1 refused-tap pairs, replayed offline against both released detectors."""
import asyncio
import importlib.machinery
import importlib.util
from unittest.mock import AsyncMock

import pytest
from test_landscape_real_frames import FIXTURES, TARGET, locate
from test_tap_reobservation import frame, phone

from hidagent.phone import PhoneUnavailable

PAIRS = FIXTURES / 'pointer-pairs'
TARGETS = dict(settings=(.685, .15), joespeaking=(.314, .695),
               discord=(.561, .51), camera=(.438, .33))


def pair(name):
    return [(PAIRS / f'{name}-{suffix}.jpg').read_bytes()
            for suffix in ('before', 'after-dx12')]


@pytest.mark.parametrize('revision', ['840b616', '286ea83'])
@pytest.mark.parametrize('name', TARGETS)
@pytest.mark.parametrize('shift_px', [12, 28])
def test_released_detector_replay(revision, name, shift_px, monkeypatch):
    monkeypatch.delenv('POINTER_TWO_BLOB_CONFIRM', raising=False)
    path = PAIRS.parents[1] / 'pointer_detector_references' / f'vision_{revision}.txt'
    loader = importlib.machinery.SourceFileLoader(f'vision_{revision}', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    detector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(detector)
    diagnostics = {}
    found = detector.pointer(*pair(name), near=(.5, .5), shift=shift_px/1646,
                             reach=(.5, .5), top_margin=.025, diagnostics=diagnostics,
                             **({'faint_motion': True} if revision == '286ea83' else {}))
    # Settings includes a whole-icon hover transition (107x108 at threshold30).
    # Contrary to the initial hypothesis, even the old detector rejected it.
    assert (found is not None) == (revision == '840b616' and name != 'settings'), diagnostics
    if found:
        assert found == pytest.approx((.191 if shift_px == 28 else .195, .693), abs=.003)


@pytest.mark.parametrize('name', ['joespeaking', 'discord', 'camera'])
@pytest.mark.parametrize('shift_px', [12, 28])
def test_real_compact_pointer_is_reacquired_anywhere(tmp_path, monkeypatch, name, shift_px):
    found, p = locate(tmp_path, monkeypatch, pair(name), shift=shift_px/1646,
                      near=TARGETS[name], anywhere=True)
    assert found == pytest.approx((.191 if shift_px == 28 else .195, .693), abs=.003), p.last_pointer_probe


def test_whole_icon_hover_is_not_a_confirmed_pointer(tmp_path, monkeypatch):
    found, _ = locate(tmp_path, monkeypatch, pair('settings'), shift=12/1646,
                      near=TARGETS['settings'], anywhere=True)
    assert found is None


@pytest.mark.parametrize('name', ['joespeaking', 'discord', 'camera'])
@pytest.mark.parametrize('guard', [True, False])
def test_real_pair_restores_correction_before_click(tmp_path, monkeypatch, name, guard):
    p, _ = phone(tmp_path, monkeypatch)
    p.target_info = lambda: dict(TARGET)
    p.screen.frame = pair(name)[0]
    state = dict(nudged=False, corrected=False)
    target = (.275, .693)  # 8% miss: outside the regressed 4% ROI, inside AIM_REJECT.

    async def send(command):
        if command == 'move 12 0': state['nudged'] = True
        if command == 'move -12 0': state['nudged'] = False

    async def fresh(*args):
        p.screen.frame = (frame(pointer=(round(target[0]*1646)+12*state['nudged'],
                                         round(target[1]*1080))) if state['corrected']
                          else pair(name)[int(state['nudged'])])
        return p.screen.frame

    async def correct(dx, dy):
        assert dx == pytest.approx(132, abs=2) and abs(dy) <= 1
        state['corrected'] = True
        await fresh()

    p._send = AsyncMock(side_effect=send)
    p.screen.fresh = AsyncMock(side_effect=fresh)
    p._move_xy = AsyncMock(side_effect=correct)
    if guard:
        asyncio.run(p.tap(*target, aim=True, pre_click=lambda: guard))
    else:
        with pytest.raises(PhoneUnavailable, match='context changed'):
            asyncio.run(p.tap(*target, aim=True, pre_click=lambda: guard))
    p._move_xy.assert_awaited_once()
    assert sum(c.args[0] == 'click' for c in p._send.await_args_list) == int(guard)
    assert p.last_pointer_check['attempt'] == 1


@pytest.mark.parametrize('name', TARGETS)
def test_supplied_large_misses_still_never_click(tmp_path, monkeypatch, name):
    p, _ = phone(tmp_path, monkeypatch)
    p.target_info = lambda: dict(TARGET)
    p.screen.frame = pair(name)[0]
    p.screen.fresh = AsyncMock(return_value=pair(name)[1])
    p._send = AsyncMock()
    with pytest.raises(PhoneUnavailable):
        asyncio.run(p.tap(*TARGETS[name], aim=True))
    assert all(c.args[0] != 'click' for c in p._send.await_args_list)
    p._save.assert_not_called()

pytestmark = pytest.mark.skip(reason="Requires original real-device before/after pointer pairs and archived detector snapshots excluded from public snapshot")
