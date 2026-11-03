import asyncio,json,os,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from hidagent.app_tools import AppTools
from hidagent.receipt_journal import ReceiptJournal,ReceiptJournalError,ReceiptConflict


def setup(path,*,writer=None,bridge=None,journal=None):
 state={'device':True,'screen':True,'calibrated':True,'controller':{'stable':True},'agent':{'running':False}}
 bridge=bridge or SimpleNamespace(exploration=None,execute=AsyncMock(return_value={'status':'model_reported_done','running':False}),snapshot=lambda **kw:{})
 writer=writer or SimpleNamespace(model='test-haiku',chat=AsyncMock(return_value='{"text":"Local review draft."}'))
 memory=SimpleNamespace(snapshot=lambda:{'preferences':[],'discoveries':[]})
 journal=journal or ReceiptJournal(path)
 return AppTools(bridge,writer,memory,lambda:state,AsyncMock(),journal=journal),journal,bridge,writer,state


def test_single_owner_permissions_and_id_conflict(tmp_path):
 path=tmp_path/'tasks.sqlite3';j=ReceiptJournal(path)
 try:
  assert path.stat().st_mode&0o777==0o600
  with pytest.raises(ReceiptJournalError):ReceiptJournal(path)
  j.reserve('request_one','boot_one','sig','x.open','device')
  assert j.reserve('request_one','boot_one','sig','x.open','device')['state']=='accepted'
  with pytest.raises(ReceiptConflict):j.reserve('request_one','boot_two','different','x.open','device')
 finally:j.close()
 reopened=ReceiptJournal(path);reopened.close()


def test_started_uncertainty_and_accepted_no_dispatch_survive_real_process_exit(tmp_path):
 path=tmp_path/'crash.sqlite3'
 code='from hidagent.receipt_journal import ReceiptJournal; import os,sys; j=ReceiptJournal(sys.argv[1]); j.reserve("started_one","old_boot","s","keyboard.type_focused","device"); j.started("started_one","old_boot"); j.reserve("accepted_one","old_boot","s2","x.open","device"); os._exit(0)'
 subprocess.run([sys.executable,'-c',code,str(path)],check=True)
 j=ReceiptJournal(path)
 try:
  j.recover('new_boot');started=j.get('started_one');accepted=j.get('accepted_one')
  assert started['state']==accepted['state']=='interrupted'
  assert started['result']['delivery_uncertain'] and started['result']['never_retry_typing']
  assert accepted['result']['input_delivery']=='not_dispatched' and not accepted['result']['never_retry_typing']
  with pytest.raises(ReceiptConflict):j.started('started_one','new_boot')
 finally:j.close()


def test_stopped_tombstone_cannot_be_overwritten_by_late_completion(tmp_path):
 j=ReceiptJournal(tmp_path/'tasks.sqlite3')
 try:
  j.reserve('request_one','boot_one','sig','x.prepare_draft','local_draft');j.started('request_one','boot_one')
  assert j.finish('request_one','boot_one','stopped',{'reason':'stop'},.1)
  assert not j.finish('request_one','boot_one','needs_review',{'body':'late'},1)
  assert j.get('request_one')['result']=={'reason':'stop'}
 finally:j.close()


def test_full_history_rejects_before_a_second_reservation(tmp_path):
 j=ReceiptJournal(tmp_path/'tasks.sqlite3',capacity=1)
 try:
  j.reserve('one','boot','sig','x.open','device')
  with pytest.raises(ReceiptJournalError,match='full'):j.reserve('two','boot','sig','x.open','device')
  assert j.get('two') is None and j.history()[1]==1
 finally:j.close()


def test_same_saved_request_after_restart_returns_history_without_calling_models_or_hid(tmp_path):
 async def run():
  path=tmp_path/'tasks.sqlite3';tools,j,bridge,writer,state=setup(path)
  first=await tools.call('saved_draft_one','x.prepare_draft',{'goal':'Draft one English local review post.'})
  old_boot=tools.server_instance;j.close()
  fresh,j,bridge,writer,state=setup(path);state['device']=state['screen']=False
  try:
   receipt=await fresh.call('saved_draft_one','x.prepare_draft',{'goal':'Draft one English local review post.'})
   assert receipt['historical'] and receipt['origin_server_instance']==old_boot
   assert not receipt['running'] and receipt['terminal'] and not receipt['live_device_verification']
   assert receipt['result']['body']==first['result']['body'] and receipt['receipt_persisted']
   assert not receipt['screen_ready'];bridge.execute.assert_not_called();writer.chat.assert_not_called()
   with pytest.raises(HTTPException):await fresh.call('saved_draft_one','x.prepare_draft',{'goal':'Different brief'})
  finally:j.close()
 asyncio.run(run())


def test_typing_literal_is_not_copied_to_receipt_database(tmp_path):
 async def run():
  path=tmp_path/'tasks.sqlite3';tools,j,bridge,_,_=setup(path)
  literal='PRIVATE_TEST_LITERAL_89'
  bridge.execute.return_value={'status':'model_reported_done','running':False,'last_action':{'action':'type','text':literal}}
  try:
   r=await tools.call('typing_one','keyboard.type_focused',{'text':literal})
   assert r['receipt_persisted'] and 'last_action' not in r['result']['receipt']
   assert literal not in json.dumps(j.get('typing_one'))
  finally:j.close()
  assert literal.encode() not in path.read_bytes()
 asyncio.run(run())


def test_reservation_failure_blocks_model_and_hid(tmp_path,monkeypatch):
 async def run():
  tools,j,bridge,writer,_=setup(tmp_path/'tasks.sqlite3')
  def fail(*a):raise ReceiptJournalError('disk unavailable')
  monkeypatch.setattr(j,'reserve',fail)
  try:
   with pytest.raises(HTTPException) as exc:await tools.call('request_one','x.prepare_draft',{'goal':'Draft a local post.'})
   assert exc.value.status_code==503 and not tools.calls
   bridge.execute.assert_not_called();writer.chat.assert_not_called()
  finally:j.close()
 asyncio.run(run())


def test_dispatch_mark_failure_never_runs_tool(tmp_path,monkeypatch):
 async def run():
  tools,j,bridge,writer,_=setup(tmp_path/'tasks.sqlite3')
  def fail(*a):raise ReceiptJournalError('disk unavailable')
  monkeypatch.setattr(j,'started',fail)
  try:
   r=await tools.call('request_one','x.prepare_draft',{'goal':'Draft a local post.'})
   assert r['status']=='failed' and not r['result']['input_was_sent']
   bridge.execute.assert_not_called();writer.chat.assert_not_called()
  finally:j.close()
 asyncio.run(run())


def test_final_save_failure_preserves_result_and_forbids_replay(tmp_path,monkeypatch):
 async def run():
  tools,j,bridge,writer,_=setup(tmp_path/'tasks.sqlite3')
  def fail(*a):raise ReceiptJournalError('disk unavailable')
  monkeypatch.setattr(j,'finish',fail)
  try:
   r=await tools.call('request_one','x.prepare_draft',{'goal':'Draft a local post.'})
   assert r['status']=='needs_review' and r['result']['body']=='Local review draft.'
   assert not r['receipt_persisted'] and 'Never repeat' in r['instruction']
   second=await tools.call('request_one','x.prepare_draft',{'goal':'Draft a local post.'})
   assert second['status']=='needs_review';writer.chat.assert_awaited_once()
  finally:j.close()
 asyncio.run(run())


def test_stop_is_durable_even_when_provider_swallows_cancel(tmp_path):
 async def run():
  entered=asyncio.Event();release=asyncio.Event()
  async def slow(*a,**kw):
   entered.set()
   try:await release.wait()
   except asyncio.CancelledError:await release.wait()
   return '{"text":"Late body must not replace stopped receipt."}'
  tools,j,bridge,writer,_=setup(tmp_path/'tasks.sqlite3',writer=SimpleNamespace(model='test',chat=slow))
  try:
   await tools.call('stop_one','x.prepare_draft',{'goal':'Draft a local post.'},wait_seconds=0)
   await entered.wait();await tools.stop()
   assert j.get('stop_one')['state']=='stopped' and 'body' not in j.get('stop_one')['result']
   release.set();job=tools.calls['stop_one']['job'];await asyncio.wait({job},timeout=1);assert job.done()
   await tools.flush_history()
   assert tools.status('stop_one')['status']=='stopped' and j.get('stop_one')['state']=='stopped'
   assert 'body' not in j.get('stop_one')['result']
  finally:release.set();j.close()
 asyncio.run(run())


def test_history_is_read_only_and_paged(tmp_path):
 async def run():
  tools,j,bridge,writer,_=setup(tmp_path/'tasks.sqlite3')
  try:
   await tools.call('one','x.prepare_draft',{'goal':'Draft a local post.'})
   history=tools.history(1);assert history['total']==1 and history['items'][0]['has_draft']
   assert 'result' not in history['items'][0] and not history['automatic_replay']
   tools.status('one');tools.history(1);writer.chat.assert_awaited_once();bridge.execute.assert_not_called()
  finally:j.close()
 asyncio.run(run())


@pytest.mark.parametrize('commit_before_stop',[False,True])
def test_slow_final_disk_write_does_not_block_stop_or_publish_late_body(tmp_path,monkeypatch,commit_before_stop):
 import threading,time
 async def run():
  tools,j,bridge,writer,_=setup(tmp_path/'tasks.sqlite3')
  entered=threading.Event();release=threading.Event();finish=j.finish
  def slow(request_id,instance,state,result,elapsed):
   if state=='needs_review':
    value=finish(request_id,instance,state,result,elapsed) if commit_before_stop else None
    entered.set();release.wait(3)
    return value if commit_before_stop else finish(request_id,instance,state,result,elapsed)
   return finish(request_id,instance,state,result,elapsed)
  monkeypatch.setattr(j,'finish',slow)
  try:
   await tools.call('slow_disk','x.prepare_draft',{'goal':'Draft a local post.'},wait_seconds=0)
   assert await asyncio.to_thread(entered.wait,1)
   at=time.monotonic();await tools.stop();elapsed=time.monotonic()-at
   assert elapsed<.6
   assert j.get('slow_disk')['state']=='stopped' and 'body' not in j.get('slow_disk')['result']
   release.set();await tools.flush_history();await asyncio.sleep(.01)
   assert tools.status('slow_disk')['status']=='stopped'
   assert j.get('slow_disk')['state']=='stopped' and tools.status('slow_disk')['receipt_persisted']
  finally:
   release.set();await tools.flush_history();j.close()
 asyncio.run(run())


def test_saved_read_receipt_cannot_become_a_current_draft_source(tmp_path):
 async def run():
  j=ReceiptJournal(tmp_path/'tasks.sqlite3');j.reserve('old_read','old_boot','sig','public.read_results','device_read');j.started('old_read','old_boot')
  j.finish('old_read','old_boot','completed',{'verification_policy':'app_public_result_quotes','verification_passed':True,'source_kind':'verified_search_snapshot','results':[{'title_quote':'Saved public quote'}]},1)
  tools,j,bridge,writer,_=setup(tmp_path/'tasks.sqlite3',journal=j)
  try:
   assert tools.status('old_read')['historical']
   result=await tools.call('new_draft','x.prepare_draft',{'goal':'Draft a local post.','source_read_request_id':'old_read'})
   assert result['status']=='failed';writer.chat.assert_not_called();bridge.execute.assert_not_called()
  finally:j.close()
 asyncio.run(run())


def test_history_http_is_same_origin_bounded_and_has_no_action_route(tmp_path):
 from fastapi import FastAPI
 from fastapi.testclient import TestClient
 from hidagent.app_tools import router_for
 tools,j,bridge,writer,_=setup(tmp_path/'tasks.sqlite3')
 try:
  asyncio.run(tools.call('read_only_history','x.prepare_draft',{'goal':'Draft a local post.'}))
  app=FastAPI();app.include_router(router_for(tools))
  with TestClient(app) as client:
   page=client.get('/api/app-tools/history');assert page.status_code==200 and page.json()['total']==1
   assert client.get('/api/app-tools/status?request_id=read_only_history').json()['result']['body']=='Local review draft.'
   assert client.get('/api/app-tools/history?limit=0').status_code==422
   assert client.get('/api/app-tools/history?offset=-1').status_code==422
   assert client.get('/api/app-tools/history',headers={'Origin':'https://other.example'}).status_code==403
   assert client.post('/api/app-tools/history',json={}).status_code==405
  writer.chat.assert_awaited_once();bridge.execute.assert_not_called()
 finally:j.close()


def test_input_uncertain_terminal_is_durable_and_never_replayed_after_restart(tmp_path):
 async def run():
  path=tmp_path/'uncertain.sqlite3';tools,j,bridge,writer,state=setup(path)
  bridge.execute.return_value={'status':'failed','running':False,'input_was_sent':True,'never_retry_typing':True,
      'last_action':{'action':'error','reason':'Independent result check could not confirm input'}}
  try:
   receipt=await tools.call('uncertain_search','xiaohongshu.search',{'query':'IELTS'})
   assert receipt['status']=='input_uncertain'
   assert receipt['receipt_persisted'] and receipt['history_error'] is None
   saved=j.get('uncertain_search')
   assert saved['state']=='input_uncertain' and saved['result']['receipt']['never_retry_typing']
   assert not j.finish('uncertain_search',tools.server_instance,'completed',{'late':'not accepted'},1)
   assert bridge.execute.await_count==2  # skill + one general-agent fallback, never more
  finally:j.close()
  tools,j,bridge,writer,state=setup(path)
  try:
   receipt=await tools.call('uncertain_search','xiaohongshu.search',{'query':'IELTS'})
   assert receipt['historical'] and receipt['status']=='input_uncertain' and receipt['receipt_persisted']
   assert receipt['result']['receipt']['never_retry_typing']
   bridge.execute.assert_not_called();writer.chat.assert_not_called()
  finally:j.close()
 asyncio.run(run())


def test_device_timeout_after_input_keeps_uncertainty_markers_in_durable_receipt(tmp_path,monkeypatch):
 class Deadline:
  async def __aenter__(self):return self
  async def __aexit__(self,*args):raise TimeoutError()
 async def run():
  receipt={'status':'model_reported_done','task_id':'physical-task-1','running':False,
           'input_was_sent':True,'never_retry_typing':False}
  bridge=SimpleNamespace(exploration=None,execute=AsyncMock(return_value=receipt),snapshot=lambda **kw:{**receipt,'status':'stopped','never_retry_typing':True})
  tools,j,bridge,writer,state=setup(tmp_path/'deadline.sqlite3',bridge=bridge)
  monkeypatch.setattr('hidagent.app_tools.asyncio.timeout',lambda limit:Deadline())
  try:
   out=await tools.call('timed_input','xiaohongshu.search',{'query':'PTE'})
   assert out['status']=='input_uncertain'
   assert out['result']['input_was_sent'] and out['result']['never_retry_typing']
   assert out['receipt_persisted'] and j.get('timed_input')['state']=='input_uncertain'
   tools.stop_phone.assert_awaited_once()
   again=await tools.call('timed_input','xiaohongshu.search',{'query':'PTE'})
   assert again['result']['never_retry_typing'];bridge.execute.assert_awaited_once()
  finally:j.close()
 asyncio.run(run())


@pytest.mark.parametrize('sent,owned,expected',[(True,True,True),(False,True,False),(False,False,None)])
def test_explicit_stop_preserves_task_bound_delivery_and_no_retry(tmp_path,sent,owned,expected):
 async def run():
  entered=asyncio.Event()
  async def execute(*a,**kw):
   entered.set();return {'status':'in_progress','task_id':'owned-task','running':True,'input_was_sent':False}
  snapshot={'status':'stopped','task_id':'owned-task' if owned else 'other-task','running':False,
            'input_was_sent':sent if owned else True,'never_retry_typing':sent if owned else True}
  bridge=SimpleNamespace(exploration=None,execute=execute,snapshot=lambda **kw:snapshot)
  tools,j,bridge,writer,state=setup(tmp_path/'stop-delivery.sqlite3',bridge=bridge)
  try:
   await tools.call('stop-delivery','xiaohongshu.search',{'query':'IELTS'},wait_seconds=0)
   await entered.wait();await tools.stop();await tools.flush_history()
   receipt=tools.status('stop-delivery')
   assert receipt['status']=='stopped'
   assert receipt['result']['input_was_sent'] is expected
   assert receipt['result']['never_retry_typing'] is (expected is not False)
   assert receipt['result']['task_id']=='owned-task'
   saved=j.get('stop-delivery');assert saved['state']=='stopped'
   assert saved['result']['never_retry_typing'] is (expected is not False)
   assert 'other-task' not in json.dumps(saved)
  finally:j.close()
 asyncio.run(run())


def test_stop_during_dispatch_without_task_id_remains_unknown_not_unsent(tmp_path):
 async def run():
  entered=asyncio.Event()
  async def execute(*a,**kw):entered.set();await asyncio.Event().wait()
  bridge=SimpleNamespace(exploration=None,execute=execute,snapshot=lambda **kw:{'task_id':'unrelated','input_was_sent':False,'running':False})
  tools,j,bridge,writer,state=setup(tmp_path/'stop-dispatch.sqlite3',bridge=bridge)
  try:
   await tools.call('stop-dispatch','xiaohongshu.search',{'query':'IELTS'},wait_seconds=0)
   await entered.wait();await tools.stop();await tools.flush_history()
   result=tools.status('stop-dispatch')['result']
   assert result['input_was_sent'] is None and result['never_retry_typing']
   assert 'unrelated' not in json.dumps(j.get('stop-dispatch'))
  finally:j.close()
 asyncio.run(run())


def test_missing_dispatch_identity_cannot_later_adopt_an_unrelated_task(tmp_path):
 tools,j,bridge,writer,state=setup(tmp_path/'no-adoption.sqlite3')
 try:
  entry={'name':'xiaohongshu.search','result':{},'_physical_started':True}
  tools._remember_device_receipt(entry,{'task_id':None,'input_was_sent':False})
  tools._remember_device_receipt(entry,{'task_id':'foreign','input_was_sent':True,'goal':'private extra context'})
  assert entry['_device_task_id'] is None and not entry['_last_device_receipt']['input_was_sent']
  flags=tools._interruption_flags(entry)
  assert flags['input_was_sent'] is None and flags['never_retry_typing']
  assert 'foreign' not in json.dumps(entry) and 'private extra context' not in json.dumps(entry)
 finally:j.close()


def test_known_input_uncertainty_is_monotonic_when_later_snapshot_is_empty(tmp_path):
 tools,j,bridge,writer,state=setup(tmp_path/'monotonic.sqlite3')
 try:
  entry={'name':'xiaohongshu.search','result':{'input_was_sent':True,'never_retry_typing':True},'_physical_started':True}
  flags=tools._interruption_flags(entry)
  assert flags['input_was_sent'] is True and flags['never_retry_typing']
  tools._remember_device_receipt(entry,{'task_id':'owned','running':False,'input_was_sent':False})
  assert tools._interruption_flags(entry)['input_was_sent'] is True
 finally:j.close()
