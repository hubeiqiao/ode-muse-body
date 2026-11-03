import asyncio,io
from types import SimpleNamespace
from PIL import Image
import pytest
from hidagent.shortcut_workflow import next_shortcut_actions

@pytest.mark.parametrize('confidence,model,fallback,expected_overlay,changed,text',[(.99,'jev-1.14.0',False,False,False,None),(.98,'jev-1.14.0',False,True,False,None),(.99,'claude-haiku-5-5',True,True,False,None),(.99,'claude-haiku-5-5',False,True,False,None),(.99,'jev-1.14.0',True,True,False,None),(.99,'jev-1.14.0',False,True,True,None),(.99,'jev-1.14.0',False,True,False,'ESP32QA never sent')])
def test_only_strong_direct_jev_app_decision_elides_duplicate_overlay(monkeypatch,confidence,model,fallback,expected_overlay,changed,text):
 image=io.BytesIO();Image.new('RGB',(710,1080)).save(image,format='JPEG');frame=image.getvalue();calls=[]
 class Client:
  async def evaluate(self,state,questions,image):
   purpose=state['purpose'];calls.append(purpose)
   if state.get('policy')=='public_app_identity':return {'answers':{'visible_app':{'choice':'YouTube','confidence':.99}},'seconds':.1,'approved':True}
   if purpose=='classify foreground':
    if changed:a.screen.frame=frame+b'changed'
    return {'answers':{'page':{'choice':'app','confidence':confidence}},'seconds':.1,'model':model,'fallback_used':fallback,'approved':True}
   return {'answers':{'ready':{'type':'noul','noul':0 if 'compact' in purpose else 1}},'seconds':.1,'approved':True}
 a=SimpleNamespace(screen=SimpleNamespace(frame=frame),client=Client(),steps=[],trace=SimpleNamespace(write=lambda *args,**kwargs:None),phone=SimpleNamespace(target_info=lambda:{'device':'ipad'}))
 monkeypatch.setattr('hidagent.shortcut_workflow.scene_changed',lambda *args:False)
 actions=asyncio.run(next_shortcut_actions(a,{'app':'YouTube','text':text}))
 assert ('verify compact iPad Spotlight overlay' in calls)==expected_overlay
 assert 'verify foremost iPad app window' in calls
 assert actions[0]['action']==('done' if text is None else 'key')

def test_strong_jev_reuse_still_rejects_failed_independent_foreground(monkeypatch):
 image=io.BytesIO();Image.new('RGB',(710,1080)).save(image,format='JPEG');calls=[]
 class Client:
  async def evaluate(self,state,questions,frame):
   purpose=state['purpose'];calls.append(purpose)
   if state.get('policy')=='public_app_identity':return {'answers':{'visible_app':{'choice':'YouTube','confidence':.99}},'seconds':.1,'approved':True}
   if purpose=='classify foreground':return {'answers':{'page':{'choice':'app','confidence':.99}},'seconds':.1,'model':'jev-1.14.0','fallback_used':False,'approved':True}
   return {'answers':{'ready':{'noul':0}},'seconds':.1,'approved':False}
 a=SimpleNamespace(screen=SimpleNamespace(frame=image.getvalue()),client=Client(),steps=[],trace=SimpleNamespace(write=lambda *args,**kwargs:None),phone=SimpleNamespace(target_info=lambda:{'device':'ipad'}))
 monkeypatch.setattr('hidagent.shortcut_workflow.scene_changed',lambda *args:False)
 actions=asyncio.run(next_shortcut_actions(a,{'app':'YouTube','text':None}))
 assert 'verify compact iPad Spotlight overlay' not in calls
 assert 'verify foremost iPad app window' in calls
 assert actions[0]['action']!='done'
 assert all(x['action']!='type' for x in actions)
