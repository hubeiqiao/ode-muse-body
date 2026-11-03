import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from hidagent.phone import Phone,PhoneUnavailable


def make(tmp_path):
 p=Phone(SimpleNamespace(links=1),tmp_path/'c.json',screen=SimpleNamespace(frame=b'base',settled=AsyncMock(),fresh=AsyncMock(return_value=b'outside')))
 p.span={'w':1000,'h':1000};p._pos=(500,500);p._send=AsyncMock();p._save=Mock();p._active_point_timing={}
 p.target_info=lambda:{'device':'ipad','orientation':'portrait','width':710,'height':1080,'calibration_key':'qa'}
 p._locate_after_nudge=AsyncMock(return_value=(.545,.5))
 return p


def test_escape_measures_two_visible_positions_then_restores_without_click(tmp_path):
 p=make(tmp_path);binding=tuple(p.target_info().get(k) for k in ('device','orientation','calibration_key','width','height'))
 r=asyncio.run(p._escape_pointer_probe(.5,.5,12,None,1,binding))
 assert r==pytest.approx((.5,.5))
 assert [c.args[0] for c in p._send.await_args_list]==['move 45 0','move 12 0','move -57 0']
 assert p._locate_after_nudge.await_args.args[:4]==(b'outside',pytest.approx(p._locate_after_nudge.await_args.args[1]),(.545,.5),.012)
 p._save.assert_not_called()


def test_escape_without_fresh_outside_frame_restores_and_refuses(tmp_path):
 p=make(tmp_path);p.screen.fresh.return_value=None;binding=tuple(p.target_info().get(k) for k in ('device','orientation','calibration_key','width','height'))
 assert asyncio.run(p._escape_pointer_probe(.5,.5,12,None,1,binding)) is None
 assert [c.args[0] for c in p._send.await_args_list]==['move 45 0','move -45 0']
 p._locate_after_nudge.assert_not_awaited()


def test_escape_cancel_discards_position_and_sends_no_restore_after_stop(tmp_path):
 async def run():
  p=make(tmp_path);entered=asyncio.Event()
  async def fresh(*args):entered.set();await asyncio.Future()
  p.screen.fresh.side_effect=fresh;binding=tuple(p.target_info().get(k) for k in ('device','orientation','calibration_key','width','height'))
  task=asyncio.create_task(p._escape_pointer_probe(.5,.5,12,None,1,binding));await entered.wait();task.cancel()
  with pytest.raises(asyncio.CancelledError):await task
  assert [c.args[0] for c in p._send.await_args_list]==['move 45 0']
  assert p._pos is None
 asyncio.run(run())


def test_escape_link_change_never_sends_suffix_or_inverse_to_new_link(tmp_path):
 p=make(tmp_path)
 async def changed(*args):p.device.links=2;return b'outside'
 p.screen.fresh.side_effect=changed;binding=tuple(p.target_info().get(k) for k in ('device','orientation','calibration_key','width','height'))
 with pytest.raises(PhoneUnavailable):asyncio.run(p._escape_pointer_probe(.5,.5,12,None,1,binding))
 assert [c.args[0] for c in p._send.await_args_list]==['move 45 0']
 assert p._pos is None


def test_aim_escape_keeps_original_tolerance_and_does_not_learn(tmp_path):
 async def run(found):
  p=make(tmp_path);p._move_xy=AsyncMock()
  async def hidden(*args,**kwargs):
   p.last_pointer_probe={'outcome':'pointer_not_confirmed','fresh_frames':2,'cv_latest':{'near':{'outcome':'too_few_changes'}}}
   return None
  p._locate_after_nudge.side_effect=hidden;p._escape_pointer_probe=AsyncMock(return_value=found)
  if found==(.5,.5):await p._aim(.5,.5)
  else:
   with pytest.raises(PhoneUnavailable,match='too far'):await p._aim(.5,.5)
  p._escape_pointer_probe.assert_awaited_once();p._save.assert_not_called()
 asyncio.run(run((.5,.5)));asyncio.run(run((.7,.5)))


@pytest.mark.parametrize('reason',['no_fresh_frame','too_many_changes','change_too_spread'])
def test_escape_is_not_a_general_retry_for_other_pointer_failures(tmp_path,reason):
 p=make(tmp_path)
 async def rejected(*args,**kwargs):
  p.last_pointer_probe={'outcome':reason if reason=='no_fresh_frame' else 'pointer_not_confirmed','fresh_frames':0 if reason=='no_fresh_frame' else 2,'cv_latest':{'near':{'outcome':reason}}}
  return None
 p._locate_after_nudge.side_effect=rejected;p._escape_pointer_probe=AsyncMock()
 with pytest.raises(PhoneUnavailable):asyncio.run(p._aim(.5,.5))
 p._escape_pointer_probe.assert_not_awaited()
