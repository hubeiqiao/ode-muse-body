"""Offline synthetic regression cases; no device frames or device access."""
import json
import math
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from hidagent import vision

W, H = 710, 1080


def scene(before_center, after_center, *, bright=False, radius=8, extra=None):
    background, ink = (30, 220) if bright else (200, 40)
    frames = [Image.new('L', (W, H), background) for _ in range(2)]
    for frame, center in zip(frames, (before_center, after_center)):
        if center is not None:
            x, y = center
            ImageDraw.Draw(frame).ellipse((x-radius, y-radius, x+radius, y+radius), fill=ink)
    if extra:
        ImageDraw.Draw(frames[1]).ellipse(extra, fill=ink)
    return frames


def cases():
    yield 'hidden', scene(None, (370, 480)), dict(near=(.5, 480/H), shift=20/968)
    yield 'outside_roi', scene((355, 480), (506, 480)), dict(near=(.5, 480/H), shift=200/968)
    yield 'wrong_direction', scene((355, 480), (317, 480)), dict(near=(.5, 480/H), shift=50/968)
    yield 'wrong_magnitude', scene((355, 480), (400, 480)), dict(near=(.5, 480/H), shift=20/968)
    yield 'off_axis', scene((355, 480), (392, 492)), dict(near=(.5, 480/H), shift=50/968)
    yield 'three_blobs', scene((355, 480), (392, 480), extra=(408, 494, 424, 510)), dict(near=(.5, 480/H), shift=50/968)
    yield 'unequal_blobs', scene((355, 480), (392, 480), radius=8), dict(near=(.5, 480/H), shift=50/968)
    for axis in ('x', 'y'):
        for units in (20, 50, 100):
            for sign in (-1, 1):
                for bright in (False, True):
                    shift = sign * units / (968 if axis == 'x' else 1424)
                    end = (355 + round(shift*W), 480) if axis == 'x' else (355, 480 + round(shift*H))
                    yield f'{axis}_{sign*units}_{bright}', scene((355, 480), end, bright=bright), dict(near=(.5, 480/H), shift=shift, axis=axis)


CASES = list(cases())
# Shrink just the AFTER disk to exercise size mismatch independently of direction.
CASES[6] = ('unequal_blobs', scene((355, 480), None), CASES[6][2])
ImageDraw.Draw(CASES[6][1][1]).ellipse((388, 476, 396, 484), fill=40)


@pytest.mark.parametrize('name,frames,kwargs', CASES[:7], ids=[v[0] for v in CASES[:7]])
def test_rejects_unconfirmed_pair(monkeypatch, name, frames, kwargs):
    monkeypatch.setenv('POINTER_TWO_BLOB_CONFIRM', '1')
    diag = {}
    assert vision.pointer(*frames, **kwargs, diagnostics=diag) is None
    assert diag['outcome'] == ('single_blob' if name in ('hidden', 'outside_roi') else 'shift_inconsistent')
    assert diag['blob_count'] >= 1


@pytest.mark.parametrize('name,frames,kwargs', CASES[7:], ids=[v[0] for v in CASES[7:]])
def test_two_blobs_report_before_center(monkeypatch, name, frames, kwargs):
    monkeypatch.setenv('POINTER_TWO_BLOB_CONFIRM', '1')
    diag = {}
    found = vision.pointer(*frames, **kwargs, diagnostics=diag)
    assert found is not None, diag
    assert math.dist((found[0]*W, found[1]*H), (355, 480)) <= 1
    assert diag['outcome'] == 'found' and diag['blob_count'] == 2


@pytest.mark.parametrize('flag', [None, '0', 'true'])
@pytest.mark.parametrize('name,frames,kwargs', CASES, ids=[v[0] for v in CASES])
def test_flag_off_matches_frozen_legacy(monkeypatch, flag, name, frames, kwargs):
    if flag is None:
        monkeypatch.delenv('POINTER_TWO_BLOB_CONFIRM', raising=False)
    else:
        monkeypatch.setenv('POINTER_TWO_BLOB_CONFIRM', flag)
    # The legacy API is horizontal. Axis is an opt-in extension only.
    kwargs = {k: v for k, v in kwargs.items() if k != 'axis'}
    diag = {}
    found = vision.pointer(*frames, **kwargs, diagnostics=diag)
    actual = {'position': list(found) if found else None, 'diag': diag}
    expected = json.loads((Path(__file__).parent/'fixtures/pointer_two_blob_legacy.json').read_text())
    assert actual == expected[name]


def test_complete_pair_can_touch_roi_edge(monkeypatch):
    monkeypatch.setenv('POINTER_TWO_BLOB_CONFIRM', '1')
    # ROI right edge is x=439 inclusive; AFTER disk is fully visible through it.
    frames = scene((355, 480), (431, 480))
    diag = {}
    found = vision.pointer(*frames, near=(.5, 480/H), shift=100/968, diagnostics=diag)
    assert found is not None, diag
    assert math.dist((found[0]*W, found[1]*H), (355, 480)) <= 1


def test_clean_pair_matches_legacy_before_center(monkeypatch):
    frames = scene((355, 480), (392, 480))
    kwargs = dict(near=(.5, 480/H), shift=50/968)
    monkeypatch.setenv('POINTER_TWO_BLOB_CONFIRM', '0')
    legacy = vision.pointer(*frames, **kwargs)
    monkeypatch.setenv('POINTER_TWO_BLOB_CONFIRM', '1')
    confirmed = vision.pointer(*frames, **kwargs)
    assert legacy is not None and confirmed is not None
    assert math.dist((confirmed[0]*W, confirmed[1]*H),
                     (legacy[0]*W, legacy[1]*H)) <= 1
