import asyncio
from unittest.mock import AsyncMock,patch
import pytest
from hidagent.phone import Phone,PhoneUnavailable

def test_long_press_reuses_aim_and_always_releases(tmp_path):
 async def run():
  p=Phone(None,tmp_path/'map.json');p.point=AsyncMock();p._send=AsyncMock()
  with patch('hidagent.phone.asyncio.sleep',new_callable=AsyncMock):await p.long_press(.2,.4)
  p.point.assert_awaited_once_with(.2,.4,aim=True)
  assert [x.args[0] for x in p._send.await_args_list]==['press','release']
 asyncio.run(run())
def test_cancelled_hold_still_releases_and_never_clicks(tmp_path):
 async def run():
  p=Phone(None,tmp_path/'map.json');p.point=AsyncMock();p._send=AsyncMock()
  with patch('hidagent.phone.asyncio.sleep',new_callable=AsyncMock,side_effect=asyncio.CancelledError()):
   with pytest.raises(asyncio.CancelledError):await p.long_press(.2,.4)
  assert [x.args[0] for x in p._send.await_args_list]==['press','release']
 asyncio.run(run())
def test_unbounded_hold_refused_before_input(tmp_path):
 async def run():
  p=Phone(None,tmp_path/'map.json');p.point=AsyncMock()
  with pytest.raises(PhoneUnavailable):await p.long_press(.2,.4,5)
  p.point.assert_not_awaited()
 asyncio.run(run())
