"""Landscape (iPad 1646x1080) Grok -> X geometry. Real UI crops, synthetic frames.

Synthetic protocol acceptance only, never publication/device evidence.
"""
import asyncio,io,json,hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
from PIL import Image,ImageDraw
import pytest
from hidagent.app_controls import ROOT,public_search_control
from hidagent.decision import DecisionUnavailable

LAND={'device':'ipad','orientation':'landscape','width':1646,'height':1080,'calibration_key':'ipad-landscape-66'}
PORT={'device':'ipad','orientation':'portrait','width':710,'height':1080,'calibration_key':'ipad-portrait-66'}

def encoded(im):
 b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()

def compose(rel,shift=None,skip=(),size=(1646,1080),base=None):
 """Paste a template's real crops onto a dark frame; `shift` moves searched patches."""
 path=ROOT/rel;spec=json.loads(path.read_text());im=base or Image.new('RGB',size,(20,20,20))
 moving=set((spec.get('search') or {}).get('patches',()))
 for name,p in spec['patches'].items():
  if name in skip:continue
  dy=shift if (shift is not None and name in moving) else 0
  im.paste(Image.open(path.parent/p['file']).convert('RGB'),(p['box'][0],p['box'][1]+dy))
 return im

# --- geometry selection: no stretching, portrait untouched ---------------------

def test_landscape_registry_never_reuses_portrait_templates_or_stretches():
 frame=encoded(compose('grok/joe-action-landscape.json'))
 hit=public_search_control(frame,'Grok Bot',LAND,control='example_creator_action_pane')
 assert hit and hit['template']=='joe-action-landscape.json'
 # Same landscape pixels under a portrait binding never match.
 assert public_search_control(frame,'Grok Bot',PORT,control='example_creator_action_pane') is None
 # A portrait frame (even with real portrait crops) under a landscape binding never matches.
 portrait=encoded(compose('grok/joe-action.json',size=(710,1080)))
 assert public_search_control(portrait,'Grok Bot',PORT,control='example_creator_action_pane')
 assert public_search_control(portrait,'Grok Bot',LAND,control='example_creator_action_pane') is None
 # Resized landscape is not landscape reference geometry.
 assert public_search_control(encoded(compose('grok/joe-action-landscape.json').resize((1645,1080))),'Grok Bot',LAND,control='example_creator_action_pane') is None

def test_landscape_registry_is_a_closed_allowlist():
 # Public search controls stay portrait-only; landscape must not silently try them.
 frame=encoded(compose('grok/joe-action-landscape.json'))
 for app,control in [('TikTok','public_search_entry'),('X','public_search_entry'),('System UI','excluded_notification_overlay')]:
  assert public_search_control(frame,app,LAND,control=control) is None
 assert public_search_control(frame,'Grok Bot',{**LAND,'orientation':'unknown'},control='example_creator_action_pane') is None
 assert public_search_control(frame,'Grok Bot',{**LAND,'device':'iphone'},control='example_creator_action_pane') is None

def test_landscape_workspace_rejects_open_sidebar_or_other_workspace():
 im=compose('grok/joe-action-landscape.json')
 ImageDraw.Draw(im).rectangle((726,62,800,98),fill=(40,40,40))  # different workspace name
 assert public_search_control(encoded(im),'Grok Bot',LAND,control='example_creator_action_pane') is None
 im=compose('grok/joe-action-landscape.json',skip=('workspace_icon',))
 assert public_search_control(encoded(im),'Grok Bot',LAND,control='example_creator_action_pane') is None

# --- landscape Copy: unique vertical search ----------------------------------

@pytest.mark.parametrize('dy',[-480,-49,0,146,270])
def test_landscape_copy_found_at_actual_card_height(dy):
 frame=encoded(compose('grok/copy-landscape.json',shift=dy))
 hit=public_search_control(frame,'Grok Bot',LAND,control='prepared_reply_copy')
 assert hit and hit['template']=='copy-landscape.json' and hit['dy']==dy
 assert abs(hit['y']/1000-(646+dy)/1080)<.0015 and abs(hit['x']/1000-905/1646)<.0015
 box=hit['target_box'];assert abs(box['top']-(630+dy)/1080)<1e-9 and abs(box['left']-890/1646)<1e-9

def test_landscape_copy_ambiguous_or_out_of_range_or_missing_anchor_never_matches():
 im=compose('grok/copy-landscape.json',shift=0)
 compose('grok/copy-landscape.json',shift=-300,base=im)  # second visible card's Copy
 assert public_search_control(encoded(im),'Grok Bot',LAND,control='prepared_reply_copy') is None
 # Under the translucent header / composer: outside searched band.
 assert public_search_control(encoded(compose('grok/copy-landscape.json',shift=-560)),'Grok Bot',LAND,control='prepared_reply_copy') is None
 assert public_search_control(encoded(compose('grok/copy-landscape.json',shift=320)),'Grok Bot',LAND,control='prepared_reply_copy') is None
 # Missing workspace anchor: no match even anchors-only.
 im=compose('grok/copy-landscape.json',shift=0,skip=('workspace',))
 assert public_search_control(encoded(im),'Grok Bot',LAND,control='prepared_reply_copy') is None
 assert public_search_control(encoded(im),'Grok Bot',LAND,control='prepared_reply_copy',anchors_only=True) is None

def test_landscape_copy_anchors_only_never_returns_a_tap_point():
 im=compose('grok/copy-landscape.json',shift=0,skip=('copy',))  # pointer hover over glyph
 hit=public_search_control(encoded(im),'Grok Bot',LAND,control='prepared_reply_copy',anchors_only=True)
 assert hit and 'x' not in hit and 'y' not in hit and hit['dy'] is None
 assert public_search_control(encoded(im),'Grok Bot',LAND,control='prepared_reply_copy') is None

def test_landscape_card_header_binding_follows_matched_height():
 from hidagent.native_action_copy import same_card_header
 a=compose('grok/copy-landscape.json',shift=100);ImageDraw.Draw(a).text((44,690),'20261029-14 . ZH . @learner',fill='white')
 b=a.copy();ImageDraw.Draw(b).rectangle((1000,300,1600,900),fill='red')  # outside card column
 assert same_card_header(encoded(a),encoded(b),'copy-landscape.json',100)
 c=a.copy();ImageDraw.Draw(c).rectangle((44,690,60,706),fill='blue')  # changed author/index text
 assert not same_card_header(encoded(a),encoded(c),'copy-landscape.json',100)
 assert not same_card_header(encoded(a),encoded(a.resize((710,1080))),'copy-landscape.json',100)
 assert not same_card_header(encoded(a),encoded(a),'copy-landscape.json',2000)
 # Portrait boxes keep their old signature/behaviour.
 p=Image.new('RGB',(710,1080),'white');assert same_card_header(encoded(p),encoded(p))

def _copy_tools(frame):
 phone=SimpleNamespace(target_info=lambda:LAND,device=SimpleNamespace(links=1,connected=True),calibrated=True,tap=AsyncMock())
 a=SimpleNamespace(phone=phone,running=False,screen=SimpleNamespace(frame=frame,live=True))
 done=SimpleNamespace(done=lambda:True,cancelled=lambda:False)
 source={'name':'grok.read_action','status':'completed','job':done,'_action_source_frame':frame,'result':{'verification_passed':True,'workspace':'Example Action Workspace','frame_sha256':hashlib.sha256(frame).hexdigest(),'card_id':'20261029-14','target_handle':'@learner'}}
 tools=SimpleNamespace(calls={'source111':source,'copy1111':{'status':'in_progress'}},result_reader=SimpleNamespace(agent=a),_active=Mock())
 return tools,phone,a

def test_landscape_native_copy_taps_located_glyph_with_pre_click_guard():
 from hidagent.native_action_copy import copy_reply
 frame=encoded(compose('grok/copy-landscape.json',shift=-49))
 tools,phone,a=_copy_tools(frame)
 async def tap(x,y,**kw):
  from hidagent.hid_lease import has_input_lease
  assert has_input_lease() and kw['aim'] and kw['pre_click']()
  assert abs(x-905/1646)<.002 and abs(y-(646-49)/1080)<.002
 phone.tap.side_effect=tap
 r=asyncio.run(copy_reply(tools,'copy1111','source111',0))
 phone.tap.assert_awaited_once()
 assert r['copy_command_acknowledged'] and not r['clipboard_contents_read'] and not r['published']

def test_landscape_native_copy_rejects_scrolled_card_before_any_input():
 from hidagent.native_action_copy import copy_reply
 frame=encoded(compose('grok/copy-landscape.json',shift=-49))
 tools,phone,a=_copy_tools(frame)
 a.screen.frame=encoded(compose('grok/copy-landscape.json',shift=60))  # different card height now
 with pytest.raises(DecisionUnavailable):asyncio.run(copy_reply(tools,'copy1111','source111',0))
 phone.tap.assert_not_awaited()

# --- read_action ---------------------------------------------------------------

def _reader_agent(frame,target):
 card={'card_id':'20261029-14','target_handle':'@learner','target_post_url':None,'reply_text':None}
 return SimpleNamespace(running=False,phone=SimpleNamespace(target_info=lambda:target,device=SimpleNamespace(links=1,connected=True),calibrated=True),screen=SimpleNamespace(frame=frame,live=True),actor=SimpleNamespace(model='test',chat=AsyncMock(return_value=json.dumps(card))),client=SimpleNamespace(evaluate=AsyncMock(return_value={'answers':{'ready':{'noul':1},'blocked':{'noul':0}},'model':'independent','seconds':.1})))

def test_landscape_read_action_uses_landscape_workspace_template():
 from hidagent.grok_action import GrokActionReader
 a=_reader_agent(encoded(compose('grok/joe-action-landscape.json')),LAND)
 with patch('hidagent.frame_privacy.check_cloud_frame'):r=asyncio.run(GrokActionReader(a).read(lambda:None))
 assert r['verification_passed'] and r['card_id']=='20261029-14' and not r['published']
 a.actor.chat.assert_awaited_once()

def test_landscape_read_action_wrong_pane_rejects_before_model():
 from hidagent.grok_action import GrokActionReader
 a=_reader_agent(encoded(Image.new('RGB',(1646,1080),'black')),LAND)
 with pytest.raises(DecisionUnavailable,match='Example Action Workspace pane'):asyncio.run(GrokActionReader(a).read(lambda:None))
 a.actor.chat.assert_not_awaited()

def test_unknown_orientation_still_refuses_read_action():
 from hidagent.grok_action import GrokActionReader
 a=_reader_agent(encoded(compose('grok/joe-action-landscape.json')),{**LAND,'orientation':'unknown'})
 with pytest.raises(DecisionUnavailable):asyncio.run(GrokActionReader(a).read(lambda:None))
 a.actor.chat.assert_not_awaited()

# --- x.verify_account ---------------------------------------------------------

def _account_agent(frame,target=LAND):
 return SimpleNamespace(running=False,phone=SimpleNamespace(calibrated=True,target_info=lambda:target,device=SimpleNamespace(connected=True,links=1)),screen=SimpleNamespace(frame=frame,live=True))

def test_landscape_account_from_exact_self_profile():
 from hidagent.action_review import verify_visible_account
 r=verify_visible_account(_account_agent(encoded(compose('x/joe-profile-landscape.json'))),lambda:None)
 assert r['account']=='@example_creator' and r['self_profile'] and r['source_kind']=='exact_registered_self_profile_ui' and not r['published']

def test_landscape_account_from_exact_reply_composer_header():
 from hidagent.action_review import verify_visible_account
 r=verify_visible_account(_account_agent(encoded(compose('x/reply-account-landscape.json'))),lambda:None)
 assert r['account']=='@example_creator' and r['account_verified'] and not r['self_profile']
 assert r['source_kind']=='exact_registered_reply_composer_account_ui' and r['calibration_key']=='ipad-landscape-66'

def test_landscape_account_rejects_other_handle_or_blank_and_composer_header_is_landscape_only():
 from hidagent.action_review import verify_visible_account
 im=compose('x/reply-account-landscape.json');ImageDraw.Draw(im).rectangle((446,768,600,798),fill='black')
 with pytest.raises(DecisionUnavailable):verify_visible_account(_account_agent(encoded(im)),lambda:None)
 with pytest.raises(DecisionUnavailable):verify_visible_account(_account_agent(encoded(Image.new('RGB',(1646,1080)))),lambda:None)
 # Portrait binding with landscape composer pixels: never accepted.
 with pytest.raises(DecisionUnavailable):verify_visible_account(_account_agent(encoded(compose('x/reply-account-landscape.json')),PORT),lambda:None)

# --- grok.preview_reply landscape envelopes and flow ----------------------------

def test_landscape_role_envelopes_exclude_inline_reply_button():
 from hidagent.grok_preview import allowed_point
 assert allowed_point('copy',905/1646,600/1080,'landscape') and not allowed_point('copy',905/1646,600/1080)
 assert allowed_point('reply_bar',549/1646,935/1080,'landscape')
 # The inline composer Reply/Post control (x .71-.77, y .84-.89) is never a role point.
 for role in ('copy','open_target','open_post','reply_bar','reply','discard','profile_tab','home_tab'):
  assert not allowed_point(role,1213/1646,935/1080,'landscape')
 assert not allowed_point('reply_bar',549/1646,935/1080)  # portrait has no such role

def test_x_landscape_templates_match_their_own_states_only():
 empty=encoded(compose('x/preview-empty-landscape.json'));filled=encoded(compose('x/preview-filled-landscape.json'))
 assert public_search_control(empty,'X',LAND,control='preview_empty_composer')
 assert not public_search_control(filled,'X',LAND,control='preview_empty_composer')
 assert public_search_control(filled,'X',LAND,control='preview_filled_composer')
 assert not public_search_control(empty,'X',LAND,control='preview_filled_composer')
 bar=public_search_control(encoded(compose('x/reply-bar-landscape.json')),'X',LAND,control='preview_reply_bar')
 assert bar and bar['x']/1000<.45 and .82<bar['y']/1000<.91
 # Expanded composer is not a collapsed reply bar.
 assert not public_search_control(empty,'X',LAND,control='preview_reply_bar')
 for rel,control in [('x/preview-filled-landscape.json','preview_filled_composer'),('x/preview-empty-landscape.json','preview_empty_composer')]:
  spec=json.loads((ROOT/rel).read_text());t=spec['patches'][spec['target']]['box']
  assert spec['target'] not in ('reply_enabled','reply_disabled') and not (t[0]>=1100 and t[1]>=900)

LSRC={'card_id':'20261029-14','target_handle':'@learner','card_box':{'left':.014,'top':.44,'right':.578,'bottom':.64},'copy':{'left':.541,'top':.537,'right':.559,'bottom':.564},'open_target':{'left':.027,'top':.49,'right':.17,'bottom':.52}}

def lencoded(label):
 im=Image.new('RGB',(1646,1080),'black');ImageDraw.Draw(im).text((80,180),label,fill='white');return encoded(im)

def lbuild(*,empty=1,same=1,cleaned=1,detail=True,account=True,linked=False):
 args={'mode':'preview','card_id':'20261029-14','target_handle':'@learner'}
 screen=SimpleNamespace(frame=lencoded('source'),live=True,settled=AsyncMock());phone=SimpleNamespace(target_info=lambda:LAND,device=SimpleNamespace(connected=True,links=1),calibrated=True);sent=[];state={'n':0}
 entry={'status':'in_progress'};tools=SimpleNamespace(epoch=0)
 def active(epoch):
  if epoch!=tools.epoch:raise asyncio.CancelledError()
 tools._active=active
 async def tap(x,y,**kw):
  if 'verified_control_box' in kw:phone.last_pointer_check={'target':[x,y],'observed':[x,y],'control_hit_confirmed':True,'control_box':kw['verified_control_box']}
  assert kw['aim'] and kw['pre_click']();state['n']+=1;sent.append(('tap',x,y));screen.frame=lencoded('stage'+str(state['n']))
 async def key(text):sent.append(('key',text));screen.frame=lencoded(text)
 phone.tap=AsyncMock(side_effect=tap);phone.key=AsyncMock(side_effect=key);phone.point=AsyncMock();phone.forget_position=Mock()
 async def judge(st,questions,frame):
  p=st['policy'].removeprefix('grok_preview_');a={'ready':{'noul':1},'blocked':{'noul':0}}
  if p=='copy_confirmation':a['glyph_state']={'choice':'copied_checkmark','confidence':1}
  if p=='empty':a['empty']={'noul':empty}
  if p=='preview':a.update(same={'noul':same},fits={'noul':1})
  if p=='cleaned':a['empty']={'noul':cleaned}
  if p=='linked_post':a.update(post={'box':{'left':.23,'top':.17,'right':.77,'bottom':.6},'confidence':1},open_post={'box':{'left':.55,'top':.19,'right':.58,'bottom':.21},'confidence':1})
  return {'answers':a,'seconds':.01,'model':'synthetic'}
 a=SimpleNamespace(phone=phone,screen=screen,running=False,actor=SimpleNamespace(model='synthetic',chat=AsyncMock(return_value=json.dumps(LSRC))),client=SimpleNamespace(evaluate=AsyncMock(side_effect=judge)))
 tools.result_reader=SimpleNamespace(agent=a)
 def match(frame,app,target,**kw):
  c=kw.get('control');last=sent[-1] if sent else None
  n=len([s for s in sent if s[0]=='tap'])-(1 if linked else 0)
  if c=='example_creator_action_pane':return {'template':'qa'}
  if c=='preview_x_tab_bar':return {'template':'qa'} if any(s[0]=='tap' for s in sent[1:]) else None
  if c=='preview_post_detail':return {'template':'qa'} if detail and n>=2 else None
  if c=='preview_reply_bar':return {'template':'qa','x':334,'y':866,'target_box':{'left':464/1646,'top':914/1080,'right':634/1646,'bottom':956/1080}} if n==2 else None
  if c in ('preview_empty_composer','example_creator_reply_composer'):
   if c=='example_creator_reply_composer' and not account:return None
   return {'template':'qa'} if (n==3 and (last[0]=='tap' or last==('key','backspace'))) else None
  if c=='preview_filled_composer':return {'template':'qa'} if last in (('key','cmd+v'),('key','cmd+a')) else None
  return None
 return tools,entry,args,sent,match

def lpatches(match):
 from contextlib import ExitStack
 async def refine(client,frame,coarse,description,*,container,check_active=None,symbol=False,minimum_confidence=.5,text_label=False,pixel_identity=False,content_region=False,proposal_confidence=None):
  return {'box':coarse,'seconds':.01,'model':'synthetic','fallback_used':False}
 s=ExitStack()
 s.enter_context(patch('hidagent.grok_preview.refine_control',new=AsyncMock(side_effect=refine)))
 s.enter_context(patch('hidagent.grok_preview.verify_content_center',new=AsyncMock(side_effect=refine)))
 s.enter_context(patch('hidagent.grok_preview.public_search_control',side_effect=match))
 s.enter_context(patch('hidagent.grok_preview.same_region',return_value=True))
 s.enter_context(patch('hidagent.grok_preview.known_excluded_overlay',return_value=None))
 s.enter_context(patch('hidagent.grok_preview.check_cloud_frame'))
 return s

def test_landscape_preview_pastes_once_and_clears_by_keyboard_never_reply():
 from hidagent.grok_preview import Preview
 async def run():
  t,e,args,sent,match=lbuild()
  with lpatches(match):r=await Preview(t,e,args,0).run()
  assert r['geometry']=='landscape' and r['preview_verified'] and r['cleanup_verified'] and not r['published'] and not r['acknowledged']
  assert [x for x in sent if x[0]=='key']==[('key','cmd+v'),('key','cmd+a'),('key','backspace')]
  taps=[x for x in sent if x[0]=='tap'];assert len(taps)==3
  assert all(not (x>.6 and y>.8) for _,x,y in taps)  # never the inline Reply corner
  assert r['account']=='@example_creator' and r['account_method']=='inline_reply_composer_header'
  phases=[x['phase'] for x in r['phases']]
  assert phases.index('copy_confirmation')<phases.index('reply_bar_open')<phases.index('empty')<phases.index('paste_once')<phases.index('clear_owned_preview')
 asyncio.run(run())

@pytest.mark.parametrize('kw',[{'empty':.98},{'account':False}])
def test_landscape_preview_no_paste_without_empty_composer_and_exact_account(kw):
 from hidagent.grok_preview import Preview
 async def run():
  t,e,args,sent,match=lbuild(**kw)
  with lpatches(match):
   with pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert ('key','cmd+v') not in sent
 asyncio.run(run())

def test_landscape_preview_mismatched_paste_is_still_cleared_not_submitted():
 from hidagent.grok_preview import Preview
 async def run():
  t,e,args,sent,match=lbuild(same=.5)
  with lpatches(match):
   with pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert [x for x in sent if x[0]=='key']==[('key','cmd+v'),('key','cmd+a'),('key','backspace')]
  assert e['_preview_report']['cleanup_verified'] and not e['_preview_report']['published']
 asyncio.run(run())

def test_portrait_preview_still_rejects_landscape_sized_portrait_binding():
 from hidagent.grok_preview import Preview
 async def run():
  t,e,args,sent,match=lbuild()
  t.result_reader.agent.phone.target_info=lambda:{**LAND,'width':1600}
  with lpatches(match):
   with pytest.raises(DecisionUnavailable):await Preview(t,e,args,0).run()
  assert sent==[]
 asyncio.run(run())

def test_landscape_linked_feed_card_opens_detail_by_timestamp_once():
 from hidagent.grok_preview import Preview
 async def run():
  t,e,args,sent,match=lbuild(linked=True)
  with lpatches(match):r=await Preview(t,e,args,0).run()
  taps=[x for x in sent if x[0]=='tap'];assert len(taps)==4 and r['cleanup_verified'] and not r['published']
  # The detail-opening tap is inside the X content column, far from Reply.
  assert .23<taps[2][1]<.77 and taps[2][2]<.3
  phases=[x['phase'] for x in r['phases']];assert phases.index('open_verified_post')<phases.index('reply_bar_open')
 asyncio.run(run())
