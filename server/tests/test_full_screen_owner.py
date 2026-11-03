"""Owner mode settings survive app margins and stale flow observations."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hidagent.full_screen_state import FullScreenState, identity
from hidagent.x_photo_flow import PhotoPostVision
from test_ipad_demo_flows import make, run


@pytest.mark.parametrize('recreated', [False, True])
def test_owner_env_beats_persisted_false_and_next_preflight(tmp_path, monkeypatch, recreated):
    monkeypatch.setenv('HIDAGENT_DATA', str(tmp_path))
    monkeypatch.delenv('IPAD_FULL_SCREEN_VERIFIED', raising=False)
    f, p, v, state, _ = make(tmp_path)
    info = p.target_info()
    stale = json.dumps({identity(info): {'verified': False, 'source': 'observed_windowed_layout'}})
    f.full_screen.path.write_text(stale)
    assert f.full_screen.verified(info) is False
    monkeypatch.setenv('IPAD_FULL_SCREEN_VERIFIED', '1')
    if recreated:
        f.full_screen = FullScreenState(p, f.memory)
    assert f.full_screen.verified(info) is True
    assert f.full_screen.proof(info) == (True, 'env')
    state['full_screen_apps'] = False
    v.observe = AsyncMock(side_effect=AssertionError('Owner override requires no OCR'))
    for _ in range(2):
        assert run(f, 'preflight')['status'] == 'ready'
    assert f.full_screen.path.read_text() == stale
    v.observe.assert_not_awaited()
    for method in (p.key, p.type, p.tap, p.go_home):
        method.assert_not_awaited()


def test_flow_cannot_persist_negative_full_screen_observation(tmp_path, monkeypatch):
    monkeypatch.setenv('HIDAGENT_DATA', str(tmp_path))
    f, p, _, _, _ = make(tmp_path)
    f.full_screen.mark(p.target_info(), True, 'settings_checkmark')
    before = f.full_screen.path.read_bytes()
    with pytest.raises(ValueError, match='Only owner configuration'):
        f.full_screen.mark(p.target_info(), False, 'observed_windowed_layout')
    assert f.full_screen.path.read_bytes() == before
