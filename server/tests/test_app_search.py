import asyncio,json,io
from PIL import Image
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from hidagent.app_search import search_goal,match_search_intent,next_search_actions,PREFIX
from hidagent.app_tools import match_goal,TOOLS
from hidagent.shortcut_workflow import shortcut_goal
from hidagent.decision import DecisionUnavailable

@pytest.mark.parametrize('name',['TikTok','Douyin','Bilibili','LinkedIn','Reddit','Instagram','YouTube','Threads','Google Maps','Discord','Muse','App Store','Chrome'])
def test_first_screen_open_aliases(name):assert shortcut_goal('Open '+name)=={'app':name,'text':None}
@pytest.mark.parametrize('task',['Open TikTok and post hello','Open X and follow Joe','Open Reddit and delete account'])
def test_open_alias_never_eats_trailing_action(task):assert shortcut_goal(task) is None
@pytest.mark.parametrize('app,key',[('YouTube','youtube'),('小红书','xiaohongshu'),('X','x'),('Google Maps','google_maps')])
def test_search_voice_routing(app,key):assert match_goal('Search '+app+' for IELTS speaking')==(key+'.search',{'query':'IELTS speaking'})
@pytest.mark.parametrize('data',[{'app':'Gmail','query':'secret'},{'app':'X','query':'hello\nsubmit'},{'app':'X','query':'中文'},{'app':'X','query':'a','publish':True}])
def test_bad_goal_fails_closed(data):
 with pytest.raises(DecisionUnavailable):search_goal(PREFIX+json.dumps(data))
def test_literal_input_is_not_search():assert match_goal('type "Search X for IELTS"')==('keyboard.type_focused',{'text':'Search X for IELTS'})
def test_no_private_search_or_publish_tools():
 assert 'discord.search' not in TOOLS and 'gmail.search' not in TOOLS and 'x.publish' not in TOOLS

def fake(phases,answers):
 frame=io.BytesIO();Image.new('RGB',(100,100),'white').save(frame,format='PNG')
 return SimpleNamespace(screen=SimpleNamespace(frame=frame.getvalue()),steps=[{'workflow':'app_search','phase':p,**({'action':'tap','act_s':.1} if p=='focus' else {})} for p in phases],client=SimpleNamespace(evaluate=AsyncMock(return_value={'answers':{**answers,'blocked':{'noul':0}},'seconds':.1})),trace=Mock())
def assert_query_batch(actions, text, app=None):
 assert [(a['action'],a.get('combo')) for a in actions[:-1]]==[('key','cmd+right'),('key','cmd+backspace')]+[('key','backspace')]*4
 assert actions[-1]['action']=='type' and actions[-1]['text']==text and actions[-1]['phase']=='query'
 assert actions[-1]['_search_clear_sequence']=={'select':None,'caret_to_end':'cmd+right','delete_to_line_start':'cmd+backspace','backspaces':4}
 assert all(a['_search_focus_verified'] for a in actions)
 return actions[-1]

@pytest.mark.parametrize('phases', [['app_ready','focus'], ['app_ready','focus','select']])
def test_finite_search_input_stage(phases):
 a=fake(phases,{'ready':{'noul':1}})
 assert_query_batch(asyncio.run(next_search_actions(a,{'app':'YouTube','query':'IELTS'})),'IELTS')

@pytest.mark.parametrize('phases,answers,action,nextphase',[
 (['app_ready','query'],{'ready':{'noul':1}},'key','submit'),
 (['app_ready','query','submit'],{'results':{'noul':1}},'done','verified')])
def test_finite_search_stage(phases,answers,action,nextphase):
 a=fake(phases,answers);out=asyncio.run(next_search_actions(a,{'app':'YouTube','query':'IELTS'}))[0]
 assert out['action']==action and out['phase']==nextphase
 if nextphase=='submit':assert out['combo']=='enter'

def test_uncertain_query_never_retypes_or_submits():
 a=fake(['app_ready','query','exact_query_visible','exact_query_visible'],{'ready':{'noul':.8}})
 with pytest.raises(DecisionUnavailable):asyncio.run(next_search_actions(a,{'app':'X','query':'IELTS'}))

def test_no_repeated_search_submission_after_results_uncertain():
 a=fake(['app_ready','query','submit','results_visible','results_visible'],{'results':{'noul':0}})
 with pytest.raises(DecisionUnavailable):asyncio.run(next_search_actions(a,{'app':'X','query':'IELTS'}))


def test_personal_threads_excluded_from_shared_tools():
 assert 'threads.open' not in TOOLS and 'threads.search' not in TOOLS
 assert match_search_intent('Search Threads for IELTS') is None

def test_completed_hid_acks_avoid_false_offline_after_active_work():
 from hidagent.device import Device
 d=Device();d._writer=SimpleNamespace(is_closing=lambda:False);d.links=2;d._connected_at=80
 d.timings.extend([{'link':2,'finished_at':99,'command':'type','wire_s':5,'outcome':'ok'},
                  {'link':2,'finished_at':99.5,'command':'key','wire_s':.1,'outcome':'ok'}])
 assert d.readiness(now=100)['stable']
 d.timings.append({'link':2,'finished_at':99.9,'command':'key','wire_s':.1,'outcome':'failed'})
 assert not d.readiness(now=100)['stable']


def test_search_policy_requests_backup_instead_of_silently_accepting_uncertainty():
 from hidagent.vision_judge import JudgeFallback
 q={'ready':{'type':'noul'},'blocked':{'type':'noul'},'target':{'type':'box'}}
 state={'policy':'app_public_search'}
 a={'ready':{'noul':.97},'blocked':{'noul':0},'target':{'confidence':.95}}
 assert not JudgeFallback._approved(state,q,a)
 a['ready']['noul']=1
 assert JudgeFallback._approved(state,q,a)
 a['blocked']['noul']=1
 assert not JudgeFallback._approved(state,q,a)


def test_verified_focus_is_carried_from_actual_judge_not_assumed():
 a=fake(['app_ready','focus'],{'ready':{'noul':1},'focused':{'noul':1}})
 actions=asyncio.run(next_search_actions(a,{'app':'rednote','query':'IELTS'}))
 assert_query_batch(actions,'IELTS')
 a=fake(['app_ready','focus','select'],{'ready':{'noul':1}})
 a.steps[-1].update(_search_focus_verified=True,act_s=.2)
 asyncio.run(next_search_actions(a,{'app':'rednote','query':'IELTS'}))
 state=a.client.evaluate.call_args.args[0]
 assert state['focus_visually_verified_before_selection'] is True
 a=fake(['app_ready','select'],{'ready':{'noul':0}})
 asyncio.run(next_search_actions(a,{'app':'rednote','query':'IELTS'}))
 assert a.client.evaluate.call_args.args[0]['focus_visually_verified_before_selection'] is False


def test_verified_focus_clearing_and_typing_needs_only_one_model_check():
 a=fake(['app_ready','focus'],{'ready':{'noul':1},'focused':{'noul':1}})
 actions=asyncio.run(next_search_actions(a,{'app':'rednote','query':'IELTS'}))
 assert_query_batch(actions,'IELTS')
 assert a.client.evaluate.await_count==1


@pytest.mark.parametrize('problem',['old_run','stale','scene'])
def test_legacy_focus_proof_cannot_bypass_fresh_focus_check(problem):
 a=fake(['app_ready','select'],{'ready':{'noul':0}})
 a.steps[-1].update(act_s=.2,_search_focus_verified=True,_search_focus_ticket='old-run')
 a._app_search_focus_proof={'ticket':'old-run','app':'rednote','query':'IELTS','frame':a.screen.frame,'at':0}
 if problem=='old_run':a._app_search_focus_proof['ticket']='another-run'
 if problem=='scene':
  b=io.BytesIO();Image.new('RGB',(100,100),'black').save(b,format='PNG');a.screen.frame=b.getvalue()
 out=asyncio.run(next_search_actions(a,{'app':'rednote','query':'IELTS'}))
 assert len(out)==1 and out[0]['action']=='wait'
 a.client.evaluate.assert_awaited_once()


def test_learned_reopened_explore_goes_to_focus_not_guessed_feed(monkeypatch):
 a=fake(['app_ready'],{});a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad','orientation':'portrait'})
 def locate(frame,app,target,**kw):
  return {'x':470,'y':50,'control':'public_search_form_entry'} if kw.get('control')=='public_search_form_entry' else None
 monkeypatch.setattr('hidagent.app_search.public_search_control',locate)
 out=asyncio.run(next_search_actions(a,{'app':'Instagram','query':'IELTS'}))[0]
 assert out['action']=='tap' and out['phase']=='search_form' and out['_learned_search_control']=='public_search_form_entry'
 a.client.evaluate.assert_not_called()


def test_instagram_form_navigation_cannot_skip_actual_field_tap():
 a=fake(['app_ready','search_form'],{'ready':{'noul':1},'focused':{'noul':1},'target':{'confidence':.95,'box':{'left':.2,'right':.8,'top':.03,'bottom':.07}}})
 out=asyncio.run(next_search_actions(a,{'app':'Instagram','query':'IELTS'}))[0]
 assert out['action']=='tap' and out['phase']=='focus'
 assert '_search_focus_ticket' not in out


@pytest.mark.parametrize('verified,expected',[(1,'tap'),(0,None)])
def test_uncertain_box_is_only_a_proposal_until_independent_point_proof(verified,expected):
 a=fake(['app_ready','search_control'],{})
 first={'answers':{'ready':{'noul':1},'focused':{'noul':0},'target':{'confidence':.6,'box':{'left':.2,'right':.8,'top':.03,'bottom':.07}},'blocked':{'noul':0}},'seconds':.1}
 second={'answers':{'ready':{'noul':verified},'blocked':{'noul':0}},'seconds':.1}
 a.client.evaluate=AsyncMock(side_effect=[first,second])
 if expected:assert asyncio.run(next_search_actions(a,{'app':'Instagram','query':'IELTS'}))[0]['action']==expected
 else:
  with pytest.raises(DecisionUnavailable):asyncio.run(next_search_actions(a,{'app':'Instagram','query':'IELTS'}))
 assert a.client.evaluate.await_count==2
 assert 'independently ground' in a.client.evaluate.call_args.args[0]['purpose']


@pytest.mark.parametrize('empty',[1,0,.5])
def test_instagram_uses_fixed_clear_only_after_actual_editor_focus(empty):
 a=fake(['app_ready','focus'],{'ready':{'noul':1},'empty':{'noul':empty}})
 actions=asyncio.run(next_search_actions(a,{'app':'Instagram','query':'IELTS'}))
 assert_query_batch(actions,'IELTS','Instagram')
 assert 'empty' not in a.client.evaluate.call_args.args[1]
 assert a.client.evaluate.call_args.args[0]['policy']=='app_public_search_focus'


@pytest.mark.parametrize('safe',[True,False])
@pytest.mark.parametrize('app,phase',[('Bilibili','dismiss_bilibili_youth_intro'),('Douyin','dismiss_douyin_youth_intro')])
def test_late_popup_recovery_accepts_only_explicit_safe_markers(monkeypatch,safe,app,phase):
 a=fake(['app_ready'],{})
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 a.steps.append({'workflow':'keyboard','phase':'icon_launch'})
 a.client.evaluate.return_value={'answers':{'blocked':{'noul':1}},'seconds':.1}
 action={'action':'tap','workflow':'keyboard','phase':phase,'_shot':a.screen.frame}
 if safe:action['_intro_notice_dismissal']=True
 monkeypatch.setattr('hidagent.shortcut_workflow.next_shortcut_actions',AsyncMock(return_value=[action]))
 if safe:assert asyncio.run(next_search_actions(a,{'app':app,'query':'IELTS'}))[0]['_intro_notice_dismissal']
 else:
  with pytest.raises(DecisionUnavailable):asyncio.run(next_search_actions(a,{'app':app,'query':'IELTS'}))


@pytest.mark.parametrize('empty',[1,0,.5])
def test_focused_search_clear_is_independent_of_empty_answer(empty):
 a=fake(['app_ready','focus'],{'ready':{'noul':1},'empty':{'noul':empty}})
 actions=asyncio.run(next_search_actions(a,{'app':'Bilibili','query':'IELTS'}))
 assert_query_batch(actions,'IELTS')
 assert 'empty' not in a.client.evaluate.call_args.args[1]


def test_verified_readonly_results_ignore_video_motion_but_input_does_not(monkeypatch):
 monkeypatch.setattr('hidagent.app_search.scene_changed',lambda *args:True)
 a=fake(['app_ready','query','submit'],{'results':{'noul':1}})
 out=asyncio.run(next_search_actions(a,{'app':'Bilibili','query':'IELTS'}))[0]
 assert out['action']=='done' and out['_verified_public_results']
 a=fake(['app_ready','focus'],{'ready':{'noul':1},'empty':{'noul':1}})
 assert asyncio.run(next_search_actions(a,{'app':'Bilibili','query':'IELTS'}))[0]['action']=='wait'


@pytest.mark.parametrize('actions',[[],[{'action':'type','workflow':'keyboard','phase':'dismiss_bilibili_youth_intro','_intro_notice_dismissal':True,'text':'not allowed'}]])
def test_popup_marker_never_authorizes_empty_or_typing_recovery(monkeypatch,actions):
 a=fake(['app_ready'],{});a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 a.steps.append({'workflow':'keyboard','phase':'icon_launch'})
 a.client.evaluate.return_value={'answers':{'blocked':{'noul':1}},'seconds':.1}
 monkeypatch.setattr('hidagent.shortcut_workflow.next_shortcut_actions',AsyncMock(return_value=actions))
 with pytest.raises(DecisionUnavailable):asyncio.run(next_search_actions(a,{'app':'Bilibili','query':'IELTS'}))

@pytest.mark.parametrize('known,empty_ack,literal,scoped',[(True,True,'IELTS',True),(False,True,'IELTS',False),(True,False,'IELTS',False),(True,True,'IELT5',False)])
def test_caret_independent_literal_check_requires_current_form_and_real_empty_input_ack(monkeypatch,known,empty_ack,literal,scoped):
 from hidagent import app_search
 a=fake(['app_ready','query'],{'ready':{'noul':1}})
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad','orientation':'portrait'})
 a.steps[-1].update(act_s=.5,text=literal,_search_was_empty_verified=empty_ack)
 monkeypatch.setattr(app_search,'public_search_control',lambda *args,**kw: {'x':500,'y':40} if known else None)
 out=asyncio.run(next_search_actions(a,{'app':'Bilibili','query':'IELTS'}))[0]
 question=a.client.evaluate.call_args.args[1]['ready']['instructions']
 assert ('blinking caret' in question)==scoped
 assert out['phase']=='submit'
 assert a.client.evaluate.call_args.args[0]['policy']=='app_public_search'


def test_learned_query_field_only_taps_then_requires_fresh_focus(monkeypatch):
 a=fake(['app_ready','search_control'],{});a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad','orientation':'portrait'})
 def locate(frame,app,target,**kw):
  return {'x':500,'y':48,'control':'public_search_field','template':'field.json'} if kw.get('control')=='public_search_field' else None
 monkeypatch.setattr('hidagent.app_search.public_search_control',locate)
 out=asyncio.run(next_search_actions(a,{'app':'YouTube','query':'new query'}))[0]
 assert out['action']=='tap' and out['phase']=='focus' and out['_learned_search_control']=='public_search_field'
 assert 'text' not in out and '_search_focus_ticket' not in out
 a.client.evaluate.assert_not_called()
 a.steps.append({**out,'act_s':.1});a.client.evaluate.return_value={'answers':{'ready':{'noul':0},'empty':{'noul':0},'blocked':{'noul':0}},'seconds':.1}
 next_action=asyncio.run(next_search_actions(a,{'app':'YouTube','query':'new query'}))[0]
 assert next_action['action']=='wait' and next_action['phase']=='search_focus'
 a.client.evaluate.assert_awaited_once()


@pytest.mark.parametrize('focused,next_action',[ (0,'wait'),(1,'key') ])
def test_youtube_learned_editor_needs_fresh_visible_focus(focused,next_action):
 a=fake(['app_ready'],{});a.steps.append({'workflow':'app_search','phase':'focus','action':'tap','_learned_public_search':'YouTube','_learned_search_control':'public_search_field','act_s':.1})
 a.client.evaluate.return_value={'answers':{'ready':{'noul':1},'focused':{'noul':focused},'empty':{'noul':0},'blocked':{'noul':0}},'seconds':.1}
 out=asyncio.run(next_search_actions(a,{'app':'YouTube','query':'IELTS linking words'}))[0]
 assert out['action']==next_action and out.get('text') is None
 if focused:assert out['phase']=='clear_query' and out['_search_focus_verified']
 else:assert out['phase']=='search_focus'


def test_focus_policy_does_not_ignore_separate_focus_question():
 from hidagent.vision_judge import JudgeFallback
 questions={k:{'type':'noul'} for k in ['ready','focused','empty','blocked']}
 answers={k:{'noul':v} for k,v in {'ready':1,'focused':0,'empty':0,'blocked':0}.items()}
 assert not JudgeFallback._approved({'policy':'app_public_search_focus'},questions,answers)
 answers['focused']['noul']=1
 assert JudgeFallback._approved({'policy':'app_public_search_focus'},questions,answers)


def test_public_search_geometry_failure_is_terminal_before_another_model_call():
 a=fake(['app_ready'],{});a.phone=SimpleNamespace(calibrated=False,target_info=lambda:{'device':'ipad','orientation':'landscape','width':1920,'height':1080})
 with pytest.raises(DecisionUnavailable,match='uncalibrated geometry'):
  asyncio.run(next_search_actions(a,{'app':'Reddit','query':'IELTS speaking'}))
 a.client.evaluate.assert_not_called()


def test_app_store_public_catalog_search_does_not_register_install_or_purchase():
 assert match_goal("Search App Store for IELTS speaking")==("app_store.search",{"query":"IELTS speaking"})
 assert search_goal(PREFIX+json.dumps({"app":"App Store","query":"IELTS speaking"}))=={"app":"App Store","query":"IELTS speaking"}
 assert "app_store.install" not in TOOLS and "app_store.purchase" not in TOOLS


@pytest.mark.parametrize('selection_proven',[False,True])
def test_app_store_literal_check_uses_local_form_only_with_same_run_focus(monkeypatch,selection_proven):
 from hidagent import app_controls
 monkeypatch.setattr('hidagent.app_search.public_search_control',lambda *a,**kw:{'app':'App Store'})
 a=fake(['app_ready'],{'ready':{'noul':1}});a.phone=SimpleNamespace(calibrated=True,target_info=lambda:{'device':'ipad','orientation':'portrait'})
 a.steps.extend([{'workflow':'app_search','phase':'select','act_s':.1,'_search_focus_verified':selection_proven},{'workflow':'app_search','phase':'query','act_s':.1,'text':'English grammar'}])
 out=asyncio.run(next_search_actions(a,{'app':'App Store','query':'English grammar'}))[0]
 question=a.client.evaluate.call_args.args[1]['ready']['instructions']
 assert ('independently measured' in question)==selection_proven
 assert out['action']=='key' and out['combo']=='enter'


@pytest.mark.parametrize('focused',[0,1])
def test_app_store_cold_editor_always_places_caret(monkeypatch,focused):
 monkeypatch.setattr('hidagent.app_search.public_search_control',lambda *a,**kw:{'app':'App Store','control':'public_search_field','x':500,'y':52})
 a=fake(['app_ready'],{'ready':{'noul':1},'focused':{'noul':focused},'empty':{'noul':0}})
 a.phone=SimpleNamespace(calibrated=True,target_info=lambda:{'device':'ipad','orientation':'portrait'})
 out=asyncio.run(next_search_actions(a,{'app':'App Store','query':'English idioms'}))[0]
 assert out['action']=='tap' and out['phase']=='focus'


@pytest.mark.parametrize('focused',[0,1])
def test_douyin_old_selection_ack_needs_current_editor_focus(focused):
 a=fake(['app_ready','focus','select'],{'ready':{'noul':focused}})
 a.steps[-1].update(act_s=.1,_search_focus_verified=True,_search_focus_ticket='ack-only')
 a._app_search_focus_proof={'ticket':'ack-only','app':'Douyin','query':'IELTS','frame':a.screen.frame,'at':0}
 actions=asyncio.run(next_search_actions(a,{'app':'Douyin','query':'IELTS'}))
 if focused:assert_query_batch(actions,'IELTS','Douyin')
 else:assert len(actions)==1 and actions[0]['action']=='wait'
 a.client.evaluate.assert_awaited_once()


def test_current_run_focus_and_literal_are_independent_not_an_ack_assumption(monkeypatch):
 monkeypatch.setenv('HID_SEARCH_INPUT_CONTINUITY','1')
 a=fake(['app_ready'],{'ready':{'noul':1},'literal':{'noul':1},'editing_mode':{'choice':'query_editor','confidence':.99}})
 a.steps.extend([{'workflow':'app_search','phase':'select','action':'key','_search_focus_verified':True,'act_s':.1}, {'workflow':'app_search','phase':'query','action':'type','text':'IELTS','act_s':.2}])
 out=asyncio.run(next_search_actions(a,{'app':'YouTube','query':'IELTS'}))[0]
 assert out['action']=='key' and out['combo']=='enter'
 state=a.client.evaluate.call_args.args[0];assert state['policy']=='app_public_search_literal' and state['current_run_input_continuity'] is True
 assert a.client.evaluate.call_args.args[1]['editing_mode']['type']=='choice'


@pytest.mark.parametrize('case',['ack_only','intervening_tap','duplicate_query','wrong_literal'])
def test_continuity_requires_exact_current_owned_sequence(monkeypatch,case):
 monkeypatch.setenv('HID_SEARCH_INPUT_CONTINUITY','1')
 a=fake(['app_ready'],{'ready':{'noul':0},'literal':{'noul':1}})
 a.steps.extend([{'workflow':'app_search','phase':'select','action':'key','_search_focus_verified':True,'act_s':.1}, {'workflow':'app_search','phase':'query','action':'type','text':'IELTS','act_s':.2}])
 if case=='ack_only':a.steps[1].pop('_search_focus_verified')
 if case=='intervening_tap':a.steps.insert(2,{'workflow':'app_search','phase':'focus','action':'tap','act_s':.1})
 if case=='duplicate_query':a.steps.append(dict(a.steps[-1]))
 if case=='wrong_literal':a.steps[-1]['text']='Other'
 out=asyncio.run(next_search_actions(a,{'app':'YouTube','query':'IELTS'}))[0]
 assert out['action']=='wait' and a.client.evaluate.call_args.args[0]['policy']=='app_public_search'


def test_literal_or_ownership_rejection_still_never_submits(monkeypatch):
 monkeypatch.setenv('HID_SEARCH_INPUT_CONTINUITY','1')
 for ready,literal in [(1,0),(0,1)]:
  a=fake(['app_ready'],{'ready':{'noul':ready},'literal':{'noul':literal}})
  a.steps.append({'workflow':'app_search','phase':'query','action':'type','_search_was_empty_verified':True,'text':'IELTS','act_s':.2})
  out=asyncio.run(next_search_actions(a,{'app':'TikTok','query':'IELTS'}))[0];assert out['action']=='wait'


@pytest.mark.parametrize('app',['YouTube','TikTok','X','LinkedIn','Bilibili'])
@pytest.mark.parametrize('focused,empty,action,phase',[(1,0,'tap','focus'),(1,1,'tap','focus'),(0,0,'tap','focus')])
def test_reuse_visible_focus_is_common_independent_editor_gate(monkeypatch,app,focused,empty,action,phase):
 monkeypatch.setenv('APP_SEARCH_REUSE_VISIBLE_FOCUS','1')
 monkeypatch.setattr('hidagent.app_search.public_search_control',lambda *a,**kw:{'app':app,'control':'public_search_field','x':500,'y':52})
 a=fake(['app_ready','search_control'],{'ready':{'noul':1},'focused':{'noul':focused},'empty':{'noul':empty}})
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad','orientation':'portrait'})
 out=asyncio.run(next_search_actions(a,{'app':app,'query':'IELTS'}))[0]
 assert out['action']==action and out['phase']==phase
 if action=='key':assert out['combo']=='cmd+backspace' and out['_search_focus_verified']
 if action=='type':assert out['_search_was_empty_verified'] and out['text']=='IELTS'


def test_reuse_visible_focus_wrong_owner_never_moves_or_types(monkeypatch):
 monkeypatch.setenv('APP_SEARCH_REUSE_VISIBLE_FOCUS','1')
 monkeypatch.setattr('hidagent.app_search.public_search_control',lambda *a,**kw:{'control':'public_search_field','x':500,'y':52})
 a=fake(['app_ready','search_control'],{'ready':{'noul':0},'focused':{'noul':1},'empty':{'noul':1}})
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 out=asyncio.run(next_search_actions(a,{'app':'YouTube','query':'IELTS'}))[0]
 assert out['action']=='wait' and out['phase']=='known_editor_ownership'


def test_reuse_visible_focus_cannot_enable_instagram_select_all(monkeypatch):
 monkeypatch.setenv('APP_SEARCH_REUSE_VISIBLE_FOCUS','1')
 monkeypatch.setattr('hidagent.app_search.public_search_control',lambda *a,**kw:{'control':'public_search_field','x':500,'y':52})
 a=fake(['app_ready','search_control'],{'ready':{'noul':1},'focused':{'noul':1},'empty':{'noul':0}})
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 out=asyncio.run(next_search_actions(a,{'app':'Instagram','query':'IELTS'}))[0]
 assert out['action']=='tap' and out['phase']=='focus'

@pytest.mark.parametrize('device,flag',[('iphone','1'),('ipad','0')])
def test_visible_focus_reuse_is_isolated_from_iphone_and_opt_out(monkeypatch,device,flag):
 monkeypatch.setenv('APP_SEARCH_REUSE_VISIBLE_FOCUS',flag)
 monkeypatch.setattr('hidagent.app_search.public_search_control',lambda *a,**kw:{'control':'public_search_field','x':500,'y':52})
 a=fake(['app_ready','search_control'],{'ready':{'noul':1},'focused':{'noul':1},'empty':{'noul':1}})
 a.phone=SimpleNamespace(target_info=lambda:{'device':device})
 out=asyncio.run(next_search_actions(a,{'app':'YouTube','query':'IELTS'}))[0]
 assert out['action']=='tap' and out['phase']=='focus'


def test_visible_focus_reuse_uncertain_empty_value_still_places_caret(monkeypatch):
 monkeypatch.setenv('APP_SEARCH_REUSE_VISIBLE_FOCUS','1')
 monkeypatch.setattr('hidagent.app_search.public_search_control',lambda *a,**kw:{'control':'public_search_field','x':500,'y':52})
 a=fake(['app_ready','search_control'],{'ready':{'noul':1},'focused':{'noul':1},'empty':{'noul':.5}})
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 actions=asyncio.run(next_search_actions(a,{'app':'YouTube','query':'IELTS'}))
 assert len(actions)==1 and actions[0]['action']=='tap' and actions[0]['phase']=='focus'
 assert 'empty' not in a.client.evaluate.call_args.args[1]


def test_unfocused_public_navigation_is_satisfiable_but_not_text_authorization():
 from hidagent.vision_judge import JudgeFallback
 questions={k:{'type':('box' if k=='target' else 'noul')} for k in ('ready','focused','empty','target','blocked')}
 answers={'ready':{'noul':1},'focused':{'noul':0},'empty':{'noul':0},'target':{'confidence':.99},'blocked':{'noul':0}}
 assert JudgeFallback._approved({'policy':'app_public_search'},questions,answers)
 assert not JudgeFallback._approved({'policy':'app_public_search_focus'},questions,answers)
 assert not JudgeFallback._approved({'policy':'app_public_search_literal'},questions,answers)


def test_readonly_ocr_candidate_needs_independent_point_gate_before_navigation():
 from unittest.mock import AsyncMock
 from types import SimpleNamespace
 a=fake(['app_ready'],{})
 a.phone=SimpleNamespace(calibrated=True,device=SimpleNamespace(links=1,connected=True),target_info=lambda:{'device':'ipad','orientation':'portrait','calibration_key':'ipad-portrait-66','width':710,'height':1080})
 a.screen.live=True
 a.navigation_text_reader=SimpleNamespace(read=AsyncMock(return_value={'rows':[{'text':'IELTS speaking fluency','confidence':.5,'box':{'left':.08,'right':.3,'top':.04,'bottom':.06}}],'read_s':.02}))
 async def judge(state,questions,frame):
  # Abstract combined location still fails; only an independently checked
  # concrete point in public Search is approved. OCR itself grants nothing.
  ready='target' not in questions
  return {'answers':{'ready':{'noul':float(ready)},'blocked':{'noul':0}},'seconds':.1}
 a.client.evaluate=AsyncMock(side_effect=judge)
 out=asyncio.run(next_search_actions(a,{'app':'rednote','query':'new query'}))[0]
 assert out['action']=='tap' and out['phase']=='search_control'
 assert out.get('_observed_public_navigation')
 a.navigation_text_reader.read.assert_awaited_once_with(a.screen.frame,mode='fast')
 assert all(call.args[1].keys()=={'ready','blocked'} for call in a.client.evaluate.await_args_list)
 assert out.get('_search_focus_verified') is not True
