import asyncio
import pytest
from hidagent.vision_judge import JudgeFallback
from hidagent.decision import DecisionUnavailable

class Judge:
    model='test'
    def __init__(self,value=None,error=False):self.value=value;self.error=error;self.calls=0
    async def evaluate(self,*args):
        self.calls+=1
        if self.error:raise DecisionUnavailable('offline')
        return {'seconds':.1,'answers':{'ready':{'noul':self.value}}}

@pytest.mark.parametrize('value,error,backup_calls',[(.99,False,0),(.85,False,1),(0,False,1),(None,True,1)])
def test_backup_only_runs_after_primary_failure_or_uncertainty(value,error,backup_calls):
    primary,backup=Judge(value,error),Judge(1)
    r=asyncio.run(JudgeFallback(primary,backup).evaluate({}, {'ready':{'type':'noul'}},b'image'))
    assert primary.calls==1 and backup.calls==backup_calls
    assert r['fallback_used']==bool(backup_calls)

def test_backup_does_not_turn_a_negative_result_into_approval():
    r=asyncio.run(JudgeFallback(Judge(.4),Judge(0)).evaluate({}, {'ready':{'type':'noul'}},b'image'))
    assert r['answers']['ready']['noul']==0


class DualJudge(Judge):
    def __init__(self,ready,blocked):super().__init__(ready);self.blocked=blocked
    async def evaluate(self,*args):
        r=await super().evaluate(*args);r['answers']['blocked']={'noul':self.blocked};return r

@pytest.mark.parametrize('verified,action,blocked,use_backup',[
    (True,'type',.02,False),(True,'paragraph',.02,False),
    (False,'type',.02,True),(True,'done',.02,True),
    (True,'type',.9,True),(True,'key',.02,True)])
def test_notes_fast_policy_is_scoped_and_checks_competing_screen(verified,action,blocked,use_backup):
    primary,backup=DualJudge(.8,blocked),DualJudge(1,0)
    state={'policy':'notes_formatting','verified_new_note':verified,'candidate':{'action':action}}
    r=asyncio.run(JudgeFallback(primary,backup).evaluate(state,{'ready':{'type':'noul'},'blocked':{'type':'noul'}},b'image'))
    assert r['approved'] and bool(backup.calls)==use_backup


def test_notes_conflicting_visual_evidence_is_never_approved():
    state={'policy':'notes_formatting','verified_new_note':True,'candidate':{'action':'type'}}
    r=asyncio.run(JudgeFallback(DualJudge(.99,.99),DualJudge(1,1)).evaluate(state,{'ready':{'type':'noul'},'blocked':{'type':'noul'}},b'image'))
    assert not r['approved']


def test_content_grounding_falls_back_when_either_identity_or_geometry_is_uncertain():
    class GroundJudge:
        model='test'
        def __init__(self,visible,confidence):self.calls=0;self.visible=visible;self.confidence=confidence
        async def evaluate(self,*args):
            self.calls+=1
            return {'seconds':.1,'answers':{'target':{'confidence':self.confidence},'visible':{'noul':self.visible},'navigation':{'noul':1}}}
    primary,backup=GroundJudge(.5,.99),GroundJudge(1,.95)
    result=asyncio.run(JudgeFallback(primary,backup).evaluate({'policy':'content_navigation'}, {}, b'image'))
    assert result['approved'] and result['fallback_used'] and backup.calls==1


def test_ipad_scroll_requires_both_ready_screen_and_confident_pane():
    from hidagent.vision_judge import JudgeFallback
    q={'ready':{'type':'noul'},'surface':{'type':'box'}}
    for ready,confidence,expected in [(1,.99,True),(1,.8,False),(.8,.99,False)]:
        assert JudgeFallback._approved({'policy':'ipad_scroll'},q,{'ready':{'noul':ready},'surface':{'confidence':confidence}}) is expected


def test_ipad_foreground_borderline_primary_uses_backup_not_false_navigation():
    primary,backup=Judge(.93),Judge(1)
    result=asyncio.run(JudgeFallback(primary,backup).evaluate({'policy':'ipad_foreground'},{'ready':{'type':'noul'}},b'image'))
    assert result['approved'] and result['fallback_used'] and backup.calls==1
    assert result['answers']['ready']['noul']==1



def test_inventory_confirmation_runs_real_vision_parser_before_threshold():
    from hidagent.vision_judge import VisionJudge,JudgeFallback
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    llm=SimpleNamespace(model='test-haiku',chat=AsyncMock(return_value='{"ready":true}'))
    judge=VisionJudge(llm)
    questions={'ready':{'type':'noul','instructions':'Verify every copied row against original pixels'}}
    result=asyncio.run(judge.evaluate({'policy':'source_note_inventory_confirmation'},questions,b'original'))
    assert isinstance(result,dict) and result['answers']['ready']['noul']==1
    assert 'PRE-INPUT' in llm.chat.await_args.args[0]
    assert llm.chat.await_args.args[2]==b'original'
    assert JudgeFallback._approved({'policy':'source_note_inventory_confirmation'},questions,result['answers'])
    assert not JudgeFallback._approved({'policy':'source_note_inventory_confirmation'},questions,{'ready':{'noul':.98}})



def test_vision_format_repair_is_bounded_and_never_retries_valid_negative():
    from hidagent.vision_judge import VisionJudge
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    q={'ready':{'type':'noul','instructions':'Is the target actually visible?'}}
    llm=SimpleNamespace(model='test',chat=AsyncMock(side_effect=['not json','{"ready":false}']))
    result=asyncio.run(VisionJudge(llm).evaluate({},q,b'original'))
    assert result['answers']['ready']['noul']==0 and result['format_repairs']==1 and llm.chat.await_count==2
    assert all(call.args[2]==b'original' for call in llm.chat.await_args_list)
    llm=SimpleNamespace(model='test',chat=AsyncMock(return_value='{"ready":false}'))
    result=asyncio.run(VisionJudge(llm).evaluate({},q,b'original'))
    assert result['format_repairs']==0 and llm.chat.await_count==1
    llm=SimpleNamespace(model='test',chat=AsyncMock(return_value='broken'))
    with pytest.raises(DecisionUnavailable):asyncio.run(VisionJudge(llm).evaluate({},q,b'original'))
    assert llm.chat.await_count==2


@pytest.mark.parametrize('readiness,confidence',[(.98,.99),(1,.89)])
def test_notes_compose_uses_backup_for_uncertain_readiness_or_location(readiness,confidence):
    class ComposeJudge:
        model='test'
        def __init__(self,ready,confidence):self.ready=ready;self.confidence=confidence;self.calls=0
        async def evaluate(self,*args):
            self.calls+=1
            return {'seconds':.1,'answers':{'ready':{'noul':self.ready},'target':{'confidence':self.confidence}}}
    primary=ComposeJudge(readiness,confidence);backup=ComposeJudge(1,.99)
    result=asyncio.run(JudgeFallback(primary,backup).evaluate({'policy':'notes_new_note_compose'}, {'ready':{'type':'noul'},'target':{'type':'box'}},b'image'))
    assert backup.calls==1 and result['approved'] and result['fallback_used']


def test_icon_postcondition_is_readonly_and_separate_from_input_policy():
 from hidagent.vision_judge import JudgeFallback
 q={'ready':{'type':'noul'},'blocked':{'type':'noul'},'loading':{'type':'noul'}}
 a={'ready':{'noul':.98},'blocked':{'noul':.02},'loading':{'noul':.01}}
 assert JudgeFallback._approved({'policy':'app_icon_ready'},q,a)
 assert not JudgeFallback._approved({'policy':'app_public_search'},q,a)
 for bad in [{'ready':{'noul':.1}}, {'blocked':{'noul':.98}}, {'loading':{'noul':.99}}]:
  assert not JudgeFallback._approved({'policy':'app_icon_ready'},q,{**a,**bad})


@pytest.mark.parametrize('ready,confidence,expected',[(1,.95,True),(.98,1,False),(1,.89,False),(0,1,False)])
def test_douyin_intro_close_requires_both_strict_presence_and_target(ready,confidence,expected):
    assert JudgeFallback._approved({'policy':'douyin_intro_dismissal'},{},{'ready':{'noul':ready},'target':{'confidence':confidence}}) is expected


def test_public_literal_policy_requires_two_finite_independent_yes_and_no_blocker():
 from hidagent.vision_judge import JudgeFallback
 q={k:{'type':'noul'} for k in ('ready','literal','blocked')}
 good={'ready':{'noul':1.0},'literal':{'noul':1.0},'blocked':{'noul':0.0}}
 assert JudgeFallback._approved({'policy':'app_public_search_literal'},q,good)
 for key in good:
  for value in (True,None,float('nan'),.5):
   bad={k:dict(v) for k,v in good.items()};bad[key]['noul']=value
   assert not JudgeFallback._approved({'policy':'app_public_search_literal'},q,bad)

