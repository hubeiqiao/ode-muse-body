import asyncio,json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from hidagent.vision_judge import VisionJudge
from hidagent.decision import DecisionUnavailable

Q={'ready':{'type':'noul','instructions':'Does the actual public field have focus?'}}

@pytest.mark.parametrize('reply,code,index',[
 ('SECRET not json','invalid_json',None),
 ('[]','root_not_object',None),
 ('{}','missing_answer',0),
 ('{"ready":"SECRET"}','boolean_required',0),
 ('{"ready":null}','boolean_required',0),
])
def test_invalid_diagnostics_are_bounded_and_contain_no_reply(reply,code,index):
 llm=SimpleNamespace(model='test',chat=AsyncMock(return_value=reply));judge=VisionJudge(llm)
 with pytest.raises(DecisionUnavailable) as caught:asyncio.run(judge.evaluate({},Q,b'public'))
 expected=[{'attempt':n,'code':code,'field_index':index} for n in (1,2)]
 assert judge.validation_diagnostics==expected
 assert caught.value.validation_diagnostics==expected
 assert llm.chat.await_count==2
 assert 'SECRET' not in str(caught.value)
 assert code in str(caught.value)


def test_repair_negative_remains_negative_and_stale_diagnostic_is_cleared():
 llm=SimpleNamespace(model='test',chat=AsyncMock(side_effect=['{"ready":"SECRET"}','{"ready":false}','{"ready":true}']))
 judge=VisionJudge(llm)
 async def run():
  result=await judge.evaluate({},Q,b'public')
  assert result['answers']['ready']['noul']==0
  assert result['validation_diagnostics']==[{'attempt':1,'code':'boolean_required','field_index':0}]
  result=await judge.evaluate({},Q,b'public')
  assert result['validation_diagnostics']==[] and judge.validation_diagnostics==[]
 asyncio.run(run())
 assert llm.chat.await_count==3


@pytest.mark.parametrize('kind,value,code',[
 ('box',{'box':{'left':.5,'right':.1,'top':0,'bottom':1},'confidence':.95},'invalid_box'),
 ('choice',{'choice':'SECRET','confidence':.95},'invalid_choice'),
])
def test_diagnostic_does_not_leak_unsupported_option_or_box(kind,value,code):
 q={'ready':{'type':kind,'instructions':'Local predefined check','criteria':{'allowed':'Allowed'}}}
 llm=SimpleNamespace(model='test',chat=AsyncMock(return_value=json.dumps({'ready':value})))
 judge=VisionJudge(llm)
 with pytest.raises(DecisionUnavailable) as caught:asyncio.run(judge.evaluate({},q,b'public'))
 assert caught.value.validation_diagnostics[-1]=={'attempt':2,'code':code,'field_index':0}
 assert 'SECRET' not in str(caught.value)


@pytest.mark.parametrize('provider,policy,expected',[('meta','precise_control_point','low'),('meta','precise_control_grounding','low'),('meta','grok_preview_target',None),('other','precise_control_point',None)])
def test_precision_profile_is_explicit_policy_scoped_and_meta_only(monkeypatch,provider,policy,expected):
 from unittest.mock import AsyncMock
 from types import SimpleNamespace
 from hidagent.vision_judge import VisionJudge
 monkeypatch.setenv('META_PRECISE_CONTROL_EFFORT','low')
 llm=SimpleNamespace(model='synthetic',provider=provider,effort='minimal',supports_native_json=False,chat=AsyncMock(return_value='{"ready":true}'))
 async def run():
  result=await VisionJudge(llm).evaluate({'policy':policy},{'ready':{'type':'noul','instructions':'Visible?'}},None)
  assert llm.chat.await_args.kwargs.get('reasoning_effort')==expected
  assert result['reasoning_effort']==(expected or 'minimal')
 asyncio.run(run())
