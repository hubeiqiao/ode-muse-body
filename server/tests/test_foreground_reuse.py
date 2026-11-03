import asyncio,io,time
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from PIL import Image,ImageDraw
import pytest
from hidagent.foreground_reuse import same_visible_app,reuse_foreground,remember_completed_app

def picture(change=None):
 im=Image.new('RGB',(710,1080),'white');d=ImageDraw.Draw(im);d.rectangle((30,80,600,160),fill='blue')
 if change:d.rectangle(change,fill='red')
 b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()

def fixture():
 now=time.time();frame=picture();phone=SimpleNamespace(device=SimpleNamespace(connected=True,links=2),calibrated=True,
  target_info=lambda:{'device':'ipad','calibration_key':'ipad-portrait-66'},ensure_live=AsyncMock())
 a=SimpleNamespace(phone=phone,screen=SimpleNamespace(frame=frame,live=True,updated=now),trace=Mock(),_foreground_epoch=1)
 a._foreground_candidate={'app':'YouTube','frame':frame,'pointer':(.5,.5),'key':'ipad-portrait-66','link':2,
  'at':time.monotonic(),'checked_at':now-.2,'task_id':'task1'}
 return a

def test_clock_and_pointer_do_not_hide_other_changed_content():
 base=picture()
 assert same_visible_app(base,picture((5,5,30,20)),(.5,.5))
 assert same_visible_app(base,picture((350,535,360,545)),(.5,.5))
 assert not same_visible_app(base,picture((5,60,100,95)),(.5,.5))  # notification/banner
 assert not same_visible_app(base,picture((200,200,204,209)),(.5,.5))  # tiny new text, no mean threshold
 assert not same_visible_app(base,b'bad image',(.5,.5))

def test_current_fresh_identical_public_view_needs_no_hid_or_model():
 a=fixture();r=asyncio.run(reuse_foreground(a,'YouTube'))
 assert r is not None and not r['liveness_probe_used'];a.phone.ensure_live.assert_not_awaited()

@pytest.mark.parametrize('bad',['other_app','threads','expired','future_proof','link','geometry','offline','uncalibrated','changed_content'])
def test_missing_current_proof_cannot_reuse_or_probe(bad):
 a=fixture();app='YouTube'
 if bad=='other_app':app='LinkedIn'
 if bad=='threads':app=a._foreground_candidate['app']='Threads'
 if bad=='expired':a._foreground_candidate['at']-=121
 if bad=='future_proof':a._foreground_candidate['at']+=5
 if bad=='link':a.phone.device.links+=1
 if bad=='geometry':a._foreground_candidate['key']='ipad-landscape-66'
 if bad=='offline':a.screen.live=False
 if bad=='uncalibrated':a.phone.calibrated=False
 if bad=='changed_content':a.screen.frame=picture((200,200,204,209))
 assert asyncio.run(reuse_foreground(a,app)) is None
 a.phone.ensure_live.assert_not_awaited();a.trace.write.assert_not_called()

def test_old_receipt_timestamp_needs_a_real_new_frame():
 a=fixture();a.screen.updated=time.time()-10
 async def probe(**kwargs):assert kwargs=={'force_probe':True};a.screen.updated=time.time()
 a.phone.ensure_live=probe
 assert asyncio.run(reuse_foreground(a,'YouTube'))['liveness_probe_used']

def test_probe_ack_without_new_frame_is_not_current_foreground_proof():
 a=fixture();a.screen.updated=time.time()-10
 assert asyncio.run(reuse_foreground(a,'YouTube')) is None
 a.phone.ensure_live.assert_awaited_once_with(force_probe=True)

def test_stop_during_probe_prevents_late_reuse():
 a=fixture();a.screen.updated=time.time()-10
 async def probe(**kw):a._foreground_epoch+=1;a.screen.updated=time.time()
 a.phone.ensure_live=probe
 with pytest.raises(asyncio.CancelledError):asyncio.run(reuse_foreground(a,'YouTube'))
 a.trace.write.assert_not_called()

def test_new_scene_during_probe_discards_proof():
 a=fixture();a.screen.updated=time.time()-10
 async def probe(**kw):a.screen.updated=time.time();a.screen.frame=picture((200,200,204,209))
 a.phone.ensure_live=probe
 assert asyncio.run(reuse_foreground(a,'YouTube')) is None

def test_only_verified_completed_public_app_is_remembered():
 a=fixture();a.task='Open YouTube';a.steps=[{'t':1},{'action':'done','_verified_icon_entry':True}];a.shots={1:a.screen.frame}
 assert remember_completed_app(a)['app']=='YouTube'
 a.steps[-1]={'action':'done'};assert remember_completed_app(a) is None
 a.steps[-1]={'action':'done','_verified_icon_entry':True};a.task='Open Threads';assert remember_completed_app(a) is None

def test_system_home_indicator_and_sparse_codec_noise_are_not_navigation():
 base=picture()
 assert same_visible_app(base,picture((220,1067,490,1073)),(.5,.5))
 im=Image.open(io.BytesIO(base)).convert('RGB');ImageDraw.Draw(im).rectangle((200,250,300,350),fill=(253,253,253))
 b=io.BytesIO();im.save(b,format='PNG');assert same_visible_app(base,b.getvalue(),(.5,.5))
 assert not same_visible_app(base,picture((300,1040,310,1050)),(.5,.5))
 assert not same_visible_app(base,picture((200,40,204,50)),(.5,.5))

def real_agent():
 from hidagent.hybrid_agent import HybridAgent
 return HybridAgent(None,SimpleNamespace(live=True),SimpleNamespace(model='judge-test'),SimpleNamespace(model='haiku-test'),Mock())

def test_hybrid_stop_invalidates_completed_and_candidate_proofs():
 a=real_agent();a._completed_app_source={'app':'YouTube'};a._foreground_candidate={'app':'YouTube'};a.stop()
 assert a._completed_app_source is None and a._foreground_candidate is None

def test_clear_guard_preserves_state_while_running_then_discards_it_when_idle():
 a=real_agent();a._completed_app_source={'app':'YouTube'};a._job=SimpleNamespace(done=lambda:False)
 with pytest.raises(RuntimeError):a.clear()
 assert a._completed_app_source is not None
 a._job=None;a.clear();assert a._completed_app_source is None

def test_late_done_after_stop_cannot_recapture_a_proof(monkeypatch):
 from hidagent.jev_agent import JevAgent
 from hidagent import foreground_reuse
 a=real_agent();a._completed_app_source=None
 async def late(self):self.stop();return True
 monkeypatch.setattr(JevAgent,'_step_actions',late)
 remember=Mock(return_value={'app':'YouTube'});monkeypatch.setattr(foreground_reuse,'remember_completed_app',remember)
 assert asyncio.run(a._step_actions()) is True
 remember.assert_not_called();assert a._completed_app_source is None


def test_foreground_identity_is_read_only_and_bound_to_current_proof():
 from hidagent.foreground_reuse import current_foreground_app
 a=fixture();a._completed_app_source=a._foreground_candidate;a._foreground_candidate=None
 assert current_foreground_app(a)=='YouTube'
 a.phone.ensure_live.assert_not_awaited()
 a.screen.frame=picture((100,200,150,250))
 assert current_foreground_app(a) is None


def test_generic_foreground_open_skips_spotlight_even_without_launch_flag(monkeypatch):
 from hidagent.spotlight_launch import prepare
 monkeypatch.delenv('APP_LAUNCH_SPOTLIGHT_FIRST',raising=False)
 a=fixture();a.steps=[]
 a.phone.key=AsyncMock();a.phone.type=AsyncMock()
 actions=asyncio.run(prepare(a,{'app':'YouTube','text':None}))
 assert actions and actions[0]['action']=='done'
 a.phone.key.assert_not_awaited();a.phone.type.assert_not_awaited()
