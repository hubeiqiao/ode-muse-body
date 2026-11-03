"""Offline landscape OCR contracts. No model, device, or network calls."""
import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from PIL import Image, ImageDraw
from hidagent import app_query_text as m
from hidagent.app_search import next_search_actions, PublicSearchBlocked
from hidagent.local_text import LocalTextError

GOAL = {'app': 'YouTube', 'query': 'IELTS speaking'}

def frame(*, typed=True, results=False, size=(1646, 1080)):
    im = Image.new('RGB', size, '#171717')
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((65, 50, size[0]-24, 115), radius=20, fill='#303030')
    if typed: d.rectangle((90, 68, 360, 89), fill='white')
    if results: d.rectangle((20, 220, size[0]-20, 800), fill='#777777')
    out = io.BytesIO(); im.save(out, format='PNG'); return out.getvalue()

def row(text='IELTS speaking', confidence=.5, **box):
    return {'text': text, 'confidence': confidence,
            'box': dict(left=.05, top=.25, right=.4, bottom=.65) | box}

def observation(rows=None):
    return {'source': 'local_vision_ocr', 'is_accessibility_tree': False,
            'mode': 'fast', 'rows': [row()] if rows is None else rows, 'read_s': .01}

def agent(monkeypatch, *, submitted=False):
    monkeypatch.setenv('LANDSCAPE_QUERY_OCR', '1')
    a = SimpleNamespace(
        phone=SimpleNamespace(target_info=lambda: {'device': 'ipad', 'orientation': 'landscape'}),
        screen=SimpleNamespace(frame=frame(results=submitted)), trace=Mock(),
        query_text_reader=SimpleNamespace(read=AsyncMock(return_value=observation())),
        client=SimpleNamespace(evaluate=AsyncMock(return_value={
            'answers': {'ready': {'noul': 1}, 'results': {'noul': 1}, 'blocked': {'noul': 0}}, 'seconds': .1})),
        steps=[{'workflow': 'app_search', 'phase': 'app_ready', 'action': 'wait', 'act_s': .1},
               {'workflow': 'app_search', 'phase': 'query', 'action': 'type', 'act_s': .2,
                'text': GOAL['query'], '_search_was_empty_verified': True}], shots={1: frame(typed=False)})
    if submitted:
        a.steps.append({'workflow': 'app_search', 'phase': 'submit', 'action': 'key', 'combo': 'enter', 'act_s': .1})
        a.shots[2] = frame()
    return a

def observe(a): return asyncio.run(m.observe_landscape_query(a, GOAL, a.screen.frame))
def run(a, goal=GOAL): return asyncio.run(next_search_actions(a, goal))[0]

@pytest.mark.parametrize('text', ['IELTS speaking', '  ielts   SPEAKING  ', 'ielts\tspeaking'])
def test_exact_full_query_accepts_only_case_and_space_normalization(monkeypatch, text):
    a = agent(monkeypatch); a.query_text_reader.read.return_value = observation([row(text)])
    assert observe(a)
    crop = Image.open(io.BytesIO(a.query_text_reader.read.call_args.args[0]))
    assert crop.height < 1080 * .3 and crop.width <= 1646 * 3
    assert a.query_text_reader.read.call_args.kwargs == {'mode': 'fast'}

@pytest.mark.parametrize('rows', [[], [row('IELTS')], [row('IELTS speaking extra')],
    [row('IELT5 speaking')], [row('IELTSspeaking')],
    [row(confidence=float('nan'))], [row(confidence=True)],
    [row(), row('extra', left=.41, right=.5)], [row(), row()],
    [row(left=0, right=1)], [row(top=.9, bottom=1)]])
def test_ocr_mismatch_fragment_ambiguity_clipping_and_invalid_confidence_fall_back(monkeypatch, rows):
    a = agent(monkeypatch); a.query_text_reader.read.return_value = observation(rows)
    assert observe(a) is None


@pytest.mark.parametrize('query,text', [
    ('ielts speaking', 'lelts speaking'),
    *[('i love 101', f'{letter} l{zero}ve {letter}{zero}{letter}')
      for letter in ('i', 'l', '1', 'I', '|') for zero in ('o', '0', 'O')],
    ('  IELTS   speaking  ', '  lelts\tSPEAKING '),
])
def test_fixed_position_confusables_accept_native_half_confidence(monkeypatch, query, text):
    a = agent(monkeypatch)
    a.query_text_reader.read.return_value = observation([row(text, confidence=.5)])
    assert asyncio.run(m.observe_landscape_query(a, {'query': query}, a.screen.frame))


@pytest.mark.parametrize('text', [
    'elts speaking', 'iielts speaking', 'ietls speaking', 'ielts listening',
    'lelts speakin', 'lelts speakingg', 'lelts spekaing', 'lelts speeking',
])
def test_confusables_reject_missing_extra_swapped_and_different_words(monkeypatch, text):
    a = agent(monkeypatch)
    a.query_text_reader.read.return_value = observation([row(text)])
    assert observe(a) is None

@pytest.mark.parametrize('case', ['off', 'unset', 'iphone', 'portrait', 'size', 'corrupt', 'no_reader', 'mode_mismatch', 'wrong_source', 'timeout'])
def test_disabled_unsupported_or_failed_ocr_is_only_a_miss(monkeypatch, case):
    a = agent(monkeypatch)
    if case == 'off': monkeypatch.setenv('LANDSCAPE_QUERY_OCR', '0')
    if case == 'unset': monkeypatch.delenv('LANDSCAPE_QUERY_OCR')
    if case in ('iphone', 'portrait'):
        a.phone.target_info = lambda: {'device': 'iphone' if case == 'iphone' else 'ipad', 'orientation': 'portrait' if case == 'portrait' else 'landscape'}
    if case == 'size': a.screen.frame = frame(size=(1920, 1080))
    if case == 'corrupt': a.screen.frame = b'bad image'
    if case == 'no_reader':
        a.query_text_reader = None; monkeypatch.delenv('LOCAL_OCR_BIN', raising=False)
    if case == 'mode_mismatch': a.query_text_reader.read.return_value['mode'] = 'accurate'
    if case == 'wrong_source': a.query_text_reader.read.return_value['source'] = 'remote'
    if case == 'timeout': a.query_text_reader.read.side_effect = LocalTextError('unavailable')
    assert observe(a) is None

def test_standalone_flag_can_use_configured_local_binary_and_closes_owned_worker(monkeypatch):
    a = agent(monkeypatch); a.query_text_reader = None
    monkeypatch.setenv('LOCAL_OCR_BIN', '/offline/vision-ocr')
    worker = SimpleNamespace(read=AsyncMock(return_value=observation()), close=AsyncMock())
    constructor = Mock(return_value=worker); monkeypatch.setattr(m, 'LocalTextReader', constructor)
    assert observe(a)
    constructor.assert_called_once_with(['/offline/vision-ocr', '--serve']); worker.close.assert_awaited_once()

def test_literal_ocr_removes_only_text_question_keeps_fresh_owner_editor_modal_gate(monkeypatch):
    a = agent(monkeypatch); assert run(a)['phase'] == 'submit'
    state, questions, sent_frame = a.client.evaluate.call_args.args
    assert 'literal' not in questions and 'blocked' in questions
    assert 'exactly' not in questions['ready']['instructions'].lower()
    assert 'focused' in questions['ready']['instructions'].lower()
    assert state['policy'] == 'app_public_search' and sent_frame == a.screen.frame
    assert a.query_text_reader.read.await_count == 1

@pytest.mark.parametrize('ready,blocked', [(0, 0), (.98, 0), (1, 1)])
def test_matching_text_never_supplies_owner_focus_or_modal_permission(monkeypatch, ready, blocked):
    a = agent(monkeypatch); a.client.evaluate.return_value['answers'].update(ready={'noul': ready}, blocked={'noul': blocked})
    if blocked:
        with pytest.raises(PublicSearchBlocked): run(a)
    else: assert run(a)['action'] == 'wait'

@pytest.mark.parametrize('case', ['no_ack', 'wrong_input', 'no_focus', 'duplicate', 'intervening', 'same_pixels', 'no_shot'])
def test_literal_requires_current_run_owned_input_and_changed_search_pixels(monkeypatch, case):
    a = agent(monkeypatch)
    if case == 'no_ack': a.steps[1].pop('act_s')
    if case == 'wrong_input': a.steps[1]['text'] = 'other'
    if case == 'no_focus': a.steps[1].pop('_search_was_empty_verified')
    if case == 'duplicate': a.steps.append(dict(a.steps[1]))
    if case == 'intervening': a.steps.append({'workflow': 'other', 'action': 'tap', 'act_s': .1})
    if case == 'same_pixels': a.shots[1] = a.screen.frame
    if case == 'no_shot': a.shots.clear()
    run(a)
    assert 'exactly' in a.client.evaluate.call_args.args[1]['ready']['instructions'].lower()
    a.query_text_reader.read.assert_not_called()

def test_proven_selection_sequence_also_supports_literal_ocr(monkeypatch):
    a = agent(monkeypatch); a.steps[1].pop('_search_was_empty_verified')
    a.steps.insert(1, {'workflow': 'app_search', 'phase': 'select', 'action': 'key', '_search_focus_verified': True, 'act_s': .1})
    a.shots[2] = a.shots.pop(1); run(a)
    assert a.query_text_reader.read.await_count == 1
    assert 'exactly' not in a.client.evaluate.call_args.args[1]['ready']['instructions'].lower()

def test_results_never_skip_first_model_approval_then_reuse_same_task_pixels(monkeypatch):
    a = agent(monkeypatch, submitted=True); assert run(a)['action'] == 'done'
    a.client.evaluate.assert_awaited_once(); a.query_text_reader.read.assert_not_called()
    a.client.evaluate.reset_mock(); assert run(a)['action'] == 'done'
    a.client.evaluate.assert_not_called(); a.query_text_reader.read.assert_awaited_once()

@pytest.mark.parametrize('case', ['editor', 'different_results', 'new_task', 'new_query', 'no_submit_ack', 'no_query_ack', 'extra_input', 'wrong_ocr', 'off', 'missing_editor'])
def test_results_reuse_requires_same_task_approval_exact_query_and_changed_body(monkeypatch, case):
    a = agent(monkeypatch, submitted=True); run(a); a.client.evaluate.reset_mock()
    if case == 'editor': a.screen.frame = frame()
    if case == 'different_results': a.screen.frame = frame(typed=False, results=True)
    if case == 'new_task': a.steps = [dict(s) for s in a.steps]
    if case == 'new_query': a.steps[1]['text'] = 'another query'
    if case == 'no_submit_ack': a.steps[2].pop('act_s')
    if case == 'no_query_ack': a.steps[1].pop('act_s')
    if case == 'extra_input': a.steps.append({'workflow': 'other', 'action': 'tap', 'act_s': .1})
    if case == 'wrong_ocr': a.query_text_reader.read.return_value = observation([row('different')])
    if case == 'off': monkeypatch.setenv('LANDSCAPE_QUERY_OCR', '0')
    if case == 'missing_editor': a.shots.clear()
    run(a); a.client.evaluate.assert_awaited_once()

def test_query_only_changes_do_not_establish_results_body_transition(monkeypatch):
    a = agent(monkeypatch, submitted=True); a.shots[2] = frame(typed=False, results=True)
    run(a); a.client.evaluate.reset_mock(); run(a); a.client.evaluate.assert_awaited_once()

def test_unapproved_results_never_create_reuse_proof(monkeypatch):
    a = agent(monkeypatch, submitted=True); a.client.evaluate.return_value['answers']['results'] = {'noul': .62}
    assert run(a)['action'] == 'wait' and run(a)['action'] == 'wait'
    assert a.client.evaluate.await_count == 2; a.query_text_reader.read.assert_not_called()

@pytest.mark.parametrize('extra', [row("'", left=.405, right=.41, top=.17, bottom=.22),
                                  row('extra', left=.1, right=.3, top=.75, bottom=.85)])
def test_disjoint_punctuation_or_wrapped_extra_text_never_counts_as_exact_value(monkeypatch, extra):
    a = agent(monkeypatch)
    a.query_text_reader.read.return_value = observation([row(), extra])
    assert observe(a) is None
