import asyncio
import io
from types import SimpleNamespace
import pytest
from PIL import Image
from hidagent.hybrid_agent import HybridAgent
from hidagent.debug import Trace
from hidagent.decision import DecisionUnavailable


def make_agent(monkeypatch, reply, verdict='allow', confidence=1):
    monkeypatch.setattr('hidagent.hybrid_agent.scene_changed',lambda *a:False)
    image=io.BytesIO();Image.new('RGB',(498,1080)).save(image,format='JPEG')
    class Planner:
        model='haiku-test'
        calls=0
        async def chat(self,*args,**kwargs):
            self.calls+=1
            return reply
    class Judge:
        model='jev-test'
        def __init__(self):self.calls=[]
        async def evaluate(self,state,questions,image):
            self.calls.append(state)
            return {'seconds':.1,'answers':{'ready':{'noul':confidence if verdict=='allow' else 0}, **({'surface': {'box': {'left':.4,'right':.9,'top':.1,'bottom':.9}, 'confidence':confidence}} if 'surface' in questions else {})}}
    screen=SimpleNamespace(frame=image.getvalue(),live=True)
    phone=SimpleNamespace(target_info=lambda:{'device':'iphone'})
    a=HybridAgent(phone,screen,Judge(),Planner(),Trace(None));a.task='open Notes'
    return a


def test_crop_judgment_never_receives_original_screen_coordinates(monkeypatch):
    a=make_agent(monkeypatch,'DO: tap 249 540')
    act=asyncio.run(a._next_actions())[0]
    assert (act['x'],act['y'])==(500,500)
    assert a.client.calls[-1]['candidate_action']['point']=='center of supplied crop'
    assert 'x' not in a.client.calls[-1]['candidate_action'] and 'y' not in a.client.calls[-1]['candidate_action']
    assert a.planner.calls==1


@pytest.mark.parametrize('verdict,confidence',[('revise',1),('allow',.5)])
def test_unapproved_proposal_never_reaches_executor(monkeypatch,verdict,confidence):
    a=make_agent(monkeypatch,'DO: tap 249 540',verdict,confidence)
    with pytest.raises(DecisionUnavailable):asyncio.run(a._next_actions())
    assert a.planner.calls==2


def test_done_is_judged_instead_of_trusting_planner(monkeypatch):
    a=make_agent(monkeypatch,'DO: done App is open','revise')
    with pytest.raises(DecisionUnavailable):asyncio.run(a._next_actions())
    assert a.client.calls[-1]['candidate_action']['action']=='done'


def test_inline_target_label_does_not_corrupt_tap_coordinates(monkeypatch):
    a=make_agent(monkeypatch,'THINK: Open Notes\nDO: tap 249 540 TARGET: Notes 2 app icon')
    act=asyncio.run(a._next_actions())[0]
    assert act['action']=='tap' and (act['x'],act['y'])==(500,500)


def test_exact_text_fast_path_skips_planner_and_only_verifies_after_typing(monkeypatch):
    monkeypatch.setattr('hidagent.jev_agent.scene_changed',lambda *a:False)
    a=make_agent(monkeypatch,'DO: key enter');a.task='type Agent73'
    class FocusJudge:
        model='jev-test'
        def __init__(self):self.calls=[]
        async def evaluate(self,state,questions,image):
            self.calls.append(state)
            return {'seconds':.1,'answers':{'condition':{'noul':1},
                    'field':{'box':{'left':.1,'right':.9,'top':.05,'bottom':.15}}}}
    a.client=FocusJudge()
    action=asyncio.run(a._next_actions())[0]
    assert (action['action'],action['text'])==('type','Agent73')
    a.steps=[{'action':'type','text':'Agent73'}]
    assert asyncio.run(a._next_actions())[0]['action']=='done'
    assert a.planner.calls==0
    assert [r['already_typed'] for r in a.client.calls]==[False,True]


def test_drawing_actions_use_pixel_scaling(monkeypatch):
    a=make_agent(monkeypatch,'DO: circle 249 540 100');a.task='Draw a smiley in a new Notes drawing';a._demo_note_created=True
    action=asyncio.run(a._next_actions())[0]
    assert (action['x'],action['y'],action['r'])==(500,500,201)


def test_drawing_outside_screen_is_rejected_before_judgment(monkeypatch):
    a=make_agent(monkeypatch,'DO: path 10 20 9999 400')
    with pytest.raises(DecisionUnavailable,match='outside'):asyncio.run(a._next_actions())
    assert not a.client.calls


def test_first_click_does_not_request_expensive_pointer_recovery(monkeypatch):
    a=make_agent(monkeypatch,'DO: tap 249 540 precise')
    action=asyncio.run(a._next_actions())[0]
    assert 'precise' not in action
    a.steps=[{'action':'tap','x':500,'y':500,'changed':False}]
    action=asyncio.run(a._next_actions())[0]
    assert action['precise'] is True


def test_borderline_tap_is_refined_before_replanning(monkeypatch):
    a=make_agent(monkeypatch,'DO: tap 90 235\nTARGET: JoeSpeaking app icon')
    class Judge:
        model='jev-test'
        def __init__(self):self.calls=0
        async def evaluate(self,*args):
            self.calls+=1
            return {'seconds':.1,'answers':{'ready':{'noul':.88 if self.calls==1 else .96}}}
    a.client=Judge()
    act=asyncio.run(a._next_actions())[0]
    assert act['action']=='tap'
    assert a.client.calls==2 and a.planner.calls==1


def test_uncertain_refinement_never_executes_tap(monkeypatch):
    a=make_agent(monkeypatch,'DO: tap 90 235\nTARGET: JoeSpeaking app icon',confidence=.88)
    with pytest.raises(DecisionUnavailable):asyncio.run(a._next_actions())
    assert a.planner.calls==2
    assert len(a.client.calls)==4


def test_app_identity_uses_user_goal_instead_of_truncated_label(monkeypatch):
    a=make_agent(monkeypatch,'THINK: Tap installed app icon\nDO: tap 91 232\nTARGET: JoeSpeaki...')
    a.task='open Joe Speaking'
    class IdentityJudge:
        model='jev-test'
        async def evaluate(self,state,questions,image):
            q=questions['ready']['instructions']
            assert 'Joe Speaking application' in q
            assert 'ellipsis' in q
            return {'seconds':.1,'answers':{'ready':{'noul':.97}}}
    a.client=IdentityJudge()
    assert asyncio.run(a._next_actions())[0]['action']=='tap'


def test_demo_never_draws_before_creating_fresh_note(monkeypatch):
    a=make_agent(monkeypatch,'DO: circle 249 540 100')
    a.task='Open Notes and create a new note and draw a smiley'
    with pytest.raises(DecisionUnavailable):asyncio.run(a._next_actions())
    assert not a.client.calls


def test_demo_writer_generates_once_and_freezes_fresh_copy(monkeypatch):
    import json
    a=make_agent(monkeypatch,'');a.task='Create an original Shopify Builder Sundays note'
    class Writer:
        model='haiku-test'
        def __init__(self):self.calls=0
        async def chat(self,*args,**kwargs):
            self.calls+=1
            return json.dumps({'title':'Shopify Builder Sundays','body':f'Fresh idea number {self.calls}: a tiny board gives AI a real iPhone.','principle':'Haiku -> Jev -> ESP32 -> iPhone'})
    a.planner=Writer()
    asyncio.run(a._create_demo_draft());first=a._demo_draft.copy()
    asyncio.run(a._create_demo_draft());second=a._demo_draft.copy()
    assert first['body']!=second['body'] and a.planner.calls==2
    assert second['principle']=='Haiku -> Jev -> ESP32 -> iPhone'


def test_invalid_generated_draft_stops_before_any_typing(monkeypatch):
    a=make_agent(monkeypatch,'not JSON');a.task='Write original Shopify Builder Sundays copy'
    with pytest.raises(DecisionUnavailable,match='valid fresh'):
        asyncio.run(a._create_demo_draft())
    assert not a.client.calls


def test_new_note_guard_allows_only_checked_notes_search_navigation(monkeypatch):
    a=make_agent(monkeypatch,'DO: type Notes');a.task='Open Notes and create a new note for Shopify Builder Sundays'
    a._demo_draft={'title':'Shopify Builder Sundays','body':'Fresh body for the event today.','principle':'Haiku -> Jev -> ESP32 -> iPhone'}
    class SearchJudge:
        model='jev-test'
        async def evaluate(self,state,questions,image):
            assert 'Spotlight' in questions['ready']['instructions']
            return {'seconds':.1,'answers':{'ready':{'noul':.98}}}
    a.client=SearchJudge()
    action=asyncio.run(a._next_actions())[0]
    assert (action['action'],action['text'])==('type','Notes')


def test_authored_draft_formats_once_without_regenerating_each_line(monkeypatch):
    a=make_agent(monkeypatch,'DO: fail planner must not run')
    a._demo_draft={'title':'Shopify Builder Sundays','body':'A fresh AI idea comes to life on this real iPhone.','principle':'Haiku -> Jev -> ESP32 -> iPhone'}
    a._demo_note_created=True
    sequence=[]
    for _ in range(6):
        action=asyncio.run(a._next_actions())[0]
        sequence.append(action['action'])
        a.steps.append({k:v for k,v in action.items() if k!='_shot'})
    assert sequence==['type','paragraph','type','paragraph','type','done']
    assert [s['text'] for s in a.steps if s['action']=='type']==list(a._demo_draft.values())
    assert a.planner.calls==0


def test_paragraph_macro_sends_exactly_two_returns(monkeypatch):
    from unittest.mock import AsyncMock
    a=make_agent(monkeypatch,'');a.phone.key=AsyncMock()
    asyncio.run(a._do({'action':'paragraph','count':2}))
    assert a.phone.key.await_count==2
    assert all(call.args==('enter',) for call in a.phone.key.await_args_list)


def test_lost_typing_receipt_stops_without_automatic_retry(monkeypatch):
    from hidagent.device import DeviceError
    from hidagent.agent import BlindError
    a = make_agent(monkeypatch, "DO: type test")
    calls = []
    async def type_text(text):
        calls.append(text)
        raise DeviceError("ack lost after partial input")
    a.phone.type = type_text
    with pytest.raises(BlindError, match="Typing delivery uncertain"):
        asyncio.run(a._do({"action":"type", "text":"test"}))
    assert calls == ["test"]
    assert a.steps[-1]["action"] == "type_uncertain"


def test_calculator_arithmetic_uses_finite_jev_workflow(monkeypatch):
    a = make_agent(monkeypatch, "DO: tap 100 100")
    a.task = "Open Calculator and calculate 27 * 14, verify 378."
    calls = []
    async def finite(agent):
        calls.append(agent.task)
        return [{"action":"key", "combo":"enter"}]
    monkeypatch.setattr("hidagent.jev_agent.JevAgent._next_actions", finite)
    assert asyncio.run(a._next_actions()) == [{"action":"key", "combo":"enter"}]
    assert calls == [a.task] and a.planner.calls == 0


def test_plain_calculator_open_still_uses_normal_planning(monkeypatch):
    a = make_agent(monkeypatch, "DO: key cmd+space")
    a.task = "Open Calculator"
    assert asyncio.run(a._next_actions())[0]["action"] == "key"
    assert a.planner.calls == 1


def test_x_compose_is_not_tagged_as_notes_creation(monkeypatch):
    a=make_agent(monkeypatch,'THINK: Tap compose\nDO: tap 450 900 TARGET: compose')
    a.task='Open X and draft a tweet without posting'
    action=asyncio.run(a._next_actions())[0]
    assert action['action']=='tap'
    assert 'creates_test_note' not in action and 'expected_effect' not in action


def test_compound_x_launch_is_fast_but_does_not_complete_the_tweet(monkeypatch):
    a=make_agent(monkeypatch,'DO: done incorrect')
    a.task='Open X, draft a tweet without posting';a.keyboard_workflows=True
    async def opened(agent, goal):
        assert goal=={'app':'X','text':None}
        return [{'action':'done','phase':'verified','workflow':'keyboard'}]
    monkeypatch.setattr('hidagent.shortcut_workflow.next_shortcut_actions',opened)
    action=asyncio.run(a._next_actions())[0]
    assert action['action']=='wait' and action['phase']=='opened_X'
    assert a.planner.calls==0


def test_proven_unsent_text_is_not_mislabeled_partial_input(monkeypatch):
    from hidagent.device import DeviceError
    a=make_agent(monkeypatch,'DO: done')
    async def failed(text):
        e=DeviceError('disconnected before write',delivery='not_sent',command_name='type')
        e.typing_may_have_happened=False
        raise e
    a.phone.type=failed
    with pytest.raises(DeviceError):asyncio.run(a._do({'action':'type','text':'hello'}))
    assert a.steps[-1]['action']=='type_not_sent'
    assert not any(s['action']=='type_uncertain' for s in a.steps)


def test_content_card_is_grounded_on_full_context_not_a_face_crop(monkeypatch):
    a=make_agent(monkeypatch,'DO: tap 372 690\nTARGET: 零环境练口语，求方法 video thumbnail')
    a.task='Click on the post about methods for practicing spoken language with zero environment'
    calls=[]
    class Ground:
        async def evaluate(self,state,questions,frame):
            calls.append((state,questions,frame))
            return {'seconds':.2,'answers':{'target':{'box':{'left':.5,'right':.98,'top':.48,'bottom':.82},'confidence':.96},
                    'visible':{'noul':1},'navigation':{'noul':1}}}
    a.client=Ground()
    action=asyncio.run(a._next_actions())[0]
    assert (action['action'],action['x'],action['y'])==('tap',740,650)
    assert calls[0][0]['policy']=='content_navigation'
    assert calls[0][2]==a.screen.frame and 'candidate_action' not in calls[0][0]
    assert len(calls)==1


@pytest.mark.parametrize('visible,navigation,confidence',[(0,1,.99),(1,0,.99),(1,1,.5)])
def test_uncertain_content_identity_or_engagement_target_never_taps(monkeypatch,visible,navigation,confidence):
    a=make_agent(monkeypatch,'DO: tap 372 690\nTARGET: requested post')
    a.task='Open the post about zero environment speaking practice'
    class Ground:
        async def evaluate(self,*args):
            return {'seconds':.1,'answers':{'target':{'box':{'left':.5,'right':.98,'top':.48,'bottom':.82},'confidence':confidence},
                    'visible':{'noul':visible},'navigation':{'noul':navigation}}}
    a.client=Ground()
    with pytest.raises(DecisionUnavailable):asyncio.run(a._next_actions())


def test_content_completion_requires_detail_not_visible_feed_card(monkeypatch):
    a=make_agent(monkeypatch,'DO: done The card is visible','revise')
    a.task='Open the post about zero environment speaking practice'
    questions=[]
    evaluate=a.client.evaluate
    async def record(state,q,frame):
        questions.append(q['ready']['instructions']);return await evaluate(state,q,frame)
    a.client.evaluate=record
    with pytest.raises(DecisionUnavailable):asyncio.run(a._next_actions())
    assert all('DETAIL' in q and 'NOT completion' in q for q in questions)


def test_content_goal_still_allows_ordinary_search_navigation(monkeypatch):
    a=make_agent(monkeypatch,'DO: tap 249 100\nTARGET: 搜索 search icon')
    a.task='Open the post about zero environment speaking practice'
    action=asyncio.run(a._next_actions())[0]
    assert action['action']=='tap'
    assert a.client.calls[-1]['candidate_action']['point']=='center of supplied crop'


@pytest.mark.parametrize('family', ['iphone', 'ipad'])
@pytest.mark.parametrize('goal', ['scroll down', 'scroll down the feed once', 'Scroll down once on the current Safari page, then stop. Do not tap links or buttons.'])
def test_plain_scroll_is_one_gesture_then_done_without_planner(monkeypatch, goal, family):
    a=make_agent(monkeypatch,'DO: swipe 249 800 249 300');a.task=goal
    a.phone.target_info=lambda:{'device':family}
    action=asyncio.run(a._next_actions())[0]
    assert action['action']=='swipe' and action['workflow']=='bounded_scroll'
    a.steps=[{**action,'act_s':.5}]
    assert asyncio.run(a._next_actions())[0]['action']=='done'
    assert a.planner.calls==0


def test_longer_scroll_search_cannot_run_unbounded(monkeypatch):
    a=make_agent(monkeypatch,'DO: swipe 249 800 249 300');a.task='Scroll down to find something'
    a.steps=[{'action':'swipe','act_s':.5}]*3
    assert asyncio.run(a._next_actions())[0]['action']=='fail'
    assert a.planner.calls==0


@pytest.mark.parametrize('action', [{'action':'type','text':'private text'}, {'action':'key','combo':'enter'}, {'action':'tap','x':500,'y':500}])
def test_rotation_after_planning_blocks_all_input(monkeypatch, action):
    from unittest.mock import AsyncMock
    from hidagent.agent import BlindError
    a=make_agent(monkeypatch,'DO: done')
    a.phone.type=AsyncMock();a.phone.key=AsyncMock();a.phone.tap=AsyncMock()
    original=a.screen.frame
    image=io.BytesIO();Image.new('RGB',(1080,710)).save(image,format='JPEG');a.screen.frame=image.getvalue()
    with pytest.raises(BlindError,match='geometry changed'):
        asyncio.run(a._do({**action,'_shot':original}))
    a.phone.type.assert_not_awaited();a.phone.key.assert_not_awaited();a.phone.tap.assert_not_awaited()


def test_ipad_scroll_uses_foreground_pane_not_display_center(monkeypatch):
    a=make_agent(monkeypatch,'DO: done');a.task='scroll down the feed once';a.phone.target_info=lambda:{'device':'ipad'}
    action=asyncio.run(a._next_actions())[0]
    assert action['x1']==action['x2']==650
    assert 100<action['y2']<action['y1']<900
    assert a.planner.calls==0


def test_ipad_ambiguous_scroll_pane_never_moves(monkeypatch):
    a=make_agent(monkeypatch,'DO: done',confidence=.89);a.task='scroll down';a.phone.target_info=lambda:{'device':'ipad'}
    with pytest.raises(DecisionUnavailable):asyncio.run(a._next_actions())


def test_test_note_cleanup_search_is_not_judged_as_body_edit(monkeypatch):
    a=make_agent(monkeypatch,'DO: type ESP32QA-20261026-IPAD-0055')
    a.task='In Apple Notes, move only ESP32QA-20261026-IPAD-0055 to Recently Deleted'
    questions=[];evaluate=a.client.evaluate
    async def record(state,q,image):
        questions.append(q['ready']['instructions']);return await evaluate(state,q,image)
    a.client.evaluate=record
    assert asyncio.run(a._next_actions())[0]['action']=='type'
    assert 'built-in SEARCH FIELD' in questions[0]


def test_test_note_cleanup_never_types_other_body_text(monkeypatch):
    a=make_agent(monkeypatch,'DO: type changed body')
    a.task='In Apple Notes, delete only ESP32QA-20261026-IPAD-0055'
    with pytest.raises(DecisionUnavailable,match='exact lookup tag'):asyncio.run(a._next_actions())
    assert not a.client.calls


def test_test_note_cleanup_tap_keeps_full_screen_identity(monkeypatch):
    a=make_agent(monkeypatch,'DO: tap 249 540\nTARGET: Delete Note')
    a.task='In Apple Notes, delete only ESP32QA-20261026-IPAD-0055'
    asyncio.run(a._next_actions())
    state=a.client.calls[-1]
    assert state['policy']=='notes_test_cleanup'
    assert state['candidate_action']['x']==.5 and state['candidate_action']['coordinate_space']=='full-image fractions 0..1'
    assert 'FULL current screenshot' in state['image_view']


def test_test_note_cleanup_does_not_relax_full_frame_threshold(monkeypatch):
    a=make_agent(monkeypatch,'DO: tap 249 540\nTARGET: Delete Note',confidence=.95)
    a.task='In Apple Notes, delete only ESP32QA-20261026-IPAD-0055'
    with pytest.raises(DecisionUnavailable):asyncio.run(a._next_actions())
    assert len(a.client.calls)==2
    assert all(x['policy']=='notes_test_cleanup' and 'FULL current screenshot' in x['image_view'] for x in a.client.calls)


@pytest.mark.parametrize('path_confidence,allowed', [(1,True),(.98,False),(0,False)])
def test_uncertain_outer_ipad_pane_needs_strict_independent_inner_path(monkeypatch,path_confidence,allowed):
    a=make_agent(monkeypatch,'DO: done');a.task='scroll down';a.phone.target_info=lambda:{'device':'ipad'}
    calls=[]
    async def evaluate(state,questions,image):
        calls.append(state)
        if state['policy']=='ipad_scroll':
            return {'seconds':.1,'approved':False,'answers':{'ready':{'noul':1},'surface':{'confidence':.85,'box':{'left':0,'right':1,'top':.18,'bottom':1}}}}
        assert state['policy']=='ipad_scroll_path'
        assert all(.18<point[1]<1 for point in state['path'])
        return {'seconds':.1,'answers':{'ready':{'noul':path_confidence}}}
    a.client.evaluate=evaluate
    if allowed:assert asyncio.run(a._next_actions())[0]['action']=='swipe'
    else:
        with pytest.raises(DecisionUnavailable,match='independently verified'):asyncio.run(a._next_actions())
    assert len(calls)==2


@pytest.mark.parametrize('family,expected',[('ipad',True),('iphone',False)])
def test_ipad_semantic_taps_verify_real_pointer_before_press(monkeypatch,family,expected):
    from unittest.mock import AsyncMock
    a=make_agent(monkeypatch,'DO: tap 500 500')
    a.phone.target_info=lambda:{'device':family};a.phone.tap=AsyncMock()
    asyncio.run(a._do({'action':'tap','x':500,'y':500,'_shot':a.screen.frame}))
    a.phone.tap.assert_awaited_once_with(.5,.5,aim=expected)


def test_content_author_state_is_cleared_for_the_next_normal_task(monkeypatch):
    from unittest.mock import AsyncMock
    a=make_agent(monkeypatch,'DO: done')
    monkeypatch.setenv('SOURCE_NOTE_WORKFLOWS','1')
    monkeypatch.setattr('hidagent.source_note.compile_source_note',AsyncMock(return_value={'author_model':'meta-writer','writing_s':1.2}))
    a.task='From the current page, create a new Notes note titled "ESP32QA-TEST-STATE" with a summary.'
    asyncio.run(a._replan())
    assert a._source_workflow and 'meta-writer' in a.plan
    a.task='Open Calculator';asyncio.run(a._replan())
    assert not a._source_workflow and a._source_note is None


def test_clear_removes_previous_source_dispatch_and_model_labels(monkeypatch):
    a=make_agent(monkeypatch,'DO: done')
    a._source_workflow=a._source_math=a._source_ocr=True
    a.inventory_writer=SimpleNamespace(model='llama-test')
    a._source_note={'title':'ESP32QA-old','body':'Old generated inventory text'}
    a._demo_draft={'body':'Old generated copy'}
    a._calculator_workflow=True
    assert a.content_author_model=='llama-test'
    a.clear()
    assert a.content_author_model is None
    assert not a._source_workflow and not a._source_math and not a._source_ocr
    assert a._source_note is None and a._demo_draft is None
    assert not a._calculator_workflow and a.task=='' and a.steps==[]


def test_clear_while_running_preserves_active_source_task(monkeypatch):
    a=make_agent(monkeypatch,'DO: done')
    a._source_workflow=True;a._source_note={'title':'ESP32QA-active'}
    a._job=SimpleNamespace(done=lambda:False)
    with pytest.raises(RuntimeError):a.clear()
    assert a._source_workflow and a._source_note['title']=='ESP32QA-active'


def test_calculator_route_uses_bounded_observation_not_eight_second_noop_wait(monkeypatch):
    from unittest.mock import AsyncMock
    a=make_agent(monkeypatch,'DO: done')
    a.task='Open Calculator and calculate 18 times 23. Verify the displayed answer and leave it visible.'
    a._calculator_workflow=True
    a.steps=[{'action':'key','combo':'esc','workflow':'keyboard'}]
    observe=AsyncMock()
    monkeypatch.setattr('hidagent.jev_agent.JevAgent._observe',observe)
    asyncio.run(a._observe(a.screen.frame,()))
    observe.assert_awaited_once_with(a,a.screen.frame,())


def test_ipad_back_requires_replanning_to_visible_window_control(monkeypatch):
 a=make_agent(monkeypatch,'DO: back');a.task='Go back'
 a.phone.target_info=lambda:{'device':'ipad'}
 with pytest.raises(DecisionUnavailable):asyncio.run(a._next_actions())
 assert a.planner.calls==2 and not a.client.calls
 assert 'active app window' in a._feedback


def test_iphone_back_proposal_keeps_existing_behavior(monkeypatch):
 a=make_agent(monkeypatch,'DO: back');a.task='Go back'
 assert asyncio.run(a._next_actions())[0]['action']=='back'


def test_source_author_reports_actual_visual_fallback_not_initial_ocr_writer(monkeypatch):
    a=make_agent(monkeypatch,'DO: done')
    a._source_workflow=a._source_ocr=True
    a.inventory_writer=SimpleNamespace(model='initial-text-model')
    a._source_note={'author_model':'actual-visual-model'}
    assert a.content_author_model=='actual-visual-model'
    a.clear()
    assert a.content_author_model is None
