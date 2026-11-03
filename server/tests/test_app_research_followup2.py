"""Real restored-search fixture and production HybridAgent fallback regressions."""
import asyncio
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from PIL import Image, ImageDraw

from hidagent.hybrid_agent import HybridAgent
from hidagent.decision import DecisionUnavailable
from test_app_research import agent, GOAL, ack, result

FIXTURE = Path(__file__).parent / 'fixtures/rednote-search-screen.jpg'


def hybrid(replies=()):
    a = agent([])
    h = HybridAgent(a.phone, a.screen, Mock(model='judge'), Mock(model='planner'), a.trace)
    h.task, h.steps = a.task, a.steps
    h.actor = h.planner
    h.planner.chat = AsyncMock(side_effect=replies)
    async def evaluate(state, questions, image):
        return {'seconds': .01, 'approved': True, 'answers': {
            name: {'noul': 0 if name == 'blocked' else 1} for name in questions}}
    h.client.evaluate = AsyncMock(side_effect=evaluate)
    return h


def test_real_restored_search_is_rednote_in_production_identity_path():
    if not FIXTURE.exists(): pytest.skip('Private device frame excluded from public snapshot')
    from hidagent.foreground_reuse import identify_public_app
    a = agent([]); a.screen.frame = FIXTURE.read_bytes()
    a.client = Mock(evaluate=AsyncMock(side_effect=AssertionError('known UI needs no cloud')))
    out = asyncio.run(identify_public_app(a, a.screen.frame))
    assert out['answers']['visible_app']['choice'] == 'rednote'
    assert out['approved'] and out['answers']['visible_app']['confidence'] >= .95


@pytest.mark.parametrize('erase', [(20, 135, 250, 180), (20, 320, 250, 365), (700, 940, 950, 1080), (1450, 60, 1630, 110)])
def test_partial_search_ui_cannot_prove_identity(erase):
    if not FIXTURE.exists(): pytest.skip('Private device frame excluded from public snapshot')
    from hidagent.foreground_reuse import identify_public_app
    a = agent([]); im = Image.open(FIXTURE).convert('RGB')
    ImageDraw.Draw(im).rectangle(erase, fill='white'); b = io.BytesIO(); im.save(b, format='PNG')
    a.client = Mock(evaluate=AsyncMock(return_value={'answers': {'visible_app': {'choice': 'other', 'confidence': 1}}, 'approved': False}))
    out = asyncio.run(identify_public_app(a, b.getvalue()))
    assert out['answers']['visible_app']['choice'] == 'other'
    a.client.evaluate.assert_awaited_once()


def test_search_without_read_result_keeps_existing_failure_behavior(monkeypatch):
    a = hybrid(['DO: wait']); a.task = 'APP_PUBLIC_SEARCH '+json.dumps({'app': 'rednote', 'query': 'shrimp'})
    monkeypatch.setattr('hidagent.app_search.next_search_actions', AsyncMock(side_effect=DecisionUnavailable('failed')))
    with pytest.raises(DecisionUnavailable): asyncio.run(a._next_actions())
    a.planner.chat.assert_not_awaited()


def test_fixture_is_accepted_by_actual_shortcut_launch_identity_gate(monkeypatch):
    if not FIXTURE.exists(): pytest.skip('Private device frame excluded from public snapshot')
    from hidagent.shortcut_workflow import next_shortcut_actions
    a = agent([]); a.screen.frame = FIXTURE.read_bytes()
    a.steps.extend([{'workflow': 'keyboard', 'phase': 'query', 'action': 'type', 'text': 'rednote', 'act_s': .1},
                    {'workflow': 'keyboard', 'phase': 'launch', 'action': 'key', 'combo': 'enter', 'act_s': .1}])
    monkeypatch.setattr('hidagent.shortcut_workflow.scene_changed', lambda *args: False)
    async def evaluate(state, questions, frame):
        assert state.get('policy') != 'public_app_identity'  # Must use real registered detector.
        answers = {}
        for name, q in questions.items():
            if name == 'page': answers[name] = {'choice': 'app', 'confidence': 1}
            else: answers[name] = {'noul': 0 if name in ('blocked', 'loading', 'launch_notice') else 1}
        return {'answers': answers, 'seconds': .01, 'approved': True}
    a.client = Mock(evaluate=AsyncMock(side_effect=evaluate))
    out = asyncio.run(next_shortcut_actions(a, {'app': 'rednote', 'text': None}))
    assert out[0]['action'] == 'done'
    assert 'rednote' in out[0]['answer']


@pytest.mark.parametrize('done,calibrated,read_result,expected', [
    (True, True, True, 'completed'),
    (False, True, True, 'input_uncertain'),
    (True, False, True, 'input_uncertain'),
    (True, True, False, 'completed'),  # general-agent fallback finished the search
])
def test_app_tool_returns_checked_planner_answer_while_retaining_no_retype_flag(done, calibrated, read_result, expected):
    from types import SimpleNamespace
    from test_app_tools import setup_tools
    answer = 'rednote | My Garlic Butter Shrimp Recipe: Cook shrimp with garlic and butter.'
    research = {'source': {'app': 'rednote'}, 'answer': answer, 'planner_fallback_used': True}
    receipt = {'status': 'model_reported_done' if done else 'failed', 'running': False,
               'calibrated': calibrated, 'input_was_sent': True, 'never_retry_typing': True,
               'last_action': {'action': 'done' if done else 'error', 'answer': answer, 'research': research}}
    bridge = SimpleNamespace(exploration=None, execute=AsyncMock(return_value=receipt), snapshot=Mock())
    tools, _, _, _ = setup_tools(bridge=bridge)
    out = asyncio.run(tools.call('research_result_123', 'xiaohongshu.search', {'query': GOAL['query'], 'read_result': read_result}))
    assert out['status'] == expected
    assert out['result']['receipt']['research'] == research
    assert out['result']['receipt']['last_action']['answer'] == answer
    assert out['result']['receipt']['never_retry_typing'] is True
