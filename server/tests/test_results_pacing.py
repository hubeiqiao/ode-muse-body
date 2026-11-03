"""Fake wall/monotonic clock, fast judge receipts, fresh frame timestamps."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from hidagent import app_search
from hidagent.decision import DecisionUnavailable
from test_app_search import fake


class Clock:
    def __init__(self, a, fresh=True):
        self.now = 100.0
        self.a, self.deliver = a, fresh
        self.checked = []
    def time(self): return self.now
    def monotonic(self): return self.now
    async def sleep(self, duration):
        self.now += duration
        if self.deliver: self.a.screen.updated = self.now + .001
    async def fresh(self, since, timeout=.8):
        await self.sleep(.02 if self.deliver else timeout)
        return self.a.screen.frame if self.deliver and self.a.screen.updated > since else None


def setup(monkeypatch, *, appears=2.5, deliver=True):
    a = fake(['app_ready', 'query', 'submit'], {})
    a.steps[-1].update(action='key', combo='enter', act_s=.1, t=100.0)
    a.screen.updated = 100.0
    clock = Clock(a, deliver)
    a.screen.fresh = clock.fresh
    monkeypatch.setattr(app_search, 'time', clock, raising=False)
    monkeypatch.setattr(app_search, 'asyncio', SimpleNamespace(sleep=clock.sleep))
    monkeypatch.setenv('JEV_CONFIDENT_NEGATIVE', '1')
    async def check(*args):
        clock.checked.append((clock.now, a.screen.updated))
        ready = clock.now >= 100 + appears
        clock.now += .1
        return {'answers': {'results': {'noul': int(ready)}, 'blocked': {'noul': 0}},
                'seconds': .1, 'confident_negative': not ready}
    a.client.evaluate = AsyncMock(side_effect=check)
    return a, clock


def step(a):
    out = asyncio.run(app_search.next_search_actions(a, {'app': 'YouTube', 'query': 'IELTS'}))
    a.steps.extend(dict(s, act_s=0) for s in out)
    return out[0]


def test_fast_negatives_cannot_exhaust_budget_before_results_arrive(monkeypatch):
    a, clock = setup(monkeypatch)
    assert step(a)['phase'] == 'results_visible'
    assert step(a)['phase'] == 'results_visible'
    assert step(a)['action'] == 'done'
    assert len(clock.checked) == 3
    assert clock.checked[-1][0] >= 102.5
    for previous, current in zip(clock.checked, clock.checked[1:]):
        assert current[0] - previous[0] >= 1.5
        assert current[1] > previous[1]
        assert current[1] >= previous[0] + 1.5


def test_two_wait_budget_still_stops_after_paced_third_negative(monkeypatch):
    a, clock = setup(monkeypatch, appears=999)
    step(a); step(a)
    with pytest.raises(DecisionUnavailable, match='results_visible'): step(a)
    assert clock.now >= 103.0


def test_stale_frame_never_rejudged_or_spends_results_budget(monkeypatch):
    a, clock = setup(monkeypatch, deliver=False)
    step(a)
    out = step(a)
    assert out['phase'] == 'results_frame'
    assert len(clock.checked) == 1
    assert sum(s['phase'] == 'results_visible' for s in a.steps) == 1


def test_flag_off_keeps_original_unpaced_behavior(monkeypatch):
    a, clock = setup(monkeypatch, appears=999)
    monkeypatch.setenv('JEV_CONFIDENT_NEGATIVE', '0')
    step(a); step(a)
    with pytest.raises(DecisionUnavailable, match='results_visible'): step(a)
    assert clock.now < 101


def test_pacing_continues_after_shortcut_then_fast_nonshortcut_negative(monkeypatch):
    a, clock = setup(monkeypatch)
    original = a.client.evaluate.side_effect
    async def mixed(*args):
        result = await original(*args)
        if len(clock.checked) == 2: result.pop('confident_negative')
        return result
    a.client.evaluate.side_effect = mixed
    step(a); step(a)
    assert step(a)['action'] == 'done'
    assert clock.checked[-1][0] >= 102.5
