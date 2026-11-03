"""sr4 Jev returned blocked=.02, which previously skipped independent review."""
import asyncio
import pytest
from hidagent.vision_judge import JudgeFallback
from test_judge_overlap import Judge
from test_confident_negative import answers, STATE, RESULTS_Q


@pytest.mark.parametrize('blocked', [.02, 1, None])
def test_confident_results_negative_never_shortcuts_uncertain_overlay(monkeypatch, blocked):
    monkeypatch.setenv('JEV_CONFIDENT_NEGATIVE', '1')
    async def go():
        async def primary():
            r = answers(.01, blocked)
            if blocked is None: r['answers'].pop('blocked')
            return r
        async def backup(): return answers(1, 0)
        judge = JudgeFallback(Judge('jev', primary), Judge('muse', backup, 'meta'))
        result = await judge.evaluate(dict(STATE), RESULTS_Q, b'offline frame')
        assert len(judge.backup.calls) == 1
        assert result['approved'] and not result.get('confident_negative')
    asyncio.run(go())
