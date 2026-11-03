import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from hidagent.calculator_workflow import calculator_goal,next_calculator_actions
from hidagent.decision import DecisionUnavailable

@pytest.mark.parametrize('text',['Open Calculator and calculate 27 times 14. Verify the displayed answer and leave it visible.','Calculate 27*14','打开计算器，计算27乘14'])
def test_bounded_arithmetic_goal(text):
 assert calculator_goal(text)=={'expression':'27*14','expected':'378'}

@pytest.mark.parametrize('text',['Calculate 27*14 then post the result','Calculate 1/0','Calculate 9999999*2','Open Calculator and type personal text'])
def test_other_actions_do_not_enter_arithmetic_route(text):assert calculator_goal(text) is None

def fake(monkeypatch,phases,confidence=1):
 monkeypatch.setattr('hidagent.calculator_workflow.scene_changed',lambda *a:False)
 return SimpleNamespace(screen=SimpleNamespace(frame=b'live'),steps=[{'workflow':'calculator','phase':p} for p in phases],
 client=SimpleNamespace(evaluate=AsyncMock(return_value={'answers':{'ready':{'noul':confidence}},'seconds':.1})),trace=SimpleNamespace(write=lambda *a,**k:None))

def test_type_expression_once_then_evaluate_then_verify(monkeypatch):
 goal=calculator_goal('Calculate 27*14')
 for phases,action in [(['clear'],'type'),(['clear','expression'],'key'),(['clear','expression','evaluate'],'done')]:
  a=fake(monkeypatch,phases);result=asyncio.run(next_calculator_actions(a,goal))[0]
  assert result['action']==action
  if action=='type':assert result['text']=='27*14'
  if action=='key':assert result['combo']=='enter'
  assert a.client.evaluate.await_args.args[0]['policy']=='calculator_keyboard'

def test_no_retyping_when_expression_not_verified(monkeypatch):
 a=fake(monkeypatch,['clear','expression'],.98)
 assert asyncio.run(next_calculator_actions(a,calculator_goal('Calculate 27*14')))[0]['action']=='wait'
 a=fake(monkeypatch,['clear','expression']+['verify_expression']*3,.98)
 with pytest.raises(DecisionUnavailable):asyncio.run(next_calculator_actions(a,calculator_goal('Calculate 27*14')))


def test_unfinished_old_expression_can_use_verified_ac_once(monkeypatch):
 a=fake(monkeypatch,['clear'],confidence=0)
 a.client.evaluate=AsyncMock(side_effect=[{'answers':{'ready':{'noul':0}},'seconds':.1},
  {'answers':{'ready':{'noul':1},'target':{'confidence':.95,'box':{'left':.8,'right':.9,'top':.5,'bottom':.6}}},'seconds':.2}])
 result=asyncio.run(next_calculator_actions(a,calculator_goal('Calculate 27*14')))[0]
 assert result['action']=='tap' and result['phase']=='clear_all' and result['precise']
 a=fake(monkeypatch,['clear','clear_all'],confidence=0)
 assert asyncio.run(next_calculator_actions(a,calculator_goal('Calculate 27*14')))[0]['action']=='wait'
 assert a.client.evaluate.await_count==1


def test_uncertain_clear_target_never_clicks_or_types(monkeypatch):
 a=fake(monkeypatch,['clear'],confidence=0)
 a.client.evaluate=AsyncMock(side_effect=[{'answers':{'ready':{'noul':0}},'seconds':.1},
  {'answers':{'ready':{'noul':1},'target':{'confidence':.89,'box':{'left':.8,'right':.9,'top':.5,'bottom':.6}}},'seconds':.2}])
 assert asyncio.run(next_calculator_actions(a,calculator_goal('Calculate 27*14')))[0]['action']=='wait'
