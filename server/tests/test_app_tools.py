import asyncio,json,time
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from hidagent.app_tools import AppTools,TOOLS,ToolCall,router_for,match_goal
from hidagent.command_input import standalone_dictation
from hidagent.device import Device
from hidagent.live_voice import VoiceBridge


def setup_tools(writer=None,bridge=None,stable=True):
    state={'device':True,'screen':True,'calibrated':True,'controller':{'stable':stable},'target':{'device':'ipad'},
           'agent':{'running':False,'steps':[]}}
    writer=writer or SimpleNamespace(model='haiku-test',chat=AsyncMock(return_value=json.dumps({'text':'An English local draft.'})))
    bridge=bridge or SimpleNamespace(exploration=None,execute=AsyncMock(return_value={'status':'model_reported_done','running':False}),snapshot=Mock())
    memory=SimpleNamespace(snapshot=lambda:{'preferences':[],'discoveries':[]})
    tools=AppTools(bridge,writer,memory,lambda:state,AsyncMock())
    return tools,bridge,writer,state


def test_app_catalog_is_real_schemas_not_prompts_and_has_no_publish_tool():
    tools,_,_,_=setup_tools();catalog=tools.catalog()
    assert {'calculator.open','calculator.calculate','x.open','x.prepare_draft','keyboard.type_focused'}<={x['name'] for x in catalog['tools']}
    assert len([x for x in catalog['tools'] if x['app']=='Calculator'])==2
    assert TOOLS['x.prepare_draft']['effect']=='local_draft' and not catalog['publishing_enabled']
    assert all('parameters' in x for x in catalog['tools']) and 'x.publish' not in TOOLS

@pytest.mark.parametrize('name,args',[('calculator.open',{'goal':'publish now'}),('calculator.calculate',{'left':True,'right':1,'operator':'add'}),('calculator.calculate',{'left':1,'right':2,'operator':'divide'}),('calculator.calculate',{'left':1000000,'right':1,'operator':'add'}),('keyboard.type_focused',{'text':'hello\nsubmit'}),('x.prepare_draft',{'goal':' '})])
def test_bad_args_never_reach_device_or_model(name,args):
    async def run():
        tools,bridge,writer,_=setup_tools()
        with pytest.raises(HTTPException):await tools.call('bad_args_1',name,args,wait_seconds=0)
        bridge.execute.assert_not_called();writer.chat.assert_not_called()
    asyncio.run(run())


def test_unstable_transport_blocks_paid_recognition_and_hid():
    async def run():
        tools,bridge,writer,_=setup_tools(stable=False)
        result=await tools.call('preflight_1','calculator.open',{})
        assert result['status']=='not_ready' and result['screen_ready']
        bridge.execute.assert_not_called();writer.chat.assert_not_called()
    asyncio.run(run())


def test_duplicate_request_and_wrong_instance_never_replay():
    async def run():
        tools,bridge,_,_=setup_tools()
        first=await tools.call('same_request_1','calculator.open',{})
        second=await tools.call('same_request_1','calculator.open',{})
        assert first['status']==second['status']=='completed';bridge.execute.assert_awaited_once()
        with pytest.raises(HTTPException):await tools.call('same_request_1','x.open',{})
        with pytest.raises(HTTPException):await tools.call('new_request_1','calculator.open',{},server_instance='old-instance-token')
        bridge.execute.assert_awaited_once()
    asyncio.run(run())


def test_local_draft_is_available_offline_and_never_types_or_publishes():
    async def run():
        tools,bridge,writer,state=setup_tools(stable=False);state['device']=state['screen']=False
        result=await tools.call('draft_request_1','x.prepare_draft',{'goal':'Draft an X post for Joe Speaking.'})
        assert result['status']=='needs_review' and result['result']['published'] is False and result['result']['account_verified'] is False
        bridge.execute.assert_not_called();writer.chat.assert_awaited_once()
    asyncio.run(run())


def test_stop_prevents_late_draft_result_even_if_provider_swallows_cancel():
    async def run():
        started=asyncio.Event();release=asyncio.Event()
        async def slow(*a,**kw):
            started.set()
            try:await release.wait()
            except asyncio.CancelledError:await release.wait()
            return 'Too late to save'
        tools,bridge,_,_=setup_tools(writer=SimpleNamespace(model='test',chat=slow))
        receipt=await tools.call('slow_draft_1','x.prepare_draft',{'goal':'Draft an X post.'},wait_seconds=0)
        await started.wait();assert receipt['running']
        await tools.stop();release.set();await asyncio.sleep(.01)
        final=tools.status('slow_draft_1');assert final['status']=='stopped' and 'body' not in final['result']
        bridge.execute.assert_not_called()
    asyncio.run(run())

@pytest.mark.parametrize('goal,name,args',[
 ('Open Calculator','calculator.open',{}),('Open X','x.open',{}),('Calculate 7+8','calculator.calculate',{'left':7,'right':8,'operator':'add'}),
 ('Draft an X post about English practice','x.prepare_draft',{'goal':'Draft an X post about English practice'}),
 ('type "hello and press send"','keyboard.type_focused',{'text':'hello and press send'})])
def test_existing_intents_map_to_specific_tools(goal,name,args):assert match_goal(goal)==(name,args)


def test_literal_quotes_and_action_words_survive_tool_routing():
    async def run():
        tools,bridge,_,_=setup_tools();text='He said "hello" and press send'
        await tools.call('literal_quotes_1','keyboard.type_focused',{'text':text})
        sent=bridge.execute.call_args.args[1]['goal']
        assert standalone_dictation(sent)==text
        assert bridge.execute.call_args.kwargs['_app_tool'] is True
    asyncio.run(run())


def test_passive_readiness_requires_current_generation_and_fast_acks():
    device=Device();device._writer=SimpleNamespace(is_closing=lambda:False);device.links=3;device._connected_at=90
    def sample(link,wire=.1):return {'link':link,'finished_at':99,'command':'ping','outcome':'ok','wire_s':wire}
    device.timings.extend([sample(2),sample(2)]);assert not device.readiness(now=100)['stable']
    device.timings.extend([sample(3),sample(3)]);assert device.readiness(now=100)['stable']
    device.timings.append(sample(3,16));assert not device.readiness(now=100)['stable']


def test_tool_api_requires_current_boot_token_and_rejects_foreign_origin():
    tools,_,_,_=setup_tools();app=FastAPI();app.include_router(router_for(tools))
    with TestClient(app) as client:
        catalog=client.get('/api/app-tools/catalog').json()
        assert client.post('/api/app-tools/call',json={'tool_name':'x.prepare_draft','args':{'goal':'hello'},'request_id':'request_123'}).status_code==422
        response=client.post('/api/app-tools/call',json={'tool_name':'x.prepare_draft','args':{'goal':'hello'},'request_id':'request_123','server_instance':'stale_boot_token_1234'})
        assert response.status_code==409
        assert client.get('/api/app-tools/catalog',headers={'Origin':'https://evil.example'}).status_code==403
        assert catalog['server_instance']==tools.server_instance


def test_voice_and_text_share_executor_without_snapshot_recursion():
    async def run():
        state={'device':True,'screen':True,'calibrated':True,'controller':{'stable':True},'target':{'device':'ipad'},'agent':{'running':False,'steps':[]}}
        async def start(goal):state['agent'].update(task=goal,steps=[{'action':'done','t':123,'answer':'Fake unit-test completion'}])
        bridge=VoiceBridge(lambda:state,start,AsyncMock())
        tools=AppTools(bridge,SimpleNamespace(model='test'),SimpleNamespace(),lambda:state,AsyncMock());bridge.app_tools=tools
        result=await bridge.execute('phone_command',{'goal':'Open Calculator'})
        assert result['status']=='completed' and result['tool_name']=='calculator.open'
        assert bridge.snapshot()['request_id']==result['request_id']
        assert state['agent']['task']=='Open Calculator'
    asyncio.run(run())


def test_new_model_call_id_cannot_replay_same_voice_intent_without_new_human_request():
    from hidagent.live_voice import VoiceControl
    async def run():
        state={'device':True,'screen':True,'calibrated':True,'controller':{'stable':True},'target':{'device':'ipad'},'agent':{'running':False,'steps':[]}}
        started=[]
        async def start(goal):started.append(goal);state['agent'].update(task=goal,steps=[{'action':'done','t':len(started)}])
        bridge=VoiceBridge(lambda:state,start,AsyncMock());sid='s'*20
        bridge.sessions[sid]={'expires':time.monotonic()+30,'calls':{},'paused':False,'cancelled_ids':set()}
        tools=AppTools(bridge,SimpleNamespace(),SimpleNamespace(),lambda:state,AsyncMock());bridge.app_tools=tools
        await bridge.execute('phone_command',{'goal':'Open Calculator'},sid,'model-call-1')
        result=await bridge.execute('use_app_tool',{'tool_name':'calculator.open','args':{}},sid,'model-call-2')
        assert result['deduplicated'] and started==['Open Calculator']
        bridge.resume(VoiceControl(session=sid,text='Open Calculator'))
        await bridge.execute('phone_command',{'goal':'Open Calculator'},sid,'model-call-3')
        assert len(started)==2
    asyncio.run(run())


def test_provider_cancellation_owns_pending_app_tool_and_stops_it():
    from hidagent.live_voice import VoiceControl
    async def run():
        state={'device':False,'screen':True,'calibrated':True,'agent':{'running':False,'steps':[]}}
        started=asyncio.Event()
        async def writing(*a,**kw):started.set();await asyncio.Event().wait()
        bridge=VoiceBridge(lambda:state,AsyncMock(),AsyncMock());sid='s'*20
        bridge.sessions[sid]={'expires':time.monotonic()+30,'calls':{},'paused':False,'cancelled_ids':set()}
        tools=AppTools(bridge,SimpleNamespace(model='test',chat=writing),SimpleNamespace(snapshot=lambda:{'preferences':[],'discoveries':[]}),lambda:state,AsyncMock());bridge.app_tools=tools
        async def stop():await tools.stop()
        bridge.stop=stop
        job=asyncio.create_task(bridge.execute('use_app_tool',{'tool_name':'x.prepare_draft','args':{'goal':'Draft an X post.'}},sid,'owned-call'))
        await started.wait()
        receipt=await bridge.interrupt(VoiceControl(session=sid,text='',ids=['owned-call']))
        assert receipt['status']=='stopped' and bridge.sessions[sid]['paused']
        result=await job;assert result['status']=='stopped' and not tools.busy
    asyncio.run(run())


@pytest.mark.parametrize('output',['Here is a draft: hello',json.dumps({'text':'x'*281}),json.dumps({'text':'Hello \u2014 world'})])
def test_invalid_generated_post_is_not_saved_as_a_reviewable_draft(output):
    async def run():
        tools,bridge,writer,_=setup_tools(writer=SimpleNamespace(model='test',chat=AsyncMock(return_value=output)))
        result=await tools.call('invalid_draft_1','x.prepare_draft',{'goal':'Draft an X post.'})
        assert result['status']=='failed' and 'body' not in result['result']
        writer.chat.assert_awaited_once();bridge.execute.assert_not_called()
    asyncio.run(run())


def test_public_reader_reuses_current_completed_search_without_device_replay():
 async def run():
  tools,bridge,writer,state=setup_tools();bridge.snapshot.return_value={'task_id':'search-1'}
  bridge.execute.return_value={'status':'model_reported_done','running':False,'task_id':'search-1'}
  tools.result_reader=SimpleNamespace(read=AsyncMock(return_value={'summary':'Visible results','source_kind':'verified_search_snapshot'}))
  await tools.call('source_search','youtube.search',{'query':'IELTS'})
  r=await tools.call('read_search','public.read_results',{})
  assert r['status']=='completed' and r['result']['source_kind']=='verified_search_snapshot'
  bridge.execute.assert_awaited_once()
  assert tools.result_reader.read.call_args.args[0]['request_id']=='source_search'
  writer.chat.assert_not_awaited()
 asyncio.run(run())


def test_public_reader_without_source_or_after_task_change_never_calls_model():
 async def run():
  tools,bridge,writer,state=setup_tools();tools.result_reader=SimpleNamespace(read=AsyncMock())
  r=await tools.call('no_source','public.read_results',{})
  assert r['status']=='failed' and 'public search first' in r['result']['reason']
  tools.result_reader.read.assert_not_awaited()
  bridge.execute.return_value={'status':'model_reported_done','running':False,'task_id':'old-search'}
  await tools.call('source_search','youtube.search',{'query':'IELTS'})
  bridge.snapshot.return_value={'task_id':'other-task'}
  r=await tools.call('new_read','public.read_results',{})
  assert r['status']=='failed' and 'task changed' in r['result']['reason']
  tools.result_reader.read.assert_not_awaited()
 asyncio.run(run())


def test_public_result_english_intent_routes_to_same_tool():
 assert match_goal('Read the search results.')==('public.read_results',{})
 assert match_goal('What did you find?') is None
 assert match_goal('Read my private messages') is None


def test_meta_local_draft_does_not_read_or_upload_personal_memory():
 async def run():
  tools,bridge,writer,state=setup_tools();writer.provider='meta'
  tools.memory.snapshot=Mock(side_effect=AssertionError('Personal memory must not be sent to Meta'))
  r=await tools.call('meta_draft_privacy_qa','x.prepare_draft',{'goal':'A public speaking practice idea for local review.'})
  assert r['status']=='needs_review'
  context=json.loads(writer.chat.await_args.args[1]);assert context['explicit_context']==[] and context['verified_sources']==[]
  tools.memory.snapshot.assert_not_called();bridge.execute.assert_not_called()
 asyncio.run(run())
