import asyncio,io
from types import SimpleNamespace
from unittest.mock import Mock
from PIL import Image
import pytest
from hidagent.app_search import next_search_actions
from hidagent.vision_judge import JudgeFallback

def test_result_page_cannot_submit_even_when_other_visual_answers_are_true(monkeypatch):
 monkeypatch.setenv('HID_SEARCH_INPUT_CONTINUITY','1')
 image=io.BytesIO();Image.new('RGB',(710,1080),'white').save(image,format='JPEG');frame=image.getvalue()
 class Judge:
  async def evaluate(self,state,questions,image):return {'seconds':.1,'approved':False,'answers':{'ready':{'noul':1},'literal':{'noul':1},'blocked':{'noul':0},'editing_mode':{'type':'choice','choice':'results','confidence':1}}}
 steps=[{'workflow':'app_search','phase':'app_ready','action':'wait','act_s':.1},{'workflow':'app_search','phase':'select','action':'key','combo':'cmd+a','_search_focus_verified':True,'act_s':.1},{'workflow':'app_search','phase':'query','action':'type','text':'IELTS useful vocabulary','act_s':1}]
 a=SimpleNamespace(screen=SimpleNamespace(frame=frame),steps=steps,trace=Mock(),client=Judge(),phone=SimpleNamespace(calibrated=True,target_info=lambda:{'device':'ipad','orientation':'portrait','width':710,'height':1080,'calibration_key':'ipad-portrait-66'}))
 actions=asyncio.run(next_search_actions(a,{'app':'YouTube','query':'IELTS useful vocabulary'}))
 assert all(not (v['action']=='key' and v.get('combo')=='enter') for v in actions)

@pytest.mark.parametrize('mode,confidence',[('results',1),('other',1),('query_editor',.94),('query_editor',True),('query_editor',None),('query_editor',float('nan')),('query_editor',float('inf'))])
def test_literal_approval_requires_requested_editor_mode(mode,confidence):
 q={k:{'type':'noul'} for k in ('ready','literal','blocked')};q['editing_mode']={'type':'choice'}
 a={'ready':{'noul':1},'literal':{'noul':1},'blocked':{'noul':0},'editing_mode':{'choice':mode,'confidence':confidence}}
 assert not JudgeFallback._approved({'policy':'app_public_search_literal'},q,a)


def test_certain_editor_mode_keeps_other_literal_checks_required():
 q={k:{'type':'noul'} for k in ('ready','literal','blocked')};q['editing_mode']={'type':'choice'}
 a={'ready':{'noul':1},'literal':{'noul':1},'blocked':{'noul':0},'editing_mode':{'choice':'query_editor','confidence':.95}}
 assert JudgeFallback._approved({'policy':'app_public_search_literal'},q,a)
 a['literal']={'noul':.98}
 assert not JudgeFallback._approved({'policy':'app_public_search_literal'},q,a)
