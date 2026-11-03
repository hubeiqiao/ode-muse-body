import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from hidagent.phone import Phone,PhoneUnavailable

def phone(tmp_path,device='ipad',connected=True,frame=b'observed QA frame'):
 p=Phone(SimpleNamespace(connected=connected,links=1),tmp_path/'c.json',SimpleNamespace(frame=frame,live=False,fresh=AsyncMock(return_value=None)))
 p.target_info=lambda:{'device':device}
 p._send=AsyncMock();p.ensure_keyboard_live=AsyncMock();p.ensure_live=AsyncMock(side_effect=AssertionError('Recovery must not probe coordinates'))
 return p

def test_stale_unmapped_ipad_home_is_keyboard_only_and_bounded(tmp_path,monkeypatch):
 from hidagent import phone as module
 monkeypatch.setattr(module,'HOME_RECOVERY_WAIT',.001)
 p=phone(tmp_path)
 async def run():
  assert await p.recover_home()==dict(ok=True,home_reached=None,attempts=1,retries=0)
  with pytest.raises(PhoneUnavailable,match='already attempted'):await p.recover_home()
 asyncio.run(run())
 assert [c.args[0] for c in p._send.await_args_list]==['key esc','key esc','key cmd+h']
 assert p._pos is None

@pytest.mark.parametrize('device,connected,frame',[('iphone',True,b'frame'),('ipad',False,b'frame'),('ipad',True,None)])
def test_recovery_refuses_wrong_device_or_no_observation(tmp_path,device,connected,frame):
 p=phone(tmp_path,device,connected,frame)
 with pytest.raises(PhoneUnavailable):asyncio.run(p.recover_home())
 p._send.assert_not_awaited()

def test_recovery_lost_ack_does_not_loop(tmp_path):
 p=phone(tmp_path);p._send=AsyncMock(side_effect=RuntimeError('lost ACK'))
 with pytest.raises(RuntimeError):asyncio.run(p.recover_home())
 with pytest.raises(PhoneUnavailable,match='already attempted'):asyncio.run(p.recover_home())
 assert p._send.await_count==1


def test_recovery_closes_filled_spotlight_before_home(tmp_path):
 p=phone(tmp_path)
 query='Settings';panel=True
 async def send(command):
  nonlocal query,panel
  if command=='key esc':
   if query:query=''
   else:panel=False
  elif command=='key cmd+h':
   assert not panel, 'Home alone leaves Spotlight and the old query open'
 p._send.side_effect=send
 assert asyncio.run(p.recover_home())['ok']
 assert not panel and not query
