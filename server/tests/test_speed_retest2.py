"""Offline regressions from speed-retest2. All action/model clients are mocked."""
import asyncio
import io
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image, ImageDraw

from hidagent import app_query_text as qt, app_search, app_controls, shortcut_workflow as sw, reddit_web as rw
from hidagent.phone import Phone
from test_launch_start_robustness import spotlight, reddit
from test_launch_receipt_fixes import result, probability
from test_landscape_query_ocr import agent, observation, row

FRAMES = Path(__file__).parent / 'fixtures/speed_retest2'
TARGET = {'device': 'ipad', 'orientation': 'landscape'}


def test_home_clears_spotlight_text_then_closes_panel_before_home():
    p = NS(profiles=object(), target_info=lambda: TARGET, key=AsyncMock())
    asyncio.run(Phone.go_home(p))
    assert [c.args[0] for c in p.key.await_args_list] == ['esc', 'esc', 'cmd+h']


@pytest.mark.parametrize('merged', ['0', '1'])
# Real sr3 values: Douyin (Chrome suggestion first) Jev first_result .01-.02.
@pytest.mark.parametrize('first', [None, 0, .02, .89])
def test_installed_app_below_chrome_suggestion_never_receives_return(monkeypatch, merged, first):
    monkeypatch.setenv('SHORTCUT_MERGE_APP_RESULT', merged)
    a = spotlight(monkeypatch, ready=1, absent=0)
    a.screen.frame = (FRAMES / 'a10-dy-post.jpg').read_bytes()
    async def evaluate(state, questions, frame):
        answers = {'ready': probability(1), 'installed_app': probability(1),
                   'no_installed_app': probability(0), 'page': {'choice': 'search', 'confidence': 1}}
        if first is not None: answers['first_result'] = probability(first)
        return result(**answers)
    a.client.evaluate.side_effect = evaluate
    actions = asyncio.run(sw.next_shortcut_actions(a, {'app': 'Douyin', 'text': None}))
    assert all(x.get('combo') != 'enter' and x['action'] != 'done' for x in actions)
    assert any('first_result' in c.args[1] for c in a.client.evaluate.call_args_list)


def test_top_installed_app_can_receive_return_once(monkeypatch):
    a = spotlight(monkeypatch, ready=1, absent=0)
    original = a.client.evaluate.side_effect
    async def evaluate(state, questions, frame):
        r = await original(state, questions, frame)
        if 'first_result' in questions: r['answers']['first_result'] = probability(1)
        return r
    a.client.evaluate.side_effect = evaluate
    assert asyncio.run(sw.next_shortcut_actions(a, {'app': 'Douyin', 'text': None}))[0]['combo'] == 'enter'


@pytest.mark.parametrize('first', [.91, .95])
def test_top_installed_app_at_real_jev_confidence_receives_return(monkeypatch, first):
    # Real sr3 values: target app as Top Hit, Jev first_result .91-.98 (14/14).
    a = spotlight(monkeypatch, ready=1, absent=0)
    original = a.client.evaluate.side_effect
    async def evaluate(state, questions, frame):
        r = await original(state, questions, frame)
        if 'first_result' in questions: r['answers']['first_result'] = probability(first)
        return r
    a.client.evaluate.side_effect = evaluate
    assert asyncio.run(sw.next_shortcut_actions(a, {'app': 'Douyin', 'text': None}))[0]['combo'] == 'enter'


@pytest.mark.parametrize('ready,literal', [(0, 0), (.02, 0), (0, 1), (.98, 1)])
def test_reddit_focus_ack_without_local_focus_evidence_cannot_type(monkeypatch, ready, literal):
    a = reddit(monkeypatch)
    a.client.evaluate.return_value = result(ready=probability(ready), literal=probability(literal), blocked=probability(0))
    out = asyncio.run(rw.next_reddit_web_actions(a, {'query': 'IELTS'}))[0]
    assert out['action'] == 'wait' and 'text' not in out


def search_agent(monkeypatch, app, phases):
    monkeypatch.setenv('APP_LAUNCH_SPOTLIGHT_FIRST', '0')
    a = NS(phone=NS(target_info=lambda: TARGET, calibrated=True),
           screen=NS(frame=(FRAMES / ('a06-x-post.jpg' if app == 'X' else 'a04-ig-post.jpg')).read_bytes()),
           steps=[dict(workflow='app_search', phase=p, action='tap', act_s=.1) for p in phases],
           trace=Mock(), client=NS(evaluate=AsyncMock()))
    a.client.evaluate.return_value = result(ready=probability(0), focused=probability(0), blocked=probability(0),
        target={'confidence': 0, 'box': dict(left=0, top=0, right=1, bottom=1)})
    return a


def test_x_scrolled_explore_reselects_registered_tab_only_once(monkeypatch):
    a = search_agent(monkeypatch, 'X', ['app_ready', 'search_control'])
    out = asyncio.run(app_search.next_search_actions(a, {'app': 'X', 'query': 'IELTS'}))[0]
    assert out['action'] == 'tap' and out['phase'] == 'explore_scroll_top' and out['precise']
    assert (out['x'], out['y']) == (217, 926)
    a.steps.append(dict(out, act_s=.1))
    out = asyncio.run(app_search.next_search_actions(a, {'app': 'X', 'query': 'IELTS'}))[0]
    assert out['action'] == 'wait' and out.get('text') is None


def test_x_scroll_top_action_reaches_existing_precise_dispatch(monkeypatch):
    from hidagent.hybrid_agent import HybridAgent
    a = search_agent(monkeypatch, 'X', ['app_ready', 'search_control'])
    a.screen.live = True
    a.phone.tap = AsyncMock()
    out = asyncio.run(app_search.next_search_actions(a, {'app': 'X', 'query': 'IELTS'}))[0]
    runner = HybridAgent(a.phone, a.screen, a.client, NS(model='offline'), a.trace)
    asyncio.run(runner._do_impl(out))
    a.phone.tap.assert_awaited_once_with(.217, .926, aim=True)


@pytest.mark.parametrize('filename', ['a04-ig-post.jpg', 'b04-ig-post.jpg'])
def test_instagram_registered_old_results_establish_identity_without_reclassification(monkeypatch, filename):
    a = search_agent(monkeypatch, 'Instagram', [])
    a.screen.frame = (FRAMES / filename).read_bytes()
    with pytest.MonkeyPatch.context() as patch:
        launcher = AsyncMock(side_effect=AssertionError('Registered public Instagram identity must not relaunch'))
        patch.setattr(sw, 'next_shortcut_actions', launcher)
        out = asyncio.run(app_search.next_search_actions(a, {'app': 'Instagram', 'query': 'new query'}))[0]
    assert out['phase'] == 'app_ready' and out['action'] == 'wait'
    assert 'text' not in out
    assert 'ready' not in a.client.evaluate.call_args.args[1]
    assert 'blocked' in a.client.evaluate.call_args.args[1]


def test_local_query_and_results_misses_always_explain_gate(monkeypatch):
    a = agent(monkeypatch)
    a.steps[1].pop('act_s')
    assert asyncio.run(qt.verify_landscape_literal(a, {'app': 'YouTube', 'query': 'IELTS speaking'}, a.screen.frame)) is None
    assert any(c.kwargs.get('kind') == 'app-search-local-query-miss' and c.kwargs.get('reason') == 'input_context'
               for c in a.trace.write.call_args_list)
    monkeypatch.setenv('LANDSCAPE_RESULTS_LOCAL', '1')
    assert asyncio.run(qt.verify_landscape_results(a, {'app': 'YouTube', 'query': 'IELTS speaking'}, a.screen.frame)) is None
    assert any(c.kwargs.get('kind') == 'app-search-local-results-miss' and c.kwargs.get('reason') == 'input_context'
               for c in a.trace.write.call_args_list)


@pytest.mark.parametrize('query,broken', [('galleries', 'gallerie'), ('watch', 'watchl'), ('piece of', 'piec&of')])
def test_pixel_caret_detection_does_not_depend_on_truncated_ocr_row(monkeypatch, query, broken):
    # Native blue insertion bar is not reliably included in Vision's text box.
    # Its physical position must be found independently before OCR retry.
    a = agent(monkeypatch)
    im = Image.new('RGB', (1646, 1080), '#292929')
    d = ImageDraw.Draw(im)
    d.text((90, 67), query, font_size=22, fill='white')
    end = round(d.textbbox((90, 67), query, font_size=22)[2])
    d.rectangle((end+2, 67, end+3, 88), fill='#007aff')
    out = io.BytesIO(); im.save(out, format='PNG'); a.screen.frame = out.getvalue()
    async def read(data, *, mode):
        masked = Image.open(io.BytesIO(data)).getpixel(((end+2)*2, (67-32)*2)) == (41, 41, 41)
        return observation([row(query if masked else broken, left=90/1646, right=(end-12)/1646,
                                top=(71-32)/98, bottom=(88-32)/98)])
    a.query_text_reader.read = AsyncMock(side_effect=read)
    assert asyncio.run(qt.observe_landscape_query(a, {'query': query}, a.screen.frame))


def test_colored_letter_height_bar_cannot_be_erased_as_a_caret():
    crop = Image.new('RGB', (400, 98), '#292929')
    draw = ImageDraw.Draw(crop)
    draw.rectangle((30, 35, 60, 56), fill='white')
    draw.rectangle((65, 35, 66, 56), fill='#007aff')
    assert qt._mask_colored_caret(crop, [row('futurehabitsl', left=.075, right=.17, top=.35, bottom=.58)]) is None


def test_colored_bar_at_crop_boundary_is_a_miss():
    crop = Image.new('RGB', (400, 98), '#292929')
    draw = ImageDraw.Draw(crop)
    draw.rectangle((395, 20, 398, 48), fill='#007aff')
    assert qt._mask_colored_caret(crop, [row('watchl', left=.1, right=.98)]) is None


def test_portrait_local_query_miss_is_diagnosable(monkeypatch):
    a = agent(monkeypatch)
    a.query_text_reader = None
    assert asyncio.run(qt.verify_local_query(a, {'app': 'Instagram', 'query': 'IELTS'}, a.screen.frame)) is None
    assert any(c.kwargs.get('kind') == 'app-search-local-query-miss' and c.kwargs.get('reason')
               for c in a.trace.write.call_args_list)


def test_results_reuse_missing_proof_is_diagnosable(monkeypatch):
    a = agent(monkeypatch, submitted=True)
    assert asyncio.run(qt.reuse_landscape_results(a, {'app': 'YouTube', 'query': 'IELTS'}, a.screen.frame)) is None
    assert any(c.kwargs.get('kind') == 'app-search-local-results-miss' and c.kwargs.get('reason')
               for c in a.trace.write.call_args_list)


# Private real-device frames are deliberately excluded from the public snapshot.
pytestmark = pytest.mark.skip(reason="Requires private real-device capture excluded from the open-source snapshot; synthetic matcher tests run separately")
