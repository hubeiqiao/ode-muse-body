import asyncio,json
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from hidagent.phone import Phone,PhoneUnavailable
from hidagent import phone as phone_module


def make(tmp_path,monkeypatch,fresh):
 monkeypatch.setattr(phone_module,'POINTER_WAIT',.001)
 monkeypatch.setattr(phone_module.vision,'pointer',Mock(return_value=None))
 p=Phone(None,tmp_path/'calibration.json',screen=SimpleNamespace(frame=b'test frame',fresh=fresh,settled=AsyncMock()))
 p.target_info=lambda:{'device':'ipad'};p.span={'w':1000,'h':1000};p._send=AsyncMock();p._active_point_timing={}
 return p


def test_no_fresh_frame_is_not_reported_as_bad_calibration(tmp_path,monkeypatch):
 p=make(tmp_path,monkeypatch,AsyncMock(return_value=None))
 with pytest.raises(PhoneUnavailable,match='no fresh mirror frame'):
  asyncio.run(p._aim(.5,.5))
 assert p.last_pointer_probe['fresh_frames']==0 and p.last_pointer_probe['outcome']=='no_fresh_frame'
 assert p._active_point_timing['aim_probe_fresh_frames']==0
 assert [c.args[0] for c in p._send.await_args_list]==[f'move {phone_module.NUDGE} 0',f'move {-phone_module.NUDGE} 0']
 assert 'test frame' not in json.dumps(p.last_pointer_probe)


def test_fresh_frame_without_pointer_is_distinct(tmp_path,monkeypatch):
 p=make(tmp_path,monkeypatch,AsyncMock(return_value=b'new frame'))
 with pytest.raises(PhoneUnavailable,match='visually confirmed'):
  asyncio.run(p._aim(.5,.5))
 assert p.last_pointer_probe['fresh_frames']>=1 and p.last_pointer_probe['outcome']=='pointer_not_confirmed'
 assert 'recalibrate' not in str(p.last_pointer_probe)


def test_old_probe_cannot_classify_new_mocked_failure(tmp_path,monkeypatch):
 p=make(tmp_path,monkeypatch,AsyncMock(return_value=None));p.last_pointer_probe={'outcome':'no_fresh_frame'}
 p._locate_after_nudge=AsyncMock(return_value=None)
 with pytest.raises(PhoneUnavailable,match='visually confirmed'):asyncio.run(p._aim(.5,.5))


def test_found_probe_records_counts_and_bounds_without_pixels(tmp_path,monkeypatch):
 p=make(tmp_path,monkeypatch,AsyncMock(return_value=b'new frame'))
 monkeypatch.setattr(phone_module.vision,'pointer',Mock(return_value=(.5,.04)))
 found=asyncio.run(p._locate_after_nudge(b'old frame',0,(.5,.04),.02,bounds=(0,.024,1,.095)))
 assert found==(.5,.04) and p.last_pointer_probe['outcome']=='found'
 assert p.last_pointer_probe['bounds']==[0,.024,1,.095] and p.last_pointer_probe['fresh_frames']==1
 assert 'frame' not in p.last_pointer_probe


def test_cancelled_probe_is_stopped_not_missing_pointer(tmp_path,monkeypatch):
 async def run():
  entered=asyncio.Event()
  async def fresh(*args):entered.set();await asyncio.Future()
  p=make(tmp_path,monkeypatch,fresh)
  task=asyncio.create_task(p._locate_after_nudge(b'old frame',0,(.5,.5),.02))
  await entered.wait();task.cancel()
  with pytest.raises(asyncio.CancelledError):await task
  assert p.last_pointer_probe['outcome']=='stopped' and p._active_point_timing['aim_last_probe_outcome']=='stopped'
 asyncio.run(run())


def test_latest_cv_rejection_reaches_point_timing_without_frame_storage(tmp_path,monkeypatch):
 p=make(tmp_path,monkeypatch,AsyncMock(return_value=b'new frame'))
 def reject(*args,diagnostics,**kwargs):
  diagnostics.update(outcome='non_pointer_shape',changed_pixels=44,roi_pixels=400,roi=[1,2,21,22],change_span=[2,22]);return None
 monkeypatch.setattr(phone_module.vision,'pointer',reject)
 asyncio.run(p._locate_after_nudge(b'old frame',0,(.5,.5),.02))
 probe=p.last_pointer_probe
 assert probe['cv_checks']>=2 and set(probe['cv_latest'])=={'near','global'}
 assert all(v['outcome']=='non_pointer_shape' for v in probe['cv_latest'].values())
 assert p._active_point_timing['aim_cv_latest']==probe['cv_latest']
 assert 'old frame' not in json.dumps(probe) and 'new frame' not in json.dumps(probe)


def test_account_toolbar_needs_two_consistent_fresh_candidates(tmp_path,monkeypatch):
 p=make(tmp_path,monkeypatch,AsyncMock(return_value=b'new frame'))
 monkeypatch.setattr(phone_module,'POINTER_WAIT',.3)
 monkeypatch.setattr(phone_module.vision,'pointer',Mock(side_effect=[(.03,.04),(.05,.05),(.0505,.0505)]))
 found=asyncio.run(p._locate_after_nudge(b'old',0,(.049,.052),.02,bounds=(0,.025,1,.11)))
 assert found==(.0505,.0505) and p.last_pointer_probe['fresh_frames']==3
 assert p.last_pointer_probe['candidate_confirmation_frames']==2


def test_single_account_toolbar_candidate_cannot_poison_position(tmp_path,monkeypatch):
 p=make(tmp_path,monkeypatch,AsyncMock(return_value=b'new frame'))
 monkeypatch.setattr(phone_module.vision,'pointer',Mock(return_value=(.05,.05)))
 found=asyncio.run(p._locate_after_nudge(b'old',0,(.049,.052),.02,bounds=(0,.025,1,.11)))
 assert found is None and p.last_pointer_probe['outcome']=='pointer_not_confirmed'
