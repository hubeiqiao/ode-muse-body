"""Offline intermittent-motion regressions with synthetic grid-aligned frames."""
import io
import time
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from hidagent import scene_scope
from hidagent.jev_agent import scene_changed


BOX = {'left': .84, 'top': .02, 'right': .94, 'bottom': .08}
ACTION = {'action': 'tap', 'x': 890, 'y': 50}
INTERIOR = [y * 32 + x for y in range(3, 21) for x in range(32)]


def frame(levels):
    image = Image.new('L', (256, 192), 30)
    draw = ImageDraw.Draw(image)
    for cell, level in levels.items():
        x, y = cell % 32, cell // 32
        draw.rectangle((x * 8, y * 8, x * 8 + 7, y * 8 + 7), fill=level)
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def motion_frames(count=91):
    # Match the probe's 56-cell intersection and 91-cell union. The remaining
    # cells change only in the first or second pair, like captions or a face.
    common = INTERIOR[:56]
    once = INTERIOR[56:count]
    first, second = once[::2], once[1::2]
    samples = [
        frame({}),
        frame(dict.fromkeys(common, 90) | dict.fromkeys(first, 120)),
        frame(dict.fromkeys(common, 150) | dict.fromkeys(once, 120)),
    ]
    after = frame(dict.fromkeys(common, 210) | dict.fromkeys(first + second, 180))
    return samples, after


def check(monkeypatch, source, samples, after, *, box=BOX, action=ACTION):
    now = time.time()
    before = samples[-1]
    agent = SimpleNamespace(steps=[], shots={})
    if source == 'history':
        agent.steps = [
            {'workflow': 'app_search', 'phase': 'scene_changed',
             'action': 'wait', 't': now - 2 + i}
            for i in range(2)
        ]
        agent.shots = dict(enumerate(samples[:2]))
        monkeypatch.setattr(scene_scope, '_receiving_motion',
                            lambda agent: pytest.fail('Usable history must not sample again'))
    else:
        monkeypatch.setattr(scene_scope, '_receiving_motion', lambda agent: samples)
    return scene_scope.verdict_scene_changed(
        agent, before, after, action, scene_changed, box=box, workflow='app_search')


@pytest.mark.parametrize('source', ['history', 'fresh'])
@pytest.mark.parametrize('count', [91, 499])
def test_intermittent_motion_allows_stable_top_right_target(monkeypatch, source, count):
    samples, after = motion_frames(count)
    assert scene_changed(samples[-1], after)
    assert not check(monkeypatch, source, samples, after)


@pytest.mark.parametrize('source', ['history', 'fresh'])
def test_target_overlapping_once_moving_cell_waits(monkeypatch, source):
    target, other = 12 * 32 + 16, 8 * 32 + 2
    samples = [frame({}), frame({target: 120}), frame({target: 120, other: 120})]
    # The target changes only in the first pair and is stable at verdict time.
    box = {'left': .505, 'top': .505, 'right': .525, 'bottom': .535}
    action = {'action': 'tap', 'x': 515, 'y': 520}
    assert check(monkeypatch, source, samples, samples[-1], box=box, action=action)


@pytest.mark.parametrize('source', ['history', 'fresh'])
def test_massive_intermittent_union_waits(monkeypatch, source):
    # Each pair changes less than 65%, but their 500/768 union exceeds it.
    samples, after = motion_frames(500)
    assert scene_changed(samples[-1], after)
    assert check(monkeypatch, source, samples, after)


@pytest.mark.parametrize('source', ['history', 'fresh'])
@pytest.mark.parametrize('cell', [0, 23 * 32, 15 * 32 + 20])
def test_changes_outside_sampled_motion_still_wait(monkeypatch, source, cell):
    samples, after = motion_frames()
    image = Image.open(io.BytesIO(after))
    x, y = cell % 32, cell // 32
    ImageDraw.Draw(image).rectangle((x * 8, y * 8, x * 8 + 7, y * 8 + 7), fill=240)
    out = io.BytesIO()
    image.save(out, format='PNG')
    assert check(monkeypatch, source, samples, out.getvalue())


def test_fresh_transient_chrome_still_waits(monkeypatch):
    samples, after = motion_frames()
    # This top-chrome overlay disappears before the last sample and verdict.
    image = Image.open(io.BytesIO(samples[1]))
    ImageDraw.Draw(image).rectangle((0, 0, 7, 7), fill=240)
    out = io.BytesIO()
    image.save(out, format='PNG')
    samples[1] = out.getvalue()
    assert check(monkeypatch, 'fresh', samples, after)


@pytest.mark.parametrize('source', ['history', 'fresh'])
@pytest.mark.parametrize('kind', ['type', 'key'])
def test_keyboard_actions_keep_original_gate(monkeypatch, source, kind):
    samples, after = motion_frames()
    assert check(monkeypatch, source, samples, after, action={'action': kind})
