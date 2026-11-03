import asyncio,copy
from types import SimpleNamespace
import pytest
from hidagent.vision_judge import JudgeFallback
from hidagent.decision import DecisionUnavailable
Q={k:{'type':'noul','instructions':k} for k in ('ready','blocked')}
S={'policy':'app_public_search'}

def result(ready):return {'answers':{'ready':{'type':'noul','noul':ready},'blocked':{'type':'noul','noul':0}},'model':'recorded','seconds':0}
class Judge:
 def __init__(self,name,fn,provider=None):self.model=name;self.fn=fn;self.calls=[];self.llm=SimpleNamespace(provider=provider)
 async def evaluate(self,state,questions,image):
  self.calls.append((copy.deepcopy(state),copy.deepcopy(questions),image));return await self.fn()


def test_optin_backup_starts_before_primary_finishes(monkeypatch):
 monkeypatch.setenv('JEV_MUSE_OVERLAP_MS','0')
 async def go():
  began=asyncio.Event()
  async def primary():await began.wait();return result(.8)
  async def backup():began.set();return result(1)
  p=Judge('jev',primary);b=Judge('muse',backup,'meta');c=JudgeFallback(p,b)
  r=await asyncio.wait_for(c.evaluate(S,Q,b'public-frame'),.2)
  assert r['approved'] and r['fallback_used'];assert p.calls==b.calls and len(b.calls)==1
 asyncio.run(go())


def test_primary_accept_before_delay_never_starts_backup(monkeypatch):
 monkeypatch.setenv('JEV_MUSE_OVERLAP_MS','200')
 async def go():
  async def primary():return result(1)
  async def backup():raise AssertionError('No speculative request needed')
  p=Judge('jev',primary);b=Judge('muse',backup,'meta');r=await JudgeFallback(p,b).evaluate(S,Q,b'f')
  assert r['approved'] and not r['fallback_used'] and not b.calls
 asyncio.run(go())


def test_cancelled_owner_cancels_started_backup(monkeypatch):
 monkeypatch.setenv('JEV_MUSE_OVERLAP_MS','0')
 async def go():
  began=asyncio.Event();closed=asyncio.Event()
  async def primary():await asyncio.Future()
  async def backup():
   began.set()
   try:await asyncio.Future()
   finally:closed.set()
  p=Judge('jev',primary);b=Judge('muse',backup,'meta');task=asyncio.create_task(JudgeFallback(p,b).evaluate(S,Q,b'f'))
  await asyncio.wait_for(began.wait(),.2);task.cancel()
  with pytest.raises(asyncio.CancelledError):await task
  assert closed.is_set() and len(b.calls)==1
 asyncio.run(go())

@pytest.mark.parametrize('config,provider,policy',[(None,'meta','app_public_search'),('bad','meta','app_public_search'),('0','anthropic','app_public_search'),('0','meta','grok_preview_preview')])
def test_other_configs_providers_and_policies_remain_serial(monkeypatch,config,provider,policy):
 if config is None:monkeypatch.delenv('JEV_MUSE_OVERLAP_MS',raising=False)
 else:monkeypatch.setenv('JEV_MUSE_OVERLAP_MS',config)
 async def go():
  order=[]
  async def primary():order.append('p');await asyncio.sleep(0);order.append('p_done');return result(.8)
  async def backup():order.append('b');return result(1)
  await JudgeFallback(Judge('jev',primary),Judge('muse',backup,provider)).evaluate({'policy':policy},Q,b'f')
  assert order==['p','p_done','b']
 asyncio.run(go())


def test_backup_negative_is_preserved(monkeypatch):
 monkeypatch.setenv('JEV_MUSE_OVERLAP_MS','0')
 async def go():
  async def primary():await asyncio.sleep(0);return result(.8)
  async def backup():return result(0)
  r=await JudgeFallback(Judge('jev',primary),Judge('muse',backup,'meta')).evaluate(S,Q,b'f')
  assert not r['approved'] and r['answers']['ready']['noul']==0
 asyncio.run(go())


def test_backup_that_finishes_after_cancellation_cannot_return_approval(monkeypatch):
 monkeypatch.setenv('JEV_MUSE_OVERLAP_MS','0')
 async def go():
  began=asyncio.Event();closed=[]
  async def primary():await asyncio.Future()
  async def backup():
   began.set()
   try:await asyncio.Future()
   except asyncio.CancelledError:
    await asyncio.sleep(0);closed.append(True);return result(1)
  task=asyncio.create_task(JudgeFallback(Judge('jev',primary),Judge('muse',backup,'meta')).evaluate(S,Q,b'f'))
  await asyncio.wait_for(began.wait(),.2);task.cancel()
  with pytest.raises(asyncio.CancelledError):await task
  assert closed==[True]
 asyncio.run(go())


def test_started_unused_backup_is_cancelled_and_counted(monkeypatch):
 monkeypatch.setenv('JEV_MUSE_OVERLAP_MS','0')
 async def go():
  began=asyncio.Event();closed=[]
  async def primary():await began.wait();return result(1)
  async def backup():
   began.set()
   try:await asyncio.Future()
   finally:closed.append(True)
  c=JudgeFallback(Judge('jev',primary),Judge('muse',backup,'meta'));r=await c.evaluate(S,Q,b'f')
  assert r['approved'] and not r['fallback_used'] and closed==[True]
  assert r['backup_overlap']['started'] and r['backup_overlap']['cancelled'] and c.stats['backup_calls']==1
 asyncio.run(go())


def test_speculation_preserves_strong_primary_noneditor_veto(monkeypatch):
 from test_editor_field_evidence import answers,QUESTIONS,STATE
 monkeypatch.setenv('JEV_MUSE_OVERLAP_MS','0')
 async def go():
  async def primary():await asyncio.sleep(0);return {'model':'jev','answers':answers(mode='results',confidence=1,ready=.9),'seconds':0}
  async def backup():return {'model':'muse','answers':answers(mode='query_editor',confidence=1),'seconds':0}
  r=await JudgeFallback(Judge('jev',primary),Judge('muse',backup,'meta')).evaluate(STATE,QUESTIONS,b'f')
  assert not r['approved'] and r['answers']['editing_mode']['choice']=='results'
 asyncio.run(go())


def test_optin_known_private_frame_reaches_neither_provider_http(monkeypatch):
 import httpx
 from unittest.mock import AsyncMock
 from test_frame_privacy import known_overlay
 from hidagent.decision import Jev
 from hidagent.meta_model import MetaCloudLLM
 from hidagent.vision_judge import VisionJudge
 monkeypatch.setenv('JEV_MUSE_OVERLAP_MS','0');requests=[]
 async def go():
  p=Jev('fake-key',transport=httpx.MockTransport(lambda r:requests.append(r)))
  llm=MetaCloudLLM('muse-spark-1.3','fake-key');llm._post=AsyncMock();c=JudgeFallback(p,VisionJudge(llm))
  try:
   with pytest.raises(DecisionUnavailable):await c.evaluate(S,Q,known_overlay())
   llm._post.assert_not_awaited();assert requests==[]
  finally:await c.close()
 asyncio.run(go())


def test_definite_primary_rejection_does_not_wait_remaining_speculation_delay(monkeypatch):
 monkeypatch.setenv('JEV_MUSE_OVERLAP_MS','200')
 async def go():
  async def primary():return result(.8)
  async def backup():return result(1)
  c=JudgeFallback(Judge('jev',primary),Judge('muse',backup,'meta'))
  r=await asyncio.wait_for(c.evaluate(S,Q,b'f'),.1)
  assert r['approved'] and c.stats['backup_calls']==1
 asyncio.run(go())
