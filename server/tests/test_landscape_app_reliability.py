"""Offline regressions for the sweep's generic landscape navigation path."""
import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from hidagent.decision import DecisionUnavailable
from hidagent.vision_judge import JudgeFallback, VisionJudge
from test_hybrid_agent import make_agent


def agent(monkeypatch, reply, goal):
    a = make_agent(monkeypatch, reply)
    out = io.BytesIO()
    Image.new('RGB', (1646, 1080), 'gray').save(out, format='JPEG')
    a.screen.frame = out.getvalue()
    a.phone.target_info = lambda: {'device': 'ipad', 'orientation': 'landscape'}
    a.task = goal
    return a


@pytest.mark.parametrize('reply,goal', [
    ('DO: key cmd+a', 'Open Notes and open the Search field'),
    ('DO: type Notes', 'Open Notes and open the Search field'),
    ('DO: tap 1118 1020\nTARGET: Profile tab', 'Open TikTok and open the Profile tab'),
    ('DO: swipe 900 800 900 400', 'Open Reddit and scroll the feed'),
    ('DO: type headphones', 'Open the Amazon app and search for headphones'),
])
def test_revised_navigation_accepts_muse_when_jev_is_unavailable(monkeypatch, reply, goal):
    a = agent(monkeypatch, reply, goal)
    calls = []

    async def revised_plan(*args, **kwargs):
        a.planner.calls += 1
        return 'DO: key cmd+space' if a.planner.calls == 1 else reply

    a.planner.chat = revised_plan

    class Primary:
        model = 'jev-test'
        async def evaluate(self, state, questions, image):
            calls.append((state, questions, image))
            if len(calls) > 1:
                raise DecisionUnavailable('Jev connection failed or timed out')
            return {'seconds': .1, 'answers': {'ready': {'noul': 0},
                    'navigation': {'noul': 0}, 'blocked': {'noul': 0}}}

    llm = SimpleNamespace(model='muse-test', chat=AsyncMock(side_effect=[
        '{"ready":false,"navigation":false,"blocked":false}',
        '{"ready":true,"navigation":true,"blocked":false}',
    ]))
    a.client = JudgeFallback(Primary(), VisionJudge(llm))
    result = asyncio.run(a._next_actions())[0]
    assert result['action'] not in ('wait', 'fail')
    assert a.planner.calls == 2
    assert a.client.stats['backup_calls'] == 2
    assert all(image == a.screen.frame for _, _, image in calls)
    assert all('navigation' in questions and 'blocked' in questions for _, questions, _ in calls)
    candidate = calls[-1][0]['candidate_action']
    if candidate['action'] == 'tap':
        assert candidate['x'] == pytest.approx(1118 / 1646)
        assert candidate['y'] == pytest.approx(1020 / 1080)
        assert candidate['coordinate_space'] == 'full-image fractions 0..1'


@pytest.mark.parametrize('reply', ['DO: key enter', 'DO: tap 486 741\nTARGET: App Store Bilibili result'])
def test_spotlight_store_result_never_authorizes_launch(monkeypatch, reply):
    a = agent(monkeypatch, reply, 'Open Bilibili and open the 我的 tab')

    class Judge:
        model = 'test'
        async def evaluate(self, state, questions, image):
            # The icon alone looks right. Only full-screen launch context
            # reveals that it is a download suggestion rather than installed.
            guarded = image == a.screen.frame and 'App Store' in str(questions)
            return {'seconds': .1, 'answers': {'ready': {'noul': 0 if guarded else 1},
                    'navigation': {'noul': 0 if guarded else 1}, 'blocked': {'noul': 0}}}

    a.client = Judge()
    with pytest.raises(DecisionUnavailable):
        asyncio.run(a._next_actions())


@pytest.mark.parametrize('navigation,blocked', [(0, 0), (1, 1), (.98, 0), (1, .02)])
def test_navigation_never_bypasses_risk_or_modal_refusal(monkeypatch, navigation, blocked):
    a = agent(monkeypatch, 'DO: tap 1118 1020\nTARGET: Profile', 'Open TikTok and open the Profile tab')

    class Judge:
        model = 'test'
        async def evaluate(self, *args):
            return {'seconds': .1, 'answers': {'ready': {'noul': 1},
                    'navigation': {'noul': navigation}, 'blocked': {'noul': blocked}}}

    a.client = Judge()
    with pytest.raises(DecisionUnavailable):
        asyncio.run(a._next_actions())


@pytest.mark.parametrize('goal', ['Send a message', 'Post a reply', 'Buy headphones',
                                 'Log in to Muse', 'Change Settings', 'Delete a note'])
def test_risky_goals_keep_existing_judgment_path(monkeypatch, goal):
    a = agent(monkeypatch, 'DO: tap 800 500\nTARGET: Button', goal)
    asyncio.run(a._next_actions())
    assert a.client.calls[-1]['candidate_action']['point'] == 'center of supplied crop'


def test_readonly_policy_requires_all_three_independent_checks():
    questions = {key: {'type': 'noul'} for key in ('ready', 'navigation', 'blocked')}
    good = {'ready': {'noul': 1}, 'navigation': {'noul': 1}, 'blocked': {'noul': 0}}
    assert JudgeFallback._approved({'policy': 'landscape_readonly_navigation'}, questions, good)
    for key in good:
        for value in (None, True, float('nan'), .5):
            bad = {**good, key: {'noul': value}}
            assert not JudgeFallback._approved({'policy': 'landscape_readonly_navigation'}, questions, bad)
