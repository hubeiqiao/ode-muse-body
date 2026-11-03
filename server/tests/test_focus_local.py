"""Offline focus evidence: real native frames, no model/device access."""
import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image, ImageDraw
from hidagent.app_search import next_search_actions
from test_app_search import assert_query_batch

FIXTURES = Path(__file__).parent / 'fixtures/focus_local'
GOAL = {'app': 'TikTok', 'query': 'IELTS speaking'}


def agent(frame=None, rows=None):
    frame = frame or (FIXTURES / '043.jpg').read_bytes()
    return SimpleNamespace(
        screen=SimpleNamespace(frame=frame, updated=101.0),
        phone=SimpleNamespace(target_info=lambda: {'device': 'ipad', 'orientation': 'landscape'}),
        steps=[{'workflow': 'app_search', 'phase': 'app_ready', 'action': 'wait', 'act_s': .1, 't': 99},
               {'workflow': 'app_search', 'phase': 'focus', 'action': 'tap', 'act_s': .1, 't': 100}],
        query_text_reader=SimpleNamespace(read=AsyncMock(return_value=rows)),
        client=SimpleNamespace(evaluate=AsyncMock(return_value={
            'answers': {'ready': {'noul': 0}, 'blocked': {'noul': 0}}, 'seconds': .1})),
        trace=Mock())


def observation(rows):
    return {'source': 'local_vision_ocr', 'is_accessibility_tree': False, 'mode': 'fast', 'rows': rows}


def read(a, goal=GOAL):
    from hidagent.focus_local import verify_local_focus
    return asyncio.run(verify_local_focus(a, goal, a.screen.frame))


@pytest.mark.parametrize('name', ['043', '046', '049'])
def test_real_native_edit_menu_bypasses_model(name):
    # Rows recorded from the actual native fast worker on the production crop.
    rows = json.loads((FIXTURES / (name + '.json')).read_text())
    a = agent((FIXTURES / (name + '.jpg')).read_bytes(), rows)
    out = asyncio.run(next_search_actions(a, GOAL))
    assert_query_batch(out, GOAL['query'])
    a.client.evaluate.assert_not_called()
    assert any(c.kwargs.get('kind') == 'app-search-local-focus' and
               c.kwargs.get('signal') == 'edit_menu' for c in a.trace.write.call_args_list)


@pytest.mark.parametrize('problem', ['no_identity', 'no_ack', 'navigation_only', 'old_frame', 'wrong_app', 'after_query'])
def test_pixels_never_replace_current_run_context(problem):
    a = agent(rows=json.loads((FIXTURES / '043.json').read_text()))
    goal = GOAL
    if problem == 'no_identity': a.steps.pop(0)
    if problem == 'no_ack': a.steps[-1].pop('act_s')
    if problem == 'navigation_only': a.steps[-1]['phase'] = 'search_control'
    if problem == 'old_frame': a.screen.updated = 99
    if problem == 'wrong_app': goal = dict(GOAL, app='LinkedIn')
    if problem == 'after_query': a.steps.append({'workflow': 'app_search', 'phase': 'query', 'act_s': .1})
    assert read(a, goal) is None
    assert any(c.kwargs.get('kind') == 'app-search-local-focus-miss' and c.kwargs.get('reason')
               for c in a.trace.write.call_args_list)


def test_actual_unfocused_results_falls_back():
    a = agent((FIXTURES / 'youtube-results.jpg').read_bytes(), observation([]))
    goal = dict(GOAL, app='YouTube')
    assert read(a, goal) is None
    out = asyncio.run(next_search_actions(a, goal))
    assert out[0]['phase'] == 'search_focus'
    a.client.evaluate.assert_awaited_once()


def test_word_without_native_bubble_chrome_is_not_focus():
    im = Image.new('RGB', (1646, 1080), 'white')
    ImageDraw.Draw(im).text((100, 126), 'AutoFill', fill='black')
    b = io.BytesIO(); im.save(b, format='PNG')
    a = agent(b.getvalue(), json.loads((FIXTURES / '043.json').read_text()))
    assert read(a) is None


def test_bubble_outside_query_band_is_not_focus():
    im = Image.open(FIXTURES / '043.jpg').convert('RGB')
    im.paste(im.crop((60, 100, 215, 177)), (60, 500))
    im.paste('white', (0, 95, 1646, 215))
    b = io.BytesIO(); im.save(b, format='PNG')
    assert read(agent(b.getvalue(), observation([]))) is None


@pytest.mark.parametrize('mode', ['fast_error', 'wrong_mode', 'changed'])
def test_local_error_or_scene_change_keeps_model_path(mode):
    a = agent(rows=json.loads((FIXTURES / '043.json').read_text()))
    if mode == 'fast_error':
        from hidagent.local_text import LocalTextError
        a.query_text_reader.read.side_effect = LocalTextError('offline failure')
    elif mode == 'wrong_mode': a.query_text_reader.read.return_value['mode'] = 'accurate'
    else:
        async def changed(*args, **kwargs):
            a.screen.frame = (FIXTURES / 'youtube-results.jpg').read_bytes()
            return json.loads((FIXTURES / '043.json').read_text())
        a.query_text_reader.read.side_effect = changed
    assert read(a) is None


def pixels(draw):
    im = Image.new('RGB', (1646, 1080), 'white')
    draw(ImageDraw.Draw(im))
    b = io.BytesIO(); im.save(b, format='PNG')
    return b.getvalue()


@pytest.mark.parametrize('elapsed,shape,expected', [
    (.5, 'caret', True), (.39, 'caret', False), (.5, 'glyph', False),
    (.5, 'ring', False), (.5, 'outside', False), (.5, 'static', False)])
def test_blink_requires_two_fresh_spaced_frames_and_only_field_bar(elapsed, shape, expected):
    before = pixels(lambda d: None)
    def draw(d):
        if shape in ('caret', 'static'): d.rectangle((200, 50, 202, 82), fill=(0, 110, 255))
        if shape == 'glyph': d.rectangle((200, 50, 218, 82), fill='black')
        if shape == 'ring': d.rectangle((117, 40, 1479, 93), outline='blue', width=3)
        if shape == 'outside': d.rectangle((200, 350, 202, 382), fill='blue')
    after = pixels(draw)
    a = agent(after if shape == 'static' else before, observation([]))
    assert read(a) is None
    a.screen.frame, a.screen.updated = after, 101 + elapsed
    assert bool(read(a)) is expected
    if expected:
        assert a.trace.write.call_args.kwargs['signal'] == 'caret_blink'


def test_blink_sample_cannot_cross_run_or_tap():
    a = agent(pixels(lambda d: None), observation([]))
    read(a)
    a.steps = [dict(s) for s in a.steps]  # different run, same-looking receipt
    a.screen.updated = 102
    a.screen.frame = pixels(lambda d: d.rectangle((200, 50, 202, 82), fill='blue'))
    assert read(a) is None


@pytest.mark.parametrize('shape,expected', [('selection', True), ('ring', False), ('button', False)])
def test_selected_text_requires_fill_and_two_native_handles_inside_field(shape, expected):
    def draw(d):
        if shape == 'ring': d.rectangle((117, 40, 1479, 93), outline='blue', width=3)
        if shape in ('selection', 'button'):
            d.rectangle((200, 54, 320, 79), fill=(170, 205, 250))
            d.text((210, 59), 'IELTS speaking', fill='black')
        if shape == 'selection':
            d.rectangle((199, 51, 201, 81), fill=(0, 110, 255))
            d.ellipse((195, 44, 205, 54), fill=(0, 110, 255))
            d.rectangle((319, 51, 321, 81), fill=(0, 110, 255))
            d.ellipse((315, 78, 325, 88), fill=(0, 110, 255))
    a = agent(pixels(draw), observation([]))
    assert bool(read(a)) is expected
    if expected: assert a.trace.write.call_args.kwargs['signal'] == 'selected_text'


def test_other_workflow_input_invalidates_focus_context():
    a = agent(rows=json.loads((FIXTURES / '043.json').read_text()))
    a.steps.append({'workflow': 'keyboard', 'action': 'key', 'combo': 'escape', 'act_s': .1})
    assert read(a) is None


def test_dynamic_field_is_bound_to_located_geometry():
    a = agent(rows=json.loads((FIXTURES / '043.json').read_text()))
    a.steps[-1].update(_search_field_box={'left':117/1646, 'top':40/1080,
        'right':1480/1646, 'bottom':94/1080}, _search_field_size=[1646, 1080])
    assert read(a, dict(GOAL, app='Instagram'))['signal'] == 'edit_menu'
    a.steps[-1]['_search_field_size'] = [1080, 1646]
    assert read(a, dict(GOAL, app='Instagram')) is None


@pytest.mark.parametrize('name,app,field', [
    ('rednote', 'rednote', (78, 53, 1490, 117)),
    ('linkedin', 'LinkedIn', (553, 206, 1142, 246))])
def test_real_dark_and_anchored_above_menus(name, app, field):
    a = agent((FIXTURES / (name+'-editor.jpg')).read_bytes(),
              json.loads((FIXTURES / (name+'.json')).read_text()))
    l, t, r, b = field
    a.steps[-1].update(_search_field_box=dict(left=l/1646, top=t/1080, right=r/1646, bottom=b/1080),
                      _search_field_size=[1646, 1080])
    proof = read(a, dict(GOAL, app=app))
    assert proof and proof['signal'] == 'edit_menu'
    if name == 'linkedin': assert t <= proof['anchor'][1] <= b


def test_above_field_menu_requires_actual_arrow_into_field():
    im = Image.open(FIXTURES / 'linkedin-editor.jpg').convert('RGB')
    im.paste((235, 235, 235), (554, 198, 590, 210))
    b = io.BytesIO(); im.save(b, format='PNG')
    a = agent(b.getvalue(), json.loads((FIXTURES / 'linkedin.json').read_text()))
    a.steps[-1].update(_search_field_box=dict(left=553/1646, top=206/1080, right=1142/1646, bottom=246/1080),
                      _search_field_size=[1646, 1080])
    assert read(a, dict(GOAL, app='LinkedIn')) is None


# Private real-device frames are deliberately excluded from the public snapshot.
pytestmark = pytest.mark.skip(reason="Requires private real-device capture excluded from the open-source snapshot; synthetic matcher tests run separately")
