import asyncio,time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from hidagent.phone import Phone


def phone(tmp_path,monkeypatch):
 monkeypatch.setenv('IPAD_HOME_PROBE_HINT','1')
 p=Phone(SimpleNamespace(links=1,connected=True),tmp_path/'c.json',SimpleNamespace(live=True,frame=b'frame'))
 p.span={'w':1000,'h':1500};profile='ipad-portrait-66';p._profile_key=p._calibration_key=profile
 p.target={'device':'ipad','orientation':'portrait','width':710,'height':1080,'calibration_key':'ipad-portrait-66'}
 p.target_info=lambda:p.target;p._sync_profile=lambda:False;p._send=AsyncMock();p.ensure_live=AsyncMock();p.ensure_keyboard_live=AsyncMock()
 p.last_pointer_check={'observed':[.4,.65],'target':[.4,.65],'error':.002}
 return p


def test_hint_is_not_commanded_position_and_needs_same_actual_proof(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch);p._pos=(900,100)
 p._remember_verified_home_pointer(dict(p.last_pointer_check))
 assert p._eligible_home_probe_hint() is None
 p._remember_verified_home_pointer(p.last_pointer_check)
 assert p._eligible_home_probe_hint()==(.4,.65) and p._pos==(900,100)
 asyncio.run(p.key('cmd+h'));assert p._pos is None and p._eligible_home_probe_hint()==(.4,.65)


@pytest.mark.parametrize('change',['link','orientation','size','key','span','axis','stale','disconnected'])
def test_changed_binding_or_expiry_discards_hint(tmp_path,monkeypatch,change):
 p=phone(tmp_path,monkeypatch);p._remember_verified_home_pointer(p.last_pointer_check)
 if change=='link':p.device.links+=1
 if change=='orientation':p.target['orientation']='landscape'
 if change=='size':p.target['width']=711
 if change=='key':p.target['calibration_key']='other'
 if change=='span':p.span['w']=999
 if change=='axis':p.axes['x']=-1
 if change=='stale':p._verified_home_probe_hint['at']-=121
 if change=='disconnected':p.device.connected=False
 assert p._eligible_home_probe_hint() is None and p._verified_home_probe_hint is None


def test_stop_and_manual_invalidation_remove_home_hint(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch);p._remember_verified_home_pointer(p.last_pointer_check)
 asyncio.run(p.release_input());assert p._eligible_home_probe_hint() is None
 p._remember_verified_home_pointer(p.last_pointer_check);p.forget_position();assert p._eligible_home_probe_hint() is None


def test_hint_default_off_never_records_or_changes_probe(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch);monkeypatch.delenv('IPAD_HOME_PROBE_HINT')
 p._remember_verified_home_pointer(p.last_pointer_check);assert p._eligible_home_probe_hint() is None


def test_hint_reuses_one_nudge_and_only_changes_observation_roi(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch);p._remember_verified_home_pointer(p.last_pointer_check)
 p.screen.settled=AsyncMock();p._locate_after_nudge=AsyncMock(return_value=(.4,.65));p._active_point_timing={}
 assert asyncio.run(p._find_pointer(known_span=True))==(.4,.65)
 call=p._locate_after_nudge.await_args
 assert call.args[2]==(.4,.65) and call.kwargs=={'anywhere':False,'timing_prefix':'reacquire'}
 assert [c.args[0] for c in p._send.await_args_list]==['move 12 0','move -12 0']
 assert p._pos is None and p._active_point_timing['reacquire_home_hint_used'] is True


@pytest.mark.parametrize('error',[float('nan'),float('inf'),True,'0',.03])
def test_bad_pointer_proof_is_not_retained(tmp_path,monkeypatch,error):
 p=phone(tmp_path,monkeypatch);p.last_pointer_check['error']=error
 p._remember_verified_home_pointer(p.last_pointer_check);assert p._eligible_home_probe_hint() is None


def test_revoked_parent_cannot_create_late_home_hint(tmp_path,monkeypatch):
 from hidagent.hid_lease import leased_input
 p=phone(tmp_path,monkeypatch)
 def stopped():raise asyncio.CancelledError()
 with leased_input(stopped):
  with pytest.raises(asyncio.CancelledError):p._remember_verified_home_pointer(p.last_pointer_check)
 assert p._eligible_home_probe_hint() is None


def test_wrong_local_hint_still_uses_existing_global_observation(tmp_path,monkeypatch):
 from hidagent import phone as module
 p=phone(tmp_path,monkeypatch);p.screen.fresh=AsyncMock(return_value=b'after');seen=[]
 def detect(before,after,near,*args,**kw):
  seen.append(near)
  kw['diagnostics'].update(change_span=[25,18],changed_pixels=150)
  return None if near==(.4,.65) else (.6,.5)
 monkeypatch.setattr(module.vision,'pointer',detect)
 result=asyncio.run(p._locate_after_nudge(b'before',time.time(),(.4,.65),.012,anywhere=False,timing_prefix='reacquire'))
 assert result==(.6,.5) and seen==[(.4,.65),(.5,.5)] and p._pos is None
