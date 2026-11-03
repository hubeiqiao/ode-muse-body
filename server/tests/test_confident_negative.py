"""A confident Jev 'no' on the results wait-check skips Muse (2026-10-30 data:
112/112 such frames were also rejected by Muse, typically loading spinners)."""
import asyncio
import pytest
from hidagent.vision_judge import JudgeFallback
from test_judge_overlap import Judge, result

RESULTS_Q={'results':{'type':'noul','instructions':'results?'},'blocked':{'type':'noul','instructions':'blocked?'}}
STATE={'policy':'app_public_search','purpose':'verify public search results'}

def answers(results,blocked=0.0):
    return {'answers':{'results':{'type':'noul','noul':results},'blocked':{'type':'noul','noul':blocked}},'model':'jev','seconds':0}

def run(primary_value,state=STATE,questions=RESULTS_Q,env='1',monkeypatch=None):
    if env is None:monkeypatch.delenv('JEV_CONFIDENT_NEGATIVE',raising=False)
    else:monkeypatch.setenv('JEV_CONFIDENT_NEGATIVE',env)
    async def go():
        async def p():return answers(primary_value)
        async def b():return answers(1.0)
        c=JudgeFallback(Judge('jev',p),Judge('muse',b,'meta'))
        r=await c.evaluate(dict(state),questions,b'img')
        return r,c
    return asyncio.run(go())

def test_confident_negative_results_skips_backup(monkeypatch):
    r,c=run(.01,monkeypatch=monkeypatch)
    assert not r['approved'] and r.get('confident_negative') and not c.backup.calls

@pytest.mark.parametrize('value',[.03,.5])
def test_uncertain_results_still_ask_backup(monkeypatch,value):
    r,c=run(value,monkeypatch=monkeypatch)
    assert len(c.backup.calls)==1 and r['approved']

@pytest.mark.parametrize('env',[None,'0'])
def test_flag_off_keeps_backup(monkeypatch,env):
    r,c=run(.01,env=env,monkeypatch=monkeypatch)
    assert len(c.backup.calls)==1

def test_query_literal_check_never_short_circuits(monkeypatch):
    # Muse overruled confident Jev 'no' 8/19 times on the query check.
    state={'policy':'app_public_search','purpose':'verify query before search submission'}
    q={'ready':{'type':'noul','instructions':'q'},'blocked':{'type':'noul','instructions':'b'}}
    async def go():
        monkeypatch.setenv('JEV_CONFIDENT_NEGATIVE','1')
        async def p():return {'answers':{'ready':{'type':'noul','noul':0},'blocked':{'type':'noul','noul':0}},'model':'jev','seconds':0}
        async def b():return result(1)
        c=JudgeFallback(Judge('jev',p),Judge('muse',b,'meta'))
        await c.evaluate(state,q,b'img');return c
    assert len(asyncio.run(go()).backup.calls)==1
