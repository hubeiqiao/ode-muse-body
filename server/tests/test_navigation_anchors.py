import io,json
from PIL import Image,ImageDraw,ImageEnhance
import pytest
from hidagent.app_controls import public_search_control

def fixture(tmp_path,monkeypatch,control='public_search_entry'):
 from hidagent import app_controls as c
 im=Image.new('RGB',(160,80),'black');patches={}
 for i,name in enumerate(('a','b','c','target')):
  box=(i*40,5,i*40+30,35);p=Image.new('RGB',(30,30),(50,50,50));d=ImageDraw.Draw(p)
  d.rectangle((5,5,10,24),fill='white');d.rectangle((5,19,24,24),fill='white');p.save(tmp_path/(name+'.png'));im.paste(p,box[:2]);patches[name]={'box':box,'file':name+'.png'}
 data={'app':'Test','control':control,'reference_size':[160,80],'center':[.84,.25],'anchors':['a','b','c'],'target':'target','patches':patches}
 path=tmp_path/'ui.json';path.write_text(json.dumps(data));monkeypatch.setitem(c.FILES,('Test',control),path);monkeypatch.setenv('APP_SEARCH_NAV_ANCHORS','1')
 return im,path,data

def encode(im):
 b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()
def changed_background(im,index=0):
 p=im.crop((index*40,5,index*40+30,35));px=p.load()
 for y in range(30):
  for x in range(30):
   if px[x,y]==(50,50,50):px[x,y]=(100,100,100)
 im.paste(p,(index*40,5));return im

def locate(im,control='public_search_entry',**kw):return public_search_control(encode(im),'Test',{'device':'ipad','orientation':'portrait'},control=control,**kw)

def test_one_background_change_retains_glyph_and_two_exact_anchors(tmp_path,monkeypatch):
 im,p,d=fixture(tmp_path,monkeypatch);changed_background(im)
 assert locate(im)
 monkeypatch.setenv('APP_SEARCH_NAV_ANCHORS','0');assert locate(im) is None

def test_two_changed_anchors_cannot_fall_back(tmp_path,monkeypatch):
 im,p,d=fixture(tmp_path,monkeypatch);changed_background(im);changed_background(im,1);assert locate(im) is None

@pytest.mark.parametrize('kind',['target','wrong_shape','dimming','unknown_matcher'])
def test_changed_target_shape_dimming_or_unknown_rule_rejects(tmp_path,monkeypatch,kind):
 im,p,d=fixture(tmp_path,monkeypatch)
 if kind=='target':im.paste('red',(120,5,150,35))
 elif kind=='wrong_shape':im.paste('white',(0,5,30,35))
 elif kind=='dimming':
  changed_background(im);im=ImageEnhance.Brightness(im).enhance(.94)
 else:
  changed_background(im);d['patches']['a']['matching']='unknown';p.write_text(json.dumps(d))
 assert locate(im) is None

def test_private_exclusion_and_input_field_do_not_get_navigation_fallback(tmp_path,monkeypatch):
 for control in ('public_search_field','excluded_notification_overlay'):
  im,p,d=fixture(tmp_path,monkeypatch,control);changed_background(im);assert locate(im,control) is None

def test_known_blocker_still_rejects_before_navigation_match(tmp_path,monkeypatch):
 im,p,d=fixture(tmp_path,monkeypatch);d['blockers']=[['b','c']];p.write_text(json.dumps(d));changed_background(im);assert locate(im) is None

def test_only_two_anchors_cannot_use_fallback(tmp_path,monkeypatch):
 im,p,d=fixture(tmp_path,monkeypatch);d['anchors']=['a','b'];p.write_text(json.dumps(d));changed_background(im);assert locate(im) is None
