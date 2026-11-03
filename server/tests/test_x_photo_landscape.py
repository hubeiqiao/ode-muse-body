"""Filmed-demo regression: offline voice routing and measured native X screens."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hidagent.camera_handoff import CameraHandoff
from hidagent.ipad_demo_flows import DemoRequest
from hidagent.live_voice import ToolCall
from hidagent.x_photo_flow import PhotoPostVision
from test_ipad_demo_flows import make, png, run
from test_ipad_demo_voice import bridge

SID = 'offline-session-123'
POINTS = {'dock_x': (313, 752), 'compose': (1590, 918), 'media': (172, 968), 'latest': (617, 503),
          'add': (1455, 171), 'caption': (580, 252), 'send': (1428, 116), 'home': (172, 1008)}


@pytest.mark.parametrize('words', [
    'post this picture on X and describe the scene',
    '把这张照片发到X上，描述一下场景',
    'post this picture on ex with a caption describing the scene',
])
def test_camera_resume_always_uses_photo_demo_not_general_agent(tmp_path, words):
    b, f, _ = bridge(tmp_path)
    h = CameraHandoff()
    source = ToolCall(session=SID, id='return', name='return_to_device_mode', utterance=words,
                      scene_description='Orange hoodie in front of the camera.')
    assert h.offer(source, {'last_camera_photo': 'photo_taken'}, b.state())
    startup = h.claim(SID, b.sessions[SID], b.state())
    b.camera_handoff = h
    b.execute = AsyncMock(side_effect=AssertionError('General agent must not receive photo posts'))
    f.run = AsyncMock(return_value={'status': 'awaiting_confirmation'})
    async def scenario():
        call = ToolCall(session=SID, **startup)
        assert (await b.call(call))['status'] == 'awaiting_confirmation'
        assert (await b.call(call.model_copy(update={'id': 'duplicate'})))['status'] == 'awaiting_confirmation'
    asyncio.run(scenario())
    assert f.run.await_count == 1
    req = f.run.await_args.args[-1]
    assert req.action == 'x_photo_draft' and req.text == 'Orange hoodie in front of the camera.'
    b.execute.assert_not_awaited()


@pytest.mark.parametrize('name', ['phone_command', 'phone_explore', 'ipad_demo'])
def test_direct_voice_photo_request_cannot_enter_general_loop(tmp_path, name):
    b, f, _ = bridge(tmp_path)
    b.execute = AsyncMock(side_effect=AssertionError('General agent forbidden'))
    f.run = AsyncMock(return_value={'status': 'awaiting_confirmation'})
    c = ToolCall(session=SID, id='direct', name=name, owner_turn=1,
                 utterance='post the photo I just took on X with caption "Hello, demo!"',
                 args={'goal': 'Post it on X'} if name != 'ipad_demo' else {'action': 'x_photo_draft', 'text': 'Wrong caption', 'account': '@example_creator'})
    async def scenario():
        assert (await b.call(c))['status'] == 'awaiting_confirmation'
        assert (await b.call(c.model_copy(update={'id': 'duplicate'})))['status'] == 'awaiting_confirmation'
    asyncio.run(scenario())
    assert f.run.await_count == 1
    assert f.run.await_args.args[-1].action == 'x_photo_draft'
    assert f.run.await_args.args[-1].text == 'Hello, demo!'
    b.execute.assert_not_awaited()


def test_direct_photo_without_caption_uses_default_not_general(tmp_path):
    b, f, p = bridge(tmp_path)
    b.execute = AsyncMock(side_effect=AssertionError('No fallback'))
    f.run = AsyncMock(return_value={'status': 'awaiting_confirmation'})
    c = ToolCall(session=SID, id='direct', name='phone_command', owner_turn=1,
                 utterance='post the photo I just took on X', args={'goal': 'post the photo'})
    result = asyncio.run(b.call(c))
    assert result['status'] == 'awaiting_confirmation'
    f.run.assert_awaited_once()
    req = f.run.await_args.args[-1]
    assert req.action == 'x_photo_draft'
    assert req.text == 'Taken by Ode, my AI living on an old iPad.'
    b.execute.assert_not_awaited(); p.tap.assert_not_awaited()


class Screens:
    def __init__(self, selected, front=True, page='timeline'):
        self.page = page if front else 'other'
        self.selected = selected
        self.calls = []
        self.caption = ''
    async def check(self, screen, kind, **kw):
        self.calls.append(kind)
        if kind == 'draft': return {'caption': self.caption, 'image_attached': True}
        return {'foreground': self.page != 'other', 'account': True,
                'timeline': self.page == 'timeline', 'home_available': self.page == 'detail',
                'composer': self.page in ('composer', 'image'),
                'empty': self.page == 'composer', 'picker': self.page == 'picker',
                'selected_picker': self.page == 'picker' and self.selected,
                'unselected_picker': self.page == 'picker' and not self.selected,
                'composer_image': self.page == 'image',
                'draft': self.page == 'image' and self.caption == kw.get('text'),
                'sheet_closed': self.page == 'timeline', 'published': self.page == 'timeline'}[kind]
    async def target(self, screen, kind, **kw):
        x, y = POINTS.get(kind, (90, 116))
        return dict(x=x/1646, y=y/1080, box=dict(left=(x-15)/1646, right=(x+15)/1646,
                                                               top=(y-15)/1080, bottom=(y+15)/1080))


def flow(tmp_path, selected=True, front=True, page='timeline'):
    f, p, _, state, _ = make(tmp_path)
    f.screen.frame = png(size=(1646, 1080))
    p.target_info = lambda: dict(device='ipad', orientation='landscape', calibration_key='ipad-landscape-66', width=1646, height=1080)
    state['target'] = p.target_info()
    vision = Screens(selected, front, page)
    f.photo_vision = vision
    from PIL import Image, ImageDraw
    import io
    def frame():
        im = Image.new('RGB', (1646, 1080))
        if vision.selected: ImageDraw.Draw(im).rectangle((684, 568, 702, 589), fill=(0, 150, 255))
        out = io.BytesIO(); im.save(out, 'PNG'); f.screen.frame = out.getvalue()
    frame()
    f.open = AsyncMock(side_effect=lambda *a, **kw: setattr(vision, 'page', 'timeline'))
    taps = []
    async def tap(x, y, **kw):
        px = (round(x*1646), round(y*1080)); taps.append(px)
        if px in (POINTS['dock_x'], POINTS['home'], (90, 116)): vision.page = 'timeline'
        elif px == POINTS['compose']: assert vision.page == 'timeline'; vision.page = 'composer'
        elif px == POINTS['media']: assert vision.page == 'composer'; vision.page = 'picker'
        elif px == POINTS['latest']: assert vision.page == 'picker'; vision.selected = not vision.selected
        elif px == POINTS['add']: assert vision.selected; vision.page = 'image'
        elif px == POINTS['send']: assert vision.page == 'image'; vision.page = 'timeline'
        f.screen.updated += .1
        frame()
    async def typed(text): vision.caption = text; f.screen.updated += .1
    p.tap.side_effect = tap; p.type.side_effect = typed
    return f, p, vision, taps


@pytest.mark.parametrize('selected', [True, False])
@pytest.mark.parametrize('front,page', [(True, 'timeline'), (False, 'timeline'), (True, 'detail'), (True, 'video')])
def test_measured_photo_picker_sequence_and_confirmation(tmp_path, selected, front, page):
    f, p, v, taps = flow(tmp_path, selected, front, page)
    result = run(f, 'x_photo_draft', text='Hello, demo!', account='@example_creator')
    assert result['status'] == 'awaiting_confirmation', result
    expected = [POINTS['dock_x']]
    expected += [POINTS['compose'], POINTS['media']]
    if not selected: expected += [POINTS['latest']]
    expected += [POINTS['add'], POINTS['caption']]
    assert taps == expected
    assert v.calls == ['timeline', 'timeline', 'draft']
    assert POINTS['send'] not in taps
    f.open.assert_not_awaited()
    p.go_home.assert_awaited_once()
    p.key.assert_not_awaited()
    req = DemoRequest(action='x_post', draft_id=result['draft_id'])
    async def confirm():
        # A bare "yes" or a negation is not a post instruction; any "post it" phrasing is.
        for words in ('yes', "don't post it"):
            assert (await f.run('session', words, words, 2, req))['status'] == 'rejected'
        assert (await f.run('session', 'send', 'Yes, post it.', 3, req))['status'] == 'published'
        assert 'sheet_closed' in v.calls
        assert (await f.run('session', 'retry', 'post it', 4, req))['status'] == 'rejected'
    asyncio.run(confirm())
    assert taps.count(POINTS['send']) == 1


def test_direct_photo_uses_fresh_handoff_scene_and_never_model_goal_as_caption(tmp_path):
    b, f, _ = bridge(tmp_path)
    h = CameraHandoff()
    h.offer(ToolCall(session=SID, id='return', name='return_to_device_mode', utterance='return to voice',
                    scene_description='Joe’s orange hoodie. Another sentence.'),
            {'last_camera_photo': 'photo_taken'}, b.state())
    h.claim(SID, b.sessions[SID], b.state())
    b.sessions[SID].pop('pending_camera_intent')
    f.run = AsyncMock(return_value={'status': 'awaiting_confirmation'})
    b.execute = AsyncMock(side_effect=AssertionError('No general fallback'))
    c = ToolCall(session=SID, id='direct', name='phone_command', owner_turn=2,
                 utterance='post the photo I just took on X', args={'goal': 'Use an invented caption'})
    assert asyncio.run(b.call(c))['status'] == 'awaiting_confirmation'
    assert f.run.await_args.args[-1].text == "Joe's orange hoodie."


@pytest.mark.parametrize('words', ['upload this photo to X', 'tweet this picture', '发这张照片到Ｘ上'])
def test_photo_post_synonyms_never_fall_back_to_general(tmp_path, words):
    b, f, _ = bridge(tmp_path)
    h = CameraHandoff()
    h.offer(ToolCall(session=SID, id='return', name='return_to_device_mode', utterance=words,
                    scene_description='Orange hoodie.'), {'last_camera_photo': 'photo_taken'}, b.state())
    startup = h.claim(SID, b.sessions[SID], b.state())
    b.camera_handoff = h
    f.run = AsyncMock(return_value={'status': 'awaiting_confirmation'})
    b.execute = AsyncMock(side_effect=AssertionError('No general fallback'))
    assert asyncio.run(b.call(ToolCall(session=SID, **startup)))['status'] == 'awaiting_confirmation'


def test_ambiguous_photo_publication_handoff_never_enters_general_agent(tmp_path):
    b, f, _ = bridge(tmp_path)
    h = CameraHandoff()
    h.offer(ToolCall(session=SID, id='return', name='return_to_device_mode', utterance='post this picture please',
                    scene_description='Orange hoodie.'), {'last_camera_photo': 'photo_taken'}, b.state())
    startup = h.claim(SID, b.sessions[SID], b.state())
    b.camera_handoff = h
    b.execute = AsyncMock(side_effect=AssertionError('Ask for the platform, never browse arbitrary photos'))
    assert asyncio.run(b.call(ToolCall(session=SID, **startup)))['status'] == 'not_ready'


def test_post_sheet_not_closed_never_resends(tmp_path):
    f, p, v, taps = flow(tmp_path)
    r = run(f, 'x_photo_draft', text='Hello, demo!', account='@example_creator')
    f.mark_readback('session', r['draft_id'], r['readback'])
    original = v.check
    async def check(screen, kind, **kw):
        return False if kind == 'sheet_closed' else await original(screen, kind, **kw)
    v.check = check
    req = DemoRequest(action='x_post', draft_id=r['draft_id'])
    assert asyncio.run(f.run('session', 'send', 'post it', 2, req))['status'] == 'uncertain'
    assert asyncio.run(f.run('session', 'retry', 'post it', 3, req))['status'] == 'rejected'
    assert taps.count(POINTS['send']) == 1


def test_final_readback_is_one_vision_read_without_ready_or_layout_gate():
    box = dict(left=.3, top=.2, right=.7, bottom=.4)
    local = SimpleNamespace(observe=AsyncMock(return_value={'rows': [
        {'text': 'Observed caption', 'box': box}]}))
    client = SimpleNamespace(evaluate=AsyncMock(return_value={'answers': {
        'caption': {'box': box, 'confidence': .7}, 'image_attached': {'noul': .8}}}), close=AsyncMock())
    observer = PhotoPostVision(local, client_factory=lambda: client)
    screen = SimpleNamespace(frame=png(size=(1646, 1080)))
    assert asyncio.run(observer.check(screen, 'draft')) == {'caption': 'Observed caption', 'image_attached': True}
    assert client.evaluate.await_count == 1
    assert set(client.evaluate.await_args.args[1]) == {'caption', 'image_attached'}
    client.close.assert_awaited_once()


def test_lost_post_ack_consumes_draft_before_send(tmp_path):
    f, p, _, taps = flow(tmp_path)
    r = run(f, 'x_photo_draft', text='Hello, demo!', account='@example_creator')
    assert f.mark_readback('session', r['draft_id'], r['readback'])
    p.tap.side_effect = RuntimeError('ACK lost')
    req = DemoRequest(action='x_post', draft_id=r['draft_id'])
    assert asyncio.run(f.run('session', 'send', 'post it', 2, req))['status'] == 'uncertain'
    count = p.tap.await_count
    assert asyncio.run(f.run('session', 'retry', 'post it', 3, req))['status'] == 'rejected'
    assert p.tap.await_count == count


def test_dock_launch_timeout_never_composes(tmp_path):
    f, p, v, taps = flow(tmp_path, front=False)
    v.check = AsyncMock(return_value=False)
    f.sleep = AsyncMock(wraps=f.sleep)
    r = run(f, 'x_photo_draft', text='Hello', account='@example_creator')
    assert r['reason'] == 'X did not open from the dock'
    assert taps == [POINTS['dock_x']]
    assert v.check.await_count == 16
    assert [c.args for c in f.sleep.await_args_list][-18:] == [(1.0,), (1,)] + [(.5,)] * 16
    assert not f.pending


def test_dock_launch_polls_until_timeline(tmp_path):
    f, p, v, taps = flow(tmp_path)
    original = v.check
    polls = []
    async def check(screen, kind, **kw):
        if kind == 'timeline':
            polls.append(kind)
            if len(polls) < 3: return False
        return await original(screen, kind, **kw)
    v.check = check
    assert run(f, 'x_photo_draft', text='Hello', account='@example_creator')['status'] == 'awaiting_confirmation'
    assert len(polls) == 4
    assert taps[:2] == [POINTS['dock_x'], POINTS['compose']]


@pytest.mark.parametrize('caption,attached', [('Hello', False), ('', True), ('   ', True), ('', False)])
def test_invalid_final_photo_draft_fails_without_pending_confirmation(tmp_path, caption, attached):
    f, p, v, taps = flow(tmp_path)
    original = v.check
    async def check(screen, kind, **kw):
        if kind == 'draft': return {'caption': caption, 'image_attached': attached}
        return await original(screen, kind, **kw)
    v.check = check
    r = run(f, 'x_photo_draft', text='Hello', account='@example_creator')
    assert r['status'] == 'failed'
    assert 'missing' in r['reason']
    assert not f.pending
    assert POINTS['send'] not in taps


def test_picker_without_private_access_banner_shifts_grid_up():
    from pathlib import Path
    from hidagent.x_photo_flow import picker_offset, blue_badge, picker_open
    source = Path(__file__).parent / 'fixtures' / 'x-picker-nobanner.jpg'
    if not source.exists(): pytest.skip('Private photo-picker capture excluded from public snapshot')
    frame = source.read_bytes()
    assert picker_offset(frame) == -176 and blue_badge(frame) and picker_open(frame)
