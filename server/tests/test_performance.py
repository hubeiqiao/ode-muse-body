"""Account for animation waits, interrupted moves and terminal rounds."""
import asyncio

import pytest

from hidagent.agent import Agent
from hidagent.debug import Trace


@pytest.mark.parametrize("mode", ["normal", "failed", "done"])
def test_round_timing_keeps_execution_and_error_semantics(monkeypatch, mode):
    clock = [0.0]
    monkeypatch.setattr("hidagent.agent.time.perf_counter", lambda: clock[0])
    monkeypatch.setattr("hidagent.agent.vision.changed", lambda *a, **kw: True)

    class Screen:
        frame = b"jpeg"
        async def settled(self):
            clock[0] += 4.0

    agent = Agent(None, Screen(), None, None, Trace(None))
    async def next_actions():
        clock[0] += 2.0
        return [{"action": "done" if mode == "done" else "wait", "_shot": b"jpeg"}]
    async def execute(action):
        clock[0] += 3.0
        if mode == "failed":
            raise RuntimeError("disconnected")
    agent._next_actions, agent._do = next_actions, execute

    if mode == "failed":
        with pytest.raises(RuntimeError, match="disconnected"):
            asyncio.run(agent._step())
    else:
        assert asyncio.run(agent._step()) is (mode == "done")

    record = agent.timings[0]
    assert record["decision_s"] == 2
    assert record["action_s"] == (0 if mode == "done" else 3)
    assert record["observe_s"] == (4 if mode == "normal" else 0)
    assert record["total_s"] == {"normal": 9, "failed": 5, "done": 2}[mode]
    assert record["outcome"] == {"normal": "continue", "failed": "interrupted", "done": "finished"}[mode]
    assert all("decision_s" not in step for step in agent.steps)
    agent.clear()
    assert agent.timings == []


def test_whole_task_clock_includes_initial_planning_and_clear(monkeypatch):
    from types import SimpleNamespace
    clock=[100.]
    monkeypatch.setattr('hidagent.agent.time.time',lambda:clock[0])
    a=Agent(None,SimpleNamespace(live=True),None,None,Trace(None))
    async def plan():
        clock[0]+=74
        a._record(action='plan',think_s=74)
    async def step():
        clock[0]+=42
        a._record(action='done')
        return True
    a._replan=plan;a._step=step
    async def run():
        a.start('fake read-only task')
        await a._job
    asyncio.run(run())
    assert a.finished_at-a.started_at==116
    assert a.steps[-1]['t']-a.steps[0]['t']==42
    a.clear()
    assert a.started_at is None and a.finished_at is None
