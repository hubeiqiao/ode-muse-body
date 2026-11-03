import asyncio
import time

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from hidagent.live_voice import VoiceBridge, ToolCall, check_local, MODEL


def test_rejects_foreign_origins_and_dns_rebinding():
    for host, origin in [('127.0.0.1:8894','https://evil.example'),('evil.example','http://evil.example')]:
        r=Request({'type':'http','scheme':'http','server':('127.0.0.1',8894),'path':'/',
                   'headers':[(b'host',host.encode()),(b'origin',origin.encode())], 'client':('127.0.0.1',123)})
        with pytest.raises(HTTPException) as e: check_local(r)
        assert e.value.status_code==403


def fixture():
    state={'device':True,'screen':True,'calibrated':True,'agent':{'running':False,'task':'','steps':[]}}
    starts=[]
    async def start(goal): starts.append(goal);state['agent']['task']=goal
    async def stop(): state['agent']['running']=False
    b=VoiceBridge(lambda:state,start,stop)
    b.sessions['x'*32]={'expires':time.monotonic()+60,'calls':{}}
    return b,state,starts


def test_duplicate_calls_are_executed_once_and_conflicts_rejected():
    async def run():
        b,_,starts=fixture()
        c=ToolCall(session='x'*32,id='one',name='phone_command',args={'goal':'Open Calculator'})
        a,z=await asyncio.gather(b.call(c),b.call(c))
        assert a==z and starts==['Open Calculator']
        with pytest.raises(HTTPException):
            await b.call(c.model_copy(update={'args':{'goal':'Type "hello"'}}))
    asyncio.run(run())


def test_missing_screen_and_busy_never_type():
    async def run():
        b,s,starts=fixture();s['screen']=False
        assert (await b.execute('phone_command',{'goal':'Type "hello"'}))['status']=='not_ready'
        s['screen']=True;s['agent']['running']=True
        assert (await b.execute('phone_command',{'goal':'Type "hello"'}))['status']=='busy'
        assert not starts
        await b.execute('stop_phone',{})
        assert not s['agent']['running']
    asyncio.run(run())


def test_token_uses_wire_schema_and_never_returns_permanent_key(monkeypatch):
    async def run():
        monkeypatch.setenv('GEMINI_LIVE_API_KEY','private-test-key')
        async def post(self,url,**kw):
            assert kw['headers']['x-goog-api-key']=='private-test-key'
            body=kw['json']
            assert body['bidiGenerateContentSetup']['model']=='models/'+MODEL
            assert body['fieldMask']=='model,generationConfig.responseModalities'
            assert 'liveConnectConstraints' not in body
            return httpx.Response(200,json={'name':'auth_tokens/short-lived'})
        monkeypatch.setattr(httpx.AsyncClient,'post',post)
        b,_,_=fixture();r=await b.mint()
        assert 'private-test-key' not in str(r)
        assert r['token'].startswith('auth_tokens/') and r['model']==MODEL
        assert r['setup']['tools'][0]['functionDeclarations'][0]['name']=='phone_command'
    asyncio.run(run())


def test_expired_sessions_and_unknown_tools_do_not_execute():
    async def run():
        b,_,starts=fixture();b.sessions['x'*32]['expires']=0
        with pytest.raises(HTTPException):
            await b.call(ToolCall(session='x'*32,id='a',name='phone_command',args={'goal':'hello'}))
        assert (await b.execute('shell',{'command':'bad'}))['status']=='rejected'
        assert not starts
    asyncio.run(run())


def test_closing_voice_cancels_only_its_own_job():
    async def run():
        b,s,_=fixture();b.owner=('x'*32,'Open Calculator')
        s['agent'].update(running=True,task='A different task')
        await b.close('x'*32)
        assert s['agent']['running']  # must not stop someone else's task
        b.sessions['y'*32]={'expires':time.monotonic()+60,'calls':{}}
        b.owner=('y'*32,'A different task')
        await b.close('y'*32)
        assert not s['agent']['running']
        assert (await b.execute('phone_command',{'goal':'Type hello'},'y'*32))['status']=='closed'
    asyncio.run(run())


def test_terminal_failure_tells_voice_to_stop_polling():
    b,s,_=fixture();s['agent']['steps']=[{'action':'error','reason':'not approved','t':123}]
    result=b.snapshot()
    assert result['status']=='failed' and result['terminal'] is True
    assert result['poll_again'] is False and result['running'] is False
    assert 'Do not poll or retry' in result['instruction']
    assert result['task_id']=='123'


def test_voice_normalizes_chinese_app_name_before_execution():
    async def run():
        b,_,starts=fixture()
        await b.execute('phone_command',{'goal':'open XiaoHongShu'})
        assert starts==['Open rednote']
    asyncio.run(run())


def test_voice_knows_executor_has_live_vision_and_must_delegate_clicks():
    from hidagent.live_voice import SYSTEM, TOOLS
    assert 'VISUALLY ENABLED' in SYSTEM
    assert 'CALL phone_command' in SYSTEM
    assert 'asking the user to tap manually' in SYSTEM
    assert 'You do not see the screen.' not in SYSTEM
    assert 'LIVE iPhone screenshot' in TOOLS[0]['functionDeclarations'][0]['description']
    b,_,_=fixture()
    assert b.snapshot()['capabilities']['visual_execution'] is True


@pytest.mark.parametrize('family,name,receiver', [('ipad','iPad','iPad Agent'),('iphone','iPhone','iPhone Agent')])
def test_voice_context_is_bound_to_console_device(family,name,receiver):
    from hidagent.live_voice import voice_context, TOOLS
    prompt,tools=voice_context({'target':{'device':family},'airplay_name':receiver})
    assert f"real {name}" in prompt
    assert f"{name} Screen Mirroring to {receiver}" in prompt
    assert 'no iPad operations' not in prompt
    assert f'LIVE {name} screenshot' in tools[0]['functionDeclarations'][0]['description']
    assert 'LIVE iPhone screenshot' in TOOLS[0]['functionDeclarations'][0]['description']
    assert 'separate console' in prompt


def test_voice_rejects_device_family_change_during_session():
    async def run():
        b,s,starts=fixture();b.sessions['x'*32]['device_family']='ipad'
        s['target']={'device':'iphone'}
        result=await b.execute('phone_command',{'goal':'Open Notes'},'x'*32,'one')
        assert result['status']=='not_ready' and result['terminal']
        assert not starts
    asyncio.run(run())


def test_inventory_voice_hint_is_opt_in_and_names_actual_content_author(monkeypatch):
    from hidagent.live_voice import voice_context
    state={'target':{'device':'ipad'},'models':{'planner':'claude-haiku-5-5','source_writer':'muse-glimmer:30b'}}
    monkeypatch.setenv('SOURCE_NOTE_WORKFLOWS','0')
    assert 'Optional inventory-to-new-note workflow is enabled' not in voice_context(state)[0]
    monkeypatch.setenv('SOURCE_NOTE_WORKFLOWS','1')
    text,_=voice_context(state)
    assert 'source content author is muse-glimmer:30b' in text
    assert 'Read the current inventory and create a new Notes note' in text
    assert 'never rewrite them' in text


def test_new_model_call_id_cannot_replay_same_goal_without_new_human_intent():
    from hidagent.live_voice import VoiceControl
    async def run():
        b,s,starts=fixture();sid='x'*32
        await b.call(ToolCall(session=sid,id='first',name='phone_command',args={'goal':'Open Calculator'}))
        s['agent']['steps']=[{'action':'done'}]
        receipt=await b.call(ToolCall(session=sid,id='duplicate-new-id',name='phone_command',args={'goal':'Open Calculator'}))
        assert receipt['deduplicated'] and starts==['Open Calculator']
        b.resume(VoiceControl(session=sid,text='打开计算器'))
        await b.call(ToolCall(session=sid,id='fresh-human',name='phone_command',args={'goal':'Open Calculator'}))
        assert starts==['Open Calculator','Open Calculator']
    asyncio.run(run())


def test_ipad_calculation_tool_is_structured_and_iphone_declarations_unchanged():
    from hidagent.live_voice import voice_context
    system,tools=voice_context({'target':{'device':'ipad'}})
    assert 'phone_calculate' in [d['name'] for d in tools[0]['functionDeclarations']]
    assert 'ALWAYS use phone_calculate' in system
    assert 'phone_calculate' not in [d['name'] for d in voice_context({'target':{'device':'iphone'}})[1][0]['functionDeclarations']]


def test_structured_calculation_canonicalizes_and_deduplicates_same_math():
    async def run():
        b,s,starts=fixture();s['target']={'device':'ipad'};sid='x'*32
        await b.call(ToolCall(session=sid,id='math',name='phone_calculate',args={'left':24,'right':18,'operator':'multiply'}))
        assert starts==['Open Calculator and calculate 24*18. Verify the displayed answer and leave it visible.']
        result=await b.call(ToolCall(session=sid,id='same-human-math',name='phone_command',args={'goal':'打开计算器，计算24乘以18'}))
        assert result['deduplicated'] and len(starts)==1
        b.sessions[sid]['paused']=True
        result=await b.execute('phone_calculate',{'left':1,'right':2,'operator':'add'},sid,'stale')
        assert result['status']=='stopped' and len(starts)==1
    asyncio.run(run())


@pytest.mark.parametrize('args',[
 {'left':'24','right':18,'operator':'multiply'}, {'left':True,'right':18,'operator':'multiply'},
 {'left':1000000,'right':18,'operator':'multiply'}, {'left':24,'right':18,'operator':'divide'},
 {'left':24,'right':18,'operator':'multiply','send':True}])
def test_structured_calculation_rejects_invalid_or_extra_action_without_input(args):
    async def run():
        b,s,starts=fixture();s['target']={'device':'ipad'}
        assert (await b.execute('phone_calculate',args))['status']=='rejected'
        assert not starts
    asyncio.run(run())


def test_restock_tool_is_gated_deduplicated_and_obeys_stop(monkeypatch):
 from hidagent.live_voice import voice_context,RESTOCK_GOAL
 monkeypatch.setenv('SOURCE_NOTE_WORKFLOWS','1')
 assert 'phone_restock_note' in [d['name'] for d in voice_context({'target':{'device':'ipad'}})[1][0]['functionDeclarations']]
 assert 'phone_restock_note' not in [d['name'] for d in voice_context({'target':{'device':'iphone'}})[1][0]['functionDeclarations']]
 async def run():
  b,s,starts=fixture();s['target']={'device':'ipad'};sid='x'*32
  assert (await b.execute('phone_restock_note',{'body':'invented'},sid))['status']=='rejected'
  await b.execute('phone_restock_note',{},sid,'first')
  assert starts==[RESTOCK_GOAL]
  assert (await b.execute('phone_restock_note',{},sid,'again'))['deduplicated']
  b.sessions[sid]['paused']=True
  assert (await b.execute('phone_restock_note',{},sid,'late'))['status']=='stopped'
  assert len(starts)==1
  monkeypatch.setenv('SOURCE_NOTE_WORKFLOWS','0')
  assert (await b.execute('phone_restock_note',{}))['status']=='rejected'
 asyncio.run(run())


def test_phone_status_without_goal_does_not_match_null_mission(monkeypatch):
 from types import SimpleNamespace
 import hidagent.live_voice as module
 async def run():
  b,_,_=fixture();sid='x'*32
  receipt={'request_id':'active-app-tool','status':'in_progress','running':True,'terminal':False}
  b.owner_app_tool='active-app-tool'
  b.app_tools=SimpleNamespace(busy=True,current_id='active-app-tool',status=lambda rid:dict(receipt))
  b.mission=SimpleNamespace(busy=False,status=lambda:{'status':'idle','running':False})
  clock=iter([0,6]);monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:next(clock)))
  result=await b.execute('phone_status',{},sid,'poll')
  assert result['request_id']=='active-app-tool' and result['running'] is True
 asyncio.run(run())


def test_phone_status_actual_mission_id_still_returns_its_terminal_receipt():
 from types import SimpleNamespace
 async def run():
  b,_,_=fixture();sid='x'*32;b.sessions[sid]['last_mission']='owned-goal'
  b.mission=SimpleNamespace(busy=False,status=lambda:{'request_id':'owned-goal','status':'completed','running':False})
  assert (await b.execute('phone_status',{},sid,'poll'))['request_id']=='owned-goal'
 asyncio.run(run())
