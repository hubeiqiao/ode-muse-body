"""Local literal checks must bind to the current clear-then-type input flow."""
import asyncio
from types import SimpleNamespace

from hidagent.app_query_text import _query_input
from hidagent.app_search import next_search_actions
from test_app_search import fake


def _executed(actions):
    return [{**a, 'act_s': .1} for a in actions]


def test_clear_sequence_then_type_binds_query_input():
    a = fake(['app_ready', 'focus'], {'ready': {'noul': 1}})
    goal = {'app': 'TikTok', 'query': 'IELTS'}
    actions = asyncio.run(next_search_actions(a, goal))
    agent = SimpleNamespace(steps=_executed(actions))
    assert _query_input(agent, goal) is not None


def test_type_without_verified_clear_does_not_bind():
    a = fake(['app_ready', 'focus'], {'ready': {'noul': 1}})
    goal = {'app': 'TikTok', 'query': 'IELTS'}
    steps = _executed(asyncio.run(next_search_actions(a, goal)))
    steps = [s for s in steps if s.get('phase') != 'clear_query']
    steps[-1] = {k: v for k, v in steps[-1].items() if k != '_search_clear_sequence'}
    assert _query_input(SimpleNamespace(steps=steps), goal) is None


def test_unacknowledged_clear_key_does_not_bind():
    a = fake(['app_ready', 'focus'], {'ready': {'noul': 1}})
    goal = {'app': 'TikTok', 'query': 'IELTS'}
    steps = _executed(asyncio.run(next_search_actions(a, goal)))
    steps[1] = {k: v for k, v in steps[1].items() if k != 'act_s'}
    assert _query_input(SimpleNamespace(steps=steps), goal) is None
