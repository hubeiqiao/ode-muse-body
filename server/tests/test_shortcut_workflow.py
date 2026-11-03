import asyncio
import io
from PIL import Image
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from hidagent.shortcut_workflow import shortcut_goal, next_shortcut_actions
from hidagent.decision import DecisionUnavailable


def agent(monkeypatch, replies, phases=()):
    monkeypatch.setattr('hidagent.shortcut_workflow.scene_changed', lambda *args: False)
    class Judge:
        async def evaluate(self, state, questions, frame):
            answer = replies.pop(0)
            return {'answers': answer, 'seconds': .1}
    image=io.BytesIO();Image.new('RGB',(498,1080)).save(image,format='JPEG')
    return SimpleNamespace(screen=SimpleNamespace(frame=image.getvalue()), client=Judge(),
        steps=[{'workflow':'keyboard','phase':p} for p in phases],
        trace=SimpleNamespace(write=lambda *args,**kw:None))


def page(name):return {'page':{'choice':name,'confidence':.99}}
def ready(value):return {'ready':{'noul':value}}
GOAL={'app':'Notes','text':'ESP32QA test 73'}


@pytest.mark.parametrize('task', ['Open Notes and share it','Open Notes and delete all notes',
    'Open Notes and create a new note with text "hello" then send it',
    'Open Notes and create a new note with text "hi\nthere"', 'Open X and send a tweet',
    'Open Notes and create a new note with text "你好"'])
def test_unbounded_or_non_ascii_goal_does_not_enter_keyboard_route(task):
    assert shortcut_goal(task) is None


def test_case_insensitive_open_and_literal_parsing():
    assert shortcut_goal('Open Notes and write \"Hello\"') == {'app':'Notes','text':'Hello'}
    assert shortcut_goal('open joe speaking') == {'app':'Joe Speaking','text':None}
    assert shortcut_goal('Open Notes and create a new note with text "Hello 73"') == {'app':'Notes','text':'Hello 73'}


def test_open_only_requires_actual_app_content(monkeypatch):
    a=agent(monkeypatch,[page('home')])
    assert asyncio.run(next_shortcut_actions(a,{'app':'Notes','text':None}))[0]['combo']=='cmd+space'
    a=agent(monkeypatch,[page('app')])
    assert asyncio.run(next_shortcut_actions(a,{'app':'Notes','text':None}))[0]['action']=='done'


def test_populated_editor_never_receives_test_text(monkeypatch):
    a=agent(monkeypatch,[page('app')])
    act=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert act['combo']=='cmd+n'
    a=agent(monkeypatch,[ready(0)],['new_note'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['action']=='wait'


def test_blank_editor_must_be_confirmed_then_type_once(monkeypatch):
    a=agent(monkeypatch,[ready(1)],['new_note'])
    act=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert (act['action'],act['text'],act['phase'])==('type',GOAL['text'],'payload')
    a=agent(monkeypatch,[ready(0)],['new_note','payload'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['action']=='wait'
    a=agent(monkeypatch,[ready(1)],['new_note','payload'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['action']=='done'


def test_no_automatic_retry_when_exact_input_cannot_be_verified(monkeypatch):
    a=agent(monkeypatch,[ready(0)],['payload']+['verify_payload']*3)
    with pytest.raises(DecisionUnavailable,match='without repeating input'):
        asyncio.run(next_shortcut_actions(a,GOAL))


def test_modal_or_locked_screen_sends_nothing(monkeypatch):
    a=agent(monkeypatch,[page('blocked'),page('unknown')])
    with pytest.raises(DecisionUnavailable,match='blocked'):
        asyncio.run(next_shortcut_actions(a,GOAL))


def test_search_selection_and_replace_share_verified_focus(monkeypatch):
    a=agent(monkeypatch,[page('search'),ready(1)],['open_search'])
    acts=asyncio.run(next_shortcut_actions(a,GOAL))
    assert [(x['action'],x.get('combo'),x.get('text')) for x in acts]==[('key','cmd+a',None),('type',None,'Notes')]
    a=agent(monkeypatch,[page('search'),ready(0)],['open_search'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['action']=='wait'


def test_return_retry_requires_fresh_matching_spotlight_and_is_bounded(monkeypatch):
    a=agent(monkeypatch,[page('search'),ready(0)],['query'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['action']=='wait'
    a=agent(monkeypatch,[page('search'),ready(1)],['query'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['combo']=='enter'
    a=agent(monkeypatch,[page('search'),ready(0)],['query','launch'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['action']=='wait'
    a=agent(monkeypatch,[page('search'),ready(1)],['query','launch'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['combo']=='enter'
    a=agent(monkeypatch,[page('search')],['query','launch','launch'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['action']=='wait'


def test_changed_scene_discards_input(monkeypatch):
    a=agent(monkeypatch,[ready(1)],['new_note'])
    monkeypatch.setattr('hidagent.shortcut_workflow.scene_changed',lambda *args,**kwargs:True)
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['action']=='wait'


def test_hybrid_shortcut_does_not_call_planner(monkeypatch):
    from hidagent.hybrid_agent import HybridAgent
    from hidagent.debug import Trace
    a=agent(monkeypatch,[page('app')])
    h=HybridAgent(SimpleNamespace(target_info=lambda:{'device':'iphone'}),SimpleNamespace(live=True,frame=a.screen.frame),a.client,
                  SimpleNamespace(model="haiku-test",chat=AsyncMock()),Trace(None))
    h.task='Open Notes';h.keyboard_workflows=True
    assert asyncio.run(h._next_actions())[0]['action']=='done'
    h.planner.chat.assert_not_awaited()


def test_uncertain_transition_waits_without_acting_or_lowering_threshold(monkeypatch):
    a=agent(monkeypatch,[{'page':{'choice':'other','confidence':.6}}])
    act=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert act['action']=='wait' and act['phase']=='classify_scene'
    a=agent(monkeypatch,[{'page':{'choice':'other','confidence':.6}}],['classify_scene']*3)
    with pytest.raises(DecisionUnavailable):asyncio.run(next_shortcut_actions(a,GOAL))


@pytest.mark.parametrize('size', [(710,1080),(1646,1080)])
def test_ipad_uses_checked_keyboard_route_in_both_orientations(monkeypatch, size):
    from hidagent.hybrid_agent import HybridAgent
    from hidagent.debug import Trace
    a=agent(monkeypatch,[page('other')])
    image=io.BytesIO();Image.new('RGB',size).save(image,format='JPEG')
    planner=SimpleNamespace(model='haiku-test',chat=AsyncMock())
    h=HybridAgent(SimpleNamespace(target_info=lambda:{'device':'ipad'}),
                  SimpleNamespace(live=True,frame=image.getvalue()),a.client,planner,Trace(None))
    h.task='Open Notes';h.keyboard_workflows=True
    assert asyncio.run(h._next_actions())[0]['combo']=='cmd+h'
    planner.chat.assert_not_awaited()


def test_new_app_command_normalizes_existing_search_once_without_typing(monkeypatch):
    a=agent(monkeypatch,[page('search')])
    acts=asyncio.run(next_shortcut_actions(a,GOAL))
    assert [x.get('combo') for x in acts]==['cmd+h','esc']
    assert all(x['action']=='key' for x in acts)
    a=agent(monkeypatch,[page('search'),ready(0)],['reset_search'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['action']=='wait'


def test_x_is_an_explicit_bounded_app_launch():
    assert shortcut_goal("Open X")=={"app":"X","text":None}


@pytest.mark.parametrize('goal_text', ['你打开一下小红书。','请打开小红书','打开小紅書','open XiaoHongShu','Open xiao hong shu','Open rednote app'])
def test_rednote_chinese_and_romanized_aliases_use_same_ascii_query(goal_text):
    assert shortcut_goal(goal_text)=={'app':'rednote','text':None}


@pytest.mark.parametrize('goal_text,app', [('打开计算器','Calculator'),('帮我打开一下设置','Settings'),('启动备忘录','Notes')])
def test_chinese_app_navigation_uses_canonical_name(goal_text,app):
    assert shortcut_goal(goal_text)=={'app':app,'text':None}


@pytest.mark.parametrize('goal_text', ['打开小红书然后发帖','Open XiaoHongShu and post hello','打开小红书，删除草稿'])
def test_alias_normalization_never_drops_remaining_actions(goal_text):
    assert shortcut_goal(goal_text) is None


def test_bottom_search_focus_uses_full_frame_and_localized_launch_labels(monkeypatch):
    a=agent(monkeypatch,[page('search'),ready(1)],('open_search',))
    captured=[]
    original=a.client.evaluate
    async def judge(state,questions,frame):
        captured.append((str(questions),frame));return await original(state,questions,frame)
    a.client.evaluate=judge
    actions=asyncio.run(next_shortcut_actions(a,{'app':'rednote','text':None}))
    assert actions[-1]['text']=='rednote'
    assert 'BOTTOM' in captured[-1][0] and captured[-1][1]==a.screen.frame
    a=agent(monkeypatch,[page('search'),ready(1)],('open_search','query'))
    original=a.client.evaluate
    async def judge_launch(state,questions,frame):
        captured.append((str(questions),frame));return await original(state,questions,frame)
    a.client.evaluate=judge_launch
    assert asyncio.run(next_shortcut_actions(a,{'app':'rednote','text':None}))[0]['combo']=='enter'
    assert '小红书' in captured[-1][0] and captured[-1][1]==a.screen.frame


def test_rednote_may_deny_incidental_paste_but_never_grant_access(monkeypatch):
    a=agent(monkeypatch,[page('blocked'),{'paste_prompt':{'noul':1},'deny':{
        'confidence':.98,'box':{'left':.5,'right':.9,'top':.5,'bottom':.6}}}])
    action=asyncio.run(next_shortcut_actions(a,{'app':'rednote','text':None}))[0]
    assert action['action']=='tap' and action['phase']=='deny_paste'
    assert (action['x'],action['y'])==(700,550)
    a=agent(monkeypatch,[page('blocked'),{'paste_prompt':{'noul':0},'deny':{
        'confidence':.98,'box':{'left':.5,'right':.9,'top':.5,'bottom':.6}}}])
    with pytest.raises(DecisionUnavailable):asyncio.run(next_shortcut_actions(a,{'app':'rednote','text':None}))


def test_query_animation_never_toggles_search_again(monkeypatch):
    a=agent(monkeypatch,[page('other')],('open_search','query'))
    action=asyncio.run(next_shortcut_actions(a,{'app':'rednote','text':None}))[0]
    assert action['action']=='wait' and 'combo' not in action


def test_fresh_acknowledged_search_avoids_redundant_invisible_caret_check(monkeypatch):
    import time
    a=agent(monkeypatch,[page('search')])
    a.steps=[{'workflow':'keyboard','phase':'open_search','action':'key','combo':'cmd+space','act_s':.2,'t':time.time()}]
    actions=asyncio.run(next_shortcut_actions(a,{'app':'rednote','text':None}))
    assert [x['action'] for x in actions]==['key','type']
    assert actions[-1]['text']=='rednote'


@pytest.mark.parametrize('age,confidence,ack,combo,expected',[
    (.5,.99,True,'cmd+space',True),(4,.99,True,'cmd+space',False),
    (.5,.9,True,'cmd+space',False),(.5,.99,False,'cmd+space',False),
    (.5,.99,True,'cmd+h',False),(-1,.99,True,'cmd+space',False)])
def test_search_transition_shortcut_is_scoped(age,confidence,ack,combo,expected):
    from hidagent.shortcut_workflow import recent_keyboard_search
    step={'action':'key','phase':'open_search','combo':combo,'t':100-age}
    if ack:step['act_s']=.2
    assert recent_keyboard_search([step],{'choice':'search','confidence':confidence},now=100) is expected


def test_visible_ipad_notes_pane_must_be_activated_before_command_n(monkeypatch):
    a=agent(monkeypatch,[page('app'),ready(0),ready(1),ready(0),page('ordinary')]);a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['combo']=='cmd+space'
    compose={'ready':{'noul':1},'target':{'confidence':.99,'box':{'left':.9,'right':.98,'top':.9,'bottom':.98}}}
    a=agent(monkeypatch,[page('app'),ready(1),compose],['query','launch']);a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['action']=='tap' and action['phase']=='new_note' and action['precise'] is True


def test_ipad_delayed_search_does_not_toggle_it_closed(monkeypatch):
    a=agent(monkeypatch,[page('app'),ready(0),ready(0)],['open_search']);a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['action']=='wait' and action['phase']=='search_open'


def onboarding_button(ready_value=1):
    return {'ready':{'noul':ready_value},'target':{'box':{'left':.25,'right':.75,'top':.68,'bottom':.74},'confidence':.99}}


def test_only_known_notes_feature_tour_is_dismissed(monkeypatch):
    a=agent(monkeypatch,[page('notes_onboarding'),onboarding_button()])
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['action']=='tap' and action['phase']=='dismiss_notes_onboarding'
    assert action['x']==500 and action['y']==710


def test_permission_or_terms_dialog_is_not_dismissed_as_onboarding(monkeypatch):
    a=agent(monkeypatch,[page('notes_onboarding'),onboarding_button(0)])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['action']=='wait'


def test_intro_after_ignored_command_n_never_receives_payload(monkeypatch):
    a=agent(monkeypatch,[{'ready':{'noul':0},'onboarding':{'noul':1}},onboarding_button()],['new_note'])
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['action']=='tap' and action['phase']=='dismiss_notes_onboarding'


def test_after_intro_create_note_once_only_if_notes_is_clear(monkeypatch):
    a=agent(monkeypatch,[ready(0),ready(1)],['new_note','dismiss_notes_onboarding'])
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['combo']=='cmd+n' and action['phase']=='new_note_after_onboarding'
    a=agent(monkeypatch,[ready(0)],['new_note','dismiss_notes_onboarding','new_note_after_onboarding'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['action']=='wait'


def test_conflicting_blank_and_onboarding_evidence_never_types(monkeypatch):
    a=agent(monkeypatch,[{'ready':{'noul':1},'onboarding':{'noul':1}},onboarding_button(0)],['new_note'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['action']=='wait'


def test_search_over_same_app_background_is_launched_before_new_note(monkeypatch):
    a=agent(monkeypatch,[page('app'),ready(1),ready(1)],['open_search','query'])
    a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['combo']=='enter' and action['phase']=='launch'


def test_open_only_never_finishes_on_same_app_search_overlay(monkeypatch):
    a=agent(monkeypatch,[page('app'),ready(1),ready(1)],['open_search','query'])
    action=asyncio.run(next_shortcut_actions(a,{'app':'Notes','text':None}))[0]
    assert action['action']=='key' and action['combo']=='enter'


def test_ipad_background_app_never_counts_as_ready(monkeypatch):
    a=agent(monkeypatch,[page('app'),ready(0),ready(0)]);a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    action=asyncio.run(next_shortcut_actions(a,{'app':'Safari','text':None}))[0]
    assert action['action']=='key' and action['combo']=='cmd+h'


def test_app_completion_is_discarded_if_scene_changed(monkeypatch):
    a=agent(monkeypatch,[page('app')]);monkeypatch.setattr('hidagent.shortcut_workflow.scene_changed',lambda *a:True)
    assert asyncio.run(next_shortcut_actions(a,{'app':'Safari','text':None}))[0]['action']=='wait'


def test_ipad_safari_hidden_toolbar_revealed_once_without_typing(monkeypatch):
    a=agent(monkeypatch,[{'page':{'choice':'app','confidence':.8}}],['launch'])
    a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    actions=asyncio.run(next_shortcut_actions(a,{'app':'Safari','text':None}))
    assert [v.get('combo') for v in actions]==['cmd+l','esc']
    a=agent(monkeypatch,[{'page':{'choice':'app','confidence':.8}}],['launch','reveal_safari_toolbar'])
    a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    assert asyncio.run(next_shortcut_actions(a,{'app':'Safari','text':None}))[0]['action']=='wait'


def test_verified_ipad_notes_caret_can_create_new_note_without_reopening(monkeypatch):
    a=agent(monkeypatch,[page('app'),ready(0),ready(1),ready(1)])
    a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['combo']=='cmd+n' and action['phase']=='new_note'


def deleted_target(confidence=1,ready_value=1):
    return {'ready':{'noul':ready_value},'target':{'confidence':confidence,'box':{'left':.3,'right':.48,'top':.53,'bottom':.6}}}


def test_deleted_note_notice_cancels_precisely_without_recovering(monkeypatch):
    a=agent(monkeypatch,[page('blocked'),page('notice'),deleted_target()])
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['action']=='tap' and action['precise'] and action['phase']=='dismiss_deleted_notice'
    assert 'text' not in action


def test_deleted_folder_navigation_is_bounded_then_new_note_is_created(monkeypatch):
    a=agent(monkeypatch,[page('deleted'),deleted_target()],['dismiss_deleted_notice'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['phase']=='leave_deleted_folder'
    a=agent(monkeypatch,[page('ordinary')],['dismiss_deleted_notice','leave_deleted_folder'])
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['combo']=='cmd+n' and action['phase']=='new_note_after_deleted'
    a=agent(monkeypatch,[page('deleted')],['leave_deleted_folder']*2)
    with pytest.raises(DecisionUnavailable,match='safely'):asyncio.run(next_shortcut_actions(a,GOAL))


@pytest.mark.parametrize('confidence,ready_value',[(.8,1),(1,.98)])
def test_deleted_navigation_does_not_lower_target_or_state_confidence(monkeypatch,confidence,ready_value):
    a=agent(monkeypatch,[page('blocked'),page('notice'),deleted_target(confidence,ready_value)])
    with pytest.raises(DecisionUnavailable,match='not verified'):asyncio.run(next_shortcut_actions(a,GOAL))


def test_deleted_view_contradicts_blank_editor_even_when_ready_is_positive(monkeypatch):
    a=agent(monkeypatch,[{'ready':{'noul':1},'deleted_view':{'noul':1}},page('deleted'),deleted_target()],['new_note'])
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['action']=='tap' and action['phase']=='leave_deleted_folder'


def test_new_editor_after_deleted_recovery_still_requires_blank_proof(monkeypatch):
    a=agent(monkeypatch,[ready(0)],['new_note_after_deleted'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['action']=='wait'
    a=agent(monkeypatch,[ready(1)],['new_note_after_deleted'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['text']==GOAL['text']


def test_compact_ipad_spotlight_wins_over_unblurred_notes_background(monkeypatch):
    a=agent(monkeypatch,[page('app'),ready(1),ready(1)],['open_search'])
    a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['phase']=='select_query' and action['combo']=='cmd+a'


def test_deleted_readonly_notes_navigates_without_reopening_spotlight(monkeypatch):
    a=agent(monkeypatch,[page('app'),ready(0),ready(1),ready(0),page('deleted'),deleted_target()])
    a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['phase']=='leave_deleted_folder' and action['action']=='tap'


def test_tiny_sidebar_uses_magnified_geometry_without_lowering_threshold(monkeypatch):
    small={'ready':{'noul':1},'target':{'confidence':.85,'box':{'left':.028,'right':.062,'top':.028,'bottom':.057}}}
    enlarged={'ready':{'noul':1},'target':{'confidence':.99,'box':{'left':.05,'right':.15,'top':.1,'bottom':.3}}}
    a=agent(monkeypatch,[page('blocked'),page('deleted'),small,enlarged])
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['phase']=='leave_deleted_folder' and action['precise']
    assert abs(action['x']-40)<=1 and abs(action['y']-44)<=1


def test_drawer_uncertain_label_still_requires_strict_current_navigation(monkeypatch):
    uncertain={'page':{'choice':'deleted','confidence':.88}}
    a=agent(monkeypatch,[uncertain,deleted_target()],['leave_deleted_folder'])
    assert asyncio.run(next_shortcut_actions(a,GOAL))[0]['phase']=='leave_deleted_folder'
    a=agent(monkeypatch,[uncertain,deleted_target(ready_value=.98)],['leave_deleted_folder'])
    with pytest.raises(DecisionUnavailable,match='not verified'):asyncio.run(next_shortcut_actions(a,GOAL))


def control_center_ready(confidence=1,blocked=0):
    return {'ready':{'noul':confidence},'blocked':{'noul':blocked},'target':{'confidence':.95,'box':{'left':0,'right':.08,'top':.1,'bottom':.9}}}


def test_confirmed_control_center_dismisses_once_without_touching_settings(monkeypatch):
    a=agent(monkeypatch,[page('control_center'),control_center_ready()]);a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    action=asyncio.run(next_shortcut_actions(a,{'app':'Calculator','text':None}))[0]
    assert (action['action'],action['x'],action['y'],action['phase'])==('tap',40,500,'dismiss_control_center')
    a=agent(monkeypatch,[page('control_center')],['dismiss_control_center']);a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    assert asyncio.run(next_shortcut_actions(a,{'app':'Calculator','text':None}))[0]['action']=='wait'
    a=agent(monkeypatch,[page('control_center')],['dismiss_control_center']+['control_center_closed']*3);a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    with pytest.raises(DecisionUnavailable):asyncio.run(next_shortcut_actions(a,{'app':'Calculator','text':None}))


@pytest.mark.parametrize('confidence,blocked',[(0,0),(.98,0),(1,.02)])
def test_control_center_ambiguity_never_clicks(monkeypatch,confidence,blocked):
    a=agent(monkeypatch,[page('control_center'),control_center_ready(confidence,blocked)]);a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    with pytest.raises(DecisionUnavailable,match='could not be verified'):
        asyncio.run(next_shortcut_actions(a,{'app':'Calculator','text':None}))


def test_control_center_recovery_does_not_change_iphone_behavior(monkeypatch):
    a=agent(monkeypatch,[page('control_center')]);a.phone=SimpleNamespace(target_info=lambda:{'device':'iphone'})
    with pytest.raises(DecisionUnavailable,match='blocked'):
        asyncio.run(next_shortcut_actions(a,{'app':'Calculator','text':None}))


def test_ipad_blank_spotlight_focus_can_be_revealed_once_without_typing(monkeypatch):
 a=agent(monkeypatch,[page('search'),ready(0)],('open_search',))
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 actions=asyncio.run(next_shortcut_actions(a,{'app':'Settings','text':None}))
 assert len(actions)==1 and actions[0]['combo']=='right' and actions[0]['phase']=='reveal_search_caret'
 assert all(x['action']!='type' for x in actions)
 a.steps.append({**actions[0],'act_s':.1})
 a.client=agent(monkeypatch,[page('search'),ready(0)]).client
 assert asyncio.run(next_shortcut_actions(a,{'app':'Settings','text':None}))[0]['action']=='wait'
 a.client=agent(monkeypatch,[page('search'),ready(1)]).client
 actions=asyncio.run(next_shortcut_actions(a,{'app':'Settings','text':None}))
 assert [x['action'] for x in actions]==['key','type']


def test_focus_pulse_never_changes_iphone_behavior(monkeypatch):
 a=agent(monkeypatch,[page('search'),ready(0)],('open_search',))
 a.phone=SimpleNamespace(target_info=lambda:{'device':'iphone'})
 assert asyncio.run(next_shortcut_actions(a,{'app':'Settings','text':None}))[0]['action']=='wait'


@pytest.mark.parametrize('confidence,readiness',[(.89,1),(.99,.98)])
def test_ipad_launch_receipt_never_authorizes_unfocused_command_n(monkeypatch,confidence,readiness):
    compose={'ready':{'noul':readiness},'target':{'confidence':confidence,'box':{'left':.9,'right':.98,'top':.9,'bottom':.98}}}
    a=agent(monkeypatch,[page('app'),ready(1),compose],['query','launch'])
    a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['action']=='wait' and action['phase']=='notes_compose_visible'
    assert 'combo' not in action and 'text' not in action


def test_ipad_compose_tap_still_requires_fresh_empty_editor_before_payload(monkeypatch):
    a=agent(monkeypatch,[ready(0)],['query','launch','new_note'])
    a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
    action=asyncio.run(next_shortcut_actions(a,GOAL))[0]
    assert action['action']=='wait' and 'text' not in action and 'combo' not in action


@pytest.mark.parametrize('text,app',[('Open Maps','Maps'),('Open Apple Maps','Maps'),('打开地图','Maps'),('打开蘋果地圖','Maps'),('Open Weather','Weather'),('打开天气','Weather'),('打开天氣','Weather')])
def test_maps_weather_use_bounded_checked_app_open_route(text,app):
    assert shortcut_goal(text)=={'app':app,'text':None}

@pytest.mark.parametrize('text',['Open Maps and share my location','Open Weather and add a city','打开地图然后发送位置','Open Maps and type Home'])
def test_maps_weather_routes_never_swallow_followup_actions(text):
    assert shortcut_goal(text) is None


def test_ipad_return_fallback_taps_only_one_grounded_installed_app(monkeypatch):
 box={'ready':{'noul':1},'target':{'confidence':.95,'box':{'left':.1,'right':.3,'top':.1,'bottom':.2}}}
 a=agent(monkeypatch,[page('search'),box],['query','launch','launch'])
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 out=asyncio.run(next_shortcut_actions(a,{'app':'Bilibili','text':None}))[0]
 assert out['action']=='tap' and out['phase']=='launch_tap'
 a=agent(monkeypatch,[page('search')],['query','launch','launch','launch_tap'])
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 assert asyncio.run(next_shortcut_actions(a,{'app':'Bilibili','text':None}))[0]['action']=='wait'

def test_splash_screen_never_counts_as_usable_app(monkeypatch):
 a=agent(monkeypatch,[page('loading')])
 out=asyncio.run(next_shortcut_actions(a,{'app':'TikTok','text':None}))[0]
 assert out['action']=='wait' and out['phase']=='app_loading'


def test_direct_icon_launch_never_falls_back_to_typing_another_query(monkeypatch):
 a=agent(monkeypatch,[{'ready':{'noul':0},'blocked':{'noul':0},'loading':{'noul':1}}],['icon_launch'])
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 out=asyncio.run(next_shortcut_actions(a,{'app':'rednote','text':None}))[0]
 assert out['action']=='wait' and out['phase']=='icon_app_ready'
 a=agent(monkeypatch,[{'ready':{'noul':1},'blocked':{'noul':0},'loading':{'noul':0}}],['icon_launch'])
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 assert asyncio.run(next_shortcut_actions(a,{'app':'rednote','text':None}))[0]['action']=='done'


def test_video_motion_cannot_block_readonly_verified_icon_completion(monkeypatch):
 a=agent(monkeypatch,[{'ready':{'noul':1},'blocked':{'noul':0},'loading':{'noul':0}}],['icon_launch'])
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 monkeypatch.setattr('hidagent.shortcut_workflow.scene_changed',lambda *args,**kwargs:True)
 out=asyncio.run(next_shortcut_actions(a,{'app':'Douyin','text':None}))[0]
 assert out['action']=='done' and out['_verified_icon_entry']


def test_icon_permission_is_denied_without_allow_or_retyping(monkeypatch):
 blocked={'ready':{'noul':0},'blocked':{'noul':1},'loading':{'noul':0}}
 denial={'ready':{'noul':1},'target':{'confidence':.95,'box':{'left':.3,'right':.5,'top':.7,'bottom':.8}}}
 a=agent(monkeypatch,[blocked,{'ready':{'noul':1}},denial],['icon_launch'])
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 original=a.client.evaluate
 async def evaluate(*args):
  result=await original(*args);result['approved']=bool(result['answers'].get('ready',{}).get('noul',0));return result
 a.client.evaluate=evaluate
 monkeypatch.setattr('hidagent.shortcut_workflow.control_region_matches',lambda *args,**kwargs:True)
 out=asyncio.run(next_shortcut_actions(a,{'app':'Douyin','text':None}))[0]
 assert out['action']=='tap' and out['phase']=='deny_local_network' and out['_local_network_denial']
 assert out['x']==400 and out['y']==750


def test_absent_network_sheet_never_requests_a_nonexistent_button(monkeypatch):
 blocked={'ready':{'noul':0},'blocked':{'noul':1},'loading':{'noul':0}}
 a=agent(monkeypatch,[blocked,{'ready':{'noul':0}},{'ready':{'noul':0}}],['icon_launch'])
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 original=a.client.evaluate;a.client.evaluate=AsyncMock(side_effect=original)
 out=asyncio.run(next_shortcut_actions(a,{'app':'rednote','text':None}))[0]
 assert out['action']=='key' and out['combo']=='esc' and out['phase']=='icon_cancel_overlay'
 assert a.client.evaluate.await_count==3
 assert set(a.client.evaluate.call_args.args[1])=={'ready'}
 a=agent(monkeypatch,[blocked,{'ready':{'noul':0}},{'ready':{'noul':0}}],['icon_launch','icon_cancel_overlay'])
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 out=asyncio.run(next_shortcut_actions(a,{'app':'rednote','text':None}))[0]
 assert out['action']=='wait' and out['phase']=='icon_app_ready'


def test_native_tracking_sheet_only_refuses_tracking_once(monkeypatch):
 blocked={'ready':{'noul':0},'blocked':{'noul':1},'loading':{'noul':0}}
 refusal={'ready':{'noul':1},'target':{'confidence':.95,'box':{'left':.3,'right':.7,'top':.56,'bottom':.61}}}
 a=agent(monkeypatch,[blocked,{'ready':{'noul':0}},{'ready':{'noul':1}},refusal],['icon_launch'])
 a.phone=SimpleNamespace(target_info=lambda:{'device':'ipad'})
 original=a.client.evaluate
 async def evaluate(*args):
  result=await original(*args);result['approved']=bool(result['answers'].get('ready',{}).get('noul',0));return result
 a.client.evaluate=evaluate
 monkeypatch.setattr('hidagent.shortcut_workflow.control_region_matches',lambda *args,**kwargs:True)
 out=asyncio.run(next_shortcut_actions(a,{'app':'Bilibili','text':None}))[0]
 assert out['action']=='tap' and out['phase']=='deny_app_tracking' and out['_tracking_denial']
 assert out['x']==500 and out['y']==585

@pytest.mark.parametrize('approved_target',[True,False])
def test_douyin_optional_intro_close_is_scoped_and_not_a_mode_change(monkeypatch,approved_target):
    monkeypatch.setattr('hidagent.shortcut_workflow.control_region_matches',lambda *args,**kw:True)
    image=io.BytesIO();Image.new('RGB',(710,1080),'gray').save(image,format='PNG');frame=image.getvalue()
    calls=[]
    class Judge:
        async def evaluate(self,state,questions,shot):
            calls.append((state,questions))
            policy=state.get('policy')
            if policy=='app_icon_ready':a={'ready':{'noul':0},'blocked':{'noul':1},'loading':{'noul':0}};ok=False
            elif policy in ('system_local_network_present','system_tracking_present'):a={'ready':{'noul':0}};ok=False
            elif policy=='douyin_intro_present':a={'ready':{'noul':1}};ok=True
            elif policy=='douyin_intro_dismissal':a={'ready':{'noul':1},'target':{'box':{'left':.72,'right':.77,'top':.38,'bottom':.43},'confidence':1 if approved_target else .5}};ok=approved_target
            else:raise AssertionError(policy)
            return {'answers':a,'approved':ok,'seconds':.01}
    a=SimpleNamespace(screen=SimpleNamespace(frame=frame),client=Judge(),phone=SimpleNamespace(target_info=lambda:{'device':'ipad','orientation':'portrait'}),steps=[{'workflow':'keyboard','phase':'icon_launch'}],trace=SimpleNamespace(write=lambda *args,**kw:None))
    result=asyncio.run(next_shortcut_actions(a,{'app':'Douyin','text':None}))[0]
    if approved_target:
        assert result['action']=='tap' and result['phase']=='dismiss_douyin_youth_intro' and result['_intro_notice_dismissal']
        assert 'close' in calls[-1][1]['target']['instructions'].lower()
    else:assert result['action']=='key' and result['combo']=='esc'
    a.steps.append({'workflow':'keyboard','phase':'dismiss_douyin_youth_intro'})
    calls.clear();result=asyncio.run(next_shortcut_actions(a,{'app':'Douyin','text':None}))[0]
    assert result['action']!='tap' and not any(s[0].get('policy','').startswith('douyin_intro') for s in calls)
