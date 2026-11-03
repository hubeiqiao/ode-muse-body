import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from hidagent.phone import Phone,PhoneUnavailable

BOX={'left':19/710,'right':51/710,'top':31/1080,'bottom':63/1080}
def make(tmp_path):
 p=Phone(SimpleNamespace(links=1),tmp_path/'c.json',SimpleNamespace(frame=b'frame',settled=AsyncMock()))
 p.span={'w':1000,'h':1000};p._pos=(49,43);p._last_move=0
 p.target_info=lambda:{'device':'ipad','orientation':'portrait','width':710,'height':1080,'calibration_key':'qa'}
 p._send=AsyncMock();p._save=Mock();p._move_xy=AsyncMock();p._locate_after_nudge=AsyncMock(return_value=(.0384,.0551))
 async def point(x,y,*a,**kw):await p._aim(x,y)
 p.point=AsyncMock(side_effect=point)
 return p

def test_fresh_pointer_inside_verified_control_clicks_without_center_chasing_or_learning(tmp_path):
 p=make(tmp_path)
 asyncio.run(p.tap(.049,.043,aim=True,pre_click=lambda:True,verified_control_box=BOX))
 assert [a.args[0] for a in p._send.await_args_list]==['move 12 0','move -12 0','click']
 p._move_xy.assert_not_awaited();p._save.assert_not_called()
 assert p.last_pointer_check['control_hit_confirmed']

@pytest.mark.parametrize('guard',[None,False])
def test_verified_control_never_bypasses_independent_context_guard(tmp_path,guard):
 p=make(tmp_path)
 with pytest.raises(PhoneUnavailable):asyncio.run(p.tap(.049,.043,aim=True,pre_click=None if guard is None else lambda:False,verified_control_box=BOX))
 assert 'click' not in [a.args[0] for a in p._send.await_args_list]
 p._save.assert_not_called()

def test_pointer_outside_control_is_rejected_even_within_original_center_tolerance(tmp_path):
 p=make(tmp_path);p._locate_after_nudge.return_value=(.507,.5)
 with pytest.raises(PhoneUnavailable):asyncio.run(p.tap(.5,.5,aim=True,pre_click=lambda:True,verified_control_box={'left':.495,'right':.505,'top':.495,'bottom':.505}))
 assert 'click' not in [a.args[0] for a in p._send.await_args_list]
 p._save.assert_not_called()

def test_invalid_or_unbound_control_box_rejects_before_any_hid(tmp_path):
 for box in ({'left':0,'right':1,'top':0,'bottom':1},{**BOX,'top':True}):
  p=make(tmp_path)
  with pytest.raises(PhoneUnavailable):asyncio.run(p.tap(.049,.043,aim=True,pre_click=lambda:True,verified_control_box=box))
  p._send.assert_not_awaited();p.point.assert_not_awaited()


def test_control_link_change_inside_guard_prevents_click(tmp_path):
 p=make(tmp_path)
 def guard():p.device.links+=1;return True
 with pytest.raises(PhoneUnavailable):asyncio.run(p.tap(.049,.043,aim=True,pre_click=guard,verified_control_box=BOX))
 assert 'click' not in [a.args[0] for a in p._send.await_args_list]
 assert p._verified_control_tx.get() is None

def test_stop_at_context_check_prevents_click_and_closes_control_scope(tmp_path):
 p=make(tmp_path)
 def stop():raise asyncio.CancelledError()
 with pytest.raises(asyncio.CancelledError):asyncio.run(p.tap(.049,.043,aim=True,pre_click=stop,verified_control_box=BOX))
 assert 'click' not in [a.args[0] for a in p._send.await_args_list]
 assert p._verified_control_tx.get() is None and p._pos is None
