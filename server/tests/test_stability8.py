"""Offline first-tap regressions with a deterministic 8 fps frame stream."""
from types import SimpleNamespace

import pytest

from hidagent import scene_scope
from hidagent.jev_agent import scene_changed
from test_scene_scope import BOX, make_agent, picture


ACTION = {'action': 'tap', 'x': 870, 'y': 130}


def stream(monkeypatch, *, history='missing', frames=None):
    before, after = picture(40), picture(100)
    agent = make_agent('app_search', before, after, prior=False)
    if history == 'missing':
        agent.steps = []
    else:
        agent = make_agent('app_search', before, after)
        for step in agent.steps:
            step['t'] -= 60
    agent.screen.frame = after
    start = agent.screen.updated
    frames = frames if frames is not None else [picture(n) for n in (160, 220, 250)]
    clock = SimpleNamespace(elapsed=0., delivered=0, sleeps=0)

    def sleep(seconds):
        clock.sleeps += 1
        clock.elapsed += seconds
        index = int(clock.elapsed / .125)
        if index > clock.delivered and index <= len(frames):
            agent.screen.frame = frames[index - 1]
            agent.screen.updated = start + index * .125
            clock.delivered = index

    monkeypatch.setattr(scene_scope, 'time', SimpleNamespace(
        time=lambda: start + clock.elapsed, monotonic=lambda: clock.elapsed, sleep=sleep))
    return agent, before, after, clock


@pytest.mark.parametrize('history', ['missing', 'stale'])
def test_first_tap_allows_stable_magnifier_over_live_autoplay(monkeypatch, history):
    agent, before, after, clock = stream(monkeypatch, history=history)
    assert scene_changed(before, after)
    assert not scene_scope.verdict_scene_changed(
        agent, before, after, ACTION, scene_changed, box=BOX, workflow='app_search')
    assert clock.delivered == 3
    assert .375 <= clock.elapsed <= .5
    assert not hasattr(agent, '_search_motion')


@pytest.mark.parametrize('change', ['target', 'margin', 'chrome'])
@pytest.mark.parametrize('when', ['current', 'middle_sample', 'last_sample'])
def test_first_tap_waits_for_target_margin_or_chrome_change(monkeypatch, change, when):
    frames = [picture(n, change if when == 'middle_sample' and n == 220
                      or when == 'last_sample' and n == 250 else None)
              for n in (160, 220, 250)]
    agent, before, after, _ = stream(monkeypatch, frames=frames)
    if when == 'current':
        after = picture(100, change)
        agent.screen.frame = after
    assert scene_scope.verdict_scene_changed(
        agent, before, after, ACTION, scene_changed, box=BOX, workflow='app_search')


def test_first_tap_waits_if_motion_cells_overlap_an_unchanged_target(monkeypatch):
    agent, before, after, _ = stream(monkeypatch)
    # The target crop has not changed, but shares a grid cell with the video.
    box = {'left': .825, 'top': .3, 'right': .84, 'bottom': .4}
    assert scene_scope.verdict_scene_changed(
        agent, before, after, {'action': 'tap', 'x': 832, 'y': 350},
        scene_changed, box=box, workflow='app_search')


def test_first_tap_waits_for_excessive_motion_area(monkeypatch):
    def large(level):
        import io
        from PIL import Image, ImageDraw
        image = Image.open(io.BytesIO(picture(level)))
        ImageDraw.Draw(image).rectangle((0, 48, 319, 209), fill=(level, level, level))
        out = io.BytesIO()
        image.save(out, format='PNG')
        return out.getvalue()

    agent, _, _, _ = stream(monkeypatch, frames=[large(n) for n in (160, 220, 250)])
    before, after = large(40), large(100)
    agent.screen.frame = after
    assert scene_scope.verdict_scene_changed(
        agent, before, after, ACTION, scene_changed, box=BOX, workflow='app_search')


@pytest.mark.parametrize('problem', ['stopped', 'one', 'two', 'duplicate', 'stale', 'resized', 'broken'])
def test_first_tap_waits_without_three_usable_fresh_frames(monkeypatch, problem):
    frames = [picture(n) for n in (160, 220, 250)]
    if problem == 'stopped':
        frames = []
    elif problem == 'one':
        frames = frames[:1]
    elif problem == 'two':
        frames = frames[:2]
    elif problem == 'duplicate':
        frames = [frames[0]] * 3
    elif problem == 'resized':
        import io
        from PIL import Image
        out = io.BytesIO()
        Image.new('RGB', (240, 320)).save(out, format='PNG')
        frames[1] = out.getvalue()
    elif problem == 'broken':
        frames[1] = b'not an image'
    agent, before, after, clock = stream(monkeypatch, frames=frames)
    if problem == 'stale':
        # Bytes may change without a fresh receiver receipt.
        sleep = scene_scope.time.sleep

        def stale_sleep(seconds):
            sleep(seconds)
            agent.screen.updated = scene_scope.time.time() - 60

        scene_scope.time.sleep = stale_sleep
    assert scene_scope.verdict_scene_changed(
        agent, before, after, ACTION, scene_changed, box=BOX, workflow='app_search')
    assert clock.elapsed <= .5 + 1e-9


@pytest.mark.parametrize('action,box', [
    ({'action': 'type', 'text': 'English practice'}, BOX),
    ({'action': 'key', 'combo': 'enter'}, BOX),
    ({'action': 'done'}, BOX),
    (ACTION, None),
    (ACTION, {'left': .1, 'top': .1, 'right': .2, 'bottom': .2}),
])
def test_non_taps_and_invalid_boxes_never_sample_live_motion(monkeypatch, action, box):
    agent, before, after, clock = stream(monkeypatch)
    assert scene_scope.verdict_scene_changed(
        agent, before, after, action, scene_changed, box=box, foreground=True,
        workflow='keyboard')
    assert clock.sleeps == 0


def test_usable_history_keeps_existing_path_without_live_sampling(monkeypatch):
    agent, before, after, clock = stream(monkeypatch)
    before, after = picture(160), picture(220)
    prior = make_agent('app_search', before, after)
    agent.steps, agent.shots = prior.steps, prior.shots
    # stream() freezes time just before this history was constructed.
    for step in agent.steps:
        step['t'] -= .1
    assert not scene_scope.verdict_scene_changed(
        agent, before, after, ACTION, scene_changed, box=BOX, workflow='app_search')
    assert clock.sleeps == 0
