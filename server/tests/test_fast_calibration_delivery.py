import asyncio,json
from types import SimpleNamespace
import pytest
from hidagent.phone import Phone,PhoneUnavailable

class Device:
 links=1
 connected=True
 def __init__(self,mode='ok'):
  self.pos=[0,0];self.sent=100;self.retried=0;self.dropped=0;self.mode=mode;self.stats_calls=0;self.commands=[]
 async def send(self,line):
  self.commands.append(line)
  cmd,*args=line.split()
  if cmd=='stats':
   self.stats_calls+=1
   if self.mode=='unknown_before' or (self.mode=='unknown_after' and self.stats_calls>1):return 'ok'
   ms=30 if self.mode=='pace_change' and self.stats_calls>1 else 20
   return f'ok sent={self.sent} retried={self.retried} dropped={self.dropped} ms={ms} mouse_sub=1'
  if cmd=='home':self.pos=[0,0]
  elif cmd=='move':
   self.pos=[self.pos[i]+int(args[i]) for i in range(2)];self.sent+=1
   if self.mode=='dropped':self.dropped+=1
   elif self.mode=='retried':self.retried+=1
   elif self.mode=='link_change':self.links+=1
  return 'ok'

def make(tmp_path,monkeypatch,mode):
 d=Device(mode);p=Phone(d,tmp_path/'map.json',screen=SimpleNamespace(live=True))
 p.target_info=lambda:{'device':'ipad','orientation':'portrait','calibration_key':'test','width':710,'height':1080}
 p._pointer_ms=lambda:20
 p.span={'w':999,'h':999};p._seen['x'].append((.5,.5));p._save();original=p._file.read_bytes()
 async def find():return (d.pos[0]/460,d.pos[1]/1000)
 monkeypatch.setattr(p,'_find_pointer',find)
 return p,d,original

@pytest.mark.parametrize('mode',['dropped','retried','link_change','unknown_after','pace_change'])
def test_fast_calibration_rejects_good_grid_with_unproven_delivery(tmp_path,monkeypatch,mode):
 p,d,original=make(tmp_path,monkeypatch,mode)
 with pytest.raises(PhoneUnavailable):asyncio.run(p.start_calibration())
 assert p._file.read_bytes()==original
 assert p.span=={'w':999,'h':999} and list(p._seen['x'])==[(.5,.5)]
 assert p._pos is None


def test_fast_calibration_unknown_baseline_rejects_before_motion(tmp_path,monkeypatch):
 p,d,original=make(tmp_path,monkeypatch,'unknown_before')
 with pytest.raises(PhoneUnavailable):asyncio.run(p.start_calibration())
 assert d.commands==['stats']
 assert p._file.read_bytes()==original


def test_fast_calibration_zero_retry_and_drop_preserves_real_grid_acceptance(tmp_path,monkeypatch):
 p,d,original=make(tmp_path,monkeypatch,'ok')
 assert asyncio.run(p.start_calibration())
 assert p.span=={'w':460,'h':1000}
 assert d.stats_calls==2
 assert len(p._seen['x'])==11
 assert json.loads(p._file.read_text())['motion_ms']==20


def test_fast_calibration_visual_failure_has_no_manual_save_fallback(tmp_path,monkeypatch):
 p,d,original=make(tmp_path,monkeypatch,'ok')
 async def absent():return None
 monkeypatch.setattr(p,'_find_pointer',absent)
 with pytest.raises(PhoneUnavailable):asyncio.run(p.start_calibration())
 assert p._file.read_bytes()==original and p.span=={'w':999,'h':999}
 assert p._calibration_key is None and p._pointer_tuned_link is None


def test_failed_normal_calibration_preserves_prior_mapping_in_memory(tmp_path,monkeypatch):
 p=Phone(Device(),tmp_path/'old.json');p.span={'w':460,'h':1000};p._save();original=p._file.read_bytes()
 async def failed():
  p.span={'w':1,'h':1};p._seen['x'].clear()
  raise RuntimeError('measurement failed')
 monkeypatch.setattr(p,'_calibrate',failed)
 with pytest.raises(RuntimeError):asyncio.run(p.start_calibration())
 assert p.span=={'w':460,'h':1000} and p._file.read_bytes()==original


@pytest.mark.parametrize('error',[asyncio.CancelledError,RuntimeError])
def test_failed_calibration_invalidates_old_manual_finish_ticket(tmp_path,monkeypatch,error):
 p=Phone(Device(),tmp_path/'ticket.json');p.span={'w':460,'h':1000};p._profile_key='ipad-portrait-66'
 async def interrupted_stops():raise error()
 monkeypatch.setattr(p,'_measure_stops',interrupted_stops)
 with pytest.raises(error):asyncio.run(p.start_calibration())
 assert p._calibration_key is None
 assert p.span=={'w':460,'h':1000}
