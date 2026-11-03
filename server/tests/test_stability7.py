"""Retest6 regressions, using only mocked judgments and received frames."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hidagent import app_search
from hidagent.decision import DecisionUnavailable
from test_app_search import fake, assert_query_batch
from test_scene_scope import make_agent, picture, BOX


@pytest.mark.parametrize('change', [None, 'target', 'chrome', 'overlap'])
def test_reused_app_ready_samples_received_motion_before_entry_judgment(monkeypatch, change):
    async def run():
        a = make_agent('app_search', picture(40), picture(100), prior=False)
        a.steps = []
        monkeypatch.setattr('hidagent.shortcut_workflow.next_shortcut_actions', AsyncMock(
            return_value=[{'action': 'done', '_verified_foreground_entry': True}]))
        goal = {'app': 'TikTok', 'query': 'English practice'}
        ready = (await app_search.next_search_actions(a, goal))[0]
        assert ready['phase'] == 'app_ready'
        a.steps.append({**ready, 'act_s': 0, 't': time.time()})
        for level in (40, 100, 160):
            a.screen.frame = picture(level)
            a.screen.updated = time.time()
            await asyncio.sleep(.04)
        original = a.client.evaluate.side_effect

        async def evaluate(*args):
            result = await original(*args)
            a.screen.frame = picture(220, change)
            if change == 'overlap':
                result['answers']['target']['box'] = {
                    'left': .2, 'top': .3, 'right': .3, 'bottom': .4}
            return result

        a.client.evaluate.side_effect = evaluate
        out = (await app_search.next_search_actions(a, goal))[0]
        assert out['action'] == ('tap' if change is None else 'wait')
        assert out['phase'] == ('search_control' if change is None else 'scene_changed')
    asyncio.run(run())


@pytest.mark.parametrize('handoff', ['ready', 'input', 'unacknowledged', 'new_task', 'goal_changed'])
def test_reuse_samples_during_readonly_identity_at_receiver_frame_rate(monkeypatch, handoff):
    async def run():
        a = make_agent('app_search', picture(40), picture(220), prior=False)
        a.steps = []

        async def classify(*args):
            # The default receiver supplies 8 fps. A 0.15 s app_ready wait
            # alone cannot reliably provide the three required observations.
            for level in (40, 100, 160):
                a.screen.frame = picture(level)
                a.screen.updated = time.time()
                await asyncio.sleep(.125)
            return [{'action': 'done', '_verified_foreground_entry': True}]

        monkeypatch.setattr('hidagent.shortcut_workflow.next_shortcut_actions', AsyncMock(side_effect=classify))
        goal = {'app': 'TikTok', 'query': 'English practice'}
        ready = (await app_search.next_search_actions(a, goal))[0]
        if handoff == 'input':
            a.steps.append({'workflow': 'keyboard', 'phase': 'launch', 'action': 'key', 'act_s': .1})
        a.steps.append({**ready, 'act_s': 0, 't': time.time()})
        if handoff == 'unacknowledged':
            a.steps[-1].pop('act_s')
        if handoff == 'new_task':
            a.steps = list(a.steps)
        if handoff == 'goal_changed':
            goal = {**goal, 'query': 'another query'}
        out = (await app_search.next_search_actions(a, goal))[0]
        # Box-only rule (2026-10-30): a fresh judge located the target on this frame and the
        # target box itself is unchanged, so motion-history handoff no longer gates the tap.
        assert out['action'] == 'tap'
    asyncio.run(run())


def learned_field(*args, **kwargs):
    if kwargs.get('control') == 'public_search_field':
        return {'control': 'public_search_field', 'x': 870, 'y': 130,
                'target_box': BOX, 'template': 'field.json'}


@pytest.mark.parametrize('phases', [[], ['app_ready'], ['app_ready', 'search_control']])
@pytest.mark.parametrize('app', ['rednote', 'YouTube', 'App Store', 'Douyin'])
def test_cold_editor_positive_model_focus_still_taps_once_before_clearing(monkeypatch, phases, app):
    monkeypatch.setenv('APP_SEARCH_REUSE_VISIBLE_FOCUS', '1')
    monkeypatch.setattr(app_search, 'public_search_control', learned_field)
    a = fake(phases, {'ready': {'noul': 1}, 'focused': {'noul': 1}})
    a.phone = SimpleNamespace(target_info=lambda: {'device': 'ipad', 'orientation': 'landscape'})
    goal = {'app': app, 'query': 'English practice'}
    tap = asyncio.run(app_search.next_search_actions(a, goal))
    assert len(tap) == 1 and tap[0]['action'] == 'tap' and tap[0]['phase'] == 'focus'
    assert tap[0]['precise'] and (tap[0]['x'], tap[0]['y']) == (870, 130)
    a.steps.append({**tap[0], 'act_s': .1})
    actions = asyncio.run(app_search.next_search_actions(a, goal))
    assert_query_batch(actions, goal['query'])
    a.steps.extend({**action, 'act_s': .1} for action in actions)
    a.client.evaluate.return_value['answers']['ready']['noul'] = 0
    out = asyncio.run(app_search.next_search_actions(a, goal))
    assert len(out) == 1 and out[0]['phase'] == 'exact_query_visible'


def test_dirty_editor_prefers_learned_field_over_navigation_and_identity_waits(monkeypatch):
    def controls(*args, **kwargs):
        return learned_field(*args, **kwargs) or {
            'control': kwargs.get('control'), 'x': 200, 'y': 50}
    monkeypatch.setattr(app_search, 'public_search_control', controls)
    launch = AsyncMock(return_value=[{'action': 'wait', 'phase': 'foreground_identity_uncertain'}])
    monkeypatch.setattr('hidagent.shortcut_workflow.next_shortcut_actions', launch)
    a = fake([], {'ready': {'noul': 1}, 'focused': {'noul': 1}})
    a.phone = SimpleNamespace(target_info=lambda: {'device': 'ipad', 'orientation': 'landscape'})
    out = asyncio.run(app_search.next_search_actions(a, {'app': 'rednote', 'query': 'English practice'}))[0]
    assert out['action'] == 'tap' and out['phase'] == 'focus'
    assert (out['x'], out['y']) == (870, 130)
    launch.assert_not_awaited()


@pytest.mark.parametrize('phase', ['search_control', 'search_form', None])
def test_generic_cold_editor_model_focus_cannot_send_clear_or_text(phase):
    phases = ['app_ready'] + ([phase] if phase else [])
    a = fake(phases, {'ready': {'noul': 1}, 'focused': {'noul': 1},
                      'target': {'confidence': .99, 'box': BOX}})
    out = asyncio.run(app_search.next_search_actions(a, {'app': 'rednote', 'query': 'English practice'}))
    assert len(out) == 1 and out[0]['action'] == 'tap'


@pytest.mark.parametrize('proof', ['legacy_select', 'unacknowledged', 'phase_only', 'other_workflow'])
def test_focus_proof_requires_completed_field_tap_in_this_search_run(monkeypatch, proof):
    monkeypatch.setattr(app_search, 'public_search_control', learned_field)
    phases = ['app_ready', 'focus'] if proof in ('unacknowledged', 'phase_only') else ['app_ready', 'select']
    a = fake(phases, {'ready': {'noul': 1}})
    a.phone = SimpleNamespace(target_info=lambda: {'device': 'ipad'})
    if proof == 'unacknowledged':
        a.steps[-1].pop('act_s')
    if proof == 'phase_only':
        a.steps[-1].pop('action')
    if proof == 'other_workflow':
        a.steps.insert(0, {'workflow': 'keyboard', 'phase': 'focus', 'action': 'tap', 'act_s': .1})
    out = asyncio.run(app_search.next_search_actions(a, {'app': 'rednote', 'query': 'English practice'}))
    assert len(out) == 1 and out[0]['action'] == (
        'wait' if proof in ('unacknowledged', 'phase_only') else 'tap')


@pytest.mark.parametrize('change', [None, 'overlap'])
def test_learned_current_entry_keeps_motion_target_guard(monkeypatch, change):
    a = make_agent('app_search', picture(160), picture(220))
    # Keep the two same-task scene waits, but exercise the pre-app_ready entry.
    a.steps.pop(0)
    a.shots = {i - 1: frame for i, frame in a.shots.items()}
    a.phone = SimpleNamespace(target_info=lambda: {'device': 'ipad'})
    box = BOX if change is None else {'left': .2, 'top': .3, 'right': .3, 'bottom': .4}
    x, y = (870, 130) if change is None else (250, 350)
    monkeypatch.setattr(app_search, 'public_search_control', lambda *args, **kwargs: {
        'control': 'public_search_entry', 'x': x, 'y': y, 'target_box': box})
    if change == 'overlap':
        with pytest.raises(DecisionUnavailable, match='could not verify scene_changed'):
            asyncio.run(app_search.next_search_actions(a, {'app': 'TikTok', 'query': 'English practice'}))
    else:
        out = asyncio.run(app_search.next_search_actions(a, {'app': 'TikTok', 'query': 'English practice'}))[0]
        assert out['action'] == 'tap'
