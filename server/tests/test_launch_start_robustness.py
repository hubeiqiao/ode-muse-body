"""Offline baseline regressions. Fixtures retain only public launch/toolbar pixels.
All input and model clients are mocked. No private Spotlight pixels are uploaded.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from hidagent import shortcut_workflow as sw, reddit_web as rw
from hidagent.phone import Phone
from test_launch_receipt_fixes import fake, result, probability, image

FIXTURES = Path(__file__).parent / 'fixtures' / 'launch_robust'
GOAL = {'app': 'Douyin', 'text': None}


def spotlight(monkeypatch, *, launches=0, absent=1, ready=.98, queries=('douyin',)):
    a = fake(monkeypatch, 'Douyin', phases=())
    a.screen.frame = image()
    a.steps = [dict(workflow='keyboard', phase='query', action='type', text=q, act_s=.1) for q in queries]
    a.steps += [dict(workflow='keyboard', phase='launch', action='key', combo='enter', act_s=.1)] * launches
    async def evaluate(state, questions, frame):
        if 'page' in questions:
            return result(page={'choice': 'search', 'confidence': 1})
        return result(ready=probability(ready), no_installed_app=probability(absent))
    a.client.evaluate.side_effect = evaluate
    return a


@pytest.mark.parametrize('device,expected', [('ipad', ['esc', 'esc', 'cmd+h']), ('iphone', ['cmd+h'])])
def test_go_home_closes_spotlight_before_home_and_keeps_key_transition(device, expected):
    p = NS(profiles=object(), target_info=lambda: {'device': device}, key=AsyncMock())
    asyncio.run(Phone.go_home(p))
    assert [c.args[0] for c in p.key.await_args_list] == expected


def test_no_app_row_overrides_false_positive_and_tries_next_ascii_candidate(monkeypatch):
    a = spotlight(monkeypatch)
    out = asyncio.run(sw.next_shortcut_actions(a, GOAL))
    assert [(x['action'], x.get('combo'), x.get('text')) for x in out] == [('key', 'cmd+a', None), ('type', None, 'dou yin')]
    assert 'no_installed_app' in a.client.evaluate.call_args_list[-1].args[1]


@pytest.mark.parametrize('absent', [0, .98])
def test_alias_is_not_tried_without_new_strong_absence_evidence(monkeypatch, absent):
    a = spotlight(monkeypatch, absent=absent, ready=0)
    out = asyncio.run(sw.next_shortcut_actions(a, GOAL))
    assert all(x['action'] not in ('type', 'done') and x.get('combo') != 'enter' for x in out)


def test_candidates_are_bounded_and_no_return_when_none_has_app_row(monkeypatch):
    a = spotlight(monkeypatch, queries=('douyin', 'dou yin'))
    out = asyncio.run(sw.next_shortcut_actions(a, GOAL))
    assert all(x['action'] not in ('type', 'done') and x.get('combo') != 'enter' for x in out)


def test_no_app_after_return_never_repeats_return_or_query_or_claims_success(monkeypatch):
    a = spotlight(monkeypatch, launches=1)
    out = asyncio.run(sw.next_shortcut_actions(a, GOAL))
    assert all(x['action'] not in ('type', 'done') and x.get('combo') != 'enter' for x in out)


def test_uncertain_query_receipt_cannot_authorize_an_alias(monkeypatch):
    a = spotlight(monkeypatch);a.steps[-1].pop('act_s')
    out = asyncio.run(sw.next_shortcut_actions(a, GOAL))
    assert all(x['action'] != 'type' and x.get('combo') != 'enter' for x in out)


def reddit(monkeypatch, *, typed=False):
    a = fake(monkeypatch, phases=())
    # A patterned synthetic toolbar provides nonuniform stability evidence.
    import io
    from PIL import Image, ImageDraw
    picture = Image.new('RGB', (1646,1080), 'white')
    draw = ImageDraw.Draw(picture)
    for x in range(20, 1600, 40):
        draw.rectangle((x,120,x+16,180), fill='black')
    output = io.BytesIO(); picture.save(output, format='PNG')
    a.screen.frame = output.getvalue()
    a.steps = [dict(workflow='reddit_web', phase=p, action='key', combo=k, act_s=.1)
               for p, k in [('home', 'cmd+h'), ('chrome_ready', None), ('new_tab', 'cmd+t'), ('url_focus', 'cmd+l')]]
    if typed:
        a.steps.append(dict(workflow='reddit_web', phase='url_type', action='type', text=rw.URL+'?q=IELTS#esp32', local_url_probe=True, act_s=.1))
    a.client.evaluate.side_effect = None
    a.client.evaluate.return_value = result(ready=probability(0), literal=probability(1), blocked=probability(0))
    a.query_text_reader = NS(read=AsyncMock(return_value={'rows': [dict(text=rw.URL+'?q=IELTS#esp32', confidence=1,
                      box={'left': .2, 'top': .2, 'right': .8, 'bottom': .8})]}))
    return a


def test_landscape_ack_cannot_type_when_passive_omnibox_focus_model_rejects(monkeypatch):
    a = reddit(monkeypatch)
    out = asyncio.run(rw.next_reddit_web_actions(a, {'query': 'IELTS'}))[0]
    assert out['action'] == 'wait' and 'text' not in out


@pytest.mark.parametrize('mutation', ['missing_ack', 'intervening_key', 'blocked'])
def test_url_probe_requires_uninterrupted_focus_ack_and_unblocked_chrome(monkeypatch, mutation):
    a = reddit(monkeypatch)
    if mutation == 'missing_ack': a.steps[-1].pop('act_s')
    elif mutation == 'intervening_key': a.steps.append(dict(action='key', combo='esc', workflow='reddit_web', phase='other', act_s=.1))
    else: a.client.evaluate.return_value['answers']['blocked'] = probability(1)
    out = asyncio.run(rw.next_reddit_web_actions(a, {'query': 'IELTS'}))[0]
    assert out['action'] == 'wait'


def test_local_probe_submits_only_after_exact_ocr_and_never_retypes(monkeypatch):
    a = reddit(monkeypatch, typed=True)
    out = asyncio.run(rw.next_reddit_web_actions(a, {'query': 'IELTS'}))[0]
    assert (out['action'], out.get('combo'), out['phase']) == ('key', 'enter', 'url_submit')
    a.query_text_reader.read.assert_awaited_once_with(rw.address_band(a.screen.frame), mode='fast')


@pytest.mark.parametrize('failure', ['wrong_url', 'empty', 'suggestion', 'low_confidence', 'lost_type_ack', 'unavailable', 'changed_frame'])
def test_local_probe_never_submits_uncertain_text_or_retypes(monkeypatch, failure):
    a = reddit(monkeypatch, typed=True);row = a.query_text_reader.read.return_value['rows'][0]
    if failure == 'wrong_url': row['text'] += 'old'
    if failure == 'empty': a.query_text_reader.read.return_value['rows'] = []
    if failure == 'suggestion': row['box'].update(top=1.1, bottom=1.2)
    if failure == 'low_confidence': row['confidence'] = .5
    if failure == 'lost_type_ack': a.steps[-1].pop('act_s')
    if failure == 'unavailable':
        from hidagent.local_text import LocalTextError
        a.query_text_reader.read.side_effect = LocalTextError('offline OCR unavailable')
    if failure == 'changed_frame':
        async def changed(*args, **kw):
            a.screen.frame = image(color='black');return {'rows': [row]}
        a.query_text_reader.read.side_effect = changed
    out = asyncio.run(rw.next_reddit_web_actions(a, {'query': 'IELTS'}))[0]
    assert out['action'] == 'wait' and 'text' not in out and out.get('combo') != 'enter'


def test_spotlight_privacy_refusal_precedes_new_checks_and_input(monkeypatch):
    a = spotlight(monkeypatch)
    monkeypatch.setattr('hidagent.spotlight_launch.prepare', AsyncMock(return_value='fallback_keyboard'))
    monkeypatch.setattr('hidagent.spotlight_launch.safe_frame', lambda *args: False)
    from hidagent.decision import DecisionUnavailable
    with pytest.raises(DecisionUnavailable, match='privacy'): asyncio.run(sw.next_shortcut_actions(a, GOAL))
    a.client.evaluate.assert_not_awaited()


def test_baseline_reddit_zero_focus_and_empty_verdict_cannot_probe(monkeypatch):
    a = reddit(monkeypatch)
    a.client.evaluate.return_value = result(ready=probability(0), literal=probability(0), blocked=probability(0))
    out = asyncio.run(rw.next_reddit_web_actions(a, {'query': 'IELTS'}))[0]
    assert out['action'] == 'wait' and 'text' not in out


def test_alias_result_question_uses_the_current_ascii_query(monkeypatch):
    monkeypatch.setenv('SHORTCUT_MERGE_APP_RESULT', '1')
    a = spotlight(monkeypatch, queries=('douyin', 'dou yin'), ready=1, absent=0)
    asyncio.run(sw.next_shortcut_actions(a, GOAL))
    question = a.client.evaluate.call_args_list[0].args[1]['installed_app']['instructions']
    assert "query 'dou yin'" in question


def test_local_probe_after_submit_checks_actual_page_without_address_keys(monkeypatch):
    a = reddit(monkeypatch, typed=True)
    a.steps.append(dict(workflow='reddit_web', phase='url_submit', action='key', combo='enter', act_s=.1))
    a.client.evaluate.return_value = result(ready=probability(1), blocked=probability(0))
    out = asyncio.run(rw.next_reddit_web_actions(a, {'query': 'IELTS'}))[0]
    assert out['action'] == 'done' and out['_verified_public_results']
    a.query_text_reader.read.assert_not_awaited()


def test_local_probe_ocr_does_not_authorize_return_after_focus_lost(monkeypatch):
    a = reddit(monkeypatch, typed=True)
    a.steps.append(dict(workflow='reddit_web', phase='other', action='key', combo='esc', act_s=.1))
    out = asyncio.run(rw.next_reddit_web_actions(a, {'query': 'IELTS'}))[0]
    assert out['action'] == 'wait'
