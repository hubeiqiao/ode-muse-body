import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from test_home_icons import make,encode
from hidagent.home_icons import locate_home_icon
from hidagent.hybrid_agent import HybridAgent
from hidagent.phone import PhoneUnavailable
from hidagent.shortcut_workflow import next_shortcut_actions
REASON='Learned target context changed during pointer movement; no click sent'

def make_agent(tmp_path,monkeypatch):
 im,path=make(tmp_path);monkeypatch.setenv('APP_ICON_LAYOUT',str(path));monkeypatch.setenv('APP_ICON_KEYBOARD_RECOVERY','1')
 frame=encode(im);target={'device':'ipad','orientation':'portrait'};icon=locate_home_icon(frame,'YouTube',target)
 a=SimpleNamespace(screen=SimpleNamespace(frame=frame,live=True),steps=[],phone=SimpleNamespace(target_info=lambda:target,last_tap_timing=None),trace=Mock())
 action={'action':'tap','workflow':'keyboard','phase':'icon_launch','_home_icon_app':'YouTube','_icon_hitbox':icon['hitbox'],'x':icon['x'],'y':icon['y'],'_shot':frame}
 async def refuse(*args,**kw):
  a.phone.last_tap_timing={'target':[args[0],args[1]],'kind':'tap','outcome':'failed','pre_click_check_s':.001,'point':{'outcome':'ok'}}
  raise PhoneUnavailable(REASON)
 a.phone.tap=AsyncMock(side_effect=refuse)
 return a,action

def test_exact_preclick_refusal_becomes_honest_no_click_recovery_marker(tmp_path,monkeypatch):
 a,x=make_agent(tmp_path,monkeypatch);asyncio.run(HybridAgent._do_impl(a,x))
 assert x['action']=='wait' and x['phase']=='home_icon_guard_refused' and x['click_was_sent'] is False
 assert a.phone.tap.await_count==1

@pytest.mark.parametrize('failure',['unknown_ack','click_entered','duplicate','no_grid'])
def test_uncertain_or_repeat_failure_never_recovers(tmp_path,monkeypatch,failure):
 from PIL import Image
 a,x=make_agent(tmp_path,monkeypatch)
 if failure=='duplicate':a.steps=[{'workflow':'keyboard','phase':'home_icon_guard_refused','act_s':1}]
 original=a.phone.tap.side_effect
 async def refuse(*args,**kw):
  try:await original(*args,**kw)
  except PhoneUnavailable:
   if failure=='unknown_ack':raise PhoneUnavailable('connection lost')
   if failure=='click_entered':a.phone.last_tap_timing['click_s']=0
   if failure=='no_grid':a.screen.frame=encode(Image.new('RGB',(710,1080),'black'))
   raise
 a.phone.tap.side_effect=refuse
 with pytest.raises(PhoneUnavailable):asyncio.run(HybridAgent._do_impl(a,x))
 assert x['action']=='tap'

def test_refused_icon_is_not_retried_and_existing_spotlight_route_is_reused(tmp_path,monkeypatch):
 a,x=make_agent(tmp_path,monkeypatch);a.task='Open YouTube';a.steps=[{'workflow':'keyboard','phase':'home_icon_guard_refused','action':'wait','act_s':.5}]
 a.client=SimpleNamespace(evaluate=AsyncMock(return_value={'answers':{'page':{'choice':'other','confidence':1}},'seconds':.1}))
 actions=asyncio.run(next_shortcut_actions(a,{'app':'YouTube','text':None}))
 assert actions[0]['action']=='key' and actions[0]['combo']=='cmd+space'
 assert a.phone.tap.await_count==0
