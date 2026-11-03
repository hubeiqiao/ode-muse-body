"""Task-only local OCR. This is text observation, never an accessibility tree."""
import asyncio
import io
from PIL import Image, UnidentifiedImageError
import hashlib
import time
from .decision import DecisionUnavailable

class InventoryOCR:
 def __init__(self,binary='/opt/homebrew/bin/tesseract',timeout=5,*,languages='eng'):
  if languages not in ('eng','eng+chi_sim'):raise ValueError('Unsupported local OCR languages')
  self.binary,self.timeout,self.languages=binary,timeout,languages
 async def _mode(self,image,mode,*,tsv=False):
  process=None
  try:
   process=await asyncio.create_subprocess_exec(self.binary,'stdin','stdout','--psm',str(mode),'-l',self.languages,*(['tsv'] if tsv else []),
       stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
       env={'OMP_THREAD_LIMIT':'1','PATH':'/opt/homebrew/bin:/usr/bin:/bin'})
   stdout,_=await asyncio.wait_for(process.communicate(image),self.timeout)
   if process.returncode or not stdout or len(stdout)>32768:raise ValueError('unsupported OCR output')
   return stdout.decode('utf-8')
  except (OSError,ValueError,UnicodeError,TimeoutError):
   raise DecisionUnavailable('Local inventory OCR unavailable; no note created') from None
  finally:
   if process is not None and process.returncode is None:
    process.kill();await process.wait()
 async def read(self,image):
  if not isinstance(image,bytes) or not image or len(image)>8*1024*1024:
   raise DecisionUnavailable('No supported inventory source image')
  started=time.perf_counter()
  # Enlarge source pixels before dense-table OCR. Sparse mode keeps original
  # layout for large headings/budget. No fixture-specific crop or expected data.
  table_image=image
  try:
   with Image.open(io.BytesIO(image)) as source:
    if source.width*source.height<=4_000_000 and max(source.size)<=2000:
     enlarged=source.resize((source.width*2,source.height*2),Image.Resampling.LANCZOS)
     buffer=io.BytesIO();enlarged.save(buffer,format='PNG');table_image=buffer.getvalue()
  except (UnidentifiedImageError,OSError,ValueError):
   pass
  modes=await asyncio.gather(self._mode(table_image,6),self._mode(image,11))
  return {'source':'local_tesseract_ocr','is_accessibility_tree':False,
          'table_text':modes[0],'sparse_text':modes[1],'frame_sha256':hashlib.sha256(image).hexdigest(),
          'read_s':round(time.perf_counter()-started,3),
          'limitations':'OCR can omit/misread text. Independently verify all rows against original pixels.'}

 async def read_layout(self,image):
  """Unscaled line boxes for local, exact-label control lookup. Never a UI tree."""
  import csv
  if not isinstance(image,bytes) or not image or len(image)>8*1024*1024:
   raise DecisionUnavailable('No supported demo source image')
  with Image.open(io.BytesIO(image)) as source:
   w,h=source.size
   if w*h>4_000_000:raise DecisionUnavailable('Demo image too large')
  started=time.perf_counter()
  raw=await self._mode(image,11,tsv=True)
  lines={}
  try:
   for word in csv.DictReader(io.StringIO(raw),delimiter='\t'):
    if word['level']!='5' or not word['text'].strip() or float(word['conf'])<85:continue
    x,y,bw,bh=[int(word[k]) for k in ('left','top','width','height')]
    if not (0<=x<x+bw<=w and 0<=y<y+bh<=h):continue
    key=tuple(word[k] for k in ('page_num','block_num','par_num','line_num'))
    lines.setdefault(key,[]).append((word['text'],x,y,x+bw,y+bh,float(word['conf'])/100))
  except (KeyError,ValueError,TypeError):raise DecisionUnavailable('Invalid local OCR layout') from None
  rows=[]
  for words in lines.values():
   rows.append({'text':' '.join(v[0] for v in words),'confidence':min(v[5] for v in words),
     'box':{'left':min(v[1] for v in words)/w,'top':min(v[2] for v in words)/h,
            'right':max(v[3] for v in words)/w,'bottom':max(v[4] for v in words)/h}})
  rows.sort(key=lambda r:(r['box']['top'],r['box']['left']))
  return {'source':'local_tesseract_ocr','is_accessibility_tree':False,'rows':rows,
          'text':'\n'.join(r['text'] for r in rows),'frame_sha256':hashlib.sha256(image).hexdigest(),
          'read_s':round(time.perf_counter()-started,3)}
