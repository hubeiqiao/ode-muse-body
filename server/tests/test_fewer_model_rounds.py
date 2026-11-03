"""Offline call-count and safety contracts for opt-in same-frame judgments."""
import asyncio
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from hidagent.shortcut_workflow import next_shortcut_actions
from hidagent.vision_judge import JudgeFallback


def picture(color='white'):
    output = io.BytesIO()
    Image.new('RGB', (710, 1080), color).save(output, format='PNG')
    return output.getvalue()


def fixture(monkeypatch, *, phase='query', app='Grok Bot', device='ipad',
            ack=True, text=None, page='search', ready=1, changed=False):
    monkeypatch.delenv('APP_LAUNCH_SPOTLIGHT_FIRST', raising=False)
    monkeypatch.setattr('hidagent.shortcut_workflow.scene_changed', lambda a, b: a != b)
    calls = []
    steps = [dict(workflow='keyboard', phase='query', action='type', text=app)]
    if ack:
        steps[0]['act_s'] = .2
    if phase == 'launch':
        steps.append(dict(workflow='keyboard', phase='launch', action='key', combo='enter'))
        if ack:
            steps[-1]['act_s'] = .2

    async def evaluate(state, questions, frame):
        calls.append((state, questions, frame))
        if changed:
            agent.screen.frame = picture('black')
        answers = {}
        for key in questions:
            if key == 'page':
                answers[key] = dict(type='choice', choice=page, confidence=.99)
            elif key == 'visible_app':
                answers[key] = dict(type='choice', choice=app, confidence=.99)
            else:
                answers[key] = dict(type='noul', noul=ready)
        return dict(answers=answers, seconds=.1, model='jev-test', fallback_used=False,
                    approved=JudgeFallback._approved(state, questions, answers))

    agent = SimpleNamespace(screen=SimpleNamespace(frame=picture()), steps=steps,
                            phone=SimpleNamespace(target_info=lambda: {'device': device}),
                            client=SimpleNamespace(evaluate=evaluate), trace=Mock())
    return agent, calls, dict(app=app, text=text)


def run(agent, goal):
    return asyncio.run(next_shortcut_actions(agent, goal))[0]


def test_merge_result_keeps_exact_installed_target_check_in_one_round(monkeypatch):
    monkeypatch.setenv('SHORTCUT_MERGE_APP_RESULT', '1')
    agent, calls, goal = fixture(monkeypatch)
    action = run(agent, goal)
    assert action['phase'] == 'launch' and action['combo'] == 'enter'
    assert len(calls) == 1
    assert set(calls[0][1]) == {'page', 'installed_app', 'first_result'}
    question = calls[0][1]['installed_app']['instructions']
    assert 'FIRST' in question and 'installed APP' in question and 'Grok Bot' in question
    assert 'Web pages, documents' in question
    assert action['think_s'] == .1


def test_merge_foreground_keeps_foremost_and_public_identity_checks(monkeypatch):
    monkeypatch.setenv('SHORTCUT_MERGE_FOREGROUND', '1')
    agent, calls, goal = fixture(monkeypatch, phase='launch', page='app')
    assert run(agent, goal)['action'] == 'done'
    assert len(calls) == 1
    assert set(calls[0][1]) == {'page', 'foremost'}
    assert 'NO system Spotlight overlay' in calls[0][1]['foremost']['instructions']

    agent, calls, goal = fixture(monkeypatch, phase='launch', page='app', app='YouTube')
    assert run(agent, goal)['action'] == 'done'
    assert len(calls) == 2
    identity = calls[1]
    assert identity[0]['policy'] == 'public_app_identity'
    assert 'target_app' not in identity[0] and 'app_card' not in identity[0]
    assert set(identity[1]) == {'visible_app'}


def test_later_overlay_cannot_turn_classifier_approval_into_return_approval(monkeypatch):
    monkeypatch.setenv('SHORTCUT_MERGE_APP_RESULT', '1')
    agent, calls, goal = fixture(monkeypatch, page='app')
    original = agent.client.evaluate

    async def overlay(state, questions, frame):
        result = await original(state, questions, frame)
        if 'installed_app' in questions:
            result['answers']['installed_app']['noul'] = 0
            result['approved'] = JudgeFallback._approved(state, questions, result['answers'])
        elif state['purpose'] == 'verify installed app before Return':
            result['answers']['ready']['noul'] = 0
            result['approved'] = False
        return result

    agent.client.evaluate = overlay
    assert run(agent, goal)['action'] == 'wait'


@pytest.mark.parametrize('flag,phase,page', [
    ('SHORTCUT_MERGE_APP_RESULT', 'query', 'search'),
    ('SHORTCUT_MERGE_FOREGROUND', 'launch', 'app'),
])
def test_uncertain_merged_positive_never_authorizes_action(monkeypatch, flag, phase, page):
    monkeypatch.setenv(flag, '1')
    agent, calls, goal = fixture(monkeypatch, phase=phase, page=page, ready=.8)
    assert run(agent, goal)['action'] == 'wait'


def test_merged_public_identity_disagreement_still_vetoes_completion(monkeypatch):
    monkeypatch.setenv('SHORTCUT_MERGE_FOREGROUND', '1')
    agent, calls, goal = fixture(monkeypatch, phase='launch', page='app', app='YouTube')
    original = agent.client.evaluate

    async def wrong_app(state, questions, frame):
        result = await original(state, questions, frame)
        if 'visible_app' in questions:
            result['answers']['visible_app']['choice'] = 'TikTok'
        return result

    agent.client.evaluate = wrong_app
    assert run(agent, goal)['action'] == 'wait'
    assert len(calls) == 2


def test_two_return_cap_remains_with_both_flags(monkeypatch):
    monkeypatch.setenv('SHORTCUT_MERGE_APP_RESULT', '1')
    monkeypatch.setenv('SHORTCUT_MERGE_FOREGROUND', '1')
    agent, calls, goal = fixture(monkeypatch, phase='launch')
    agent.steps.append(dict(agent.steps[-1]))
    agent.steps.append(dict(workflow='keyboard', phase='launch_tap'))
    assert run(agent, goal)['action'] == 'wait'
    assert not any('installed_app' in call[1] for call in calls)


@pytest.mark.parametrize('flag,phase,page,expected', [
    ('SHORTCUT_MERGE_APP_RESULT', 'query', 'search', {'page', 'installed_app', 'first_result'}),
    ('SHORTCUT_MERGE_FOREGROUND', 'launch', 'app', {'page', 'foremost'}),
])
@pytest.mark.parametrize('bad', ['off', 'iphone', 'payload', 'no_ack'])
def test_merges_are_default_off_and_scoped(monkeypatch, flag, phase, page, expected, bad):
    monkeypatch.delenv(flag, raising=False)
    if bad != 'off':
        monkeypatch.setenv(flag, '1')
    agent, calls, goal = fixture(monkeypatch, phase=phase, page=page,
                                device='iphone' if bad == 'iphone' else 'ipad',
                                text='ESP32QA test' if bad == 'payload' else None,
                                ack=bad != 'no_ack')
    run(agent, goal)
    assert set(calls[0][1]) == {'page'}
    assert all(set(call[1]) != expected for call in calls)


@pytest.mark.parametrize('flag,phase,page,expected', [
    ('SHORTCUT_MERGE_APP_RESULT', 'query', 'search', 'wait'),
    ('SHORTCUT_MERGE_FOREGROUND', 'launch', 'app', 'done'),
])
def test_merged_verdict_on_changed_pixels_only_allows_verified_readonly_done(monkeypatch, flag, phase, page, expected):
    monkeypatch.setenv(flag, '1')
    agent, calls, goal = fixture(monkeypatch, phase=phase, page=page, changed=True)
    action = run(agent, goal)
    assert action['action'] == expected
    assert 'combo' not in action and 'text' not in action
    assert len(calls) == 1


@pytest.mark.parametrize('page,fields,accepted', [
    ('search', {'installed_app': .9}, True),
    ('search', {'installed_app': .899}, False),
    ('search', {}, False),
    ('app', {'foremost': .99}, True),
    ('app', {'foremost': .98}, False),
    ('app', {}, False),
    ('blocked', {'installed_app': 1, 'foremost': 1}, True),
])
def test_merged_policy_preserves_each_original_gate(page, fields, accepted):
    questions = {'page': {'type': 'choice'},
                 **{key: {'type': 'noul'} for key in ('installed_app', 'foremost')}}
    answers = {'page': {'type': 'choice', 'choice': page, 'confidence': .85},
               **{key: {'type': 'noul', 'noul': value} for key, value in fields.items()}}
    # Approval for blocked is classification only, never permission to act.
    assert JudgeFallback._approved({'policy': 'shortcut_same_frame'}, questions, answers) is accepted


def test_both_flags_remove_two_serial_calls_without_repeating_input(monkeypatch):
    monkeypatch.setenv('SHORTCUT_MERGE_APP_RESULT', '1')
    monkeypatch.setenv('SHORTCUT_MERGE_FOREGROUND', '1')
    agent, calls, goal = fixture(monkeypatch)
    action = run(agent, goal)
    agent.steps.append({**action, 'act_s': .2})
    # Switch the fake's current page without changing any model answer schema.
    original = agent.client.evaluate

    async def launched(state, questions, frame):
        result = await original(state, questions, frame)
        if 'page' in result['answers']:
            result['answers']['page']['choice'] = 'app'
        result['approved'] = JudgeFallback._approved(state, questions, result['answers'])
        return result

    agent.client.evaluate = launched
    assert run(agent, goal)['action'] == 'done'
    assert len(calls) == 2
    assert len([step for step in agent.steps if step.get('phase') == 'query']) == 1
    assert len([step for step in agent.steps if step.get('phase') == 'launch']) == 1
