import asyncio,io,json
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from PIL import Image,ImageDraw
from hidagent.navigation_observation import header_navigation_proposal,navigation_binding,observation_bound
from hidagent.hid_lease import leased_input


def picture(change=False):
 im=Image.new('RGB',(710,1080),'white');d=ImageDraw.Draw(im);d.rectangle((55,40,230,58),fill='gray')
 if change:d.rectangle((300,60,350,75),fill='red')
 b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()


def fixture():
 frame=picture();target={'device':'ipad','orientation':'portrait','calibration_key':'ipad-portrait-66','width':710,'height':1080}
 phone=SimpleNamespace(calibrated=True,device=SimpleNamespace(links=1,connected=True),target_info=lambda:target)
 reader=SimpleNamespace(read=AsyncMock(return_value={'rows':[{'text':'IELTS speaking fluérKy','confidence':.5,'box':{'left':.08,'right':.3,'top':.04,'bottom':.06}}]}))
 return SimpleNamespace(phone=phone,screen=SimpleNamespace(live=True,frame=frame),navigation_text_reader=reader),target


def test_imperfect_ocr_only_proposes_position_without_text_permission():
 a,_=fixture();r=asyncio.run(header_navigation_proposal(a,a.screen.frame))
 assert r['x']==190 and r['y']==50
 assert set(r)=={'x','y','box','binding','seconds'}
 assert 'text' not in json.dumps(r) and observation_bound(a.phone,a.screen,r,a.screen.frame)


@pytest.mark.parametrize('bad',['ambiguous','feed','status','tiny','bool-coordinate','iphone','landscape','no-calibration','offline','scene-changed','link-changed'])
def test_unbound_or_ambiguous_observation_cannot_be_navigation_proof(bad):
 a,t=fixture();frame=a.screen.frame;rows=a.navigation_text_reader.read.return_value['rows']
 if bad=='ambiguous':rows.append(dict(rows[0]))
 if bad=='feed':rows[0]['box'].update(top=.3,bottom=.32)
 if bad=='status':rows[0]['box'].update(top=.005,bottom=.02)
 if bad=='tiny':rows[0]['box'].update(right=.09)
 if bad=='bool-coordinate':rows[0]['box']['left']=False
 if bad=='iphone':t['device']='iphone'
 if bad=='landscape':t['orientation']='landscape'
 if bad=='no-calibration':a.phone.calibrated=False
 if bad=='offline':a.screen.live=False
 async def read(*args,**kwargs):
  if bad=='scene-changed':a.screen.frame=picture(True)
  if bad=='link-changed':a.phone.device.links=2
  return {'rows':rows}
 a.navigation_text_reader.read=AsyncMock(side_effect=read)
 assert asyncio.run(header_navigation_proposal(a,frame)) is None


def test_swallowed_cancellation_during_ocr_cannot_issue_a_proposal():
 a,_=fixture();active=True
 def guard():
  if not active:raise asyncio.CancelledError()
 async def read(*args,**kwargs):
  nonlocal active
  active=False;return {'rows':[{'text':'IELTS speaking','box':{'left':.08,'right':.3,'top':.04,'bottom':.06}}]}
 a.navigation_text_reader.read=read
 async def run():
  with leased_input(guard):return await header_navigation_proposal(a,a.screen.frame)
 with pytest.raises(asyncio.CancelledError):asyncio.run(run())


@pytest.mark.parametrize('bad',[None,'scene-after-move','link-after-move','stale-pointer','changed-coordinate','wrong-goal'])
def test_actual_executor_rechecks_source_binding_and_fresh_pointer_before_click(bad):
 from hidagent.hybrid_agent import HybridAgent
 from hidagent.app_search import PREFIX
 from hidagent.phone import PhoneUnavailable
 from hidagent.decision import DecisionUnavailable
 a,_=fixture();source=a.screen.frame;proof={'box':{'left':.08,'right':.3,'top':.04,'bottom':.06},'binding':navigation_binding(a.phone)}
 clicks=[];old={'target':[.19,.05],'observed':[.19,.05]};a.phone.last_pointer_check=old
 async def tap(x,y,aim=False,pre_click=None,aim_region=None):
  assert aim_region is not None and aim_region[3]<.15
  assert aim is True
  if bad!='stale-pointer':a.phone.last_pointer_check={'target':[x,y],'observed':[x,y]}
  if bad=='scene-after-move':a.screen.frame=picture(True)
  if bad=='link-after-move':a.phone.device.links=2
  if not pre_click():raise PhoneUnavailable('proof changed')
  clicks.append((x,y))
 a.phone.tap=AsyncMock(side_effect=tap)
 actor=HybridAgent(a.phone,a.screen,SimpleNamespace(model='jev'),SimpleNamespace(model='haiku'),Mock())
 actor.task=PREFIX+json.dumps({'app':'rednote','query':'IELTS practice'}) if bad!='wrong-goal' else 'Open Notes'
 action={'action':'tap','x':190 if bad!='changed-coordinate' else 300,'y':50,'workflow':'app_search','phase':'search_control','_observed_public_navigation':proof,'_shot':source}
 if bad:
  with pytest.raises((PhoneUnavailable,DecisionUnavailable)):asyncio.run(actor._do(action))
  assert not clicks
 else:
  asyncio.run(actor._do(action));assert clicks==[(.19,.05)]


def test_generic_control_boundary_expands_text_to_actual_container_center():
 from hidagent.navigation_observation import enclosing_flat_control,observation_pointer_region
 im=Image.new('RGB',(710,1080),(15,15,15));d=ImageDraw.Draw(im)
 d.rounded_rectangle((42,34,694,80),radius=18,fill=(50,50,50));d.rectangle((58,50,205,62),fill='white')
 b=io.BytesIO();im.save(b,format='PNG')
 box={'left':58/710,'right':205/710,'top':50/1080,'bottom':62/1080}
 control=enclosing_flat_control(b.getvalue(),box)
 assert control and .50<(control['left']+control['right'])/2<.54
 region=observation_pointer_region({'box':control})
 assert region[1]>=.025 and region[3]<.15
 # A flat full-screen background or short text stroke has no enclosing control.
 assert enclosing_flat_control(picture(),box) is None


def test_only_public_navigation_chrome_can_be_reused_over_dynamic_results():
 from hidagent.navigation_observation import navigation_chrome_matches
 a,_=fixture();before=a.screen.frame
 im=Image.open(io.BytesIO(before));ImageDraw.Draw(im).rectangle((50,250,650,900),fill='red')
 b=io.BytesIO();im.save(b,format='PNG')
 assert navigation_chrome_matches(before,b.getvalue())
 assert not navigation_chrome_matches(before,picture(True))
 im=Image.open(io.BytesIO(before)).point(lambda v:int(v*.6));b=io.BytesIO();im.save(b,format='PNG')
 assert not navigation_chrome_matches(before,b.getvalue())
