import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from hidagent.phone import Phone,PhoneUnavailable


def setup(tmp_path,monkeypatch,chunk='100',device='ipad',orientation='portrait'):
 monkeypatch.setenv('IPAD_POINT_CHUNK_UNITS',chunk)
 p=Phone(SimpleNamespace(links=1,connected=True),tmp_path/'map.json')
 p.span={'w':1000,'h':1000};p._active_point_timing={}
 p.target_info=lambda:{'device':device,'orientation':orientation,'calibration_key':'test','width':710,'height':1080}
 p._home_fraction=lambda:.025;p._send=AsyncMock();p._pos=(0,0)
 return p


def test_bounded_point_preserves_single_axis_order_units_and_ten_unit_reports(tmp_path,monkeypatch):
 p=setup(tmp_path,monkeypatch)
 asyncio.run(p._move_xy(250,-125))
 assert [c.args[0] for c in p._send.await_args_list]==['move 100 0','move 100 0','move 50 0','move 0 -100','move 0 -25']
 assert p._active_point_timing['point_chunks_dispatched']==5
 assert p._active_point_timing['point_chunks_acknowledged']==5


@pytest.mark.parametrize('reason',['reconnect','offline','rotate'])
def test_changed_binding_discards_remaining_point_chunks(tmp_path,monkeypatch,reason):
 p=setup(tmp_path,monkeypatch)
 async def changed(command):
  if reason=='reconnect':p.device.links=2
  if reason=='offline':p.device.connected=False
  if reason=='rotate':p.target_info=lambda:{'device':'ipad','orientation':'landscape'}
 p._send=AsyncMock(side_effect=changed)
 with pytest.raises(PhoneUnavailable):asyncio.run(p._move_xy(500,0))
 assert p._send.await_count==1 and p._pos is None


def test_cancelled_point_packet_never_dispatches_remainder(tmp_path,monkeypatch):
 async def run():
  p=setup(tmp_path,monkeypatch);entered=asyncio.Event()
  async def send(command):entered.set();await asyncio.Future()
  p._send=AsyncMock(side_effect=send)
  task=asyncio.create_task(p._move_xy(500,0));await entered.wait();task.cancel()
  with pytest.raises(asyncio.CancelledError):await task
  await asyncio.sleep(0)
  assert p._send.await_count==1 and p._pos is None
  assert p._active_point_timing['point_chunk_delivery_uncertain']
 asyncio.run(run())


@pytest.mark.parametrize('device,orientation,active,point_move',[('iphone','portrait',True,True),('ipad','landscape',True,True),('ipad','portrait',False,True),('ipad','portrait',True,False)])
def test_only_active_ipad_portrait_point_is_changed(tmp_path,monkeypatch,device,orientation,active,point_move):
 p=setup(tmp_path,monkeypatch,device=device,orientation=orientation)
 if not active:p._active_point_timing=None
 asyncio.run(p._move_xy(250,0,point_move=point_move))
 p._send.assert_awaited_once_with('move 250 0')


def test_disabled_or_invalid_chunk_setting(tmp_path,monkeypatch):
 p=setup(tmp_path,monkeypatch,chunk='0');asyncio.run(p._move_xy(250,0))
 p._send.assert_awaited_once_with('move 250 0')
 monkeypatch.setenv('IPAD_POINT_CHUNK_UNITS','99');p._send.reset_mock()
 with pytest.raises(ValueError):asyncio.run(p._move_xy(250,0))
 p._send.assert_not_awaited()
