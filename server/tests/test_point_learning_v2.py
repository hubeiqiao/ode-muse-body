import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from hidagent.phone import Phone,PhoneUnavailable

def setup(tmp_path,monkeypatch,readings):
 monkeypatch.setenv('HID_POINT_LEARNING_V2','1')
 p=Phone(SimpleNamespace(links=1),tmp_path/'c.json',SimpleNamespace(frame=b'frame',settled=AsyncMock()))
 p.span={'w':1000,'h':1000};p._pos=(480,500);p._moves=8
 p.target_info=lambda:{'device':'ipad'}
 p._send=AsyncMock();p._move_xy=AsyncMock();p._locate_after_nudge=AsyncMock(side_effect=readings)
 return p

def test_v2_only_learns_converged_actual_command_position(tmp_path,monkeypatch):
 p=setup(tmp_path,monkeypatch,[(.48,.5),(.5,.5)])
 asyncio.run(p._aim(.5,.5))
 assert list(p._seen['x'])==[(.5,.5)]
 assert list(p._seen['y'])==[(.5,.5)]
 assert p._moves==0

def test_v2_failed_correction_does_not_persist_bad_samples(tmp_path,monkeypatch):
 p=setup(tmp_path,monkeypatch,[(.48,.5),(.47,.5),(.46,.5)])
 with pytest.raises(PhoneUnavailable):asyncio.run(p._aim(.5,.5))
 assert not p._seen['x'] and not p._seen['y']
 assert not (tmp_path/'c.json').exists()


@pytest.mark.parametrize('found',[None,(.9,.9)])
def test_v2_missing_or_far_pointer_never_learns(tmp_path,monkeypatch,found):
 p=setup(tmp_path,monkeypatch,[found])
 with pytest.raises(PhoneUnavailable):asyncio.run(p._aim(.5,.5))
 assert not p._seen['x'] and p._pos is None


def test_v2_does_not_affect_iphone_or_opt_out(tmp_path,monkeypatch):
 for family,flag in [('iphone','1'),('ipad','0')]:
  p=setup(tmp_path,monkeypatch,[(.5,.5)]);p.target_info=lambda:{'device':family};monkeypatch.setenv('HID_POINT_LEARNING_V2',flag)
  asyncio.run(p._aim(.5,.5));assert p._moves==8


def test_v2_unknown_position_cannot_create_fake_command_sample(tmp_path,monkeypatch):
 p=setup(tmp_path,monkeypatch,[(.5,.5)]);p._pos=None
 asyncio.run(p._aim(.5,.5))
 assert not p._seen['x'] and p._moves==8


def test_v2_link_change_cannot_learn_or_confirm_old_position(tmp_path,monkeypatch):
 p=setup(tmp_path,monkeypatch,[])
 async def locate(*args,**kwargs):p.device.links=2;return (.5,.5)
 p._locate_after_nudge=locate
 with pytest.raises(PhoneUnavailable,match='binding changed'):asyncio.run(p._aim(.5,.5))
 assert not p._seen['x'] and p._pos is None
