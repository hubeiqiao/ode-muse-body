"""Offline reproduction of the rednote -> X/Instagram blocked launch chain."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hidagent.app_search import next_search_actions
from hidagent.decision import DecisionUnavailable
from test_app_search import fake


def blocked_agent(monkeypatch, app='X'):
    monkeypatch.setenv('APP_SEARCH_KEYBOARD_ROUTES', '0')
    monkeypatch.setenv('APP_LAUNCH_SPOTLIGHT_FIRST', '0')
    monkeypatch.setattr('hidagent.app_search.public_search_control', lambda *a, **k: None)
    monkeypatch.setattr('hidagent.home_icons.locate_home_icon', lambda *a, **k: None)
    monkeypatch.setattr('hidagent.home_icons.has_home_icon', lambda *a, **k: False)
    a = fake([], {})
    a.screen.live = True
    a.phone = SimpleNamespace(calibrated=True, target_info=lambda: dict(
        device='ipad', orientation='landscape', width=100, height=100))
    a.client.evaluate = AsyncMock(return_value=dict(answers=dict(
        page=dict(choice='blocked', confidence=.94)), seconds=.01, approved=True))
    a.trace = Mock()
    return a, dict(app=app, query='IELTS speaking')


def run(a, goal):
    return asyncio.run(next_search_actions(a, goal))


def acknowledge(a, actions):
    a.steps.extend(dict(s, act_s=.1) for s in actions)


@pytest.mark.parametrize('app', ['X', 'Instagram'])
def test_new_search_leaves_previous_blocked_surface_once(monkeypatch, app):
    a, goal = blocked_agent(monkeypatch, app)
    actions = run(a, goal)
    assert [(s['action'], s.get('combo')) for s in actions] == [('key', 'cmd+h'), ('key', 'esc')]
    acknowledge(a, actions)
    # The same request cannot repeatedly escape a genuinely blocked surface.
    with pytest.raises(DecisionUnavailable, match='[Bb]locked'):
        run(a, goal)


def test_recovery_receipt_unknown_is_consumed_even_before_steps_recorded(monkeypatch):
    a, goal = blocked_agent(monkeypatch)
    run(a, goal)
    calls = a.client.evaluate.await_count
    with pytest.raises(DecisionUnavailable, match='uncertain'):
        run(a, goal)
    assert a.client.evaluate.await_count == calls


@pytest.mark.parametrize('completed', [0, 1])
def test_partial_home_escape_never_resends_remaining_input(monkeypatch, completed):
    a, goal = blocked_agent(monkeypatch)
    actions = run(a, goal)
    a.steps.extend(dict(s, **({'act_s': .1} if i < completed else {}))
                   for i, s in enumerate(actions))
    with pytest.raises(DecisionUnavailable, match='uncertain'):
        run(a, goal)


def test_acknowledged_home_requires_new_unblocked_observation_before_spotlight(monkeypatch):
    a, goal = blocked_agent(monkeypatch)
    acknowledge(a, run(a, goal))
    a.client.evaluate.return_value['answers']['page'] = dict(choice='other', confidence=1)
    actions = run(a, goal)
    assert [(s['action'], s.get('combo')) for s in actions] == [('key', 'cmd+space')]
    assert a.client.evaluate.await_count == 2


def test_new_steps_release_old_uncertainty_without_replaying_old_request(monkeypatch):
    a, goal = blocked_agent(monkeypatch)
    first = run(a, goal)
    with pytest.raises(DecisionUnavailable, match='uncertain'):
        run(a, goal)
    a.steps = []  # Agent.clear/start establishes a distinct request ledger.
    other = dict(app='Instagram', query='morning routines')
    second = run(a, other)
    assert second == first
    with pytest.raises(DecisionUnavailable, match='uncertain'):
        run(a, other)


@pytest.mark.parametrize('prior', [
    dict(action='key', combo='cmd+space', workflow='keyboard', phase='open_search'),
    dict(action='type_uncertain'),
    dict(action='type', text='IELTS', workflow='app_search', phase='query'),
    dict(action='tap', workflow='app_search', phase='search_control'),
])
def test_same_request_input_history_never_gets_new_home_recovery(monkeypatch, prior):
    a, goal = blocked_agent(monkeypatch)
    a.steps.append(prior)
    with pytest.raises(DecisionUnavailable):
        run(a, goal)


@pytest.mark.parametrize('condition', ['iphone', 'uncalibrated', 'not_live', 'changed_frame'])
def test_recovery_requires_current_live_calibrated_ipad(monkeypatch, condition):
    a, goal = blocked_agent(monkeypatch)
    if condition == 'iphone':
        a.phone.target_info = lambda: dict(device='iphone')
    if condition == 'uncalibrated':
        a.phone.calibrated = False
    if condition == 'not_live':
        a.screen.live = False
    if condition == 'changed_frame':
        async def changed(*args):
            a.screen.frame += b'changed'
            return dict(answers=dict(page=dict(choice='blocked', confidence=.94)), seconds=.01)
        a.client.evaluate.side_effect = changed
    with pytest.raises(DecisionUnavailable):
        run(a, goal)
