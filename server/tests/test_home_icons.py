from synthetic_assets import ASSETS
import io,json
import pytest
from pathlib import Path
from PIL import Image
from hidagent.home_icons import locate_home_icon

def make(tmp_path):
 p=Image.new('RGB',(710,1080),'white');icons={}
 for i in range(10):
  x,y=80+(i%4)*150,250+(i//4)*170
  color=((i*23)%255,(i*47)%255,(i*71)%255);box=(x-20,y-20,x+20,y+20)
  p.paste(color,box);name='YouTube' if i==0 else str(i);f=str(i)+'.png';p.crop(box).save(tmp_path/f)
  icons[name]={'center':[x/710,y/1080],'crop':[box[0]/710,box[1]/1080,box[2]/710,box[3]/1080],'file':f}
 path=tmp_path/'layout.json';path.write_text(json.dumps({'reference_size':[710,1080],'icons':icons}))
 return p,path

def encode(p):
 b=io.BytesIO();p.save(b,format='JPEG');return b.getvalue()

def test_only_current_matched_icon_grid_is_clickable(tmp_path):
 p,path=make(tmp_path);t={'device':'ipad','orientation':'portrait'}
 assert locate_home_icon(encode(p),'YouTube',t,path)['matched_icons']==10
 assert locate_home_icon(encode(p),'Threads',t,path) is None
 assert locate_home_icon(encode(p),'YouTube',{'device':'iphone','orientation':'portrait'},path) is None
 assert locate_home_icon(encode(p.transpose(Image.Transpose.ROTATE_90)),'YouTube',t,path) is None
 assert locate_home_icon(encode(Image.new('RGB',p.size,'black')),'YouTube',t,path) is None
 # Most icons unchanged is insufficient when the actual target was moved/replaced.
 p.paste('white',(60,230,100,270))
 assert locate_home_icon(encode(p),'YouTube',t,path) is None


def test_top_search_overlay_rejects_unchanged_icons(tmp_path):
 p,path=make(tmp_path);data=json.loads(path.read_text())
 guard=(200,100,510,160);p.paste('gray',guard)
 p.crop(guard).save(tmp_path/'guard.png')
 data['home_guard']={'file':'guard.png','crop':[200/710,100/1080,510/710,160/1080]}
 path.write_text(json.dumps(data));target={'device':'ipad','orientation':'portrait'}
 assert locate_home_icon(encode(p),'YouTube',target,path)
 # Spotlight can leave all lower icons perfectly intact.
 p.paste('black',guard)
 assert locate_home_icon(encode(p),'YouTube',target,path) is None


def test_only_explicit_observed_icon_variant_is_accepted(tmp_path):
 p,path=make(tmp_path);data=json.loads(path.read_text());target={'device':'ipad','orientation':'portrait'}
 variant=Image.new('RGB',(40,40),'blue');variant.paste('yellow',(0,0,20,40));variant.save(tmp_path/'alternate.png')
 data['icons']['YouTube']['variants']=['alternate.png'];path.write_text(json.dumps(data))
 p.paste(variant,(60,230));assert locate_home_icon(encode(p),'YouTube',target,path)
 p.paste('white',(60,230,100,270));assert locate_home_icon(encode(p),'YouTube',target,path) is None


def test_observed_guard_variant_keeps_overlay_and_target_rejection(tmp_path):
 p,path=make(tmp_path);data=json.loads(path.read_text());target={'device':'ipad','orientation':'portrait'}
 box=(200,100,510,160);p.paste('gray',box);p.crop(box).save(tmp_path/'guard.png')
 p.paste('blue',box);p.crop(box).save(tmp_path/'guard-blue.png')
 data['home_guard']={'file':'guard.png','variants':['guard-blue.png'],'crop':[200/710,100/1080,510/710,160/1080]}
 path.write_text(json.dumps(data))
 assert locate_home_icon(encode(p),'YouTube',target,path)
 p.paste('gray',box);assert locate_home_icon(encode(p),'YouTube',target,path)
 p.paste('black',box);assert locate_home_icon(encode(p),'YouTube',target,path) is None
 p.paste('blue',box);p.paste('white',(60,230,100,270))
 assert locate_home_icon(encode(p),'YouTube',target,path) is None


def test_home_icon_move_must_not_click_an_overlay_that_arrives_during_aim(tmp_path,monkeypatch):
 import asyncio
 from types import SimpleNamespace
 from unittest.mock import AsyncMock
 from hidagent.hybrid_agent import HybridAgent
 from hidagent.phone import PhoneUnavailable
 picture,path=make(tmp_path);target={'device':'ipad','orientation':'portrait'};monkeypatch.setenv('APP_ICON_LAYOUT',str(path))
 frame=encode(picture);icon=locate_home_icon(frame,'YouTube',target)
 a=SimpleNamespace(screen=SimpleNamespace(frame=frame,live=True))
 clicked=[]
 async def tap(*args,**kwargs):
  a.screen.frame=encode(Image.new('RGB',picture.size,'black'))
  if not kwargs.get('pre_click',lambda:True)():raise PhoneUnavailable('Home changed; no click')
  clicked.append(True)
 a.phone=SimpleNamespace(target_info=lambda:target,tap=AsyncMock(side_effect=tap))
 try:asyncio.run(HybridAgent._do_impl(a,{'action':'tap','workflow':'keyboard','phase':'icon_launch','_home_icon_app':'YouTube','_icon_hitbox':icon['hitbox'],'x':icon['x'],'y':icon['y'],'_shot':frame}))
 except PhoneUnavailable:pass
 assert not clicked


def test_home_icon_unchanged_target_keeps_precise_click_and_rejects_wrong_binding(tmp_path,monkeypatch):
 import asyncio
 from types import SimpleNamespace
 from unittest.mock import AsyncMock
 from hidagent.hybrid_agent import HybridAgent
 from hidagent.decision import DecisionUnavailable
 picture,path=make(tmp_path);target={'device':'ipad','orientation':'portrait'};monkeypatch.setenv('APP_ICON_LAYOUT',str(path))
 frame=encode(picture);icon=locate_home_icon(frame,'YouTube',target);a=SimpleNamespace(screen=SimpleNamespace(frame=frame,live=True))
 async def tap(*args,**kwargs):assert kwargs['aim'] and kwargs['pre_click']()
 a.phone=SimpleNamespace(target_info=lambda:target,tap=AsyncMock(side_effect=tap))
 action={'action':'tap','workflow':'keyboard','phase':'icon_launch','_home_icon_app':'YouTube','_icon_hitbox':icon['hitbox'],'x':icon['x'],'y':icon['y'],'_shot':frame}
 asyncio.run(HybridAgent._do_impl(a,action));assert a.phone.tap.await_count==1
 for updates in ({'_home_icon_app':'Threads'},{'_home_icon_app':None},{'x':action['x']+1},{'workflow':'unbound'}):
  try:asyncio.run(HybridAgent._do_impl(a,{**action,**updates}))
  except DecisionUnavailable:pass
  else:raise AssertionError('Unbound Home target moved')
 assert a.phone.tap.await_count==1


def test_pointer_occlusion_is_bounded_and_does_not_hide_a_different_target():
 from PIL import ImageDraw
 from hidagent.home_icons import locate_home_icon
 import json,io
 from pathlib import Path
 p=ASSETS / 'home/layout.json';data=json.loads(p.read_text())
 im=Image.new('RGB',data['reference_size'],'black');w,h=im.size
 for item in data['icons'].values():
  box=tuple(round(c*(w if i%2==0 else h)) for i,c in enumerate(item['crop']))
  with Image.open(p.parent/item['file']) as v:im.paste(v.convert('RGB').resize((box[2]-box[0],box[3]-box[1])),box[:2])
 guard=data['home_guard'];box=tuple(round(c*(w if i%2==0 else h)) for i,c in enumerate(guard['crop']))
 with Image.open(p.parent/guard['file']) as v:im.paste(v.convert('RGB').resize((box[2]-box[0],box[3]-box[1])),box[:2])
 item=data['icons']['LinkedIn'];x,y=item['center'];proof={'observed':[x,y],'shift':.0124};target={'device':'ipad','orientation':'portrait'}
 def encode(pic):
  b=io.BytesIO();pic.save(b,format='PNG');return b.getvalue()
 assert locate_home_icon(encode(im),'LinkedIn',target,p)
 covered=im.copy();cx,cy=(x+.0124)*w,y*h;ImageDraw.Draw(covered).ellipse((cx-9,cy-9,cx+9,cy+9),fill=(120,120,120))
 assert locate_home_icon(encode(covered),'LinkedIn',target,p,pointer_occlusion=proof)
 wrong=covered.copy();box=tuple(round(c*(w if i%2==0 else h)) for i,c in enumerate(item['crop']));ImageDraw.Draw(wrong).rectangle(box,fill='red')
 assert not locate_home_icon(encode(wrong),'LinkedIn',target,p,pointer_occlusion=proof)
 assert not locate_home_icon(encode(covered),'LinkedIn',target,p,pointer_occlusion={'observed':[x,y],'shift':.2})
 assert not locate_home_icon(encode(covered),'LinkedIn',target,p,pointer_occlusion={'observed':[.8,.8],'shift':.0124})


def test_pointer_sweep_target_must_equal_bound_icon_center(tmp_path):
 p,path=make(tmp_path);t={'device':'ipad','orientation':'portrait'}
 center=[80/710,250/1080]
 good={'observed':center,'shift':.004,'target':center}
 assert locate_home_icon(encode(p),'YouTube',t,path,pointer_occlusion=good)
 for target in ([.5,.5],[True,center[1]],[center[0]+.02,center[1]]):
  assert locate_home_icon(encode(p),'YouTube',t,path,pointer_occlusion={**good,'target':target}) is None


def test_pointer_sweep_does_not_hide_replaced_icon_or_bypass_grid(tmp_path):
 p,path=make(tmp_path);t={'device':'ipad','orientation':'portrait'};center=[80/710,250/1080]
 proof={'observed':center,'shift':.004,'target':center}
 p.paste('yellow',(60,230,100,270))
 assert locate_home_icon(encode(p),'YouTube',t,path,pointer_occlusion=proof) is None
 p,path=make(tmp_path);p.paste('black',(200,200,710,1080))
 assert locate_home_icon(encode(p),'YouTube',t,path,pointer_occlusion=proof) is None


def test_pointer_sweep_still_rejects_excessive_occlusion(tmp_path):
 p,path=make(tmp_path);center=[80/710,250/1080]
 assert locate_home_icon(encode(p),'YouTube',{'device':'ipad','orientation':'portrait'},path,pointer_occlusion={'observed':center,'target':center,'shift':.03}) is None


def test_restored_frame_never_exempts_target_or_grid_checks(tmp_path):
 p,path=make(tmp_path);t={'device':'ipad','orientation':'portrait'};center=[80/710,250/1080]
 proof={'observed':center,'target':center,'shift':.0124,'probe_restored':True}
 assert locate_home_icon(encode(p),'YouTube',t,path,pointer_occlusion=proof)
 assert not locate_home_icon(encode(p),'YouTube',t,path,pointer_occlusion={**proof,'target':[.5,.5]})
 p.paste('yellow',(60,230,100,270))
 assert not locate_home_icon(encode(p),'YouTube',t,path,pointer_occlusion=proof)


@pytest.mark.parametrize('updated,stamp,enabled,expected',[(10,9,'1',True),(9,9,'1',False),(8,9,'1',False),(10,None,'1',False),(10,9,'0',False)])
def test_only_fresh_post_restore_frame_can_omit_probe_sweep(monkeypatch,updated,stamp,enabled,expected):
 import asyncio
 from types import SimpleNamespace
 from unittest.mock import AsyncMock
 from hidagent.hybrid_agent import HybridAgent
 import hidagent.home_icons as icons
 target={'device':'ipad','orientation':'portrait'};shot=encode(Image.new('RGB',(710,1080),'black'))
 icon={'x':500,'y':500,'hitbox':[.46,.46,.54,.54]};proofs=[]
 def locate(*args,**kwargs):
  if kwargs.get('pointer_occlusion'):proofs.append(kwargs['pointer_occlusion'])
  return icon
 monkeypatch.setattr(icons,'locate_home_icon',locate);monkeypatch.setenv('HID_POINT_SWEEP_GUARD','1');monkeypatch.setenv('HID_POINT_RESTORED_FRAME',enabled)
 a=SimpleNamespace(screen=SimpleNamespace(frame=shot,live=True,updated=updated))
 async def tap(*args,**kwargs):
  a.phone.last_pointer_check={'target':[.5,.5],'observed':[.5,.5],'error':0,'probe_restored_at':stamp}
  assert kwargs['pre_click']()
 a.phone=SimpleNamespace(target_info=lambda:target,last_pointer_check=None,last_pointer_probe={'outcome':'found','shift':.0124},tap=AsyncMock(side_effect=tap))
 asyncio.run(HybridAgent._do_impl(a,{'action':'tap','workflow':'keyboard','phase':'icon_launch','_home_icon_app':'YouTube','_icon_hitbox':icon['hitbox'],'x':500,'y':500,'_shot':shot}))
 assert bool(proofs[-1].get('probe_restored')) is expected
