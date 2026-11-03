"""Exercise the actual fallback boundary with independent model answers."""
import asyncio
import copy
import pytest
from hidagent.vision_judge import JudgeFallback

QUESTIONS={k:{'type':'noul'} for k in ('ready','literal','blocked')}
QUESTIONS['editing_mode']={'type':'choice','criteria':{k:k for k in ('query_editor','results','other')}}
STATE={'policy':'app_public_search_literal','current_run_input_continuity':True}

def answers(mode='query_editor',confidence=.9,ready=1,literal=1,blocked=0):
 return {'ready':{'type':'noul','noul':ready},'literal':{'type':'noul','noul':literal},
         'blocked':{'type':'noul','noul':blocked},
         'editing_mode':{'type':'choice','choice':mode,'confidence':confidence}}

class RecordedJudge:
 def __init__(self,model,value):self.model=model;self.value=value;self.calls=[]
 async def evaluate(self,state,questions,image):
  self.calls.append((copy.deepcopy(state),copy.deepcopy(questions),image))
  return {'model':self.model,'answers':self.value,'seconds':.01}

def run(primary=None,backup=None,state=None,image=b'one immutable frame'):
 p=RecordedJudge('jev-latest',primary if primary is not None else answers(confidence=.95,ready=.94,literal=.96,blocked=.04))
 b=RecordedJudge('muse-spark-1.3',backup if backup is not None else answers())
 result=asyncio.run(JudgeFallback(p,b).evaluate(STATE if state is None else state,QUESTIONS,image))
 return result,p,b

def test_actual_failed_pair_preserves_agreed_strong_primary_mode_without_mutation():
 r,p,b=run(); assert r['approved']
 assert r['answers']['editing_mode']['confidence']==.95
 assert r['answer_sources']['editing_mode']=='jev-latest'
 assert r['answer_sources']['literal']=='muse-spark-1.3'
 assert r['backup_answers']['editing_mode']['confidence']==.9
 assert p.value['editing_mode']['confidence']==.95 and b.value['editing_mode']['confidence']==.9
 assert p.calls==b.calls and len(p.calls)==1

@pytest.mark.parametrize('confidence',[.94,True,None,float('nan'),float('inf'),'0.95'])
def test_uncertain_or_invalid_primary_cannot_supply_mode(confidence):
 r,_,_=run(primary=answers(confidence=confidence,ready=.94))
 assert not r['approved']

@pytest.mark.parametrize('key,value',[('ready',0),('literal',0),('blocked',1)])
def test_backup_negative_is_never_overridden(key,value):
 a=answers();a[key]['noul']=value
 r,_,_=run(backup=a);assert not r['approved']

@pytest.mark.parametrize('mode',['results','other'])
def test_choice_disagreement_never_becomes_positive(mode):
 r,_,_=run(backup=answers(mode=mode,confidence=1));assert not r['approved']
 r,_,_=run(primary=answers(mode=mode,confidence=.95,ready=.94),backup=answers(confidence=1))
 assert not r['approved'] and r['answers']['editing_mode']['choice']==mode

@pytest.mark.parametrize('image,state',[(None,STATE),(b'',STATE),(b'frame',{'policy':'app_public_search_literal'}),(b'frame',{'policy':'app_public_search_focus','current_run_input_continuity':True})])
def test_field_reuse_requires_frozen_image_and_current_input_contract(image,state):
 r,_,_=run(image=image,state=state)
 assert r['answers']['editing_mode']['confidence']==.9


def test_no_previous_frame_field_is_reused_on_later_evaluation():
 async def scenario():
  p=RecordedJudge('jev-latest',answers(confidence=.95,ready=.94))
  b=RecordedJudge('muse-spark-1.3',answers())
  fallback=JudgeFallback(p,b)
  assert (await fallback.evaluate(STATE,QUESTIONS,b'frame1'))['approved']
  p.value=answers(confidence=.8,ready=.94)
  assert not (await fallback.evaluate(STATE,QUESTIONS,b'frame2'))['approved']
 asyncio.run(scenario())

@pytest.mark.parametrize('confidence',[.899,True,None,float('nan'),float('inf')])
def test_backup_agreement_must_itself_be_valid_and_confident(confidence):
 r,_,_=run(backup=answers(confidence=confidence));assert not r['approved']

@pytest.mark.parametrize('key,value',[('ready',0),('literal',0),('blocked',1)])
def test_primary_explicit_negative_disables_positive_field_fusion(key,value):
 p=answers(confidence=.95);p[key]['noul']=value
 r,_,_=run(primary=p);assert not r['approved']


def test_cancelled_backup_does_not_return_primary_evidence():
 class Cancelled(RecordedJudge):
  async def evaluate(self,*args):raise asyncio.CancelledError()
 with pytest.raises(asyncio.CancelledError):
  asyncio.run(JudgeFallback(RecordedJudge('jev-latest',answers(confidence=.95,ready=.94)),Cancelled('muse-spark-1.3',answers())).evaluate(STATE,QUESTIONS,b'frame'))

@pytest.mark.parametrize('mode,expected',[('query_editor',True),('results',False)])
def test_real_search_caller_retains_editor_gate_with_combined_evidence(monkeypatch,mode,expected):
 import io
 from PIL import Image
 from types import SimpleNamespace
 from unittest.mock import Mock
 from hidagent.app_search import next_search_actions
 monkeypatch.setenv('HID_SEARCH_INPUT_CONTINUITY','1')
 output=io.BytesIO();Image.new('RGB',(710,1080),'white').save(output,format='JPEG');frame=output.getvalue()
 p=RecordedJudge('jev-latest',answers(confidence=.95,ready=.94,literal=.96,blocked=.04))
 b=RecordedJudge('muse-spark-1.3',answers(mode=mode))
 query='IELTS fresh evidence'
 steps=[{'workflow':'app_search','phase':'app_ready','action':'wait','act_s':.1},
        {'workflow':'app_search','phase':'select','action':'key','combo':'cmd+a','_search_focus_verified':True,'act_s':.1},
        {'workflow':'app_search','phase':'query','action':'type','text':query,'act_s':1}]
 agent=SimpleNamespace(screen=SimpleNamespace(frame=frame),steps=steps,trace=Mock(),client=JudgeFallback(p,b),
  phone=SimpleNamespace(calibrated=True,target_info=lambda:{'device':'ipad','orientation':'portrait','width':710,'height':1080,'calibration_key':'ipad-portrait-66'}))
 actions=asyncio.run(next_search_actions(agent,{'app':'YouTube','query':query}))
 assert any(a['action']=='key' and a.get('combo')=='enter' for a in actions)==expected
 assert p.calls==b.calls
