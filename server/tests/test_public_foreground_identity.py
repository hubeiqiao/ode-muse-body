import asyncio,io
from types import SimpleNamespace
from unittest.mock import Mock
from PIL import Image
import pytest
from hidagent.shortcut_workflow import next_shortcut_actions

@pytest.mark.parametrize('actual,confidence',[('TikTok',1),('other',1),('rednote',.94),('rednote',True)])
def test_target_biased_app_answer_cannot_finish_wrong_or_uncertain_app(monkeypatch,actual,confidence):
 monkeypatch.setattr('hidagent.shortcut_workflow.scene_changed',lambda *args:False)
 image=io.BytesIO();Image.new('RGB',(710,1080)).save(image,format='JPEG');calls=[]
 class Client:
  async def evaluate(self,state,questions,frame):
   calls.append(state)
   if state.get('policy')=='public_app_identity':
    assert 'target_app' not in state and 'app_card' not in state and 'query' not in state
    return {'answers':{'visible_app':{'choice':actual,'confidence':confidence}},'seconds':.1,'approved':True}
   if 'page' in questions:return {'answers':{'page':{'choice':'app','confidence':.99}},'seconds':.1,'approved':True,'model':'jev-1.14.0','fallback_used':False}
   return {'answers':{'ready':{'noul':0 if 'compact' in state['purpose'] else 1}},'seconds':.1,'approved':True}
 a=SimpleNamespace(screen=SimpleNamespace(frame=image.getvalue()),steps=[],trace=Mock(),client=Client(),phone=SimpleNamespace(target_info=lambda:{'device':'ipad'}))
 out=asyncio.run(next_shortcut_actions(a,{'app':'rednote','text':None}))
 assert any(x.get('policy')=='public_app_identity' for x in calls)
 assert all(x['action'] not in ('done','type','tap') for x in out)
 assert all(x.get('combo')!='enter' for x in out)
 if actual=='other' or confidence!=1:assert all(x['action']=='wait' for x in out)

def test_identity_policy_does_not_accept_invalid_confidence():
 from hidagent.vision_judge import JudgeFallback
 q={'visible_app':{'type':'choice','criteria':{'rednote':'rednote','other':'unknown'}}}
 for confidence in [.94,True,float('nan'),float('inf'),None]:
  assert not JudgeFallback._approved({'policy':'public_app_identity'},q,{'visible_app':{'choice':'rednote','confidence':confidence}})
 assert JudgeFallback._approved({'policy':'public_app_identity'},q,{'visible_app':{'choice':'rednote','confidence':.95}})
