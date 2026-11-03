"""Offline safety and persistence contracts for the two bounded voice demos."""
import asyncio
from datetime import datetime, timezone

import pytest

from hidagent.ipad_demo_flows import DemoRequest
from hidagent.voice_task_memory import VoiceTaskMemory
from test_ipad_demo_flows import make, run

APPS = ['X', 'YouTube', 'TikTok', 'Instagram', 'LinkedIn', 'Bilibili', 'Douyin']


def social_fake(tmp_path):
    f, p, v, state, clock = make(tmp_path)
    old = v.check
    async def check(screen, kind, **kw):
        if kind == 'social_landing': return {'status': 'clear'}
        if kind == 'social_profile':
            return {'account': 'Joe Speaking', 'metrics': {'followers': {'value': 112, 'raw': '112', 'exact': True},
                    'posts': {'value': 8, 'raw': '8', 'exact': True}}, 'latest_posts': []}
        if kind == 'grok_saved': return {'text': 'A visible saved answer', 'content_only': True}
        return await old(screen, kind, **kw)
    v.check = check
    return f, p, v, state, clock


def test_social_visits_seven_native_apps_and_persists_previous_day(tmp_path):
    f, p, v, _, _ = social_fake(tmp_path)
    f.wall_clock = lambda: datetime(2026, 10, 30, 18, tzinfo=timezone.utc)
    first = run(f, 'social_check')
    assert first['status'] == 'read' and len(first['apps']) == 7
    assert all('没有更早记录' in item['speech'] for item in first['apps'])
    assert [c.args[0] for c in p.type.await_args_list] == APPS
    assert all(c.args[0] in ('cmd+space', 'cmd+a', 'enter') for c in p.key.await_args_list)
    assert p.tap.await_count == 7
    f.memory = VoiceTaskMemory(f.memory.memory)
    saved = f.memory.social_snapshot('x')
    saved['metrics']['followers'] = {'value':100, 'raw':'100', 'exact':True}
    f.memory.remember_social('x', saved)
    f.wall_clock = lambda: datetime(2026, 10, 31, 18, tzinfo=timezone.utc)
    second = run(f, 'social_check')
    assert '10 月 30 日多 12' in second['apps'][0]['speech']
    assert second['elapsed_s'] >= 0 and second['timeline']
    assert f.memory.social_snapshot('x')['date'] == '2026-10-31'
    assert len([i for i in f.memory._items() if i['id'].startswith('social_x_')]) == 2


@pytest.mark.parametrize('status', ['login', 'security', 'unknown'])
def test_social_skips_login_security_unknown_without_profile_tap(tmp_path, status):
    f, p, v, _, _ = social_fake(tmp_path)
    old = v.check
    async def check(screen, kind, **kw):
        if kind == 'social_landing': return {'status': status}
        return await old(screen, kind, **kw)
    v.check = check
    result = run(f, 'social_check')
    assert all(item['status'] == 'skipped' for item in result['apps'])
    p.tap.assert_not_awaited()
    assert f.memory.social_snapshot('x') is None


def test_social_budget_skips_read_timeout_but_not_uncertain_input(tmp_path):
    f, p, v, _, clock = social_fake(tmp_path)
    old = v.check
    async def check(screen, kind, **kw):
        if kind == 'social_profile': await clock.sleep(3)
        return await old(screen, kind, **kw)
    v.check = check
    result = run(f, 'social_check', per_app_s=2)
    assert len(result['apps']) == 7 and all(i['status'] == 'skipped' for i in result['apps'])
    assert f.memory.social_snapshot('x') is None
    p.tap.side_effect = RuntimeError('Lost ACK')
    result = run(f, 'social_check')
    assert result['status'] == 'uncertain' and result['never_resend']


def test_spotlight_failure_never_toggles_again_or_types_app(tmp_path):
    f, p, v, _, _ = social_fake(tmp_path)
    old = v.check
    async def check(screen, kind, **kw):
        return False if kind == 'spotlight' else await old(screen, kind, **kw)
    v.check = check
    result = run(f, 'social_check')
    assert result['status'] == 'uncertain'
    # At most one extra toggle, and only after a fresh look still showed no Spotlight.
    assert [c.args for c in p.key.await_args_list] == [('cmd+space',), ('cmd+space',)]
    p.type.assert_not_awaited()


def test_spotlight_checks_separate_every_toggle(tmp_path):
    f, p, v, _, _ = social_fake(tmp_path)
    trace = []
    old_key, old_check = p.key.side_effect, v.check
    async def key(combo): trace.append(combo); await old_key(combo)
    async def check(screen, kind, **kw): trace.append(kind); return await old_check(screen, kind, **kw)
    p.key.side_effect, v.check = key, check
    run(f, 'social_check')
    positions = [i for i,x in enumerate(trace) if x == 'cmd+space']
    for left, right in zip(positions, positions[1:]): assert 'spotlight' in trace[left+1:right]


@pytest.mark.parametrize('action', ['post', 'reply', 'send', 'message', 'follow', 'like', 'buy',
    'add_to_cart', 'sign_in', 'settings', 'rednote', 'x_post', 'x_draft', 'grok_read'])
def test_yolo_schema_has_no_unsafe_actions(action):
    with pytest.raises(ValueError): DemoRequest(action='yolo', steps=[{'action': action}])


def test_yolo_default_plan_is_bounded_journaled_and_read_only(tmp_path):
    f, p, _, _, _ = social_fake(tmp_path)
    result = run(f, 'yolo', step_budget=2)
    assert result['status'] == 'completed' and len(result['journal']) == 2
    assert result['plan'] == [{'action': 'check_stats', 'query': ''}, {'action': 'read_grok', 'query': ''}]
    assert len(f.memory.yolo_journal(result['journal_id'])['steps']) == 2
    assert result['speech'] and result['elapsed_s'] >= 0
    assert all(c.args[0] in APPS + ['Grok Bot'] for c in p.type.await_args_list)


def test_yolo_goal_plans_search_lookup_photo_and_never_sends_grok_prompt(tmp_path):
    f, p, _, _, _ = social_fake(tmp_path)
    result = run(f, 'yolo', query='search speaking practice', step_budget=1)
    assert result['plan'][0]['action'] == 'search'
    assert any(c.args[0].startswith('https://www.google.com/search?q=') for c in p.type.await_args_list)
    p.tap.assert_not_awaited()
    result = run(f, 'yolo', steps=[{'action':'lookup', 'query':'travel mug'}, {'action':'take_photo'}, {'action':'read_grok'}])
    assert result['status'] == 'completed' and len(result['journal']) == 3
    assert p.tap.await_count == 1
    assert p.type.await_args_list[-1].args == ('Grok Bot',)


def test_yolo_unsafe_goal_is_draft_only_before_any_hid(tmp_path):
    f, p, _, _, _ = social_fake(tmp_path)
    result = run(f, 'yolo', query='Post a reply on X')
    assert result['status'] == 'awaiting_confirmation' and result['draft']
    assert result['journal_id']
    p.key.assert_not_awaited(); p.type.assert_not_awaited(); p.tap.assert_not_awaited()


def test_yolo_time_budget_stops_before_next_step(tmp_path):
    f, p, v, _, clock = social_fake(tmp_path)
    old = v.check
    async def check(screen, kind, **kw):
        if kind == 'grok_saved': await clock.sleep(4)
        return await old(screen, kind, **kw)
    v.check = check
    # Opening an app now includes a fixed 1 s Home settle, so the budget leaves room for one step.
    result = run(f, 'yolo', budget_s=3, steps=[{'action':'read_grok'}, {'action':'take_photo'}])
    assert result['status'] == 'expired' and len(result['journal']) == 1
    assert result['journal'][0]['status'] == 'expired'
    p.tap.assert_not_awaited()


def test_yolo_uncertain_delivery_stops_no_second_step_or_replay(tmp_path):
    f, p, _, _, _ = social_fake(tmp_path)
    p.key.side_effect = RuntimeError('Lost ACK')
    req = DemoRequest(action='yolo', steps=[{'action':'read_grok'}, {'action':'take_photo'}])
    async def scenario():
        r = await f.run('s', 'once', '自己干', 1, req)
        assert r['status'] == 'uncertain' and len(r['journal']) == 1
        assert await f.run('s', 'once', '自己干', 1, req) == r
    asyncio.run(scenario())
    p.key.assert_awaited_once()


def test_yolo_stop_persists_partial_journal_and_progress(tmp_path):
    f, p, _, _, _ = social_fake(tmp_path)
    async def scenario():
        entered = asyncio.Event()
        async def blocked(combo): entered.set(); await asyncio.Event().wait()
        p.key.side_effect = blocked
        job = asyncio.create_task(f.run('s','one','自己干',1,DemoRequest(action='yolo')))
        await entered.wait()
        assert f.progress['line'] and f.progress['sequence'] > 0
        f.stop('s')
        result = await job
        assert result['status'] == 'stopped'
        saved = f.memory.yolo_journal(result['journal_id'])
        assert saved['status'] == 'stopped' and saved['steps'][0]['status'] == 'uncertain'
        assert not f.busy
    asyncio.run(scenario())


def test_local_social_ocr_parser_requires_own_profile_and_reads_metrics():
    from hidagent.ipad_social_yolo import parse_profile
    text = 'Joe Speaking\n@example_creator\nEdit profile\n112 Followers\n8 Posts\nLatest\nSpeaking practice\n123 Views\n4 Likes\n2 Comments'
    parsed = parse_profile('x', text)
    assert parsed['metrics']['followers']['value'] == 112
    assert parsed['latest_posts'][0]['metrics']['views']['value'] == 123
    assert parse_profile('x', text.replace('Edit profile', 'Follow')) is None
    assert parse_profile('x', text.replace('Joe Speaking', 'Other account')) is None
    assert parse_profile('x', text + '\nLog in') is None
    rounded = parse_profile('x', text.replace('112 Followers', '1.2K Followers'))
    assert rounded['metrics']['followers'] == {'value':1200, 'raw':'1.2K', 'exact':False}
    split = parse_profile('x', text.replace('112 Followers', '112\nFollowers'))
    assert split['metrics']['followers']['value'] == 112
    assert parse_profile('x', text.replace('112 Followers', '112 Followers\n116 Followers'))['metrics'].get('followers') is None


def test_profile_target_is_local_unique_navigation_only(tmp_path):
    from hidagent.ipad_demo_vision import DemoVision
    f, _, _, _, clock = make(tmp_path)
    async def layout(frame):
        return {'text':'X\nHome\nProfile', 'rows':[{'text':'Profile', 'box':dict(left=.02, right=.15, top=.8, bottom=.88)}]}
    vision = DemoVision(ocr=type('OCR', (), {'read_layout': staticmethod(layout)})(), clock=clock, sleep=clock.sleep)
    target = asyncio.run(vision.target(f.screen, 'social_profile_entry', app='x'))
    assert target['x'] < .28 and target['y'] > .7


def test_stop_and_new_demo_intents_match_browser_and_server():
    import json
    import subprocess
    from hidagent.control_intent import is_action_intent, is_stop_intent
    assert is_stop_intent('停') and not is_stop_intent('不要停')
    assert is_action_intent('Joe Speaking 各平台运营情况怎么样')
    assert is_action_intent('你自己去干事情')
    code = "const x=require('./hidagent/static/voice-intents.js');console.log(JSON.stringify([x.isStopIntent('停'),x.isStopIntent('不要停'),x.isActionIntent('Joe Speaking 各平台运营情况怎么样')]));"
    assert json.loads(subprocess.check_output(['node','-e',code],text=True)) == [True, False, True]


def test_voice_long_demo_returns_progress_then_terminal_without_replay(tmp_path):
    from test_ipad_demo_voice import bridge
    from hidagent.live_voice import ToolCall
    b, f, p = bridge(tmp_path)
    old_check = f.vision.check
    async def check(screen, kind, **kw):
        if kind == 'grok_saved': return {'text':'A saved answer'}
        return await old_check(screen, kind, **kw)
    f.vision.check = check
    async def scenario():
        entered, gate = asyncio.Event(), asyncio.Event()
        old = p.key.side_effect
        async def key(combo): entered.set(); await gate.wait(); await old(combo)
        p.key.side_effect = key
        c = ToolCall(session='offline-session-123', id='yolo', name='ipad_demo', args={'action':'yolo', 'steps':[{'action':'read_grok'}]}, utterance='自己干', owner_turn=1)
        result = await asyncio.wait_for(b.call(c), .1)
        assert result['status'] == 'in_progress' and result['progress']['line']
        await entered.wait()
        duplicate = await b.call(c.model_copy(update={'id':'duplicate'}))
        assert duplicate['status'] == 'in_progress' and p.key.await_count == 1
        gate.set()
        task = b.sessions[c.session]['demo_calls'][(1, 'yolo')][1]
        await task
        terminal = await b.execute('phone_status', {}, c.session)
        assert terminal['terminal'] and terminal['status'] == 'completed'
        assert terminal['speech'] and 'elapsed_s' in terminal
        assert not b.start.await_count
    asyncio.run(scenario())


def test_voice_yolo_uses_owner_goal_and_blocks_model_plan_for_unsafe_goal(tmp_path):
    from test_ipad_demo_voice import bridge
    from hidagent.live_voice import ToolCall
    b, f, p = bridge(tmp_path)
    async def scenario():
        c = ToolCall(session='offline-session-123',id='unsafe',name='ipad_demo',args={'action':'yolo','query':'safe search','steps':[{'action':'take_photo'}]},utterance='Post a reply on X',owner_turn=1)
        result = await b.call(c)
        if result['running']:
            await b.sessions[c.session]['demo_calls'][(1, 'yolo')][1]
            result = await b.execute('phone_status', {}, c.session)
        assert result['status'] == 'awaiting_confirmation'
        p.key.assert_not_awaited();p.tap.assert_not_awaited()
    asyncio.run(scenario())


def test_social_unknown_profile_navigation_skips_without_input(tmp_path):
    f, p, v, _, _ = social_fake(tmp_path)
    async def missing(*args, **kwargs): raise RuntimeError('No unique local navigation')
    v.target = missing
    result = run(f, 'social_check')
    assert result['status'] == 'read' and len(result['apps']) == 7
    assert all(r['status'] == 'skipped' for r in result['apps'])
    p.tap.assert_not_awaited()


def test_saved_grok_answer_waits_stable_and_excludes_composer(tmp_path):
    from hidagent.ipad_demo_vision import DemoVision
    f, _, _, _, clock = make(tmp_path)
    async def layout(frame):
        return {'text':'Grok Bot\nA saved public answer\nAsk anything', 'rows':[], 'source':'local_ocr'}
    vision = DemoVision(ocr=type('OCR', (), {'read_layout':staticmethod(layout)})(), clock=clock, sleep=clock.sleep)
    from unittest.mock import AsyncMock
    vision.wait_stable = AsyncMock(return_value=f.screen.frame)
    result = asyncio.run(vision.check(f.screen, 'grok_saved', before=f.screen.frame))
    vision.wait_stable.assert_awaited_once()
    assert 'Ask anything' not in result['text']


def test_social_hung_hid_respects_app_budget_and_stops_uncertain(tmp_path):
    f, p, _, _, _ = social_fake(tmp_path)
    async def hung(*args, **kwargs): await asyncio.Event().wait()
    p.tap.side_effect = hung
    async def scenario():
        # per_app_s leaves room for the fixed 1 s Home settle before the hung tap.
        return await asyncio.wait_for(f.run('s','one','check stats',1,DemoRequest(action='social_check',per_app_s=3)), 4)
    result = asyncio.run(scenario())
    assert result['status'] == 'uncertain' and result['never_resend']
    assert p.key.await_count == 3 and p.tap.await_count == 1  # cmd+space, cmd+a, enter


def test_social_already_on_own_profile_needs_no_navigation_tap(tmp_path):
    f, p, v, _, _ = social_fake(tmp_path)
    old = v.check
    async def check(screen, kind, **kw):
        if kind == 'social_landing':
            profile = await old(screen, 'social_profile')
            return {'status':'clear', 'profile':profile}
        return await old(screen, kind, **kw)
    v.check = check
    result = run(f, 'social_check')
    assert all(r['status'] == 'read' for r in result['apps'])
    p.tap.assert_not_awaited()


def test_yolo_summary_includes_checked_findings(tmp_path):
    f, _, _, _, _ = social_fake(tmp_path)
    result = run(f, 'yolo',steps=[{'action':'read_grok'}])
    assert result['results'][0]['text'] == 'A visible saved answer'
    assert result['summary'][0]['action'] == 'read_grok'
    assert result['summary'][0]['status'] == 'read'


def test_two_visible_post_blocks_and_inline_chinese_counts():
    from hidagent.ipad_social_yolo import parse_profile
    text = 'Joe Speaking\n编辑资料\n1.2万 粉丝 | 8 作品\n作品\nFirst video\n123 播放\n4 赞\n2 评论\n\nSecond video\n400 播放\n5 赞'
    result = parse_profile('douyin', text)
    assert result['metrics']['followers']['value'] == 12000
    assert len(result['latest_posts']) == 2
    assert result['latest_posts'][1]['metrics']['views']['value'] == 400


def test_expired_yolo_does_not_claim_photo_not_delivered(tmp_path):
    f, p, v, _, clock = social_fake(tmp_path)
    old = p.tap.side_effect
    async def slow(*args, **kwargs):
        await old(*args, **kwargs); await clock.sleep(2)
    p.tap.side_effect = slow
    result = run(f, 'yolo',budget_s=1,steps=[{'action':'take_photo'}])
    assert result['status'] == 'uncertain' and result['never_resend']
    assert result['journal'][0]['status'] == 'uncertain'


def test_spotlight_search_word_in_an_app_is_not_open_proof(tmp_path):
    from hidagent.ipad_demo_vision import DemoVision
    f, _, _, _, clock = make(tmp_path)
    # open() always starts from Home, so only Home-screen evidence matters: the Settings
    # sidebar search (far left) is not Spotlight; Spotlight's own result header is.
    data = {'text':'Settings\nSearch', 'rows':[{'text':'Search','box':dict(left=.03, top=.13, right=.1, bottom=.16)}]}
    async def layout(frame): return data
    vision = DemoVision(ocr=type('OCR', (), {'read_layout':staticmethod(layout)})(), clock=clock, sleep=clock.sleep)
    assert asyncio.run(vision.check(f.screen, 'spotlight')) is False
    data.update(text='Top Hit', rows=[{'text':'Top Hit','box':dict(left=.26, top=.21, right=.3, bottom=.23)}])
    vision.cached = None
    assert asyncio.run(vision.check(f.screen, 'spotlight')) is True


def test_memory_same_day_snapshot_replaces_and_rounded_counts_have_no_exact_delta(tmp_path):
    from hidagent.ipad_social_yolo import social_speech
    f, _, _, _, _ = social_fake(tmp_path)
    first = {'date':'2026-10-30','account':'@example_creator','metrics':{'followers':{'value':1200,'raw':'1.2K','exact':False}},'latest_posts':[]}
    f.memory.remember_social('x', first)
    second = {**first, 'metrics':{'followers':{'value':1300,'raw':'1.3K','exact':False}}}
    f.memory.remember_social('x', second)
    assert len(f.memory._demo_records('social_x_')) == 1
    assert '多 100' not in social_speech('x', second, first)
    assert '不计算精确增量' in social_speech('x', second, first)


def test_voice_stop_during_yolo_returns_stopped_journal(tmp_path):
    from test_ipad_demo_voice import bridge
    from hidagent.live_voice import ToolCall, VoiceControl
    b, f, p = bridge(tmp_path)
    async def scenario():
        entered = asyncio.Event()
        async def blocked(combo): entered.set(); await asyncio.Event().wait()
        p.key.side_effect = blocked
        c=ToolCall(session='offline-session-123',id='one',name='ipad_demo',args={'action':'yolo'},utterance='自己干',owner_turn=1)
        assert (await b.call(c))['status'] == 'in_progress'
        await entered.wait()
        await b.interrupt(VoiceControl(session=c.session, text='停'))
        await b.sessions[c.session]['demo_status_task']
        result = await b.execute('phone_status',{},c.session)
        assert result['status'] == 'stopped'
        assert f.memory.yolo_journal(result['journal_id'])['status'] == 'stopped'
        assert p.key.await_count == 1
    asyncio.run(scenario())


def test_yolo_search_returns_real_local_ocr_receipt(tmp_path):
    f, _, v, _, _ = social_fake(tmp_path)
    old = v.check
    async def check(screen, kind, **kw):
        if kind == 'public_search': return {'text':'Google public results', 'source':'tesseract', 'content_only':True}
        return await old(screen, kind, **kw)
    v.check = check
    result = run(f, 'yolo', steps=[{'action':'search','query':'speaking practice'}])
    assert result['status'] == 'completed'
    assert result['results'][0]['status'] == 'read'
    assert result['results'][0]['text'] == 'Google public results'


def test_new_long_demo_never_reports_previous_demo_progress(tmp_path):
    from test_ipad_demo_voice import bridge
    from hidagent.live_voice import ToolCall
    b, f, _ = bridge(tmp_path)
    f.progress = {'sequence':42,'line':'Old unrelated task'}
    f.events = [{'step':'old_task'}]
    async def scenario():
        c=ToolCall(session='offline-session-123',id='one',name='ipad_demo',args={'action':'yolo'},utterance='自己干',owner_turn=1)
        result=await b.call(c)
        try:
            assert result['progress']['line'] != 'Old unrelated task'
            assert result['timeline'] == []
        finally:
            b.sessions[c.session]['demo_status_task'].cancel()
            await asyncio.gather(b.sessions[c.session]['demo_status_task'], return_exceptions=True)
    asyncio.run(scenario())


def test_starting_another_owner_task_clears_completed_demo_status(tmp_path):
    from test_ipad_demo_voice import bridge
    b, _, _ = bridge(tmp_path)
    b.sessions['offline-session-123']['demo_status_task'] = object()
    b._begin_task('Open Safari', 'offline-session-123', 'new-owner-task')
    assert b.sessions['offline-session-123'].get('demo_status_task') is None


def test_social_ocr_uses_local_chinese_and_english_model(tmp_path, monkeypatch):
    import hidagent.ipad_demo_vision as module
    from hidagent.inventory_ocr import InventoryOCR
    class LocalOCR:
        def __init__(self, *, languages='eng'): self.languages = languages
        async def read_layout(self, frame):
            return {'text':'Joe Speaking\n编辑资料\n120 粉丝\n8 作品', 'rows':[], 'source':'local_tesseract_ocr'}
    monkeypatch.setattr(module, 'InventoryOCR', LocalOCR)
    vision = module.DemoVision()
    assert vision.ocr.languages == 'eng'
    assert vision.social_ocr.languages == 'eng+chi_sim'
    f, _, _, _, _ = make(tmp_path)
    from unittest.mock import AsyncMock
    vision.wait_stable = AsyncMock(return_value=f.screen.frame)
    result = asyncio.run(vision.check(f.screen, 'social_profile',app='douyin',before=f.screen.frame))
    assert result['metrics']['followers']['value'] == 120
    assert InventoryOCR(languages='eng+chi_sim').languages == 'eng+chi_sim'
    with pytest.raises(ValueError): InventoryOCR(languages='arbitrary-executable-option')


def test_local_ocr_passes_explicit_language_to_subprocess(tmp_path, monkeypatch):
    from hidagent.inventory_ocr import InventoryOCR
    from unittest.mock import AsyncMock
    import hidagent.inventory_ocr as module
    process = type('Process', (), {'communicate':AsyncMock(return_value=(b'local text', b'')), 'returncode':0})()
    create = AsyncMock(return_value=process)
    monkeypatch.setattr(module.asyncio,'create_subprocess_exec',create)
    assert asyncio.run(InventoryOCR(languages='eng+chi_sim')._mode(b'image',11)) == 'local text'
    args = create.await_args.args
    assert args[args.index('-l')+1] == 'eng+chi_sim'


def test_social_second_check_same_day_compares_previous_snapshot(tmp_path):
    f, _, _, _, _ = social_fake(tmp_path)
    first = run(f, 'social_check')
    assert '没有更早记录' in first['apps'][0]['speech']
    second = run(f, 'social_check')
    assert '没有更早记录' not in second['apps'][0]['speech']
    assert '持平' in second['apps'][0]['speech']
