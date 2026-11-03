import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from hidagent.phone import Phone,PhoneUnavailable

def phone(tmp_path,monkeypatch,family='ipad',found=(.5,.5)):
 monkeypatch.setenv('IPAD_VISUAL_REACQUIRE','1')
 p=Phone(SimpleNamespace(links=1),tmp_path/'c.json',SimpleNamespace(live=True))
 p.span={'w':1000,'h':1000};p._link=1
 p.target_info=lambda:{'device':family};p._sync_profile=lambda:False
 p._send=AsyncMock();p._find_pointer=AsyncMock(return_value=found);p._move_xy=AsyncMock()
 return p

def test_observed_home_pointer_avoids_corner_but_keeps_calibrated_move(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch)
 asyncio.run(p._move_to(700,700,reacquire=True))
 p._find_pointer.assert_awaited_once_with(known_span=True)
 p._send.assert_not_awaited();p._move_xy.assert_awaited_once_with(200,200,point_move=True)
 assert p._pos==(700,700)

@pytest.mark.parametrize('found',[None,(.99,.5),(.5,.01)])
def test_missing_or_edge_pointer_falls_back_to_existing_home(tmp_path,monkeypatch,found):
 p=phone(tmp_path,monkeypatch,found=found)
 asyncio.run(p._move_to(700,700,reacquire=True))
 assert p._send.await_args_list[0].args==('home',)

@pytest.mark.parametrize('case',['opt_out','iphone','known_position','ordinary_tap'])
def test_reacquire_never_applies_outside_explicit_unknown_ipad_scope(tmp_path,monkeypatch,case):
 p=phone(tmp_path,monkeypatch,family='iphone' if case=='iphone' else 'ipad')
 if case=='opt_out':monkeypatch.delenv('IPAD_VISUAL_REACQUIRE')
 if case=='known_position':p._pos=(100,100)
 asyncio.run(p._move_to(700,700,reacquire=case!='ordinary_tap'))
 p._find_pointer.assert_not_awaited()

def test_rotation_during_probe_does_not_move_or_click_using_old_map(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch);checks=iter([False,True]);p._sync_profile=lambda:next(checks)
 with pytest.raises(PhoneUnavailable):asyncio.run(p._move_to(700,700,reacquire=True))
 p._move_xy.assert_not_awaited();p._send.assert_not_awaited()

def test_reacquire_cv_time_is_not_miscounted_as_final_aim(tmp_path,monkeypatch):
 import time
 from hidagent import phone as module
 p=phone(tmp_path,monkeypatch);p.screen.fresh=AsyncMock(return_value=b'after');p._active_point_timing={}
 def detect(*args,**kwargs):
  kwargs['diagnostics'].update(change_span=[25,18],changed_pixels=150);return (.5,.5)
 monkeypatch.setattr(module.vision,'pointer',detect)
 found=asyncio.run(p._locate_after_nudge(b'before',time.time(),(.5,.5),.01,timing_prefix='reacquire'))
 assert found==(.5,.5) and 'reacquire_cv_s' in p._active_point_timing
 assert 'aim_cv_s' not in p._active_point_timing


@pytest.mark.parametrize('family,combo,invalidated',[
 ('ipad','cmd+h',True),('ipad','command+h',True),('ipad','cmd+a',False),('iphone','cmd+h',False)])
def test_system_home_does_not_carry_app_pointer_position_into_next_grid(tmp_path,monkeypatch,family,combo,invalidated):
 p=phone(tmp_path,monkeypatch,family=family);p._pos=(100,200);p.ensure_keyboard_live=AsyncMock()
 asyncio.run(p.key(combo))
 assert (p._pos is None)==invalidated
 if invalidated:
  asyncio.run(p._move_to(700,700,reacquire=True))
  p._find_pointer.assert_awaited_once_with(known_span=True)
  p._move_xy.assert_awaited_once_with(200,200,point_move=True)


def test_home_delivery_error_also_discards_position_instead_of_trusting_old_app(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch);p._pos=(100,200);p.ensure_keyboard_live=AsyncMock();p._send=AsyncMock(side_effect=RuntimeError('lost ACK'))
 with pytest.raises(RuntimeError):asyncio.run(p.key('cmd+h'))
 assert p._pos is None


def test_second_home_requires_new_picture_and_is_bounded(monkeypatch):
 import time,io
 from PIL import Image
 from hidagent.shortcut_workflow import next_shortcut_actions
 from hidagent import home_icons
 monkeypatch.setattr(home_icons,'locate_home_icon',lambda *a,**kw:None)
 monkeypatch.setattr(home_icons,'has_home_icon',lambda *a:True)
 buf=io.BytesIO();Image.new('RGB',(710,1080),'white').save(buf,format='PNG');frame=buf.getvalue()
 t=time.time()-1
 step=dict(workflow='keyboard',phase='normalize_ipad_home',act_s=.1,t=t)
 a=SimpleNamespace(screen=SimpleNamespace(frame=frame,updated=t+1),steps=[step],shots={0:b'actual prior picture'},phone=SimpleNamespace(target_info=lambda:{'device':'ipad'}),trace=SimpleNamespace(write=lambda **kw:None))
 out=asyncio.run(next_shortcut_actions(a,{'app':'App Store','text':None}))
 assert out[0]['combo']=='cmd+h' and out[0]['phase']=='collapse_home_group'
 # Once consumed, the finite normalization path waits; never repeats Home.
 a.steps.append(dict(workflow='keyboard',phase='collapse_home_group',act_s=.1,t=t+.1))
 out=asyncio.run(next_shortcut_actions(a,{'app':'App Store','text':None}))
 assert out[0]['action']=='wait'
 # An unchanged old picture cannot authorize this extra command.
 a.steps=[step];a.shots[0]=frame
 out=asyncio.run(next_shortcut_actions(a,{'app':'App Store','text':None}))
 assert out[0]['action']=='wait'


def test_rejected_observed_home_candidate_uses_corner_once_with_same_aim(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch);p.ensure_live=AsyncMock()
 async def position(*args,**kw):p._active_point_timing['position_source']='observed_pointer';p._pos=(700,700)
 p._move_to=AsyncMock(side_effect=position);p._aim=AsyncMock(side_effect=[PhoneUnavailable('pointer is too far from the target; recalibrate before retrying'),None])
 asyncio.run(p.point(.7,.7,aim=True,reacquire=True))
 assert p._move_to.await_args_list[-1].kwargs=={'reacquire':False}
 assert p._aim.await_count==2 and p.last_point_timing['outcome']=='ok'
 assert p.last_point_timing['home_reacquire_recovery']=='rejected_global_candidate_use_corner_once'


@pytest.mark.parametrize('cause',['ordinary','iphone','not_observed','different_error'])
def test_home_corner_recovery_cannot_become_general_blind_retry(tmp_path,monkeypatch,cause):
 p=phone(tmp_path,monkeypatch,family='iphone' if cause=='iphone' else 'ipad');p.ensure_live=AsyncMock()
 async def position(*args,**kw):
  if cause!='not_observed':p._active_point_timing['position_source']='observed_pointer'
 p._move_to=AsyncMock(side_effect=position);p._aim=AsyncMock(side_effect=PhoneUnavailable('no fresh mirror frame' if cause=='different_error' else 'pointer is too far'))
 with pytest.raises(PhoneUnavailable):asyncio.run(p.point(.7,.7,aim=True,reacquire=cause!='ordinary'))
 assert p._move_to.await_count==1 and p._aim.await_count==1


def test_corner_recovery_failure_is_terminal_not_a_retry_loop(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch);p.ensure_live=AsyncMock()
 async def position(*args,**kw):p._active_point_timing['position_source']='observed_pointer'
 p._move_to=AsyncMock(side_effect=position);p._aim=AsyncMock(side_effect=PhoneUnavailable('pointer is too far'))
 with pytest.raises(PhoneUnavailable):asyncio.run(p.point(.7,.7,aim=True,reacquire=True))
 assert p._move_to.await_count==2 and p._aim.await_count==2


@pytest.mark.parametrize('pixels,span,accepted',[(43,[16,14],False),(1469,[77,74],False),(233,[25,18],True)])
def test_home_reacquisition_rejects_transition_specks_and_hovered_icons(tmp_path,monkeypatch,pixels,span,accepted):
 import time
 from hidagent import phone as module
 p=phone(tmp_path,monkeypatch);p.screen.fresh=AsyncMock(return_value=b'after')
 monkeypatch.setattr(module,'POINTER_WAIT',.001)
 def detect(*args,**kw):kw['diagnostics'].update(changed_pixels=pixels,change_span=span);return (.5,.5)
 monkeypatch.setattr(module.vision,'pointer',detect)
 found=asyncio.run(p._locate_after_nudge(b'before',time.time(),(.5,.5),.01,timing_prefix='reacquire'))
 assert bool(found)==accepted
