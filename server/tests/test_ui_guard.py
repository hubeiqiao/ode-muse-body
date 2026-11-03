import io
from PIL import Image,ImageDraw
from hidagent.ui_guard import control_region_matches

def encode(p):
 b=io.BytesIO();p.save(b,format='PNG');return b.getvalue()

def test_local_grounded_control_ignores_video_outside_its_box():
 a=Image.new('RGB',(400,600),'black');d=ImageDraw.Draw(a)
 d.rectangle((100,400,200,480),fill='white');d.text((110,430),"Don't Allow",fill='black')
 b=a.copy();ImageDraw.Draw(b).rectangle((0,0,399,300),fill='red')
 box={'left':.25,'top':400/600,'right':.5,'bottom':.8}
 assert control_region_matches(encode(a),encode(b),box)
 ImageDraw.Draw(b).rectangle((100,400,200,480),fill='blue')
 assert not control_region_matches(encode(a),encode(b),box)
 assert not control_region_matches(encode(a),encode(a.resize((200,300))),box)
 assert not control_region_matches(encode(a),encode(a),{'left':0,'top':0,'right':.1,'bottom':.1})


def test_native_denial_hover_keeps_label_identity_without_global_tolerance():
 from PIL import ImageFont
 box={'left':0,'right':1,'top':0,'bottom':1}
 a=Image.new('RGB',(280,50),(90,90,90));b=Image.new('RGB',a.size,(50,50,50))
 font=ImageFont.load_default(size=18)
 for p in (a,b):ImageDraw.Draw(p).text((20,15),'Ask App Not to Track',fill=(240,240,240),font=font)
 assert not control_region_matches(encode(a),encode(b),box)
 assert control_region_matches(encode(a),encode(b),box,allow_text_hover=True)
 b=Image.new('RGB',a.size,(50,50,50));ImageDraw.Draw(b).text((20,15),'Allow',fill=(240,240,240),font=font)
 assert not control_region_matches(encode(a),encode(b),box,allow_text_hover=True)
