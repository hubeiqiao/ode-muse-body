import io
from PIL import Image,ImageDraw
from hidagent import vision
from hidagent.phone import pointer_observation_roi

def frame(x,y):
 im=Image.new('RGB',(710,1080),'black');ImageDraw.Draw(im).ellipse((x-9,y-9,x+9,y+9),fill='white');b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()

def test_full_pointer_outside_control_roi_can_be_observed_without_relaxing_shape():
 a,b=frame(250,22),frame(259,22);bounds=(0,.024,1,.095);target={'device':'ipad','width':710,'height':1080}
 assert vision.pointer(a,b,(.35,.039),9/710,bounds=bounds,top_margin=.025) is None
 roi,margin=pointer_observation_roi('near',bounds,.025,target,True)
 found=vision.pointer(a,b,(.35,.039),9/710,bounds=roi,top_margin=margin)
 assert found and abs(found[0]-250/710)<.003 and abs(found[1]-22/1080)<.003
 # Observation outside the click target is NOT acceptance of its landing.
 assert abs(found[1]-.039)>.008

def test_global_and_iphone_and_disabled_observations_keep_original_bounds():
 b=(0,.024,1,.095)
 for label,t,enabled in [('global',{'device':'ipad','width':710,'height':1080},True),('near',{'device':'iphone','width':710,'height':1080},True),('near',{'device':'ipad','width':710,'height':1080},False)]:
  assert pointer_observation_roi(label,b,.025,t,enabled)==(b,.025)

def test_unbounded_or_invalid_geometry_is_not_expanded():
 assert pointer_observation_roi('near',None,.025,{'device':'ipad','width':710,'height':1080},True)==(None,.025)
 assert pointer_observation_roi('near',(0,.02,1,.1),.025,{'device':'ipad'},True)==((0,.02,1,.1),.025)

def test_expanded_roi_does_not_accept_caret_or_widespread_changes():
 target={'device':'ipad','width':710,'height':1080};roi,margin=pointer_observation_roi('near',(0,.024,1,.095),.025,target,True)
 a=Image.new('RGB',(710,1080),'black');b=a.copy();ImageDraw.Draw(b).rectangle((250,5,252,38),fill='white')
 assert vision.pointer(a,b,(.35,.039),9/710,bounds=roi,top_margin=margin) is None
 ImageDraw.Draw(b).rectangle((150,5,340,40),fill='white')
 assert vision.pointer(a,b,(.35,.039),9/710,bounds=roi,top_margin=margin) is None
