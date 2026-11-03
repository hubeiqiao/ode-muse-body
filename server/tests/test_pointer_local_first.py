import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from hidagent.phone import Phone

@pytest.mark.parametrize('family,anywhere,enabled,rejected',[('ipad',False,'1',True),('ipad',True,'1',False),('iphone',False,'1',False),('ipad',False,'0',False)])
def test_unrelated_global_change_cannot_preempt_later_local_pointer(tmp_path,monkeypatch,family,anywhere,enabled,rejected):
 monkeypatch.setenv('HID_POINT_LOCAL_FIRST',enabled)
 screen=SimpleNamespace(frame=b'before',fresh=AsyncMock(return_value=b'after'))
 p=Phone(SimpleNamespace(links=1),tmp_path/'c.json',screen);p.target_info=lambda:{'device':family}
 calls=[]
 def pointer(*args,**kwargs):
  calls.append(kwargs.get('reach'))
  # First local image has no change. Far dynamic content appears in global.
  if anywhere:return (.82,.88)
  if len(calls)==1:return None
  if len(calls)==2:return (.82,.88)
  return (.52,.096)
 monkeypatch.setattr('hidagent.phone.vision.pointer',pointer)
 found=asyncio.run(p._locate_after_nudge(b'before',0,(.52,.096),.0124,anywhere=anywhere))
 assert found==((.52,.096) if rejected else (.82,.88))
 assert screen.fresh.await_count==(2 if rejected else 1)


def test_normal_near_candidate_keeps_one_frame_fast_path(tmp_path,monkeypatch):
 monkeypatch.setenv('HID_POINT_LOCAL_FIRST','1')
 screen=SimpleNamespace(frame=b'before',fresh=AsyncMock(return_value=b'after'))
 p=Phone(SimpleNamespace(links=1),tmp_path/'c.json',screen);p.target_info=lambda:{'device':'ipad'}
 monkeypatch.setattr('hidagent.phone.vision.pointer',lambda *a,**k:(.52,.096))
 assert asyncio.run(p._locate_after_nudge(b'before',0,(.52,.096),.0124))==(.52,.096)
 assert screen.fresh.await_count==1
