"""Actual Device queue seam with memory-only board I/O; no network or HID."""
import asyncio
import pytest
from hidagent.device import Device,DeviceError
from hidagent.hid_lease import leased_input

class Writer:
 def __init__(self):self.sent=[];self.closed=False
 def is_closing(self):return self.closed
 def write(self,data):self.sent.append(data)
 async def drain(self):pass
 def close(self):self.closed=True
class Reader:
 async def readline(self):return b'ok\n'

@pytest.mark.parametrize('command',['key enter','click','type QA sample'])
def test_revoked_lease_cannot_cross_device_queue_lock(command):
 async def run():
  d=Device();w=Writer();d._reader,d._writer=Reader(),w;revoked=False
  def guard():
   if revoked:raise asyncio.CancelledError()
  await d._lock.acquire()
  with leased_input(guard):job=asyncio.create_task(d.send(command))
  await asyncio.sleep(0);assert not w.sent
  revoked=True;d._lock.release()
  with pytest.raises(DeviceError) as error:await job
  assert error.value.delivery=='not_sent' and not w.sent and not w.closed
  assert not d.timings and d._command_sequence==0
  # A new explicit unleased task remains usable; old rejection did not close it.
  await d.send('ping');assert w.sent==[b'ping\n']
 asyncio.run(run())


def test_connection_guard_is_rechecked_after_waiting_in_device_queue():
 async def run():
  d=Device();w=Writer();d._reader,d._writer=Reader(),w;generation=1
  def guard():
   if generation!=1:raise RuntimeError('Generation changed')
  await d._lock.acquire()
  with leased_input(guard):job=asyncio.create_task(d.send('move 10 0'))
  await asyncio.sleep(0);generation=2;d._lock.release()
  with pytest.raises(DeviceError) as error:await job
  assert error.value.delivery=='not_sent' and not w.sent and not w.closed
 asyncio.run(run())


def test_rescue_release_can_cross_revoked_queue_lease():
 async def run():
  d=Device();w=Writer();d._reader,d._writer=Reader(),w
  def guard():raise asyncio.CancelledError()
  with leased_input(guard):await d.send('release')
  assert w.sent==[b'release\n'] and not w.closed
 asyncio.run(run())


def test_queued_typing_rejection_does_not_invent_delivered_characters(tmp_path,monkeypatch):
 from hidagent.phone import Phone
 from unittest.mock import AsyncMock
 monkeypatch.setenv('IPAD_TYPE_CHUNK_CHARS','8');monkeypatch.delenv('HID_KEY_MS',raising=False)
 async def run():
  d=Device();w=Writer();d._reader,d._writer=Reader(),w;revoked=False
  p=Phone(d,tmp_path/'map.json');p.ensure_keyboard_live=AsyncMock()
  p.target_info=lambda:{'device':'ipad','orientation':'portrait','calibration_key':'test','width':710,'height':1080}
  def guard():
   if revoked:raise asyncio.CancelledError()
  await d._lock.acquire()
  with leased_input(guard):job=asyncio.create_task(p.type('abcdefghijklmnop'))
  await asyncio.sleep(0);revoked=True;d._lock.release()
  with pytest.raises(DeviceError) as error:await job
  assert error.value.delivery=='not_sent' and not error.value.typing_may_have_happened
  assert p.last_typing_timing['acknowledged_chars']==0
  assert p.last_typing_timing['unconfirmed_chars']==0 and not w.sent
 asyncio.run(run())
