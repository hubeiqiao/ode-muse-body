"""Offline navigation replay: all model decisions and HID delivery are simulated."""
import asyncio
import io
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageOps

from hidagent.agent import Agent
from hidagent.exploration_round import RoundRejected
from test_exploration_landscape import FINDING, source_fixture

URL = 'https://en.wikipedia.org/wiki/Axolotl'
PLAN = {'topic': 'Regenerating animals', 'query': 'Axolotl', 'public_search_url': URL}


@pytest.fixture(autouse=True)
def navigation_clock(monkeypatch):
    # Advance only the navigation pacing clock; leases retain the real clock.
    clock = [0.0]
    async def sleep(seconds):
        clock[0] += seconds
    monkeypatch.setattr('hidagent.exploration_navigation.monotonic', lambda: clock[0], raising=False)
    monkeypatch.setattr('hidagent.exploration_navigation.sleep', sleep, raising=False)
    return clock


async def replay_agent(monkeypatch, *, plan=None):
    d, r, stub = await source_fixture()
    a = Agent(stub.phone, stub.screen, stub.actor, stub.actor, stub.trace)
    a.client = stub.client
    a.client.evaluate.return_value['answers'].update(clear={'noul': 1.0}, overlay={'noul': 0.0})
    a.inputs = []
    a._replan = AsyncMock()
    a._observe = AsyncMock()
    async def deliver(action):
        a.inputs.append(dict(action))
        if action.get('phase') == 'passage_scroll':
            # Simulated article movement; a HID receipt alone is not progress.
            buf = io.BytesIO()
            ImageOps.invert(Image.open(io.BytesIO(a.screen.frame)).convert('RGB')).save(buf, format='PNG')
            a.screen.frame = buf.getvalue()
    a._do = AsyncMock(side_effect=deliver)
    a.actor.chat.return_value = json.dumps(FINDING)
    d.agent = a
    monkeypatch.setattr('hidagent.exploration_agent.choose_topic', AsyncMock(return_value=plan or PLAN))
    return d, r, a


def pairs(a):
    return [(s['action'], s.get('combo', s.get('text'))) for s in a.inputs if s['action'] != 'wait']


def test_real_run_reobservations_do_not_spend_navigation_budget(monkeypatch):
    async def run():
        # Include the preceding Spotlight launch + judgment wait: eight old loop
        # iterations ended immediately after cmd+l, before typing the known URL.
        d, r, a = await replay_agent(monkeypatch, plan={'topic': 'Animals', 'query': 'Axolotl', 'app': 'Wikipedia'})
        sequence = [
            {'action': 'key', 'combo': 'cmd+space'}, {'action': 'type', 'text': 'Safari'},
            {'action': 'wait', 'thought': 'Screen changed during judgment; observe again.'},
            {'action': 'key', 'combo': 'enter'},
            *[{'action': 'wait', 'thought': 'Screen changed during planning; observe again.'} for _ in range(3)],
            {'action': 'key', 'combo': 'cmd+l'}, {'action': 'type', 'text': URL},
            {'action': 'key', 'combo': 'enter'}, {'action': 'wait'}, {'action': 'done'}]
        a._next_actions = AsyncMock(side_effect=[[s] for s in sequence])
        original = a.max_steps, a._next_actions, a._do, a.client
        try:
            await d(r, lambda _: None)
            assert not any(s.get('reason') == 'step limit reached' for s in a.steps)
            assert ('type', URL) in pairs(a)
            assert r.action_count == 6
            assert r.evidence
            assert (a.max_steps, a._next_actions, a._do, a.client) == original
        finally:
            await r.stop()
    asyncio.run(run())


def route_judge(a, *, overlay=None, safari=True, loading=0):
    """Judge only the actual emulated foreground state, never real device IO."""
    page_checks = 0
    async def evaluate(state, questions, image=None):
        nonlocal page_checks
        phase = state.get('phase')
        dismissals = sum(s.get('phase') == 'dismiss_overlay' for s in a.inputs)
        launch = any(s.get('phase') == 'launch' for s in a.inputs)
        answers = {k: {'noul': 1.0} for k in questions}
        for k in ('blocked', 'system_blocker', 'overlay', 'spotlight', 'safari', 'safari_hit'):
            if k in answers: answers[k]['noul'] = 0.0
        if 'overlay' in answers: answers['overlay']['noul'] = float(bool(overlay and not dismissals))
        if 'safari' in answers:
            answers['safari']['noul'] = float(safari or launch)
            answers['ready']['noul'] = float(safari or launch)
        if 'surface' in questions:
            answers['surface'] = {'confidence': 1., 'box': {'left': .2, 'right': .8, 'top': .25, 'bottom': .9}}
        if phase == 'url_ready':
            page_checks += 1
            if page_checks <= loading: answers['ready']['noul'] = 0.0
        return {'seconds': 0, 'model': 'jev-latest', 'approved': True, 'answers': answers}
    a.client.evaluate.side_effect = evaluate


@pytest.mark.parametrize('overlay', [None, 'Control Center', 'Screen Mirroring', 'Spotlight', 'notification'])
def test_url_route_reuses_safari_and_dismisses_system_overlay(monkeypatch, overlay):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a, overlay=overlay)
        a._next_actions = AsyncMock(side_effect=AssertionError('Known URL must not use generic planner'))
        try:
            await d(r, lambda _: None)
            assert pairs(a) == ([('key', 'esc')] if overlay else []) + [
                ('key', 'cmd+t'), ('type', URL), ('key', 'enter')]
            assert r.action_count == (4 if overlay else 3)
            assert r.action_count <= r.max_actions
            assert r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


def test_url_route_launches_safari_then_uses_known_address(monkeypatch):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a, safari=False)
        # Existing app launcher remains responsible for verifying the installed
        # Safari top hit. Simulate its normal three-input launch, no tap changes.
        launch = AsyncMock(side_effect=[
            [{'action': 'key', 'combo': 'cmd+space'}], [{'action': 'type', 'text': 'Safari'}],
            [{'action': 'key', 'combo': 'enter', 'phase': 'launch'}], [{'action': 'done'}]])
        monkeypatch.setattr('hidagent.shortcut_workflow.next_shortcut_actions', launch)
        a._next_actions = AsyncMock(side_effect=AssertionError('Known URL must not use generic planner'))
        try:
            await d(r, lambda _: None)
            assert pairs(a) == [('key', 'cmd+space'), ('type', 'Safari'), ('key', 'enter'),
                                ('key', 'cmd+t'), ('type', URL), ('key', 'enter')]
            assert r.action_count == 6
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('foreground', ['home', 'x', 'safari'])
def test_saved_landscape_foreground_recovers_rejected_focus_candidate(monkeypatch, foreground):
    """Real saved frames; judgments, app launching and HID delivery are simulated."""
    # Unmodified 1646x1080 frames: Home = 20261102-155101/003.jpg,
    # Safari = 20261102-155027/003.jpg. X provenance is in test_exploration_landscape.
    async def run():
        if not (Path(__file__).parent/'fixtures'/f'exploration-{foreground}-landscape.jpg').exists():
            pytest.skip('Private device frames excluded from public snapshot')
        d, r, a = await replay_agent(monkeypatch)
        frame = (Path(__file__).parent/'fixtures'/f'exploration-{foreground}-landscape.jpg').read_bytes()
        a.screen.frame = frame
        safari_frame = (Path(__file__).parent/'fixtures'/'exploration-safari-landscape.jpg').read_bytes()
        deliver = a._do.side_effect
        async def foreground_after_launch(action):
            await deliver(action)
            if action.get('phase') == 'launch':
                a.screen.frame = safari_frame
        a._do.side_effect = foreground_after_launch
        a.actor.chat.return_value = json.dumps({**FINDING, 'app': 'Safari', 'title': 'Axolotl',
            'attribution': '', 'url': 'https://en.wikipedia.org',
            'quote': 'the adults remain fully aquatic', 'claim': 'Adult axolotls remain fully aquatic.'})
        route_judge(a, safari=foreground == 'safari')
        base = a.client.evaluate.side_effect
        async def verdict(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == 'url_focus' and not result['answers']['safari']['noul']:
                # The 15:51 Home trace rejected cmd+L, not launching Safari.
                assert image == frame
                result['approved'] = False
                result['answers']['exploration_navigation'] = {'noul': 0.0}
                # Once Spotlight opens it may itself be classified as a system
                # blocker for cmd+T. Its launcher must still finish its own checks.
                result['answers']['system_blocker'] = {'noul': float(bool(a.inputs))}
            return result
        a.client.evaluate.side_effect = verdict
        launch = AsyncMock(side_effect=[
            [{'action': 'key', 'combo': 'cmd+space'}], [{'action': 'type', 'text': 'Safari'}],
            [{'action': 'key', 'combo': 'enter', 'phase': 'launch'}], [{'action': 'done'}]])
        monkeypatch.setattr('hidagent.shortcut_workflow.next_shortcut_actions', launch)
        a._next_actions = AsyncMock(side_effect=AssertionError('Must use bounded URL route'))
        try:
            await d(r, lambda _: None)
            expected_launch = [] if foreground == 'safari' else [
                ('key', 'cmd+space'), ('type', 'Safari'), ('key', 'enter')]
            assert pairs(a) == expected_launch + [('key', 'cmd+t'), ('type', URL), ('key', 'enter')]
            assert launch.await_count == (0 if foreground == 'safari' else 4)
            for call in launch.await_args_list:
                assert call.args == (a, {'app': 'Safari', 'text': None})
            assert r.evidence and r.action_count == len(expected_launch) + 3
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('safari,blocker,stale', [
    (0., .8, False), (0., None, False), (0., True, False),
    (None, 0., False), (.5, 0., False), (True, 0., False), (0., 0., True),
])
def test_foreground_recovery_requires_fresh_clear_non_safari_proof(monkeypatch, safari, blocker, stale):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a, safari=False)
        base = a.client.evaluate.side_effect
        async def verdict(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == 'url_focus':
                result['approved'] = False
                result['answers'].update(safari={'noul': safari}, system_blocker={'noul': blocker})
            return result
        a.client.evaluate.side_effect = verdict
        if stale:
            calls = 0
            def changed(*_):
                nonlocal calls
                calls += 1
                return calls > 1
            monkeypatch.setattr('hidagent.exploration_navigation.scene_changed', changed)
        launch = AsyncMock()
        monkeypatch.setattr('hidagent.shortcut_workflow.next_shortcut_actions', launch)
        try:
            with pytest.raises(RoundRejected, match='url_focus.*5 observations'):
                await d(r, lambda _: None)
            launch.assert_not_awaited()
            assert not pairs(a) and not r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('combo,approved', [('cmd+t', True), ('cmd+l', False)])
def test_new_tab_is_the_only_relaxed_focus_candidate(combo, approved):
    from hidagent.exploration_agent import NavigationJudge
    async def run():
        base = AsyncMock()
        base.evaluate.return_value = {'approved': True, 'answers': {
            'ready': {'noul': .9}, 'exploration_navigation': {'noul': .9}}}
        result = await NavigationJudge(base).evaluate({
            'policy': 'exploration_navigation', 'phase': 'url_focus', 'public_url': URL,
            'candidate_action': {'action': 'key', 'combo': combo}}, {})
        assert result['approved'] is approved
    asyncio.run(run())


def test_waits_remain_bounded_separately_from_inputs(monkeypatch):
    async def run():
        d, r, a = await replay_agent(monkeypatch, plan={'topic': 'Tools', 'query': 'Tools', 'app': 'X'})
        a._next_actions = AsyncMock(return_value=[{'action': 'wait'}])
        try:
            with pytest.raises(RoundRejected, match='observation'):
                await d(r, lambda _: None)
            assert a._next_actions.await_count == 30
            assert r.action_count == 0 and not r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('inputs', [8, 9])
def test_eight_inputs_allowed_but_ninth_never_delivered(monkeypatch, inputs):
    async def run():
        d, r, a = await replay_agent(monkeypatch, plan={'topic': 'Tools', 'query': 'Tools', 'app': 'X'})
        a._next_actions = AsyncMock(side_effect=[
            *[[{'action': 'key', 'combo': f'test-{i}'}] for i in range(inputs)],
            [{'action': 'wait'}], [{'action': 'done'}]])
        try:
            if inputs == 9:
                with pytest.raises(RoundRejected, match='navigation limit'):
                    await d(r, lambda _: None)
                assert not r.evidence
            else:
                await d(r, lambda _: None)
                assert r.evidence
            assert len(pairs(a)) == r.action_count == 8
        finally:
            await r.stop()
    asyncio.run(run())


def test_loading_reobservations_do_not_repeat_url_input(monkeypatch):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a, loading=2)
        try:
            await d(r, lambda _: None)
            assert pairs(a) == [('key', 'cmd+t'), ('type', URL), ('key', 'enter')]
            assert r.observation_count == 6 and r.action_count == 3
            assert r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['url_type', 'url_submit'])
def test_unproven_focus_or_submit_fails_after_five_observations(monkeypatch, phase):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        async def deny(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == phase: result['answers']['ready']['noul'] = .79
            return result
        a.client.evaluate.side_effect = deny
        try:
            with pytest.raises(RoundRejected, match=phase+'.*5 observations'):
                await d(r, lambda _: None)
            checks = [c for c in a.client.evaluate.call_args_list if c.args[0].get('phase') == phase]
            assert len(checks) == 5
            assert pairs(a) == [('key', 'cmd+t')] + ([('type', URL)] if phase == 'url_submit' else [])
            assert not r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('phase,count', [('url_focus', 1), ('url_type', 2), ('url_submit', 3)])
def test_uncertain_url_delivery_stops_once_and_restores_executor(monkeypatch, phase, count):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        deliver = a._do.side_effect
        async def uncertain(action):
            await deliver(action)
            if action.get('phase') == phase: raise TimeoutError('lost input acknowledgement')
        a._do.side_effect = uncertain
        original = a.max_steps, a._next_actions, a._do, a.client
        try:
            with pytest.raises(RoundRejected, match='uncertain input'):
                await d(r, lambda _: None)
            assert pairs(a) == [('key', 'cmd+t'), ('type', URL), ('key', 'enter')][:count]
            assert r.action_count == count and not r.evidence
            assert (a.max_steps, a._next_actions, a._do, a.client) == original
        finally:
            await r.stop()
    asyncio.run(run())


def test_skipped_batch_candidate_does_not_consume_an_input(monkeypatch):
    async def run():
        d, r, a = await replay_agent(monkeypatch, plan={'topic': 'Tools', 'query': 'Tools', 'app': 'X'})
        a._next_actions = AsyncMock(side_effect=[[
            {'action': 'key', 'combo': 'esc'}, {'action': 'tap', 'x': 500, 'y': 500},
            {'action': 'type', 'text': 'must not type after a missed tap'}], [{'action': 'done'}]])
        try:
            await d(r, lambda _: None)
            assert pairs(a) == [('key', 'esc'), ('tap', None)]
            assert r.action_count == 2
        finally:
            await r.stop()
    asyncio.run(run())


def test_known_notification_is_dismissed_locally_before_cloud_check(monkeypatch):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        def excluded(frame):
            return None if a.inputs else 'notification_center_known_layout'
        monkeypatch.setattr('hidagent.exploration_navigation.known_excluded_overlay', excluded)
        def privacy(frame):
            assert not excluded(frame), 'notification frame must not reach cloud'
        monkeypatch.setattr('hidagent.exploration_navigation.check_cloud_frame', privacy)
        try:
            await d(r, lambda _: None)
            assert pairs(a) == [('key', 'esc'), ('key', 'cmd+t'), ('type', URL), ('key', 'enter')]
            assert r.action_count == 4
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('ready,blocked', [(.9, .05), (.8, .2)])
def test_moderate_url_judgments_load_source_without_second_focus_gate(monkeypatch, caplog, ready, blocked):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        async def moderate(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') in ('url_focus', 'url_type', 'url_submit'):
                result['answers'].update(ready={'noul': ready}, blocked={'noul': blocked},
                                         exploration_navigation={'noul': ready})
            return result
        a.client.evaluate.side_effect = moderate
        try:
            with caplog.at_level(logging.INFO, logger='hidagent.exploration_navigation'):
                await d(r, lambda _: None)
            assert pairs(a) == [('key', 'cmd+t'), ('type', URL), ('key', 'enter')]
            phases = [c.args[0].get('phase') for c in a.client.evaluate.call_args_list]
            assert phases.count('url_focus') == 1, 'Focus must use a single Safari readiness check'
            assert r.evidence
            for phase in ('url_type', 'url_submit'):
                assert f'phase={phase} ready={ready} blocked={blocked} approved=True' in caplog.text
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['source'])
@pytest.mark.parametrize('ready,blocked,approved', [(.9, .05, True), (.96, .12, True), (1., .02, True), (1., 0., False)])
def test_final_verification_remains_strict(monkeypatch, phase, ready, blocked, approved):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        async def uncertain(state, questions, image=None):
            result = await base(state, questions, image)
            if (state.get('phase') == phase or
                    (phase == 'source' and state.get('policy') == 'exploration_public_source')):
                result.update(approved=approved)
                result['answers'].update(ready={'noul': ready}, blocked={'noul': blocked})
            return result
        a.client.evaluate.side_effect = uncertain
        try:
            with pytest.raises(RoundRejected, match='independent pixel verification'):
                await d(r, lambda _: None)
            assert pairs(a) == [('key', 'cmd+t'), ('type', URL), ('key', 'enter')]
            assert not r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['url_type', 'url_submit'])
@pytest.mark.parametrize('ready,blocked,approved', [(1., 0., False),
                                                  (None, 0., True), (True, 0., True)])
def test_relaxed_url_gate_still_rejects_blocked_or_invalid_judgments(monkeypatch, phase, ready, blocked, approved):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        async def blocked_result(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == phase:
                result.update(approved=approved)
                result['answers'].update(ready={'noul': ready}, blocked={'noul': blocked})
            return result
        a.client.evaluate.side_effect = blocked_result
        try:
            with pytest.raises(RoundRejected, match=phase+'.*5 observations'):
                await d(r, lambda _: None)
            assert not r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('launcher_action', ['done', 'wait', 'replan'])
def test_safari_foreground_waits_are_bounded(monkeypatch, launcher_action):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a, safari=False)
        monkeypatch.setattr('hidagent.shortcut_workflow.next_shortcut_actions',
                            AsyncMock(return_value=[{'action': launcher_action}]))
        try:
            with pytest.raises(RoundRejected, match='url_focus.*5 observations'):
                await d(r, lambda _: None)
            assert not pairs(a) and not r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('change', [
    {'policy': 'other'}, {'phase': 'url_focus'},
    {'public_url': 'https://example.com/'},
    {'candidate_action': {'action': 'type', 'text': 'a reply'}},
    {'candidate_action': {'action': 'key', 'combo': 'cmd+enter'}},
    {'candidate_action': {'action': 'tap', 'target': 'Like'}},
])
def test_relaxed_navigation_judge_is_scoped_to_exact_public_url_input(change):
    from hidagent.exploration_agent import NavigationJudge
    async def run():
        base = AsyncMock()
        base.evaluate.return_value = {'approved': True, 'answers': {
            'ready': {'noul': .9}, 'exploration_navigation': {'noul': .9}}}
        state = {'policy': 'exploration_navigation', 'phase': 'url_type', 'public_url': URL,
                 'candidate_action': {'action': 'type', 'text': URL}, **change}
        result = await NavigationJudge(base).evaluate(state, {})
        assert result['approved'] is False
    asyncio.run(run())


def test_scene_changes_stop_url_phase_after_five_observations(monkeypatch):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        # Start and Safari confirmation are stable; every subsequent URL-focus
        # proof becomes stale while the judge evaluates it.
        calls = 0
        def changed(*_):
            nonlocal calls
            calls += 1
            return calls > 2
        monkeypatch.setattr('hidagent.exploration_navigation.scene_changed', changed)
        try:
            with pytest.raises(RoundRejected, match='url_type.*5 observations'):
                await d(r, lambda _: None)
            assert pairs(a) == [('key', 'cmd+t')]
            assert not r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('verdicts', [
    [(0., 0., False), (.96, .12, True), (.96, .12, True)],
    [(0., 0., False)] * 4 + [(.9, .2, True)],
    [(0., 0., False)] * 4 + [(.9, .2, None)],
])
def test_loading_then_banner_proceeds_to_independent_source_verification(monkeypatch, navigation_clock, verdicts):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        checks = []
        async def logged(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == 'url_ready':
                ready, blocked, approved = verdicts[len(checks)]
                checks.append(navigation_clock[0])
                result.update(approved=approved)
                result['answers'].update(ready={'noul': ready}, blocked={'noul': blocked})
            return result
        a.client.evaluate.side_effect = logged
        try:
            await d(r, lambda _: None)
            assert len(checks) == (2 if len(verdicts) == 3 else 5)
            assert all(b - a >= 1.5 for a, b in zip(checks, checks[1:]))
            assert len(checks) < 5 or checks[-1] - checks[0] >= 8
            policies = [c.args[0].get('policy') for c in a.client.evaluate.call_args_list]
            assert policies[-1] == 'exploration_public_source'
            a.actor.chat.assert_awaited_once()
            assert r.evidence and r.evidence[0]['verifier'] == 'jev-latest'
            assert pairs(a) == [('key', 'cmd+t'), ('type', URL), ('key', 'enter')]
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('ready,blocked,approved', [
    (.799, 0., True), (1., 0., False),
    (None, 0., True), (True, 0., True),
])
def test_url_ready_failure_allows_eight_seconds_but_remains_bounded(monkeypatch, navigation_clock, ready, blocked, approved):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        checks = []
        async def loading(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == 'url_ready':
                checks.append(navigation_clock[0])
                result.update(approved=approved)
                result['answers'].update(ready={'noul': ready}, blocked={'noul': blocked})
            return result
        a.client.evaluate.side_effect = loading
        try:
            with pytest.raises(RoundRejected, match='url_ready.*5 observations'):
                await d(r, lambda _: None)
            assert len(checks) == 5 and checks[-1] - checks[0] >= 8
            assert all(b - a >= 1.5 for a, b in zip(checks, checks[1:]))
            a.actor.chat.assert_not_awaited()
            assert not r.evidence
            assert pairs(a) == [('key', 'cmd+t'), ('type', URL), ('key', 'enter')]
        finally:
            await r.stop()
    asyncio.run(run())


def test_reader_and_pixel_judge_allow_only_nonobscuring_dismissible_banners(monkeypatch):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        try:
            await d(r, lambda _: None)
            reader = a.actor.chat.call_args.args[0].lower()
            questions = a.client.evaluate.call_args.args[1]
            for prompt in (reader, questions['ready']['instructions'].lower(), questions['blocked']['instructions'].lower()):
                assert 'fundraising' in prompt and 'cookie' in prompt
                assert 'title' in prompt and 'passage' in prompt and 'chrome' in prompt
                assert 'donat' in prompt
        finally:
            await r.stop()
    asyncio.run(run())


URL_PHASES = ('url_focus', 'url_type', 'url_submit', 'url_ready')


@pytest.mark.parametrize('typing', [(.94, .39), (.97, .25), (.95, .25)])
def test_logged_1527_typing_and_1519_page_verdicts_reach_source(monkeypatch, typing):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        async def logged(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == 'url_type':
                result['answers'].update(ready={'noul': typing[0]}, blocked={'noul': typing[1]})
            if state.get('phase') == 'url_ready':
                result['answers'].update(ready={'noul': .96}, blocked={'noul': .12})
            return result
        a.client.evaluate.side_effect = logged
        try:
            await d(r, lambda _: None)
            assert pairs(a) == [('key', 'cmd+t'), ('type', URL), ('key', 'enter')]
            assert a.client.evaluate.call_args.args[0]['policy'] == 'exploration_public_source'
            assert r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('phase', URL_PHASES)
@pytest.mark.parametrize('blocked', [.39, 1., None, float('nan')])
def test_all_navigation_phases_ignore_noisy_blocked_score(monkeypatch, phase, blocked):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        async def verdict(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == phase:
                result.pop('approved')
                result['answers'].update(ready={'noul': .8}, blocked={'noul': blocked})
            return result
        a.client.evaluate.side_effect = verdict
        try:
            await d(r, lambda _: None)
            assert r.evidence
            assert pairs(a) == [('key', 'cmd+t'), ('type', URL), ('key', 'enter')]
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('phase', URL_PHASES)
@pytest.mark.parametrize('blocker,approved', [(.8, True), (1., None), (0., False)])
def test_every_navigation_phase_paces_five_blocking_observations(monkeypatch, navigation_clock, phase, blocker, approved):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        checks = []
        async def verdict(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == phase:
                checks.append(navigation_clock[0])
                result.update(approved=approved)
                result['answers'].update(ready={'noul': 1.}, system_blocker={'noul': blocker})
                prompt = questions['system_blocker']['instructions'].lower()
                assert all(word in prompt for word in ('system overlay', 'login', 'permission'))
            return result
        a.client.evaluate.side_effect = verdict
        try:
            with pytest.raises(RoundRejected, match=phase+'.*5 observations'):
                await d(r, lambda _: None)
            assert len(checks) == 5 and checks[-1] - checks[0] >= 8
            assert all(b - a >= 1.5 for a, b in zip(checks, checks[1:]))
            a.actor.chat.assert_not_awaited()
            assert not r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('phase', URL_PHASES)
def test_every_navigation_phase_can_recover_on_fifth_observation(monkeypatch, navigation_clock, phase):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        checks, all_checks = [], []
        async def verdict(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') in URL_PHASES:
                all_checks.append(navigation_clock[0])
            if state.get('phase') == phase:
                checks.append(navigation_clock[0])
                result['answers'].update(ready={'noul': .79 if len(checks) < 5 else .8},
                                         system_blocker={'noul': .79}, blocked={'noul': 1.})
            return result
        a.client.evaluate.side_effect = verdict
        try:
            await d(r, lambda _: None)
            assert len(checks) == 5 and checks[-1] - checks[0] >= 8
            assert all(b - a >= 1.5 for a, b in zip(all_checks, all_checks[1:]))
            assert pairs(a) == [('key', 'cmd+t'), ('type', URL), ('key', 'enter')]
            assert r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('missing_screens', [0, 1, 2, 3])
def test_article_passage_search_rereads_each_page_and_accepts_visible_lead(monkeypatch, missing_screens):
    """Simulated lead/section fixture; no live device or model verification."""
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        passages = []
        finding = {**FINDING, 'app': 'Safari', 'title': 'Axolotl', 'attribution': '',
                   'url': 'https://en.wikipedia.org',
                   'quote': 'Axolotls can regenerate their limbs.' if missing_screens else 'The axolotl is a paedomorphic salamander.',
                   'claim': 'Axolotls can regenerate their limbs.' if missing_screens else 'The axolotl is a paedomorphic salamander.'}
        a.actor.chat.return_value = json.dumps(finding)
        async def article(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == 'url_ready':
                pages = sum(s.get('phase') == 'passage_scroll' for s in a.inputs)
                passages.append(pages)
                result['answers']['passage'] = {'noul': float(pages >= missing_screens)}
            return result
        a.client.evaluate.side_effect = article
        try:
            await d(r, lambda _: None)
            assert passages == ([0] if missing_screens else []) + list(range(missing_screens + 1))
            assert pairs(a) == [('key', 'cmd+t'), ('type', URL), ('key', 'enter')] + ([('key', 'esc')] + [('scroll', None)] * missing_screens if missing_screens else [])
            assert r.action_count == 3 + missing_screens + bool(missing_screens)
            assert r.evidence[0]['quote'] == finding['quote']
            reader = a.actor.chat.call_args.args[0].lower()
            final_check = a.client.evaluate.call_args.args[1]['ready']['instructions'].lower()
            assert a.client.evaluate.call_args.args[0]['proposal_url'] == URL
            assert 'browser chrome to show the host in proposal_url' in final_check
            for prompt in (reader, final_check):
                assert 'same article' in prompt and 'pre-planned' in prompt
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('passage', [0., .79, True, None])
def test_missing_article_passage_stops_after_three_scrolls_without_evidence(monkeypatch, passage):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        async def missing(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == 'url_ready':
                result['answers']['passage'] = {'noul': passage}
            return result
        a.client.evaluate.side_effect = missing
        try:
            with pytest.raises(RoundRejected, match='passage'):
                await d(r, lambda _: None)
            assert pairs(a)[3:] == [('key', 'esc')] + [('scroll', None)] * 3
            assert r.action_count == 7 and not r.evidence
            a.actor.chat.assert_not_awaited()
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['focus', 'uncertain', 'judge'])
def test_passage_recovery_keeps_focus_delivery_and_final_evidence_fail_closed(monkeypatch, failure):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        async def verdict(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == 'url_ready':
                result['answers']['passage'] = {'noul': float(any(s.get('phase') == 'passage_scroll' and s['action'] == 'scroll' for s in a.inputs))}
            if state.get('phase') == 'passage_scroll' and failure == 'focus':
                result['answers']['ready']['noul'] = .98
            if state.get('policy') == 'exploration_public_source':
                result['answers']['ready']['noul'] = .98
            return result
        a.client.evaluate.side_effect = verdict
        deliver = a._do.side_effect
        async def uncertain(action):
            await deliver(action)
            if action.get('phase') == 'passage_scroll':
                raise TimeoutError('scroll receipt lost')
        if failure == 'uncertain': a._do.side_effect = uncertain
        try:
            with pytest.raises(RoundRejected):
                await d(r, lambda _: None)
            assert not r.evidence
            assert pairs(a)[3:] == [('key', 'esc')] + ([] if failure == 'focus' else [('scroll', None)])
            if failure != 'judge': a.actor.chat.assert_not_awaited()
        finally:
            await r.stop()
    asyncio.run(run())


def test_launch_navigation_and_three_passage_pages_fit_twelve_input_service_budget(monkeypatch):
    from hidagent.exploration_service import ExplorationService
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        r.max_actions = ExplorationService(None, None, None).max_actions
        assert r.max_actions == 12 and r.seconds == 90
        route_judge(a, safari=False)
        base = a.client.evaluate.side_effect
        async def article(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == 'url_ready':
                count = sum(s.get('phase') == 'passage_scroll' and s['action'] == 'scroll' for s in a.inputs)
                result['answers']['passage'] = {'noul': float(count == 3)}
            return result
        a.client.evaluate.side_effect = article
        monkeypatch.setattr('hidagent.shortcut_workflow.next_shortcut_actions', AsyncMock(side_effect=[
            [{'action': 'key', 'combo': 'cmd+space'}], [{'action': 'type', 'text': 'Safari'}],
            [{'action': 'key', 'combo': 'enter', 'phase': 'launch'}], [{'action': 'done'}]]))
        try:
            await d(r, lambda _: None)
            assert r.action_count == len(pairs(a)) == 10 and r.evidence
        finally:
            await r.stop()
    asyncio.run(run())

@pytest.mark.parametrize('passage', [.9, .97, .98])
def test_visible_lead_goes_to_strict_source_reader_without_scroll(monkeypatch, passage):
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        async def verdict(state, questions, image=None):
            result = await base(state, questions, image)
            if state.get('phase') == 'url_ready': result['answers']['passage']['noul'] = passage
            return result
        a.client.evaluate.side_effect = verdict
        try:
            await d(r, lambda _: None)
            assert pairs(a) == [('key', 'cmd+t'), ('type', URL), ('key', 'enter')]
            assert r.evidence
        finally: await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('alternate_moves', [True, False])
def test_focused_address_scroll_escapes_uses_pointer_then_one_alternate(monkeypatch, alternate_moves):
    from hidagent.exploration_navigation import ExplorationNavigation
    async def run():
        _, r, a = await replay_agent(monkeypatch)
        route_judge(a)
        base = a.client.evaluate.side_effect
        async def verdict(state, questions, image=None):
            result = await base(state, questions, image)
            if 'surface' in questions:
                result['answers']['surface'] = {'confidence': 1., 'box': {'left': .2, 'right': .8, 'top': .25, 'bottom': .9}}
            if state.get('phase') == 'url_ready': result['answers']['passage']['noul'] = 0.
            return result
        a.client.evaluate.side_effect = verdict
        nav = ExplorationNavigation(a, PLAN, AsyncMock())
        nav.started = True
        a.steps = [{'workflow': 'exploration_url', 'phase': phase, 'action': 'key', 'act_s': 0}
                   for phase in ('url_focus', 'url_type', 'url_submit')]
        async def sent():
            action = (await nav.next())[0]
            a.steps.append({**action, 'act_s': 0})
            return action
        try:
            escape = await sent()
            assert (escape['action'], escape.get('combo')) == ('key', 'esc')
            pointer = await sent()
            assert pointer['action'] == 'scroll' and pointer['lines'] < 0
            assert 200 < pointer['x'] < 800 and 250 < pointer['y'] < 900
            alternate = await sent()  # unchanged article, despite pointer receipt
            assert (alternate['action'], alternate.get('combo')) == ('key', 'space')
            if alternate_moves:
                buf = io.BytesIO()
                ImageOps.invert(Image.open(io.BytesIO(a.screen.frame)).convert('RGB')).save(buf, format='PNG')
                a.screen.frame = buf.getvalue()
                assert (await sent())['action'] == 'scroll'  # progress permits next page
            else:
                with pytest.raises(RoundRejected, match='did not move.*pointer.*keyboard'):
                    await sent()
        finally: await r.stop()
    asyncio.run(run())


def test_pointer_scroll_dispatch_uses_existing_phone_transport():
    from types import SimpleNamespace
    from hidagent.agent import _pointer_path
    async def run():
        phone = SimpleNamespace(scroll=AsyncMock())
        a = Agent(phone, None, None, None, None)
        action = {'action': 'scroll', 'x': 530, 'y': 640, 'lines': -6}
        await a._do(action)
        phone.scroll.assert_awaited_once_with(.53, .64, -6)
        assert _pointer_path(action) == [(.53, .64)]
    asyncio.run(run())


def test_failed_navigation_topic_is_excluded_on_next_round(monkeypatch):
    from hidagent.exploration_round import ExplorationRound
    async def run():
        d, r, a = await replay_agent(monkeypatch)
        from hidagent.exploration_agent import choose_topic as select
        route_judge(a)
        select.side_effect = [PLAN, RoundRejected('second selection stopped for test')]
        a._do.side_effect = TimeoutError('uncertain input')
        try:
            with pytest.raises(RoundRejected): await d(r, lambda _: None)
        finally: await r.stop()
        second = await ExplorationRound('ipad:8890', 'Surprise me', AsyncMock()).start()
        try:
            with pytest.raises(RoundRejected, match='second selection'):
                await d(second, lambda _: None)
            assert select.call_args.kwargs['prior_topics'] == [{'topic': PLAN['topic'], 'query': PLAN['query']}]
        finally: await second.stop()
    asyncio.run(run())


def test_toolbar_only_change_does_not_count_as_article_scroll(monkeypatch):
    from hidagent.exploration_navigation import ExplorationNavigation
    from PIL import ImageDraw
    async def run():
        _, r, a = await replay_agent(monkeypatch)
        nav = ExplorationNavigation(a, PLAN, AsyncMock())
        nav.article_box = (.2, .25, .8, .9)
        before = nav.article_frame(a.screen.frame)
        picture = Image.open(io.BytesIO(a.screen.frame)).convert('RGB')
        ImageDraw.Draw(picture).rectangle((0, 0, picture.width, 150), fill='blue')
        buf = io.BytesIO(); picture.save(buf, format='PNG'); a.screen.frame = buf.getvalue()
        nav.pending_scroll = (before, False)
        nav.scroll_passage = AsyncMock(return_value=[{'action': 'key', 'combo': 'space'}])
        try:
            assert (await nav.next())[0]['combo'] == 'space'
            assert nav.alternate_scroll
            nav.scroll_passage.assert_awaited_once()
        finally: await r.stop()
    asyncio.run(run())
