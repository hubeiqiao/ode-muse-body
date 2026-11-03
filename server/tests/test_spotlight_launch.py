"""Offline privacy/delivery contracts; synthetic images plus one optional evidence check."""
import asyncio
import io
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
import pytest
from PIL import Image, ImageDraw
from hidagent.device import DeviceError
from hidagent.hid_lease import leased_input
from hidagent.shortcut_workflow import next_shortcut_actions

GOAL={'app':'YouTube','text':None}

def picture(kind='home',size=(710,1080),edge=30):
    im=Image.new('RGB',size,(240,)*3 if kind=='app' else (35,)*3);d=ImageDraw.Draw(im)
    if kind=='open':d.rectangle((90,76,620,132),fill=(35+edge,)*3)
    if kind=='home':
        for x in range(60,700,100):
            for y in range(150,1000,160):d.rectangle((x,y,x+40,y+40),fill=(150,90,80))
    out=io.BytesIO();im.save(out,format='PNG');return out.getvalue()

@pytest.fixture
def setup(monkeypatch):
    try:
        from hidagent import spotlight_launch as sl
    except ImportError:
        sl=NS(local_check=lambda frame:{'state':'unknown'})
    monkeypatch.setenv('APP_LAUNCH_SPOTLIGHT_FIRST','1')
    s=NS(sl=sl,t=100.,calls=[],samples=[],after_enter=[picture('app')],after_esc=[picture()])
    monkeypatch.setattr(sl,'_now',lambda:s.t,raising=False);monkeypatch.setattr(sl,'_wall',lambda:s.t,raising=False)
    screen=NS(frame=picture(),live=True,updated=s.t)
    s.target=dict(device='ipad',orientation='portrait',width=710,height=1080,calibration_key='ipad-portrait-66')
    phone=NS(target_info=lambda:dict(s.target),calibrated=True,device=NS(connected=True,links=1))
    def update(frame):screen.frame=frame;screen.updated=s.t
    s.update=update
    async def sleep(seconds):
        s.t+=seconds
        if s.samples:update(s.samples.pop(0))
        else:screen.updated=s.t
    monkeypatch.setattr(sl,'_sleep',sleep,raising=False)
    async def key(combo):
        s.calls.append(combo);s.t+=.1
        if combo=='cmd+space':update(picture('open'))
        if combo=='enter':s.samples.extend(s.after_enter)
        if combo=='esc':s.samples.extend(s.after_esc)
    async def type_text(text):s.calls.append(('type',text));s.t+=.1;update(picture('open'))
    phone.key=AsyncMock(side_effect=key);phone.type=AsyncMock(side_effect=type_text)
    async def judge(state,questions,frame):
        assert sl.local_check(frame)['state']=='closed','Private panel reached model'
        if 'page' in questions:
            return {'answers': {'page': {'choice': 'other', 'confidence': 1}}, 'seconds': .1}
        return {'answers':{'ready':{'noul':1},'blocked':{'noul':0},'loading':{'noul':0}},'seconds':.1}
    s.a=NS(phone=phone,screen=screen,steps=[],client=NS(evaluate=AsyncMock(side_effect=judge)),trace=Mock())
    def record(shot=None,**step):
        assert shot is None
        s.a.steps.append(step);s.a.trace.write(kind='step',**step)
    s.a._record=record
    monkeypatch.setattr('hidagent.home_icons.locate_home_icon',lambda *args:None)
    monkeypatch.setattr('hidagent.home_icons.home_grid_witness',lambda *args:False)
    return s

def run(a,goal=GOAL):
    async def invoke():
        with leased_input(lambda:None):return await next_shortcut_actions(a,goal)
    return asyncio.run(invoke())

def test_sequence_private_spy_and_receipt(setup):
    s=setup;action=run(s.a)[0]
    assert s.calls==['cmd+space',('type','YouTube'),'enter']
    assert action['action']=='done' and action['launch_method']=='spotlight_first'
    r=action['launch_receipt']
    assert {'open_s','type_s','enter_s','close_wait_s','total_s'}<=r['stages_s'].keys()
    assert r['local_checks'] and r['fallback_reason'] is None
    assert s.a.client.evaluate.await_count==1
    for c in s.a.trace.write.call_args_list:
        if c.args:assert s.sl.local_check(c.args[0])['state']=='closed'

@pytest.mark.parametrize('edge,state',[(30,'open'),(8,'unknown'),(0,'closed')])
def test_detector_margin(setup,edge,state):assert setup.sl.local_check(picture('open',edge=edge))['state']==state

@pytest.mark.parametrize('frame',[b'bad',picture(size=(1080,710)),picture(size=(710,300)),picture(size=(498,1080))])
def test_unknown_geometry(setup,frame):assert setup.sl.local_check(frame)['state']=='unknown'

@pytest.mark.parametrize('delivery',['unknown','not_sent'])
def test_uncertain_type_no_retype(setup,delivery):
    s=setup
    async def uncertain(text):
        s.calls.append(('type',text));raise DeviceError('lost receipt',delivery=delivery,command_name='type')
    s.a.phone.type.side_effect=uncertain
    first=run(s.a)[0];second=run(s.a)[0]
    assert first['action']==second['action']=='fail' and 'uncertain' in first['reason']
    assert s.a.phone.type.await_count==1 and 'enter' not in s.calls
    s.a.client.evaluate.assert_not_awaited()
    assert first['_shot']!=picture('open')

@pytest.mark.parametrize('esc_closes,home',[(False,False),(True,False),(True,True)])
def test_timeout_esc_once_closed_only_fallback(setup,monkeypatch,esc_closes,home):
    s=setup;s.after_enter[:]=[picture('open')];s.after_esc[:]=[picture() if esc_closes else picture('open')]
    monkeypatch.setattr('hidagent.home_icons.home_grid_witness',lambda *args:home)
    icon={'x':200,'y':300,'hitbox':[.1,.2,.3,.4]}
    monkeypatch.setattr('hidagent.home_icons.locate_home_icon',lambda *args:icon if home else None)
    a=run(s.a)[0]
    assert s.calls.count('esc')==1 and s.a.phone.type.await_count==1
    if home:
        assert (a['action'],a['phase'])==('tap','icon_launch')
        assert a['launch_receipt']['fallback_reason']=='spotlight_close_timeout'
    else:
        assert a['action']=='fail'
        assert s.a.client.evaluate.await_count == int(esc_closes)
        assert run(s.a)[0]['action']=='fail' and s.calls.count('esc')==1

@pytest.mark.parametrize('frame',[picture(),picture('open',edge=8)])
def test_unchanged_or_ambiguous_close_no_judge(setup,frame):
    s=setup;s.after_enter[:]=[frame];s.after_esc[:]=[picture('open',edge=8)]
    assert run(s.a)[0]['action']=='fail' and s.calls.count('esc')==1
    s.a.client.evaluate.assert_not_awaited()

def test_verify_open_before_type(setup):
    s=setup
    async def no_open(combo):s.calls.append(combo);s.t+=.1;s.update(picture())
    s.a.phone.key.side_effect=no_open
    action = run(s.a)[0]
    assert action['action']=='key' and action['combo']=='cmd+h'
    assert s.calls==['cmd+space','esc']
    s.a.phone.type.assert_not_awaited();s.a.client.evaluate.assert_awaited_once()

@pytest.mark.parametrize('mode',['flag_off','unknown_app','non_ascii'])
def test_legacy_sequence_unchanged(setup,monkeypatch,mode):
    s=setup;goal=GOAL.copy()
    if mode=='flag_off':monkeypatch.delenv('APP_LAUNCH_SPOTLIGHT_FIRST')
    if mode=='unknown_app':goal['app']='Unregistered App'
    if mode=='non_ascii':
        from hidagent.shortcut_workflow import APP_ALIASES
        monkeypatch.setitem(APP_ALIASES,'YouTube',('非ASCII',))
    monkeypatch.setattr('hidagent.home_icons.locate_home_icon',lambda *args:{'x':200,'y':300,'hitbox':[.1,.2,.3,.4]})
    a=run(s.a,goal)[0]
    assert (a['action'],a['phase'])==('tap','icon_launch')
    assert s.calls==[] and 'launch_method' not in a;s.a.client.evaluate.assert_not_awaited()

@pytest.mark.parametrize('bad',['stale','not_live','uncalibrated','disconnected','geometry','no_lease'])
def test_preconditions_no_input(setup,bad):
    s=setup
    if bad=='stale':s.a.screen.updated=90
    if bad=='not_live':s.a.screen.live=False
    if bad=='uncalibrated':s.a.phone.calibrated=False
    if bad=='disconnected':s.a.phone.device.connected=False
    if bad=='geometry':s.target['width']=711
    acts=asyncio.run(next_shortcut_actions(s.a,GOAL)) if bad=='no_lease' else run(s.a)
    assert acts[0]['action']=='fail' and s.calls==[];s.a.client.evaluate.assert_not_awaited()

def test_search_gate_before_learned_model(setup,monkeypatch):
    from hidagent.app_search import next_search_actions
    s=setup;monkeypatch.setattr('hidagent.app_search.public_search_control',lambda *args,**kw:{'control':'public_search_entry'})
    async def invoke():
        with leased_input(lambda:None):return await next_search_actions(s.a,{'app':'YouTube','query':'IELTS'})
    a=asyncio.run(invoke())[0]
    assert (a['action'],a['phase'])==('wait','app_ready')
    assert s.calls==['cmd+space',('type','YouTube'),'enter'] and s.a.client.evaluate.await_count==1

def test_reopened_panel_not_uploaded(setup):
    s=setup;run(s.a);s.update(picture('open'))
    assert run(s.a)[0]['action']=='fail' and s.a.client.evaluate.await_count==1
    assert s.a.phone.type.await_count==1

def test_new_task_new_attempt(setup):
    s=setup;run(s.a);s.a.steps=[];s.update(picture())
    assert run(s.a)[0]['action']=='done' and s.a.phone.type.await_count==2


def test_search_ready_reopened_panel_no_model(setup):
    from hidagent.app_search import next_search_actions
    s=setup;run(s.a);s.a.steps.append({'workflow':'app_search','phase':'app_ready'})
    s.update(picture('open'))
    async def invoke():
        with leased_input(lambda:None):return await next_search_actions(s.a,{'app':'YouTube','query':'IELTS'})
    out=asyncio.run(invoke())[0]
    assert out['action']=='fail'
    assert s.a.client.evaluate.await_count==1


def test_geometry_change_after_type_no_enter_or_model(setup):
    s=setup
    async def rotate(text):
        s.calls.append(('type',text));s.target.update(width=1080,height=710,orientation='landscape')
        s.update(picture('app',size=(1080,710)))
    s.a.phone.type.side_effect=rotate
    assert run(s.a)[0]['action']=='fail' and 'enter' not in s.calls
    s.a.client.evaluate.assert_not_awaited()


def test_cancelled_lease_cannot_send_or_upload(setup):
    s=setup
    async def invoke():
        def revoked():raise asyncio.CancelledError()
        with leased_input(revoked):return await next_shortcut_actions(s.a,GOAL)
    with pytest.raises(asyncio.CancelledError):asyncio.run(invoke())
    assert not s.calls;s.a.client.evaluate.assert_not_awaited()


def test_cancel_during_type_keeps_no_replay_state(setup):
    s=setup
    async def cancel(text):raise asyncio.CancelledError()
    s.a.phone.type.side_effect=cancel
    with pytest.raises(asyncio.CancelledError):run(s.a)
    assert run(s.a)[0]['action']=='fail' and s.a.phone.type.await_count==1
    s.a.client.evaluate.assert_not_awaited()


def test_flag_changed_mid_attempt_cannot_disable_privacy_guard(setup,monkeypatch):
    s=setup;run(s.a);monkeypatch.setenv('APP_LAUNCH_SPOTLIGHT_FIRST','0');s.update(picture('open'))
    assert run(s.a)[0]['action']=='fail' and s.a.client.evaluate.await_count==1


def bridge_receipt(a):
    from hidagent.live_voice import VoiceBridge
    state=lambda:dict(agent={'steps':a.steps,'running':False,'task':'Open YouTube'},
                      device=True,screen=True,calibrated=True)
    return VoiceBridge(state,None,None).snapshot(raw=True)


def test_bridge_exposes_uncertain_launch_typing(setup):
    s=setup
    s.a.phone.type.side_effect=DeviceError('unknown',command_name='type')
    out=run(s.a)[0];s.a.steps.append({k:v for k,v in out.items() if k!='_shot'})
    r=bridge_receipt(s.a)
    assert r['input_was_sent'] and r['never_retry_typing']


def test_bridge_exposes_confirmed_launch_typing(setup):
    s=setup;out=run(s.a)[0];s.a.steps.append({k:v for k,v in out.items() if k!='_shot'})
    r=bridge_receipt(s.a)
    assert r['input_was_sent'] and not r['never_retry_typing']


@pytest.mark.parametrize('app,native', [('Reddit',False),('rednote',True)])
def test_existing_browser_route_precedence(setup,monkeypatch,app,native):
    from hidagent.app_search import next_search_actions
    s=setup;route=AsyncMock(return_value=[{'action':'wait','phase':'browser_route'}])
    if native:
        monkeypatch.setenv('APP_PUBLIC_SEARCH_NATIVE_LINK','1')
        monkeypatch.setattr('hidagent.native_public_search.next_native_public_search_actions',route)
    else:monkeypatch.setattr('hidagent.reddit_web.next_reddit_web_actions',route)
    async def invoke():
        with leased_input(lambda:None):return await next_search_actions(s.a,{'app':app,'query':'IELTS'})
    assert asyncio.run(invoke())[0]['phase']=='browser_route'
    route.assert_awaited_once();assert not s.calls


def test_redirected_browser_private_panel_guard(setup,monkeypatch):
    from hidagent.app_search import next_search_actions
    s=setup;run(s.a,{'app':'Chrome','text':None});s.update(picture('open'))
    route=AsyncMock(return_value=[{'action':'wait','phase':'browser_route'}])
    monkeypatch.setattr('hidagent.reddit_web.next_reddit_web_actions',route)
    async def invoke():
        with leased_input(lambda:None):return await next_search_actions(s.a,{'app':'Reddit','query':'IELTS'})
    assert asyncio.run(invoke())[0]['action']=='fail'
    route.assert_not_awaited()


def landscape_picture(kind='home', edge=30, size=(1646, 1080), shoulders=True, typed=False):
    """Synthetic top capsule with content deliberately outside the detector band."""
    im = Image.new('RGB', size, (240,) * 3 if kind == 'app' else (140,) * 3)
    draw = ImageDraw.Draw(im)
    if kind == 'open':
        bounds = (389, 117, 1257, 210) if shoulders else (0, 117, 1645, 210)
        draw.rounded_rectangle(bounds, radius=38, fill=(140-edge,) * 3)
        if edge and shoulders:
            draw.ellipse((1195, 145, 1225, 175), fill='white')
            draw.rectangle((480, 145, 560 if typed else 640, 175), fill='white')
    out = io.BytesIO()
    im.save(out, format='PNG')
    return out.getvalue()


@pytest.fixture
def landscape_setup(setup):
    s = setup
    s.target.update(orientation='landscape', width=1646, height=1080,
                    calibration_key='ipad-landscape-66')
    s.update(landscape_picture())
    s.after_enter[:] = [landscape_picture('app')]
    s.after_esc[:] = [landscape_picture()]

    async def key(combo):
        s.calls.append(combo)
        s.t += .1
        if combo == 'cmd+space': s.update(landscape_picture('open'))
        if combo == 'enter': s.samples.extend(s.after_enter)
        if combo == 'esc': s.samples.extend(s.after_esc)

    async def type_text(text):
        s.calls.append(('type', text))
        s.t += .1
        s.update(landscape_picture('open', typed=True))

    s.a.phone.key.side_effect = key
    s.a.phone.type.side_effect = type_text
    return s


@pytest.mark.parametrize('edge,state', [(30, 'unknown'), (8, 'unknown'), (0, 'closed')])
def test_landscape_detector_three_way(setup, edge, state):
    assert setup.sl.local_check(landscape_picture('open', edge))['state'] == state


def test_landscape_full_width_app_bar_is_closed(setup):
    assert setup.sl.local_check(landscape_picture('open', shoulders=False))['state'] == 'closed'


@pytest.mark.parametrize('size', [(1645, 1080), (1646, 1079), (1646, 160), (1080, 710)])
def test_landscape_unmeasured_geometry_stays_unknown(setup, size):
    assert setup.sl.local_check(landscape_picture(size=size))['state'] == 'unknown'


def test_landscape_eligible_only_measured_ipad(landscape_setup):
    s = landscape_setup
    assert s.sl.eligible(s.a, GOAL)
    for changes in ({'device': 'iphone'}, {'width': 1645}, {'height': 1079},
                    {'orientation': 'unknown'}):
        original = s.target.copy()
        s.target.update(changes)
        assert not s.sl.eligible(s.a, GOAL)
        s.target.update(original)
    assert not s.sl.eligible(s.a, {'app': 'YouTube', 'text': 'hello'})
    s.a.steps.append({'workflow': 'keyboard'})
    assert not s.sl.eligible(s.a, GOAL)


def test_landscape_sequence_privacy_and_no_replay(landscape_setup, monkeypatch):
    s = landscape_setup
    action = run(s.a)[0]
    assert action['action'] == 'done'
    assert action['launch_method'] == 'spotlight_first'
    assert s.calls == ['cmd+space', ('type', 'YouTube'), 'enter']
    s.a.client.evaluate.assert_awaited_once()
    # Trace contains receipts only, including no image nested in an action.
    assert all(not isinstance(v, bytes) for c in s.a.trace.write.call_args_list for v in c.kwargs.values())
    monkeypatch.setenv('APP_LAUNCH_SPOTLIGHT_FIRST', '0')
    s.update(landscape_picture('open'))
    assert run(s.a)[0]['action'] == 'fail'
    assert s.a.phone.type.await_count == 1
    s.a.client.evaluate.assert_awaited_once()


@pytest.mark.parametrize('mode', ['open', 'unknown'])
def test_landscape_private_preflight_esc_before_legacy(landscape_setup, mode):
    s = landscape_setup
    s.update(landscape_picture('open', 30 if mode == 'open' else 8))
    action = run(s.a)[0]
    assert action['action'] == 'key' and action['combo'] == 'cmd+h'
    assert s.calls == ['esc'] and action['_shot'] == s.a.screen.frame
    s.a.client.evaluate.assert_awaited_once()


@pytest.mark.parametrize('delivery', ['unknown', 'not_sent'])
def test_landscape_uncertain_type_never_retypes(landscape_setup, delivery):
    s = landscape_setup
    s.a.phone.type.side_effect = DeviceError('lost receipt', delivery=delivery, command_name='type')
    assert run(s.a)[0]['action'] == run(s.a)[0]['action'] == 'fail'
    assert s.a.phone.type.await_count == 1
    assert s.calls == ['cmd+space']
    s.a.client.evaluate.assert_not_awaited()


@pytest.mark.parametrize('close_kind,edge', [('open', 30), ('open', 8), ('home', 30)])
def test_landscape_unsafe_close_esc_once_no_upload(landscape_setup, close_kind, edge):
    s = landscape_setup
    s.after_enter[:] = [landscape_picture(close_kind, edge)]
    s.after_esc[:] = [landscape_picture('open', 8)]
    first = run(s.a)[0]
    assert first['action'] == run(s.a)[0]['action'] == 'fail'
    assert s.calls.count('esc') == 1 and s.a.phone.type.await_count == 1
    assert first['_shot'] != s.a.screen.frame
    s.a.client.evaluate.assert_not_awaited()


def test_landscape_rotation_after_type_no_enter_or_upload(landscape_setup):
    s = landscape_setup

    async def rotate(text):
        s.calls.append(('type', text))
        s.target.update(width=710, height=1080, orientation='portrait')
        s.update(picture('open'))

    s.a.phone.type.side_effect = rotate
    assert run(s.a)[0]['action'] == 'fail'
    assert 'enter' not in s.calls and s.a.phone.type.await_count == 1
    s.a.client.evaluate.assert_not_awaited()


@pytest.mark.parametrize('edge,state', [(4, 'unknown'), (5, 'unknown'), (11, 'unknown'), (12, 'unknown')])
def test_landscape_threshold_boundaries(setup, edge, state):
    assert setup.sl.local_check(landscape_picture('open', edge))['state'] == state


def test_landscape_partial_capsule_is_unknown(setup):
    with Image.open(io.BytesIO(landscape_picture('open'))) as im:
        ImageDraw.Draw(im).rectangle((1190, 130, 1290, 159), fill=(140,) * 3)
        out = io.BytesIO()
        im.save(out, format='PNG')
    assert setup.sl.local_check(out.getvalue())['state'] == 'unknown'


def test_landscape_only_top_band_affects_check(setup):
    frame = landscape_picture('open')
    with Image.open(io.BytesIO(frame)) as im:
        ImageDraw.Draw(im).rectangle((0, 220, 1645, 1079), fill='white')
        out = io.BytesIO()
        im.save(out, format='PNG')
    assert setup.sl.local_check(out.getvalue()) == setup.sl.local_check(frame)
    assert setup.sl.local_check(frame, before=landscape_picture())['state'] == 'open'


def test_optional_older_short_crops_never_authorize_upload():
    """Old labels are unreliable and the crops lack the new lower shape band."""
    import os
    from pathlib import Path
    from hidagent.spotlight_launch import landscape_capsule_check, local_check
    root = Path(os.environ.get('SPOTLIGHT_LANDSCAPE_EVIDENCE',
        'runtime/local-evidence/'
        'outputs/current-goal-20261028/evidence')) / 'landscape-spotlight-20261029'
    paths = list(root.glob('*.png'))
    if not paths:
        pytest.skip('Optional older evidence unavailable')
    for path in paths:
        frame = path.read_bytes()
        assert local_check(frame)['state'] == 'unknown'
        with pytest.raises(ValueError, match='Insufficient landscape capsule region'):
            landscape_capsule_check(frame)
