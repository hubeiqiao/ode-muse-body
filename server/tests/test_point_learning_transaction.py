import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from hidagent.phone import Phone,PhoneUnavailable

def make(tmp_path,monkeypatch):
 monkeypatch.setenv('HID_POINT_LEARNING_V2','1');monkeypatch.setenv('HID_POINT_LEARNING_TRANSACTION','1')
 p=Phone(SimpleNamespace(links=1),tmp_path/'sample.json',SimpleNamespace(frame=b'f',settled=AsyncMock()))
 p.span={'w':1000,'h':1000};p._pos=(500,500);p._moves=3;p._last_move=0
 p.target_info=lambda:{'device':'ipad','orientation':'portrait','calibration_key':'ipad-test','width':710,'height':1080}
 p._send=AsyncMock();p._locate_after_nudge=AsyncMock(return_value=(.5,.5))
 async def point(x,y,*a,**kw):await p._aim(x,y)
 p.point=AsyncMock(side_effect=point)
 return p

def test_rejected_pre_click_cannot_commit_sample(tmp_path,monkeypatch):
 p=make(tmp_path,monkeypatch)
 with pytest.raises(PhoneUnavailable):asyncio.run(p.tap(.5,.5,aim=True,pre_click=lambda:False))
 assert not p._seen['x'] and not p._seen['y']
 assert not p._file.exists()
 assert 'click' not in [c.args[0] for c in p._send.await_args_list]

def test_guarded_ack_click_commits_once_after_guard(tmp_path,monkeypatch):
 p=make(tmp_path,monkeypatch)
 def guard():
  assert not p._seen['x'] and not p._file.exists()
  return True
 asyncio.run(p.tap(.5,.5,aim=True,pre_click=guard))
 assert list(p._seen['x'])==[(.5,.5)] and p._file.exists()
 assert p.last_tap_timing['learning_sample_committed'] is True
 assert p._point_learning_tx.get() is None

@pytest.mark.parametrize('stage',['guard','click','after_click'])
def test_cancellation_or_lost_click_ack_discards_pending(tmp_path,monkeypatch,stage):
 p=make(tmp_path,monkeypatch)
 def guard():
  if stage=='guard':raise asyncio.CancelledError()
  return True
 async def send(line):
  if line=='click':
   if stage=='click':raise RuntimeError('lost acknowledgement')
   if stage=='after_click':asyncio.current_task().cancel()
 p._send=AsyncMock(side_effect=send)
 with pytest.raises((RuntimeError,asyncio.CancelledError)):
  asyncio.run(p.tap(.5,.5,aim=True,pre_click=guard))
 assert not p._seen['x'] and not p._file.exists() and p._pos is None
 assert p._point_learning_tx.get() is None

@pytest.mark.parametrize('changed',['link','file','span','orientation','axes'])
def test_binding_change_before_commit_cannot_cross_write(tmp_path,monkeypatch,changed):
 p=make(tmp_path,monkeypatch)
 def guard():
  if changed=='link':p.device.links+=1
  if changed=='file':p._file=tmp_path/'other.json'
  if changed=='span':p.span['w']=1100
  if changed=='axes':p.axes['x']=-1
  if changed=='orientation':p.target_info=lambda:{'device':'ipad','orientation':'landscape'}
  return True
 with pytest.raises(PhoneUnavailable,match='binding changed'):
  asyncio.run(p.tap(.5,.5,aim=True,pre_click=guard))
 assert not p._seen['x'] and not list(tmp_path.glob('*.json'))


def test_opt_out_preserves_existing_behavior(tmp_path,monkeypatch):
 p=make(tmp_path,monkeypatch);monkeypatch.setenv('HID_POINT_LEARNING_TRANSACTION','0')
 with pytest.raises(PhoneUnavailable):asyncio.run(p.tap(.5,.5,aim=True,pre_click=lambda:False))
 assert list(p._seen['x'])==[(.5,.5)]


def test_inherited_late_child_cannot_append_to_finished_transaction(tmp_path,monkeypatch):
 p=make(tmp_path,monkeypatch)
 async def scenario():
  ready=asyncio.Event();release=asyncio.Event();children=[]
  async def late():
   ready.set();await release.wait();await p._aim(.5,.5)
  def guard():
   children.append(asyncio.create_task(late()))
   return False
  with pytest.raises(PhoneUnavailable):await p.tap(.5,.5,aim=True,pre_click=guard)
  await ready.wait();release.set();await children[0]
  assert not p._seen['x'] and p._point_learning_tx.get() is None
 asyncio.run(scenario())


def test_rejected_guard_preserves_existing_mapping_and_disk_bytes(tmp_path,monkeypatch):
 p=make(tmp_path,monkeypatch)
 for a in ('x','y'):p._seen[a].append((.25,.26))
 p._save();before=p._file.read_bytes()
 with pytest.raises(PhoneUnavailable):asyncio.run(p.tap(.5,.5,aim=True,pre_click=lambda:False))
 assert p._file.read_bytes()==before
 assert list(p._seen['x'])==[(.25,.26)] and list(p._seen['y'])==[(.25,.26)]

def legacy(tmp_path,monkeypatch):
 p=make(tmp_path,monkeypatch);monkeypatch.setenv('HID_POINT_LEARNING_V2','0');monkeypatch.setenv('HID_POINT_LEARNING_TRANSACTION','0');return p

def test_default_legacy_rejected_context_preserves_disk_and_memory(tmp_path,monkeypatch):
 p=legacy(tmp_path,monkeypatch);p._save();before=p._file.read_bytes()
 with pytest.raises(PhoneUnavailable):asyncio.run(p.tap(.5,.5,aim=True,pre_click=lambda:False))
 assert p._file.read_bytes()==before and not p._seen['x'] and not p._seen['y']
 assert 'click' not in [c.args[0] for c in p._send.await_args_list]
 assert p._point_learning_tx.get() is None

def test_default_legacy_persists_only_after_guard_and_click_ack(tmp_path,monkeypatch):
 p=legacy(tmp_path,monkeypatch);p._save();before=p._file.read_bytes()
 def guard():assert p._file.read_bytes()==before;return True
 async def send(line):
  if line=='click':assert p._file.read_bytes()==before
 p._send=AsyncMock(side_effect=send)
 asyncio.run(p.tap(.5,.5,aim=True,pre_click=guard))
 assert p._file.read_bytes()!=before and list(p._seen['x'])==[(.5,.5)]
 assert p.last_tap_timing['learning_sample_committed'] is True

@pytest.mark.parametrize('failure',['guard_cancel','click_ack_lost','cancel_after_click'])
def test_default_legacy_failure_keeps_last_accepted_mapping(tmp_path,monkeypatch,failure):
 p=legacy(tmp_path,monkeypatch);p._save();before=p._file.read_bytes()
 def guard():
  if failure=='guard_cancel':raise asyncio.CancelledError()
  return True
 async def send(line):
  if line=='click':
   if failure=='click_ack_lost':raise RuntimeError('lost ack')
   if failure=='cancel_after_click':asyncio.current_task().cancel()
 p._send=AsyncMock(side_effect=send)
 with pytest.raises((RuntimeError,asyncio.CancelledError)):asyncio.run(p.tap(.5,.5,aim=True,pre_click=guard))
 assert p._file.read_bytes()==before and not p._seen['x'] and not p._seen['y'] and p._pos is None

@pytest.mark.parametrize('changed',['link','file','span','axes'])
def test_default_legacy_changed_binding_rejects_before_click(tmp_path,monkeypatch,changed):
 p=legacy(tmp_path,monkeypatch);p._save();before=p._file.read_bytes();original=p._file
 def guard():
  if changed=='link':p.device.links+=1
  if changed=='file':p._file=tmp_path/'other.json'
  if changed=='span':p.span['w']=1100
  if changed=='axes':p.axes['x']=-1
  return True
 with pytest.raises(PhoneUnavailable):asyncio.run(p.tap(.5,.5,aim=True,pre_click=guard))
 assert original.read_bytes()==before and not p._seen['x'] and not p._seen['y']
 assert 'click' not in [c.args[0] for c in p._send.await_args_list]

def test_default_legacy_late_child_rejects_before_probe(tmp_path,monkeypatch):
 p=legacy(tmp_path,monkeypatch)
 async def run():
  release=asyncio.Event();children=[]
  async def child():
   await release.wait();before=len(p._send.await_args_list)
   with pytest.raises(PhoneUnavailable):await p._aim(.5,.5)
   assert len(p._send.await_args_list)==before
  def guard():children.append(asyncio.create_task(child()));return False
  with pytest.raises(PhoneUnavailable):await p.tap(.5,.5,aim=True,pre_click=guard)
  release.set();await children[0]
  assert not p._file.exists() and not p._seen['x']
 asyncio.run(run())
