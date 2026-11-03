"""Offline landscape safety regressions and optional labelled holdouts."""
import asyncio
import io
import json
import math
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageDraw

from hidagent import spotlight_launch as sl
from hidagent.hid_lease import leased_input
from test_spotlight_launch import GOAL, setup, landscape_setup, landscape_picture, run


def capsule(background=20, contrast=10, typed=False, outside=False):
    im = Image.new('RGB', (1646, 1080), (background,) * 3)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((389, 117, 1257, 210), radius=38,
                        fill=(background + contrast,) * 3)
    d.ellipse((1195, 145, 1225, 175), fill='white')
    d.rectangle((480, 145, 560 if typed else 640, 175), fill='white')
    if outside:
        d.rectangle((0, 230, 1645, 1079), fill='red')
    out = io.BytesIO()
    im.save(out, 'PNG')
    return out.getvalue()


def flat(value=20):
    out = io.BytesIO()
    Image.new('RGB', (1646, 1080), (value,) * 3).save(out, 'PNG')
    return out.getvalue()


def invoke(s):
    async def task():
        with leased_input(lambda: None):
            return await sl._prepare(s.a, GOAL)
    return asyncio.run(task())


def test_dark_capsule_uses_relative_geometry_and_never_false_closed():
    before = flat()
    check = sl.landscape_capsule_check(capsule(), before=before)
    assert check['state'] == 'open', check
    assert check['band_delta'] > 0 and check['open_margin'] > 0
    assert sl.local_check(capsule())['state'] != 'closed'


def test_same_open_reference_is_not_permission_to_upload():
    frame = capsule()
    assert sl.local_check(frame, before=frame)['state'] != 'closed'


def test_global_scene_change_without_localized_change_stays_unknown():
    check = sl.local_check(capsule(), before=flat(240))
    assert check['localized_delta'] == 0
    assert check['state'] == 'unknown', check


def test_non_capsule_motion_does_not_prove_open():
    before = landscape_picture()
    im = Image.open(io.BytesIO(before))
    ImageDraw.Draw(im).rectangle((0, 230, 1645, 1079), fill='white')
    out = io.BytesIO()
    im.save(out, 'PNG')
    check = sl.local_check(out.getvalue(), before=before)
    assert check['state'] == 'closed' and check['band_delta'] == 0


def test_query_change_is_band_only():
    assert sl.query_band_changed(capsule(), capsule(typed=True))
    assert not sl.query_band_changed(capsule(), capsule(outside=True))
    assert not sl.query_band_changed(capsule(), capsule())


def test_prefilled_selected_query_replaced_once(landscape_setup):
    s = landscape_setup
    s.update(flat())
    old_key = s.a.phone.key.side_effect
    async def key(combo):
        await old_key(combo)
        if combo == 'cmd+space':
            s.update(capsule())
    async def type_text(text):
        s.calls.append(('type', text))
        s.t += .1
        s.update(capsule(typed=True))
    s.a.phone.key.side_effect = key
    s.a.phone.type.side_effect = type_text
    assert run(s.a)[0]['action'] == 'done'
    assert s.calls == ['cmd+space', ('type', 'YouTube'), 'enter']
    checks = sl.receipt(s.a, 'YouTube')['local_checks']
    assert any(c['stage'] == 'query_change_wait' and c['query_changed'] for c in checks)


def test_unchanged_query_never_enters_or_retypes(landscape_setup):
    s = landscape_setup
    s.update(flat())
    old_key = s.a.phone.key.side_effect
    async def key(combo):
        await old_key(combo)
        if combo == 'cmd+space':
            s.update(capsule())
    async def type_text(text):
        s.calls.append(('type', text))
        s.t += .1
        s.update(capsule())
    s.a.phone.key.side_effect = key
    s.a.phone.type.side_effect = type_text
    result = run(s.a)[0]
    assert 'enter' not in s.calls
    assert s.a.phone.type.await_count == 1
    assert result['action'] != 'done'
    assert run(s.a)[0]['action'] != 'done'
    assert s.a.phone.type.await_count == 1


def test_unknown_preflight_esc_then_fallback(landscape_setup):
    s = landscape_setup
    s.update(capsule())
    assert invoke(s) == 'fallback'
    assert s.calls == ['esc']
    assert sl.receipt(s.a, 'YouTube')['fallback_reason'] == 'spotlight_preflight_unknown'
    s.a.client.evaluate.assert_not_awaited()


def test_closed_global_brightness_change_is_not_a_capsule():
    check = sl.local_check(landscape_picture('app'), before=landscape_picture())
    assert check['state'] == 'closed', check


def test_unknown_falls_back_to_guarded_legacy_keyboard(landscape_setup):
    s = landscape_setup
    s.update(capsule())
    s.a.client.evaluate = AsyncMock(return_value={
        'answers': {'page': {'choice': 'other', 'confidence': .6}}, 'seconds': .1})
    result = run(s.a)[0]
    assert result['phase'] == 'classify_scene' and result['action'] == 'wait'
    assert s.calls == ['esc']
    assert result['launch_receipt']['fallback_reason'] == 'spotlight_preflight_unknown'
    s.a.client.evaluate.assert_awaited_once()
    s.update(capsule())
    assert run(s.a)[0]['action'] == 'fail'
    s.a.client.evaluate.assert_awaited_once()


def test_esc_close_wait_at_least_point_four_seconds(landscape_setup):
    s = landscape_setup
    s.update(capsule())
    judged = []
    original = sl.local_check
    async def key(combo):
        s.calls.append(combo)
        s.esc_at = s.t
        s.update(landscape_picture())
    def check(frame, *args, **kwargs):
        if hasattr(s, 'esc_at'):
            judged.append(s.t - s.esc_at)
        return original(frame, *args, **kwargs)
    s.a.phone.key.side_effect = key
    with pytest.MonkeyPatch.context() as m:
        m.setattr(sl, 'local_check', check)
        assert invoke(s) == 'fallback'
    assert judged and min(judged) >= .4


def test_foreground_reuse_precedes_spotlight(landscape_setup, monkeypatch):
    s = landscape_setup
    reuse = AsyncMock(return_value={'seconds': .01, 'liveness_probe_used': False})
    monkeypatch.setattr('hidagent.foreground_reuse.reuse_foreground', reuse)
    action = run(s.a)[0]
    assert action['action'] == 'done' and action['_verified_foreground_reuse']
    assert s.calls == [] and sl.receipt(s.a, 'YouTube') is None
    s.a.client.evaluate.assert_not_awaited()


EVIDENCE = Path(os.environ.get('SPOTLIGHT_LABELLED_EVIDENCE',
    'runtime/local-evidence/'
    'outputs/current-goal-20261028/evidence/app-learning-20261029/spotlight'))


def labelled_samples():
    if not (EVIDENCE / 'labels.json').is_file():
        pytest.skip('Optional labelled evidence unavailable')
    samples = json.loads((EVIDENCE / 'labels.json').read_text())['samples']
    assert len(samples) == 60
    return samples


def label(sample):
    observed = sample['observed_label']
    return 'unknown' if observed.startswith('closing_ghost') else 'open' if observed.startswith('open_') else 'closed'


def test_optional_labelled_no_false_closed():
    samples = labelled_samples()
    for sample in samples:
        frame = (EVIDENCE / sample['file']).read_bytes()
        before = (EVIDENCE / (sample['foreground'] + '__closed.png')).read_bytes()
        check = sl.landscape_capsule_check(frame, before=before)
        if label(sample) != 'closed':
            assert check['state'] != 'closed', (sample['file'], check)
            assert sl.landscape_capsule_check(frame)['state'] != 'closed', sample['file']
        if label(sample) == 'open':
            assert check['state'] == 'open', (sample['file'], check)
            assert sl.landscape_capsule_check(frame)['state'] != 'closed'
        assert sl.local_check(frame)['state'] == 'unknown', 'Crops cannot authorize runtime upload'


def fit_limits(rows):
    """Train on open rows only; round half-minima downward for safety margin.

    The rule and closed vetoes are fixed. No held-out foreground, filename,
    query or absolute wallpaper values enter the four fitted open limits.
    """
    positives = [row['features'] for row in rows if row['label'] == 'open']
    def half_min(key, step):
        return math.floor(min(row[key] for row in positives) / 2 / step) * step
    return sl.LandscapeLimits(band=half_min('localized_delta', 1),
                             temporal_shape=half_min('temporal_shape', .5),
                             shape=half_min('shape_score', .5),
                             glyph=half_min('glyph_contrast', 2))


def evaluate_holdouts():
    samples = labelled_samples()
    rows = []
    # Pixels are read in place. Only scalar results leave this process.
    for sample in samples:
        frame = (EVIDENCE / sample['file']).read_bytes()
        before = (EVIDENCE / (sample['foreground'] + '__closed.png')).read_bytes()
        rows.append(dict(sample, label=label(sample),
                         features=sl.landscape_capsule_check(frame, before=before)))
    results = []
    for foreground in sorted({r['foreground'] for r in rows}):
        training = [r for r in rows if r['foreground'] != foreground]
        testing = [r for r in rows if r['foreground'] == foreground]
        assert len(training) == 55 and len(testing) == 5
        limits = fit_limits(training)
        matrix = {actual: {pred: 0 for pred in ('open', 'closed', 'unknown')}
                  for actual in ('open', 'closed', 'unknown')}
        margins = []
        closed_veto = []
        for row in testing:
            frame = (EVIDENCE / row['file']).read_bytes()
            before = (EVIDENCE / (foreground + '__closed.png')).read_bytes()
            check = sl.landscape_capsule_check(frame, before=before, limits=limits)
            matrix[row['label']][check['state']] += 1
            if row['label'] != 'closed':
                assert check['state'] != 'closed', (row['file'], check)
            if row['label'] == 'open':
                assert check['state'] == 'open', (row['file'], check)
                margins.append(check['open_margin'])
                closed_veto.append(-check['closed_margin'])
        results.append({'foreground': foreground, 'confusion': matrix,
                        'limits': vars(limits), 'min_open_margin': min(margins),
                        'min_closed_veto_margin': min(closed_veto)})
    return {'folds': results, 'full_fit': vars(fit_limits(rows))}


def test_optional_leave_one_foreground_out():
    results = evaluate_holdouts()
    assert results['full_fit'] == vars(sl.LANDSCAPE_LIMITS)
    assert len(results['folds']) == 12
    print(json.dumps(results, indent=2))


def test_optional_real_selected_query_changes():
    for foreground in sorted({s['foreground'] for s in labelled_samples()}):
        before = (EVIDENCE / (foreground + '__open-empty.png')).read_bytes()
        after = (EVIDENCE / (foreground + '__open-typed.png')).read_bytes()
        assert sl.query_band_changed(before, after), foreground


if __name__ == '__main__':
    print(json.dumps(evaluate_holdouts(), indent=2))
