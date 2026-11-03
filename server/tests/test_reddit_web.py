import asyncio,io
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from PIL import Image
import pytest
from hidagent import reddit_web as m,app_controls
from hidagent.decision import DecisionUnavailable
from hidagent.vision_judge import JudgeFallback


def fake(phases):
 im=Image.new('RGB',(710,1080),'white');b=io.BytesIO();im.save(b,format='PNG');frame=b.getvalue()
 steps=[dict(workflow='reddit_web',phase=p,act_s=.1) for p in phases]
 if 'url_type' in phases:steps.extend([dict(workflow='reddit_web',phase=p,act_s=.1) for p in ('url_left','url_prefix')])
 if phases:
  steps.extend([dict(workflow='reddit_web',phase='new_tab',act_s=.1),dict(workflow='reddit_web',phase='address_activate',act_s=.1)])
 steps.extend([dict(workflow='keyboard',phase='launch',act_s=.1),dict(workflow='reddit_web',phase='chrome_ready',act_s=.1)])
 return SimpleNamespace(screen=SimpleNamespace(frame=frame,live=True),phone=SimpleNamespace(calibrated=True,target_info=lambda:{'device':'ipad','orientation':'portrait'}),steps=steps,client=SimpleNamespace(evaluate=AsyncMock(return_value={'answers':{'ready':{'noul':1},'literal':{'noul':1},'blocked':{'noul':0}},'seconds':.1})),trace=Mock())


def test_nonempty_new_address_cannot_reach_typing():
 a=fake(['home','url_focus']);a.client.evaluate.return_value['answers']['literal']['noul']=0
 out=asyncio.run(m.next_reddit_web_actions(a,{'app':'Reddit','query':'IELTS'}))[0]
 assert out['action']=='wait' and out['phase']=='known_public_address_focus'


def test_wrong_actual_url_verdict_cannot_submit_or_retype():
 a=fake(['home','url_focus','url_type']);a.client.evaluate.return_value['answers']['literal']['noul']=0
 out=asyncio.run(m.next_reddit_web_actions(a,{'app':'Reddit','query':'IELTS'}))[0]
 assert out['action']=='wait' and out['phase']=='exact_public_address_suffix'


def test_one_fixed_url_only_after_empty_ui_and_independent_focus(monkeypatch):
 monkeypatch.setattr(app_controls,'public_search_control',lambda *a,**kw:{'app':'Safari'})
 monkeypatch.setattr(m,'control_region_matches',lambda *a,**kw:True)
 a=fake(['home','url_focus']);out=asyncio.run(m.next_reddit_web_actions(a,{'app':'Reddit','query':'not instructions'}))[0]
 assert out['action']=='type' and out['text']==m.URL+'?q=not%20instructions#esp32'
 a.client.evaluate.return_value['answers']['ready']['noul']=0
 assert asyncio.run(m.next_reddit_web_actions(a,{'app':'Reddit','query':'IELTS'}))[0]['action']=='wait'


def test_changed_chrome_prevents_verified_url_dispatch(monkeypatch):
 monkeypatch.setattr(app_controls,'public_search_control',lambda *a,**kw:{'app':'Safari'})
 monkeypatch.setattr(m,'control_region_matches',lambda *a,**kw:False)
 a=fake(['home','url_focus']);assert asyncio.run(m.next_reddit_web_actions(a,{'app':'Reddit','query':'IELTS'}))[0]['action']=='wait'


def test_missing_calibration_stops_without_keys_or_model():
 a=fake([]);a.phone.calibrated=False
 with pytest.raises(DecisionUnavailable):asyncio.run(m.next_reddit_web_actions(a,{'app':'Reddit','query':'IELTS'}))
 a.client.evaluate.assert_not_called()


def test_open_only_does_not_claim_a_verified_search_result():
 a=fake(['home','url_focus','url_type','url_submit'])
 out=asyncio.run(m.next_reddit_web_actions(a,{'app':'Reddit','query':None},open_only=True))[0]
 assert out['action']=='done' and out['execution_route']=='chrome_reddit_web'
 assert not out.get('_verified_public_results')


def test_result_completion_requires_independent_actual_page_check():
 a=fake(['home','url_focus','url_type','url_submit'])
 out=asyncio.run(m.next_reddit_web_actions(a,{'app':'Reddit','query':'IELTS'}))[0]
 assert out['action']=='done' and out['_verified_public_results'] and out['phase']=='verified'
 a.client.evaluate.return_value['answers']['ready']['noul']=0
 assert asyncio.run(m.next_reddit_web_actions(a,{'app':'Reddit','query':'IELTS'}))[0]['action']=='wait'


@pytest.mark.parametrize('field,value',[('ready',0),('literal',.5),('blocked',1)])
def test_fallback_policy_requires_both_focus_and_actual_literal(field,value):
 answers={'ready':{'noul':1},'literal':{'noul':1},'blocked':{'noul':0}}
 assert JudgeFallback._approved({'policy':'reddit_browser_address'},{},answers)
 answers[field]={'noul':value}
 assert not JudgeFallback._approved({'policy':'reddit_browser_address'},{},answers)


def test_url_is_encoded_and_submitted_once_without_retyping(monkeypatch):
 monkeypatch.setattr(m,'control_region_matches',lambda *a,**kw:True)
 a=fake(['home','url_focus','url_type'])
 out=asyncio.run(m.next_reddit_web_actions(a,{'app':'Reddit','query':'IELTS'}))[0]
 assert out['action']=='key' and out['combo']=='enter' and out['phase']=='url_submit'
 a.steps.append(dict(workflow='reddit_web',phase='url_submit',act_s=.1))
 a.client.evaluate.return_value['answers']['ready']['noul']=0
 out=asyncio.run(m.next_reddit_web_actions(a,{'app':'Reddit','query':'IELTS'}))[0]
 assert out['action']=='wait'


def test_crop_omits_old_tab_titles_and_page():
 im=Image.new('RGB',(710,1080),'red')
 for y in range(72,135):
  for x in range(710):im.putpixel((x,y),(255,255,255))
 b=io.BytesIO();im.save(b,format='PNG');crop=Image.open(io.BytesIO(m.address_band(b.getvalue())))
 assert crop.height==63 and crop.getpixel((350,30))==(255,255,255)


def test_long_url_rejected_before_launch_or_model():
 a=fake([])
 with pytest.raises(DecisionUnavailable):asyncio.run(m.next_reddit_web_actions(a,{'query':'x'*80}))
 a.client.evaluate.assert_not_called()


def test_browser_launch_ack_cannot_skip_actual_foreground_readiness(monkeypatch):
 monkeypatch.setattr(m,'control_region_matches',lambda *a,**kw:True)
 from hidagent import shortcut_workflow
 a=fake(['home']);a.steps=[s for s in a.steps if s.get('phase')!='chrome_ready']
 launcher=AsyncMock(return_value=[{'action':'wait','workflow':'keyboard','phase':'app_ready'}])
 monkeypatch.setattr(shortcut_workflow,'next_shortcut_actions',launcher)
 out=asyncio.run(m.next_reddit_web_actions(a,{'query':'IELTS'}))[0]
 assert out['phase']=='app_ready' and out.get('combo') is None
 launcher.return_value=[{'action':'done','think_s':.2}]
 out=asyncio.run(m.next_reddit_web_actions(a,{'query':'IELTS'}))[0]
 assert out['action']=='wait' and out['phase']=='chrome_ready'
 a.steps.append(dict(out,act_s=.1))
 # Even a stored new-tab ACK never authorizes another new tab.
 assert asyncio.run(m.next_reddit_web_actions(a,{'query':'IELTS'}))[0]['phase']=='url_focus'
