import asyncio,io,hashlib
from PIL import Image,ImageDraw
from unittest.mock import Mock,AsyncMock,patch
from types import SimpleNamespace
import pytest
from hidagent.native_action_copy import same_card_header,copy_reply
from hidagent.decision import DecisionUnavailable

def encoded(im):
 b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()
def image():
 im=Image.new('RGB',(710,1080),'white');ImageDraw.Draw(im).rectangle((30,265,120,280),fill='black');return im

def test_header_binding_rejects_wrong_card_but_ignores_other_content():
 a=image();b=a.copy();ImageDraw.Draw(b).rectangle((100,600,600,900),fill='red');assert same_card_header(encoded(a),encoded(b))
 ImageDraw.Draw(b).rectangle((30,265,46,280),fill='blue');assert not same_card_header(encoded(a),encoded(b))
 assert not same_card_header(encoded(a),encoded(a.resize((355,540))))

def test_native_copy_keeps_lease_and_pre_click_guard_no_clipboard_or_post():
 async def run():
  frame=encoded(image());t={'device':'ipad','orientation':'portrait','calibration_key':'ipad-portrait-66'}
  phone=SimpleNamespace(target_info=lambda:t,device=SimpleNamespace(links=1,connected=True),calibrated=True,tap=AsyncMock())
  a=SimpleNamespace(phone=phone,running=False,screen=SimpleNamespace(frame=frame,live=True))
  done=SimpleNamespace(done=lambda:True,cancelled=lambda:False)
  source={'name':'grok.read_action','status':'completed','job':done,'_action_source_frame':frame,'result':{'verification_passed':True,'workspace':'Example Action Workspace','frame_sha256':hashlib.sha256(frame).hexdigest(),'card_id':'6','target_handle':'@learner'}}
  tools=SimpleNamespace(calls={'source111':source,'copy1111':{'status':'in_progress'}},result_reader=SimpleNamespace(agent=a),_active=Mock())
  async def tap(*args,**kwargs):
   from hidagent.hid_lease import has_input_lease
   assert has_input_lease() and kwargs['pre_click']()
  phone.tap.side_effect=tap
  with patch('hidagent.native_action_copy.public_search_control',return_value={'x':838,'y':325}):r=await copy_reply(tools,'copy1111','source111',0)
  assert r['copy_command_acknowledged'] and not r['clipboard_contents_read'] and not r['published'] and r['requires_destination_verification']
  assert '_copy_source_frame' in tools.calls['copy1111']
 asyncio.run(run())

def test_compact_header_uses_registered_box_and_rejects_changed_author():
 a=Image.new('RGB',(710,1080),'black');ImageDraw.Draw(a).rectangle((30,415,390,432),fill='white')
 b=a.copy();ImageDraw.Draw(b).rectangle((30,265,46,280),fill='red')
 assert same_card_header(encoded(a),encoded(b),'copy-compact.json')
 ImageDraw.Draw(b).rectangle((30,415,46,431),fill='blue')
 assert not same_card_header(encoded(a),encoded(b),'copy-compact.json')
 assert not same_card_header(encoded(a),encoded(a),'unregistered.json')

def test_actual_compact_control_fixture_retains_independent_anchors():
 from hidagent.app_controls import ROOT,public_search_control
 import json
 path=ROOT/'grok/copy-compact.json';spec=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'black')
 for patch in spec['patches'].values():
  im.paste(Image.open(path.parent/patch['file']),tuple(patch['box'][:2]))
 target={'device':'ipad','orientation':'portrait'}
 hit=public_search_control(encoded(im),'Grok Bot',target,control='prepared_reply_copy',template=path.name)
 assert hit and hit['template']==path.name and hit['y']==427
 # Missing Copy glyph blocks a click; missing target link blocks even anchors-only.
 ImageDraw.Draw(im).rectangle(tuple(spec['patches']['copy']['box']),fill='black')
 assert not public_search_control(encoded(im),'Grok Bot',target,control='prepared_reply_copy',template=path.name)
 ImageDraw.Draw(im).rectangle(tuple(spec['patches']['target_link']['box']),fill='black')
 assert not public_search_control(encoded(im),'Grok Bot',target,control='prepared_reply_copy',anchors_only=True,template=path.name)
