import json
import asyncio
import httpx
import pytest
from hidagent.meta_model import meta_llm


def test_meta_never_falls_back_to_other_provider_credentials(monkeypatch):
    monkeypatch.delenv('META_API_KEY', raising=False)
    monkeypatch.delenv('MODEL_API_KEY', raising=False)
    monkeypatch.setenv('LLM_API_KEY', 'other-provider-secret')
    with pytest.raises(RuntimeError, match='no provider was substituted'):
        meta_llm()


def test_meta_uses_explicit_key_and_compatible_vision_wire_format(monkeypatch):
    monkeypatch.setenv('META_API_KEY', 'meta-test-key')
    monkeypatch.setenv('LLM_API_KEY', 'other-provider-secret')
    async def run():
        client=meta_llm()
        async def handler(request):
            import json
            body=json.loads(request.content)
            assert str(request.url)=='https://api.meta.ai/v1/chat/completions'
            assert request.headers['authorization']=='Bearer meta-test-key'
            assert 'other-provider-secret' not in request.content.decode()
            assert body['model']=='muse-spark-1.3'
            assert body['max_completion_tokens']==2048
            assert body['reasoning_effort']=='minimal'
            assert body['messages'][1]['content'][0]['type']=='image_url'
            return httpx.Response(200,json={'choices':[{'message':{'content':'DO: done'}}]})
        await client._http.aclose()
        client._http=httpx.AsyncClient(base_url='https://api.meta.ai/v1',headers={'Authorization':'Bearer meta-test-key'},transport=httpx.MockTransport(handler))
        try: assert await client.chat('Observe only','Check screenshot', b'test-jpeg', max_tokens=60)=='DO: done'
        finally: await client._http.aclose()
    asyncio.run(run())


def test_local_meta_never_uses_cloud_keys_or_proxy(monkeypatch):
    monkeypatch.setenv('AGENT_ENGINE','meta_local_hybrid')
    monkeypatch.setenv('META_API_KEY','cloud-meta-secret')
    monkeypatch.setenv('LLM_API_KEY','anthropic-secret')
    async def run():
        client=meta_llm()
        try:
            assert str(client._http.base_url)=='http://127.0.0.1:11434/v1/'
            assert 'authorization' not in client._http.headers
            assert client._http._trust_env is False
            assert client.model=='muse-glimmer:30b'
            assert client.supports_native_json is True
            assert client.native_url=='http://127.0.0.1:11434/api/chat'
        finally: await client._http.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('url',['https://evil.example/v1','http://192.0.2.10:11434/v1','http://user:secret@127.0.0.1:11434/v1','http://127.0.0.1:11434/v1?key=secret'])
def test_local_meta_rejects_non_loopback_or_credential_urls(monkeypatch,url):
    monkeypatch.setenv('AGENT_ENGINE','meta_local_hybrid');monkeypatch.setenv('META_LOCAL_BASE_URL',url)
    with pytest.raises(RuntimeError,match='loopback'):meta_llm()


def test_local_meta_explicit_no_thinking_and_json_wire(monkeypatch):
    monkeypatch.setenv('AGENT_ENGINE','meta_local_hybrid')
    async def run():
        client=meta_llm()
        async def handler(request):
            import json
            body=json.loads(request.content)
            assert str(request.url)=='http://127.0.0.1:11434/api/chat'
            assert body['think'] is False and body['format']=='json'
            assert body['options']['num_predict']==150
            assert body['messages'][1]['images']
            assert 'authorization' not in request.headers
            return httpx.Response(200,json={'message':{'content':'{"ready":true}'},'load_duration':100,'eval_count':20,'arbitrary_secret':'never-log-this'})
        await client._http.aclose();client._http=httpx.AsyncClient(transport=httpx.MockTransport(handler),trust_env=False)
        try:
            assert await client.chat('JSON','read',b'jpeg',max_tokens=150,json_mode=True)=='{"ready":true}'
            assert client.last_metrics=={'load_duration':100,'eval_count':20}
        finally:await client._http.aclose()
    asyncio.run(run())


def test_local_meta_schema_and_exact_terminal_marker(monkeypatch):
    monkeypatch.setenv('AGENT_ENGINE','meta_local_hybrid')
    async def run():
        client=meta_llm()
        async def handler(request):
            import json
            assert json.loads(request.content)['format']=={'type':'object','required':['ready']}
            return httpx.Response(200,json={'message':{'content':'{"ready":true}<|eot|>'}})
        await client._http.aclose();client._http=httpx.AsyncClient(transport=httpx.MockTransport(handler),trust_env=False)
        try:assert await client.chat('JSON','read',json_schema={'type':'object','required':['ready']})=='{"ready":true}'
        finally:await client._http.aclose()
    asyncio.run(run())


def test_local_vision_judge_requires_typed_fields():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from hidagent.vision_judge import VisionJudge
    async def run():
        llm=SimpleNamespace(model='local',supports_native_json=True,chat=AsyncMock(return_value='{"ready":true}'))
        result=await VisionJudge(llm).evaluate({}, {'ready':{'type':'noul','instructions':'Is Calculator really open?'}}, b'fixture')
        schema=llm.chat.await_args.kwargs['json_schema']
        assert schema['required']==['ready'] and schema['properties']['ready']=={'type':'boolean'}
        assert result['answers']['ready']['noul']==1
    asyncio.run(run())


def test_explicit_local_writer_does_not_change_hybrid_engine(monkeypatch):
    monkeypatch.setenv('AGENT_ENGINE','hybrid')
    async def run():
        client=meta_llm(engine='meta_local_hybrid')
        try:
            import os
            assert os.environ['AGENT_ENGINE']=='hybrid'
            assert client.model=='muse-glimmer:30b' and 'authorization' not in client._http.headers
        finally:await client._http.aclose()
    asyncio.run(run())



@pytest.mark.parametrize('temperature',[0,.3,.7])
def test_local_meta_preserves_requested_sampling_temperature(temperature):
    from hidagent.meta_model import LocalMetaLLM
    async def run():
        client=LocalMetaLLM('http://127.0.0.1:11434/v1','muse-glimmer:30b')
        async def handler(request):
            import json
            assert json.loads(request.content)['options']['temperature']==temperature
            return httpx.Response(200,json={'message':{'content':'{}'}})
        await client._http.aclose();client._http=httpx.AsyncClient(transport=httpx.MockTransport(handler),trust_env=False)
        try:await client.chat('Transcribe','Source',temperature=temperature,json_schema={'type':'object'})
        finally:await client._http.aclose()
    asyncio.run(run())


def test_dedicated_ocr_model_does_not_replace_visual_source_model(monkeypatch):
    monkeypatch.setenv('META_LOCAL_MODEL','muse-glimmer:30b')
    async def run():
        visual=meta_llm(engine='meta_local_hybrid')
        ocr=meta_llm(engine='meta_local_hybrid',local_model='llama3.2:3b')
        try:
            assert visual.model=='muse-glimmer:30b' and ocr.model=='llama3.2:3b'
            assert str(visual._http.base_url)==str(ocr._http.base_url)=='http://127.0.0.1:11434/v1/'
            assert 'authorization' not in ocr._http.headers
        finally:await visual._http.aclose();await ocr._http.aclose()
    asyncio.run(run())


def test_dedicated_meta_file_wins_without_mutating_other_provider_env(tmp_path,monkeypatch):
 p=tmp_path/'meta.env';p.write_text('META_API_KEY=meta-file-only\nMETA_MODEL=muse-spark-1.3\n')
 monkeypatch.setenv('META_CREDENTIALS_FILE',str(p));monkeypatch.setenv('META_API_KEY','unused-environment-key');monkeypatch.setenv('ACTOR_API_KEY','anthropic-only')
 async def run():
  c=meta_llm(engine='meta_hybrid',role='mission')
  try:
   assert c.model=='muse-spark-1.3' and c.effort=='minimal'
   assert c._http.headers['authorization']=='Bearer meta-file-only'
   assert c.minimum_tokens==2048 and c._http._trust_env is False
  finally:await c._http.aclose()
 asyncio.run(run())


@pytest.mark.parametrize('contents',['META_API_KEY=\nMETA_MODEL=muse-spark-1.3\n','ACTOR_API_KEY=not-meta\n','META_API_KEY=meta-file-only\nBASE_URL=https://other.example\n'])
def test_incomplete_meta_file_never_falls_back_to_other_keys(tmp_path,monkeypatch,contents):
 p=tmp_path/'meta.env';p.write_text(contents);monkeypatch.setenv('META_CREDENTIALS_FILE',str(p));monkeypatch.setenv('META_API_KEY','do-not-use');monkeypatch.setenv('MODEL_API_KEY','do-not-use-either')
 with pytest.raises(RuntimeError,match='no other provider key'):meta_llm(engine='meta_hybrid')


def test_reasoner_profile_is_explicit_and_not_contributor(monkeypatch):
 monkeypatch.setenv('META_API_KEY','meta-test-key')
 async def run():
  c=meta_llm(engine='meta_hybrid',role='reasoner')
  try:
   assert c.model=='muse-wonder-1.0' and c.effort=='low' and c.minimum_tokens==4096
  finally:await c._http.aclose()
 asyncio.run(run())


def test_hosted_meta_structured_output_is_per_call_not_shared_state(monkeypatch):
 monkeypatch.setenv('META_API_KEY','meta-test-key');monkeypatch.delenv('META_CREDENTIALS_FILE',raising=False)
 async def run():
  c=meta_llm(engine='meta_hybrid',role='mission');seen=[]
  async def handler(request):
   seen.append(json.loads(request.content));return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
  await c._http.aclose();c._http=httpx.AsyncClient(base_url='https://api.meta.ai/v1',transport=httpx.MockTransport(handler))
  schema={'type':'json_schema','json_schema':{'name':'QA','schema':{'type':'object'}}}
  try:
   await c.chat('System','Public task',response_format=schema)
   await c.chat('System','Other task')
   assert seen[0]['response_format']==schema and 'response_format' not in seen[1]
   assert seen[0]['max_completion_tokens']==2048
  finally:await c._http.aclose()
 asyncio.run(run())


def test_hosted_meta_vision_uses_strict_native_schema_without_other_provider(monkeypatch):
 from hidagent.meta_model import MetaCloudLLM
 from hidagent.vision_judge import VisionJudge
 import json,io
 from PIL import Image
 async def run():
  client=MetaCloudLLM('muse-spark-1.3','dedicated-test-key',effort='minimal')
  async def handle(request):
   body=json.loads(request.content)
   assert str(request.url)=='https://api.meta.ai/v1/chat/completions'
   spec=body['response_format']['json_schema']
   assert body['response_format']['type']=='json_schema' and spec['strict']
   assert spec['schema']['required']==['ready','blocked']
   assert body['max_completion_tokens']>=2048 and body['reasoning_effort']=='minimal'
   assert 'max_tokens' not in body
   return httpx.Response(200,json={'choices':[{'message':{'content':'{"ready":true,"blocked":false}'}}]})
  await client._http.aclose();client._http=httpx.AsyncClient(base_url='https://api.meta.ai/v1',transport=httpx.MockTransport(handle))
  image=io.BytesIO();Image.new('RGB',(710,1080),'white').save(image,format='JPEG')
  judge=VisionJudge(client)
  try:
   r=await judge.evaluate({'scope':'public QA'}, {'ready':{'type':'noul','instructions':'Visible?'},'blocked':{'type':'noul','instructions':'Blocked?'}},image.getvalue())
   assert r['answers']['ready']['noul']==1 and r['format_repairs']==0
  finally:await judge.close()
 asyncio.run(run())


def test_hosted_meta_existing_response_format_is_preserved():
 from hidagent.meta_model import MetaCloudLLM
 import json
 async def run():
  client=MetaCloudLLM('muse-spark-1.3','dedicated-test-key')
  fmt={'type':'json_schema','json_schema':{'name':'ExistingMission','strict':True,'schema':{'type':'object','properties':{},'required':[],'additionalProperties':False}}}
  async def handle(request):
   assert json.loads(request.content)['response_format']==fmt
   return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
  await client._http.aclose();client._http=httpx.AsyncClient(base_url='https://api.meta.ai/v1',transport=httpx.MockTransport(handle))
  try:assert await client.chat('JSON','public',response_format=fmt)=='{}'
  finally:await client._http.aclose()
 asyncio.run(run())


def test_hosted_meta_conflicting_schemas_reject_before_http():
 from hidagent.meta_model import MetaCloudLLM
 async def run():
  client=MetaCloudLLM('muse-spark-1.3','dedicated-test-key')
  async def handle(request):raise AssertionError('No HTTP on ambiguous schema')
  await client._http.aclose();client._http=httpx.AsyncClient(base_url='https://api.meta.ai/v1',transport=httpx.MockTransport(handle))
  try:
   with pytest.raises(ValueError,match='exactly one'):await client.chat('JSON','public',json_schema={'type':'object'},response_format={'type':'json_object'})
  finally:await client._http.aclose()
 asyncio.run(run())


def test_per_request_effort_does_not_leak_to_parallel_or_later_calls():
 from hidagent.meta_model import MetaCloudLLM
 async def run():
  c=MetaCloudLLM('muse-spark-1.3','dedicated-test-key');seen=[]
  async def handler(request):
   body=json.loads(request.content);seen.append(body['reasoning_effort']);await asyncio.sleep(.01)
   return httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}]})
  await c._http.aclose();c._http=httpx.AsyncClient(base_url='https://api.meta.ai/v1',transport=httpx.MockTransport(handler))
  try:
   await asyncio.gather(c.chat('s','a',reasoning_effort='low'),c.chat('s','b'))
   await c.chat('s','c')
   assert sorted(seen[:2])==['low','minimal'] and seen[-1]=='minimal'
   assert c.extra['reasoning_effort']=='minimal'
  finally:await c._http.aclose()
 asyncio.run(run())


@pytest.mark.parametrize('status', [400, 401, 422, 429])
@pytest.mark.parametrize('json_body', [True, False])
def test_meta_4xx_body_logging_is_bounded_and_redacts_echoes(caplog, status, json_body):
 from hidagent.meta_model import MetaCloudLLM
 async def run():
  c=MetaCloudLLM('muse-spark-1.3','dedicated-test-key');seen=[]
  prompt='private request text\nwith quotes "here"'
  async def handler(request):
   seen.append(request)
   message='Invalid response_format schema: required must include every property. '+request.headers['authorization']+' '+prompt+' data:image/jpeg;base64,c2VjcmV0 sk-other-secret api_key=other-key '+('x'*1500)
   if json_body:return httpx.Response(status,json={'error':{'message':message,'code':'insufficient_quota','param':'response_format','request_echo':request.content.decode()}})
   return httpx.Response(status,text=message+' insufficient_quota')
  await c._http.aclose()
  c._http=httpx.AsyncClient(base_url='https://api.meta.ai/v1',headers={'Authorization':'Bearer dedicated-test-key'},transport=httpx.MockTransport(handler))
  try:
   with pytest.raises(httpx.HTTPStatusError):await c.chat(prompt,'public',b'synthetic-jpeg')
   record=next(r for r in caplog.records if r.name=='hidagent.meta_model')
   diagnostic=record.getMessage()
   assert len(diagnostic)<850 and '[truncated]' in diagnostic
   assert 'Invalid response_format schema' in diagnostic and str(status) in diagnostic
   assert all(secret not in diagnostic for secret in ('dedicated-test-key','private request text','c2VjcmV0','sk-other-secret','other-key','request_echo','synthetic-jpeg'))
   assert len(seen)==1
  finally:await c._http.aclose()
 asyncio.run(run())
