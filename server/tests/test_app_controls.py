import io,json,asyncio
from PIL import Image,ImageDraw
from unittest.mock import AsyncMock
import pytest
from hidagent import app_controls
from hidagent.phone import Phone,PhoneUnavailable

def encoded(im):
 b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()

def test_learned_search_requires_actual_brand_navigation_and_target(tmp_path,monkeypatch):
 im=Image.new('RGB',(400,600),'black');spec={}
 for n,box in {'brand':(100,20,200,60),'home':(10,200,50,240),'search':(10,300,50,340)}.items():
  d=ImageDraw.Draw(im);d.rectangle(box,fill='white');d.line((box[0],box[1],box[2],box[3]),fill='black',width=8)
  im.crop(box).save(tmp_path/(n+'.png'));spec[n]={'box':box,'file':n+'.png'}
 path=tmp_path/'search.json';path.write_text(json.dumps({'app':'Instagram','control':'public_search_entry','reference_size':[400,600],'center':[.075,.53],'anchors':['brand','home'],'target':'search','patches':spec}))
 monkeypatch.setattr(app_controls,'FILES',{('Instagram','public_search_entry'):path});target={'device':'ipad','orientation':'portrait'}
 def match(p,**kw):return app_controls.public_search_control(encoded(p),'Instagram',target,**kw)
 assert match(im)
 video=im.copy();ImageDraw.Draw(video).rectangle((100,200,399,599),fill='red');assert match(video)
 ImageDraw.Draw(video).rectangle((10,300,50,340),fill='blue');assert not match(video)
 assert match(video,anchors_only=True)
 ImageDraw.Draw(video).rectangle((10,200,50,240),fill='blue');assert not match(video,anchors_only=True)
 assert not app_controls.public_search_control(encoded(im),'X',target)
 assert not app_controls.public_search_control(encoded(im),'Instagram',{'device':'iphone','orientation':'portrait'})


def test_pre_click_recheck_runs_after_point_and_can_prevent_click(tmp_path):
 async def run():
  p=Phone(None,tmp_path/'map.json');p._last_move=0;p._send=AsyncMock();p.point=AsyncMock()
  def refuse():
   p.point.assert_awaited_once();return False
  with pytest.raises(PhoneUnavailable,match='no click sent'):await p.tap(.5,.5,pre_click=refuse)
  p._send.assert_not_called()
 asyncio.run(run())


@pytest.mark.parametrize('device,releases',[('ipad',1),('iphone',0)])
def test_button_up_is_reasserted_only_before_free_ipad_point(tmp_path,device,releases):
 async def run():
  p=Phone(None,tmp_path/'map.json');p.span={'w':1000,'h':1000};p.target_info=lambda:{'device':device}
  p._send=AsyncMock();p._move_to=AsyncMock();p.ensure_live=AsyncMock()
  await p.point(.5,.5)
  assert p._send.await_count==releases
  if releases:p._send.assert_awaited_with('release')
  p._move_to.assert_awaited_once()
 asyncio.run(run())

@pytest.mark.parametrize('kind,filename',[('public_search_entry','search.json'),('public_search_entry','search-light.json'),('public_search_form_entry','focus.json'),('public_search_form_entry','results.json'),('public_search_form_entry','results-light.json')])
def test_x_learned_navigation_is_only_valid_with_all_observed_anchors(kind,filename):
 path=app_controls.ROOT/'x'/filename;data=json.loads(path.read_text())
 im=Image.new('RGB',tuple(data['reference_size']),'black')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,tuple(spec['box'][:2]))
 target={'device':'ipad','orientation':'portrait'}
 assert app_controls.public_search_control(encoded(im),'X',target,control=kind)
 changed=im.copy();ImageDraw.Draw(changed).rectangle((200,250,550,700),fill='blue')
 assert app_controls.public_search_control(encoded(changed),'X',target,control=kind)
 for anchor in data['anchors']:
  altered=im.copy();ImageDraw.Draw(altered).rectangle(data['patches'][anchor]['box'],fill='red')
  assert not app_controls.public_search_control(encoded(altered),'X',target,control=kind)
 assert not app_controls.public_search_control(encoded(im),'X',{'device':'ipad','orientation':'landscape'},control=kind)


def test_registered_variant_cannot_silently_change_after_pointer_movement():
 from types import SimpleNamespace
 from hidagent.hybrid_agent import HybridAgent
 path=app_controls.ROOT/'x/results.json';data=json.loads(path.read_text())
 im=Image.new('RGB',tuple(data['reference_size']),'black')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,tuple(spec['box'][:2]))
 target={'device':'ipad','orientation':'portrait'}
 assert app_controls.public_search_control(encoded(im),'X',target,control='public_search_form_entry',template='results.json')
 assert not app_controls.public_search_control(encoded(im),'X',target,control='public_search_form_entry',template='focus.json')
 assert not app_controls.public_search_control(encoded(im),'X',target,control='public_search_form_entry',template='../results.json')
 a=SimpleNamespace(screen=SimpleNamespace(frame=encoded(im),live=True),phone=SimpleNamespace(target_info=lambda:target))
 async def moving(*args,**kwargs):
  # A real change of layout after the move must reject the click instead of
  # accepting a different registered search layout.
  a.screen.frame=encoded(Image.new('RGB',im.size,'white'))
  assert not kwargs['pre_click']()
 a.phone.tap=AsyncMock(side_effect=moving)
 asyncio.run(HybridAgent._do_impl(a,{'action':'tap','x':465,'y':50,'workflow':'app_search','phase':'search_form','_learned_public_search':'X','_learned_search_control':'public_search_form_entry','_learned_control_template':'results.json'}))
 a.phone.tap.assert_awaited_once()


def test_rednote_learned_entry_uses_ui_chrome_and_rejects_changed_navigation():
 path=app_controls.ROOT/'rednote/search.json';data=json.loads(path.read_text())
 im=Image.new('RGB',tuple(data['reference_size']),'black')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,tuple(spec['box'][:2]))
 target={'device':'ipad','orientation':'portrait'}
 hit=app_controls.public_search_control(encoded(im),'rednote',target)
 assert hit and (hit['x'],hit['y'])==(845,37)
 feed=im.copy();ImageDraw.Draw(feed).rectangle((0,110,709,1010),fill='magenta')
 assert app_controls.public_search_control(encoded(feed),'rednote',target)
 for name in data['anchors']+[data['target']]:
  changed=im.copy();ImageDraw.Draw(changed).rectangle(data['patches'][name]['box'],fill='red')
  assert not app_controls.public_search_control(encoded(changed),'rednote',target)
 assert not app_controls.public_search_control(encoded(im),'rednote',{'device':'iphone','orientation':'portrait'})
 assert not app_controls.public_search_control(encoded(im.resize((355,540))),'rednote',target)


def test_tiktok_navigation_ignores_video_but_requires_every_control_glyph():
 path=app_controls.ROOT/'tiktok/search.json';data=json.loads(path.read_text())
 im=Image.new('RGB',tuple(data['reference_size']),'black')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,tuple(spec['box'][:2]))
 target={'device':'ipad','orientation':'portrait'}
 def match(frame):return app_controls.public_search_control(encoded(frame),'TikTok',target)
 assert match(im) and match(im)['x']==956
 ImageDraw.Draw(im).rectangle((51,100,650,1000),fill='magenta')
 assert match(im)
 for name in data['anchors']+[data['target']]:
  bad=im.copy();ImageDraw.Draw(bad).rectangle(data['patches'][name]['box'],fill='red')
  assert not match(bad)
 assert not app_controls.public_search_control(encoded(im),'TikTok',{'device':'ipad','orientation':'landscape'})
 assert not app_controls.public_search_control(encoded(im.resize((355,540))),'TikTok',target)


@pytest.mark.parametrize('filename',['results.json','results-photos.json','results-users-first.json'])
def test_only_registered_region_is_forwarded_and_anchor_guard_remains(filename):
 from types import SimpleNamespace
 from hidagent.hybrid_agent import HybridAgent
 path=app_controls.ROOT/'tiktok'/filename;data=json.loads(path.read_text())
 im=Image.new('RGB',tuple(data['reference_size']),'black')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,tuple(spec['box'][:2]))
 target={'device':'ipad','orientation':'portrait'}
 hit=app_controls.public_search_control(encoded(im),'TikTok',target,control='public_search_form_entry',template=filename)
 assert hit['pointer_region']==[0,.024,1,.095]
 a=SimpleNamespace(screen=SimpleNamespace(frame=encoded(im),live=True),phone=SimpleNamespace(target_info=lambda:target,tap=AsyncMock()))
 action={'action':'tap','x':hit['x'],'y':hit['y'],'workflow':'app_search','phase':'search_form','_learned_public_search':'TikTok','_learned_search_control':'public_search_form_entry','_learned_control_template':filename,'aim_region':[0,0,1,1]}
 asyncio.run(HybridAgent._do_impl(a,action))
 kw=a.phone.tap.await_args.kwargs
 assert kw['aim_region']==[0,.024,1,.095] and kw['pre_click']()
 damaged=im.copy();ImageDraw.Draw(damaged).rectangle(data['patches']['back']['box'],fill='red');a.screen.frame=encoded(damaged)
 assert not kw['pre_click']()


def test_douyin_fixed_navigation_excludes_known_intro_before_and_after_aim():
 path=app_controls.ROOT/'douyin/search.json';data=json.loads(path.read_text())
 im=Image.new('RGB',tuple(data['reference_size']),'black')
 for name in data['anchors']+[data['target']]:
  spec=data['patches'][name]
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,tuple(spec['box'][:2]))
 target={'device':'ipad','orientation':'portrait'}
 def match(frame,**kw):return app_controls.public_search_control(encoded(frame),'Douyin',target,**kw)
 assert match(im)
 ImageDraw.Draw(im).rectangle((70,120,640,980),fill='purple')
 assert match(im)
 covered=im.copy()
 for name in data['blockers'][0]:
  spec=data['patches'][name]
  with Image.open(path.parent/spec['file']) as patch:covered.paste(patch,tuple(spec['box'][:2]))
 assert not match(covered) and not match(covered,anchors_only=True)
 for name in data['anchors']+[data['target']]:
  altered=im.copy();ImageDraw.Draw(altered).rectangle(data['patches'][name]['box'],fill='red');assert not match(altered)
 assert not app_controls.public_search_control(encoded(im),'Douyin',{'device':'iphone','orientation':'portrait'})
 assert not match(im.resize((355,540)))


@pytest.mark.parametrize('blockers',[[[]],[['search']],[['search','search']],[['missing','search']],{}])
def test_malformed_overlay_exclusions_never_enable_navigation(tmp_path,monkeypatch,blockers):
 data=json.loads((app_controls.ROOT/'douyin/search.json').read_text());data['blockers']=[]
 path=tmp_path/'search.json';path.write_text(json.dumps(data));monkeypatch.setattr(app_controls,'FILES',{('Douyin','public_search_entry'):path})
 monkeypatch.setattr(app_controls,'_patches_match',lambda *args:True)
 frame=encoded(Image.new('RGB',(710,1080)));target={'device':'ipad','orientation':'portrait'}
 assert app_controls.public_search_control(frame,'Douyin',target)
 data['blockers']=blockers;path.write_text(json.dumps(data))
 assert not app_controls.public_search_control(frame,'Douyin',target)


def test_youtube_editor_requires_independent_anchors_and_specific_geometry():
 path=app_controls.ROOT/'youtube/field.json';data=json.loads(path.read_text());im=Image.new('RGB',tuple(data['reference_size']),'black')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,tuple(spec['box'][:2]))
 target={'device':'ipad','orientation':'portrait'}
 assert app_controls.public_search_control(encoded(im),'YouTube',target,control='public_search_field')
 for anchor in data['anchors']:
  changed=im.copy();ImageDraw.Draw(changed).rectangle(data['patches'][anchor]['box'],fill='red')
  assert not app_controls.public_search_control(encoded(changed),'YouTube',target,control='public_search_field',anchors_only=True)
 assert not app_controls.public_search_control(encoded(im),'YouTube',{'device':'iphone','orientation':'portrait'},control='public_search_field')
 assert not app_controls.public_search_control(encoded(im),'YouTube',{'device':'ipad','orientation':'landscape'},control='public_search_field')


def test_douyin_transparent_toolbar_requires_all_undimmed_glyphs():
 from PIL import ImageEnhance
 path=app_controls.ROOT/'douyin/search-video.json';data=json.loads(path.read_text())
 im=Image.new('RGB',tuple(data['reference_size']),'#352646')
 for name in data['anchors']+[data['target']]:
  spec=data['patches'][name]
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,tuple(spec['box'][:2]))
 target={'device':'ipad','orientation':'portrait'}
 def match(frame,**kw):return app_controls.public_search_control(encoded(frame),'Douyin',target,template=path.name,**kw)
 assert match(im)
 assert not match(ImageEnhance.Brightness(im).enhance(.65))
 assert not match(Image.new('RGB',im.size,'white'))
 for name in data['anchors']+[data['target']]:
  bad=im.copy();ImageDraw.Draw(bad).rectangle(data['patches'][name]['box'],fill='white')
  assert not match(bad)
 covered=im.copy()
 for name in data['blockers'][0]:
  spec=data['patches'][name]
  with Image.open(path.parent/spec['file']) as patch:covered.paste(patch,tuple(spec['box'][:2]))
 assert not match(covered) and not match(covered,anchors_only=True)


def test_instagram_light_variant_does_not_match_other_themes_or_missing_controls():
 path=app_controls.ROOT/'instagram/search-light.json';data=json.loads(path.read_text())
 im=Image.new('RGB',(710,1080),'white')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,spec['box'][:2])
 target={'device':'ipad','orientation':'portrait'}
 def match(frame):return app_controls.public_search_control(encoded(frame),'Instagram',target,template=path.name)
 assert match(im)
 for name in data['anchors']+[data['target']]:
  bad=im.copy();ImageDraw.Draw(bad).rectangle(data['patches'][name]['box'],fill='white');assert not match(bad)
 assert not app_controls.public_search_control(encoded(im),'Instagram',target,template='unregistered.json')


def test_douyin_results_toolbar_ignores_video_but_requires_each_owned_control():
 path=app_controls.ROOT/'douyin/results.json';data=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'white')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,spec['box'][:2])
 target={'device':'ipad','orientation':'portrait'}
 def match(frame):return app_controls.public_search_control(encoded(frame),'Douyin',target,control='public_search_form_entry')
 assert match(im)
 video=im.copy();ImageDraw.Draw(video).rectangle((0,120,709,1000),fill='green');assert match(video)
 for n in data['anchors']+[data['target']]:
  bad=im.copy();ImageDraw.Draw(bad).rectangle(data['patches'][n]['box'],fill='white');assert not match(bad)
 assert not app_controls.public_search_control(encoded(im),'Douyin',{'device':'iphone','orientation':'portrait'},control='public_search_form_entry')


def test_blank_browser_admission_requires_empty_prompt_and_all_chrome():
 path=app_controls.ROOT/'safari/reddit-blank.json';data=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'white')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,spec['box'][:2])
 target={'device':'ipad','orientation':'portrait'}
 def match(frame):return app_controls.public_search_control(encoded(frame),'Safari',target,control='public_browser_blank_address')
 assert match(im)
 for n in data['anchors']+[data['target']]:
  changed=im.copy();ImageDraw.Draw(changed).rectangle(data['patches'][n]['box'],fill='black');assert not match(changed)


def test_reddit_address_activation_is_bound_to_request_and_post_move_chrome():
 from types import SimpleNamespace
 from hidagent.hybrid_agent import HybridAgent
 from hidagent.decision import DecisionUnavailable
 path=app_controls.ROOT/'safari/reddit-blank.json';data=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'white')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,spec['box'][:2])
 target={'device':'ipad','orientation':'portrait'}
 a=SimpleNamespace(task='Open X',screen=SimpleNamespace(frame=encoded(im),live=True),phone=SimpleNamespace(target_info=lambda:target,tap=AsyncMock()))
 action=dict(action='tap',x=250,y=50,workflow='reddit_web',phase='address_activate',_reddit_blank_address=True)
 with pytest.raises(DecisionUnavailable):asyncio.run(HybridAgent._do_impl(a,action))
 a.phone.tap.assert_not_called()
 a.task='APP_PUBLIC_SEARCH '+json.dumps({'app':'Reddit','query':'IELTS'})
 asyncio.run(HybridAgent._do_impl(a,action));args=a.phone.tap.await_args
 assert args.args==(.25,.05) and args.kwargs['aim'] is True and args.kwargs['pre_click']()
 changed=im.copy();ImageDraw.Draw(changed).rectangle(data['patches']['blank_close']['box'],fill='black');a.screen.frame=encoded(changed)
 assert not args.kwargs['pre_click']()


def test_app_store_search_requires_catalog_navigation_not_get_or_account():
 path=app_controls.ROOT/'app_store/search.json';data=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'white')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,spec['box'][:2])
 target={'device':'ipad','orientation':'portrait'}
 def match(frame):return app_controls.public_search_control(encoded(frame),'App Store',target)
 hit=match(im);assert hit and hit['x']==659 and hit['y']==48
 for n in data['anchors']+[data['target']]:
  bad=im.copy();ImageDraw.Draw(bad).rectangle(data['patches'][n]['box'],fill='white');assert not match(bad)
 assert not app_controls.public_search_control(encoded(im),'App Store',{'device':'ipad','orientation':'landscape'})


def test_app_store_populated_editor_requires_all_fixed_controls():
 path=app_controls.ROOT/'app_store/field.json';data=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'white')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,spec['box'][:2])
 target={'device':'ipad','orientation':'portrait'}
 def match(frame):return app_controls.public_search_control(encoded(frame),'App Store',target,control='public_search_field')
 hit=match(im);assert hit and (hit['x'],hit['y'])==(500,52)
 for n in data['anchors']+[data['target']]:
  bad=im.copy();ImageDraw.Draw(bad).rectangle(data['patches'][n]['box'],fill='white');assert not match(bad)
 assert not app_controls.public_search_control(encoded(im),'App Store',{'device':'iphone','orientation':'portrait'},control='public_search_field')


def test_douyin_query_only_editor_is_not_video_or_result_toolbar():
 path=app_controls.ROOT/'douyin/field.json';data=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'white')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,spec['box'][:2])
 target={'device':'ipad','orientation':'portrait'}
 def match(frame):return app_controls.public_search_control(encoded(frame),'Douyin',target,control='public_search_field')
 hit=match(im);assert hit and (hit['x'],hit['y'])==(450,40)
 for name in data['anchors']+[data['target']]:
  bad=im.copy();ImageDraw.Draw(bad).rectangle(data['patches'][name]['box'],fill='white');assert not match(bad)
 assert not app_controls.public_search_control(encoded(im),'YouTube',target,control='public_search_field')


def test_bilibili_fullheight_variant_requires_its_actual_footer():
 path=app_controls.ROOT/'bilibili/search-fullheight.json';data=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'white')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,spec['box'][:2])
 target={'device':'ipad','orientation':'portrait'}
 def match(frame):return app_controls.public_search_control(encoded(frame),'Bilibili',target)
 hit=match(im);assert hit and hit['template']=='search-fullheight.json'
 for name in data['anchors']+[data['target']]:
  bad=im.copy();ImageDraw.Draw(bad).rectangle(data['patches'][name]['box'],fill='white');assert not match(bad)


def test_observed_rednote_paste_sheet_uses_one_escape_not_clipboard_grant():
 from types import SimpleNamespace
 from unittest.mock import Mock
 from hidagent.shortcut_workflow import next_shortcut_actions
 path=app_controls.ROOT/'rednote/paste-denial.json';data=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'white')
 for spec in data['patches'].values():
  with Image.open(path.parent/spec['file']) as patch:im.paste(patch,spec['box'][:2])
 target={'device':'ipad','orientation':'portrait'}
 def match(frame):return app_controls.public_search_control(encoded(frame),'rednote',target,control='native_paste_denial_notice')
 assert match(im)
 for name in data['anchors']+[data['target']]:
  bad=im.copy();ImageDraw.Draw(bad).rectangle(data['patches'][name]['box'],fill='white');assert not match(bad)
 a=SimpleNamespace(screen=SimpleNamespace(frame=encoded(im),live=True),phone=SimpleNamespace(target_info=lambda:target),steps=[{'workflow':'keyboard','phase':'icon_launch','act_s':.1}],trace=Mock(),client=SimpleNamespace(evaluate=AsyncMock()))
 out=asyncio.run(next_shortcut_actions(a,{'app':'rednote','text':None}))[0]
 assert out['action']=='key' and out['combo']=='esc' and out['phase']=='icon_cancel_overlay'
 a.client.evaluate.assert_not_awaited()
 a.steps.append(dict(out,act_s=.1))
 a.client.evaluate.return_value={'answers':{'ready':{'noul':0},'blocked':{'noul':0},'loading':{'noul':1}},'seconds':0}
 again=asyncio.run(next_shortcut_actions(a,{'app':'rednote','text':None}))[0]
 assert again['action']=='wait' and again['phase']=='icon_app_ready'


def test_tiktok_editor_locator_requires_all_actual_editor_cues():
 p=app_controls.ROOT/'tiktok/field.json';d=json.loads(p.read_text());im=Image.new('RGB',(710,1080),'white')
 for spec in d['patches'].values():
  with Image.open(p.parent/spec['file']) as patch:im.paste(patch,spec['box'][:2])
 target={'device':'ipad','orientation':'portrait'}
 assert app_controls.public_search_control(encoded(im),'TikTok',target,control='public_search_field')
 for n in d['anchors']:
  changed=im.copy();ImageDraw.Draw(changed).rectangle(d['patches'][n]['box'],fill='red')
  assert not app_controls.public_search_control(encoded(changed),'TikTok',target,control='public_search_field')
 assert not app_controls.public_search_control(encoded(im),'TikTok',{'device':'iphone','orientation':'portrait'},control='public_search_field')


def test_generic_post_detail_route_needs_actual_title_back_and_native_footer():
 import json
 from PIL import ImageDraw
 path=app_controls.ROOT/'x/post-profile.json';data=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'black')
 for patch in data['patches'].values():im.paste(Image.open(path.parent/patch['file']).convert('RGB'),patch['box'][:2])
 target={'device':'ipad','orientation':'portrait'}
 assert app_controls.public_search_control(encoded(im),'X',target,control='preview_post_profile_tab')
 changed=im.copy();ImageDraw.Draw(changed).rectangle(data['patches']['post_title']['box'],fill='black')
 assert not app_controls.public_search_control(encoded(changed),'X',target,control='preview_post_profile_tab')
 assert not app_controls.public_search_control(encoded(im),'X',{'device':'iphone','orientation':'portrait'},control='preview_post_profile_tab')

def test_registered_profile_home_return_uses_existing_ui_footer_not_post_body():
 import json
 path=app_controls.ROOT/'x/profile-return-home.json';data=json.loads(path.read_text());im=Image.new('RGB',(710,1080),'black')
 for patch in data['patches'].values():im.paste(Image.open(path.parent/patch['file']).convert('RGB'),patch['box'][:2])
 result=app_controls.public_search_control(encoded(im),'X',{'device':'ipad','orientation':'portrait'},control='preview_return_home_tab')
 assert result and result['x']==63 and result['y']==956
