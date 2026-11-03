"""Voice integration with fakes: no token service, network or hardware."""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hidagent.live_voice import VoiceBridge, ToolCall, VoiceControl
from hidagent.ipad_demo_flows import DemoFlows, DemoRequest
from test_ipad_demo_flows import make, prepare


def bridge(tmp_path):
    f, phone, vision, state, clock = make(tmp_path)
    state['agent'] = {'running': False, 'task': '', 'steps': []}
    b = VoiceBridge(lambda: state, AsyncMock(), AsyncMock(), driver=SimpleNamespace(name='esp32'))
    b.demos = f
    b.sessions['offline-session-123'] = {'expires': time.monotonic()+600, 'calls': {}, 'paused': False, 'cancelled_ids': set()}
    return b, f, phone


def test_demo_voice_routes_without_general_model_executor(tmp_path):
    b, f, p = bridge(tmp_path)
    async def scenario():
        c = ToolCall(session='offline-session-123', id='open', name='ipad_demo',
                     args={'action':'camera_open'}, utterance='打开相机', owner_turn=1)
        r = await b.call(c)
        assert r['status'] == 'ready'
        b.start.assert_not_awaited()
        assert (await b.call(c)) == r and p.type.await_count == 1
    asyncio.run(scenario())


def test_demo_voice_rejects_missing_utterance_and_paused(tmp_path):
    b, _, p = bridge(tmp_path)
    async def scenario():
        # No utterance/turn gate any more (tool calls can precede transcription); pause still stops.
        c = ToolCall(session='offline-session-123', id='open', name='ipad_demo', args={'action':'camera_open'})
        b.sessions[c.session]['paused'] = True
        c = c.model_copy(update={'id':'next', 'utterance':'打开相机', 'owner_turn':1})
        assert (await b.call(c))['status'] == 'stopped'
        p.key.assert_not_awaited()
    asyncio.run(scenario())


def test_demo_voice_new_tool_id_same_owner_turn_never_replays(tmp_path):
    b, _, p = bridge(tmp_path)
    async def scenario():
        c = ToolCall(session='offline-session-123', id='one', name='ipad_demo',
                     args={'action':'camera_photo'}, utterance='拍', owner_turn=1)
        r = await b.call(c)
        assert (await b.call(c.model_copy(update={'id':'two'}))) == r
        assert p.tap.await_count == 1
    asyncio.run(scenario())


def test_stop_cancels_demo_and_invalidates_pending_confirmation(tmp_path):
    b, f, p = bridge(tmp_path)
    async def scenario():
        block = asyncio.Event()
        async def key(combo): await block.wait()
        p.key.side_effect = key
        c = ToolCall(session='offline-session-123', id='one', name='ipad_demo',
                     args={'action':'camera_open'}, utterance='打开相机', owner_turn=1)
        task = asyncio.create_task(b.call(c))
        for _ in range(10): await asyncio.sleep(0)
        await b.interrupt(VoiceControl(session=c.session, text='stop'))
        assert (await task)['status'] == 'stopped'
        assert not f.pending and not f.busy
    asyncio.run(scenario())


def test_post_requires_browser_audio_readback_ack(tmp_path):
    f, p, _, _, _ = make(tmp_path)
    d = prepare(f)
    # Clearing acknowledgment models a readback interrupted before audio ended.
    f.pending['session']['readback_complete'] = False
    before = p.tap.await_count
    r = asyncio.run(f.run('session', 'confirm', '发送', 2,
                         DemoRequest(action='x_post', draft_id=d['draft_id'])))
    assert r['status'] == 'rejected' and p.tap.await_count == before
    assert f.mark_readback('session', d['draft_id'], d['readback'])
    r = asyncio.run(f.run('session', 'confirm2', '发送', 3,
                         DemoRequest(action='x_post', draft_id=d['draft_id'])))
    assert r['status'] == 'published'


def test_demo_action_utterances_can_resume_after_stop():
    from hidagent.control_intent import is_action_intent
    for text in ('拍', '倒计时三秒再拍', '切前置', '切后置', '上次那个产品怎么样了', '发送', 'post it'):
        assert is_action_intent(text), text


def test_demo_catalog_is_exposed_only_when_configured(tmp_path, monkeypatch):
    import httpx
    b, _, _ = bridge(tmp_path)
    monkeypatch.setenv('GEMINI_LIVE_API_KEY', 'offline-fake')
    async def post(*a, **kw): return httpx.Response(200, json={'name':'auth_tokens/fake'})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    result=asyncio.run(b.mint())
    names=[d['name'] for d in result['setup']['tools'][0]['functionDeclarations']]
    assert 'ipad_demo' in names
    b.demos=None;b.last_mint=0
    result=asyncio.run(b.mint())
    assert 'ipad_demo' not in [d['name'] for d in result['setup']['tools'][0]['functionDeclarations']]


def test_demo_rechecks_competing_task_after_acquiring_lock(tmp_path):
    b,_,p=bridge(tmp_path)
    state=b.state()
    class Lock:
        def locked(self):return False
        async def __aenter__(self):state['agent']['running']=True
        async def __aexit__(self,*args):pass
    b.command_lock=Lock()
    c=ToolCall(session='offline-session-123',id='one',name='ipad_demo',
               args={'action':'camera_open'},utterance='打开相机',owner_turn=1)
    result=asyncio.run(b.call(c))
    assert result['status']=='busy'
    p.key.assert_not_awaited()


def test_browser_readback_endpoint_to_explicit_post_end_to_end(tmp_path):
    import httpx
    from fastapi import FastAPI
    from hidagent.live_voice import router_for
    b,f,p=bridge(tmp_path)
    app=FastAPI();app.include_router(router_for(b))
    sid='offline-session-123'
    async def scenario():
        c=ToolCall(session=sid,id='draft',name='ipad_demo',utterance='Draft a reply',owner_turn=1,
                   args={'action':'x_draft','target':'https://x.com/owner/status/123',
                         'account':'@example_creator','text':'A useful reply'})
        draft=await b.call(c)
        assert draft['status']=='awaiting_confirmation'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
            denied=await client.post('/api/voice/demo-readback',json={'session':sid,'draft_id':draft['draft_id'],'text':'Different text'})
            assert denied.status_code==200 and not denied.json()['ok']
            accepted=await client.post('/api/voice/demo-readback',json={'session':sid,'draft_id':draft['draft_id'],'text':draft['readback']})
            assert accepted.status_code==200 and accepted.json()['ok']
        confirm=ToolCall(session=sid,id='post',name='ipad_demo',utterance='post it',owner_turn=2,
                         args={'action':'x_post','draft_id':draft['draft_id']})
        posted=await b.call(confirm)
        assert posted['status']=='published'
        count=p.tap.await_count
        assert await b.call(confirm.model_copy(update={'id':'duplicate'}))==posted
        assert p.tap.await_count==count
    asyncio.run(scenario())


def test_demo_session_offers_direct_camera_controls(tmp_path, monkeypatch):
    import httpx
    b,_,_=bridge(tmp_path)
    monkeypatch.setenv('GEMINI_LIVE_API_KEY','offline-fake')
    async def post(*a,**kw):return httpx.Response(200,json={'name':'auth_tokens/fake'})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    setup=asyncio.run(b.mint())['setup']
    names=[d['name'] for d in setup['tools'][0]['functionDeclarations']]
    assert {'ipad_demo', 'open_camera', 'flip_camera', 'take_photo'} <= set(names)
    assert 'start_camera_conversation' not in names
    assert 'For every phone action call phone_command;' not in setup['systemInstruction']['parts'][0]['text']


def test_spoken_stop_cancels_demo_queued_before_run(tmp_path):
    b,_,p=bridge(tmp_path)
    async def scenario():
        gate=asyncio.Event(); entered=asyncio.Event()
        class Lock:
            def locked(self):return False
            async def __aenter__(self):entered.set();await gate.wait()
            async def __aexit__(self,*args):pass
        b.command_lock=Lock()
        c=ToolCall(session='offline-session-123',id='one',name='ipad_demo',
                   args={'action':'camera_open'},utterance='打开相机',owner_turn=1)
        job=asyncio.create_task(b.call(c));await entered.wait()
        await b.interrupt(VoiceControl(session=c.session,text='stop'))
        gate.set()
        assert (await job)['status']=='stopped'
        p.key.assert_not_awaited()
    asyncio.run(scenario())


def test_closing_old_voice_session_does_not_cancel_new_demo(tmp_path):
    b,f,p=bridge(tmp_path)
    async def scenario():
        old='offline-session-123';new='another-session-123'
        b.sessions[old]['demo_owner_id']='old-call'
        b.sessions[new]={'expires':time.monotonic()+60,'calls':{},'paused':False,'cancelled_ids':set()}
        gate=asyncio.Event();entered=asyncio.Event()
        async def key(combo):entered.set();await gate.wait();f.screen.updated+=.1
        p.key.side_effect=key
        c=ToolCall(session=new,id='new-call',name='ipad_demo',args={'action':'camera_open'},utterance='打开相机',owner_turn=1)
        job=asyncio.create_task(b.call(c));await entered.wait()
        await b.close(old);gate.set()
        assert (await job)['status']=='ready'
    asyncio.run(scenario())


def test_voice_catalog_keeps_tools_but_drops_acceptance_evidence():
    from hidagent.live_voice import voice_catalog
    catalog = {'publishing_enabled': False, 'tools': [{'name': 'calculator.open', 'app': 'Calculator', 'description': 'Open it.',
               'effect': 'device', 'parameters': {'type': 'object', 'properties': {}},
               'validation': {'status': 'verified', 'evidence': 'x' * 5000, 'runs': list(range(200))}}]}
    compact = voice_catalog(catalog)
    tool = compact['tools'][0]
    assert tool['name'] == 'calculator.open' and tool['description'] == 'Open it.' and tool['validation'] == 'verified'
    assert compact['publishing_enabled'] is False
    assert len(json.dumps(compact)) < 400
    assert catalog['tools'][0]['validation']['status'] == 'verified'  # input untouched


def test_demo_session_says_how_to_see_instead_of_pretending(tmp_path, monkeypatch):
    import httpx
    b,_,_=bridge(tmp_path)
    monkeypatch.setenv('GEMINI_LIVE_API_KEY','offline-fake')
    async def post(*a,**kw):return httpx.Response(200,json={'name':'auth_tokens/fake'})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    text=asyncio.run(b.mint())['setup']['systemInstruction']['parts'][0]['text']
    assert 'open_camera' in text and 'camera preview' in text
    assert 'start_camera_conversation' not in text
    assert 'Amazon uses the Amazon app' in text
    assert '"open Amazon" or an Amazon product search goes to phone_command' in text
    assert 'Open the Amazon app and search for wireless earbuds' in text
    assert 'ipad_demo amazon_lookup only when the owner explicitly asks for the Amazon website' in text
    assert 'Never say an app is not installed unless a tool receipt says so' in text
