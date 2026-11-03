"""Offline regression contracts; model verdicts are simulated, never device proof."""
import asyncio
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from hidagent.companion import Companion, CompanionMemory, ChatRequest
from hidagent.exploration_round import ExplorationRound, RoundRejected
from hidagent.exploration_voice import is_exploration_intent
from hidagent.live_voice import ToolCall, VoiceBridge, voice_context

EXPLORE = [
    'Explore something surprising for me and bring back one discovery with a source.',
    'Do something by yourself', 'Surprise me', 'Whatever you like',
    'I want you to explore on your own',
]
TARGETS = [
    'Find posts on X about IELTS speaking',
    'Find some potential users from X discussing IELTS speaking',
    'I want you to find some users on X and show me those posts',
    'Search YouTube for IELTS speaking',
    'Explore X for posts about IELTS speaking',
]


@pytest.mark.parametrize('goal', EXPLORE)
def test_independent_request_routes_without_a_topic_question(goal):
    assert is_exploration_intent(goal)
    prompt, tools = voice_context({'target': {'device': 'ipad'}, 'exploration': {'available': True}})
    assert 'Never ask the user for a topic' in prompt
    assert 'At most ONE clarifying question' in prompt
    assert 'Haiku' not in prompt
    assert 'phone_explore immediately' in prompt
    assert 'phone_command immediately' in prompt
    declarations = {t['name']: t for t in tools[0]['functionDeclarations']}
    assert 'surprise me' in declarations['phone_explore']['description'].lower()
    assert 'X' in declarations['phone_command']['description']


@pytest.mark.parametrize('goal', TARGETS)
def test_concrete_target_is_not_independent_exploration(goal):
    assert not is_exploration_intent(goal)


@pytest.mark.parametrize('goal', EXPLORE+TARGETS)
def test_spoken_request_resumes_a_stopped_voice_session(goal):
    from hidagent.control_intent import is_action_intent
    assert is_action_intent(goal)


@pytest.mark.parametrize('goal,selected,expected', [
    ('Surprise me', 'phone_command', 'phone_explore'),
    ('Find posts on X about IELTS speaking', 'phone_explore', 'phone_command'),
])
def test_voice_tools_use_original_words_and_correct_misrouting(goal, selected, expected):
    async def run():
        bridge = VoiceBridge(lambda: {}, AsyncMock(), AsyncMock())
        sid = 's'*32
        bridge.sessions[sid] = {'expires': time.monotonic()+60, 'calls': {}}
        bridge.execute = AsyncMock(return_value={'status': 'in_progress', 'running': True})
        bridge.finish_receipt = lambda call, result: result
        await bridge.call(ToolCall(session=sid, id='route', name=selected,
                                   args={'goal': 'Invented topic'}, utterance=goal))
        assert bridge.execute.call_args.args[:2] == (expected, {'goal': goal})
    asyncio.run(run())


@pytest.mark.parametrize('goal,tool', [(g, 'phone_explore') for g in EXPLORE] + [(g, 'phone_command') for g in TARGETS])
def test_text_chat_uses_the_same_device_route(tmp_path, goal, tool):
    async def run():
        writer = SimpleNamespace(model='fake', chat=AsyncMock(return_value='Should not chat'))
        voice = SimpleNamespace(execute=AsyncMock(return_value={'status': 'in_progress', 'running': True, 'task_id': 'a'*32}),
                                state=lambda: {'agent': {'running': False}})
        ex = SimpleNamespace(start=AsyncMock(return_value={'round_id': 'a'*32}))
        c = Companion(CompanionMemory(tmp_path), writer, ex, voice, AsyncMock())
        await c.chat(ChatRequest(text=goal, request_id='landscape_chat'))
        voice.execute.assert_awaited_once()
        assert voice.execute.call_args.args == (tool, {'goal': goal})
        writer.chat.assert_not_called()
        ex.start.assert_not_called()
    asyncio.run(run())


def test_all_voice_calls_log_name_and_terminal_status_even_early_returns(caplog):
    async def run():
        bridge = VoiceBridge(lambda: {}, AsyncMock(), AsyncMock())
        sid = 's'*32
        bridge.sessions[sid] = {'expires': time.monotonic()+60, 'camera': True}
        result = await bridge.call(ToolCall(session=sid, id='rejected', name='phone_explore', args={'goal': 'Surprise me'}))
        assert result['status'] == 'rejected'
    with caplog.at_level('INFO', logger='hidagent.live_voice'):
        asyncio.run(run())
    assert 'voice tool result name=phone_explore id=rejected status=rejected' in caplog.text


# Original 1646x1080 saved public X frame, unmodified:
# runtime/ipad/traces/20261102-135928/003.jpg (example creator @example_owner post).
FRAME = Path(__file__).parent/'fixtures'/'exploration-x-landscape.jpg'
FINDING = {'source_ready': True, 'app': 'X', 'title': '', 'attribution': '@example_owner', 'url': '',
           'quote': 'I built a skill that turns your og:image into a looping og:video.',
           'claim': 'A public X post by @example_owner describes a skill for turning an og:image into a looping og:video.',
           'observation': 'A public X feed with the example creator’s post is visible.'}


async def source_fixture(data=None, verdict=None):
    if not FRAME.exists(): pytest.skip('Private device frame excluded from public snapshot')
    from hidagent.exploration_agent import AgentExplorationDriver
    frame = FRAME.read_bytes()
    assert Image.open(FRAME).size == (1646, 1080)
    phone = SimpleNamespace(calibrated=True, device=SimpleNamespace(links=1),
                            target_info=lambda: {'device': 'ipad', 'orientation': 'landscape', 'calibration_key': 'ipad-landscape-66'})
    screen = SimpleNamespace(live=True, frame=frame)
    judge = SimpleNamespace(evaluate=AsyncMock(return_value=verdict or {
        'answers': {'ready': {'noul': 1.0}, 'blocked': {'noul': 0.0}}, 'model': 'jev-latest', 'approved': True}))
    writer = SimpleNamespace(model='muse-spark-1.3', chat=AsyncMock(return_value=json.dumps(data or FINDING)))
    agent = SimpleNamespace(phone=phone, screen=screen, client=judge, actor=writer, trace=Mock())
    driver = AgentExplorationDriver(agent)
    r = await ExplorationRound('ipad:8890', 'Surprise me', AsyncMock()).start()
    return driver, r, agent


def test_real_landscape_native_app_source_needs_no_url_bar():
    async def run():
        if not FRAME.exists(): pytest.skip('Private device frame excluded from public snapshot')
        d, r, a = await source_fixture()
        try:
            await d.read_source(r, {'topic': 'public creative tools'})
            assert r.evidence[0]['source'] == 'X · @example_owner'
            assert r.evidence[0]['frame_sha256'] == hashlib.sha256(FRAME.read_bytes()).hexdigest()
            assert r.evidence[0]['quote'] == FINDING['quote']
            assert not r.evidence[0]['url']  # Never invent a permalink.
            assert a.client.evaluate.call_args.args[2] == FRAME.read_bytes()
            assert 'verbatim' in str(a.client.evaluate.call_args).lower()
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('changes', [
    {'attribution': '', 'title': ''}, {'app': 'Messages'},
    {'app': 'Safari', 'url': ''}, {'app': 'Safari', 'url': 'https://127.0.0.1/private'},
    {'source_ready': False, 'observation': 'X search suggestions are visible, but no public post is open.'},
])
def test_unverifiable_source_is_plain_and_never_a_discovery(changes):
    async def run():
        d, r, a = await source_fixture({**FINDING, **changes})
        try:
            with pytest.raises(RoundRejected) as caught:
                await d.read_source(r, {'topic': 'creative tools'})
            assert 'no source image uploaded' not in str(caught.value)
            assert not r.evidence
            if changes.get('source_ready') is False:
                assert 'X search suggestions' in str(caught.value)
        finally:
            await r.stop()
    asyncio.run(run())


def test_independent_judge_rejection_prevents_saved_discovery():
    async def run():
        d, r, a = await source_fixture(verdict={'answers': {'ready': {'noul': .98}, 'blocked': {'noul': 0}}, 'model': 'jev-latest'})
        try:
            with pytest.raises(RoundRejected):
                await d.read_source(r, {'topic': 'creative tools'})
            assert not r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('score', [0, .98, True, float('nan'), None])
def test_navigation_judge_blocks_engagement_missing_and_malformed_verdicts(score):
    from hidagent.exploration_agent import NavigationJudge
    async def run():
        if not FRAME.exists(): pytest.skip('Private device frame excluded from public snapshot')
        base = SimpleNamespace(evaluate=AsyncMock(return_value={'model': 'jev-latest', 'approved': True,
            'answers': {'ready': {'noul': 1.0}, 'exploration_navigation': {'noul': score}}}))
        result = await NavigationJudge(base).evaluate({'candidate_action': {'action': 'tap', 'target': 'Like'}},
            {'ready': {'type': 'noul', 'instructions': 'Ready?'}}, FRAME.read_bytes())
        assert result['approved'] is False
        assert result['answers']['ready']['noul'] == 0
        assert 'lik' in str(base.evaluate.call_args).lower()
    asyncio.run(run())


def navigation_fixture(agent, monkeypatch):
    """Simulated agent lifecycle with the same task-local input lease contract."""
    from hidagent.hid_lease import check_hid
    from hidagent.decision import DecisionUnavailable
    # Lifecycle tests isolate navigation decisions; route tests exercise the
    # actual URL/preflight workflow against the real Agent loop separately.
    monkeypatch.setattr('hidagent.exploration_agent.ExplorationNavigation',
        lambda a, p, fallback: SimpleNamespace(next=fallback, url=None))
    agent.running = False
    agent.max_steps = 40
    agent.steps = []
    agent.inputs = []
    agent._next_actions = AsyncMock(return_value=[{'action': 'done'}])
    agent._job = None
    async def deliver(action):
        check_hid('key example')
        agent.inputs.append(action)
    agent._do = AsyncMock(side_effect=deliver)
    async def loop():
        try:
            for _ in range(agent.max_steps):
                actions = await agent._next_actions()
                for action in actions:
                    if action['action'] == 'done':
                        agent.steps.append(action)
                        return
                    await agent._do(action)
                    agent.steps.append(action)
        except DecisionUnavailable as exc:
            agent.steps.append({'action': 'error', 'reason': str(exc)})
        finally:
            agent.running = False
    def start(goal):
        agent.task = goal
        agent.running = True
        agent._job = asyncio.create_task(loop())
    def stop():
        if agent._job and not agent._job.done():agent._job.cancel()
    agent.start = Mock(side_effect=start)
    agent.stop = Mock(side_effect=stop)
    return agent


def test_agent_round_reuses_executor_and_restores_it_with_bound_verified_evidence(monkeypatch):
    async def run():
        d, r, a = await source_fixture()
        navigation_fixture(a, monkeypatch)
        original = a.client, a.max_steps, a._next_actions
        a.actor.chat.side_effect = [json.dumps({'topic': 'Creative tools', 'query': 'Tools', 'reason': 'Investigate public tools.'}), json.dumps(FINDING)]
        proposals = []
        try:
            await d(r, proposals.append)
            assert (a.client, a.max_steps, a._next_actions) == original
            a.start.assert_called_once()
            assert 'Public reading and search only' in a.task
            assert proposals[0]['kind'] == 'unverified_exploration_proposal'
            assert r.evidence[0]['source'] == 'X · @example_owner'
            assert (await r.finish())['state'] == 'completed'
        finally:
            await r.stop()
    asyncio.run(run())


def test_navigation_rejects_an_oversized_action_batch_before_input(monkeypatch):
    async def run():
        d, r, a = await source_fixture()
        navigation_fixture(a, monkeypatch)
        a._next_actions.return_value = [{'action': 'tap'}]*9
        a.actor.chat.side_effect = [json.dumps({'topic': 'Tools', 'query': 'Tools', 'reason': 'Investigate tools.'})]
        try:
            with pytest.raises(RoundRejected, match='limit'):
                await d(r, lambda p: None)
            assert not a.inputs and not r.evidence
            assert a.max_steps == 40
        finally:
            await r.stop()
    asyncio.run(run())


def test_stop_during_navigation_revokes_inherited_lease_and_never_reads_source(monkeypatch):
    from hidagent.hid_lease import check_hid
    async def run():
        d, r, a = await source_fixture()
        navigation_fixture(a, monkeypatch)
        entered = asyncio.Event()
        blocked = []
        async def stubborn():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                try:check_hid('key late')
                except RoundRejected:blocked.append(True)
                raise
        a._next_actions.side_effect = stubborn
        a.actor.chat.side_effect = [json.dumps({'topic': 'Tools', 'query': 'Tools', 'reason': 'Investigate tools.'})]
        task = asyncio.create_task(d(r, lambda p: None))
        await entered.wait()
        await r.stop()
        with pytest.raises(asyncio.CancelledError):await task
        assert blocked and not a.inputs and not r.evidence
        assert a.actor.chat.await_count == 1
        assert a.max_steps == 40
    asyncio.run(run())


def test_meta_topic_request_excludes_personal_hermes_memory(monkeypatch):
    async def run():
        d, r, a = await source_fixture()
        navigation_fixture(a, monkeypatch)
        a.actor.provider = 'meta'
        d.memory = SimpleNamespace(preferences=Mock(side_effect=AssertionError('private preferences read')),
                                   discoveries=Mock(side_effect=AssertionError('private discoveries read')))
        a.actor.chat.side_effect = [json.dumps({'topic': 'Creative tools', 'query': 'Tools', 'reason': 'Investigate tools.'}), json.dumps(FINDING)]
        try:
            await d(r, lambda p: None)
            request = json.loads(a.actor.chat.call_args_list[0].args[1])
            assert request['explicit_feedback'] == []
            assert request['past_verified_discoveries'] == []
        finally:
            await r.stop()
    asyncio.run(run())


def test_companion_http_route_preserves_words_and_deduplicates(tmp_path):
    import httpx
    from fastapi import FastAPI
    from hidagent.companion import router_for
    async def run():
        voice = SimpleNamespace(execute=AsyncMock(return_value={'status': 'in_progress', 'running': True, 'task_id': 'a'*32}))
        writer = SimpleNamespace(chat=AsyncMock())
        companion = Companion(CompanionMemory(tmp_path), writer, None, voice, AsyncMock())
        app = FastAPI()
        app.include_router(router_for(companion))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            for i, (goal, tool) in enumerate([('Surprise me', 'phone_explore'), ('Find posts on X about IELTS speaking', 'phone_command')]):
                payload = {'text': goal, 'request_id': 'http_route_'+str(i)}
                first = await client.post('/api/companion/chat', json=payload)
                second = await client.post('/api/companion/chat', json=payload)
                assert first.status_code == 200 and first.json() == second.json()
                assert voice.execute.call_args.args == (tool, {'goal': goal})
            assert voice.execute.await_count == 2
            writer.chat.assert_not_called()
    asyncio.run(run())


AEROGEL_PLAN = {'topic': 'Aerogel', 'public_search_url': 'https://en.wikipedia.org/wiki/Aerogel'}
AEROGEL = {**FINDING, 'app': 'Safari', 'title': 'Aerogel', 'attribution': '',
           'url': 'https://en.wikipedia.org', 'quote': 'Aerogel is a synthetic porous ultralight material.',
           'claim': 'Aerogel is a synthetic porous ultralight material.'}
READER_AVAILABLE = {**AEROGEL, 'source_ready': False, 'url': '',
                    'observation': 'Safari shows the readable Aerogel article, but the address pill shows Reader Available.'}


@pytest.mark.parametrize('rechecks', [1, 2, 3])
def test_reader_available_passively_rechecks_then_verifies_aerogel(monkeypatch, rechecks):
    async def run():
        d, r, a = await source_fixture()
        sleep = AsyncMock()
        monkeypatch.setattr('hidagent.exploration_agent.source_recheck_sleep', sleep, raising=False)
        a.actor.chat.side_effect = [json.dumps(READER_AVAILABLE)]*rechecks + [json.dumps(AEROGEL)]
        try:
            await d.read_source(r, AEROGEL_PLAN)
            assert a.actor.chat.await_count == rechecks+1
            assert sleep.await_count == rechecks
            assert all(c.args == (2.0,) for c in sleep.await_args_list)
            assert r.action_count == 0
            assert r.evidence[0]['source_title'] == 'Aerogel'
            assert r.evidence[0]['url'] == 'https://en.wikipedia.org'
            calls = a.client.evaluate.call_args_list
            assert [c.args[0]['policy'] for c in calls] == ['exploration_source_chrome_wait']*rechecks + ['exploration_public_source']
            assert 'Reader Available' in str(calls[0])
        finally:
            await r.stop()
    asyncio.run(run())


def test_reader_available_rechecks_are_bounded_and_never_invent_host(monkeypatch):
    async def run():
        d, r, a = await source_fixture(READER_AVAILABLE)
        sleep = AsyncMock()
        monkeypatch.setattr('hidagent.exploration_agent.source_recheck_sleep', sleep, raising=False)
        try:
            with pytest.raises(RoundRejected, match='Reader Available'):
                await d.read_source(r, AEROGEL_PLAN)
            assert a.actor.chat.await_count == 4
            assert sleep.await_count == 3
            assert not r.evidence
            assert r.action_count == 0
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('ready,blocked,approved', [(.98, 0, True), (1, .02, True), (1, 0, False), (True, 0, True)])
def test_chrome_recheck_requires_independent_matching_article_and_passage(monkeypatch, ready, blocked, approved):
    async def run():
        d, r, a = await source_fixture(READER_AVAILABLE, {
            'answers': {'ready': {'noul': ready}, 'blocked': {'noul': blocked}},
            'model': 'jev-latest', 'approved': approved})
        sleep = AsyncMock()
        monkeypatch.setattr('hidagent.exploration_agent.source_recheck_sleep', sleep, raising=False)
        try:
            with pytest.raises(RoundRejected):
                await d.read_source(r, AEROGEL_PLAN)
            sleep.assert_not_awaited()
            assert not r.evidence
            instructions = str(a.client.evaluate.call_args)
            assert 'wrong host' in instructions
            assert 'title' in instructions and 'passage' in instructions
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('ready,blocked', [(.98, 0), (1, .02)])
def test_chrome_recovery_does_not_relax_final_title_passage_gate(monkeypatch, ready, blocked):
    async def run():
        d, r, a = await source_fixture()
        monkeypatch.setattr('hidagent.exploration_agent.source_recheck_sleep', AsyncMock(), raising=False)
        a.actor.chat.side_effect = [json.dumps(READER_AVAILABLE), json.dumps(AEROGEL)]
        good = {'answers': {'ready': {'noul': 1}, 'blocked': {'noul': 0}}, 'model': 'jev-latest'}
        bad = {'answers': {'ready': {'noul': ready}, 'blocked': {'noul': blocked}}, 'model': 'jev-latest'}
        a.client.evaluate.side_effect = [good, bad, bad]
        try:
            with pytest.raises(RoundRejected):
                await d.read_source(r, AEROGEL_PLAN)
            assert not r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


@pytest.mark.parametrize('change', ['stop', 'binding'])
def test_chrome_wait_honors_stop_and_device_binding(monkeypatch, change):
    async def run():
        d, r, a = await source_fixture(READER_AVAILABLE)
        async def sleep(seconds):
            if change == 'stop':
                await r.stop()
            else:
                a.phone.device.links += 1
        monkeypatch.setattr('hidagent.exploration_agent.source_recheck_sleep', sleep)
        try:
            with pytest.raises(RoundRejected, match='inactive|binding changed|connection changed'):
                await d.read_source(r, AEROGEL_PLAN)
            assert a.actor.chat.await_count == 1
            assert not r.evidence
        finally:
            await r.stop()
    asyncio.run(run())


def test_chrome_recheck_uses_fresh_frame_for_final_source_evidence(monkeypatch):
    async def run():
        if not FRAME.exists(): pytest.skip('Private device frame excluded from public snapshot')
        d, r, a = await source_fixture()
        recovered_frame = (FRAME.parent/'exploration-safari-landscape.jpg').read_bytes()
        async def sleep(seconds):
            a.screen.frame = recovered_frame
        monkeypatch.setattr('hidagent.exploration_agent.source_recheck_sleep', sleep)
        a.actor.chat.side_effect = [json.dumps(READER_AVAILABLE), json.dumps(AEROGEL)]
        try:
            await d.read_source(r, AEROGEL_PLAN)
            assert a.actor.chat.call_args.args[2] == recovered_frame
            assert a.client.evaluate.call_args.args[2] == recovered_frame
            assert r.evidence[0]['frame_sha256'] == hashlib.sha256(recovered_frame).hexdigest()
        finally:
            await r.stop()
    asyncio.run(run())
