"""Offline regressions for the filmed read-only public search request."""
import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from hidagent.app_search import PREFIX, match_search_intent, next_search_actions, search_goal
from hidagent.app_tools import match_goal
from hidagent.decision import DecisionUnavailable

QUERY = 'how to cook Costco shrimp recipes'
GOAL = {'app': 'rednote', 'query': QUERY, 'read_result': True}
BOX = {'left': .03, 'top': .25, 'right': .25, 'bottom': .78}
PANE = {'left': .65, 'top': .25, 'right': .94, 'bottom': .80}
TITLE = 'Stir-fry Costco white shrimp in 10 minutes'


def result(**changes):
    return dict(ready=True, title=TITLE, author='@cook', target=BOX, **changes)


def detail(*, more=False, body='Thaw shrimp. Pat dry. Stir fry with garlic.', facts=None, **changes):
    return dict(ready=True, title=TITLE, author='@cook', body=body,
                facts=facts if facts is not None else ['Thaw shrimp.', 'Pat dry.', 'Stir fry with garlic.'],
                more=more, text_pane=PANE, back=BOX, **changes)


def agent(responses, phases=()):
    buf = io.BytesIO(); Image.new('RGB', (1646, 1080), 'white').save(buf, format='PNG')
    return SimpleNamespace(task=PREFIX+json.dumps(GOAL),
        steps=[{'workflow':'app_search', 'phase':p, 'act_s':.1} for p in phases],
        screen=SimpleNamespace(frame=buf.getvalue(), live=True, updated=1),
        phone=SimpleNamespace(calibrated=True, target_info=lambda:{'device':'ipad', 'orientation':'landscape',
                            'width':1646, 'height':1080, 'calibration_key':'ipad-landscape-66'},
                            device=SimpleNamespace(links=1)),
        actor=SimpleNamespace(chat=AsyncMock(side_effect=[json.dumps(r) for r in responses])), trace=Mock())


def ack(a, action):
    a.steps.append(dict(action, act_s=.1))
    # Distinct current frame after each input, as real AirPlay would provide.
    buf=io.BytesIO(); Image.new('RGB',(1646,1080),(100+len(a.steps),)*3).save(buf,format='PNG')
    a.screen.frame=buf.getvalue(); a.screen.updated+=1


@pytest.mark.parametrize('goal', [
    'Open rednote and search for '+QUERY+', look at a post, and tell me',
    'Search rednote for '+QUERY+' and tell me',
    'Open 小红书 and search for '+QUERY+' and read a post',
    'Search rednote for a Costco shrimp recipe',
])
def test_compound_voice_routes_one_research_search(goal):
    expected_query = 'a Costco shrimp recipe' if goal.endswith('recipe') else QUERY
    assert match_goal(goal) == ('xiaohongshu.search', {'query':expected_query, 'read_result':True})


def test_research_goal_round_trip():
    assert search_goal(PREFIX+json.dumps(GOAL)) == GOAL


@pytest.mark.parametrize('action',['like','follow','comment','save','DM','post'])
def test_engagement_cannot_hide_in_readonly_search(action):
    assert match_search_intent('Open rednote and search for shrimp and '+action+' it') is None


def test_rednote_prompt_permits_only_readonly_research():
    from hidagent.ipad_demo_flows import TOOL
    text=TOOL['description']
    assert 'or access rednote' not in text
    assert 'read-only' in text and 'rednote' in text


def test_voice_cross_tool_duplicate_is_one_dispatch():
    from hidagent.app_tools import AppTools
    from test_live_voice import fixture
    async def run():
        b,state,starts=fixture(); sid='x'*32
        state.update(controller={'stable':True}, target={'device':'ipad'})
        b.app_tools=AppTools(b,SimpleNamespace(model='test'),None,lambda:state,AsyncMock())
        await b.execute('phone_command',{'goal':'Open rednote and search for '+QUERY+' and tell me'},sid,'first')
        duplicate=await b.execute('use_app_tool',{'tool_name':'xiaohongshu.search','args':{'query':QUERY,'read_result':True}},sid,'second')
        assert duplicate.get('deduplicated') or duplicate['status']=='busy'
        assert len(starts)==1 and starts[0].startswith(PREFIX)
    asyncio.run(run())


@pytest.mark.parametrize('action',['like','follow','comment','save','DM','post'])
def test_voice_engagement_is_refused_before_general_loop(action):
    from test_live_voice import fixture
    async def run():
        b,_,starts=fixture()
        out=await b.execute('phone_command',{'goal':'Open rednote and search for shrimp and '+action+' it'},'x'*32,'forbidden')
        assert out['status']=='rejected' and not starts
    asyncio.run(run())


def test_final_voice_snapshot_preserves_research_source():
    from test_live_voice import fixture
    b,state,_=fixture()
    research={'source':{'app':'rednote','title':TITLE,'author':'@cook'},'visible_facts':['Thaw shrimp.']}
    state['agent']['steps']=[{'action':'done','answer':'Source: rednote','research':research}]
    assert b.snapshot(raw=True)['last_action']['research']==research


def test_search_dedupe_survives_case_spacing_and_missing_read_flag():
    from hidagent.app_tools import AppTools
    from test_live_voice import fixture
    async def run():
        b,state,starts=fixture();sid='x'*32
        state.update(controller={'stable':True},target={'device':'ipad'})
        b.app_tools=AppTools(b,SimpleNamespace(model='test'),None,lambda:state,AsyncMock())
        await b.execute('phone_command',{'goal':'Open rednote and search for '+QUERY+' and tell me'},sid,'first')
        out=await b.execute('use_app_tool',{'tool_name':'xiaohongshu.search','args':{'query':'  '+QUERY.upper()+'  '}},sid,'again')
        assert out['deduplicated'] and len(starts)==1
    asyncio.run(run())


@pytest.mark.parametrize('more', [False, True])
def test_fixed_read_sequence(more, monkeypatch):
    from hidagent.app_research import next_research_actions, execute_research_action
    sleep = AsyncMock()
    monkeypatch.setattr('hidagent.app_research.asyncio.sleep', sleep)
    async def run():
        a = agent([detail(more=more), detail(body='Cook until pink.', facts=[])])
        a.phone.tap = AsyncMock(); a.phone.scroll = AsyncMock()
        out = (await next_research_actions(a, GOAL, results_verified=True))[0]
        phases = []
        while out['action'] != 'done':
            phases.append(out['phase'])
            # No frame-change or pixel guard, even on an unchanged frame.
            await execute_research_action(a, out)
            a.steps.append(dict(out, act_s=.1))
            out = (await next_research_actions(a, GOAL))[0]
        assert phases == ['open_result']+(['read_more'] if more else [])+['return_results']
        assert a.phone.tap.await_args_list[0].args == (210/1646, 520/1080)
        assert a.phone.tap.await_args_list[1].args == (34/1646, 84/1080)
        assert all(not c.kwargs for c in a.phone.tap.await_args_list)
        if more: a.phone.scroll.assert_awaited_once_with(1230/1646, 600/1080, 4)
        else: a.phone.scroll.assert_not_awaited()
        assert a.actor.chat.await_count == 1+int(more)
        assert TITLE in out['answer'] and '@cook' in out['answer'] and 'rednote' in out['answer']
        assert 'Thaw shrimp.' in out['answer']
        assert sleep.await_args_list[0].args == (3,)
    asyncio.run(run())


def test_no_input_replay_or_planner_fallback(monkeypatch):
    from hidagent.app_research import next_research_actions, next_research_route
    async def run():
        a = agent([])
        out = (await next_research_actions(a, GOAL, results_verified=True))[0]
        with pytest.raises(DecisionUnavailable): await next_research_actions(a, GOAL)
        a._general_planner_actions = AsyncMock(side_effect=AssertionError('No fallback'))
        monkeypatch.setattr('hidagent.app_search.next_search_actions', AsyncMock(side_effect=ValueError('read failed')))
        with pytest.raises(ValueError): await next_research_route(a, GOAL)
        a._general_planner_actions.assert_not_awaited()
    asyncio.run(run())


def test_submitted_search_opens_fixed_card_without_vision():
    a = agent([], ['app_ready', 'query', 'submit'])
    out = asyncio.run(next_search_actions(a, GOAL))[0]
    assert out['phase'] == 'open_result'
    a.actor.chat.assert_not_awaited()
