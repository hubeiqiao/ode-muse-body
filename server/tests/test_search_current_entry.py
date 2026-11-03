import asyncio
from unittest.mock import patch,AsyncMock
from types import SimpleNamespace
import pytest
from test_app_search import fake, assert_query_batch
from hidagent.app_search import next_search_actions
from hidagent.decision import DecisionUnavailable

def candidate():return {'x':500,'y':50,'control':'public_search_entry','template':'search.json'}
def agent(ready=1,blocked=0,steps=()):
 a=fake(steps,{'ready':{'noul':ready}});a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad','orientation':'portrait'})
 a.client.evaluate=AsyncMock(return_value={'answers':{'ready':{'noul':ready},'blocked':{'noul':blocked}},'seconds':.1});return a

def test_current_public_entry_no_home_or_typing():
 a=agent()
 with patch('hidagent.app_search.public_search_control',return_value=candidate()),patch('hidagent.shortcut_workflow.next_shortcut_actions',new_callable=AsyncMock) as launch:
  out=asyncio.run(next_search_actions(a,{'app':'TikTok','query':'IELTS'}))[0]
 assert out['action']=='tap' and out['phase']=='search_control' and out['_search_foreground_verified'] is True
 assert out['_learned_control_template']=='search.json';launch.assert_not_called()

@pytest.mark.parametrize('ready',[0,.5,.98])
def test_unconfirmed_current_entry_never_launches_or_mutates(ready):
 a=agent(ready)
 with patch('hidagent.app_search.public_search_control',return_value=candidate()),patch('hidagent.shortcut_workflow.next_shortcut_actions',new_callable=AsyncMock) as launch:
  out=asyncio.run(next_search_actions(a,{'app':'TikTok','query':'IELTS'}))[0]
 assert out['action']=='wait';launch.assert_not_called()

def test_modal_rejects_before_tap():
 a=agent(blocked=1)
 with patch('hidagent.app_search.public_search_control',return_value=candidate()):
  with pytest.raises(DecisionUnavailable):asyncio.run(next_search_actions(a,{'app':'TikTok','query':'IELTS'}))

def test_current_template_changed_after_judgment_no_click():
 a=agent()
 with patch('hidagent.app_search.public_search_control',side_effect=[candidate(),None]):out=asyncio.run(next_search_actions(a,{'app':'TikTok','query':'IELTS'}))[0]
 assert out['action']=='wait' and out['phase']=='search_control_changed'

def test_missing_template_keeps_existing_open_route():
 a=agent()
 with patch('hidagent.app_search.public_search_control',return_value=None),patch('hidagent.shortcut_workflow.next_shortcut_actions',new_callable=AsyncMock,return_value=[{'action':'key','combo':'cmd+h'}]) as launch:
  out=asyncio.run(next_search_actions(a,{'app':'TikTok','query':'IELTS'}))[0]
 assert out['combo']=='cmd+h';launch.assert_awaited_once()

@pytest.mark.parametrize('kind,phase',[('public_search_form_entry','search_form'),('public_search_field','focus')])
def test_current_form_and_field_keep_distinct_focus_stages(kind,phase):
 a=agent()
 def match(frame,app,target,**kw):return {**candidate(),'control':kind} if kw.get('control',kind)==kind else None
 with patch('hidagent.app_search.public_search_control',side_effect=match):out=asyncio.run(next_search_actions(a,{'app':'YouTube','query':'IELTS'}))[0]
 assert out['action']=='tap' and out['phase']==phase and out['_learned_search_control']==kind

@pytest.mark.parametrize('name,key',[('ChatGPT','chatgpt'),('Grok Bot','grok'),('Grok','grok')])
def test_added_assistant_open_routes_no_implicit_send(name,key):
 from hidagent.app_tools import match_goal,TOOLS
 assert match_goal('Open '+name)==(key+'.open',{})
 assert key+'.ask' not in TOOLS
 assert match_goal('Open '+name+' and publish hello') is None

@pytest.mark.parametrize('empty',[1,0,.5])
def test_cold_learned_editor_places_caret_despite_model_focus(empty):
 a=agent();a.client.evaluate=AsyncMock(return_value={'answers':{'ready':{'noul':1},'focused':{'noul':1},'empty':{'noul':empty},'blocked':{'noul':0}},'seconds':.1})
 def match(frame,app,target,**kw):return {**candidate(),'control':'public_search_field'} if kw.get('control')=='public_search_field' else None
 with patch('hidagent.app_search.public_search_control',side_effect=match):actions=asyncio.run(next_search_actions(a,{'app':'TikTok','query':'word stress'}))
 assert len(actions)==1 and actions[0]['action']=='tap' and actions[0]['phase']=='focus'
 assert all(action['_search_foreground_verified'] for action in actions)
 assert 'empty' not in a.client.evaluate.call_args.args[1]

def test_already_tapped_form_navigation_is_not_repeated_on_stale_frame():
 a=agent(steps=['app_ready','search_form']);a.steps[-1].update(act_s=.1,_learned_public_search='TikTok',_learned_search_control='public_search_form_entry')
 def match(frame,app,target,**kw):return {**candidate(),'control':'public_search_form_entry'} if kw.get('control')=='public_search_form_entry' else None
 with patch('hidagent.app_search.public_search_control',side_effect=match),patch('hidagent.app_search.asyncio.sleep',new_callable=AsyncMock):out=asyncio.run(next_search_actions(a,{'app':'TikTok','query':'word stress'}))[0]
 assert out['action']=='wait' and out['phase']=='form_navigation_transition'
