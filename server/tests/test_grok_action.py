import asyncio,io,json
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
import pytest
from PIL import Image
from pydantic import ValidationError
from hidagent.grok_action import ActionCard,GrokActionReader
from hidagent.decision import DecisionUnavailable
from hidagent.vision_judge import JudgeFallback
CARD={'card_id':'20261027-06','target_handle':'@learner_qa','target_post_url':None,'reply_text':'Practice one answer. https://x.com/example_creator/status/1234567890'}
def fake(ready=1,blocked=0):
 b=io.BytesIO();Image.new('RGB',(710,1080),'white').save(b,format='PNG')
 target={'device':'ipad','orientation':'portrait','calibration_key':'ipad-portrait-66'}
 return SimpleNamespace(running=False,phone=SimpleNamespace(target_info=lambda:target,device=SimpleNamespace(links=1,connected=True),calibrated=True),screen=SimpleNamespace(frame=b.getvalue(),live=True),actor=SimpleNamespace(model='test',chat=AsyncMock(return_value=json.dumps(CARD))),client=SimpleNamespace(evaluate=AsyncMock(return_value={'answers':{'ready':{'noul':ready},'blocked':{'noul':blocked}},'model':'independent','seconds':.1})))
def test_exact_dedicated_card_read_no_hid_or_message():
 a=fake()
 with patch('hidagent.grok_action.public_search_control',return_value={'template':'joe-action.json'}):r=asyncio.run(GrokActionReader(a).read(lambda:None,include_text=True))
 assert r['reply_text']==CARD['reply_text'] and r['target_post_url'] is None and r['target_navigation_required']
 assert not r['published'] and not r['acknowledged'] and r['verification_passed']
 a.client.evaluate.assert_awaited_once()

def test_wrong_workspace_rejects_before_any_model():
 a=fake()
 with patch('hidagent.grok_action.public_search_control',return_value=None):
  with pytest.raises(DecisionUnavailable):asyncio.run(GrokActionReader(a).read(lambda:None))
 a.actor.chat.assert_not_awaited();a.client.evaluate.assert_not_awaited()

@pytest.mark.parametrize('ready,blocked',[(.98,0),(1,.02),(True,0),(1,float('nan')),(1,None)])
def test_independent_rejection_is_not_returned_as_prepared_reply(ready,blocked):
 a=fake(ready,blocked)
 with patch('hidagent.grok_action.public_search_control',return_value={'template':'joe-action.json'}):
  with pytest.raises(DecisionUnavailable):asyncio.run(GrokActionReader(a).read(lambda:None))

def test_stop_after_extraction_discards_card_before_verifier():
 a=fake();calls=0
 def active():
  nonlocal calls
  calls+=1
  if calls>=2:raise asyncio.CancelledError()
 with patch('hidagent.grok_action.public_search_control',return_value={'template':'joe-action.json'}):
  with pytest.raises(asyncio.CancelledError):asyncio.run(GrokActionReader(a).read(active))
 a.client.evaluate.assert_not_awaited()

@pytest.mark.parametrize('field,value',[('target_handle','@bad space'),('target_post_url','https://evil.test/post'),('reply_text',''),('publish',True)])
def test_closed_schema_no_action_injection(field,value):
 with pytest.raises(ValidationError):ActionCard.model_validate({**CARD,field:value})

def test_dedicated_policy_keeps_strict_thresholds():
 q={'ready':{},'blocked':{}}
 assert JudgeFallback._approved({'policy':'grok_action_card'},q,{'ready':{'noul':1},'blocked':{'noul':0}})
 assert not JudgeFallback._approved({'policy':'grok_action_card'},q,{'ready':{'noul':.98},'blocked':{'noul':0}})

def test_source_change_after_extraction_rejects_before_verification():
 a=fake()
 async def change(*args,**kw):a.phone.device.links=2;return json.dumps(CARD)
 a.actor.chat=AsyncMock(side_effect=change)
 with patch('hidagent.grok_action.public_search_control',return_value={'template':'joe-action.json'}):
  with pytest.raises(DecisionUnavailable):asyncio.run(GrokActionReader(a).read(lambda:None))
 a.client.evaluate.assert_not_awaited()

def test_invalid_json_has_safe_field_diagnostic_not_secondary_index_error():
 a=fake();a.actor.chat=AsyncMock(return_value='not json')
 with patch('hidagent.grok_action.public_search_control',return_value={'template':'joe-action.json'}):
  with pytest.raises(DecisionUnavailable,match='json:json_invalid'):asyncio.run(GrokActionReader(a).read(lambda:None))
 a.client.evaluate.assert_not_awaited()

def test_metadata_default_never_supplies_visual_transcription_as_outgoing_copy():
 a=fake()
 with patch('hidagent.grok_action.public_search_control',return_value={'template':'joe-action.json'}):r=asyncio.run(GrokActionReader(a).read(lambda:None))
 assert r['reply_text'] is None and r['read_mode']=='metadata_only' and r['requires_native_copy'] and not r['byte_exact_copy_verified']
 assert a.actor.chat.await_args.kwargs['max_tokens']==500
 assert 'reply_text MUST be null' in a.actor.chat.await_args.args[0]
 assert a.client.evaluate.await_args.args[0]['proposed_card']['reply_text'] is None

def test_review_transcription_is_never_byte_exact_or_outgoing_copy():
 a=fake()
 with patch('hidagent.grok_action.public_search_control',return_value={'template':'joe-action.json'}):r=asyncio.run(GrokActionReader(a).read(lambda:None,include_text=True))
 assert r['reply_text']==CARD['reply_text'] and not r['reply_verbatim_verified'] and not r['byte_exact_copy_verified'] and r['requires_native_copy']
