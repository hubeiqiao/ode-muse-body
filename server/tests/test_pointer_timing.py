import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from hidagent.phone import Phone,PhoneUnavailable

def phone(tmp_path,monkeypatch):
 from hidagent import phone as module
 monkeypatch.setattr(module,'TAP_GAP',0)
 p=Phone(SimpleNamespace(links=1),tmp_path/'c.json',SimpleNamespace(settled=AsyncMock()))
 p.span={'w':1000,'h':1000};p._link=1
 p.target_info=lambda:{'device':'ipad'};p._sync_profile=lambda:False
 p.ensure_live=AsyncMock();p._send=AsyncMock();p._move_to=AsyncMock();p._aim=AsyncMock()
 return p

def test_tap_timing_keeps_input_order_and_outer_parts_do_not_double_count(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch);asyncio.run(p.tap(.5,.5,aim=True))
 assert [x.args[0] for x in p._send.await_args_list]==['release','click']
 m=p.last_tap_timing;assert m['outcome']=='ok' and m['point']['outcome']=='ok'
 assert m['point']['aim_requested'] and 'aim_s' in m['point']
 parts=sum(m.get(k,0) for k in ('point_s','post_aim_settle_s','long_move_settle_s','pre_click_check_s','click_s','tap_gap_s'))
 assert abs(parts-m['total_s'])<.02
 assert p._active_point_timing is None

def test_failed_preclick_still_records_timing_and_sends_no_click(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch)
 with pytest.raises(PhoneUnavailable):asyncio.run(p.tap(.5,.5,aim=True,pre_click=lambda:False))
 assert p.last_tap_timing['outcome']=='failed' and 'click_s' not in p.last_tap_timing
 assert [x.args[0] for x in p._send.await_args_list]==['release']

def test_cancelled_move_records_stop_and_does_not_continue(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch);p._move_to.side_effect=asyncio.CancelledError()
 with pytest.raises(asyncio.CancelledError):asyncio.run(p.tap(.5,.5,aim=True))
 assert p.last_tap_timing['outcome']=='stopped' and p.last_point_timing['outcome']=='stopped'
 assert [x.args[0] for x in p._send.await_args_list]==['release']
 p._aim.assert_not_awaited();assert p._active_point_timing is None


def test_prepare_point_remains_visible_and_nested_tap_is_not_an_extra_outer_action(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch)
 async def run():
  await p.point(.3,.4)
  await p.tap(.5,.5,aim=True)
 asyncio.run(run())
 events=list(p.action_timings);assert len(events)==3
 outer=[e for e in events if e['parent_action_id'] is None]
 assert [e['kind'] for e in outer]==['point','tap']
 nested=next(e for e in events if e['parent_action_id'] is not None)
 assert nested['parent_action_id']==outer[1]['action_id']
 assert nested['total_s']<=outer[1]['total_s']
 p.last_point_timing['outcome']='mutated later'
 assert all(e['outcome']=='ok' for e in events)
 assert all(e['finished_monotonic_s']>=e['started_monotonic_s'] for e in events)


def test_cancelled_action_restores_timing_parent_for_next_request(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch)
 async def run():
  p._move_to.side_effect=asyncio.CancelledError()
  with pytest.raises(asyncio.CancelledError):await p.tap(.5,.5)
  p._move_to.side_effect=None
  await p.point(.2,.2)
 asyncio.run(run())
 assert p.action_timings[-1]['parent_action_id'] is None
 assert [e['outcome'] for e in list(p.action_timings)[:2]]==['stopped','stopped']


def test_numeric_action_history_is_bounded(tmp_path,monkeypatch):
 p=phone(tmp_path,monkeypatch)
 async def run():
  for _ in range(140):await p.point(.2,.2)
 asyncio.run(run())
 assert len(p.action_timings)==128
 assert p.action_timings[0]['action_id']==13 and p.action_timings[-1]['action_id']==140
