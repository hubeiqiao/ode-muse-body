"""Offline fail-first regressions for bounded settle motion and query clearing."""
import asyncio
import time

import pytest

from hidagent.app_search import next_search_actions
from hidagent.shortcut_workflow import next_shortcut_actions
from hidagent.scene_scope import verdict_scene_changed
from hidagent.jev_agent import scene_changed
from test_scene_scope import make_agent, picture, BOX
from test_app_search import fake


@pytest.mark.parametrize('edge', ['top', 'bottom'])
def test_search_first_scene_wait_samples_received_settle_frames(edge):
 async def run():
  a=make_agent('app_search',picture(40),picture(100),prior=False)
  box=BOX if edge=='top' else {'left':.82,'right':.92,'top':.9,'bottom':.96}
  original=a.client.evaluate.side_effect
  async def evaluate(*args):
   result=await original(*args)
   result['answers']['target']['box']=box
   return result
  a.client.evaluate.side_effect=evaluate
  goal={'app':'YouTube','query':'English practice'}
  # Box-only rule (2026-10-30): motion outside the located target box no longer
  # costs a scene_changed wait round; the first decision taps directly.
  result=(await next_search_actions(a,goal))[0]
  assert result['action']=='tap' and result['phase']=='search_control'
  assert sum(s.get('phase')=='scene_changed' for s in a.steps)==0
 asyncio.run(run())


def assert_cleared_query(actions, query, app=None):
 assert [(a['action'],a.get('combo')) for a in actions[:-1]] == [('key','cmd+right'),('key','cmd+backspace')]+[('key','backspace')]*4
 assert actions[-1]['action']=='type' and actions[-1]['text']==query
 assert actions[-1]['phase']=='query'
 assert actions[-1]['_search_clear_sequence']=={'select':None,'caret_to_end':'cmd+right','delete_to_line_start':'cmd+backspace','backspaces':4}
 assert all(a['workflow']=='app_search' and a.get('_search_focus_verified') is True for a in actions)
 assert not any(a.get('_search_was_empty_verified') for a in actions)


@pytest.mark.parametrize('app',['rednote','YouTube','TikTok','Bilibili','Douyin','Instagram'])
@pytest.mark.parametrize('empty',[0,.5,1,None])
def test_focused_query_always_clears_then_types_without_empty_judgment(app,empty):
 answers={'ready':{'noul':1},'focused':{'noul':1}}
 if empty is not None:answers['empty']={'noul':empty}
 a=fake(['app_ready','focus'],answers)
 actions=asyncio.run(next_search_actions(a,{'app':app,'query':'IELTS'}))
 assert_cleared_query(actions,'IELTS',app)
 assert a.client.evaluate.await_count==1
 assert 'empty' not in a.client.evaluate.call_args.args[1]


@pytest.mark.parametrize('ready,blocked',[(0,0),(.98,0),(1,1)])
def test_unverified_focus_never_sends_backspace_or_query(ready,blocked):
 a=fake(['app_ready','focus'],{'ready':{'noul':ready}})
 a.client.evaluate.return_value['answers']['blocked']={'noul':blocked}
 if blocked:
  from hidagent.app_search import PublicSearchBlocked
  with pytest.raises(PublicSearchBlocked):asyncio.run(next_search_actions(a,{'app':'rednote','query':'IELTS'}))
 else:
  actions=asyncio.run(next_search_actions(a,{'app':'rednote','query':'IELTS'}))
  assert all(x['action']=='wait' for x in actions)


def test_literal_refusal_after_clearing_never_retypes_or_submits():
 a=fake(['app_ready','focus'],{'ready':{'noul':1}})
 goal={'app':'rednote','query':'IELTS'}
 actions=asyncio.run(next_search_actions(a,goal))
 assert_cleared_query(actions,'IELTS','rednote')
 a.steps.extend({**x,'act_s':.1} for x in actions)
 a.client.evaluate.return_value={'answers':{'ready':{'noul':0},'blocked':{'noul':0}},'seconds':.1}
 result=asyncio.run(next_search_actions(a,goal))
 assert len(result)==1 and result[0]['phase']=='exact_query_visible'
 assert result[0]['action']=='wait'

def test_stability_keyboard_first_verdict_uses_post_return_settle_frames():
 async def run():
  a=make_agent('keyboard',picture(40),picture(220),prior=False)
  a.steps=[{'workflow':'keyboard','phase':'query','action':'type','text':'YouTube','act_s':.1}]
  post_launch=a.client.evaluate.side_effect
  a.client.evaluate.side_effect=None
  a.client.evaluate.return_value={'answers':{'page':{'choice':'search','confidence':.99},'ready':{'noul':1}},'seconds':.1}
  launch=(await next_shortcut_actions(a,{'app':'YouTube','text':None}))[0]
  assert launch['phase']=='launch'
  a.client.evaluate.side_effect=post_launch
  a.steps.append({'workflow':'keyboard','phase':'launch','action':'key','combo':'enter','act_s':.1,'t':time.time()})
  for level in (40,100,160):
   a.screen.frame=picture(level);a.screen.updated=time.time()
   await asyncio.sleep(.06)
  result=(await next_shortcut_actions(a,{'app':'YouTube','text':None}))[0]
  assert result['action']=='done'
  assert not any(s.get('phase')=='changed_scene' for s in a.steps)
 asyncio.run(run())


def test_stability_last_settle_frame_is_captured_before_verdict():
 from hidagent.scene_scope import start_launch_observation,finish_launch_observation
 async def run():
  a=make_agent('keyboard',picture(40),picture(220),prior=False)
  goal={'app':'YouTube','text':None}
  start_launch_observation(a,goal)
  a.steps.append({'workflow':'keyboard','phase':'launch','action':'key','combo':'enter','act_s':.1,'t':time.time()})
  for level in (40,100):
   a.screen.frame=picture(level);a.screen.updated=time.time();await asyncio.sleep(.06)
  a.screen.frame=picture(160);a.screen.updated=time.time()
  finish_launch_observation(a,goal)
  assert not verdict_scene_changed(a,picture(160),picture(220),{'action':'done'},scene_changed,foreground=True,workflow='keyboard')
 asyncio.run(run())


@pytest.mark.parametrize('workflow',['keyboard','app_search'])
@pytest.mark.parametrize('problem',['stale','unacknowledged','handoff','new_task','goal_changed','chrome','target_motion','missing','post_verdict'])
def test_stability_settle_history_does_not_weaken_conservative_gates(workflow,problem):
 from hidagent.scene_scope import start_launch_observation,finish_launch_observation
 async def run():
  a=make_agent(workflow,picture(40),picture(220),prior=False)
  goal={'app':'YouTube','text':None} if workflow=='keyboard' else {'app':'YouTube','query':'English practice'}
  start_launch_observation(a,goal,workflow=workflow)
  step={'phase':'launch','action':'key','combo':'enter'} if workflow=='keyboard' else {'phase':'scene_changed','action':'wait'}
  a.steps.append({**step,'workflow':workflow,'act_s':.1,'t':time.time()})
  for level in (40,100,160):
   a.screen.frame=picture(level);a.screen.updated=time.time();await asyncio.sleep(.04)
  if problem=='unacknowledged':a.steps[-1].pop('act_s')
  elif problem=='handoff':a.steps.append({'workflow':'app_search','phase':'focus','action':'tap'})
  elif problem=='new_task':a.steps=list(a.steps)
  elif problem=='goal_changed':goal={**goal,'app':'TikTok'}
  finish_launch_observation(a,goal,workflow=workflow)
  record=a._launch_motion if workflow=='keyboard' else a._search_motion
  if problem=='stale':
   record['samples']=[(at-60,frame) for at,frame in record['samples']]
  if problem=='missing':record['samples']=record['samples'][-2:]
  before=picture(160)
  if problem=='post_verdict':
   before=picture(180)
   a.screen.frame=before;a.screen.updated=time.time();await asyncio.sleep(.04)
   assert record['samples'][-1][1]==picture(160)
  action={'action':'done'} if workflow=='keyboard' else {'action':'tap','x':870,'y':130}
  box=None if workflow=='keyboard' else BOX
  after=picture(220,'chrome' if problem=='chrome' else None)
  if problem=='target_motion':
   action={'action':'tap','x':250,'y':350}
   box={'left':.2,'top':.3,'right':.3,'bottom':.4}
  assert verdict_scene_changed(a,before,after,action,scene_changed,foreground=True,workflow=workflow,box=box)
 asyncio.run(run())


@pytest.mark.parametrize('old_text',['old editor text','previous search query '+('x'*80)])
def test_fixed_batch_executes_ascii_keys_and_records_every_clear(old_text):
 from types import SimpleNamespace
 from unittest.mock import AsyncMock,Mock
 from hidagent.agent import Agent
 async def run():
  a=fake(['app_ready','focus'],{'ready':{'noul':1}})
  actions=await next_search_actions(a,{'app':'rednote','query':'IELTS'})
  # A tap may leave the caret mid-text (seen on TikTok: old tail survived).
  text=old_text;caret=len(text)//2
  async def key(combo):
   nonlocal text,caret
   if combo=='cmd+right':caret=len(text)
   elif combo=='cmd+backspace':text=text[caret:];caret=0
   elif combo=='backspace' and caret:text=text[:caret-1]+text[caret:];caret-=1
  async def type_query(value):
   nonlocal text,caret
   text=text[:caret]+value+text[caret:];caret+=len(value)
  phone=SimpleNamespace(key=AsyncMock(side_effect=key),type=AsyncMock(side_effect=type_query))
  runner=Agent(phone,a.screen,None,None,Mock())
  runner._next_actions=AsyncMock(return_value=actions)
  runner._observe=AsyncMock()
  await runner._step()
  assert text=='IELTS'
  assert phone.key.await_count==6 and phone.type.await_count==1
  assert len(runner.steps)==7 and all('act_s' in step for step in runner.steps)
  assert runner.steps[-1]['_search_clear_sequence']=={'select':None,'caret_to_end':'cmd+right','delete_to_line_start':'cmd+backspace','backspaces':4}
  assert [s['_search_clear_index'] for s in runner.steps[:-1]]==list(range(6))
 asyncio.run(run())


@pytest.mark.parametrize('app',['Instagram','Douyin'])
def test_select_all_skipped_for_apps_with_former_guards(app):
    a=fake(['app_ready','focus'],{'ready':{'noul':1}})
    actions=asyncio.run(next_search_actions(a,{'app':app,'query':'IELTS'}))
    assert [a.get('combo') for a in actions[:-1]]==['cmd+right','cmd+backspace']+['backspace']*4
    assert not any(a.get('combo')=='cmd+a' for a in actions)
