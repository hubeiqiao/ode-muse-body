import asyncio,json
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from fastapi import HTTPException
from hidagent.app_tools import AppTools


async def fixture():
 state={'device':False,'screen':False,'calibrated':True,'agent':{'running':False},'controller':{'stable':False}}
 bridge=SimpleNamespace(exploration=None,execute=AsyncMock())
 writer=SimpleNamespace(model='haiku-test',chat=AsyncMock(return_value=json.dumps({'text':'Could a daily speaking habit help? A draft idea for review.'})))
 memory=SimpleNamespace(snapshot=lambda:{'preferences':[],'discoveries':[]})
 tools=AppTools(bridge,writer,memory,lambda:state,AsyncMock())
 done=asyncio.get_running_loop().create_future();done.set_result(None)
 quote={'title_quote':'Practice speaking every day.','byline_quote':'Public channel','box':{'left':.1,'right':.8,'top':.2,'bottom':.3}}
 tools.calls['search_test_001']={'name':'x.search','status':'completed','job':done,'search_query':'IELTS','result':{'receipt':{'task_id':'42'}}}
 tools.calls['read_test_001']={'name':'public.read_results','status':'completed','job':done,'result':{
  'source_kind':'verified_search_snapshot','verification_policy':'app_public_result_quotes','verification_passed':True,'visual_evidence_contract':'boxed_quote_byline_v2',
  'source_request_id':'search_test_001','source_task_id':'42','frame_sha256':'a'*64,'app':'X','query':'IELTS',
  'source_completed_at':123,'results':[quote]}}
 return tools,bridge,writer


def test_explicit_source_is_historical_local_review_not_memory_or_publication():
 async def run():
  t,b,w=await fixture();r=await t.call('draft_test_001','x.prepare_draft',{'goal':'Use this checked excerpt for a tentative idea.','source_read_request_id':'read_test_001'})
  assert r['status']=='needs_review'
  result=r['result'];source=result['source_evidence']
  assert source['source_read_request_id']=='read_test_001' and source['frame_sha256']=='a'*64
  assert source['historical_snapshot'] and source['customer_feedback_verified'] is False
  assert not result['published'] and not result['memory_written'] and not result['interpretation_verified']
  context=json.loads(w.chat.call_args.args[1]);assert context['explicit_checked_app_source']==source
  assert len(w.chat.call_args.args)==2 and 'image' not in w.chat.call_args.kwargs
  b.execute.assert_not_awaited()
 asyncio.run(run())


def test_without_explicit_source_does_not_attach_latest_search_automatically():
 async def run():
  t,b,w=await fixture();r=await t.call('draft_test_002','x.prepare_draft',{'goal':'Draft an unrelated idea.'})
  assert r['status']=='needs_review' and 'source_evidence' not in r['result']
  assert 'explicit_checked_app_source' not in json.loads(w.chat.call_args.args[1])
 asyncio.run(run())


@pytest.mark.parametrize('bad',['missing','failed','wrong_tool','wrong_policy','not_verified','wrong_parent','parent_failed','wrong_app','wrong_query','wrong_task','bad_sha','too_long','bad_box','personal_app'])
def test_unusable_source_rejected_before_model_or_device(bad):
 async def run():
  t,b,w=await fixture();read=t.calls['read_test_001'];e=read['result'];parent=t.calls['search_test_001'];rid='read_test_001'
  if bad=='missing':rid='missing_source'
  if bad=='failed':read['status']='failed'
  if bad=='wrong_tool':read['name']='x.prepare_draft'
  if bad=='wrong_policy':e['verification_policy']='old_policy'
  if bad=='not_verified':e['verification_passed']=False
  if bad=='wrong_parent':e['source_request_id']='missing_search'
  if bad=='parent_failed':parent['status']='failed'
  if bad=='wrong_app':e['app']='Instagram'
  if bad=='wrong_query':e['query']='other'
  if bad=='wrong_task':e['source_task_id']='old'
  if bad=='bad_sha':e['frame_sha256']='invented'
  if bad=='too_long':e['results'][0]['title_quote']=' '.join(['word']*26)
  if bad=='bad_box':e['results'][0]['box']['left']=.9
  if bad=='personal_app':parent['name']='threads.search';e['app']='Threads'
  r=await t.call('draft_bad_001','x.prepare_draft',{'goal':'Draft an idea.','source_read_request_id':rid})
  assert r['status']=='failed' and 'body' not in r['result']
  w.chat.assert_not_awaited();b.execute.assert_not_awaited()
 asyncio.run(run())


def test_source_change_during_generation_suppresses_draft():
 async def run():
  t,b,w=await fixture()
  async def changed(*args,**kw):
   t.calls['read_test_001']['result']['results'][0]['title_quote']='Changed source'
   return json.dumps({'text':'An otherwise valid draft.'})
  w.chat=AsyncMock(side_effect=changed)
  r=await t.call('draft_changed_001','x.prepare_draft',{'goal':'Draft an idea.','source_read_request_id':'read_test_001'})
  assert r['status']=='failed' and 'body' not in r['result']
 asyncio.run(run())


def test_source_identity_participates_in_request_deduplication():
 async def run():
  t,b,w=await fixture();args={'goal':'Draft an idea.','source_read_request_id':'read_test_001'}
  await t.call('draft_same_001','x.prepare_draft',args)
  await t.call('draft_same_001','x.prepare_draft',args)
  w.chat.assert_awaited_once()
  with pytest.raises(HTTPException):await t.call('draft_same_001','x.prepare_draft',{'goal':'Draft an idea.'})
 asyncio.run(run())

def test_legacy_global_only_verdict_cannot_become_new_grounded_draft():
 from hidagent.decision import DecisionUnavailable
 async def run():
  t,b,w=await fixture();t.calls['read_test_001']['result'].pop('visual_evidence_contract')
  with pytest.raises(DecisionUnavailable):t.draft_source('read_test_001')
  w.chat.assert_not_awaited();b.execute.assert_not_awaited()
 asyncio.run(run())
