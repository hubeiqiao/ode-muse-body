"""Synthetic protocol acceptance only, never publication/device evidence."""
import asyncio,io,json
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
from PIL import Image,ImageDraw
import pytest
from pydantic import ValidationError
from hidagent.grok_preview import Preview,Box,Source,allowed_point,same_region,same_empty_editor
from hidagent.app_tools import ActionPreviewArgs,TOOLS
from hidagent.vision_judge import JudgeFallback
from hidagent.decision import DecisionUnavailable
from hidagent.device import DeviceError

def encoded(label):
 im=Image.new('RGB',(710,1080),'black');ImageDraw.Draw(im).text((80,180),label,fill='white');b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()
SOURCE={'card_id':'20261027-06','target_handle':'@learner','card_box':{'left':.01,'top':.08,'right':.95,'bottom':.4},'copy':{'left':.82,'top':.16,'right':.85,'bottom':.19},'open_target':{'left':.65,'top':.12,'right':.82,'bottom':.15}}

def build(*,empty=1,stop_copy=False,fail_paste=False,source=None):
 args={'mode':'preview','card_id':'20261027-06','target_handle':'@learner'};binding={'device':'ipad','orientation':'portrait','width':710,'height':1080,'calibration_key':'qa'}
 screen=SimpleNamespace(frame=encoded('source'),live=True,settled=AsyncMock());phone=SimpleNamespace(target_info=lambda:binding,device=SimpleNamespace(connected=True,links=1),calibrated=True);sent=[];tap_count=0
 entry={'status':'in_progress'};tools=SimpleNamespace(epoch=0)
 def active(epoch):
  if epoch!=tools.epoch:raise asyncio.CancelledError()
 tools._active=active
 async def tap(x,y,**kw):
  nonlocal tap_count
  if 'verified_control_box' in kw:phone.last_pointer_check={'target':[x,y],'observed':[x,y],'control_hit_confirmed':True,'control_box':kw['verified_control_box']}
  assert kw['aim'] and kw['pre_click']();tap_count+=1;sent.append(('tap',x,y));screen.frame=encoded('stage'+str(tap_count))
  if stop_copy and tap_count==1:tools.epoch+=1
 async def key(text):
  sent.append(('key',text));screen.frame=encoded('preview')
  if fail_paste and text=='cmd+v':raise DeviceError('lost ACK',delivery='unknown',command_name='key')
 phone.tap=AsyncMock(side_effect=tap);phone.key=AsyncMock(side_effect=key);phone.point=AsyncMock();phone.forget_position=Mock()
 async def swipe(*points):sent.append(('swipe',*points));screen.frame=encoded('account')
 phone.swipe=AsyncMock(side_effect=swipe)
 async def judge(state,questions,frame):
  p=state['policy'].removeprefix('grok_preview_');a={'ready':{'noul':1},'blocked':{'noul':0}}
  if p=='copy_confirmation':a['glyph_state']={'choice':'copied_checkmark','confidence':1}
  if p=='target':
   a['target']={'box':{'left':.17,'top':.28,'right':.21,'bottom':.32},'confidence':1}
   a['post']={'box':{'left':.05,'top':.1,'right':.92,'bottom':.35},'confidence':1}
  if p in ('empty','empty_after_copy'):a['empty']={'noul':empty}
  if p=='preview':a.update(same={'noul':1},fits={'noul':1})
  if p=='cleanup':a.update(discard={'noul':1},closed={'noul':0},target={'box':{'left':.4,'top':.4,'right':.6,'bottom':.6},'confidence':1})
  return {'answers':a,'seconds':.01,'model':'synthetic'}
 a=SimpleNamespace(phone=phone,screen=screen,running=False,actor=SimpleNamespace(model='synthetic',chat=AsyncMock(return_value=json.dumps(source or SOURCE))),client=SimpleNamespace(evaluate=AsyncMock(side_effect=judge)))
 tools.result_reader=SimpleNamespace(agent=a)
 return tools,entry,args,sent

def patches():
 from contextlib import ExitStack
 async def refine(client,frame,coarse,description,*,container,check_active=None,symbol=False,minimum_confidence=.5,text_label=False,pixel_identity=False,content_region=False,proposal_confidence=None):
  return {'box':coarse,'seconds':.01,'model':'synthetic','fallback_used':False}
 def match(frame,app,target,**kw):
  if kw.get('control')=='preview_post_profile_tab':return {'template':'qa','x':688,'y':956,'target_box':{'left':474/710,'top':1019/1080,'right':504/710,'bottom':1049/1080}}
  if kw.get('control')=='preview_return_home_tab':return {'template':'qa','x':63,'y':956,'target_box':{'left':31/710,'top':1020/1080,'right':59/710,'bottom':1047/1080}}
  return {'template':'qa'}
 stack=ExitStack();stack.enter_context(patch('hidagent.grok_preview.refine_control',new=AsyncMock(side_effect=refine)));stack.enter_context(patch('hidagent.grok_preview.verify_content_center',new=AsyncMock(side_effect=refine)));stack.enter_context(patch('hidagent.grok_preview.public_search_control',side_effect=match));stack.enter_context(patch('hidagent.grok_preview.same_region',return_value=True));stack.enter_context(patch('hidagent.grok_preview.known_excluded_overlay',return_value=None));return stack

def test_continuous_preview_never_submits_and_discards_owned_text():
 async def run():
  t,e,args,sent=build()
  with patches():r=await Preview(t,e,args,0).run()
  assert r['preview_verified'] and r['cleanup_verified'] and not r['published'] and not r['acknowledged']
  assert [x for x in sent if x[0]=='key']==[('key','cmd+v'),('key','escape')]
  assert len([x for x in sent if x[0]=='tap'])==6
  assert r['native_copy_confirmation']
  phases=[x['phase'] for x in r['phases']]
  assert phases.index('copy')<phases.index('empty')<phases.index('paste_once')
  assert phases.index('copy_confirmation')<phases.index('paste_once')
  assert 'preview' in e['_preview_frames'] and r['never_retry_typing']
 asyncio.run(run())

@pytest.mark.parametrize('empty',[0,.98,True,None])
def test_existing_or_uncertain_draft_never_receives_paste(empty):
 async def run():
  t,e,args,sent=build(empty=empty)
  with patches(),pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert not [x for x in sent if x==('key','cmd+v')] and not e['_preview_report']['paste_attempted']
 asyncio.run(run())

def test_stop_even_if_tap_returns_prevents_next_phase():
 async def run():
  t,e,args,sent=build(stop_copy=True)
  with patches(),pytest.raises(asyncio.CancelledError):await Preview(t,e,args,0).run()
  assert len(sent)==1 and sent[0][0]=='tap'
 asyncio.run(run())

def test_uncertain_native_paste_never_repeats_or_runs_cleanup():
 async def run():
  t,e,args,sent=build(fail_paste=True)
  with patches(),pytest.raises(DeviceError):await Preview(t,e,args,0).run()
  assert sent[-1]==('key','cmd+v') and sum(v==('key','cmd+v') for v in sent)==1
  assert e['_preview_report']['paste_attempted'] and e['_preview_report']['never_retry_typing']
 asyncio.run(run())

def test_source_changed_during_planning_prevents_all_input():
 async def run():
  t,e,args,sent=build()
  with patches(),patch('hidagent.grok_preview.same_region',return_value=False),pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert not sent
 asyncio.run(run())

def test_explicit_card_mismatch_prevents_all_input():
 async def run():
  t,e,args,sent=build(source={**SOURCE,'card_id':'20261027-05'})
  with patches(),pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert not sent
 asyncio.run(run())

@pytest.mark.parametrize('role,x,y',[('publish',.93,.07),('send',.94,.96),('reply',.93,.07),('reply',.86,.91),('discard',.86,.91),('cancel',.93,.07),('open_target',.8,.97)])
def test_role_allowlist_cannot_target_known_submit_controls(role,x,y):assert not allowed_point(role,x,y)

def test_registered_tool_exposes_no_publish_option_or_body_override():
 args={'card_id':'20261027-06','target_handle':'@learner'}
 assert TOOLS['grok.preview_reply']['schema'] is ActionPreviewArgs
 for extra in ({'publish':True},{'mode':'publish'},{'text':'invented'},{'cleanup':False}):
  with pytest.raises(ValidationError):ActionPreviewArgs.model_validate({**args,**extra})

def test_preview_policy_keeps_independent_empty_and_length_gates():
 q={k:{} for k in ('ready','blocked','same','fits')};a={k:{'noul':1} for k in ('ready','same','fits')};a['blocked']={'noul':0}
 assert JudgeFallback._approved({'policy':'grok_preview_preview'},q,a)
 assert not JudgeFallback._approved({'policy':'grok_preview_preview'},q,{**a,'fits':{'noul':.98}})
 assert not JudgeFallback._approved({'policy':'grok_preview_preview'},q,{**a,'same':{'noul':True}})

def test_region_guard_rejects_card_changes_but_can_mask_bounded_copy_hover():
 a=encoded('one');b=encoded('two');box=Box(left=.01,top=.1,right=.95,bottom=.4)
 assert not same_region(a,b,box)
 assert same_region(a,a,box)
 assert not same_region(a,b,box,((.8,.3),))

def test_observed_status_badge_header_requires_all_independent_anchors():
 from hidagent.app_controls import ROOT,public_search_control
 from PIL import ImageDraw
 from pathlib import Path
 spec=json.loads((ROOT/'grok/joe-action-status.json').read_text());im=Image.new('RGB',(710,1080),'black')
 for patch in spec['patches'].values():im.paste(Image.open(ROOT/'grok'/patch['file']),tuple(patch['box'][:2]))
 def match(picture):
  b=io.BytesIO();picture.save(b,format='PNG');return public_search_control(b.getvalue(),'Grok Bot',{'device':'ipad','orientation':'portrait'},control='example_creator_action_pane',template='joe-action-status.json')
 assert match(im)
 ImageDraw.Draw(im).rectangle(spec['patches']['workspace_name']['box'],fill='black')
 assert not match(im)

def test_copy_checkmark_timeout_mask_is_glyph_only_not_body():
 im=Image.new('RGB',(710,1080),'black');b=io.BytesIO();im.save(b,format='PNG');a=b.getvalue()
 changed=im.copy();ImageDraw.Draw(changed).rectangle((588,172,603,190),fill='white');b=io.BytesIO();changed.save(b,format='PNG');after=b.getvalue()
 region=Box(left=.02,top=.12,right=.9,bottom=.23);glyph=Box(left=.82,top=.15,right=.86,bottom=.18)
 assert not same_region(a,after,region)
 assert same_region(a,after,region,ignore_boxes=(glyph,))
 ImageDraw.Draw(changed).rectangle((50,180,65,195),fill='white');b=io.BytesIO();changed.save(b,format='PNG')
 assert not same_region(a,b.getvalue(),region,ignore_boxes=(glyph,))
 assert not same_region(a,after,region,ignore_boxes=(region,))
 with pytest.raises(ValidationError):Source.model_validate({**SOURCE,'copy':{'left':.5,'top':.15,'right':.95,'bottom':.22}})

def test_native_link_hover_still_requires_actual_bright_glyphs():
 from hidagent.app_controls import ROOT,bright_glyphs_match
 from PIL import ImageChops
 spec=json.loads((ROOT/'grok/copy-compact.json').read_text())['patches']['target_link']
 expected=Image.open(ROOT/'grok'/spec['file']).convert('RGB')
 assert bright_glyphs_match(expected,expected)
 assert not bright_glyphs_match(expected,Image.new('RGB',expected.size,'white'))
 assert not bright_glyphs_match(expected,Image.new('RGB',expected.size,'black'))
 assert not bright_glyphs_match(expected,ImageChops.multiply(expected,Image.new('RGB',expected.size,(110,110,110))))

def test_stop_during_postclick_observation_parking_never_clicks_next_control():
 async def run():
  t,e,args,sent=build()
  async def revoke(*a,**kw):
   if t.result_reader.agent.phone.point.await_count==1:t.epoch+=1
  t.result_reader.agent.phone.point.side_effect=revoke
  with patches(),pytest.raises(asyncio.CancelledError):await Preview(t,e,args,0).run()
  assert len(sent)==1 and sent[0][0]=='tap'
  assert not e['_preview_report']['paste_attempted']
 asyncio.run(run())


def test_side_by_side_proof_uses_declared_jpeg_transport():
 from hidagent.grok_preview import paired_image
 proof=paired_image(encoded('original'),encoded('preview'))
 with Image.open(io.BytesIO(proof)) as im:
  assert im.format=='JPEG' and im.size==(1420,1080)


def test_empty_editor_waits_for_strict_same_frame_without_masking_text():
 async def run():
  t,e,args,sent=build();tries=0
  def identical(*a,**kw):
   nonlocal tries
   if e['_preview_report'].get('phase')=='empty':
    tries+=1;return tries>=3
   return True
  with patches(),patch('hidagent.grok_preview.same_region',side_effect=identical):r=await Preview(t,e,args,0).run()
  assert tries>=3 and r['cleanup_verified']
  assert sent.count(('key','cmd+v'))==1
 asyncio.run(run())

def test_filled_composer_is_read_only_ready_signal_not_publication_permission():
 from hidagent.app_controls import ROOT,public_search_control
 from PIL import ImageEnhance
 path=ROOT/'x/preview-filled.json';spec=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'black')
 for p in spec['patches'].values():
  with Image.open(path.parent/p['file']) as patch_image:im.paste(patch_image,p['box'][:2])
 def frame(im):
  b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()
 target={'device':'ipad','orientation':'portrait'}
 assert public_search_control(frame(im),'X',target,control='preview_filled_composer')
 assert not public_search_control(frame(ImageEnhance.Brightness(im).enhance(.5)),'X',target,control='preview_filled_composer')
 ImageDraw.Draw(im).rectangle(tuple(spec['patches']['enabled_post']['box']),fill='black')
 assert not public_search_control(frame(im),'X',target,control='preview_filled_composer')
 assert not allowed_point('publish',.933,.07)


@pytest.mark.parametrize('discard,closed',[(1,1),(0,0),(.98,0),(1,.02),(True,0)])
def test_cleanup_requires_one_unambiguous_owned_state(discard,closed):
 q={'ready':{},'blocked':{},'discard':{},'closed':{},'target':{}}
 a={'ready':{'noul':1},'blocked':{'noul':0},'discard':{'noul':discard},'closed':{'noul':closed},'target':{'confidence':1}}
 assert not JudgeFallback._approved({'policy':'grok_preview_cleanup'},q,a)




def test_owned_discard_sheet_needs_all_native_labels():
 from hidagent.app_controls import ROOT,public_search_control
 path=ROOT/'x/preview-discard.json';spec=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'black')
 for p in spec['patches'].values():
  with Image.open(path.parent/p['file']) as patch_image:im.paste(patch_image,p['box'][:2])
 def frame(im):
  b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()
 t={'device':'ipad','orientation':'portrait'}
 assert public_search_control(frame(im),'X',t,control='preview_discard_prompt')
 ImageDraw.Draw(im).rectangle(tuple(spec['patches']['discard']['box']),fill='black')
 assert not public_search_control(frame(im),'X',t,control='preview_discard_prompt')
 assert public_search_control(frame(im),'X',t,control='preview_discard_prompt',anchors_only=True)
 ImageDraw.Draw(im).rectangle(tuple(spec['patches']['keep']['box']),fill='black')
 assert not public_search_control(frame(im),'X',t,control='preview_discard_prompt',anchors_only=True)



@pytest.mark.parametrize('click_entered',[False,True])
def test_one_proven_no_click_recovery_never_repeats_attempted_click(click_entered):
 from hidagent.phone import PhoneUnavailable
 async def run():
  t,e,args,sent=build();phone=t.result_reader.agent.phone;normal=phone.tap.side_effect;failed=False
  async def tap(x,y,**kw):
   nonlocal failed
   timing={'target':[x,y],'outcome':'failed'};phone.last_tap_timing=timing
   if (x,y)==(.835,.175) and not failed:
    failed=True
    if click_entered:timing['click_s']=.01
    raise PhoneUnavailable('synthetic refusal')
   await normal(x,y,**kw);timing.update(outcome='ok',click_s=.01)
  phone.tap.side_effect=tap
  with patches():
   if click_entered:
    with pytest.raises(PhoneUnavailable):await Preview(t,e,args,0).run()
    assert not e['_preview_report'].get('no_click_recoveries') and ('key','cmd+v') not in sent
   else:
    r=await Preview(t,e,args,0).run()
    assert r['cleanup_verified'] and len(r['no_click_recoveries'])==1
    assert sum(x==('tap',.835,.175) for x in sent)==1 and sent.count(('key','cmd+v'))==1
 asyncio.run(run())

def test_no_click_recovery_requires_original_context_to_still_match():
 from hidagent.phone import PhoneUnavailable
 async def run():
  t,e,args,sent=build();phone=t.result_reader.agent.phone;normal=phone.tap.side_effect;changed=False
  async def tap(x,y,**kw):
   nonlocal changed
   if (x,y)==(.835,.175):
    changed=True;phone.last_tap_timing={'target':[x,y],'outcome':'failed'};raise PhoneUnavailable('synthetic refusal')
   await normal(x,y,**kw)
  phone.tap.side_effect=tap
  with patches(),patch('hidagent.grok_preview.same_region',side_effect=lambda *a,**kw:not changed),pytest.raises(PhoneUnavailable):await Preview(t,e,args,0).run()
  assert not e['_preview_report'].get('no_click_recoveries') and ('key','cmd+v') not in sent
 asyncio.run(run())




def test_empty_editor_caret_change_only_does_not_hide_body_or_geometry_changes():
 def picture(caret=False,text=False,wide=False,size=(710,1080)):
  im=Image.new('RGB',size,'black');d=ImageDraw.Draw(im)
  if caret:d.rectangle((114,360,115 if not wide else 123,383),fill=(0,170,255))
  if text:d.rectangle((114,361,135,381),fill='white')
  b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()
 assert same_empty_editor(picture(),picture(caret=True),(.186,.304))
 assert not same_empty_editor(picture(),picture(caret=True,text=True),(.186,.304))
 assert not same_empty_editor(picture(),picture(caret=True,wide=True),(.186,.304))
 assert not same_empty_editor(picture(),picture(caret=True,size=(1080,710)),(.186,.304))

@pytest.mark.parametrize('native',[True,False])
def test_source_locator_uses_existing_native_schema_without_changing_local_guards(native):
 async def run():
  t,e,args,sent=build();actor=t.result_reader.agent.actor
  actor.supports_native_json=native
  with patches():source,_=await Preview(t,e,args,0).locate_source()
  assert source.card_id==args['card_id'] and not sent
  kwargs=actor.chat.call_args.kwargs
  if native:
   schema=kwargs['json_schema']
   assert schema['type']=='object' and schema['additionalProperties'] is False
   assert set(schema['required'])==set(Source.model_fields)
   for key in ('card_box','copy','open_target'):
    box=schema['properties'][key]
    assert box['additionalProperties'] is False and set(box['required'])=={'left','top','right','bottom'}
  else:assert 'json_schema' not in kwargs
 asyncio.run(run())

def test_account_navigation_guard_excludes_feed_but_keeps_header_changes():
 from hidagent.grok_preview import account_navigation_box
 im=Image.new('RGB',(710,1080),'black');ImageDraw.Draw(im).text((320,42),'X',fill='white')
 def data(pic):
  b=io.BytesIO();pic.save(b,format='PNG');return b.getvalue()
 old=data(im);loaded=im.copy();ImageDraw.Draw(loaded).rectangle((75,113,390,120),fill='white')
 # Loading placeholder is below the actual navigation, not its account target.
 assert same_region(old,data(loaded),account_navigation_box(),((.049,.043),))
 changed=im.copy();ImageDraw.Draw(changed).rectangle((320,40,350,65),fill='white')
 assert not same_region(old,data(changed),account_navigation_box(),((.049,.043),))
 assert account_navigation_box().top<.052<account_navigation_box().bottom

def test_source_refines_actual_control_centers_before_independent_verification():
 async def run():
  t,e,args,sent=build()
  async def refine(client,frame,coarse,description,*,container,check_active=None,symbol=False,minimum_confidence=.5,text_label=False,pixel_identity=False,content_region=False,proposal_confidence=None):
   return {'box':coarse,'seconds':.01,'model':'synthetic','fallback_used':False}
  with patches(),patch('hidagent.grok_preview.refine_control',new=AsyncMock(side_effect=refine),create=True) as exact:
   await Preview(t,e,args,0).locate_source()
  assert exact.await_count==2 and not sent
  assert len(e['_preview_report']['grounding'])==2
 asyncio.run(run())

def test_full_source_ownership_rejection_prevents_local_grounding():
 async def run():
  t,e,args,sent=build();t.result_reader.agent.client.evaluate=AsyncMock(return_value={'answers':{'ready':{'noul':0},'blocked':{'noul':1}}})
  with patches(),patch('hidagent.grok_preview.refine_control',new=AsyncMock()) as exact,pytest.raises(DecisionUnavailable):
   await Preview(t,e,args,0).locate_source()
  exact.assert_not_awaited();assert not sent
 asyncio.run(run())

def test_stop_during_local_grounding_prevents_any_input_even_if_model_returns():
 async def run():
  t,e,args,sent=build()
  async def refine(client,frame,coarse,description,*,container,check_active=None,symbol=False,minimum_confidence=.5,text_label=False,pixel_identity=False,content_region=False,proposal_confidence=None):
   t.epoch+=1
   return {'box':coarse,'seconds':.01,'model':'synthetic'}
  with patches(),patch('hidagent.grok_preview.refine_control',new=AsyncMock(side_effect=refine)),pytest.raises(asyncio.CancelledError):
   await Preview(t,e,args,0).locate_source()
  assert not sent
 asyncio.run(run())

def test_hover_style_context_keeps_click_center_and_strict_glyph_and_body_guards():
 from hidagent.control_grounding import label_hover_context
 im=Image.new('RGB',(710,1080),'black');draw=ImageDraw.Draw(im)
 draw.rectangle((325,452,340,469),outline='white',width=2);draw.text((346,452),'Open Target Post',fill='white');draw.text((30,510),'original source body',fill='white')
 def data(picture):
  out=io.BytesIO();picture.save(out,format='PNG');return out.getvalue()
 before=data(im);hovered=im.copy();ImageDraw.Draw(hovered).rectangle((325,452,340,469),outline=(220,220,220),width=2)
 label=Box(left=343/710,top=448/1080,right=435/710,bottom=475/1080);owned=Box(left=.02,top=.38,right=.92,bottom=.55)
 context=Box(**label_hover_context(before,label.model_dump(),owned.model_dump()))
 assert context.left<label.left and context.right>label.right
 assert context.center()==pytest.approx(label.center())
 assert same_region(before,data(hovered),owned,hover_label=context)
 changed=hovered.copy();ImageDraw.Draw(changed).rectangle((30,510,100,526),fill='white')
 assert not same_region(before,data(changed),owned,hover_label=context)
 changed=hovered.copy();ImageDraw.Draw(changed).rectangle((350,452,400,469),fill='white')
 assert not same_region(before,data(changed),owned,hover_label=context)

def test_same_region_handles_balanced_chroma_noise_but_not_color_or_glyph_changes():
 im=Image.new('RGB',(32,32),'black');d=ImageDraw.Draw(im);d.rectangle((3,3,13,28),fill=(20,130,220));d.rectangle((18,3,28,28),fill=(20,130,220))
 noisy=im.copy()
 for x in range(32):
  for y in range(32):
   if im.getpixel((x,y))!=(0,0,0):noisy.putpixel((x,y),(40,120,240) if (x+y)%2 else (0,140,200))
 def encoded_picture(pic):
  b=io.BytesIO();pic.save(b,format='PNG');return b.getvalue()
 region=Box(left=0,top=0,right=1,bottom=1)
 assert same_region(encoded_picture(im),encoded_picture(noisy),region)
 recolored=im.copy();ImageDraw.Draw(recolored).rectangle((3,3,13,28),fill=(220,50,30))
 assert not same_region(encoded_picture(im),encoded_picture(recolored),region)
 shifted=Image.new('RGB',(32,32),'black');shifted.paste(im,(5,0))
 assert not same_region(encoded_picture(im),encoded_picture(shifted),region)

def test_editor_changed_after_empty_check_prevents_paste_without_app_switch():
 async def run():
  t,e,args,sent=build();original=t.result_reader.agent.client.evaluate
  async def judge(state,questions,frame):
   result=await original(state,questions,frame)
   if state['policy']=='grok_preview_empty':t.result_reader.agent.screen.frame=encoded('unrelated changed editor')
   return result
  t.result_reader.agent.client.evaluate=AsyncMock(side_effect=judge)
  with patches(),patch('hidagent.grok_preview.same_empty_editor',return_value=False),pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert ('key','cmd+v') not in sent and not e['_preview_report']['paste_attempted']
  assert ('key','cmd+tab') not in sent
 asyncio.run(run())

def test_missing_native_copy_confirmation_prevents_return_and_paste():
 async def run():
  t,e,args,sent=build();original=t.result_reader.agent.client.evaluate
  async def judge(state,questions,frame):
   if state['policy']=='grok_preview_copy_confirmation':return {'answers':{'ready':{'noul':0},'blocked':{'noul':1}}}
   return await original(state,questions,frame)
  t.result_reader.agent.client.evaluate=AsyncMock(side_effect=judge)
  with patches(),pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert ('key','cmd+tab') not in sent and ('key','cmd+v') not in sent
 asyncio.run(run())

def test_unexpected_paste_render_never_reaches_cloud_body_check_or_repeats_paste():
 async def run():
  t,e,args,sent=build();v=Preview(t,e,args,0)
  async def wait(predicate,timeout=10):
   if v.report['phase']=='paste_render':raise DecisionUnavailable('unexpected native paste UI')
  v.wait_local=wait
  with patches(),pytest.raises(DecisionUnavailable):await v.run()
  assert sent.count(('key','cmd+v'))==1 and v.report['paste_attempted']
  assert not any(call.args[0]['policy']=='grok_preview_preview' for call in t.result_reader.agent.client.evaluate.await_args_list)
 asyncio.run(run())

def test_stop_after_copy_confirmation_prevents_navigation_and_paste():
 async def run():
  t,e,args,sent=build();original=t.result_reader.agent.client.evaluate
  async def judge(state,questions,frame):
   result=await original(state,questions,frame)
   if state['policy']=='grok_preview_copy_confirmation':t.epoch+=1
   return result
  t.result_reader.agent.client.evaluate=AsyncMock(side_effect=judge)
  with patches(),pytest.raises(asyncio.CancelledError):await Preview(t,e,args,0).run()
  assert ('key','cmd+v') not in sent and ('key','cmd+tab') not in sent
  assert len([x for x in sent if x[0]=='tap'])==1
 asyncio.run(run())

def test_source_navigation_scope_preserves_identity_but_does_not_gate_on_unsent_body():
 from hidagent.grok_preview import source_navigation_region,Source
 source=Source(card_id='20261028-03',target_handle='@example',card_box=Box(left=.02,top=.4,right=.92,bottom=.7),copy=Box(left=.82,top=.5,right=.85,bottom=.52),open_target=Box(left=.45,top=.42,right=.65,bottom=.44))
 hover=Box(left=.42,top=.4,right=.68,bottom=.46)
 region=source_navigation_region(source,hover)
 assert region.top==source.card_box.top and region.bottom==source.open_target.bottom
 im=Image.new('RGB',(710,1080),'black');d=ImageDraw.Draw(im)
 d.rectangle((45,440,65,455),fill='white') # identity outside mutable hover
 def encoded(v):
  b=io.BytesIO();v.save(b,format='PNG');return b.getvalue()
 original=encoded(im);changed=im.copy();ImageDraw.Draw(changed).rectangle((400,600,420,620),fill='white')
 assert same_region(original,encoded(changed),region)
 assert not same_region(original,encoded(changed),source.card_box) # Copy still needs full source
 ImageDraw.Draw(changed).rectangle((45,440,65,455),fill='black')
 assert not same_region(original,encoded(changed),region)


def test_source_navigation_cannot_exclude_part_of_its_link():
 from hidagent.grok_preview import source_navigation_region,Source
 source=Source(card_id='20261028-03',target_handle='@example',card_box=Box(left=.02,top=.4,right=.92,bottom=.7),copy=Box(left=.82,top=.42,right=.85,bottom=.44),open_target=Box(left=.45,top=.42,right=.65,bottom=.47))
 with pytest.raises(DecisionUnavailable):source_navigation_region(source,Box(left=.42,top=.4,right=.68,bottom=.49))

@pytest.mark.parametrize('link',[
 {'left':.01,'right':.65,'top':.42,'bottom':.44},
 {'left':.45,'right':.95,'top':.42,'bottom':.44},
 {'left':.45,'right':.65,'top':.39,'bottom':.44},
])
def test_navigation_scope_rejects_link_partly_outside_owned_card(link):
 from hidagent.grok_preview import source_navigation_region,Source
 source=Source(card_id='20261028-03',target_handle='@example',card_box=Box(left=.02,top=.4,right=.92,bottom=.7),copy=Box(left=.82,top=.5,right=.85,bottom=.52),open_target=Box(**link))
 with pytest.raises(DecisionUnavailable):source_navigation_region(source,Box(left=.01,top=.39,right=.96,bottom=.46))


def test_navigation_scope_requires_style_context_to_cover_complete_link():
 from hidagent.grok_preview import source_navigation_region,Source
 source=Source(card_id='20261028-03',target_handle='@example',card_box=Box(left=.02,top=.4,right=.92,bottom=.7),copy=Box(left=.82,top=.5,right=.85,bottom=.52),open_target=Box(left=.45,top=.42,right=.65,bottom=.44))
 with pytest.raises(DecisionUnavailable):source_navigation_region(source,Box(left=.48,top=.4,right=.68,bottom=.46))


def test_source_copy_precedes_navigation_without_redundant_app_round_trip():
 async def run():
  t,e,args,sent=build()
  with patches():r=await Preview(t,e,args,0).run()
  phases=[x['phase'] for x in r['phases']]
  assert phases.index('copy')<phases.index('open_target')<phases.index('empty')<phases.index('paste_once')
  assert ('key','cmd+tab') not in sent
  assert sent.count(('key','cmd+v'))==1 and r['preview_verified'] and r['cleanup_verified']
 asyncio.run(run())


def test_frame_binds_only_fresh_verified_pointer_and_guard_masks_its_old_footprint():
 async def run():
  t,e,args,sent=build();phone=t.result_reader.agent.phone
  async def tap(x,y,**kw):
   assert kw['pre_click']();phone.last_pointer_check={'target':[x,y],'observed':[x,y],'error':0}
  phone.tap.side_effect=tap
  with patches():
   v=Preview(t,e,args,0);first=v.frame('initial');await v.tap('copy',(.83,.2),first,Box(left=.01,top=.08,right=.95,bottom=.4))
   copied=v.frame('copied')
   with patch('hidagent.grok_preview.same_region',return_value=True) as same:
    await v.tap('open_target',(.7,.15),copied,Box(left=.01,top=.08,right=.95,bottom=.25))
    assert same.call_args.args[3]==((.7,.15),(.83,.2))
 asyncio.run(run())


def test_unverified_or_old_pointer_check_is_not_adopted_for_snapshot_mask():
 async def run():
  t,e,args,sent=build();phone=t.result_reader.agent.phone;old={'target':[.83,.2],'observed':[.83,.2],'error':0};phone.last_pointer_check=old
  with patches():
   v=Preview(t,e,args,0);first=v.frame('initial');await v.tap('copy',(.83,.2),first,Box(left=.01,top=.08,right=.95,bottom=.4));copied=v.frame('copied')
   with patch('hidagent.grok_preview.same_region',return_value=True) as same:
    await v.tap('open_target',(.7,.15),copied,Box(left=.01,top=.08,right=.95,bottom=.25))
    assert same.call_args.args[3]==((.7,.15),)
 asyncio.run(run())


def test_owned_two_option_discard_requires_save_cancel_and_discard_without_delete_permission():
 from hidagent.app_controls import ROOT,public_search_control
 path=ROOT/'x/preview-discard-two.json';spec=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'black')
 for part in spec['patches'].values():
  with Image.open(path.parent/part['file']) as actual:im.paste(actual,part['box'][:2])
 def enc(v):
  b=io.BytesIO();v.save(b,format='PNG');return b.getvalue()
 target={'device':'ipad','orientation':'portrait'}
 assert public_search_control(enc(im),'X',target,control='preview_discard_prompt',template=path.name)
 for name in ('save','cancel','discard'):
  changed=im.copy();ImageDraw.Draw(changed).rectangle(tuple(spec['patches'][name]['box']),fill='black')
  assert not public_search_control(enc(changed),'X',target,control='preview_discard_prompt',template=path.name)
 assert not public_search_control(enc(im),'X',{'device':'iphone','orientation':'portrait'},control='preview_discard_prompt')


def test_stop_during_copy_observation_parking_prevents_navigation_and_paste():
 async def run():
  t,e,args,sent=build();phone=t.result_reader.agent.phone
  async def stop(*args,**kwargs):
   if sent and sent[-1][0]=='tap':t.epoch+=1
  phone.point=AsyncMock(side_effect=stop)
  with patches(),pytest.raises(asyncio.CancelledError):await Preview(t,e,args,0).run()
  assert len(sent)==1 and sent[0][0]=='tap'
  assert not e['_preview_report']['paste_attempted']
 asyncio.run(run())


def test_coarse_reply_candidate_needs_strict_local_and_full_owner_reverification():
 async def run():
  t,e,args,sent=build();client=t.result_reader.agent.client;old=client.evaluate.side_effect
  async def low(state,questions,frame):
   result=await old(state,questions,frame)
   if state['policy']=='grok_preview_target':result['answers']['target']['confidence']=.85
   return result
  client.evaluate=AsyncMock(side_effect=low)
  with patches():r=await Preview(t,e,args,0).run()
  assert r['cleanup_verified']
  assert r['reply_parent_binding']['parent_confidence']==1
  assert not any(x['phase']=='reply_binding' for x in r['checks'])
 asyncio.run(run())


def test_refined_reply_wrong_parent_is_rejected_before_click_or_paste():
 async def run():
  t,e,args,sent=build();client=t.result_reader.agent.client;old=client.evaluate.side_effect
  async def wrong(state,questions,frame):
   result=await old(state,questions,frame)
   if state['policy']=='grok_preview_target':result['answers']['post']={'box':{'left':.05,'top':.4,'right':.92,'bottom':.8},'confidence':1}
   return result
  client.evaluate=AsyncMock(side_effect=wrong)
  with patches(),pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert len([x for x in sent if x[0]=='tap'])==4
  assert not e['_preview_report']['paste_attempted']
 asyncio.run(run())


@pytest.mark.parametrize('choice,confidence',[('uncopied_two_squares',1),('unclear',1),('copied_checkmark',.89)])
def test_copy_state_needs_visible_classification_not_only_ready(choice,confidence):
 async def run():
  t,e,args,sent=build();client=t.result_reader.agent.client;old=client.evaluate.side_effect
  async def reject(state,questions,frame):
   result=await old(state,questions,frame)
   if state['policy']=='grok_preview_copy_confirmation':result['answers']['glyph_state']={'choice':choice,'confidence':confidence}
   return result
  client.evaluate=AsyncMock(side_effect=reject)
  with patches(),pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert len([x for x in sent if x[0]=='tap'])==1
  assert not e['_preview_report']['paste_attempted']
 asyncio.run(run())


def test_navigation_keeps_complete_link_when_disjoint_copy_starts_one_pixel_higher():
 from hidagent.grok_preview import source_navigation_region
 source=Source.model_validate({**SOURCE,'card_box':{'left':.02,'top':.531,'right':.923,'bottom':.66},'copy':{'left':.828,'top':.5787,'right':.848,'bottom':.5917},'open_target':{'left':.5316,'top':.5424,'right':.7303,'bottom':.5797}})
 hover=Box(left=.4748,top=.5315,right=.7871,bottom=.5905)
 result=source_navigation_region(source,hover)
 assert result.bottom>=source.open_target.bottom


def test_navigation_scope_ends_at_complete_link_not_next_code_block():
 from hidagent.grok_preview import source_navigation_region
 source=Source.model_validate({**SOURCE,'card_box':{'left':.02,'top':.534,'right':.885,'bottom':.657},'copy':{'left':.828,'top':.5787,'right':.8465,'bottom':.5908},'open_target':{'left':.5128,'top':.543,'right':.7294,'bottom':.562}})
 hover=Box(left=.4839,top=.534,right=.7584,bottom=.581)
 assert source_navigation_region(source,hover).bottom==source.open_target.bottom






@pytest.mark.parametrize('confidence',[.89,None,True])
def test_parent_post_needs_original_point_nine_confidence(confidence):
 async def run():
  t,e,args,sent=build();client=t.result_reader.agent.client;old=client.evaluate.side_effect
  async def uncertain(state,questions,frame):
   result=await old(state,questions,frame)
   if state['policy']=='grok_preview_target':result['answers']['post']['confidence']=confidence
   return result
  client.evaluate=AsyncMock(side_effect=uncertain)
  with patches(),pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert len([x for x in sent if x[0]=='tap'])==4 and not e['_preview_report']['paste_attempted']
 asyncio.run(run())


def test_primary_target_approval_requires_independent_parent_confidence():
 q={'ready':{'type':'noul'},'blocked':{'type':'noul'},'post':{'type':'box'}}
 for confidence in (.89,None,True):
  assert not JudgeFallback._approved({'policy':'grok_preview_target'},q,{'ready':{'noul':1},'blocked':{'noul':0},'post':{'confidence':confidence}})











def test_native_account_uses_self_profile_and_one_return_to_owned_post():
 async def run():
  t,e,args,sent=build()
  with patches():r=await Preview(t,e,args,0).run()
  assert ('tap',.688,.956) in sent and ('tap',.063,.956) in sent
  assert not [x for x in sent if x[0]=='swipe']
  assert r['account_method']=='native_self_profile'
  assert allowed_point('profile_tab',.688,.956) and allowed_point('home_tab',.063,.956)
  assert not allowed_point('profile_tab',.049,.043) and not allowed_point('home_tab',.5,.5)
 asyncio.run(run())


def test_stop_after_profile_tab_never_returns_or_pastes():
 async def run():
  t,e,args,sent=build();phone=t.result_reader.agent.phone;old=phone.tap.side_effect
  async def stop(x,y,**kw):
   await old(x,y,**kw)
   if (x,y)==(.688,.956):t.epoch+=1
  phone.tap=AsyncMock(side_effect=stop)
  with patches(),pytest.raises(asyncio.CancelledError):await Preview(t,e,args,0).run()
  assert ('tap',.063,.956) not in sent and not e['_preview_report']['paste_attempted']
 asyncio.run(run())


def test_actual_post_arrival_cannot_be_replaced_by_cached_feed():
 async def run():
  t,e,args,sent=build()
  def match(frame,app,target,**kw):
   if kw.get('control')=='preview_post_profile_tab':return None
   return {'template':'qa'}
  async def once(self,predicate,timeout=10):
   self.active()
   if not predicate():raise DecisionUnavailable('Actual Post detail absent')
  with patches(),patch('hidagent.grok_preview.public_search_control',side_effect=match),patch.object(Preview,'wait_local',once),pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert len([x for x in sent if x[0]=='tap'])==2 and not e['_preview_report']['paste_attempted']
 asyncio.run(run())


def test_missing_actual_self_profile_stops_before_return_or_paste():
 async def run():
  t,e,args,sent=build()
  async def once(self,predicate,timeout=10):
   self.active()
   if self.report['phase']=='account_arrival':raise DecisionUnavailable('Actual self profile absent')
   if not predicate():raise DecisionUnavailable('Expected state absent')
  with patches(),patch.object(Preview,'wait_local',once),pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert len([x for x in sent if x[0]=='tap'])==3 and not e['_preview_report']['paste_attempted']
 asyncio.run(run())


def test_pointer_cache_is_invalidated_at_confirmed_app_transition():
 async def run():
  t,e,args,sent=build();phone=t.result_reader.agent.phone;old=phone.tap.side_effect
  async def tap(x,y,**kw):
   if (x,y)==(.688,.956):phone.forget_position.assert_called_once_with()
   await old(x,y,**kw)
  phone.tap=AsyncMock(side_effect=tap)
  with patches():r=await Preview(t,e,args,0).run()
  assert r['pointer_position_invalidated']=='confirmed_native_app_transition'
 asyncio.run(run())


def test_verified_feed_card_opens_detail_once_before_profile_check():
 async def run():
  t,e,args,sent=build();phone=t.result_reader.agent.phone;client=t.result_reader.agent.client;old_tap=phone.tap.side_effect;old_judge=client.evaluate.side_effect;detail=False
  async def tap(x,y,**kw):
   nonlocal detail
   await old_tap(x,y,**kw)
   if (x,y)==(.45,.22):detail=True
  async def judge(state,q,frame):
   r=await old_judge(state,q,frame)
   if state['policy']=='grok_preview_linked_post':r['answers'].update(post={'box':{'left':.05,'top':.1,'right':.92,'bottom':.45},'confidence':1},open_post={'box':{'left':.35,'top':.2,'right':.55,'bottom':.24},'confidence':1})
   return r
  def match(frame,app,target,**kw):
   c=kw.get('control')
   if c=='preview_post_profile_tab':return None if not detail else {'template':'qa','x':688,'y':956,'target_box':{'left':474/710,'top':1019/1080,'right':504/710,'bottom':1049/1080}}
   if c=='preview_return_home_tab':return {'template':'qa','x':63,'y':956,'target_box':{'left':31/710,'top':1020/1080,'right':59/710,'bottom':1047/1080}}
   return {'template':'qa'}
  phone.tap=AsyncMock(side_effect=tap);client.evaluate=AsyncMock(side_effect=judge)
  with patches(),patch('hidagent.grok_preview.public_search_control',side_effect=match):r=await Preview(t,e,args,0).run()
  assert r['cleanup_verified'] and sent.count(('tap',.45,.22))==1
  assert sent.index(('tap',.45,.22))<sent.index(('tap',.688,.956))
 asyncio.run(run())


def test_wrong_linked_feed_target_never_opens_a_post_or_checks_account():
 async def run():
  t,e,args,sent=build();client=t.result_reader.agent.client;old=client.evaluate.side_effect
  async def judge(state,q,frame):
   r=await old(state,q,frame)
   if state['policy']=='grok_preview_linked_post':r['answers'].update(ready={'noul':0},blocked={'noul':1})
   return r
  def match(frame,app,target,**kw):return None if kw.get('control')=='preview_post_profile_tab' else {'template':'qa'}
  client.evaluate=AsyncMock(side_effect=judge)
  with patches(),patch('hidagent.grok_preview.public_search_control',side_effect=match),pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert len([x for x in sent if x[0]=='tap'])==2 and not e['_preview_report']['paste_attempted']
 asyncio.run(run())

@pytest.mark.parametrize('loading,waits',[(1,1),(.98,0),(True,0),(None,0)])
def test_loading_observation_waits_only_for_explicit_transient_and_never_clicks(loading,waits):
 async def run():
  t,e,args,sent=build();p=Preview(t,e,args,0);calls=0
  async def evaluate(*unused):
   nonlocal calls
   calls+=1
   return {'answers':{'ready':{'noul':0},'blocked':{'noul':1},'loading':{'noul':loading}}}
  p.agent.client.evaluate=evaluate;p.wait_local=AsyncMock();p.settled=AsyncMock()
  with patch('hidagent.grok_preview.check_cloud_frame'),patch('hidagent.grok_preview.known_excluded_overlay',return_value=None),pytest.raises(DecisionUnavailable):
   await p.check('linked_post',{'ready':{},'blocked':{},'loading':{}},wait_loading=True)
  assert calls==1+waits and p.wait_local.await_count==waits and not sent
 asyncio.run(run())

def test_loading_recheck_binds_rendered_frame_for_following_grounding():
 async def run():
  t,e,args,sent=build();p=Preview(t,e,args,0);old=p.screen.frame;new=encoded('real rendered target');calls=0
  async def evaluate(*unused):
   nonlocal calls
   calls+=1
   return {'answers':{'ready':{'noul':0 if calls==1 else 1},'blocked':{'noul':1 if calls==1 else 0},'loading':{'noul':1 if calls==1 else 0}}}
  async def arrived(*a,**kw):p.screen.frame=new
  p.agent.client.evaluate=evaluate;p.wait_local=arrived;p.settled=AsyncMock()
  with patch('hidagent.grok_preview.check_cloud_frame'),patch('hidagent.grok_preview.known_excluded_overlay',return_value=None):
   await p.check('linked_post',{'ready':{},'blocked':{},'loading':{}},old,wait_loading=True)
  assert p._verified_check_frames['linked_post']==new and p._verified_check_frames['linked_post']!=old
  assert not sent
 asyncio.run(run())

@pytest.mark.parametrize('proof,anchors',[(False,True),(True,False)])
def test_registered_navigation_needs_fresh_pointer_and_independent_anchors(proof,anchors):
 from hidagent.phone import PhoneUnavailable
 async def run():
  t,e,args,sent=build();p=Preview(t,e,args,0);box=Box(left=.6676,top=.9435,right=.7099,bottom=.9713)
  async def precise(x,y,**kw):
   if proof:p.phone.last_pointer_check={'target':[x,y],'observed':[x,y],'control_hit_confirmed':True,'control_box':box.model_dump()}
   if not kw['pre_click']():raise PhoneUnavailable('no click')
   sent.append(('click',x,y))
  p.phone.tap=AsyncMock(side_effect=precise)
  with patch('hidagent.grok_preview.known_excluded_overlay',return_value=None),pytest.raises(PhoneUnavailable):
   await p.tap('profile_tab',(.688,.956),p.screen.frame,Box(left=0,top=.935,right=1,bottom=.985),extra=lambda:anchors,control_box=box,navigation_guard=True)
  assert not sent
 asyncio.run(run())
