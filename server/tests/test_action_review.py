from types import SimpleNamespace
from unittest.mock import patch
import time,pytest
from hidagent.action_review import prepare_reply,completion_notice,verify_visible_account
from hidagent.decision import DecisionUnavailable
J=SimpleNamespace(done=lambda:True,cancelled=lambda:False)
def tools():return SimpleNamespace(calls={'source111':{'name':'grok.read_action','status':'completed','job':J,'result':{'verification_passed':True,'workspace':'Example Action Workspace','source_kind':'verified_dedicated_action_snapshot','reply_text':'A short exact prepared reply.','card_id':'card6','frame_sha256':'a'*64,'target_handle':'@learner','target_post_url':None}},'account11':{'name':'x.verify_account','status':'completed','job':J,'result':{'account_verified':True,'account':'@example_creator','observed_at':time.time()}}})
def test_preparation_copies_exact_source_and_never_publishes():
 t=tools();r=prepare_reply(t,'source111','account11');assert r['body']==t.calls['source111']['result']['reply_text']
 assert not r['published'] and not r['acknowledged'] and not r['target_verified'] and r['requires_target_context_check']
@pytest.mark.parametrize('mutation',[('source111','status','failed'),('source111','name','public.read_results'),('account11','status','failed')])
def test_failed_wrong_kind_source_cannot_become_comment(mutation):
 t=tools();t.calls[mutation[0]][mutation[1]]=mutation[2]
 with pytest.raises(DecisionUnavailable):prepare_reply(t,'source111','account11')
@pytest.mark.parametrize('field,value',[('account','@example_owner'),('account_verified',False),('observed_at',0)])
def test_wrong_or_expired_account_never_prepares(field,value):
 t=tools();t.calls['account11']['result'][field]=value
 with pytest.raises(DecisionUnavailable):prepare_reply(t,'source111','account11')
@pytest.mark.parametrize('reply',['x'*281,'hello\u2014world'])
def test_no_silent_truncation_or_rewriting(reply):
 t=tools();t.calls['source111']['result']['reply_text']=reply
 with pytest.raises(DecisionUnavailable):prepare_reply(t,'source111','account11')
def proof(item):return {'item_id':item,'state':'visibly_verified_published','account':'@example_creator','delivery_uncertain':False,'reply_permalink':'https://x.com/example_creator/status/123456789','verification_frame_sha256':'a'*64}
def test_all_published_notice_uses_the_human_configured_ai_name():
 notice=completion_notice(['1','2'],[proof('1'),proof('2')],'Nova')
 assert 'Nova' in notice and 'AI controlling this iPad' in notice and 'All 2' in notice
@pytest.mark.parametrize('receipts',[[],[proof('1')],[proof('1'),proof('1')],[{**proof('1'),'delivery_uncertain':True},proof('2')],[{**proof('1'),'account':'@other'},proof('2')],[{**proof('1'),'state':'model_reported_done'},proof('2')]])
def test_incomplete_duplicate_wrong_account_or_uncertain_never_acknowledged(receipts):
 with pytest.raises(DecisionUnavailable):completion_notice(['1','2'],receipts,'Nova')
def test_account_check_no_avatar_inference_or_model_call():
 a=SimpleNamespace(running=False,phone=SimpleNamespace(calibrated=True,target_info=lambda:{'device':'ipad','orientation':'portrait'},device=SimpleNamespace(connected=True,links=1)),screen=SimpleNamespace(frame=b'frame',live=True))
 with patch('hidagent.action_review.public_search_control',return_value=None):
  with pytest.raises(DecisionUnavailable):verify_visible_account(a,lambda:None)
 with patch('hidagent.action_review.public_search_control',return_value={'template':'joe-profile.json'}):
  assert verify_visible_account(a,lambda:None)['account']=='@example_creator'

def test_metadata_only_prepares_manifest_without_inventing_a_body():
 t=tools();t.calls['source111']['result']['reply_text']=None;r=prepare_reply(t,'source111','account11')
 assert r['body'] is None and r['status']=='needs_native_copy' and r['requires_native_copy'] and not r['source_verbatim'] and not r['published']
