import asyncio,io,json
from unittest.mock import Mock
from PIL import Image
import httpx,pytest
from hidagent import app_controls
from hidagent.llm import LLM
from hidagent.decision import Jev,DecisionUnavailable


def known_overlay():
 p=app_controls.ROOT/'system-ui/notification-exclusion.json';d=json.loads(p.read_text());im=Image.new('RGB',(710,1080),'white')
 for spec in d['patches'].values():
  with Image.open(p.parent/spec['file']) as patch:im.paste(patch,spec['box'][:2])
 b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()


def test_known_notification_frame_never_reaches_chat_http():
 posted=[]
 def transport(request):posted.append(request);return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
 async def run():
  c=LLM('https://example.test','test');await c._http.aclose();c._http=httpx.AsyncClient(base_url='https://example.test',transport=httpx.MockTransport(transport))
  try:
   with pytest.raises(DecisionUnavailable):await c.chat('system','task',known_overlay())
  finally:await c._http.aclose()
 asyncio.run(run());assert posted==[]


def test_known_notification_frame_never_reaches_jev_http():
 posted=[]
 def transport(request):posted.append(request);return httpx.Response(500)
 async def run():
  c=Jev('fake-test-key',transport=httpx.MockTransport(transport))
  try:
   with pytest.raises(DecisionUnavailable):await c.evaluate({}, {'ready':{'type':'noul','instructions':'check'}},known_overlay())
  finally:await c.close()
 asyncio.run(run());assert posted==[]


def test_ui_only_overlay_needs_both_cues_and_does_not_match_public_frame():
 from hidagent.frame_privacy import known_excluded_overlay
 from PIL import ImageDraw
 assert known_excluded_overlay(known_overlay())=='notification_center_known_layout'
 im=Image.open(io.BytesIO(known_overlay()));ImageDraw.Draw(im).rectangle((521,659,558,699),fill='white')
 b=io.BytesIO();im.save(b,format='PNG');assert known_excluded_overlay(b.getvalue()) is None
 assert known_excluded_overlay(b'not an image') is None
 assert known_excluded_overlay(None) is None


def test_hosted_meta_inherits_no_notification_upload():
 from hidagent.meta_model import MetaCloudLLM
 async def run():
  c=MetaCloudLLM('muse-spark-1.3','fake-test-key')
  c._post=__import__('unittest.mock',fromlist=['AsyncMock']).AsyncMock()
  try:
   with pytest.raises(DecisionUnavailable):await c.chat('system','task',known_overlay())
   c._post.assert_not_awaited()
  finally:await c._http.aclose()
 asyncio.run(run())

@pytest.mark.parametrize('app',['chatgpt','grok'])
def test_known_assistant_history_sidebar_is_denied_before_any_cloud_http(app):
 from hidagent.frame_privacy import known_excluded_overlay
 p=app_controls.ROOT/('system-ui/'+app+'-sidebar-exclusion.json');d=json.loads(p.read_text());im=Image.new('RGB',(710,1080),'white')
 for spec in d['patches'].values():
  with Image.open(p.parent/spec['file']) as patch:im.paste(patch,spec['box'][:2])
 b=io.BytesIO();im.save(b,format='PNG');frame=b.getvalue()
 assert known_excluded_overlay(frame)==app+'_history_sidebar_known_layout'
 posted=[]
 async def run():
  c=Jev('fake-test-key',transport=httpx.MockTransport(lambda r:posted.append(r)))
  try:
   with pytest.raises(DecisionUnavailable):await c.evaluate({}, {'ready':{'type':'noul','instructions':'check'}},frame)
  finally:await c.close()
 asyncio.run(run());assert posted==[]

def test_known_password_fill_sheet_is_denied_before_cloud():
 from hidagent.frame_privacy import check_cloud_frame,known_excluded_overlay
 p=app_controls.ROOT/'system-ui/password-fill-exclusion.json';d=json.loads(p.read_text());im=Image.new('RGB',(710,1080),'white')
 for spec in d['patches'].values():
  with Image.open(p.parent/spec['file']) as patch:im.paste(patch,spec['box'][:2])
 b=io.BytesIO();im.save(b,format='PNG')
 assert known_excluded_overlay(b.getvalue())=='password_fill_known_layout'
 with pytest.raises(DecisionUnavailable):check_cloud_frame(b.getvalue())


@pytest.mark.parametrize('kind',['llm','meta','jev'])
def test_landscape_upload_boundaries_emit_flag_free_privacy_field(caplog,kind):
 from hidagent.meta_model import MetaCloudLLM
 output=io.BytesIO();Image.new('RGB',(1646,1080),'white').save(output,format='PNG');image=output.getvalue()
 async def run():
  posted=[]
  def transport(request):
   posted.append(request)
   return httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}],
     'answers':{'ready':{'type':'noul','noul':1}}})
  client=Jev('fake-test-key',transport=httpx.MockTransport(transport)) if kind=='jev' else MetaCloudLLM('muse-spark-1.3','fake-test-key') if kind=='meta' else LLM('https://example.test','test')
  caplog.clear()
  if kind!='jev':
   await client._http.aclose();client._http=httpx.AsyncClient(base_url='https://example.test',transport=httpx.MockTransport(transport))
  try:
   if kind=='jev':await client.evaluate({}, {'ready':{'type':'noul','instructions':'check'}},image)
   else:assert await client.chat('system','task',image)=='ok'
  finally:
   await (client.close() if kind=='jev' else client._http.aclose())
  assert len(posted)==1
  records=[r for r in caplog.records if getattr(r,'privacy_templates',None)=='unavailable_for_geometry']
  assert len(records)==1,kind
  assert 'privacy_templates=unavailable_for_geometry' in records[0].getMessage()
 asyncio.run(run())


def test_landscape_saved_trace_carries_privacy_coverage(tmp_path):
 from hidagent.debug import Trace
 output=io.BytesIO();Image.new('RGB',(1646,1080),'white').save(output,format='PNG')
 trace=Trace(tmp_path);trace.start('Synthetic landscape check')
 trace.write(output.getvalue(),kind='shortcut-check')
 record=json.loads((trace.dir/'steps.jsonl').read_text().splitlines()[-1])
 assert record['privacy_templates']=='unavailable_for_geometry'
 assert (trace.dir/record['frame']).read_bytes()==output.getvalue()
