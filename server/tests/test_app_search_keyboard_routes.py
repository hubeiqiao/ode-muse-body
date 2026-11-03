"""Offline program-route tests. All model, controller and mirror IO is fake."""
import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from hidagent.app_search import next_search_actions, PREFIX
from hidagent.decision import DecisionUnavailable
from hidagent.hybrid_agent import HybridAgent
from test_keyboard_no_pointer import keyboard_phone


def agent(monkeypatch, app='X'):
    monkeypatch.setenv('APP_SEARCH_KEYBOARD_ROUTES', '1')
    monkeypatch.setenv('APP_SEARCH_KEYBOARD_APPS', 'X,TikTok')
    monkeypatch.setattr('hidagent.app_search.public_search_control', lambda *a, **k: None)
    monkeypatch.setattr('hidagent.focus_local.verify_local_focus', AsyncMock(return_value=None))
    frame = io.BytesIO()
    Image.new('RGB', (100, 100), 'white').save(frame, format='PNG')
    a = SimpleNamespace(
        steps=[dict(workflow='app_search', phase='app_ready', action='wait', act_s=.1, t=1)],
        screen=SimpleNamespace(frame=frame.getvalue(), updated=2, live=True), shots={}, trace=Mock(),
        phone=SimpleNamespace(calibrated=True, target_info=lambda: dict(device='ipad', orientation='landscape')),
        client=SimpleNamespace(evaluate=AsyncMock(return_value=dict(
            answers={k: dict(noul=1 if k != 'blocked' else 0) for k in ('ready', 'focused', 'search_control_focused', 'results', 'blocked')},
            seconds=.01))))
    a.client.evaluate.return_value['answers']['target'] = dict(
        confidence=.99, box=dict(left=.1, top=.04, right=.8, bottom=.08))
    return a, dict(app=app, query='IELTS speaking')


def next_actions(a, goal):
    return asyncio.run(next_search_actions(a, goal))


def ack(a, actions):
    for action in actions:
        a.shots[len(a.steps)] = action['_shot']
        a.steps.append({**action, 'act_s': .1, 't': a.screen.updated})
    a.screen.updated += 1


@pytest.mark.parametrize('app,combo,route', [('X', 'cmd+f', 'keyboard_shortcut'), ('TikTok', 'tab', 'fka_tab')])
def test_evidence_backed_entry_precedes_pointer(monkeypatch, app, combo, route):
    a, goal = agent(monkeypatch, app)
    out = next_actions(a, goal)
    assert len(out) == 1 and out[0]['action'] == 'key' and out[0]['combo'] == combo
    assert out[0]['route_used'] == route and out[0]['pointer_probe_sent'] is False


@pytest.mark.parametrize('case', ['default_off', 'disabled', 'unsupported', 'iphone', 'override_excludes'])
def test_route_gate_keeps_existing_pointer_path(monkeypatch, case):
    a, goal = agent(monkeypatch)
    if case == 'default_off': monkeypatch.delenv('APP_SEARCH_KEYBOARD_ROUTES')
    if case == 'disabled': monkeypatch.setenv('APP_SEARCH_KEYBOARD_ROUTES', '0')
    if case == 'unsupported': goal['app'] = 'LinkedIn'
    if case == 'iphone': a.phone.target_info = lambda: dict(device='iphone')
    if case == 'override_excludes': monkeypatch.setenv('APP_SEARCH_KEYBOARD_APPS', 'TikTok,Unknown')
    out = next_actions(a, goal)[0]
    assert out['action'] == 'tap' and out['route_used'] == 'pointer'


def test_override_cannot_invent_unsupported_routes(monkeypatch):
    a, goal = agent(monkeypatch, 'Reddit')
    a.phone.target_info = lambda: dict(device='iphone')
    monkeypatch.setenv('APP_SEARCH_KEYBOARD_APPS', 'Reddit')
    assert next_actions(a, goal)[0]['route_used'] == 'pointer'


def test_failed_focus_falls_back_same_run_without_query_or_shortcut_replay(monkeypatch):
    a, goal = agent(monkeypatch)
    ack(a, next_actions(a, goal))
    a.client.evaluate.return_value['answers']['ready']['noul'] = 0
    monkeypatch.setattr('hidagent.app_search.public_search_control', lambda *a, **k: dict(x=500, y=50, control='public_search_field'))
    out = next_actions(a, goal)
    assert len(out) == 1 and out[0]['action'] == 'tap' and out[0]['phase'] == 'focus'
    assert out[0]['route_used'] == 'pointer'
    assert [s.get('combo') for s in a.steps].count('cmd+f') == 1
    assert not any(s['action'] == 'type' for s in a.steps + out)


def test_tiktok_checks_ring_before_space_and_stops_at_three_tabs(monkeypatch):
    a, goal = agent(monkeypatch, 'TikTok')
    a.client.evaluate.return_value['answers']['search_control_focused']['noul'] = 0
    for _ in range(3):
        out = next_actions(a, goal)
        assert out[0]['combo'] == 'tab'
        ack(a, out)
    out = next_actions(a, goal)
    assert out[0]['action'] == 'tap' and out[0]['route_used'] == 'pointer'
    assert not any(s.get('combo') == 'space' for s in a.steps)


def test_tiktok_activates_only_independently_verified_search_ring(monkeypatch):
    a, goal = agent(monkeypatch, 'TikTok')
    ack(a, next_actions(a, goal))
    out = next_actions(a, goal)
    assert out[0]['combo'] == 'space' and out[0]['phase'] == 'focus'
    assert out[0]['_search_keyboard_entry'] == 'TikTok'


def test_bounded_tabs_are_not_suppressed_by_generic_repeat_detector(monkeypatch):
    from hidagent.agent import Agent
    a, goal = agent(monkeypatch, 'TikTok')
    a.client.evaluate.return_value['answers']['search_control_focused']['noul'] = 0
    for _ in range(2):
        ack(a, next_actions(a, goal))
        a.steps[-1]['changed'] = False  # Small ring motion can miss generic scene-diff thresholds.
    third = next_actions(a, goal)[0]
    assert third['combo'] == 'tab'
    assert not Agent._repeating(a, third)


@pytest.mark.parametrize('app', ['X', 'TikTok'])
def test_keyboard_search_runs_real_phone_methods_without_pointer(monkeypatch, keyboard_phone, app):
    a, goal = agent(monkeypatch, app)
    executor = HybridAgent.__new__(HybridAgent)
    executor.__dict__.update(a.__dict__)
    a = executor
    a.task = PREFIX + json.dumps(goal)
    a.phone = keyboard_phone
    a.phone.ensure_keyboard_live = AsyncMock(wraps=a.phone.ensure_keyboard_live)
    a.phone.ensure_live = AsyncMock(side_effect=AssertionError('Pointer liveness must not run'))
    a.phone.screen.frame = a.screen.frame
    # Query/result checks are mocked local observations, never API calls.
    monkeypatch.setattr('hidagent.app_query_text.verify_local_query', AsyncMock(return_value={'seconds': .01}))
    monkeypatch.setattr('hidagent.app_query_text.verify_landscape_results', AsyncMock(return_value={'seconds': .01}))
    for _ in range(8):
        actions = next_actions(a, goal)
        assert all(s['action'] in ('key', 'type', 'wait', 'done') for s in actions)
        for action in actions:
            if action['action'] not in ('wait', 'done'): asyncio.run(a._do(action))
        ack(a, actions)
        if actions[-1]['action'] == 'done': break
    assert a.steps[-1]['action'] == 'done'
    commands = [c.args[0] for c in a.phone.device.send.await_args_list]
    assert len([c for c in commands if c.startswith('type ')]) == 1
    assert commands.count('key enter') == 1
    assert all(c.startswith(('key ', 'type ')) for c in commands)
    assert a.phone.ensure_keyboard_live.await_count == len(commands)
    a.phone.ensure_live.assert_not_awaited()
    assert all(s['pointer_probe_sent'] is False for s in a.steps[1:])


@pytest.mark.parametrize('phase', ['query', 'submit'])
def test_verification_fallback_never_retypes_or_resubmits(monkeypatch, phase):
    a, goal = agent(monkeypatch)
    ack(a, next_actions(a, goal))
    ack(a, next_actions(a, goal))
    if phase == 'submit': ack(a, next_actions(a, goal))
    a.client.evaluate.return_value['answers']['ready']['noul'] = 0
    a.client.evaluate.return_value['answers']['results']['noul'] = 0
    out = next_actions(a, goal)
    assert out[0]['route_used'] == 'pointer'
    assert out[0]['action'] == 'wait'
    assert sum(s['action'] == 'type' for s in a.steps) == 1
    assert not any(s['action'] == 'type' or s.get('combo') == 'enter' for s in out)


@pytest.mark.skip(reason="Requires original real-device pixel crop excluded from public snapshot; synthetic algorithm cases remain active")
def test_keyboard_focus_uses_existing_local_pixels(monkeypatch):
    from hidagent import focus_local
    from test_focus_local import agent as focus_agent, FIXTURES
    a = focus_agent(rows=json.loads((FIXTURES / '043.json').read_text()))
    a.steps[-1].update(action='key', combo='space', _search_keyboard_entry='TikTok', route_used='fka_tab')
    assert asyncio.run(focus_local.verify_local_focus(a, dict(app='TikTok'), a.screen.frame))['signal'] == 'edit_menu'


def test_uncertain_keyboard_receipt_stops_before_any_fallback_or_resend(monkeypatch):
    a, goal = agent(monkeypatch)
    action = next_actions(a, goal)[0]
    a._do_impl = AsyncMock(side_effect=RuntimeError('lost key receipt'))
    with pytest.raises(RuntimeError): asyncio.run(HybridAgent._do(a, action))
    with pytest.raises(DecisionUnavailable, match='uncertain'):
        next_actions(a, goal)


def test_route_trace_records_actual_pointer_probe_on_fallback(monkeypatch):
    a, goal = agent(monkeypatch)
    action = dict(action='tap', workflow='app_search', phase='focus', route_used='pointer')
    async def tap(*args):
        a.phone.last_tap_timing = dict(pointer_probe_sent=True, outcome='ok')
    a._do_impl = tap
    asyncio.run(HybridAgent._do(a, action))
    assert action['pointer_probe_sent'] is True
    assert any(c.kwargs.get('kind') == 'app-search-route' and c.kwargs.get('pointer_probe_sent') is True
               for c in a.trace.write.call_args_list)


def test_unacknowledged_keyboard_entry_never_authorizes_pointer_fallback(monkeypatch):
    a, goal = agent(monkeypatch)
    action = next_actions(a, goal)[0]
    a.steps.append(action)
    with pytest.raises(DecisionUnavailable, match='uncertain'):
        next_actions(a, goal)


def test_existing_browser_route_is_named_in_trace_metadata(monkeypatch):
    a, goal = agent(monkeypatch, 'Reddit')
    monkeypatch.setattr('hidagent.app_search._recover_search_actions', AsyncMock(return_value=[dict(
        action='key', combo='cmd+l', workflow='reddit_web', phase='address')]))
    out = next_actions(a, goal)[0]
    assert out['route_used'] == 'chrome_reddit_web' and out['pointer_probe_sent'] is False


@pytest.mark.parametrize('app', ['X', 'TikTok'])
def test_benchmark_routes_require_explicit_app_opt_in(monkeypatch, app):
    a, goal = agent(monkeypatch, app)
    monkeypatch.delenv('APP_SEARCH_KEYBOARD_APPS')
    out = next_actions(a, goal)[0]
    assert out['action'] == 'tap' and out['route_used'] == 'pointer'


def x_search_open_failure(monkeypatch):
    # b04-x: no cmd+f or text, only acknowledged Home/Escape/Cmd+Space,
    # followed by three waits and a fresh high-confidence non-Spotlight frame.
    a, goal = agent(monkeypatch)
    a.steps = [dict(workflow='keyboard', action='key', combo=combo, phase=phase,
                    act_s=.2, t=1) for combo, phase in (
        ('cmd+h', 'normalize_ipad_home'), ('esc', 'normalize_ipad_home_done'),
        ('cmd+space', 'open_search'))]
    a.steps += [dict(workflow='keyboard', action='wait', phase='search_open',
                     act_s=0, t=1) for _ in range(3)]
    monkeypatch.setattr('hidagent.home_icons.locate_home_icon', lambda *args: None)
    monkeypatch.setattr('hidagent.home_icons.has_home_icon', lambda *args: False)
    monkeypatch.setenv('APP_LAUNCH_SPOTLIGHT_FIRST', '0')
    a.client.evaluate.side_effect = [
        dict(answers=dict(page=dict(choice='other', confidence=.98)), seconds=.01),
        dict(answers=dict(ready=dict(noul=1), blocked=dict(noul=0),
                          target=dict(confidence=.99, box=dict(left=.1, top=.6, right=.2, bottom=.8))),
             approved=True, seconds=.01)]
    return a, goal


def test_x_benchmark_search_open_failure_uses_pointer_in_same_run(monkeypatch):
    a, goal = x_search_open_failure(monkeypatch)
    out = next_actions(a, goal)
    assert [(s['action'], s['phase'], s['route_used']) for s in out] == [('tap', 'icon_launch', 'pointer')]
    assert out[0]['_pointer_launch_fallback'] == 'X'
    assert not any(s['action'] == 'type' or s.get('combo') in ('cmd+f', 'enter') for s in a.steps + out)
    assert sum(s.get('combo') == 'cmd+space' for s in a.steps + out) == 1
    ack(a, out)
    # Observe the clicked app once, then continue public Search. Do not relaunch.
    a.client.evaluate.side_effect = None
    a.client.evaluate.return_value = dict(answers=dict(ready=dict(noul=1), blocked=dict(noul=0),
                                                       loading=dict(noul=0)), seconds=.01, approved=True)
    ready = next_actions(a, goal)
    assert ready[0]['phase'] == 'app_ready'
    assert 'independently verified Home' in a.client.evaluate.call_args.args[1]['ready']['instructions']
    ack(a, ready)
    a.client.evaluate.return_value['answers']['target'] = dict(confidence=.99, box=dict(left=.1, top=.04, right=.8, bottom=.08))
    launch = AsyncMock(side_effect=AssertionError('App must not relaunch'))
    monkeypatch.setattr('hidagent.shortcut_workflow.next_shortcut_actions', launch)
    out = next_actions(a, goal)
    assert out[0]['action'] == 'tap' and out[0]['route_used'] == 'pointer'
    launch.assert_not_awaited()


@pytest.mark.parametrize('case', ['text', 'submit', 'uncertain_key', 'not_home', 'blocked', 'stale', 'bad_box'])
def test_launch_fallback_requires_absent_effect_and_certain_input_ledger(monkeypatch, case):
    a, goal = x_search_open_failure(monkeypatch)
    if case == 'text': a.steps.insert(0, dict(workflow='keyboard', action='type', phase='query', text='X', act_s=.1))
    if case == 'submit': a.steps.insert(0, dict(workflow='keyboard', action='key', phase='launch', combo='enter', act_s=.1))
    if case == 'uncertain_key': a.steps[2].pop('act_s')
    replies = a.client.evaluate.side_effect
    # Materialize AsyncMock's iterator to modify the independent Home check.
    replies = list(replies)
    if case == 'not_home': replies[1]['answers']['ready']['noul'] = 0
    if case == 'blocked': replies[1]['answers']['blocked']['noul'] = 1
    if case == 'bad_box': replies[1]['answers']['target']['box']['right'] = 2
    async def evaluate(*args):
        reply = replies.pop(0)
        if case == 'stale' and not replies: a.screen.frame = b'changed'
        return reply
    a.client.evaluate.side_effect = evaluate
    if case == 'text':
        out = next_actions(a, goal)
        assert all(s['action'] == 'wait' for s in out)
    else:
        with pytest.raises(DecisionUnavailable): next_actions(a, goal)


def test_pointer_launch_unknown_receipt_never_relaunches(monkeypatch):
    a, goal = x_search_open_failure(monkeypatch)
    out = next_actions(a, goal)
    a.steps.extend(out)  # No ACK.
    with pytest.raises(DecisionUnavailable, match='uncertain'): next_actions(a, goal)


@pytest.mark.parametrize('phase', ['query', 'submit'])
def test_keyboard_fallback_preserves_completed_app_launch(monkeypatch, phase):
    a, goal = agent(monkeypatch)
    a.steps.insert(0, dict(workflow='keyboard', action='key', combo='enter', phase='launch', act_s=.1))
    ack(a, next_actions(a, goal))
    ack(a, next_actions(a, goal))
    if phase == 'submit': ack(a, next_actions(a, goal))
    a.client.evaluate.return_value['answers']['ready']['noul'] = 0
    a.client.evaluate.return_value['answers']['results']['noul'] = 0
    launch = AsyncMock(side_effect=AssertionError('App must not relaunch'))
    monkeypatch.setattr('hidagent.shortcut_workflow.next_shortcut_actions', launch)
    out = next_actions(a, goal)
    assert out[0]['route_used'] == 'pointer'
    assert not any(s['action'] == 'type' or s.get('combo') == 'enter' for s in out)
    launch.assert_not_awaited()


@pytest.mark.parametrize('change', ['frame', 'binding', 'duplicate'])
def test_pointer_launch_fallback_rechecks_target_before_click(monkeypatch, change):
    a, goal = agent(monkeypatch)
    executor = HybridAgent.__new__(HybridAgent)
    executor.__dict__.update(a.__dict__)
    a = executor
    monkeypatch.setattr('hidagent.agent.Agent._do', AsyncMock(side_effect=AssertionError('Fallback must use guarded pointer execution')))
    a.task = PREFIX + json.dumps(goal)
    a.phone.tap = AsyncMock()
    a.phone.last_tap_timing = None
    a.phone.target_info = lambda: dict(device='ipad', orientation='landscape')
    action = dict(action='tap', workflow='keyboard', phase='icon_launch',
                  x=150, y=700, _pointer_launch_fallback='X',
                  _pointer_launch_binding=['ipad', 'landscape', None, None, None],
                  _control_box=dict(left=.1, top=.6, right=.2, bottom=.8), _shot=a.screen.frame)
    a.steps = []
    a.phone.tap = AsyncMock(side_effect=lambda *args, **kw: kw['pre_click']())
    if change == 'frame':
        changed = io.BytesIO()
        Image.new('RGB', (100, 100), 'black').save(changed, format='PNG')
        a.screen.frame = changed.getvalue()
    if change == 'binding': a.phone.target_info = lambda: dict(device='ipad', orientation='portrait')
    if change == 'duplicate': a.steps.append({**action, 'act_s': .1})
    with pytest.raises(DecisionUnavailable): asyncio.run(HybridAgent._do_impl(a, action))
    a.phone.tap.assert_not_awaited()


def test_pointer_launch_fallback_refuses_changed_target_during_move(monkeypatch):
    a, goal = agent(monkeypatch)
    executor = HybridAgent.__new__(HybridAgent)
    executor.__dict__.update(a.__dict__)
    a = executor
    monkeypatch.setattr('hidagent.agent.Agent._do', AsyncMock(side_effect=AssertionError('Fallback must use guarded pointer execution')))
    a.task = PREFIX + json.dumps(goal)
    a.phone.last_tap_timing = None
    picture = Image.new('RGB', (200, 200), 'black')
    for x in range(200):
        for y in range(200): picture.putpixel((x, y), (255, 255, 255) if (x+y)%2 else (0, 0, 0))
    frame = io.BytesIO(); picture.save(frame, format='PNG')
    a.screen.frame = frame.getvalue()
    action = dict(action='tap', workflow='keyboard', phase='icon_launch',
                  x=150, y=700, _pointer_launch_fallback='X',
                  _pointer_launch_binding=['ipad', 'landscape', None, None, None],
                  _control_box=dict(left=.1, top=.6, right=.2, bottom=.8), _shot=a.screen.frame)
    async def tap(*args, **kw):
        assert kw['pre_click']()
        a.screen.frame = b'changed'
        assert kw['pre_click']() is False
    a.phone.tap = tap
    asyncio.run(HybridAgent._do_impl(a, action))


@pytest.mark.parametrize('error', [RuntimeError('lost click receipt'), asyncio.CancelledError()])
def test_pointer_launch_exception_blocks_unrecorded_retry(monkeypatch, error):
    a, goal = x_search_open_failure(monkeypatch)
    replies = list(a.client.evaluate.side_effect)
    a.client.evaluate.side_effect = replies
    out = next_actions(a, goal)
    a._do_impl = AsyncMock(side_effect=error)
    with pytest.raises(type(error)): asyncio.run(HybridAgent._do(a, out[0]))
    a.client.evaluate.side_effect = replies
    with pytest.raises(DecisionUnavailable, match='uncertain'): next_actions(a, goal)
