import asyncio,io
from unittest.mock import AsyncMock,patch
from PIL import Image
import pytest
from hidagent.control_grounding import control_crop,screen_box,refine_control
from hidagent.decision import DecisionUnavailable
from hidagent.vision_judge import JudgeFallback

def frame():
 b=io.BytesIO();Image.new('RGB',(710,1080),'black').save(b,format='PNG');return b.getvalue()
COARSE={'left':.465,'top':.405,'right':.675,'bottom':.425}
CONTAINER={'left':.02,'top':.38,'right':.92,'bottom':.55}

def test_zoom_crop_maps_pixel_center_back_without_global_coordinates():
 image,g=control_crop(frame(),COARSE,CONTAINER)
 assert Image.open(io.BytesIO(image)).size==tuple(g['image_size'])
 x0,y0,x1,y1=g['bounds'];assert y0<448<470<y1
 wanted={'left':340/710,'right':480/710,'top':452/1080,'bottom':470/1080}
 local={k:((v*(710 if k in ('left','right') else 1080)-(x0 if k in ('left','right') else y0))/((x1-x0) if k in ('left','right') else (y1-y0))) for k,v in wanted.items()}
 assert screen_box(local,g)==pytest.approx(wanted)
 assert (screen_box(local,g)['top']+screen_box(local,g)['bottom'])/2>COARSE['bottom']

@pytest.mark.parametrize('bad',[{'left':True,'right':.8,'top':.1,'bottom':.2},{'left':.2,'right':.1,'top':.1,'bottom':.2},{'left':0,'right':float('nan'),'top':.1,'bottom':.2}])
def test_refined_invalid_box_rejected(bad):
 with pytest.raises(DecisionUnavailable):screen_box(bad,{'source':[710,1080],'bounds':[0,0,100,100]})

def test_outside_container_never_sends_cloud_request():
 client=AsyncMock()
 async def run():
  with pytest.raises(DecisionUnavailable):await refine_control(client,frame(),{**COARSE,'left':0,'right':.01},'a generic public control',container=CONTAINER)
  client.evaluate.assert_not_awaited()
 asyncio.run(run())

@pytest.mark.parametrize('ready,blocked,confidence',[(.98,0,1),(1,.02,1),(1,0,.49),(True,0,1),(1,0,float('inf'))])
def test_uncertain_grounding_never_becomes_coordinates(ready,blocked,confidence):
 async def run():
  client=AsyncMock();client.evaluate.return_value={'answers':{'ready':{'noul':ready},'blocked':{'noul':blocked},'target':{'confidence':confidence,'box':{'left':.2,'right':.8,'top':.2,'bottom':.8}}}}
  with pytest.raises(DecisionUnavailable):await refine_control(client,frame(),COARSE,'a generic public control',container=CONTAINER)
 asyncio.run(run())

def test_original_privacy_checked_before_crop_or_http():
 async def run():
  client=AsyncMock()
  with patch('hidagent.control_grounding.check_cloud_frame',side_effect=DecisionUnavailable('excluded')),pytest.raises(DecisionUnavailable):await refine_control(client,frame(),COARSE,'test',container=CONTAINER)
  client.evaluate.assert_not_awaited()
 asyncio.run(run())

def test_grounding_policy_retains_strict_gate():
 q={k:{} for k in ('ready','blocked','target')};a={'ready':{'noul':1},'blocked':{'noul':0},'target':{'confidence':.9}}
 assert JudgeFallback._approved({'policy':'precise_control_grounding'},q,a)
 assert not JudgeFallback._approved({'policy':'precise_control_grounding'},q,{**a,'blocked':{'noul':.02}})


def test_moderate_proposal_requires_independent_exact_point_and_rejects_wrong_center():
 async def run(accept):
  client=AsyncMock();client.evaluate.side_effect=[{'model':'proposal','answers':{'ready':{'noul':1},'blocked':{'noul':0},'target':{'confidence':.85,'box':{'left':.2,'right':.8,'top':.2,'bottom':.8}}}}, {'model':'verifier','answers':{'ready':{'noul':1 if accept else 0},'blocked':{'noul':0}}}]
  if accept:
   result=await refine_control(client,frame(),COARSE,'a public control',container=CONTAINER)
   assert result['point_verified'] and result['point_model']=='verifier'
  else:
   with pytest.raises(DecisionUnavailable):await refine_control(client,frame(),COARSE,'a public control',container=CONTAINER)
  assert client.evaluate.await_count==2
  assert client.evaluate.call_args.args[0]['policy']=='precise_control_point'
 for accept in (True,False):asyncio.run(run(accept))

def test_tall_refinement_does_not_drag_center_below_corroborated_target():
 from hidagent.control_grounding import click_box
 coarse={'left':.452,'right':.674,'top':.42,'bottom':.44}
 refined={'left':.459,'right':.675,'top':.418,'bottom':.469}
 box,strategy=click_box(coarse,refined,{'source':[710,1080]})
 assert strategy=='corroborated_overlap'
 assert (box['top']+box['bottom'])/2==pytest.approx(.43)
 far={**refined,'top':.5,'bottom':.52}
 assert click_box(coarse,far,{'source':[710,1080]})==(far,'local_proposal')

def test_high_confidence_alone_never_authorizes_click_center():
 async def run():
  client=AsyncMock();client.evaluate.side_effect=[{'model':'proposal','answers':{'ready':{'noul':1},'blocked':{'noul':0},'target':{'confidence':.99,'box':{'left':.2,'right':.8,'top':.2,'bottom':.8}}}}, {'model':'verifier','answers':{'ready':{'noul':0},'blocked':{'noul':0}}}]
  with pytest.raises(DecisionUnavailable):await refine_control(client,frame(),COARSE,'public control',container=CONTAINER)
  assert client.evaluate.await_count==2
 asyncio.run(run())


def test_proposal_edge_can_cross_container_but_crop_and_final_coordinates_cannot():
 coarse={**COARSE,'right':.94}
 _,g=control_crop(frame(),coarse,CONTAINER)
 assert g['bounds'][2]<=int(CONTAINER['right']*710)
 mapped=screen_box({'left':0,'top':0,'right':1,'bottom':1},g)
 assert mapped['right']<=CONTAINER['right'] and mapped['left']>=CONTAINER['left']

def test_one_geometry_correction_is_reverified_and_not_an_input_retry():
 async def run(final_ready):
  client=AsyncMock();proposal={'model':'proposal','answers':{'ready':{'noul':1},'blocked':{'noul':0},'target':{'confidence':.95,'box':{'left':.2,'right':.8,'top':.2,'bottom':.8}}}}
  correction={'model':'correction','answers':{'ready':{'noul':0},'blocked':{'noul':0},'target':{'confidence':.95,'box':{'left':.1,'right':.3,'top':.3,'bottom':.6}}}}
  final={'model':'final','answers':{'ready':{'noul':final_ready},'blocked':{'noul':0},'target':correction['answers']['target']}}
  client.evaluate.side_effect=[proposal,correction,final]
  if final_ready:
   r=await refine_control(client,frame(),COARSE,'public control',container=CONTAINER);assert r['point_checks']==2 and r['point_strategy']=='independently_corrected'
  else:
   with pytest.raises(DecisionUnavailable):await refine_control(client,frame(),COARSE,'public control',container=CONTAINER)
  assert client.evaluate.await_count==3
 for value in (1,0):asyncio.run(run(value))

def test_stop_after_first_model_prevents_point_upload():
 async def run():
  client=AsyncMock();cancelled=False
  async def evaluate(*args):
   nonlocal cancelled
   cancelled=True
   return {'answers':{}}
  client.evaluate.side_effect=evaluate
  def active():
   if cancelled:raise asyncio.CancelledError()
  with pytest.raises(asyncio.CancelledError):await refine_control(client,frame(),COARSE,'public control',container=CONTAINER,check_active=active)
  assert client.evaluate.await_count==1
 asyncio.run(run())

@pytest.mark.parametrize('accepted',[True,False])
def test_point_audit_retains_original_refinement_and_selected_center_without_model_text(accepted):
 async def run():
  local={'left':.1,'right':.7,'top':.25,'bottom':.65}
  client=AsyncMock();client.evaluate.side_effect=[
   {'model':'proposal','answers':{'ready':{'noul':1},'blocked':{'noul':0},'target':{'confidence':.95,'box':local,'reason':'DO NOT STORE RAW MODEL TEXT'}}},
   {'model':'verifier','answers':{'ready':{'noul':int(accepted)},'blocked':{'noul':0}}}]
  if accepted:
   result=await refine_control(client,frame(),COARSE,'generic control',container=CONTAINER)
  else:
   with pytest.raises(DecisionUnavailable) as error:await refine_control(client,frame(),COARSE,'generic control',container=CONTAINER)
   result=error.value.result
  audit=result['proposal_geometry']
  assert set(audit)=={'coarse_box','refined_box','selected_box','selection','confidence'}
  assert audit['coarse_box']==COARSE
  assert audit['refined_box']==pytest.approx(screen_box(local,result['geometry']))
  assert audit['selected_box']==pytest.approx(result['box'])
  assert audit['confidence']==.95
  assert 'DO NOT STORE' not in repr(result)
  assert client.evaluate.await_count==2
 asyncio.run(run())

def test_point_audit_keeps_initial_selection_after_independent_correction():
 async def run():
  local={'left':.1,'right':.7,'top':.25,'bottom':.65}
  corrected={'left':.7,'right':.9,'top':.7,'bottom':.9}
  client=AsyncMock();client.evaluate.side_effect=[
   {'answers':{'ready':{'noul':1},'blocked':{'noul':0},'target':{'confidence':.95,'box':local}}},
   {'answers':{'ready':{'noul':0},'blocked':{'noul':0},'target':{'confidence':.95,'box':corrected}}},
   {'answers':{'ready':{'noul':1},'blocked':{'noul':0}}}]
  result=await refine_control(client,frame(),COARSE,'generic control',container=CONTAINER)
  assert result['point_checks']==2
  assert result['proposal_geometry']['selected_box']!=result['box']
  assert result['box']==pytest.approx(screen_box(corrected,result['geometry']))
 asyncio.run(run())

@pytest.mark.parametrize('background,ink',[(40,180),(240,40)])
def test_isolated_symbol_candidate_uses_pixels_not_wrong_model_center(background,ink):
 from PIL import ImageDraw
 from hidagent.control_grounding import isolated_glyph_box
 im=Image.new('L',(160,160),background);d=ImageDraw.Draw(im);d.rectangle((75,80,90,96),outline=ink,width=2)
 raw=io.BytesIO();im.save(raw,format='PNG')
 proposal={'left':.45,'right':.55,'top':.42,'bottom':.51}
 found=isolated_glyph_box(raw.getvalue(),proposal,{'left':0,'right':1,'top':0,'bottom':1})
 assert found==pytest.approx({'left':75/160,'right':91/160,'top':80/160,'bottom':97/160})
 # A second neighboring glyph makes this ambiguous; no local candidate.
 d.rectangle((62,80,70,96),outline=ink,width=2);raw=io.BytesIO();im.save(raw,format='PNG')
 assert isolated_glyph_box(raw.getvalue(),proposal,{'left':0,'right':1,'top':0,'bottom':1}) is None


def test_isolated_glyph_does_not_authorize_click_without_independent_check():
 from PIL import ImageDraw
 async def run():
  im=Image.new('RGB',(710,1080),(40,40,40));ImageDraw.Draw(im).rectangle((385,445,400,461),outline='white',width=2)
  raw=io.BytesIO();im.save(raw,format='PNG')
  client=AsyncMock();client.evaluate.side_effect=[{'answers':{'ready':{'noul':1},'blocked':{'noul':0},'target':{'confidence':.95,'box':{'left':.4,'right':.5,'top':.25,'bottom':.35}}}}, {'answers':{'ready':{'noul':0},'blocked':{'noul':0}}}]
  with pytest.raises(DecisionUnavailable):await refine_control(client,raw.getvalue(),COARSE,'generic symbol',container=CONTAINER,symbol=True)
  assert client.evaluate.await_count==2
 asyncio.run(run())

@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_actual_public_copy_offset_is_resolved_to_unique_original_pixel_glyph():
 from pathlib import Path
 from hidagent.control_grounding import isolated_glyph_box
 raw=(Path(__file__).parent/'fixtures/control-grounding/copy-offset-public.png').read_bytes()
 # Genuine model proposal from copy-selection-causal-check, in fixture pixels.
 proposal={'left':(586.34-565)/60,'right':(602.5-565)/60,'top':(479.82-460)/60,'bottom':(491.92-460)/60}
 box=isolated_glyph_box(raw,proposal,{'left':0,'right':1,'top':0,'bottom':1})
 assert box==pytest.approx({'left':23/60,'right':36/60,'top':32/60,'bottom':46/60})
 assert (box['top']+box['bottom'])/2>(proposal['top']+proposal['bottom'])/2

@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_actual_reply_outline_excludes_neighbor_count_and_body_strokes():
 from pathlib import Path
 from hidagent.control_grounding import isolated_glyph_box
 raw=(Path(__file__).parent/'fixtures/control-grounding/reply-and-counter-public.png').read_bytes()
 proposal={'left':(110.31-90)/90,'right':(143.97-90)/90,'top':(320.5-280)/80,'bottom':(353.5-280)/80}
 result=isolated_glyph_box(raw,proposal,{'left':0,'right':1,'top':0,'bottom':1})
 assert result==pytest.approx({'left':36/90,'right':53/90,'top':42/80,'bottom':58/80})


def test_coordinate_marker_does_not_overwrite_small_control_outline():
 from PIL import ImageDraw
 from hidagent.control_grounding import marked_control
 im=Image.new('RGB',(400,300),'black');ImageDraw.Draw(im).rectangle((190,140,210,160),outline='white',width=2)
 raw=io.BytesIO();im.save(raw,format='PNG')
 marked=Image.open(io.BytesIO(marked_control(raw.getvalue(),{'left':190/400,'right':210/400,'top':140/300,'bottom':160/300},{'source':[400,300],'bounds':[0,0,400,300],'image_size':[400,300]})))
 assert all(marked.getpixel((x,y))[1]>200 for x in range(190,211) for y in range(140,161) if im.getpixel((x,y))==(255,255,255))


def test_observation_parking_is_outside_owned_content_and_inside_viewport():
 from hidagent.control_grounding import observation_parking_point
 point=observation_parking_point({'left':.82,'right':.85,'top':.57,'bottom':.60},{'left':.02,'right':.925,'top':.53,'bottom':.66})
 assert point==pytest.approx((.955,.585))
 assert not .02<point[0]<.925
 with pytest.raises(DecisionUnavailable):observation_parking_point({'left':.4,'right':.5,'top':.4,'bottom':.5},{'left':0,'right':1,'top':0,'bottom':1})


def test_strict_local_confidence_contract_rejects_uncertain_refinement():
 import asyncio
 from unittest.mock import AsyncMock
 from types import SimpleNamespace
 from hidagent.control_grounding import refine_control
 client=SimpleNamespace(evaluate=AsyncMock(return_value={'answers':{'ready':{'noul':1},'blocked':{'noul':0},'target':{'confidence':.85,'box':{'left':.4,'right':.5,'top':.4,'bottom':.5}}}}))
 im=Image.new('RGB',(710,1080),'black');b=io.BytesIO();im.save(b,format='PNG')
 with pytest.raises(DecisionUnavailable):asyncio.run(refine_control(client,b.getvalue(),{'left':.4,'right':.5,'top':.4,'bottom':.5},'Reply',container={'left':.1,'right':.9,'top':.1,'bottom':.9},minimum_confidence=.9))
 assert client.evaluate.await_count==1


@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_actual_dual_proposal_symbol_uses_unique_contained_glyph_not_body():
 from pathlib import Path
 from hidagent.control_grounding import symbol_candidate
 raw=(Path(__file__).parent/'fixtures/control-grounding/reply-near-text-public.png').read_bytes()
 coarse={'left':27.8/85,'right':63.3/85,'top':38.08/70,'bottom':59.68/70}
 refined={'left':24.875/85,'right':59.525/85,'top':22.75/70,'bottom':40.75/70}
 box=symbol_candidate(raw,coarse,refined,{'left':0,'right':1,'top':0,'bottom':1})
 assert box==pytest.approx({'left':26/85,'right':43/85,'top':39/70,'bottom':55/70})


def test_dual_proposal_disagreement_is_not_a_symbol_candidate():
 from unittest.mock import patch
 from hidagent.control_grounding import symbol_candidate
 a={'left':.1,'right':.2,'top':.1,'bottom':.2};b={'left':.3,'right':.4,'top':.3,'bottom':.4}
 with patch('hidagent.control_grounding.isolated_glyph_box',side_effect=[None,a,b]):
  assert symbol_candidate(b'',a,b,CONTAINER) is None


@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_actual_text_label_vertical_overshoot_snaps_only_to_unique_ink_line():
 from pathlib import Path
 from hidagent.control_grounding import text_line_box
 raw=(Path(__file__).parent/'fixtures/control-grounding/text-line-public.png').read_bytes()
 proposal={'left':17.868/210,'right':174.055/210,'top':19.575/80,'bottom':57.705/80}
 box=text_line_box(raw,proposal,{'left':0,'right':1,'top':0,'bottom':1})
 assert box is not None
 assert box['left']==proposal['left'] and box['right']==proposal['right']
 assert 18/80<(box['top']+box['bottom'])/2<35/80
 assert (box['top']+box['bottom'])/2<(proposal['top']+proposal['bottom'])/2


def test_text_row_proposal_rejects_two_equally_near_lines_and_blank():
 from PIL import ImageDraw
 from hidagent.control_grounding import text_line_box
 proposal={'left':.1,'right':.9,'top':.4,'bottom':.5};container={'left':0,'right':1,'top':0,'bottom':1}
 im=Image.new('L',(200,150),0);d=ImageDraw.Draw(im);d.text((30,45),'ONE CONTROL',fill=255);d.text((30,75),'TWO CONTROL',fill=255)
 b=io.BytesIO();im.save(b,format='PNG');assert text_line_box(b.getvalue(),proposal,container) is None
 blank=io.BytesIO();Image.new('L',(200,150),255).save(blank,format='PNG');assert text_line_box(blank.getvalue(),proposal,container) is None


@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_text_line_candidate_never_overrides_independent_point_rejection():
 from pathlib import Path
 from hidagent.control_grounding import refine_control
 async def run():
  raw=(Path(__file__).parent/'fixtures/control-grounding/text-line-public.png').read_bytes()
  proposal={'left':17.868/210,'right':174.055/210,'top':19.575/80,'bottom':57.705/80};container={'left':0,'right':1,'top':0,'bottom':1}
  client=AsyncMock();client.evaluate.side_effect=[{'answers':{'ready':{'noul':1},'blocked':{'noul':0},'target':{'confidence':.95,'box':{'left':.1,'right':.8,'top':.4,'bottom':.7}}}}, {'answers':{'ready':{'noul':0},'blocked':{'noul':1}}}]
  with pytest.raises(DecisionUnavailable):await refine_control(client,raw,proposal,'actual text control',container=container,text_label=True)
  assert client.evaluate.await_count==2
 asyncio.run(run())


@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_pixel_center_route_requires_independent_control_identity_not_model_coordinates():
 from pathlib import Path
 async def run():
  raw=(Path(__file__).parent/'fixtures/control-grounding/copy-offset-public.png').read_bytes()
  coarse={'left':.35,'right':.65,'top':.3,'bottom':.6};container={'left':0,'right':1,'top':0,'bottom':1}
  client=AsyncMock();client.evaluate.side_effect=[{'model':'synthetic','answers':{'ready':{'noul':1},'blocked':{'noul':0},'target':{'confidence':.95,'box':{'left':.35,'right':.65,'top':.3,'bottom':.6}}}}, {'model':'synthetic','answers':{'ready':{'noul':1},'blocked':{'noul':0}}}]
  r=await refine_control(client,raw,coarse,'native Copy icon',container=container,symbol=True,pixel_identity=True)
  assert client.evaluate.await_args.args[0]['policy']=='precise_control_identity'
  assert r['point_strategy']=='original_pixels_and_independent_identity'
  assert r['point_verified'] and r['point_checks']==1
 asyncio.run(run())


@pytest.mark.parametrize('ready,blocked',[(.98,0),(1,.02),(0,0),(True,0),(1,None)])
@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_pixel_identity_never_downgrades_original_independent_thresholds(ready,blocked):
 from pathlib import Path
 async def run():
  raw=(Path(__file__).parent/'fixtures/control-grounding/copy-offset-public.png').read_bytes();coarse={'left':.35,'right':.65,'top':.3,'bottom':.6};container={'left':0,'right':1,'top':0,'bottom':1}
  client=AsyncMock();client.evaluate.side_effect=[{'answers':{'ready':{'noul':1},'blocked':{'noul':0},'target':{'confidence':.95,'box':coarse}}},{'answers':{'ready':{'noul':ready},'blocked':{'noul':blocked}}}]
  with pytest.raises(DecisionUnavailable):await refine_control(client,raw,coarse,'native Copy icon',container=container,symbol=True,pixel_identity=True)
  assert client.evaluate.await_count==2
 asyncio.run(run())


def test_primary_pixel_identity_policy_uses_original_strict_thresholds():
 from hidagent.vision_judge import JudgeFallback
 assert JudgeFallback._approved({'policy':'precise_control_identity'},{},{'ready':{'noul':1},'blocked':{'noul':0}})
 assert not JudgeFallback._approved({'policy':'precise_control_identity'},{},{'ready':{'noul':.98},'blocked':{'noul':0}})


@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_text_identity_context_expands_observation_without_changing_click_box():
 from pathlib import Path
 from hidagent.control_grounding import identity_crop
 raw=(Path(__file__).parent/'fixtures/control-grounding/text-line-public.png').read_bytes()
 box={'left':20/210,'right':169/210,'top':15/80,'bottom':35/80};before=dict(box);container={'left':0,'right':1,'top':0,'bottom':1}
 _,tight=identity_crop(raw,box,container);_,context=identity_crop(raw,box,container,text_context=True)
 assert context['bounds'][2]>tight['bounds'][2] and context['bounds'][0]<=tight['bounds'][0]
 assert box==before and context['bounds'][2]<=210


@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_uncertain_model_coordinates_are_not_authority_when_unique_pixels_and_identity_prove_center():
 from pathlib import Path
 async def run(identity):
  raw=(Path(__file__).parent/'fixtures/control-grounding/copy-offset-public.png').read_bytes();coarse={'left':.35,'right':.65,'top':.3,'bottom':.6};container={'left':0,'right':1,'top':0,'bottom':1}
  client=AsyncMock();client.evaluate.side_effect=[{'answers':{'ready':{'noul':1},'blocked':{'noul':0},'target':{'confidence':.88,'box':coarse}}},{'answers':{'ready':{'noul':identity},'blocked':{'noul':1-identity}}}]
  if identity:
   r=await refine_control(client,raw,coarse,'Copy',container=container,symbol=True,pixel_identity=True,minimum_confidence=.9)
   assert r['point_strategy']=='original_pixels_and_independent_identity'
  else:
   with pytest.raises(DecisionUnavailable):await refine_control(client,raw,coarse,'Copy',container=container,symbol=True,pixel_identity=True,minimum_confidence=.9)
  assert client.evaluate.await_count==2
 asyncio.run(run(1));asyncio.run(run(0))

def test_uncertain_model_only_coordinates_still_reject_at_original_threshold():
 async def run():
  im=Image.new('RGB',(710,1080),'black');b=io.BytesIO();im.save(b,format='PNG')
  client=AsyncMock();client.evaluate.return_value={'answers':{'ready':{'noul':1},'blocked':{'noul':0},'target':{'confidence':.88,'box':COARSE}}}
  with pytest.raises(DecisionUnavailable):await refine_control(client,b.getvalue(),COARSE,'Reply',container=CONTAINER,symbol=True,pixel_identity=True,minimum_confidence=.9)
  assert client.evaluate.await_count==1
 asyncio.run(run())


@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_actual_body_character_tiny_hole_is_not_a_hollow_ui_icon():
 from pathlib import Path
 from hidagent.control_grounding import isolated_glyph_box,symbol_candidate
 raw=(Path(__file__).parent/'fixtures/control-grounding/hollow-vs-body-public.png').read_bytes();container={'left':0,'right':1,'top':0,'bottom':1}
 body={'left':13.6/80,'right':42/80,'top':14.6/80,'bottom':38.36/80}
 reply={'left':24.25/80,'right':45.55/80,'top':47/80,'bottom':68.6/80}
 assert isolated_glyph_box(raw,body,container,contained=True) is None
 assert symbol_candidate(raw,body,reply,container)==pytest.approx({'left':26/80,'right':43/80,'top':49/80,'bottom':65/80})


@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_actual_copy_shifted_proposal_still_finds_only_local_hollow_symbol():
 from pathlib import Path
 from hidagent.control_grounding import isolated_glyph_box
 raw=(Path(__file__).parent/'fixtures/control-grounding/copy-high-proposal-public.png').read_bytes()
 # Actual rejected full-frame proposal mapped to the original 80x80 UI crop.
 proposal={'left':(0.8264084507042253*710-565)/80,'right':(0.8471126760563381*710-565)/80,'top':(0.5617685185185185*1080-592)/80,'bottom':(0.5761296296296297*1080-592)/80}
 found=isolated_glyph_box(raw,proposal,{'left':0,'top':0,'right':1,'bottom':1})
 assert found is not None
 assert 25<(found['left']+found['right'])*40<35
 assert 33<(found['top']+found['bottom'])*40<46

@pytest.mark.parametrize('ready,blocked,ok',[(1,0,True),(.98,0,False),(1,.02,False),(True,0,False)])
def test_plain_content_center_requires_independent_strict_point(ready,blocked,ok):
 from hidagent.control_grounding import verify_content_center
 async def run():
  client=AsyncMock();client.evaluate.return_value={'answers':{'ready':{'noul':ready},'blocked':{'noul':blocked}}}
  if ok:
   result=await verify_content_center(client,frame(),COARSE,'plain body',container=CONTAINER,proposal_confidence=.9)
   assert result['box']==COARSE and result['point_checks']==1
  else:
   with pytest.raises(DecisionUnavailable):await verify_content_center(client,frame(),COARSE,'plain body',container=CONTAINER,proposal_confidence=.9)
  assert client.evaluate.await_count==1
 asyncio.run(run())

@pytest.mark.parametrize('confidence',[.89,True,None,float('nan')])
def test_plain_content_uncertain_proposal_rejects_before_model(confidence):
 from hidagent.control_grounding import verify_content_center
 async def run():
  client=AsyncMock()
  with pytest.raises(DecisionUnavailable):await verify_content_center(client,frame(),COARSE,'plain body',container=CONTAINER,proposal_confidence=confidence)
  client.evaluate.assert_not_awaited()
 asyncio.run(run())
