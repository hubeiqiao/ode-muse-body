"""Caret removal changes pixels, never the exact literal comparison."""
import asyncio
import io
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageDraw, ImageFont

from hidagent import app_query_text as m
from test_landscape_query_ocr import agent, observation, row


def editor(query, *, color='white', background='#292929', caret=True, inside=False):
    image = Image.new('RGB', (1646, 1080), background)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    bounds = draw.textbbox((90, 65), query, font=font)
    draw.text((90, 65), query, font=font, fill='black' if background == 'white' else 'white')
    x = bounds[2] + 2 if not inside else (bounds[0] + bounds[2]) // 2
    caret_box = (x, bounds[1] - 5, x + 2, bounds[3] + 5)
    if caret:
        draw.rectangle(caret_box, fill=color)
    output = io.BytesIO(); image.save(output, format='PNG')
    region = m._header_region(image)
    text_row = row(query, left=bounds[0]/1646, right=bounds[2]/1646,
                   top=(bounds[1]-region[1])/98, bottom=(bounds[3]-region[1])/98)
    return output.getvalue(), text_row, caret_box


@pytest.mark.parametrize('query,broken', [
    ('a memorable journey', 'a memorable journe'),
    ('weekend activities band 8', 'weekend activities band 4'),
    ('education answers', 'education answer'),
    ('a person who inspires you', 'a person who inspires yo'),
    ('music and hobbies tips', "music and hobbies tips'"),
    ('daily routine', 'daily routine:'),
    ('band 7 ideas', "band 7 ideas';"),
    ('why?', 'why'),
])
@pytest.mark.parametrize('color,background', [('white', '#292929'), ('#ff2442', '#292929'), ('#007aff', 'white')])
def test_detected_caret_is_masked_before_exact_ocr_retry(monkeypatch, query, broken, color, background):
    a = agent(monkeypatch)
    a.screen.frame, text_row, box = editor(query, color=color, background=background)
    original = Image.open(io.BytesIO(a.screen.frame)).crop(m._header_region(Image.open(io.BytesIO(a.screen.frame))))
    async def read(data, *, mode):
        image = Image.open(io.BytesIO(data))
        x, y = (box[0]+1)*2, (box[1]+1-32)*2
        masked = image.getpixel((x, y)) == Image.new('RGB', (1, 1), background).getpixel((0, 0))
        return observation([{**text_row, 'text': query if masked else broken}])
    a.query_text_reader.read = AsyncMock(side_effect=read)
    result = asyncio.run(m.observe_landscape_query(a, {'query': query}, a.screen.frame))
    assert result
    assert a.query_text_reader.read.await_count == 2
    assert result['caret_masked'] is True
    # Mask only the bar; neighboring last-character pixels remain identical.
    retry = Image.open(io.BytesIO(a.query_text_reader.read.call_args.args[0]))
    expected = original.resize(retry.size)
    unchanged = (0, 0, (box[0]-4)*2, 196)
    assert retry.crop(unchanged).tobytes() == expected.crop(unchanged).tobytes()


@pytest.mark.parametrize('broken', ['journe', 'journey extra', 'journey:', 'journey;', "journey'", 'journye', 'journey 4'])
def test_masked_retry_still_requires_the_entire_exact_query(monkeypatch, broken):
    a = agent(monkeypatch)
    a.screen.frame, text_row, _ = editor('journey')
    a.query_text_reader.read.return_value = observation([{**text_row, 'text': broken}])
    assert asyncio.run(m.observe_landscape_query(a, {'query': 'journey'}, a.screen.frame)) is None
    assert a.query_text_reader.read.await_count == 2


@pytest.mark.parametrize('query', ['journey', 'final l', 'final I', 'final 1', 'final |', 'why?', 'tips:', "tips'"])
def test_no_caret_means_no_pixel_edits_or_punctuation_stripping(monkeypatch, query):
    a = agent(monkeypatch)
    a.screen.frame, text_row, _ = editor(query, caret=False)
    a.query_text_reader.read.return_value = observation([text_row])
    assert asyncio.run(m.observe_landscape_query(a, {'query': query[:-1]}, a.screen.frame)) is None
    assert a.query_text_reader.read.await_count == 1


def test_bar_inside_text_is_not_erased_to_hide_a_literal_error(monkeypatch):
    a = agent(monkeypatch)
    a.screen.frame, text_row, _ = editor('journey extra', inside=True)
    a.query_text_reader.read.return_value = observation([text_row])
    assert asyncio.run(m.observe_landscape_query(a, {'query': 'journey'}, a.screen.frame)) is None
    assert a.query_text_reader.read.await_count == 1


def test_retry_does_not_hide_extra_rows(monkeypatch):
    a = agent(monkeypatch)
    a.screen.frame, text_row, _ = editor('journey')
    a.query_text_reader.read.side_effect = [observation([{**text_row, 'text': 'journe'}]),
        observation([text_row, row('extra')])]
    assert asyncio.run(m.observe_landscape_query(a, {'query': 'journey'}, a.screen.frame)) is None
    assert a.query_text_reader.read.await_count == 2


def test_caret_can_be_inside_ocr_box_and_jpeg_compressed(monkeypatch):
    a = agent(monkeypatch)
    data, text_row, box = editor('journey', color='#ff2442')
    output = io.BytesIO(); Image.open(io.BytesIO(data)).save(output, format='JPEG', quality=90)
    a.screen.frame = output.getvalue()
    text_row['box'].update(right=(box[2]+2)/1646, top=(box[1]-32)/98, bottom=(box[3]+1-32)/98)
    a.query_text_reader.read.side_effect = [observation([{**text_row, 'text': 'journe;'}]), observation([{**text_row, 'text': 'journey'}])]
    assert asyncio.run(m.observe_landscape_query(a, {'query': 'journey'}, a.screen.frame))
    assert a.query_text_reader.read.await_count == 2


@pytest.mark.parametrize('case', ['wrong_mode', 'wrong_source', 'timeout', 'invalid_box', 'invalid_confidence'])
def test_masked_retry_retains_observation_validation(monkeypatch, case):
    from hidagent.local_text import LocalTextError
    a = agent(monkeypatch)
    a.screen.frame, text_row, _ = editor('journey')
    after = observation([text_row])
    if case == 'wrong_mode': after['mode'] = 'accurate'
    if case == 'wrong_source': after['source'] = 'remote'
    if case == 'invalid_box': after['rows'][0]['box']['left'] = -1
    if case == 'invalid_confidence': after['rows'][0]['confidence'] = float('nan')
    if case == 'timeout': after = LocalTextError('unavailable')
    before = observation([{**text_row, 'box': dict(text_row['box']), 'text': 'journe'}])
    # Keep the first observation valid when testing invalid retry boxes.
    before['rows'][0]['box']['left'] = 90/1646
    before['rows'][0]['confidence'] = .5
    a.query_text_reader.read.side_effect = [before, after]
    assert asyncio.run(m.observe_landscape_query(a, {'query': 'journey'}, a.screen.frame)) is None
    assert a.query_text_reader.read.await_count == 2


def test_masked_literal_reaches_existing_owner_focus_judge_with_original_frame(monkeypatch):
    from hidagent.app_search import next_search_actions
    from test_landscape_query_ocr import GOAL
    a = agent(monkeypatch)
    a.screen.frame, text_row, _ = editor(GOAL['query'])
    a.query_text_reader.read.side_effect = [observation([{**text_row, 'text': 'IELTS speakin'}]), observation([text_row])]
    result = asyncio.run(next_search_actions(a, GOAL))[0]
    assert result['phase'] == 'submit'
    assert a.query_text_reader.read.await_count == 2
    _, questions, sent_frame = a.client.evaluate.call_args.args
    assert sent_frame == a.screen.frame
    assert 'focused' in questions['ready']['instructions']
    assert 'blocked' in questions and 'literal' not in questions


@pytest.mark.parametrize('caret', [False, True])
def test_digit_substitution_is_not_a_confusable_even_after_mask(monkeypatch, caret):
    a = agent(monkeypatch)
    a.screen.frame, text_row, _ = editor('band 4', caret=caret)
    a.query_text_reader.read.return_value = observation([text_row])
    assert asyncio.run(m.observe_landscape_query(a, {'query': 'band 8'}, a.screen.frame)) is None
    assert a.query_text_reader.read.await_count == (2 if caret else 1)
