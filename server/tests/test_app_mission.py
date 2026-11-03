import asyncio,json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from hidagent.app_mission import AppMission,MissionStart,mission_goal
from hidagent.app_tools import AppTools
from hidagent.receipt_journal import ReceiptJournal


def setup(tmp_path,plans):
 s={'device':True,'screen':True,'calibrated':True,'controller':{'stable':True},'agent':{'running':False},
    'target':{'device':'ipad','orientation':'portrait','width':710,'height':1080,'calibration_key':'ipad-portrait-66'},'control_activity':{}}
 memory=SimpleNamespace(snapshot=lambda:{'preferences':[],'discoveries':[]})
 current={'task_id':None}
 async def execute(*args,**kw):
  current['task_id']='task_'+str(len(bridge.execute.await_args_list))
  return {'status':'model_reported_done','running':False,**current}
 bridge=SimpleNamespace(exploration=None,execute=AsyncMock(side_effect=execute),snapshot=lambda **kw:current.copy())
 class Writer:
  model='fake'
  async def chat(self,system,user,**kw):
   if not system.startswith('You coordinate'):return '{"text":"Local QA draft."}'
   context=json.loads(user);plan=plans.pop(0)
   if callable(plan):plan=await plan(context)
   return json.dumps(plan)
 async def read(source,active):
  active()
  return {'source_kind':'verified_search_snapshot','verification_policy':'app_public_result_quotes','verification_passed':True,'visual_evidence_contract':'boxed_quote_byline_v2',
          'app':source['app'],'query':source['query'],'source_request_id':source['request_id'],'source_task_id':source['task_id'],
          'frame_sha256':'a'*64,'results':[{'title_quote':'Practice aloud','byline_quote':'Public author','box':{'left':.1,'right':.8,'top':.2,'bottom':.3}}]}
 journal=ReceiptJournal(tmp_path/'tasks.sqlite3');writer=Writer();stop=AsyncMock()
 tools=AppTools(bridge,writer,memory,lambda:s,stop,result_reader=SimpleNamespace(read=read),journal=journal)
 mission=AppMission(tools,writer,memory,lambda:s,stop)
 body=MissionStart(server_instance=tools.server_instance,request_id=tools.server_instance+'_qa_goal_one',goal='Research a speaking idea and prepare a local draft')
 return mission,tools,bridge,journal,s,body


def plan(name,args=None):return {'kind':'use_tool','tool_name':name,'args':args or {},'reason':'Use an existing public skill.'}
def finish():return {'kind':'finish','reason':'Return the actual results for review.'}
async def wait(m):
 await asyncio.wait_for(asyncio.shield(m.current['supervisor']),2)
 return m.status()


def test_explicit_goal_selects_search_read_and_source_bound_draft(tmp_path):
 async def run():
  async def draft(context):return plan('x.prepare_draft',{'goal':'Draft a practical English idea.','source_read_request_id':context['checked_public_sources'][0]['request_id']})
  m,t,b,j,s,body=setup(tmp_path,[plan('youtube.search',{'query':'speaking practice'}),plan('public.read_results'),draft,finish()])
  try:
   first=await m.start(body);assert first['running'] and t.busy
   with pytest.raises(HTTPException):await m.start(body.model_copy(update={'request_id':t.server_instance+'_other_qa'}))
   receipt=await t.call('outside_1','x.open',{});assert receipt['status']=='busy'
   r=await wait(m)
   assert r['status']=='awaiting_feedback' and not r['running'] and r['receipt_persisted']
   assert [x['tool_name'] for x in r['steps']]==['youtube.search','public.read_results','x.prepare_draft']
   assert r['drafts'][0]['body']=='Local QA draft.' and not r['drafts'][0]['published']
   assert r['findings'][0]['results'][0]['title_quote']=='Practice aloud'
   b.execute.assert_awaited_once()
   assert (await m.start(body))['status']=='awaiting_feedback'
   assert j.get(body.request_id)['state']=='needs_review'
  finally:j.close()
 asyncio.run(run())


@pytest.mark.parametrize('bad',[plan('x.publish',{}),plan('keyboard.type_focused',{'text':'private'}),plan('threads.search',{'query':'x'}),plan('public.read_results'),plan('x.prepare_draft',{'goal':'Draft','source_read_request_id':'fake_source_id'})])
def test_goal_cannot_invent_tools_or_old_sources(tmp_path,bad):
 async def run():
  m,t,b,j,s,body=setup(tmp_path,[bad])
  try:
   await m.start(body);r=await wait(m);assert r['status']=='failed';b.execute.assert_not_called();assert not t.calls
  finally:j.close()
 asyncio.run(run())


def test_stop_revokes_swallowed_model_cancellation_and_holds_ownership(tmp_path):
 async def run():
  entered,release=asyncio.Event(),asyncio.Event()
  async def blocked(context):
   entered.set()
   try:await release.wait()
   except asyncio.CancelledError:await release.wait()
   return plan('youtube.search',{'query':'late input'})
  m,t,b,j,s,body=setup(tmp_path,[blocked])
  try:
   await m.start(body);await entered.wait();m.revoke();await t.stop();assert t.busy
   assert m.status()['status']=='stopped';b.execute.assert_not_called()
   release.set();r=await wait(m);assert r['status']=='stopped' and not t.busy;b.execute.assert_not_called()
   assert j.get(body.request_id)['state']=='stopped'
  finally:release.set();j.close()
 asyncio.run(run())


def test_failed_skill_prevents_next_plan_and_replay(tmp_path):
 async def run():
  m,t,b,j,s,body=setup(tmp_path,[plan('youtube.search',{'query':'speech'}),finish()]);b.execute.side_effect=None;b.execute.return_value={'status':'failed','running':False,'never_retry_typing':True}
  try:
   await m.start(body);r=await wait(m);assert r['status']=='failed' and len(r['steps'])==1
   # The failed skill falls back once to the general agent; nothing replays afterwards.
   await m.start(body);assert b.execute.await_count==2
  finally:j.close()
 asyncio.run(run())


def test_unchanged_operation_is_never_repeated_by_planner(tmp_path):
 async def run():
  p=plan('youtube.search',{'query':'speech'});m,t,b,j,s,body=setup(tmp_path,[p,p])
  try:
   await m.start(body);r=await wait(m);assert r['status']=='failed' and b.execute.await_count==1
  finally:j.close()
 asyncio.run(run())


def test_goal_geometry_change_after_planning_stops_before_hid(tmp_path):
 async def run():
  async def change(context):s['target']['orientation']='landscape';return plan('youtube.search',{'query':'speech'})
  m,t,b,j,s,body=setup(tmp_path,[change])
  try:
   await m.start(body);r=await wait(m);assert r['status']=='failed';b.execute.assert_not_called()
  finally:j.close()
 asyncio.run(run())


def test_goal_requires_current_readiness_and_journal(tmp_path):
 async def run():
  m,t,b,j,s,body=setup(tmp_path,[finish()])
  try:
   s['screen']=False
   with pytest.raises(HTTPException):await m.start(body)
   assert not j.history()[0];b.execute.assert_not_called()
  finally:j.close()
 asyncio.run(run())


def test_goal_restart_returns_historical_without_new_planning(tmp_path):
 async def run():
  m,t,b,j,s,body=setup(tmp_path,[finish()]);await m.start(body);await wait(m);j.close()
  fresh,t2,b2,j2,s2,_=setup(tmp_path,[])
  try:
   body=body.model_copy(update={'server_instance':t2.server_instance,'request_id':body.request_id})
   # Boot-scoped HTTP request rejection precedes any read/replay. GET history remains available.
   with pytest.raises(HTTPException):await fresh.start(body)
   r=t2.status(body.request_id);assert r['historical'] and not r['running'];b2.execute.assert_not_called()
  finally:j2.close()
 asyncio.run(run())


@pytest.mark.parametrize('text,expected',[('Work toward this goal: Find a public idea.','Find a public idea.'),('Start a goal: Read public results','Read public results'),('type Work toward this goal: publish',None),('Find an idea',None)])
def test_goal_intent_requires_explicit_user_boundary(text,expected):assert mission_goal(text)==expected


def test_voice_goal_uses_original_utterance_and_stop_revokes_it(tmp_path):
 from hidagent.live_voice import VoiceBridge,ToolCall,VoiceControl
 import time
 async def run():
  entered,release=asyncio.Event(),asyncio.Event()
  async def blocked(context):
   entered.set()
   try:await release.wait()
   except asyncio.CancelledError:await release.wait()
   return plan('youtube.search',{'query':'late'})
  m,t,b,j,s,body=setup(tmp_path,[blocked]);s['control_activity']['voice_sessions']=1
  async def stop():m.revoke();await t.stop();await m.stop();return {'release_pending_reconnect':False}
  v=VoiceBridge(lambda:s,AsyncMock(),stop);v.app_tools=t;v.mission=m
  sid='voice_mission_session_0001';v.sessions[sid]={'expires':time.monotonic()+60,'calls':{},'device_family':'ipad'}
  try:
   await v.call(ToolCall(session=sid,id='goal_one',name='start_app_goal',args={'goal':'model changed it'},utterance='Work toward this goal: Research one public idea.'))
   await entered.wait();assert m.current['goal']=='Research one public idea.'
   result=await v.interrupt(VoiceControl(session=sid,ids=['goal_one']))
   assert result['status']=='stopped' and result['release_pending_reconnect'] is False
   release.set();await wait(m);b.execute.assert_not_called()
   assert v.resume(VoiceControl(session=sid,text='Work toward this goal: Another public idea.'))['status']=='ready'
  finally:release.set();j.close()
 asyncio.run(run())


def test_mission_http_readonly_status_and_stale_stop_are_bounded(tmp_path):
 from fastapi import FastAPI
 from fastapi.testclient import TestClient
 from hidagent.app_mission import router_for
 m,t,b,j,s,body=setup(tmp_path,[]);stop=AsyncMock();app=FastAPI();app.include_router(router_for(m,stop))
 try:
  with TestClient(app,base_url='http://127.0.0.1:8890') as client:
   assert client.get('/api/mission/status').json()['status']=='idle'
   assert client.get('/api/mission/status',headers={'Origin':'https://evil.example'}).status_code==403
   assert client.post('/api/mission/stop',json={'request_id':'old_goal_request'}).status_code==409
   assert client.post('/api/mission/start',json={**body.model_dump(),'max_steps':1000}).status_code==422
   stop.assert_not_called();b.execute.assert_not_called()
 finally:j.close()


def test_stop_inside_explicit_goal_is_a_goal_condition_not_a_new_stop():
 from hidagent.control_intent import is_stop_intent,is_action_intent
 assert not is_stop_intent('Work toward this goal: Find one idea, then stop for review.')
 assert is_action_intent('Work toward this goal: Find one idea, then stop for review.')
 assert is_stop_intent('Stop the goal now.')


def test_restarted_goal_may_have_sent_device_input_and_never_resumes(tmp_path):
 j=ReceiptJournal(tmp_path/'tasks.sqlite3');j.reserve('old_goal_started','old_boot','signature','mission.run','sequence');j.started('old_goal_started','old_boot');j.close()
 m,t,b,j,s,body=setup(tmp_path,[])
 try:
  receipt=t.status('old_goal_started')
  assert receipt['historical'] and receipt['status']=='interrupted'
  assert receipt['result']['delivery_uncertain'] and receipt['result']['never_retry_typing']
  assert receipt['result']['input_delivery']=='unknown'
  b.execute.assert_not_called()
 finally:j.close()


def test_cross_app_read_reuses_skill_with_distinct_actual_search_parent(tmp_path):
 async def run():
  m,t,b,j,s,body=setup(tmp_path,[plan('youtube.search',{'query':'speech'}),plan('public.read_results'),plan('x.search',{'query':'speech'}),plan('public.read_results'),finish()])
  try:
   await m.start(body);r=await wait(m)
   assert r['status']=='awaiting_feedback' and len(r['findings'])==2
   assert {f['app'] for f in r['findings']}=={'YouTube','X'}
   assert b.execute.await_count==2
  finally:j.close()
 asyncio.run(run())


def test_verbose_finish_rationale_cannot_fail_completed_work(tmp_path):
 async def run():
  m,t,b,j,s,body=setup(tmp_path,[{**finish(),'reason':'Review the checked results. '*30}])
  try:
   await m.start(body);r=await wait(m);assert r['status']=='awaiting_feedback' and not r.get('failure_kind')
   assert r['planning_s']>=0;b.execute.assert_not_called()
  finally:j.close()
 asyncio.run(run())


@pytest.mark.parametrize('utterance',['Work toward this goal, find one public idea.','Work towards this goal. Find one public idea.','Work toward this goal find one public idea.'])
def test_spoken_goal_punctuation_is_not_a_literal_colon_requirement(utterance):
 from hidagent.control_intent import is_action_intent
 assert mission_goal(utterance).lower()=='find one public idea.'
 assert is_action_intent(utterance)


def test_meta_goal_planner_omits_personal_memory(tmp_path):
 async def run():
  m,t,b,j,s,body=setup(tmp_path,[finish()]);m.writer.provider='meta'
  def private_memory():raise AssertionError('Personal memory accessed by Meta planner')
  m.memory=SimpleNamespace(snapshot=private_memory)
  try:
   await m.start(body);r=await wait(m);assert r['status']=='awaiting_feedback';b.execute.assert_not_called()
  finally:j.close()
 asyncio.run(run())


@pytest.mark.parametrize('lost',['screen','device','calibrated','controller'])
def test_goal_lost_readiness_during_planning_never_accepts_late_finish(tmp_path,lost):
 async def run():
  async def disconnect(context):
   if lost=='controller':s['controller']['stable']=False
   else:s[lost]=False
   return finish()
  m,t,b,j,s,body=setup(tmp_path,[disconnect])
  try:
   await m.start(body);r=await wait(m)
   assert r['status']=='failed' and r['failure_code']=='device_not_ready'
   assert not r['steps'];b.execute.assert_not_called()
   assert j.get(body.request_id)['result']['failure_code']=='device_not_ready'
  finally:j.close()
 asyncio.run(run())


def test_goal_new_bluetooth_generation_during_planning_rejects_old_plan(tmp_path):
 async def run():
  async def reconnect(context):
   s['controller']['link_generation']=8
   return plan('youtube.search',{'query':'late old plan'})
  m,t,b,j,s,body=setup(tmp_path,[reconnect]);s['controller']['link_generation']=7
  try:
   await m.start(body);r=await wait(m)
   b.execute.assert_not_called()
   assert r['status']=='failed' and r['failure_code']=='connection_changed'
   assert not r['steps']
   # A healthy new connection is not authorization to resume the old goal.
   assert (await m.start(body))['status']=='failed';b.execute.assert_not_called()
  finally:j.close()
 asyncio.run(run())


def test_goal_screen_loss_with_active_child_cancels_without_next_skill(tmp_path):
 async def run():
  entered,cancelled=asyncio.Event(),asyncio.Event()
  async def device_work(*args,**kwargs):
   entered.set()
   try:await asyncio.Event().wait()
   except asyncio.CancelledError:cancelled.set();raise
  m,t,b,j,s,body=setup(tmp_path,[plan('youtube.search',{'query':'speech'}),finish()]);b.execute.side_effect=device_work
  try:
   await m.start(body);await entered.wait();s['screen']=False
   r=await wait(m)
   assert r['status']=='failed' and r['failure_code']=='device_not_ready'
   assert cancelled.is_set() and b.execute.await_count==1
   m.stop_phone.assert_awaited_once()
   assert not t.busy and not t.tool_busy and r['receipt_persisted']
   assert r['steps'][0]['completion_unconfirmed'] and r['steps'][0]['status']=='stopped'
  finally:j.close()
 asyncio.run(run())


def test_goal_child_inherits_connection_guard_at_each_hid_stage(tmp_path):
 from hidagent.hid_lease import check_hid
 async def run():
  wire=[]
  async def device_work(*args,**kwargs):
   s['controller']['link_generation']=8
   try:check_hid('key enter');wire.append('key enter')
   finally:check_hid('release')  # rescue release remains allowed
   return {'status':'model_reported_done','running':False}
  m,t,b,j,s,body=setup(tmp_path,[plan('youtube.search',{'query':'speech'}),finish()]);b.execute.side_effect=device_work;s['controller']['link_generation']=7
  try:
   await m.start(body);r=await wait(m)
   assert wire==[]
   assert r['status']=='failed' and r['failure_code']=='connection_changed'
  finally:j.close()
 asyncio.run(run())


def test_meta_goal_uses_strict_schema_without_arbitrary_argument_objects(tmp_path):
 async def run():
  m,t,b,j,s,body=setup(tmp_path,[]);m.writer.provider='meta';seen=[]
  async def chat(system,user,**kwargs):seen.append(kwargs);return json.dumps(finish())
  m.writer.chat=chat
  try:
   await m.start(body);r=await wait(m);assert r['status']=='awaiting_feedback'
   f=seen[0]['response_format'];assert f['json_schema']['strict'] is True
   schema=f['json_schema']['schema'];assert set(schema['required'])==set(schema['properties'])
   assert 'x.publish' not in schema['properties']['tool_name']['enum']
   for variant in schema['properties']['args']['anyOf']:
    assert variant['additionalProperties'] is False
    assert set(variant['required'])==set(variant['properties'])
  finally:j.close()
 asyncio.run(run())
