import asyncio,io
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from PIL import Image
from hidagent import native_public_search as m,app_search
from hidagent.decision import DecisionUnavailable
from hidagent.vision_judge import JudgeFallback


def result_state(**changes):
 a={k:{'noul':v} for k,v in dict(native=1,literal=1,results=1,open_prompt=0,blocked=0).items()}
 a['target']={'confidence':0,'box':{'left':.3,'right':.5,'top':.4,'bottom':.5}}
 for k,v in changes.items():a[k]={'noul':v}
 return a


def fake(phases=()):
 im=Image.new('RGB',(710,1080),'white');b=io.BytesIO();im.save(b,format='PNG')
 a=SimpleNamespace(screen=SimpleNamespace(frame=b.getvalue(),live=True),
   phone=SimpleNamespace(calibrated=True,target_info=lambda:{'device':'ipad','orientation':'portrait'}),
   steps=[{'workflow':m.WORKFLOW,'phase':p,'act_s':.1} for p in phases],trace=Mock(),
   client=SimpleNamespace(evaluate=AsyncMock(return_value={'answers':{'ready':{'noul':1},'owner':{'noul':1},'literal':{'noul':1},'blocked':{'noul':0},'target':{'confidence':1,'box':{'left':.1,'right':.42,'top':.03,'bottom':.07}}},'seconds':.1})))
 a._native_public_address_box={'left':.1,'right':.42,'top':.03,'bottom':.07}
 a._native_public_route_binding=m.device_binding(a.phone)
 return a

GOAL={'app':'rednote','query':'IELTS'}
PRE=['home','safari_ready','new_tab','url_focus']
TYPED=PRE+['url_type','url_left','url_prefix']

def run(a):return asyncio.run(m.next_native_public_search_actions(a,GOAL))[0]


def test_complete_owned_sequence_types_and_submits_once_then_requires_actual_native_pixels(monkeypatch):
 from hidagent import shortcut_workflow
 launcher=AsyncMock(return_value=[{'action':'done','think_s':.1}])
 monkeypatch.setattr(shortcut_workflow,'next_shortcut_actions',launcher)
 a=fake();actions=[]
 for phase in ['home','safari_ready','new_tab','url_focus','url_type','url_left','url_prefix','url_submit']:
  out=run(a);assert out['phase']==phase
  actions.append(out);a.steps.append(dict(out,act_s=.1))
 assert [v['text'] for v in actions if v['action']=='type']==['xhsdiscover://search/result?keyword=IELTS']
 assert sum(v.get('combo')=='enter' for v in actions)==1
 a.client.evaluate.return_value={'answers':result_state(native=0),'seconds':.1}
 assert run(a)['action']=='wait' # URL ACK and even literal/results do not prove native entry.
 a.client.evaluate.return_value={'answers':result_state(),'seconds':.1}
 out=run(a);assert out['action']=='done' and out['_verified_public_results']
 assert out['execution_route']==m.ROUTE


@pytest.mark.parametrize('key,value',[('ready',0),('literal',.98),('blocked',.02),('literal',float('nan')),('ready',True)])
def test_empty_focus_uncertain_or_malformed_prevents_any_uri_input(key,value):
 a=fake(PRE);a.client.evaluate.return_value['answers'][key]={'noul':value}
 assert run(a)['action'] in ('wait','tap') # Owned navigation is allowed; URI typing is not.


def test_wrong_prefix_or_suffix_never_retypes_or_submits():
 for phases in [PRE+['url_type','url_left'],TYPED]:
  a=fake(phases);a.client.evaluate.return_value['answers']['literal']['noul']=0
  out=run(a);assert out['action']=='wait' and out.get('text') is None and out.get('combo') is None


def test_exact_open_prompt_is_precise_and_emitted_only_once():
 a=fake(TYPED+['url_submit'])
 ans=result_state(native=0,literal=0,results=0,open_prompt=1,ready=1)
 ans['target']['confidence']=.99
 a.client.evaluate.return_value={'answers':ans,'seconds':.1}
 out=run(a);assert out['action']=='tap' and out['_native_public_open'] and out['precise']
 assert (out['x'],out['y'])==(400,450)
 a.steps.append(dict(out,act_s=.1))
 assert run(a)['action']=='wait' # No second Open, Return or URI replay.


@pytest.mark.parametrize('key,value',[('native',0),('literal',.98),('results',0),('blocked',1),('blocked',float('inf')),('literal','1')])
def test_independent_native_results_gate_rejects_incomplete_or_invalid_proof(key,value):
 ans=result_state(**{key:value})
 assert m.accepted_native_state(ans) is None
 assert not JudgeFallback._approved({'policy':'native_public_search_result'},{},ans)


def test_generic_open_or_invalid_target_cannot_authorize_a_tap():
 for confidence,box in [(0,{}),(.89,{}),(.99,{'left':.9,'right':.1,'top':.4,'bottom':.5})]:
  ans=result_state(native=0,literal=0,results=0,open_prompt=1)
  ans['target']={'confidence':confidence,'box':box}
  assert m.accepted_native_state(ans) is None
 assert m.accepted_native_state(result_state(native=0,open_prompt=.98)) is None


@pytest.mark.parametrize('bad', ['offline','uncalibrated','iphone','landscape','long'])
def test_unsupported_or_unready_route_rejects_before_model_or_hid(bad):
 a=fake();goal=GOAL
 if bad=='offline':a.screen.live=False
 if bad=='uncalibrated':a.phone.calibrated=False
 if bad in ('iphone','landscape'):a.phone.target_info=lambda:{'device':bad if bad=='iphone' else 'ipad','orientation':bad if bad=='landscape' else 'portrait'}
 if bad=='long':goal={'app':'rednote','query':'very long encoded search query '+('x'*40)}
 with pytest.raises(DecisionUnavailable):asyncio.run(m.next_native_public_search_actions(a,goal))
 a.client.evaluate.assert_not_called()


def test_scene_changed_while_judging_cannot_dispatch_uri():
 a=fake(PRE)
 async def changed(*args):
  im=Image.new('RGB',(710,1080),'black');b=io.BytesIO();im.save(b,format='PNG');a.screen.frame=b.getvalue()
  return {'answers':{'ready':{'noul':1},'literal':{'noul':1},'blocked':{'noul':0}},'seconds':.1}
 a.client.evaluate.side_effect=changed
 assert run(a)['action']=='wait'


def test_bounded_failure_stops_without_return_or_other_gui_fallback():
 a=fake(TYPED+['url_submit']);a.client.evaluate.return_value={'answers':result_state(native=0),'seconds':.1}
 a.steps.extend([{'workflow':m.WORKFLOW,'phase':'native_query_and_results'}]*2)
 with pytest.raises(DecisionUnavailable):run(a)


def test_default_off_keeps_existing_search_path_and_explicit_optin_delegates(monkeypatch):
 from hidagent import shortcut_workflow
 a=fake();a.phone.target_info=lambda:{'device':'ipad','orientation':'portrait'}
 # Deliberately throw at the preexisting path. The URI driver must not run by default.
 original=Mock(side_effect=RuntimeError('existing path'))
 monkeypatch.setattr(app_search,'public_search_control',original)
 monkeypatch.delenv('APP_PUBLIC_SEARCH_NATIVE_LINK',raising=False)
 with pytest.raises(RuntimeError,match='existing path'):asyncio.run(app_search._next_search_actions(a,GOAL))
 monkeypatch.setenv('APP_PUBLIC_SEARCH_NATIVE_LINK','1')
 out=asyncio.run(app_search._next_search_actions(a,GOAL))[0]
 assert out['workflow']==m.WORKFLOW and out['phase']=='home'


def test_connection_change_during_judgment_cannot_send_late_uri():
 a=fake(PRE);a.phone.device=SimpleNamespace(links=1)
 a._native_public_address_box={'left':.1,'right':.42,'top':.03,'bottom':.07}
 a._native_public_route_binding=m.device_binding(a.phone)
 async def changed(*args):
  a.phone.device.links=2
  return {'answers':{'ready':{'noul':1},'literal':{'noul':1},'blocked':{'noul':0}},'seconds':.1}
 a.client.evaluate.side_effect=changed
 with pytest.raises(DecisionUnavailable,match='binding changed'):run(a)


def test_narrow_safari_address_edges_cover_every_uri_character():
 from hidagent.public_search_links import build_public_search_link
 uri=build_public_search_link(GOAL)['url']
 assert len(uri)<=2*m.EDGE_CHARS
 assert uri[:m.EDGE_CHARS]+uri[m.EDGE_CHARS:]==uri
 a=fake();goal={'app':'rednote','query':'IELTS daily answers'}
 with pytest.raises(DecisionUnavailable,match='capacity'):asyncio.run(m.next_native_public_search_actions(a,goal))
 a.client.evaluate.assert_not_called()


def test_dynamic_safari_crop_respects_observed_window_and_rejects_invalid_bounds():
 a=fake();box=a._native_public_address_box
 im=Image.open(io.BytesIO(m.address_band(a.screen.frame,box)))
 assert im.size==(227,44)
 for bad in ({'left':.1,'right':.2,'top':.03,'bottom':.07},{'left':.1,'right':.42,'top':.03,'bottom':.6}):
  with pytest.raises(DecisionUnavailable):m.address_band(a.screen.frame,bad)


def test_observed_address_activation_is_once_then_focus_is_reverified():
 a=fake(PRE);a.client.evaluate.return_value['answers']['ready']['noul']=0
 out=run(a)
 assert out['action']=='tap' and out['phase']=='address_activate' and out['_native_public_address']
 a.steps.append(dict(out,act_s=.1))
 out=run(a);assert out['phase']=='address_activate_focus' and out['combo']=='cmd+l'
 a.steps.append(dict(out,act_s=.1))
 assert run(a)['action']=='wait' # Unfocused owned field still cannot accept URI.
 a.client.evaluate.return_value['answers']['ready']['noul']=1
 out=run(a);assert out['action']=='type' and out['phase']=='url_type'


def test_address_identity_and_empty_proof_are_not_focus_or_typing_permission():
 a=fake(PRE);ans=a.client.evaluate.return_value['answers'];ans['ready']['noul']=0;ans['owner']['noul']=0
 assert run(a)['action']=='wait'
 ans['owner']['noul']=1;ans['literal']['noul']=0
 assert run(a)['action']=='wait'


def test_rejected_address_decision_time_is_preserved_in_wait_receipt():
 a=fake(PRE);a.client.evaluate.return_value['answers']['owner']['noul']=0
 a.client.evaluate.return_value['seconds']=3.402
 out=run(a);assert out['action']=='wait' and out['think_s']==3.402


def test_lower_confidence_address_needs_independent_point_and_actual_caret_proof():
 a=fake(PRE);full=a.client.evaluate.return_value
 full['answers']['ready']['noul']=0;full['answers']['target']['confidence']=.85;full['seconds']=3
 point={'answers':{'ready':{'noul':1},'blocked':{'noul':0}},'seconds':1}
 crop={'answers':{'ready':{'noul':1},'literal':{'noul':1},'blocked':{'noul':0}},'seconds':1}
 a.client.evaluate.side_effect=[full,point,crop]
 out=run(a);assert out['action']=='type' and out['think_s']==5
 assert a.client.evaluate.call_args_list[1].args[0]['policy']=='native_browser_address_point'
 assert a.client.evaluate.call_args_list[2].args[0]['policy']=='native_browser_address'


def test_uncertain_address_point_stops_before_roi_and_never_types():
 a=fake(PRE);full=a.client.evaluate.return_value;full['answers']['target']['confidence']=.85
 a.client.evaluate.side_effect=[full,{'answers':{'ready':{'noul':.98},'blocked':{'noul':0}},'seconds':1}]
 assert run(a)['action']=='wait'
 assert a.client.evaluate.await_count==2
