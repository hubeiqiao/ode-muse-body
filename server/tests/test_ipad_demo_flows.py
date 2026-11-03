"""Offline contracts for keyboard-first voice demos. No device or model IO."""
import asyncio
import io
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageDraw

from hidagent.companion import CompanionMemory
from hidagent.voice_task_memory import VoiceTaskMemory
from hidagent.ipad_demo_flows import DemoFlows, DemoRequest, preflight
from hidagent.ipad_demo_vision import DemoVision, frame_diff, detect_shutter, product_rows


def png(color='black', size=(800, 500), shutter=False):
    im = Image.new('RGB', size, color)
    if shutter:
        ImageDraw.Draw(im).ellipse((720, 210, 780, 270), fill='white')
    out = io.BytesIO(); im.save(out, 'PNG'); return out.getvalue()


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    async def sleep(self, seconds): self.now += seconds


class Vision:
    def __init__(self):
        self.calls = []; self.changed = True; self.direction = 'rear'; self.draft = 'A useful reply'
    async def observe(self, frame):
        return {'text': 'Grok Bot answer', 'rows': [], 'frame': frame}
    async def check(self, screen, kind, **kw):
        self.calls.append((kind, kw))
        if kind == 'camera': return {'shutter': (.9375, .48), 'box': dict(left=.9, top=.42, right=.98, bottom=.54), 'thumbnail': (.90, .62, .98, .75), 'direction': self.direction}
        if kind == 'capture': return self.changed
        if kind == 'products': return {'items': [{'name': 'Travel mug', 'price': '$19.99', 'rating': '4.6'}], 'text': 'Travel mug $19.99 4.6 out of 5'}
        if kind == 'answer': return {'text': 'A useful answer', 'frame': screen.frame}
        if kind == 'post_read': return {'text': 'Current public post', 'frame': screen.frame}
        if kind == 'draft': return {'text': self.draft, 'account': '@example_creator', 'target': kw['target']}
        if kind == 'published': return self.changed
        if kind == 'direction': return self.direction
        if kind == 'spotlight':
            # Like the iPad: Spotlight is open only after cmd+space was sent.
            phone = getattr(self, 'phone', None)
            return phone is None or any(c.args == ('cmd+space',) for c in phone.key.await_args_list)
        return True
    async def target(self, screen, kind, **kw):
        self.calls.append((kind, kw)); return dict(x=.5, y=.8, box=dict(left=.4, top=.75, right=.6, bottom=.85))


def make(tmp_path):
    clock = Clock(); vision = Vision()
    screen = SimpleNamespace(frame=png(), updated=time.time(), live=True)
    phone = SimpleNamespace(key=AsyncMock(), type=AsyncMock(), tap=AsyncMock(), go_home=AsyncMock(), calibrated=True,
        device=SimpleNamespace(links=1, readiness=lambda: {'connected': True, 'stable': True}),
        target_info=lambda: dict(device='ipad', orientation='landscape', calibration_key='mini', width=800, height=500))
    vision.phone = phone
    async def update(*args, **kwargs): screen.updated += .1
    phone.key.side_effect = phone.type.side_effect = phone.tap.side_effect = phone.go_home.side_effect = update
    state = dict(device=True, controller={'connected': True, 'stable': True}, screen=True, calibrated=True,
        mirror_diagnostics={'state': 'session_connected', 'frame_age_s': .1}, target=phone.target_info(),
        full_screen_apps=True)
    memory = VoiceTaskMemory(CompanionMemory(tmp_path))
    flow = DemoFlows(phone, screen, lambda: state, memory, vision=vision, clock=clock, sleep=clock.sleep)
    return flow, phone, vision, state, clock


def run(flow, action, **kw):
    return asyncio.run(flow.run('session', 'request-'+str(flow.sequence), 'owner request', 1,
                                DemoRequest(action=action, **kw)))


def test_preflight_reports_all_fixes_without_input():
    r = preflight({})
    assert not r['ready']
    assert set(r['checks']) == {'board_link', 'mirror', 'calibration', 'orientation', 'full_screen_apps'}
    assert all(row['fix'] for row in r['checks'].values() if not row['ready'])


@pytest.mark.parametrize('field', ['device', 'screen', 'calibrated'])
def test_not_ready_never_touches_hid(tmp_path, field):
    f, p, _, state, _ = make(tmp_path); state[field] = False
    assert run(f, 'camera_open')['status'] == 'not_ready'
    p.key.assert_not_awaited(); p.tap.assert_not_awaited()


@pytest.mark.parametrize('action', ['preflight', 'camera_open', 'camera_photo', 'camera_front', 'camera_rear', 'x_photo_draft'])
def test_unproven_full_screen_never_navigates(tmp_path, monkeypatch, action):
    monkeypatch.delenv('IPAD_FULL_SCREEN_VERIFIED', raising=False)
    monkeypatch.setenv('HIDAGENT_DATA', str(tmp_path))
    f, p, _, state, _ = make(tmp_path); state['full_screen_apps'] = False
    f.open_full_screen_setting = AsyncMock(side_effect=AssertionError('Settings forbidden'))
    args = dict(text='A caption', account='@example_creator') if action == 'x_photo_draft' else {}
    assert run(f, action, **args)['status'] == 'not_ready'
    f.open_full_screen_setting.assert_not_awaited()
    for method in (p.key, p.type, p.tap, p.go_home): method.assert_not_awaited()


def test_preflight_env_ready_without_phone_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('HIDAGENT_DATA', str(tmp_path))
    monkeypatch.setenv('IPAD_FULL_SCREEN_VERIFIED', '1')
    f, p, v, state, _ = make(tmp_path)
    p.target_info = lambda: None  # status snapshot still has calibrated geometry
    state['full_screen_apps'] = False
    v.observe = AsyncMock(side_effect=AssertionError('Env proof needs no OCR'))
    assert run(f, 'preflight')['status'] == 'ready'
    for method in (p.key, p.type, p.tap, p.go_home): method.assert_not_awaited()
    v.observe.assert_not_awaited()


def test_preflight_sends_zero_board_commands_even_on_aged_link(tmp_path):
    f, p, _, state, _ = make(tmp_path)
    state['controller'] = dict(connected=True, stable=False, reason='waiting_for_stable_acknowledgements')
    p.device.readiness = lambda: state['controller']
    p.device.send = AsyncMock()
    assert run(f, 'preflight')['status'] == 'not_ready'
    p.device.send.assert_not_awaited()
    for method in (p.key, p.type, p.tap, p.go_home): method.assert_not_awaited()


def test_full_screen_and_action_logged_without_text(tmp_path, caplog):
    import logging
    f, _, _, _, _ = make(tmp_path)
    with caplog.at_level(logging.INFO, logger='hidagent.ipad_demo_flows'):
        assert run(f, 'preflight')['status'] == 'ready'
        assert run(f, 'grok_read', query='Private dictated query')['status'] == 'read'
    assert 'action=preflight' in caplog.text
    assert 'action=grok_read' in caplog.text
    assert 'full_screen_apps=True source=observed' in caplog.text
    assert 'category=app_search' in caplog.text
    assert 'category=query' in caplog.text
    assert 'Private dictated query' not in caplog.text


def test_camera_launch_is_spotlight_keyboard_only(tmp_path):
    f, p, _, _, _ = make(tmp_path)
    r = run(f, 'camera_open')
    assert r['status'] == 'ready'
    assert [c.args for c in p.key.await_args_list] == [('cmd+space',), ('cmd+a',), ('enter',)]
    p.type.assert_awaited_once_with('Camera'); p.tap.assert_not_awaited()
    assert r['timeline'] and all('elapsed_s' in e for e in r['timeline'])


@pytest.mark.parametrize('delay', [0, 3])
def test_photo_one_guarded_tap_local_confirmation(tmp_path, delay):
    f, p, v, _, clock = make(tmp_path)
    r = run(f, 'camera_photo', countdown=delay)
    assert r['status'] == 'captured' and p.tap.await_count == 1
    assert p.tap.await_args.kwargs['aim'] and callable(p.tap.await_args.kwargs['pre_click'])
    assert clock.now >= delay
    assert any(k == 'capture' for k, _ in v.calls)


def test_photo_uncertain_never_retries(tmp_path):
    f, p, v, _, _ = make(tmp_path); v.changed = False
    assert run(f, 'camera_photo')['status'] == 'uncertain'
    assert p.tap.await_count == 1


def test_unknown_front_rear_does_not_blind_toggle(tmp_path):
    f, p, v, _, _ = make(tmp_path); v.direction = None
    assert run(f, 'camera_front')['status'] == 'not_ready'
    p.tap.assert_not_awaited()


def test_camera_direction_is_idempotent(tmp_path):
    f, p, _, _, _ = make(tmp_path)
    assert run(f, 'camera_rear')['status'] == 'ready'
    p.tap.assert_not_awaited()


def test_grok_waits_for_new_stable_answer_and_uses_ocr(tmp_path):
    f, p, v, _, _ = make(tmp_path)
    r = run(f, 'grok_read', query='What makes speaking practice effective?')
    assert r['status'] == 'read' and r['text'] == 'A useful answer'
    assert any(k == 'answer' for k, _ in v.calls)
    assert p.type.await_args_list[0].args == ('Grok Bot',)
    assert p.type.await_args_list[1].args == ('What makes speaking practice effective?',)
    assert r['bundle'] == 'co.anysphere.sand'


def test_amazon_keyboard_url_and_explicit_needs_persist(tmp_path):
    f, p, _, _, _ = make(tmp_path)
    r = run(f, 'amazon_lookup', query='travel mug', needs='Leakproof, under $30')
    assert r['status'] == 'read' and r['items'][0]['price'] == '$19.99'
    assert [c.args for c in p.type.await_args_list] == [('Safari',), ('https://www.amazon.ca/s?k=travel+mug',)]
    assert ('cmd+t',) in [c.args for c in p.key.await_args_list]
    p.tap.assert_not_awaited()
    recalled = VoiceTaskMemory(f.memory.memory).recall_lookup('product')
    assert recalled['query'] == 'travel mug' and recalled['needs'] == 'Leakproof, under $30'


def test_followup_repeats_read_only_product_lookup_after_restart(tmp_path):
    f, p, _, _, _ = make(tmp_path)
    run(f, 'amazon_lookup', query='travel mug', needs='Leakproof')
    f.memory = VoiceTaskMemory(f.memory.memory)
    r = run(f, 'memory_followup', memory_kind='product')
    assert r['status'] == 'read' and r['recalled']['query'] == 'travel mug'
    assert p.type.await_args_list[-1].args[0].endswith('travel+mug')
    p.tap.assert_not_awaited()


def test_post_followup_never_replays_draft_or_post(tmp_path):
    f, p, _, _, _ = make(tmp_path)
    f.memory.remember_lookup('post', 'practice tips', '', 'https://x.com/owner/status/123')
    r = run(f, 'memory_followup', memory_kind='post')
    assert r['status'] == 'read'
    assert not any('A useful reply' in c.args for c in p.type.await_args_list)


def prepare(f):
    r = run(f, 'x_draft', target='https://x.com/owner/status/123', text='A useful reply', account='@example_creator')
    assert f.mark_readback('session', r['draft_id'], r['readback'])
    return r


def test_x_draft_stops_and_requires_readback_receipt(tmp_path):
    f, p, _, _, _ = make(tmp_path); r = prepare(f)
    assert r['status'] == 'awaiting_confirmation' and r['draft_id']
    assert r['readback'] == 'A useful reply'
    assert not any(k == 'post_button' for k, _ in f.vision.calls)
    assert r['account'] == '@example_creator' and r['target'].endswith('/123')


@pytest.mark.parametrize('utterance,turn', [('yes', 2), ('发送', 1), ('post it later', 2), ('do not post it', 2)])
def test_post_rejects_nonexplicit_or_same_draft_turn(tmp_path, utterance, turn):
    f, p, _, _, _ = make(tmp_path); draft = prepare(f); before = p.tap.await_count
    r = asyncio.run(f.run('session', 'confirm', utterance, turn,
        DemoRequest(action='x_post', draft_id=draft['draft_id'])))
    assert r['status'] == 'rejected' and p.tap.await_count == before


def test_explicit_confirmation_posts_once_and_uncertain_never_resends(tmp_path):
    f, p, v, _, _ = make(tmp_path); draft = prepare(f); v.changed = False
    req = DemoRequest(action='x_post', draft_id=draft['draft_id'])
    r = asyncio.run(f.run('session', 'confirm', '发送', 2, req))
    assert r['status'] == 'uncertain'
    before = p.tap.await_count
    r2 = asyncio.run(f.run('session', 'another-id', 'post it', 3, req))
    assert r2['status'] == 'rejected' and p.tap.await_count == before


def test_draft_other_session_changed_text_expired_block(tmp_path):
    f, p, v, _, clock = make(tmp_path); d = prepare(f); before = p.tap.await_count
    req = DemoRequest(action='x_post', draft_id=d['draft_id'])
    assert asyncio.run(f.run('other', 'x', '发送', 2, req))['status'] == 'rejected'
    v.draft = 'Changed text'
    assert asyncio.run(f.run('session', 'y', '发送', 2, req))['status'] == 'rejected'
    assert p.tap.await_count == before
    clock.now += 121
    assert asyncio.run(f.run('session', 'z', '发送', 3, req))['status'] == 'rejected'


def test_lost_hid_ack_returns_uncertain_without_retyping(tmp_path):
    f, p, _, _, _ = make(tmp_path); p.type.side_effect = RuntimeError('ack lost')
    r = run(f, 'amazon_lookup', query='mug')
    assert r['status'] == 'uncertain' and p.type.await_count == 1


def test_same_request_deduplicates_and_conflicts_reject(tmp_path):
    f, p, _, _, _ = make(tmp_path)
    async def scenario():
        req = DemoRequest(action='camera_photo')
        a = await f.run('session', 'id', '拍', 1, req)
        b = await f.run('session', 'id', '拍', 1, req)
        assert a == b and p.tap.await_count == 1
        assert (await f.run('session', 'id', '拍', 1, DemoRequest(action='camera_open')))['status'] == 'rejected'
    asyncio.run(scenario())


def test_rotation_or_link_change_during_tap_prevents_click(tmp_path):
    f, p, _, _, _ = make(tmp_path)
    async def tap(*args, **kwargs):
        p.device.links += 1
        assert kwargs['pre_click']() is False
        raise RuntimeError('binding changed')
    p.tap.side_effect = tap
    assert run(f, 'camera_photo')['status'] == 'uncertain'


def test_shutter_detection_round_right_edge_and_cache_geometry():
    found = detect_shutter(png(shutter=True))
    assert found and .9 < found['shutter'][0] < 1
    assert detect_shutter(png('white')) is None
    assert detect_shutter(png()) is None
    assert frame_diff(png(), png('white')) > 200


def test_product_parser_preserves_missing_fields_without_invention():
    rows = product_rows('Travel mug\n$19.99\n4.6 out of 5 stars\nWater bottle\n$12.50')
    assert rows[0] == {'name': 'Travel mug', 'price': '$19.99', 'rating': '4.6'}
    assert rows[1]['rating'] is None


def test_stability_requires_changed_fresh_frames_and_dwell(tmp_path):
    f, _, _, _, clock = make(tmp_path)
    vision = DemoVision(clock=clock, sleep=clock.sleep)
    screen = SimpleNamespace(frame=png(), updated=1, live=True)
    with pytest.raises(RuntimeError, match='changed|fresh|stable'):
        asyncio.run(vision.wait_stable(screen, before=screen.frame, timeout=2))


def test_local_inventory_layout_uses_tsv_boxes(monkeypatch):
    from hidagent.inventory_ocr import InventoryOCR
    async def mode(self, image, mode, *, tsv=False):
        assert tsv and mode == 11
        return 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t10\t20\t50\t12\t96\tSearch\n'
    monkeypatch.setattr(InventoryOCR, '_mode', mode)
    result = asyncio.run(InventoryOCR().read_layout(png()))
    assert result['rows'][0]['text'] == 'Search'
    assert result['rows'][0]['box']['left'] == .0125
    assert result['source'] == 'local_tesseract_ocr'


def test_local_check_waits_for_new_frame_after_keyboard(tmp_path):
    f, p, v, _, clock = make(tmp_path)
    stamps = []
    async def key(combo):
        stamps.append(f.screen.updated)
    p.key.side_effect = key
    async def sleep(seconds):
        clock.now += seconds
        if clock.now >= .2: f.screen.updated += .1
    f.sleep = sleep
    old = v.check
    async def check(screen, kind, **kw):
        if kind == 'spotlight' and stamps:
            assert screen.updated > stamps[-1], 'stale frame accepted after keyboard input'
        return await old(screen, kind, **kw)
    v.check = check
    assert run(f, 'camera_open')['status'] == 'ready'


def test_full_screen_check_rejects_label_without_selected_tick():
    from hidagent.ipad_demo_vision import selected_checkmark
    assert not selected_checkmark(png('white'), dict(left=.3, top=.3, right=.5, bottom=.35))


def test_confirmed_post_requires_exact_current_draft_not_substring(tmp_path):
    f, p, v, _, _ = make(tmp_path)
    d = prepare(f); v.draft = 'A useful reply with extra text'
    before = p.tap.await_count
    assert asyncio.run(f.run('session', 'p', '发送', 2,
        DemoRequest(action='x_post', draft_id=d['draft_id'])))['status'] == 'rejected'
    assert p.tap.await_count == before


def test_sending_keyboard_to_private_challenge_is_refused(tmp_path):
    f, p, v, _, _ = make(tmp_path)
    old = v.check
    async def check(screen, kind, **kw):
        if kind == 'spotlight': return False
        return await old(screen, kind, **kw)
    v.check = check
    assert run(f, 'amazon_lookup', query='mug')['status'] != 'read'
    p.type.assert_not_awaited()


def test_local_ocr_extra_draft_text_is_not_confirmed(tmp_path):
    class OCR:
        async def read_layout(self, frame):
            def row(text, top, left=.2, right=.8):
                return {'text':text, 'confidence':.99,'box':dict(left=left,top=top,right=right,bottom=top+.03)}
            return {'text':'https://x.com/owner/status/123\n@example_creator\nA useful reply with extra text',
                    'rows':[row('@example_creator',.8,.02,.2),row('A useful reply with extra text',.5)], 'source':'local_tesseract_ocr'}
    v = DemoVision(OCR())
    screen = SimpleNamespace(frame=png(), updated=1, live=True)
    assert not asyncio.run(v.check(screen, 'draft', target='https://x.com/owner/status/123',
                                  account='@example_creator', text='A useful reply'))


def test_grok_prompt_echo_without_completed_answer_is_rejected():
    class OCR:
        async def read_layout(self, frame):
            return {'text':'Grok\nWhat is practice?\nAsk anything', 'rows':[], 'source':'local_tesseract_ocr'}
    v = DemoVision(OCR())
    async def stable(*a, **kw): return png('white')
    v.wait_stable = stable
    with pytest.raises(RuntimeError, match='answer|completion'):
        asyncio.run(v.check(SimpleNamespace(frame=png(),updated=1,live=True), 'answer',
                            before=png(), query='What is practice?'))


def test_demo_factory_opt_in_and_ipad_only(tmp_path, monkeypatch):
    import hidagent.ipad_demo_flows as module
    f, phone, _, state, _ = make(tmp_path)
    monkeypatch.delenv('IPAD_VOICE_DEMOS', raising=False)
    assert module.build_demos(phone, f.screen, lambda: state, f.memory) is None
    monkeypatch.setenv('IPAD_VOICE_DEMOS','1')
    assert isinstance(module.build_demos(phone, f.screen, lambda: state, f.memory), DemoFlows)
    state['target']['device'] = 'iphone'
    assert module.build_demos(phone, f.screen, lambda: state, f.memory) is None


def test_camera_detection_cached_only_for_same_link_geometry(monkeypatch):
    import hidagent.ipad_demo_vision as module
    im = Image.new('RGB',(1646,1080),'black')
    ImageDraw.Draw(im).ellipse((1498,494,1594,590),fill='white')
    buffer=io.BytesIO(); im.save(buffer,'PNG')
    frame=buffer.getvalue(); screen=SimpleNamespace(frame=frame,updated=1,live=True)
    phone=SimpleNamespace(device=SimpleNamespace(links=1),target_info=lambda: {'calibration_key':'mini','orientation':'landscape'})
    camera=SimpleNamespace(camera_still=lambda f:True,phone=phone)
    v=DemoVision(camera=camera)
    original=module.detect_shutter; calls=[]
    def detect(frame): calls.append(1);return original(frame)
    monkeypatch.setattr(module,'detect_shutter',detect)
    asyncio.run(v.check(screen,'camera'));asyncio.run(v.check(screen,'camera'))
    assert len(calls)==1
    phone.device.links+=1
    asyncio.run(v.check(screen,'camera'));assert len(calls)==2


def test_demo_status_ownership_safe_outside_event_loop(tmp_path):
    f, _, _, _, _ = make(tmp_path)
    assert f.owns_current() is False


def test_offline_preflight_cli_never_connects_and_reports_fixes(capsys):
    from hidagent.ipad_demo_check import main
    assert main([]) == 1
    import json
    result=json.loads(capsys.readouterr().out)
    assert result['snapshot_only'] and not result['ready']
    assert all(r['fix'] for r in result['checks'].values())


def test_reply_uses_weighted_x_limit_before_any_input():
    with pytest.raises(ValueError):
        DemoRequest(action='x_draft', target='https://x.com/owner/status/123', account='@example_creator',text='你'*141)


from test_keyboard_no_pointer import keyboard_phone


def test_landscape_demo_uses_real_phone_tap_contract(tmp_path, keyboard_phone):
    f, _, _, _, _ = make(tmp_path)
    f.phone=keyboard_phone; keyboard_phone.screen=f.screen
    f.screen.settled=AsyncMock()
    async def point(*args, **kwargs): f.screen.updated+=.1
    keyboard_phone.point=AsyncMock(side_effect=point)
    r=run(f,'camera_photo')
    assert r['status']=='captured', r
    assert [c.args[0] for c in keyboard_phone.device.send.await_args_list]==['click']
    assert keyboard_phone.point.await_args.args[2] is True


def test_named_post_search_resolves_current_url_before_drafting(tmp_path):
    f,p,v,_,_=make(tmp_path)
    original=v.check
    async def check(screen,kind,**kw):
        if kind=='post_url':return 'https://x.com/owner/status/123'
        return await original(screen,kind,**kw)
    v.check=check
    r=run(f,'x_draft',query='Speaking practice tips',text='A useful reply',account='@example_creator')
    assert r['status']=='awaiting_confirmation' and r['target']=='https://x.com/owner/status/123'
    assert any('x.com/search?q=Speaking+practice+tips' in c.args[0] for c in p.type.await_args_list)
    assert any(k=='named_post' for k,_ in v.calls)


def test_control_guard_rejects_same_geometry_app_change(tmp_path):
    f,p,_,_,_=make(tmp_path); seen=[]
    async def tap(*args,**kw):
        f.screen.frame=png('white')
        seen.append(kw['pre_click']())
        raise RuntimeError('No click during test')
    p.tap.side_effect=tap
    run(f,'grok_read',query='What is useful practice?')
    assert seen == [False]


def test_preflight_missing_ocr_reports_fix_instead_of_uncertain(tmp_path):
    f,p,v,state,_=make(tmp_path)
    state.pop('full_screen_apps')
    async def observe(frame):raise RuntimeError('Local OCR binary unavailable')
    v.observe=observe
    result=run(f,'preflight')
    assert result['status']=='not_ready'
    assert 'local OCR' in result['preflight']['checks']['full_screen_apps']['fix']
    p.key.assert_not_awaited()


def test_shutter_upper_arc_survives_assistive_touch_overlap():
    im=Image.new('RGB',(800,500),'black');draw=ImageDraw.Draw(im)
    draw.ellipse((720,210,780,270),fill='white')
    draw.rectangle((738,246,792,285),fill=(80,80,80))
    buffer=io.BytesIO();im.save(buffer,'PNG')
    assert detect_shutter(buffer.getvalue()) is not None


def test_plain_grok_answer_needs_no_textual_copy_button():
    before=png();after=png('white')
    class OCR:
        async def read_layout(self,frame):
            base='Grok\nWhat is practice?\nAsk anything'
            answer='\nPractice speaking daily, review feedback and retry the same answer.' if frame==after else ''
            return {'text':base+answer,'rows':[],'source':'local_tesseract_ocr'}
    v=DemoVision(OCR())
    async def stable(*args,**kw):return after
    v.wait_stable=stable
    result=asyncio.run(v.check(SimpleNamespace(frame=after,updated=2,live=True),'answer',before=before,query='What is practice?'))
    assert result['text']=='Practice speaking daily, review feedback and retry the same answer.'


def test_open_never_toggles_an_already_open_spotlight(tmp_path):
    f, p, v, _, _ = make(tmp_path); v.phone = None  # Spotlight already open
    assert run(f, 'camera_open')['status'] == 'ready'
    assert [c.args for c in p.key.await_args_list] == [('cmd+a',), ('enter',)]


def test_amazon_signed_out_greeting_is_not_a_login_wall():
    from hidagent.ipad_demo_vision import public_text
    import re
    page = 'amazon\nHello, sign in\nAccount & Lists\nnoise cancelling headphones\n$61.43'
    assert not re.search(r'\b(sign in|log in)\b', public_text(page), re.I)
    assert re.search(r'\bsign in\b', public_text('Sign in\nEmail or mobile phone number'), re.I)


def test_amazon_cards_group_by_column_and_restore_superscript_cents():
    from hidagent.ipad_demo_vision import product_cards
    box = lambda left, top: dict(left=left, top=top, right=left+.15, bottom=top+.02)
    # Live amazon.ca OCR, 2026-10-31: two cards side by side.
    rows = [dict(text=t, box=box(l, y)) for t, l, y in [
        ('Sennheiser Momentum 4', .288, .725), ('Sennheiser HD 599SE', .813, .725),
        ('WireLess Headphones, ANC, 60...', .287, .756), ('Wired AudiopF', .813, .756),
        ('4 ****th110.3k)', .287, .793), ('4.5 ***** {4k}', .812, .792),
        ('$49995', .287, .827), ('$22844', .813, .827)]]
    cards = product_cards(rows)
    assert [(c['name'], c['price'], c['rating']) for c in cards] == [
        ('Sennheiser Momentum 4 WireLess Headphones, ANC, 60...', '$499.95', '4'),
        ('Sennheiser HD 599SE Wired AudiopF', '$228.44', '4.5')]


def test_quiet_link_is_pinged_back_to_stable_instead_of_asking_owner(tmp_path):
    f, p, _, state, _ = make(tmp_path)
    link = {'connected': True, 'stable': False, 'reason': 'waiting_for_stable_acknowledgements'}
    async def ping(command):
        assert command == 'ping'; link.update(stable=True, reason='ready')
    p.device.readiness = lambda: dict(link); p.device.send = AsyncMock(side_effect=ping)
    f.state = lambda: {**state, 'controller': dict(link)}
    assert run(f, 'camera_open')['status'] != 'not_ready'
    p.device.send.assert_awaited_once_with('ping')


def test_owner_facing_fixes_never_ask_owner_to_do_device_work():
    fixes = {k: v['fix'] for k, v in preflight({})['checks'].items()}
    assert 'Settings' not in fixes['full_screen_apps']
    assert 'Reconnect' not in fixes['board_link']


def test_amazon_results_without_readable_prices_still_report_read(tmp_path):
    f, p, _, state, _ = make(tmp_path)
    async def require(kind, **kw):
        if kind == 'products': raise RuntimeError('No locally readable product/price blocks')
        return await original(kind, **kw)
    original = f.require; f.require = require
    async def noop(*a, **kw): return 0
    f.open = noop; f.url = noop
    r = run(f, 'amazon_lookup', query='wireless earbuds')
    assert r['status'] == 'read' and r['results_on_screen'] is True and 'Describe' in r['instruction']


def test_amazon_header_sign_in_does_not_block_products_but_forms_do():
    from hidagent.ipad_demo_vision import blocked_by_login
    header = 'amazon.ca Hello, sign in Account & Lists 1-48 of over 50,000 results for "wireless earbuds"'
    assert not blocked_by_login('products', header)
    assert blocked_by_login('products', 'Sign in Email or mobile phone number Continue')
    assert blocked_by_login('products', 'Enter the characters you see captcha')
    assert blocked_by_login('answer', 'Sign in to continue')
