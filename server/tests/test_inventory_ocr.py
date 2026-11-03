import asyncio
from types import SimpleNamespace
import pytest
from hidagent.inventory_ocr import InventoryOCR
from hidagent.decision import DecisionUnavailable


def test_two_local_modes_receive_image_on_stdin_not_shell(monkeypatch):
 calls=[]
 class Process:
  returncode=0
  async def communicate(self,image):
   assert image==b'fixture-image'
   return b'Raw OCR only',None
 async def spawn(*args,**kwargs):
  calls.append((args,kwargs));return Process()
 monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
 result=asyncio.run(InventoryOCR('/trusted/tesseract').read(b'fixture-image'))
 assert [args[4] for args,_ in calls]==['6','11']
 assert result['is_accessibility_tree'] is False and result['source']=='local_tesseract_ocr'
 assert result['table_text']==result['sparse_text']=='Raw OCR only'
 assert all('fixture-image' not in str(args) for args,_ in calls)


def test_timeout_closes_process_and_never_returns_partial_text(monkeypatch):
 processes=[]
 class Process:
  returncode=None;killed=False
  async def communicate(self,image):await asyncio.sleep(10)
  def kill(self):self.killed=True;self.returncode=-9
  async def wait(self):return self.returncode
 async def spawn(*args,**kwargs):
  p=Process();processes.append(p);return p
 monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
 with pytest.raises(DecisionUnavailable):asyncio.run(InventoryOCR(timeout=.001).read(b'fixture'))
 assert processes and all(p.killed for p in processes)


def test_dense_table_uses_enlarged_pixels_sparse_keeps_original(monkeypatch):
 import io
 from PIL import Image
 buffer=io.BytesIO();Image.new('RGB',(320,200),'white').save(buffer,format='PNG')
 source=buffer.getvalue();calls=[]
 async def mode(self,image,mode):
  with Image.open(io.BytesIO(image)) as im:calls.append((mode,im.size))
  return 'source pixels'
 monkeypatch.setattr(InventoryOCR,'_mode',mode)
 asyncio.run(InventoryOCR().read(source))
 assert calls==[(6,(640,400)),(11,(320,200))]
