import asyncio
import pytest
from hidagent.device import Device,DeviceError

class Writer:
 def __init__(self,delay): self.delay=delay;self.closed=False;self.writes=[]
 def is_closing(self):return self.closed
 def close(self):self.closed=True
 def write(self,b):self.writes.append(b)
 async def drain(self):await asyncio.sleep(self.delay)
class Reader:
 def __init__(self,delay):self.delay=delay;self.calls=0
 async def readline(self):self.calls+=1;await asyncio.sleep(self.delay);return b'ok\n'

@pytest.mark.parametrize('write_delay,ack_delay',[(.08,0),(.03,.03)])
def test_total_wire_budget_covers_drain_and_ack(write_delay,ack_delay):
 async def run():
  d=Device();d._writer=Writer(write_delay);d._reader=Reader(ack_delay);d._timeout=lambda command:.04
  with pytest.raises(DeviceError) as caught:await asyncio.wait_for(d.send('type unique'),.2)
  assert caught.value.delivery=='unknown' and d._writer.closed
  assert d._writer.writes==[b'type unique\n']
  assert len(d.timings)==1 and d.timings[-1]['outcome']=='failed'
 asyncio.run(run())

def test_transport_segments_are_disjoint_and_do_not_store_payload():
 async def run():
  d=Device();d._writer=Writer(.015);d._reader=Reader(.025)
  await d.send('type private payload')
  row=d.timings[-1]
  assert row['write_drain_s']>=.01 and row['ack_wait_s']>=.02
  assert abs(row['wire_s']-row['write_drain_s']-row['ack_wait_s'])<.015
  assert 'private' not in repr(row) and 'payload' not in repr(row)
 asyncio.run(run())
