"""Client HTTP seams with memory-only I/O; no phone/model calls."""
import json
import httpx
import pytest
from pydantic import ValidationError
from hidagent.client import IPhoneClient
from hidagent.control_step import ControlStep,run_step

def request(**kw):
 return {'request_id':'qa_step_0001','expected_device':'ipad','calibration_key':'ipad-test','action':'key','arguments':{'text':'cmd+v'},'observe_seconds':0.,**kw}
def state(**kw):
 return {'device':True,'screen':True,'calibrated':True,'controller':{'stable':True,'link_generation':7},'agent':{'running':False},'control_activity':{},'exploration':{'busy':False},'target':{'device':'ipad','orientation':'portrait','width':710,'height':1080,'calibration_key':'ipad-test'},**kw}
def fixture(monkeypatch,*,mutation_status=200,changed=False,private=False,pre=None,frame_status=200):
 seen=[];states=0
 monkeypatch.setattr('hidagent.frame_privacy.known_excluded_overlay',lambda f:'known_private' if private else None)
 def handler(r):
  nonlocal states
  seen.append((r.method,r.url.path))
  if r.url.path=='/api/status':
   states+=1;d=pre or state()
   if changed and states>1:d=state(controller={'stable':True,'link_generation':8})
   return httpx.Response(200,json=d)
  if r.url.path=='/api/mission/status':return httpx.Response(200,json={'running':False})
  if r.url.path=='/api/frame.jpg':return httpx.Response(frame_status,content=b'mock-frame')
  if r.url.path=='/api/device-health':return httpx.Response(200,json={'last_tap_timing':{'target':[.5,.4],'point_s':2}})
  return httpx.Response(mutation_status,json={'ok':True} if mutation_status==200 else {'detail':'refused'})
 return IPhoneClient('http://memory-only',transport=httpx.MockTransport(handler)),seen

def test_pair_and_repeat_never_dispatches_again(tmp_path,monkeypatch):
 c,seen=fixture(monkeypatch);receipt=tmp_path/'r.json';shot=tmp_path/'s.jpg'
 with c:
  result=run_step(c,request(),receipt,shot);first=list(seen);again=run_step(c,request(),receipt,shot)
 assert seen==first and [x for x in seen if x[0]=='POST']==[('POST','/api/key')]
 assert shot.read_bytes()==b'mock-frame' and result['status']=='executed_unverified'
 assert not result['task_success_verified'] and again['historical'] and not again['automatic_replay']
 assert 'cmd+v' not in receipt.read_text()

@pytest.mark.parametrize('options,expected',[({'mutation_status':409},'delivery_unverified'),({'mutation_status':502},'delivery_unverified'),({'frame_status':503},'executed_unverified')])
def test_failures_retain_attempt_no_resend(tmp_path,monkeypatch,options,expected):
 c,seen=fixture(monkeypatch,**options)
 with c:
  result=run_step(c,request(),tmp_path/'r.json',tmp_path/'s.jpg');again=run_step(c,request(),tmp_path/'r.json',tmp_path/'s.jpg')
 assert result['status']==expected and again['historical'] and result['action_attempted']
 assert len([x for x in seen if x[0]=='POST'])==1

@pytest.mark.parametrize('pre',[state(target={'device':'iphone','calibration_key':'ipad-test'}),state(control_activity={'voice_sessions':1}),state(controller={'stable':True}),state(exploration={'busy':True})])
def test_binding_ownership_before_post(tmp_path,monkeypatch,pre):
 c,seen=fixture(monkeypatch,pre=pre)
 with c:r=run_step(c,request(),tmp_path/'r.json',tmp_path/'s.jpg')
 assert r['status']=='preflight_failed' and not r['action_attempted'] and not any(m=='POST' for m,_ in seen)

@pytest.mark.parametrize('options,observation',[({'changed':True},'unavailable_or_binding_changed_or_busy'),({'private':True},'withheld_known_private_overlay')])
def test_link_change_or_private_frame_not_exported(tmp_path,monkeypatch,options,observation):
 c,seen=fixture(monkeypatch,**options)
 with c:r=run_step(c,request(),tmp_path/'r.json',tmp_path/'s.jpg')
 assert r['observation']==observation and not (tmp_path/'s.jpg').exists() and 'screenshot_path' not in r
 if options.get('changed'):assert ('GET','/api/frame.jpg') not in seen

def test_conflicting_request_or_partial_reservation_refuses(tmp_path,monkeypatch):
 c,seen=fixture(monkeypatch)
 with c:
  run_step(c,request(),tmp_path/'r.json',tmp_path/'s.jpg');before=list(seen)
  with pytest.raises(ValueError):run_step(c,request(arguments={'text':'enter'}),tmp_path/'r.json',tmp_path/'new.jpg')
  (tmp_path/'partial.json').write_text('{')
  with pytest.raises(ValueError):run_step(c,request(),tmp_path/'partial.json',tmp_path/'other.jpg')
 assert seen==before

def test_existing_image_refused_before_http(tmp_path,monkeypatch):
 c,seen=fixture(monkeypatch);shot=tmp_path/'s.jpg';shot.write_bytes(b'old')
 with c:r=run_step(c,request(),tmp_path/'r.json',shot)
 assert r['status']=='preflight_failed' and not seen and shot.read_bytes()==b'old'

@pytest.mark.parametrize('kw',[{'action':'raw'},{'arguments':{'text':'hello\nenter'}},{'arguments':{'text':'v','extra':1}},{'action':'tap','arguments':{'x':True,'y':.5}},{'action':'tap','arguments':{'x':float('nan'),'y':.5}},{'observe_seconds':float('inf')},{'observe_seconds':True}])
def test_closed_request(kw):
 with pytest.raises(ValidationError):ControlStep.model_validate(request(**kw))

def test_cli_pairs_one_action_receipt_and_image(tmp_path,monkeypatch,capsys):
 from hidagent import cli
 c,seen=fixture(monkeypatch);monkeypatch.setattr(cli,'IPhoneClient',lambda url:c)
 source=tmp_path/'request.json';source.write_text(json.dumps(request()))
 assert cli.main(['--url','http://memory-only','control-step','--request-file',str(source),'--receipt',str(tmp_path/'r.json'),'--screenshot',str(tmp_path/'s.jpg')])==0
 r=json.loads(capsys.readouterr().out)
 assert r['status']=='executed_unverified' and r['screenshot_path']==str(tmp_path/'s.jpg')
 assert [x for x in seen if x[0]=='POST']==[('POST','/api/key')]

def test_post_action_other_owner_withholds_frame(tmp_path,monkeypatch):
 monkeypatch.setattr('hidagent.frame_privacy.known_excluded_overlay',lambda f:None)
 posts=[];states=0
 def handler(r):
  nonlocal states
  if r.url.path=='/api/status':
   states+=1
   return httpx.Response(200,json=state(control_activity={'voice_sessions':1}) if states>1 else state())
  if r.method=='POST':posts.append(r.url.path)
  assert r.url.path!='/api/frame.jpg','Do not capture another active task frame'
  return httpx.Response(200,json={'running':False,'ok':True})
 with IPhoneClient('http://memory-only',transport=httpx.MockTransport(handler)) as c:
  r=run_step(c,request(),tmp_path/'r.json',tmp_path/'s.jpg')
 assert posts==['/api/key'] and r['observation']=='unavailable_or_binding_changed_or_busy' and not (tmp_path/'s.jpg').exists()
