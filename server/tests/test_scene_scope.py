"""Offline regressions for verdicts on continuously changing content."""
import asyncio
import io
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image, ImageDraw

from hidagent.app_search import next_search_actions
from hidagent.shortcut_workflow import next_shortcut_actions
from hidagent.decision import DecisionUnavailable
from hidagent.jev_agent import scene_changed
from hidagent.scene_scope import verdict_scene_changed


BOX = {'left': .82, 'top': .08, 'right': .92, 'bottom': .18}


def picture(level, change=None):
    image = Image.new('RGB', (320, 240), '#303030')
    draw = ImageDraw.Draw(image)
    draw.rectangle((32, 60, 255, 179), fill=(level, level, level))
    draw.rectangle((264, 20, 292, 42), outline='white', width=3)
    draw.rectangle((32, 220, 288, 226), fill='white')
    if change == 'target':
        draw.rectangle((264, 20, 292, 42), fill='white')
    elif change == 'margin':
        draw.rectangle((294, 20, 300, 42), fill='white')
    elif change == 'chrome':
        draw.rectangle((0, 0, 319, 45), fill='white')
    elif change == 'large':
        draw.rectangle((0, 0, 319, 239), fill=(level, level, level))
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def make_agent(workflow, before, after, *, prior=True):
    now = time.time()
    phase = 'changed_scene' if workflow == 'keyboard' else 'scene_changed'
    steps = []
    shots = {}
    if workflow == 'app_search':
        steps.append({'workflow': workflow, 'phase': 'app_ready', 'action': 'wait', 't': now-4})
    if prior:
        for i, level in enumerate((40, 100)):
            shots[len(steps)] = picture(level)
            steps.append({'workflow': workflow, 'phase': phase, 'action': 'wait', 't': now-3+i})
    screen = SimpleNamespace(frame=before, updated=now)

    async def evaluate(state, questions, frame):
        screen.frame = after
        answers = {
            'page': {'choice': 'app', 'confidence': .99},
            'ready': {'noul': 1}, 'focused': {'noul': 0}, 'empty': {'noul': 0},
            'blocked': {'noul': 0}, 'target': {'confidence': .99, 'box': BOX},
            'loading': {'noul': 0},
        }
        return {'answers': answers, 'seconds': .1}

    return SimpleNamespace(screen=screen, steps=steps, shots=shots,
                           client=SimpleNamespace(evaluate=AsyncMock(side_effect=evaluate)), trace=Mock())


@pytest.mark.parametrize('workflow', ['keyboard', 'app_search'])
def test_autoplay_does_not_invalidate_unchanged_verdict(workflow):
    a = make_agent(workflow, picture(160), picture(220))
    if workflow == 'keyboard':
        result = asyncio.run(next_shortcut_actions(a, {'app': 'YouTube', 'text': None}))[0]
        assert result['action'] == 'done'
    else:
        result = asyncio.run(next_search_actions(a, {'app': 'YouTube', 'query': 'English practice'}))[0]
        assert result['action'] == 'tap'


@pytest.mark.parametrize('change', ['target', 'margin', 'chrome'])
def test_search_still_waits_for_changed_target_or_chrome(change):
    a = make_agent('app_search', picture(160), picture(220, change))
    with pytest.raises(DecisionUnavailable, match='could not verify scene_changed'):
        asyncio.run(next_search_actions(a, {'app': 'YouTube', 'query': 'English practice'}))


def test_foreground_still_waits_for_overlay_covering_chrome():
    a = make_agent('keyboard', picture(160), picture(220, 'chrome'))
    result = asyncio.run(next_shortcut_actions(a, {'app': 'YouTube', 'text': None}))[0]
    assert result['phase'] == 'changed_scene'


@pytest.mark.parametrize('prior', [False, True])
def test_keyboard_input_stays_conservative(prior):
    a = make_agent('app_search', picture(160), picture(220), prior=prior)
    a.steps.append({'workflow': 'app_search', 'phase': 'focus', 'action': 'tap', 'act_s': .1})
    if prior:
        with pytest.raises(DecisionUnavailable, match='could not verify scene_changed'):
            asyncio.run(next_search_actions(a, {'app': 'YouTube', 'query': 'English practice'}))
    else:
        result = asyncio.run(next_search_actions(a, {'app': 'YouTube', 'query': 'English practice'}))[0]
        assert result['phase'] == 'scene_changed'


def test_missing_motion_history_stays_conservative():
    a = make_agent('keyboard', picture(160), picture(220), prior=False)
    result = asyncio.run(next_shortcut_actions(a, {'app': 'YouTube', 'text': None}))[0]
    assert result['phase'] == 'changed_scene'


def test_small_target_change_cannot_hide_in_full_frame_average():
    before, after = picture(160), picture(160, 'target')
    assert not scene_changed(before, after)
    a = make_agent('app_search', before, after)
    action = {'action': 'tap', 'x': 870, 'y': 130}
    assert verdict_scene_changed(a, before, after, action, scene_changed, box=BOX)


def test_small_chrome_overlay_cannot_hide_in_full_frame_average():
    before = picture(160)
    image = Image.open(io.BytesIO(before))
    ImageDraw.Draw(image).rectangle((0, 10, 18, 24), fill='white')
    out = io.BytesIO(); image.save(out, format='PNG'); after = out.getvalue()
    assert not scene_changed(before, after)
    a = make_agent('keyboard', before, after)
    assert verdict_scene_changed(a, before, after, {'action': 'done'}, scene_changed, foreground=True)


def test_editable_search_target_uses_its_verdict_box():
    a = make_agent('app_search', picture(160), picture(220))
    a.steps.insert(1, {'workflow': 'app_search', 'phase': 'search_control', 'action': 'tap'})
    a.shots = {i+1: shot for i, shot in a.shots.items()}
    result = asyncio.run(next_search_actions(a, {'app': 'YouTube', 'query': 'English practice'}))[0]
    assert result['action'] == 'tap' and result['phase'] == 'focus'


@pytest.mark.parametrize('bad_history', ['stale', 'duplicate', 'navigation', 'missing', 'other_workflow'])
def test_unusable_history_cannot_exclude_motion(bad_history):
    before, after = picture(160), picture(220)
    a = make_agent('keyboard', before, after)
    if bad_history == 'stale':
        a.steps[0]['t'] -= 60
    elif bad_history == 'duplicate':
        a.shots[0] = a.shots[1]
    elif bad_history == 'navigation':
        a.steps[0]['action'] = 'tap'
    elif bad_history == 'missing':
        a.shots.clear()
    else:
        a.steps[0].update(workflow='app_search', phase='scene_changed')
    assert verdict_scene_changed(a, before, after, {'action': 'done'}, scene_changed, foreground=True)


def test_target_overlapping_historical_motion_is_conservative():
    before, after = picture(160), picture(220)
    a = make_agent('app_search', before, after)
    # The video has stopped now, but its historical motion cannot be excluded
    # when a verdict depends on a target inside it.
    box = {'left': .2, 'top': .3, 'right': .3, 'bottom': .4}
    assert verdict_scene_changed(a, before, before, {'action': 'tap', 'x': 250, 'y': 350},
                                 lambda *args: True, box=box)


def test_excessive_excluded_area_keeps_original_gate():
    before, after = picture(160, 'large'), picture(220, 'large')
    a = make_agent('keyboard', before, after)
    a.shots = {0: picture(40, 'large'), 1: picture(100, 'large')}
    assert verdict_scene_changed(a, before, after, {'action': 'done'}, scene_changed, foreground=True)


def test_motion_history_does_not_cross_workflow_handoff():
    a = make_agent('app_search', picture(160), picture(220))
    result = asyncio.run(next_shortcut_actions(a, {'app': 'YouTube', 'text': None}))[0]
    assert result['action'] == 'wait' and result['phase'] == 'changed_scene'


# A verified app entry keeps the legacy exemption: 'done' only reports the app
# is foreground, and every later search step re-checks its own target/chrome.
@pytest.mark.parametrize('change,expected', [(None, 'done'), ('chrome', 'done')])
def test_verified_icon_entry_keeps_legacy_exemption(change, expected):
    a = make_agent('keyboard', picture(160), picture(220, change))
    a.steps.insert(0, {'workflow': 'keyboard', 'phase': 'icon_launch', 'action': 'tap'})
    a.shots = {i+1: shot for i, shot in a.shots.items()}
    a.phone = SimpleNamespace(target_info=lambda: {'device': 'ipad'})
    result = asyncio.run(next_shortcut_actions(a, {'app': 'YouTube', 'text': None}))[0]
    assert result['action'] == expected


@pytest.mark.parametrize('edge', ['top', 'bottom'])
def test_chrome_bands_cannot_be_learned_as_autoplay(edge):
    def with_chrome(level):
        image = Image.open(io.BytesIO(picture(level)))
        band = (0, 0, 319, 28) if edge == 'top' else (0, 211, 319, 239)
        ImageDraw.Draw(image).rectangle(band, fill=(level, level, level))
        out = io.BytesIO(); image.save(out, format='PNG'); return out.getvalue()
    before, after = with_chrome(160), with_chrome(220)
    a = make_agent('keyboard', before, after)
    a.shots = {0: with_chrome(40), 1: with_chrome(100)}
    assert verdict_scene_changed(a, before, after, {'action': 'done'}, scene_changed,
                                 foreground=True, workflow='keyboard')


@pytest.mark.parametrize('invalid', ['resized', 'broken', 'missing_box'])
def test_invalid_geometry_or_evidence_waits(invalid):
    before, after = picture(160), picture(220)
    a = make_agent('app_search', before, after)
    if invalid == 'resized':
        out = io.BytesIO(); Image.new('RGB', (240, 320)).save(out, format='PNG'); after = out.getvalue()
    elif invalid == 'broken':
        after = b'not an image'
    assert verdict_scene_changed(a, before, after, {'action': 'tap', 'x': 870, 'y': 130},
                                 scene_changed, box=None if invalid == 'missing_box' else BOX)
