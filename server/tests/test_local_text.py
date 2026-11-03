import asyncio
import sys
import pytest
from hidagent.local_text import LocalTextReader, LocalTextError


@pytest.fixture
def worker(tmp_path):
    path=tmp_path/'worker.py'
    path.write_text('''import sys,json,base64,time
for line in sys.stdin:
    request=json.loads(line);text=base64.b64decode(request['image_base64']).decode()
    if text=='slow':time.sleep(.3)
    data={'source':'local_vision_ocr','mode':request.get('mode','fast'),'is_accessibility_tree':False,'rows':[{'text':text,'confidence':1.0,'box':{'left':.1,'top':.2,'right':.5,'bottom':.4}}]}
    if text=='bad':data['rows'][0]['box']['left']=float('nan')
    print(json.dumps(data),flush=True)
''')
    return [sys.executable,'-u',str(path)]


def test_cache_and_response_copies_cannot_poison_future_observations(worker):
    async def run():
        reader=LocalTextReader(worker)
        try:
            a=await reader.read(b'first');a['rows'][0]['text']='tampered'
            b=await reader.read(b'first')
            assert not a['cache_hit'] and b['cache_hit']
            assert b['rows'][0]['text']=='first'
            assert b['source']=='local_vision_ocr' and b['is_accessibility_tree'] is False
        finally:await reader.close()
    asyncio.run(run())


def test_concurrent_frames_cannot_consume_each_others_reply(worker):
    async def run():
        reader=LocalTextReader(worker)
        try:
            a,b=await asyncio.gather(reader.read(b'one'),reader.read(b'two'))
            assert (a['rows'][0]['text'],b['rows'][0]['text'])==('one','two')
        finally:await reader.close()
    asyncio.run(run())


def test_timeout_discards_late_response_before_next_frame(worker):
    async def run():
        reader=LocalTextReader(worker,timeout=.1)
        try:
            with pytest.raises(LocalTextError):await reader.read(b'slow')
            reader.timeout=2
            assert (await reader.read(b'fresh'))['rows'][0]['text']=='fresh'
        finally:await reader.close()
    asyncio.run(run())


def test_cancellation_discards_late_response(worker):
    async def run():
        reader=LocalTextReader(worker)
        try:
            pending=asyncio.create_task(reader.read(b'slow'));await asyncio.sleep(.08);pending.cancel()
            with pytest.raises(asyncio.CancelledError):await pending
            assert (await reader.read(b'fresh'))['rows'][0]['text']=='fresh'
        finally:await reader.close()
    asyncio.run(run())


def test_bad_geometry_never_returns_as_a_valid_observation(worker):
    async def run():
        reader=LocalTextReader(worker)
        try:
            with pytest.raises(LocalTextError):await reader.read(b'bad')
            assert (await reader.read(b'valid'))['rows'][0]['text']=='valid'
        finally:await reader.close()
    asyncio.run(run())


def test_empty_frame_does_not_start_worker(worker):
    async def run():
        reader=LocalTextReader(worker)
        with pytest.raises(LocalTextError):await reader.read(b'')
        await reader.close()
    asyncio.run(run())


def test_accurate_mode_does_not_reuse_fast_cache(worker):
 async def run():
  r=LocalTextReader(worker)
  try:
   fast=await r.read(b'same');accurate=await r.read(b'same',mode='accurate');again=await r.read(b'same',mode='accurate')
   assert fast['mode']=='fast' and accurate['mode']=='accurate' and not accurate['cache_hit'] and again['cache_hit']
   assert fast['frame_sha256']==accurate['frame_sha256']
   with pytest.raises(LocalTextError):await r.read(b'same',mode='unknown')
  finally:await r.close()
 asyncio.run(run())
