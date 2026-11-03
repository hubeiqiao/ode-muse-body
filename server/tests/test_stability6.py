"""Offline retest5 regressions; frames, judgments and all input are mocked."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hidagent import app_search, shortcut_workflow
from hidagent.decision import DecisionUnavailable
from test_app_search import fake
from test_launch_receipt_fixes import fake as launch_agent, image, result, probability


@pytest.mark.parametrize('app', ['TikTok', 'YouTube', 'Instagram', 'App Store'])
def test_verified_keyboard_foreground_does_not_recheck_autoplay(monkeypatch, app):
    a = launch_agent(monkeypatch, app)
    checked_frame = a.screen.frame
    original = a.client.evaluate.side_effect
    monkeypatch.setattr(shortcut_workflow, 'scene_changed', lambda *args: True)

    async def evaluate(state, questions, frame):
        if state.get('policy') != 'launch_open_final':
            assert frame == checked_frame
        # Retest5: every verdict is positive but received video keeps moving.
        a.screen.frame = image(color='black' if a.client.evaluate.await_count % 2 else 'red')
        if state.get('policy') == 'launch_open_final':
            return result(ready=probability(.98), blocked=probability(.07), loading=probability(.01))
        return await original(state, questions, frame)

    a.client.evaluate.side_effect = evaluate
    out = asyncio.run(shortcut_workflow.next_shortcut_actions(a, {'app': app, 'text': None}))[0]
    assert out['action'] == 'done' and out['phase'] == 'verified'
    assert out['_shot'] == checked_frame
    assert not any('stale open verdict' in c.args[0].get('purpose', '')
                   for c in a.client.evaluate.call_args_list)


@pytest.mark.parametrize('failure', ['wrong_app', 'unknown_app', 'overlay', 'loading', 'not_foremost', 'unapproved'])
def test_keyboard_foreground_exemption_keeps_identity_and_overlay_gates(monkeypatch, failure):
    a = launch_agent(monkeypatch, 'TikTok')
    original = a.client.evaluate.side_effect

    async def evaluate(state, questions, frame):
        if 'page' in questions and failure in ('overlay', 'loading'):
            return result(page={'choice': 'blocked' if failure == 'overlay' else 'loading', 'confidence': 1})
        if 'visible_app' in questions and failure in ('wrong_app', 'unknown_app'):
            return result(visible_app={'choice': 'YouTube', 'confidence': 1 if failure == 'wrong_app' else .94})
        if state.get('policy') == 'ipad_foreground':
            return {**result(ready=probability(.94 if failure == 'not_foremost' else 1)),
                    'approved': failure != 'unapproved'}
        return await original(state, questions, frame)

    a.client.evaluate.side_effect = evaluate
    try:
        actions = asyncio.run(shortcut_workflow.next_shortcut_actions(a, {'app': 'TikTok', 'text': None}))
    except DecisionUnavailable:
        return
    assert all(action['action'] not in ('done', 'type') for action in actions)


@pytest.mark.parametrize('app', ['rednote', 'YouTube', 'TikTok', 'Instagram', 'Douyin'])
def test_clear_query_uses_six_keys_and_records_no_select_all(app):
    a = fake(['app_ready', 'focus'], {'ready': {'noul': 1}})
    actions = asyncio.run(app_search.next_search_actions(a, {'app': app, 'query': 'IELTS'}))
    assert [(x['action'], x.get('combo')) for x in actions[:-1]] == [
        ('key', 'cmd+right'), ('key', 'cmd+backspace'), *[('key', 'backspace')] * 4]
    assert actions[-1]['_search_clear_sequence'] == {
        'select': None, 'caret_to_end': 'cmd+right', 'delete_to_line_start': 'cmd+backspace', 'backspaces': 4}
    assert [x['_search_clear_index'] for x in actions[:-1]] == list(range(6))
    assert sum(x['action'] == 'type' for x in actions) == 1


@pytest.mark.parametrize('path', [
    'current_learned', 'existing_learned', 'clicked_learned', 'clicked_generic',
    'legacy_select', 'search_control', 'search_form', 'entry',
    'local_literal', 'generic_literal', 'continuity',
])
def test_every_text_focus_question_requires_editing_evidence_not_keyboard_ring(monkeypatch, path):
    monkeypatch.setenv('APP_LAUNCH_SPOTLIGHT_FIRST', '0')
    monkeypatch.setenv('APP_PUBLIC_SEARCH_NATIVE_LINK', '0')
    monkeypatch.setenv('APP_SEARCH_REUSE_VISIBLE_FOCUS', '1')
    monkeypatch.setenv('HID_SEARCH_INPUT_CONTINUITY', '1' if path == 'continuity' else '0')
    monkeypatch.setattr('hidagent.navigation_observation.header_navigation_proposal', AsyncMock(return_value=None))
    monkeypatch.setattr('hidagent.app_query_text.verify_local_query', AsyncMock(return_value=None))
    monkeypatch.setattr('hidagent.app_query_text.verify_landscape_literal', AsyncMock(
        return_value={'seconds': .01} if path == 'local_literal' else None))
    phase = {'clicked_learned': 'focus', 'clicked_generic': 'focus', 'legacy_select': 'select',
             'search_control': 'search_control', 'search_form': 'search_form',
             'local_literal': 'query', 'generic_literal': 'query', 'continuity': 'query'}.get(path)
    a = fake([] if path == 'current_learned' else ['app_ready'] + ([phase] if phase else []), {})
    app = 'YouTube' if path == 'clicked_learned' else 'rednote'
    a.phone = SimpleNamespace(target_info=lambda: {'device': 'ipad', 'orientation': 'landscape'})
    if path == 'clicked_learned':
        a.steps[-1].update(_learned_search_control='public_search_field', act_s=.1)
    if path == 'continuity':
        a.steps[-1].update(action='type', text='IELTS', act_s=.1)
        a.steps.insert(-1, {'workflow': 'app_search', 'phase': 'select', 'action': 'key',
                            '_search_focus_verified': True, 'act_s': .1})

    def control(*args, **kwargs):
        if path in ('current_learned', 'existing_learned') and kwargs.get('control') == 'public_search_field':
            return {'control': 'public_search_field', 'x': 500, 'y': 50}
        return None

    monkeypatch.setattr(app_search, 'public_search_control', control)
    focus_key = 'focused' if path in (
        'current_learned', 'existing_learned', 'clicked_learned', 'search_control', 'search_form', 'entry') else 'ready'

    async def evaluate(state, questions, frame):
        text = questions[focus_key]['instructions']
        assert 'Require an actual text caret, selected text, or an editing menu inside this field.' in text
        assert 'A blue/colored Full Keyboard Access focus ring or outline around a field alone is NOT text-editing focus.' in text
        assert 'clear active-field styling' not in text and 'active search-field styling' not in text
        assert 'valid focus evidence, even when' not in text
        assert 'does not invalidate that established focus' not in text
        # A ring-only frame receives a negative focus answer, never text input.
        answers = {'ready': {'noul': 1}, 'blocked': {'noul': 0}, focus_key: {'noul': 0},
                   'target': {'confidence': 1, 'box': {'left': .2, 'right': .8, 'top': .02, 'bottom': .08}},
                   'literal': {'noul': 1}, 'editing_mode': {'choice': 'results', 'confidence': 1}}
        return result(**answers)

    a.client.evaluate.side_effect = evaluate
    actions = asyncio.run(app_search.next_search_actions(a, {'app': app, 'query': 'IELTS'}))
    a.client.evaluate.assert_awaited_once()
    assert all(x['action'] not in ('key', 'type') for x in actions)
