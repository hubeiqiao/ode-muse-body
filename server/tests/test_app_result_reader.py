import asyncio,json,io
from PIL import Image
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from hidagent.app_result_reader import AppResultReader
from hidagent.decision import DecisionUnavailable

def image():
 b=io.BytesIO();Image.new('RGB',(710,1080),'white').save(b,format='PNG');return b.getvalue()

def fixture():
 data={'results':[{'title_quote':'IELTS speaking practice','byline_quote':'Practice channel','byline_box':{'left':.1,'right':.5,'top':.32,'bottom':.36},'box':{'left':.1,'right':.7,'top':.2,'bottom':.3}}]}
 a=SimpleNamespace(running=False,task='APP_PUBLIC_SEARCH '+json.dumps({'app':'YouTube','query':'IELTS'}),
  steps=[{'t':123},{'t':130,'action':'done','_verified_public_results':True}],shots={1:image()},
  actor=SimpleNamespace(model='writer-test',chat=AsyncMock(return_value=json.dumps(data))),
  client=SimpleNamespace(evaluate=AsyncMock(return_value={'model':'judge-test','answers':{'ready':{'noul':1},'blocked':{'noul':0},'commercial':{'noul':0},'quote_bound':{'noul':1},'byline_bound':{'noul':1},'associated':{'noul':1}}})),trace=Mock())
 source={'app':'YouTube','query':'IELTS','task_id':'123','request_id':'req-1'}
 return a,source,data

def test_result_provenance_is_snapshot_not_memory_or_full_post():
 a,s,_=fixture();r=asyncio.run(AppResultReader(a).read(s,lambda:None))
 assert r['source_kind']=='verified_search_snapshot' and r['source_task_id']=='123'
 assert r['source_request_id']=='req-1' and len(r['frame_sha256'])==64
 assert r['memory_written'] is False and r['account_verified'] is False
 assert 'Practice channel' in r['summary']
 assert a.actor.chat.call_args.args[2]==a.shots[1]
 assert a.client.evaluate.call_args.args[2]!=a.shots[1]
 assert a.client.evaluate.call_args.args[0]['visual_evidence_contract']=='boxed_quote_byline_v2'

@pytest.mark.parametrize('bad',['task','query','app','running','no_final','no_frame'])
def test_no_paid_read_when_search_evidence_is_missing_or_wrong(bad):
 a,s,_=fixture()
 if bad=='task':s['task_id']='old'
 if bad=='query':s['query']='other'
 if bad=='app':s['app']='X'
 if bad=='running':a.running=True
 if bad=='no_final':a.steps[-1].pop('_verified_public_results')
 if bad=='no_frame':a.shots={}
 with pytest.raises(DecisionUnavailable):asyncio.run(AppResultReader(a).read(s,lambda:None))
 a.actor.chat.assert_not_awaited();a.client.evaluate.assert_not_awaited()

@pytest.mark.parametrize('bad',['empty','extra','reversed_box','long_quote','blocked','unsupported'])
def test_bad_or_unsupported_result_never_becomes_verified_evidence(bad):
 a,s,d=fixture()
 if bad=='empty':d['results']=[]
 if bad=='extra':d['publish']='now'
 if bad=='reversed_box':d['results'][0]['box']['left']=.9
 if bad=='long_quote':d['results'][0]['title_quote']='a'*181
 if bad=='blocked':a.client.evaluate.return_value['answers']['blocked']['noul']=1
 if bad=='unsupported':a.client.evaluate.return_value['answers']['ready']['noul']=.98
 a.actor.chat.return_value=json.dumps(d)
 with pytest.raises(DecisionUnavailable):asyncio.run(AppResultReader(a).read(s,lambda:None))
 assert all(c.kwargs.get('kind')!='app-results-read' for c in a.trace.write.call_args_list)
 assert a.trace.write.call_args.kwargs['outcome']=='rejected'

def test_stop_during_extraction_cannot_emit_late_results():
 a,s,_=fixture();stopped=False
 async def writer(*args,**kw):
  nonlocal stopped
  stopped=True;return json.dumps({'results':[]})
 a.actor.chat=writer
 def active():
  if stopped:raise asyncio.CancelledError()
 with pytest.raises(asyncio.CancelledError):asyncio.run(AppResultReader(a).read(s,active))
 a.client.evaluate.assert_not_awaited();a.trace.write.assert_not_called()

def test_changed_task_after_verification_discards_result():
 a,s,_=fixture()
 async def judge(*args,**kw):a.steps[0]['t']=999;return {'model':'judge','answers':{'ready':{'noul':1},'blocked':{'noul':0},'commercial':{'noul':0},'quote_bound':{'noul':1},'byline_bound':{'noul':1},'associated':{'noul':1}}}
 a.client.evaluate=judge
 with pytest.raises(DecisionUnavailable):asyncio.run(AppResultReader(a).read(s,lambda:None))
 a.trace.write.assert_not_called()


@pytest.mark.parametrize('text,units',[("It's useful",2),("IELTS-speaking feedback",3),("口语练习",4),("IELTS 口语",3),("IELTS口语practice",4)])
def test_excerpt_budget_counts_words_and_cjk_conservatively(text,units):
 from hidagent.app_result_reader import quote_units
 assert quote_units(text)==units


def test_aggregate_budget_rejects_across_cards_before_paid_verification():
 a,s,d=fixture()
 d['results']=[dict(d['results'][0],title_quote=' '.join(['word']*12),byline_quote='author') for _ in range(2)]
 a.actor.chat.return_value=json.dumps(d)
 with pytest.raises(DecisionUnavailable):asyncio.run(AppResultReader(a).read(s,lambda:None))
 a.client.evaluate.assert_not_awaited()
 assert a.trace.write.call_args.kwargs['reason']=='quote_budget'
 assert a.trace.write.call_args.kwargs['verified_quote'] is False


def test_text_post_excerpt_keeps_exact_source_and_does_not_claim_customer_feedback():
 a,s,d=fixture();s['app']='X'
 a.task='APP_PUBLIC_SEARCH '+json.dumps({'app':'X','query':'IELTS'})
 d['results'][0]['title_quote']='I need help with IELTS speaking'
 a.actor.chat.return_value=json.dumps(d)
 result=asyncio.run(AppResultReader(a).read(s,lambda:None))
 assert result['results'][0]['title_quote']==d['results'][0]['title_quote']
 assert result['quote_units']==8 and not result['memory_written']
 assert 'Joe Speaking customer feedback' in result['scope']
 assert 'CONTIGUOUS' in a.actor.chat.call_args.args[0]


@pytest.mark.parametrize('commercial',[1,.5,None])
def test_ad_disclosure_outside_quote_or_missing_check_cannot_become_evidence(commercial):
 a,s,_=fixture()
 if commercial is None:a.client.evaluate.return_value['answers'].pop('commercial')
 else:a.client.evaluate.return_value['answers']['commercial']['noul']=commercial
 with pytest.raises(DecisionUnavailable):asyncio.run(AppResultReader(a).read(s,lambda:None))
 assert a.trace.write.call_args.kwargs['outcome']=='rejected'
 assert all(c.kwargs.get('kind')!='app-results-read' for c in a.trace.write.call_args_list)
 assert a.client.evaluate.call_args.args[0]['policy']=='app_public_result_quotes'
 assert 'WHOLE associated' in a.client.evaluate.call_args.args[1]['commercial']['instructions']


def test_jev_quote_policy_requires_explicit_noncommercial_check_for_fallback():
 from hidagent.vision_judge import JudgeFallback
 state={'policy':'app_public_result_quotes'};q={'ready':{},'blocked':{},'commercial':{}}
 good={'ready':{'noul':1},'blocked':{'noul':0},'commercial':{'noul':0},'quote_bound':{'noul':1},'byline_bound':{'noul':1},'associated':{'noul':1}}
 assert JudgeFallback._approved(state,q,good)
 for v in [1,.5]:
  assert not JudgeFallback._approved(state,q,{**good,'commercial':{'noul':v}})
 assert not JudgeFallback._approved(state,q,{k:v for k,v in good.items() if k!='commercial'})


@pytest.mark.parametrize('field,value',[('ready',float('nan')),('blocked',float('nan')),('commercial',float('nan')),('commercial',float('inf')),('commercial',False),('ready',True),('blocked','0'),('commercial',-1)])
def test_invalid_verdict_is_rejected_with_sanitized_diagnostics(field,value):
 a,s,_=fixture();a.client.evaluate.return_value['answers'][field]['noul']=value
 with pytest.raises(DecisionUnavailable):asyncio.run(AppResultReader(a).read(s,lambda:None))
 record=a.trace.write.call_args.kwargs
 assert record['kind']=='app-results-check' and record['reason']=='invalid_verdict'
 assert field not in record['probabilities'] and not record['verified_quote']
 assert 'proposed_results' not in record and 'raw' not in record
 assert record['source_request_id']=='req-1' and len(record['frame_sha256'])==64


def test_success_has_nonoverlapping_model_stages_and_separate_verification_event():
 a,s,_=fixture();result=asyncio.run(AppResultReader(a).read(s,lambda:None))
 assert result['extraction_s']>=0 and result['verification_s']>=0
 assert result['extraction_s']+result['verification_s']<=result['read_s']+.002
 checks=[c.kwargs for c in a.trace.write.call_args_list if c.kwargs['kind']=='app-results-check']
 assert len(checks)==1 and checks[0]['reason']=='accepted'
 assert checks[0]['probabilities']=={'ready':1,'blocked':0,'commercial':0,'quote_bound':1,'byline_bound':1,'associated':1}
 assert checks[0]['verified_quote'] is False
 assert a.trace.write.call_args.kwargs['kind']=='app-results-read'


def test_empty_provider_response_is_diagnostic_not_an_empty_verified_result():
 a,s,_=fixture();a.actor.chat.return_value=''
 with pytest.raises(DecisionUnavailable):asyncio.run(AppResultReader(a).read(s,lambda:None))
 a.client.evaluate.assert_not_awaited()
 assert a.trace.write.call_args.kwargs['reason']=='empty_response'
 assert a.trace.write.call_args.kwargs['stage']=='extraction'


def test_chrome_reddit_read_excludes_existing_tab_strip():
 import io,hashlib
 from PIL import Image
 from hidagent.reddit_web import page_band
 a,s,_=fixture();a.task='APP_PUBLIC_SEARCH '+json.dumps({'app':'Reddit','query':'IELTS'});s['app']='Reddit'
 a.steps[-1]['execution_route']='chrome_reddit_web'
 im=Image.new('RGB',(710,1080),'white');b=io.BytesIO();im.save(b,format='PNG');a.shots[1]=b.getvalue()
 r=asyncio.run(AppResultReader(a).read(s,lambda:None));cropped=page_band(b.getvalue())
 assert a.actor.chat.call_args.args[2]==cropped and a.client.evaluate.call_args.args[2]!=cropped
 assert r['frame_sha256']==hashlib.sha256(cropped).hexdigest()
 assert 'Chrome public website' in r['summary']


def test_native_result_extractor_uses_strict_output_shape_in_real_reader():
 a,s,_=fixture();a.actor.supports_native_json=True
 asyncio.run(AppResultReader(a).read(s,lambda:None))
 schema=a.actor.chat.call_args.kwargs['json_schema']
 assert schema['additionalProperties'] is False and schema['required']==['results']
 card=schema['properties']['results']['items']
 assert set(card['required'])=={'title_quote','byline_quote','box','byline_box'} and card['additionalProperties'] is False
 assert set(card['properties']['box']['required'])=={'left','right','top','bottom'}
 assert a.client.evaluate.await_count==1


def test_ordinary_result_provider_does_not_receive_unsupported_schema_keyword():
 a,s,_=fixture();asyncio.run(AppResultReader(a).read(s,lambda:None))
 assert 'json_schema' not in a.actor.chat.call_args.kwargs


def test_explicit_no_visible_results_is_not_mislabelled_as_bad_json():
 a,s,_=fixture();a.actor.supports_native_json=True;a.actor.chat.return_value='{"results":[]}'
 with pytest.raises(DecisionUnavailable):asyncio.run(AppResultReader(a).read(s,lambda:None))
 assert a.trace.write.call_args.kwargs['reason']=='no_visible_result'
 a.client.evaluate.assert_not_awaited()


def test_native_extraction_requests_only_one_card_to_share_quote_budget():
 a,s,_=fixture();a.actor.supports_native_json=True
 asyncio.run(AppResultReader(a).read(s,lambda:None))
 assert a.actor.chat.call_args.kwargs['json_schema']['properties']['results']['maxItems']==1
 assert 'at most ONE object' in a.actor.chat.call_args.args[0]


def test_quote_budget_rejection_records_only_bounded_counts_not_candidate_text():
 a,s,d=fixture();d['results'][0]['title_quote']=' '.join(['word']*25)
 a.actor.chat.return_value=json.dumps(d)
 with pytest.raises(DecisionUnavailable):asyncio.run(AppResultReader(a).read(s,lambda:None))
 diag=a.trace.write.call_args.kwargs
 assert diag['reason']=='quote_budget' and diag['proposed_result_count']==1 and diag['quote_units_total']==27
 assert 'results' not in diag and 'raw' not in diag
 a.client.evaluate.assert_not_awaited()

@pytest.mark.parametrize('field',['quote_bound','byline_bound','associated'])
def test_global_agreement_cannot_override_failed_exact_crop_binding(field):
 a,s,_=fixture();a.client.evaluate.return_value['answers'][field]['noul']=0
 with pytest.raises(DecisionUnavailable):asyncio.run(AppResultReader(a).read(s,lambda:None))
 assert a.trace.write.call_args.kwargs['reason']=='unbound_evidence'
 assert not any(c.kwargs.get('kind')=='app-results-read' for c in a.trace.write.call_args_list)

def test_unlocated_nonempty_author_rejects_before_paid_verification():
 a,s,d=fixture();d['results'][0].pop('byline_box');a.actor.chat.return_value=json.dumps(d)
 with pytest.raises(DecisionUnavailable):asyncio.run(AppResultReader(a).read(s,lambda:None))
 a.client.evaluate.assert_not_awaited()
 assert a.trace.write.call_args.kwargs['reason']=='missing_byline_evidence'

def test_empty_author_is_explicitly_unattributed_not_inferred():
 a,s,d=fixture();d['results'][0].update(byline_quote='',byline_box=None);a.actor.chat.return_value=json.dumps(d)
 r=asyncio.run(AppResultReader(a).read(s,lambda:None))
 assert r['results'][0]['byline_quote']=='' and r['visual_evidence_contract']=='boxed_quote_byline_v2'
 assert 'byline_box' not in r['results'][0]

def test_original_privacy_check_precedes_proof_image_composition():
 from unittest.mock import patch
 a,s,_=fixture()
 with patch('hidagent.app_result_reader.check_cloud_frame',side_effect=DecisionUnavailable('excluded')),pytest.raises(DecisionUnavailable):
  asyncio.run(AppResultReader(a).read(s,lambda:None))
 a.client.evaluate.assert_not_awaited()

def test_proof_canvas_never_renders_proposed_text_as_visual_evidence():
 from hidagent.app_result_reader import verification_image,ExtractedResult
 a,s,d=fixture();item=ExtractedResult.model_validate(d['results'][0]);before,_=verification_image(a.shots[1],[item]);item.title_quote='INVENTED NEVER ON THE SCREEN';item.byline_quote='INVENTED AUTHOR'
 after,_=verification_image(a.shots[1],[item]);assert before==after

def test_primary_cannot_accept_legacy_only_answers_for_crop_contract():
 from hidagent.vision_judge import JudgeFallback
 a={'ready':{'noul':1},'blocked':{'noul':0},'commercial':{'noul':0}}
 assert not JudgeFallback._approved({'policy':'app_public_result_quotes','visual_evidence_contract':'boxed_quote_byline_v2'},{},a)
