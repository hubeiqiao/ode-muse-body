from synthetic_assets import ASSETS
"""Synthetic offline launch regressions. No input is dispatched."""
import asyncio
import io
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from hidagent import spotlight_launch
from hidagent.home_icons import has_home_icon, home_grid_witness, locate_home_icon
from hidagent.shortcut_workflow import next_shortcut_actions

LAYOUT = ASSETS / 'home/layout.json'


def frame(size):
    output = io.BytesIO()
    Image.new('RGB', size, 'black').save(output, format='PNG')
    return output.getvalue()


@pytest.mark.parametrize('app', ['Settings', 'YouTube'])
@pytest.mark.parametrize('enabled', ['0', '1'])
def test_landscape_registered_app_respects_spotlight_flag_without_home_loop(monkeypatch, app, enabled):
    monkeypatch.setenv('APP_ICON_LAYOUT', str(LAYOUT))
    monkeypatch.setenv('APP_LAUNCH_SPOTLIGHT_FIRST', enabled)
    target = dict(device='ipad', orientation='landscape', width=1646, height=1080,
                  calibration_key='ipad-landscape-66')
    image = frame((1646, 1080))
    judge = AsyncMock(return_value={'answers': {'page': {'choice': 'other', 'confidence': .6}}, 'seconds': .1})
    agent = NS(phone=NS(target_info=lambda: target), screen=NS(frame=image), steps=[],
               client=NS(evaluate=judge), trace=Mock())
    goal = {'app': app, 'text': None}
    assert spotlight_launch.local_check(image)['state'] == 'closed'
    assert spotlight_launch.eligible(agent, goal) is (enabled == '1')
    if enabled == '1':
        # Enabled transaction/privacy contracts live in test_spotlight_launch.
        judge.assert_not_awaited()
        return
    # An uncertain generic classification must wait, never normalize Home first.
    actions = asyncio.run(next_shortcut_actions(agent, goal))
    observed = [(a['action'], a.get('combo'), a['phase']) for a in actions]
    print(f'{app} spotlight_first={enabled} actions={observed}')
    assert observed == [('wait', None, 'classify_scene')]
    judge.assert_awaited_once()
    assert 'Classify the CURRENT screen' in judge.call_args.args[1]['page']['instructions']
    # Continue the generic route using mocked observations and receipts only.
    agent.steps.extend(actions)
    judge.return_value = {'answers': {'page': {'choice': 'other', 'confidence': .99}}, 'seconds': .1}
    actions = asyncio.run(next_shortcut_actions(agent, goal))
    assert [(a['action'], a.get('combo'), a['phase']) for a in actions] == [('key', 'cmd+space', 'open_search')]
    agent.steps.extend({**a, 'act_s': 0, 't': time.time()} for a in actions)
    judge.return_value = {'answers': {'page': {'choice': 'search', 'confidence': .99}}, 'seconds': .1}
    actions = asyncio.run(next_shortcut_actions(agent, goal))
    assert [(a['action'], a.get('combo'), a.get('text'), a['phase']) for a in actions] == [
        ('key', 'cmd+a', None, 'select_query'), ('type', None, app, 'query')]
    assert not any(s.get('phase') in ('normalize_ipad_home', 'home_icon_grid') for s in agent.steps + actions)
    print(f'{app} generic continuation=cmd+space,cmd+a,type-once')


@pytest.mark.parametrize('size,orientation,expected', [
    ((710, 1080), 'portrait', True), ((1420, 2160), 'portrait', True),
    ((1646, 1080), 'landscape', False), ((1646, 1080), 'portrait', False),
    ((710, 1080), 'landscape', False), ((498, 1080), 'portrait', False),
])
def test_home_capability_requires_current_geometry(monkeypatch, size, orientation, expected):
    monkeypatch.setenv('APP_ICON_LAYOUT', str(LAYOUT))
    target = dict(device='ipad', orientation=orientation)
    image = frame(size)
    assert has_home_icon('YouTube', image, target) is expected
    if not expected:
        assert locate_home_icon(image, 'YouTube', target) is None
        assert home_grid_witness(image, target, 'Settings') is False
