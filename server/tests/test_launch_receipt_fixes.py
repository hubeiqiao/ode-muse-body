"""Offline receipt regressions. All judgments and input methods are mocks."""
import asyncio
import io
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from hidagent import shortcut_workflow as sw, reddit_web as rw, app_search
from hidagent.app_tools import AppTools
from hidagent.decision import DecisionUnavailable


def image(size=(1646, 1080), color='white'):
    out = io.BytesIO()
    Image.new('RGB', size, color).save(out, format='PNG')
    return out.getvalue()


def result(**answers):
    return dict(answers=answers, seconds=.01, approved=True)


def probability(value):
    return {'noul': value}


def fake(monkeypatch, app='Discord', phases=('query', 'launch')):
    monkeypatch.setenv('APP_LAUNCH_SPOTLIGHT_FIRST', '0')
    target = dict(device='ipad', orientation='landscape', width=1646, height=1080,
                  calibration_key='ipad-landscape-66')
    async def evaluate(state, questions, frame):
        if 'visible_app' in questions:
            return result(visible_app={'choice': app, 'confidence': 1})
        if 'page' in questions:
            return result(page={'choice': 'app', 'confidence': 1})
        if state.get('policy') == 'launch_open_final':
            return result(ready=probability(1), blocked=probability(0), loading=probability(0))
        if state.get('policy', '').startswith('system_') or state.get('policy') == 'launch_benign_modal':
            return result(ready=probability(0))
        return result(ready=probability(1))
    return NS(screen=NS(frame=image(), live=True),
              phone=NS(target_info=lambda: target, calibrated=True, recover_home=AsyncMock()),
              steps=[dict(workflow='keyboard', phase=p, action='key', act_s=.1) for p in phases],
              client=NS(evaluate=AsyncMock(side_effect=evaluate)), trace=Mock())


@pytest.mark.parametrize('app', ['App Store', 'Instagram'])
def test_verified_foreground_completes_readonly_without_motion_recheck(monkeypatch, app):
    a = fake(monkeypatch, app)
    checked_frame = a.screen.frame
    original = a.client.evaluate.side_effect
    async def evaluate(state, questions, frame):
        if state.get('policy') == 'ipad_foreground':
            a.screen.frame = image(color='black')
        return await original(state, questions, frame)
    a.client.evaluate.side_effect = evaluate
    out = asyncio.run(sw.next_shortcut_actions(a, {'app': app, 'text': None}))[0]
    assert out['action'] == 'done'
    assert out['_shot'] == checked_frame
    checks = [c for c in a.client.evaluate.call_args_list if c.args[0].get('policy') == 'launch_open_final']
    assert not checks


@pytest.mark.parametrize('failure', ['wrong_app', 'blocked', 'loading', 'not_foremost'])
def test_foreground_verdict_never_completes_wrong_blocked_loading_or_inactive_app(monkeypatch, failure):
    a = fake(monkeypatch, 'Instagram')
    original = a.client.evaluate.side_effect
    async def evaluate(state, questions, frame):
        if 'page' in questions and failure in ('blocked', 'loading'):
            return result(page={'choice': failure, 'confidence': 1})
        if 'visible_app' in questions and failure == 'wrong_app':
            return result(visible_app={'choice': 'YouTube', 'confidence': 1})
        if state.get('policy') == 'ipad_foreground' and failure == 'not_foremost':
            return result(ready=probability(0))
        return await original(state, questions, frame)
    a.client.evaluate.side_effect = evaluate
    try:
        out = asyncio.run(sw.next_shortcut_actions(a, {'app': 'Instagram', 'text': None}))[0]
    except DecisionUnavailable:
        return
    assert out['action'] != 'done'


@pytest.mark.parametrize('notice,phase,action', [
    ('tracking', 'deny_app_tracking', 'tap'),
    ('whats_new', 'dismiss_launch_whats_new', 'tap'),
    ('promo', 'dismiss_launch_promo', 'key'),
])
@pytest.mark.parametrize('confidence', [.95, 1])
def test_generic_blocked_launch_dismisses_only_known_modal_once(monkeypatch, notice, phase, action, confidence):
    monkeypatch.setattr(sw, 'control_region_matches', lambda *args, **kwargs: True)
    a = fake(monkeypatch)
    async def evaluate(state, questions, frame):
        policy = state.get('policy')
        if 'page' in questions:
            return result(page={'choice': 'blocked', 'confidence': 1})
        if policy == 'system_tracking_denial':
            return result(ready=probability(1), target={'confidence': 1, 'box': {'left': .3, 'right': .6, 'top': .5, 'bottom': .6}})
        if policy == 'launch_benign_modal':
            return result(ready=probability(notice != 'tracking'), notice={'choice': notice, 'confidence': confidence},
                          target={'confidence': 1, 'box': {'left': .02, 'right': .04, 'top': .06, 'bottom': .09}})
        return result(ready=probability(policy == 'system_tracking_present' and notice == 'tracking'))
    a.client.evaluate.side_effect = evaluate
    out = asyncio.run(sw.next_shortcut_actions(a, {'app': 'Discord', 'text': None}))[0]
    assert (out['phase'], out['action']) == (phase, action)
    if action == 'key':
        assert out['combo'] == 'esc'
    a.steps.append(dict(out, act_s=.1))
    with pytest.raises(DecisionUnavailable, match='blocked'):
        asyncio.run(sw.next_shortcut_actions(a, {'app': 'Discord', 'text': None}))


def test_modal_dismissal_reverifies_real_content_before_completion(monkeypatch):
    a = fake(monkeypatch, phases=('query', 'launch', 'dismiss_launch_promo'))
    out = asyncio.run(sw.next_shortcut_actions(a, {'app': 'Discord', 'text': None}))[0]
    assert out['action'] == 'done'
    assert any('page' in c.args[1] for c in a.client.evaluate.call_args_list)


@pytest.mark.parametrize('ready,notice,confidence,close_confidence', [
    (0, 'whats_new', 1, 1), (1, 'other', 1, 1),
    (1, 'promo', .8, 1), (1, 'whats_new', 1, .5),
])
def test_unknown_or_uncertain_modals_never_receive_escape_or_acceptance(monkeypatch, ready, notice, confidence, close_confidence):
    a = fake(monkeypatch)
    async def evaluate(state, questions, frame):
        if 'page' in questions:
            return result(page={'choice': 'blocked', 'confidence': 1})
        if state.get('policy') == 'launch_benign_modal':
            return result(ready=probability(ready), notice={'choice': notice, 'confidence': confidence},
                          target={'confidence': close_confidence})
        return result(ready=probability(0))
    a.client.evaluate.side_effect = evaluate
    with pytest.raises(DecisionUnavailable, match='blocked'):
        asyncio.run(sw.next_shortcut_actions(a, {'app': 'Discord', 'text': None}))


def test_layered_launch_notices_are_each_cancelled_once_before_actual_done(monkeypatch):
    monkeypatch.setattr(sw, 'control_region_matches', lambda *args, **kwargs: True)
    a = fake(monkeypatch)
    box = {'left': .02, 'right': .04, 'top': .06, 'bottom': .09}
    async def evaluate(state, questions, frame):
        phases = {s['phase'] for s in a.steps}
        stage = ('tracking' if 'deny_app_tracking' not in phases else
                 'promo' if 'dismiss_launch_promo' not in phases else
                 'whats_new' if 'dismiss_launch_whats_new' not in phases else 'app')
        policy = state.get('policy')
        if 'page' in questions:
            return result(page={'choice': 'app' if stage == 'app' else 'blocked', 'confidence': 1})
        if policy == 'system_tracking_denial':
            return result(ready=probability(1), target={'confidence': 1, 'box': box})
        if policy == 'launch_benign_modal':
            return result(ready=probability(1), notice={'choice': stage, 'confidence': .95},
                          target={'confidence': 1 if stage == 'whats_new' else 0, 'box': box})
        return result(ready=probability((policy == 'system_tracking_present' and stage == 'tracking') or stage == 'app'))
    a.client.evaluate.side_effect = evaluate
    actions = []
    for _ in range(4):
        out = asyncio.run(sw.next_shortcut_actions(a, {'app': 'Discord', 'text': None}))[0]
        actions.append(out)
        a.steps.append(dict(out, act_s=.1))
    assert [(x['action'], x['phase']) for x in actions] == [
        ('tap', 'deny_app_tracking'), ('key', 'dismiss_launch_promo'),
        ('tap', 'dismiss_launch_whats_new'), ('done', 'verified')]
    assert actions[1]['combo'] == 'esc'


def test_landscape_reddit_bands_follow_observed_toolbar_and_exclude_tab_titles():
    frame = image()
    address = Image.open(io.BytesIO(rw.address_band(frame)))
    page = Image.open(io.BytesIO(rw.page_band(frame)))
    assert address.size == (1646, int(1080 * .17) - int(1080 * .11))
    assert page.size == (1646, 1080 - int(1080 * .18))


def test_portrait_band_bytes_unchanged():
    frame = image((710, 1080))
    for fn, box in [(rw.address_band, (0, int(1080*.067), 710, int(1080*.125))),
                    (rw.page_band, (0, int(1080*.125), 710, 1080))]:
        expected = io.BytesIO()
        Image.open(io.BytesIO(frame)).crop(box).save(expected, format='JPEG', quality=95)
        assert fn(frame) == expected.getvalue()


def test_landscape_reddit_open_and_search_use_geometry_aware_guard(monkeypatch):
    a = fake(monkeypatch, phases=())
    a.steps = [dict(workflow='reddit_web', phase=p, act_s=.1) for p in
               ('home', 'chrome_ready', 'new_tab', 'url_focus')]
    a.client.evaluate.side_effect = None
    a.client.evaluate.return_value = result(ready=probability(1), literal=probability(1), blocked=probability(0))
    regions = []
    monkeypatch.setattr(rw, 'control_region_matches', lambda before, after, box: regions.append(box) or True)
    for open_only in (True, False):
        out = asyncio.run(rw.next_reddit_web_actions(a, {'app': 'Reddit', 'query': 'IELTS'}, open_only=open_only))[0]
        assert out['action'] == 'type'
        assert out['text'] == rw.URL + ('#esp32' if open_only else '?q=IELTS#esp32')
    assert all(b['top'] == .11 and b['bottom'] == .17 for b in regions)


def test_direct_reddit_landscape_shortcut_routes_to_chrome_without_native_prepare(monkeypatch):
    a = fake(monkeypatch, phases=())
    route = AsyncMock(return_value=[{'action': 'wait', 'execution_route': rw.ROUTE}])
    monkeypatch.setattr(rw, 'next_reddit_web_actions', route)
    prepare = AsyncMock(side_effect=AssertionError('native launch forbidden'))
    monkeypatch.setattr('hidagent.spotlight_launch.prepare', prepare)
    assert asyncio.run(sw.next_shortcut_actions(a, {'app': 'Reddit', 'text': None}))[0]['execution_route'] == rw.ROUTE
    route.assert_awaited_once()
    prepare.assert_not_awaited()


@pytest.mark.parametrize('app', ['Joe Speaking', 'Amazon'])
def test_landscape_apps_attempt_native_launch_before_any_layout_refusal(monkeypatch, app):
    a = fake(monkeypatch, phases=())
    actions = [dict(action='key', combo='cmd+space', workflow='keyboard', phase='spotlight')]
    prepare = AsyncMock(return_value=actions)
    monkeypatch.setattr('hidagent.spotlight_launch.prepare', prepare)
    goal = {'app': app, 'text': None}
    assert asyncio.run(sw.next_shortcut_actions(a, goal)) == actions
    prepare.assert_awaited_once_with(a, goal)
    a.client.evaluate.assert_not_awaited()
    a.phone.recover_home.assert_not_awaited()


def test_unknown_launch_geometry_change_recovers_home_once_then_stops(monkeypatch):
    a = fake(monkeypatch, 'Settings')
    original = a.client.evaluate.side_effect
    async def evaluate(state, questions, frame):
        out = await original(state, questions, frame)
        a.screen.frame = image((1920, 1080))
        a.phone.target_info().update(width=1920, calibration_key='ipad-landscape-56')
        a.phone.calibrated = False
        return out
    a.client.evaluate.side_effect = evaluate
    out = asyncio.run(sw.next_shortcut_actions(a, {'app': 'Settings', 'text': None}))[0]
    assert out['action'] == 'fail' and 'external_display_layout' in out['reason']
    a.phone.recover_home.assert_awaited_once()
    calls = a.client.evaluate.await_count
    out = asyncio.run(sw.next_shortcut_actions(a, {'app': 'Settings', 'text': None}))[0]
    assert out['action'] == 'fail'
    assert a.client.evaluate.await_count == calls
    a.phone.recover_home.assert_awaited_once()


def test_reddit_search_recovers_changed_layout_before_route_and_resumes_only_when_calibrated(monkeypatch):
    a = fake(monkeypatch, phases=())
    # Establish the current calibrated launch binding, without starting a native app.
    assert asyncio.run(rw.next_reddit_web_actions(a, {'app': 'Reddit', 'query': 'IELTS'}))[0]['phase'] == 'home'
    a.screen.frame = image((1920, 1080))
    a.phone.target_info().update(width=1920, calibration_key='ipad-landscape-56')
    a.phone.calibrated = False
    a.steps.append(dict(workflow='app_search', phase='app_ready', act_s=.1))
    out = asyncio.run(app_search._next_search_actions(a, {'app': 'Reddit', 'query': 'IELTS'}))[0]
    assert out['phase'] == 'external_display_layout' and out['execution_route'] == rw.ROUTE
    a.phone.recover_home.assert_awaited_once()
    a.client.evaluate.assert_not_awaited()
    a.phone.calibrated = True
    a.phone.target_info().update(width=1646, calibration_key='ipad-landscape-66')
    a.screen.frame = image()
    out = asyncio.run(app_search._next_search_actions(a, {'app': 'Reddit', 'query': 'IELTS'}))[0]
    assert out['phase'] == 'home'
    a.phone.recover_home.assert_awaited_once()
    assert asyncio.run(sw.launch_layout_guard(a, 'Chrome')) is None


def test_failed_recovery_is_consumed_before_await_and_never_repeated(monkeypatch):
    a = fake(monkeypatch)
    assert asyncio.run(sw.launch_layout_guard(a, 'Discord')) is None
    a.screen.frame = image((1920, 1080))
    a.phone.target_info().update(width=1920, calibration_key='ipad-landscape-56')
    a.phone.calibrated = False
    a.phone.recover_home.side_effect = RuntimeError('lost acknowledgement')
    for _ in range(2):
        out = asyncio.run(sw.next_shortcut_actions(a, {'app': 'Discord', 'text': None}))[0]
        assert out['action'] == 'fail' and 'external_display_layout' in out['reason']
    a.phone.recover_home.assert_awaited_once()
    a.client.evaluate.assert_not_awaited()


@pytest.mark.parametrize('reached', [True, False, None])
def test_layout_guard_honors_home_receipt_without_replaying(monkeypatch, reached):
    a = fake(monkeypatch, phases=())
    assert asyncio.run(sw.launch_layout_guard(a, 'Reddit')) is None
    a.screen.frame = image((1920, 1080))
    a.phone.target_info().update(width=1920, calibration_key='ipad-landscape-56')
    a.phone.calibrated = False
    a.phone.recover_home.return_value = dict(ok=reached is not False, home_reached=reached)
    out = asyncio.run(sw.launch_layout_guard(a, 'Reddit'))[0]
    assert out['action'] == ('fail' if reached is False else 'wait')
    a.phone.calibrated = True
    a.phone.target_info().update(width=1646, calibration_key='ipad-landscape-66')
    a.screen.frame = image()
    out = asyncio.run(sw.launch_layout_guard(a, 'Reddit'))
    assert (out is None) is (reached is not False)
    a.phone.recover_home.assert_awaited_once()


def test_layout_shift_is_recovered_even_when_launch_verifier_raises(monkeypatch):
    a = fake(monkeypatch)
    async def evaluate(*args):
        a.screen.frame = image((1920, 1080))
        a.phone.target_info().update(width=1920, calibration_key='ipad-landscape-56')
        a.phone.calibrated = False
        raise DecisionUnavailable('frame changed during observation')
    a.client.evaluate.side_effect = evaluate
    out = asyncio.run(sw.next_shortcut_actions(a, {'app': 'Discord', 'text': None}))[0]
    assert out['action'] == 'fail' and 'external_display_layout' in out['reason']
    a.phone.recover_home.assert_awaited_once()


def test_reddit_route_itself_preserves_private_spotlight_gate(monkeypatch):
    a = fake(monkeypatch, phases=())
    a.steps = [dict(workflow='reddit_web', phase=p, act_s=.1) for p in
               ('home', 'chrome_ready', 'new_tab', 'url_focus')]
    monkeypatch.setattr('hidagent.spotlight_launch.receipt', lambda *args: {'never_retry_typing': True})
    monkeypatch.setattr('hidagent.spotlight_launch.safe_frame', lambda *args: False)
    blocked = [{'action': 'fail', 'reason': 'private Spotlight'}]
    refuse = Mock(return_value=blocked)
    monkeypatch.setattr('hidagent.spotlight_launch.fail_action', refuse)
    assert asyncio.run(rw.next_reddit_web_actions(a, {'app': 'Reddit', 'query': 'IELTS'})) == blocked
    a.client.evaluate.assert_not_awaited()


def test_reddit_layout_recovery_precedes_stale_spotlight_binding_failure(monkeypatch):
    a = fake(monkeypatch, phases=())
    asyncio.run(sw.launch_layout_guard(a, 'Reddit'))
    a.screen.frame = image((1920, 1080))
    a.phone.target_info().update(width=1920, calibration_key='ipad-landscape-56')
    a.phone.calibrated = False
    monkeypatch.setattr('hidagent.spotlight_launch.receipt', lambda *args: {'never_retry_typing': True})
    monkeypatch.setattr('hidagent.spotlight_launch.safe_frame', lambda *args: False)
    monkeypatch.setattr('hidagent.spotlight_launch.fail_action', lambda *args: [{'action': 'fail', 'reason': 'stale binding'}])
    out = asyncio.run(app_search._next_search_actions(a, {'app': 'Reddit', 'query': 'IELTS'}))[0]
    assert out['phase'] == 'external_display_layout'
    a.phone.recover_home.assert_awaited_once()
    a.client.evaluate.assert_not_awaited()


def test_open_stop_receipt_keeps_its_status_when_calibration_is_unavailable():
    state = dict(device=True, screen=True, calibrated=True, controller={'stable': True}, target={'device': 'ipad'})
    receipt = dict(status='stopped', running=False, calibrated=False, last_action={'action': 'stopped'})
    bridge = NS(exploration=None, execute=AsyncMock(return_value=receipt), snapshot=Mock())
    tools = AppTools(bridge, NS(), NS(), lambda: state, AsyncMock())
    out = asyncio.run(tools.call('stopped_launch_123', 'instagram.open', {}))
    assert out['status'] == 'stopped'


@pytest.mark.parametrize('name,done,calibrated,expected', [
    ('instagram.open', True, True, 'completed'),
    ('instagram.open', False, True, 'input_uncertain'),
    ('keyboard.type_focused', True, True, 'input_uncertain'),
    ('example_creator.open', True, False, 'failed'),
])
def test_open_receipt_uses_explicit_done_but_retains_uncertain_input(monkeypatch, name, done, calibrated, expected):
    state = dict(device=True, screen=True, calibrated=True, controller={'stable': True}, target={'device': 'ipad'})
    receipt = dict(status='model_reported_done' if done else 'failed', running=False,
                   calibrated=calibrated, never_retry_typing=True, input_was_sent=True,
                   last_action={'action': 'done' if done else 'error'})
    bridge = NS(exploration=None, execute=AsyncMock(return_value=receipt), snapshot=Mock())
    tools = AppTools(bridge, NS(), NS(), lambda: state, AsyncMock())
    out = asyncio.run(tools.call('launch_receipt_123', name, {'text': 'hello'} if name == 'keyboard.type_focused' else {}))
    assert out['status'] == expected
    assert out['result']['receipt']['never_retry_typing'] is True
    if not calibrated:
        assert 'external_display_layout' in out['result']['receipt']['reason']
