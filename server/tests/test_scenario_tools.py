"""Offline scenario contracts. No live transport or model API."""
import asyncio, io, json, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from PIL import Image
from fastapi import HTTPException
from hidagent.app_tools import AppTools, TOOLS
from hidagent.decision import DecisionUnavailable

NAMES = ('instagram.profile_status','discord.status','bilibili.notifications',
         'youtube.notifications','tiktok.notifications','douyin.notifications','muse.threads_bot_read')

def frame(size=(1646,1080)):
    b=io.BytesIO();Image.new('RGB',size,'white').save(b,format='PNG');return b.getvalue()

def setup(name='instagram.profile_status'):
    screen=SimpleNamespace(frame=frame(),live=True,updated=time.time(),settled=AsyncMock(),fresh=AsyncMock())
    target=dict(device='ipad',orientation='landscape',width=1646,height=1080,calibration_key='test')
    phone=SimpleNamespace(target_info=lambda:target.copy(),calibrated=True,
        device=SimpleNamespace(connected=True,links=1),tap=AsyncMock(),key=AsyncMock(),type=AsyncMock())
    async def probe(**kwargs):
        screen.updated=time.time()
    phone.ensure_live=AsyncMock(side_effect=probe)
    a=SimpleNamespace(phone=phone,screen=screen,running=False,
        actor=SimpleNamespace(model='fake-reader',supports_native_json=True,chat=AsyncMock()),
        client=SimpleNamespace(model='fake-judge',evaluate=AsyncMock(return_value={
            'answers':{'ready':{'noul':1.0},'blocked':{'noul':0.0}},'model':'fake','seconds':.01})),
        trace=SimpleNamespace(write=lambda *a,**k:None),
        navigation_text_reader=SimpleNamespace(read=AsyncMock(return_value={'rows':[]})))
    state=dict(device=True,screen=True,calibrated=True,controller={'stable':True},agent={'running':False})
    tools=AppTools(SimpleNamespace(exploration=None,execute=AsyncMock(),snapshot=lambda **k:{}),
        SimpleNamespace(model='fake',chat=AsyncMock()),SimpleNamespace(snapshot=lambda:{}),lambda:state,
        AsyncMock(),result_reader=SimpleNamespace(agent=a))
    return tools,a,target

def reader(name='instagram.profile_status'):
    from hidagent.scenario_tools import ScenarioReader
    t,a,b=setup();return ScenarioReader(t,name,lambda:None),t,a,b

@pytest.mark.parametrize('name',NAMES)
def test_registration_dispatch_once_and_receipt(monkeypatch,name):
    assert name in TOOLS and TOOLS[name]['effect']=='device_read'
    from hidagent.scenario_tools import ScenarioReader
    run=AsyncMock(return_value={'verification_passed':True,'published':False})
    monkeypatch.setattr(ScenarioReader,'run',run)
    async def check():
        t,a,_=setup()
        first=await t.call('scenario_once',name,{})
        second=await t.call('scenario_once',name,{})
        assert first['status']==second['status']=='completed'
        assert 'model_calls' in second['metrics'];run.assert_awaited_once()
        t.bridge.execute.assert_not_awaited()
        with pytest.raises(HTTPException):await t.call('bad_scenario_args',name,{'send':True})
    asyncio.run(check())

@pytest.mark.parametrize('change',['portrait','size','frozen','link','calibration','running'])
def test_preflight_before_input_or_model(change):
    async def check():
        r,t,a,b=reader()
        if change=='portrait':b['orientation']='portrait'
        if change=='size':a.screen.frame=frame((710,1080))
        if change=='frozen':
            a.screen.updated=time.time()-30
            a.phone.ensure_live.side_effect=None
        if change=='link':a.phone.device.links+=1
        if change=='calibration':a.phone.calibrated=False
        if change=='running':a.running=True
        with pytest.raises(DecisionUnavailable):await r.run()
        a.phone.tap.assert_not_awaited();a.actor.chat.assert_not_awaited();a.client.evaluate.assert_not_awaited()
    asyncio.run(check())

def profile(handle='example_creator.app'):
    return dict(username=handle,posts='50',followers='48',following='19')

@pytest.mark.parametrize('payload',[profile(),profile('someone_else'),{**profile(),'private_text':'secret'},
                                    {**profile(),'followers':True}])
def test_profile_schema_account_and_snapshot(monkeypatch,payload):
    async def check():
        r,t,a,b=reader()
        monkeypatch.setattr(r,'launch',AsyncMock());monkeypatch.setattr(r,'navigate',AsyncMock())
        monkeypatch.setattr(r,'read_view',AsyncMock(return_value=frame()))
        a.actor.chat.return_value=json.dumps(payload)
        if payload==profile():
            out=await r.run()
            assert out['verification_passed'] and out['account_verified']
            assert out['username']=='example_creator.app' and len(out['frame_sha256'])==64
            assert a.actor.chat.call_args.kwargs['json_schema']['additionalProperties'] is False
        else:
            with pytest.raises(DecisionUnavailable) as exc:await r.run()
            if payload==profile('someone_else'):assert 'someone_else' in str(exc.value)
    asyncio.run(check())

@pytest.mark.parametrize('verdict',[float('nan'),True,.5,None])
def test_verifier_rejects_invalid_probability(monkeypatch,verdict):
    async def check():
        r,t,a,b=reader()
        monkeypatch.setattr(r,'launch',AsyncMock());monkeypatch.setattr(r,'navigate',AsyncMock())
        monkeypatch.setattr(r,'read_view',AsyncMock(return_value=frame()))
        a.actor.chat.return_value=json.dumps(profile())
        a.client.evaluate.return_value['answers']['ready']['noul']=verdict
        with pytest.raises(DecisionUnavailable):await r.run()
    asyncio.run(check())

@pytest.mark.parametrize('app',['bilibili','tiktok','douyin'])
def test_combined_inbox_masks_private_rows(app):
    async def check():
        r,t,a,b=reader(app+'.notifications')
        a.navigation_text_reader.read.return_value={'rows':[
            dict(text='Inbox',confidence=1.,box=dict(left=.1,top=.05,right=.2,bottom=.08)),
            dict(text='Comments 3',confidence=1.,box=dict(left=.2,top=.15,right=.4,bottom=.18)),
            dict(text='SECRET PRIVATE MESSAGE',confidence=1.,box=dict(left=.1,top=.5,right=.9,bottom=.6))]}
        im=Image.open(io.BytesIO(await r.read_view()))
        assert im.getpixel((500,550))==(0,0,0)
        assert im.getpixel((400,170))==(255,255,255)
        assert r.dm_content_withheld
        a.actor.chat.assert_not_awaited()
    asyncio.run(check())

def test_unknown_muse_bot_refuses_before_upload_or_tap(monkeypatch):
    async def check():
        r,t,a,b=reader('muse.threads_bot_read');monkeypatch.setattr(r,'launch',AsyncMock())
        monkeypatch.setattr(r,'control',lambda *args,**kw:None)
        with pytest.raises(DecisionUnavailable):await r.run()
        a.actor.chat.assert_not_awaited();a.client.evaluate.assert_not_awaited();a.phone.tap.assert_not_awaited()
    asyncio.run(check())

def test_stop_after_late_model_response_discards_result(monkeypatch):
    async def check():
        r,t,a,b=reader();r.active=lambda:t._active(0)
        monkeypatch.setattr(r,'launch',AsyncMock());monkeypatch.setattr(r,'navigate',AsyncMock())
        monkeypatch.setattr(r,'read_view',AsyncMock(return_value=frame()))
        async def late(*args,**kwargs):t.epoch+=1;return json.dumps(profile())
        a.actor.chat.side_effect=late
        with pytest.raises(asyncio.CancelledError):await r.run()
        a.client.evaluate.assert_not_awaited()
    asyncio.run(check())

def test_public_row_budget_and_private_types_rejected():
    from hidagent.scenario_tools import Notifications
    from pydantic import ValidationError
    for row in [dict(type='comment',actor_handle='@public',excerpt='word '*26),
                dict(type='dm',actor_handle='@private',excerpt='secret')]:
        with pytest.raises(ValidationError):Notifications.model_validate(dict(rows=[row],sections=[]))

def test_scenarios_auto_use_new_tools():
    root=Path(__file__).resolve().parents[2]
    for filename,name in zip(('instagram_profile_status','discord_status','bilibili_notifications',
        'youtube_notifications','tiktok_notifications','douyin_notifications','muse_threads_read'),NAMES):
        data=json.loads((root/'tools/eval/scenarios'/(filename+'.json')).read_text())
        assert data['manual'] is False and 'manual_reason' not in data
        assert data['steps']==[dict(tool_name=name,args={})]


def test_existing_launcher_proxy_records_local_transaction_and_home_icon(monkeypatch):
    async def check():
        r,t,a,b=reader('youtube.notifications')
        import hidagent.shortcut_workflow as launch
        import hidagent.home_icons as icons
        calls=[]
        async def next_actions(proxy, goal):
            calls.append(goal)
            if len(calls)==1:
                proxy._record(action='wait',workflow='keyboard',phase='spotlight_query')
                return [dict(action='tap',x=500,y=500,workflow='keyboard',phase='icon_launch',
                             _home_icon_app='YouTube',_icon_hitbox=[.45,.45,.55,.55])]
            return [dict(action='done')]
        monkeypatch.setattr(launch,'next_shortcut_actions',next_actions)
        monkeypatch.setattr(icons,'locate_home_icon',lambda *args:dict(x=500,y=500,hitbox=[.45,.45,.55,.55]))
        async def tap(*args,**kw):assert kw['aim'] and kw['pre_click']()
        a.phone.tap.side_effect=tap
        await r.launch()
        a.phone.tap.assert_awaited_once()
        assert len(calls)==2
    asyncio.run(check())


def test_learned_navigation_independent_verdict_and_preclick_guard(monkeypatch):
    async def check():
        r,t,a,b=reader()
        box=[.01,.69,.06,.75]
        hit=dict(box=box,x=.035,y=.72)
        monkeypatch.setattr(r,'control',lambda *args,**kw:hit)
        monkeypatch.setattr(r,'knowledge',lambda:{n:{'box':box} for n in ('home','search','profile')})
        async def tap(*args,**kw):
            assert kw['aim'] and kw['pre_click']()
            monkeypatch.setattr(r,'control',lambda *args,**kw:None)
            assert not kw['pre_click']()
        a.phone.tap.side_effect=tap
        await r.navigate()
        assert a.phone.tap.await_args.args==(.035,.72)
        a.client.evaluate.return_value['answers']['ready']['noul']=.5
        monkeypatch.setattr(r,'control',lambda *args,**kw:hit)
        with pytest.raises(DecisionUnavailable):await r.navigate()
        assert a.phone.tap.await_count==1
    asyncio.run(check())


def test_registered_launch_judge_keeps_its_own_context(monkeypatch):
    async def check():
        r,t,a,b=reader('muse.threads_bot_read')
        import hidagent.shortcut_workflow as launch
        state={'policy':'app_icon_ready','target_app':'Muse','purpose':'Verify foreground'}
        async def next_actions(proxy,goal):
            await proxy.client.evaluate(state, {'ready':{'type':'noul','instructions':'Verify foreground'}}, a.screen.frame)
            return [dict(action='done')]
        monkeypatch.setattr(launch,'next_shortcut_actions',next_actions)
        await r.launch()
        assert a.client.evaluate.await_args.args[0]==state
        assert a.client.evaluate.await_args.args[2]==a.screen.frame
    asyncio.run(check())


@pytest.mark.parametrize('app,payload',[
    ('discord',{'servers':[{'name':'Joe Speaking','unread_badge':False}],'current_channel_header':'announcements'}),
    ('youtube',{'rows':[{'type':'reply','actor_handle':'@public','excerpt':'Thanks for sharing'}],'sections':[]}),
    ('muse',{'cards':[{'target_url':'https://www.threads.net/@public/post/ABC123','author':'@public','comment_text':'Try saying it aloud.'}]})])
def test_other_read_schemas_verified_without_engagement(monkeypatch,app,payload):
    async def check():
        name={'discord':'discord.status','youtube':'youtube.notifications','muse':'muse.threads_bot_read'}[app]
        r,t,a,b=reader(name)
        monkeypatch.setattr(r,'launch',AsyncMock());monkeypatch.setattr(r,'navigate',AsyncMock())
        monkeypatch.setattr(r,'read_view',AsyncMock(return_value=frame()))
        a.actor.chat.return_value=json.dumps(payload)
        out=await r.run()
        assert out['verification_passed'] and not out['published']
        a.phone.tap.assert_not_awaited();a.phone.type.assert_not_awaited();a.phone.key.assert_not_awaited()
    asyncio.run(check())


def test_muse_main_header_never_exposes_body_to_model():
    async def check():
        r,t,a,b=reader('muse.threads_bot_read')
        a.navigation_text_reader.read.return_value={'rows':[dict(text='Main chat',confidence=1.,
            box=dict(left=.5,top=.04,right=.6,bottom=.08))]}
        with pytest.raises(DecisionUnavailable):await r.read_view()
        im=Image.open(io.BytesIO(a.navigation_text_reader.read.await_args.args[0]))
        assert im.getpixel((1000,500))==(0,0,0)
        a.actor.chat.assert_not_awaited();a.client.evaluate.assert_not_awaited()
    asyncio.run(check())


@pytest.mark.parametrize('app,entry',[
    ('instagram','profile'),('bilibili','messages_inbox'),('youtube','notifications'),
    ('tiktok','inbox_messages_and_activity'),('douyin','messages_notifications'),('muse','threads_bot_entry')])
def test_owner_learning_crops_ground_entries_without_device(app,entry):
    from hidagent.scenario_tools import KNOWLEDGE, ANCHORS
    if not (KNOWLEDGE/'apps'/(app+'.json')).exists():pytest.skip('Owner learning evidence not installed')
    r,t,a,b=reader(app+('.threads_bot_read' if app=='muse' else '.profile_status' if app=='instagram' else '.notifications'))
    im=Image.new('RGB',(1646,1080),'black')
    for name in (*ANCHORS[app],entry):
        spec=r.knowledge()[name]
        with Image.open(KNOWLEDGE/spec['crop']) as crop:
            box=tuple(round(v*n) for v,n in zip(spec['box'],(1646,1080,1646,1080)))
            patch=crop.convert('RGB').resize((box[2]-box[0],box[3]-box[1]))
            im.paste(patch,box[:2])
    out=io.BytesIO();im.save(out,format='PNG');a.screen.frame=out.getvalue()
    assert r.control(entry)
    a.screen.frame=frame()
    assert r.control(entry) is None


def test_snapshot_change_after_verification_refuses_result(monkeypatch):
    async def check():
        r,t,a,b=reader();monkeypatch.setattr(r,'launch',AsyncMock());monkeypatch.setattr(r,'navigate',AsyncMock())
        changed=io.BytesIO();Image.new('RGB',(1646,1080),'black').save(changed,format='PNG')
        monkeypatch.setattr(r,'read_view',AsyncMock(side_effect=[frame(),changed.getvalue()]))
        a.actor.chat.return_value=json.dumps(profile())
        with pytest.raises(DecisionUnavailable,match='source changed'):await r.run()
    asyncio.run(check())


def test_combined_inbox_rejects_public_rows_even_if_model_claims_public(monkeypatch):
    async def check():
        r,t,a,b=reader('tiktok.notifications');monkeypatch.setattr(r,'launch',AsyncMock())
        monkeypatch.setattr(r,'navigate',AsyncMock());monkeypatch.setattr(r,'read_view',AsyncMock(return_value=frame()))
        a.actor.chat.return_value=json.dumps(dict(rows=[dict(type='reply',actor_handle='@x',excerpt='secret')],sections=[]))
        with pytest.raises(DecisionUnavailable,match='only locally allowed'):await r.run()
        a.client.evaluate.assert_not_awaited()
    asyncio.run(check())


def test_unknown_launch_view_refuses_without_model_request(monkeypatch):
    async def check():
        r,t,a,b=reader('muse.threads_bot_read')
        monkeypatch.setattr(r,'matches',lambda *args:False)
        with pytest.raises(DecisionUnavailable,match='private image withheld'):await r.launch_view(a.screen.frame)
        a.actor.chat.assert_not_awaited();a.client.evaluate.assert_not_awaited()
    asyncio.run(check())


def test_launch_is_step_bounded(monkeypatch):
    async def check():
        r,t,a,b=reader()
        import hidagent.shortcut_workflow as launch
        next_actions=AsyncMock(return_value=[dict(action='wait')])
        monkeypatch.setattr(launch,'next_shortcut_actions',next_actions)
        with pytest.raises(DecisionUnavailable,match='Bounded launch'):await r.launch()
        assert next_actions.await_count==16
        a.phone.tap.assert_not_awaited();a.phone.type.assert_not_awaited()
    asyncio.run(check())


def test_even_small_source_text_change_refuses_verified_receipt(monkeypatch):
    async def check():
        r,t,a,b=reader();monkeypatch.setattr(r,'launch',AsyncMock());monkeypatch.setattr(r,'navigate',AsyncMock())
        changed=io.BytesIO();im=Image.new('RGB',(1646,1080),'white');im.putpixel((700,80),(0,0,0));im.save(changed,format='PNG')
        monkeypatch.setattr(r,'read_view',AsyncMock(side_effect=[frame(),changed.getvalue()]))
        a.actor.chat.return_value=json.dumps(profile())
        with pytest.raises(DecisionUnavailable,match='source changed'):await r.run()
    asyncio.run(check())


def test_actual_discord_whats_new_x_can_be_masked_without_body(monkeypatch):
    async def check():
        r,t,a,b=reader('discord.status')
        monkeypatch.setattr(r, 'matches', lambda *args: False)
        a.navigation_text_reader.read.return_value={'rows':[
            dict(text="What's New",confidence=1.,box=dict(left=.1,top=.05,right=.2,bottom=.08)),
            dict(text='X',confidence=1.,box=dict(left=.02,top=.05,right=.04,bottom=.08)),
            dict(text='PRIVATE CHANNEL MESSAGE',confidence=1.,box=dict(left=.4,top=.5,right=.9,bottom=.55))]}
        view=await r.launch_view(a.screen.frame)
        assert Image.open(io.BytesIO(view)).getpixel((1000,550))==(0,0,0)
    asyncio.run(check())


def test_existing_llm_response_format_signature_is_supported(monkeypatch):
    async def check():
        r,t,a,b=reader();monkeypatch.setattr(r,'launch',AsyncMock());monkeypatch.setattr(r,'navigate',AsyncMock())
        monkeypatch.setattr(r,'read_view',AsyncMock(return_value=frame()))
        captured=[]
        async def chat(system,text,image=None,max_tokens=600,temperature=0,enforce_token_budget=False,response_format=None):
            captured.append(response_format)
            return json.dumps(profile())
        a.actor=SimpleNamespace(model='existing-llm',chat=chat)
        assert (await r.run())['verification_passed']
        assert captured[0]['type']=='json_schema' and captured[0]['json_schema']['strict']
    asyncio.run(check())


@pytest.mark.parametrize('name',NAMES)
def test_static_frame_and_slow_models_use_fresh_probe_not_absolute_age(monkeypatch,name):
    async def check():
        r,t,a,b=reader(name)
        a.screen.updated=time.time()-30
        wall=[time.time()]
        monkeypatch.setattr(time,'time',lambda:wall[0])
        monkeypatch.setattr(r,'launch',AsyncMock())
        monkeypatch.setattr(r,'navigate',AsyncMock())
        monkeypatch.setattr(r,'read_view',AsyncMock(return_value=frame()))
        payload=(profile() if r.app=='instagram' else
                 {'servers':[],'current_channel_header':'announcements'} if r.app=='discord' else
                 {'cards':[{'target_url':'https://www.threads.net/@public/post/ABC123',
                            'author':'@public','comment_text':'Try saying it aloud.'}]} if r.app=='muse' else
                 {'rows':[],'sections':[]})
        probes=[]
        async def probe(**kwargs):
            assert kwargs=={'force_probe':True}
            wall[0]+=.001
            a.screen.updated=time.time()
            probes.append(a.screen.updated)
        a.phone.ensure_live.side_effect=probe
        async def extract(*args,**kwargs):
            assert probes and a.screen.updated==probes[-1]
            wall[0]+=30  # A model call can outlast a static frame.
            return json.dumps(payload)
        async def verify(*args,**kwargs):
            assert len(probes)>=2 and a.screen.updated==probes[-1]
            wall[0]+=30
            return {'answers':{'ready':{'noul':1.},'blocked':{'noul':0.}},'model':'fake'}
        a.actor.chat.side_effect=extract
        a.client.evaluate.side_effect=verify
        assert (await r.run())['verification_passed']
        a.phone.tap.assert_not_awaited();a.phone.key.assert_not_awaited();a.phone.type.assert_not_awaited()
    asyncio.run(check())


@pytest.mark.parametrize('name',NAMES)
def test_home_enters_real_registered_launcher_before_scenario_privacy(monkeypatch,name):
    async def check():
        r,t,a,b=reader(name)
        import hidagent.shortcut_workflow as launch
        import hidagent.spotlight_launch as spotlight
        import hidagent.home_icons as icons
        import hidagent.foreground_reuse as foreground
        monkeypatch.setattr(launch,'launch_layout_guard',AsyncMock(return_value=None))
        monkeypatch.setattr(spotlight,'prepare',AsyncMock(return_value=None))
        monkeypatch.setattr(icons,'locate_home_icon',lambda *args:None)
        monkeypatch.setattr(icons,'has_home_icon',lambda *args:False)
        monkeypatch.setattr(foreground,'reuse_foreground',AsyncMock(return_value=None))
        # Home has no target-app anchors. A scenario mask must not preempt launch.
        monkeypatch.setattr(r,'matches',lambda *args:False)
        home=a.screen.frame
        app_image=io.BytesIO();Image.new('RGB',(1646,1080),'black').save(app_image,format='PNG')
        async def key(combo):
            if combo=='cmd+h':a.screen.frame=app_image.getvalue()
        a.phone.key.side_effect=key
        from hidagent.scenario_tools import APPS
        async def judge(state,questions,image=None):
            answers={key:{'noul':0. if key in ('blocked','loading') else 1.} for key in questions}
            if state.get('purpose')=='verify compact iPad Spotlight overlay':
                answers['ready']={'noul':0.}
            if 'page' in questions:
                answers['page']={'choice':'other' if image==home else 'app','confidence':1.}
            if 'visible_app' in questions:
                answers['visible_app']={'choice':APPS[r.app],'confidence':1.}
            return {'answers':answers,'model':'fake','seconds':0.,'approved':True}
        # Use the registered launcher's actual decisions, not a stubbed done action.
        a.client.evaluate.side_effect=judge
        await r.launch()
        assert a.phone.key.await_args_list[0].args==('cmd+h',)
        assert r.phases[-1]['phase']=='launch'
        assert a.client.evaluate.await_args_list[0].args[2]==home
        a.actor.chat.assert_not_awaited();a.phone.type.assert_not_awaited();a.phone.tap.assert_not_awaited()
        # After launch, an unknown app frame still cannot enter a scenario model.
        a.client.evaluate.reset_mock()
        with pytest.raises(DecisionUnavailable):
            if r.app=='discord':await r.read_view()
            else:await r.navigate()
        a.client.evaluate.assert_not_awaited()
    asyncio.run(check())


@pytest.mark.parametrize('failed_probe',[1,2,3])
def test_missing_probe_frame_stops_before_next_model(monkeypatch,failed_probe):
    async def check():
        r,t,a,b=reader()
        monkeypatch.setattr(r,'launch',AsyncMock())
        monkeypatch.setattr(r,'navigate',AsyncMock())
        monkeypatch.setattr(r,'read_view',AsyncMock(return_value=frame()))
        a.actor.chat.return_value=json.dumps(profile())
        probes=0
        async def probe(**kwargs):
            nonlocal probes
            probes+=1
            if probes!=failed_probe:a.screen.updated=time.time()
        a.phone.ensure_live.side_effect=probe
        with pytest.raises(DecisionUnavailable,match='newer than the pointer probe'):await r.run()
        assert probes==failed_probe
        assert a.actor.chat.await_count==(1 if failed_probe==3 else 0)
        a.client.evaluate.assert_not_awaited();a.phone.tap.assert_not_awaited()
    asyncio.run(check())


@pytest.mark.parametrize('change',['link','screen','stop'])
def test_probe_response_rechecks_ownership_before_models(monkeypatch,change):
    async def check():
        r,t,a,b=reader()
        monkeypatch.setattr(r,'launch',AsyncMock())
        async def probe(**kwargs):
            a.screen.updated=time.time()
            if change=='link':a.phone.device.links+=1
            if change=='screen':a.screen.live=False
            if change=='stop':r.active=lambda:(_ for _ in ()).throw(asyncio.CancelledError())
        a.phone.ensure_live.side_effect=probe
        with pytest.raises(asyncio.CancelledError if change=='stop' else DecisionUnavailable):await r.run()
        a.actor.chat.assert_not_awaited();a.client.evaluate.assert_not_awaited()
        r.launch.assert_not_awaited()
    asyncio.run(check())
