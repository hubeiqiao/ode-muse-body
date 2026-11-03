"""Realtime voice control (owner 2026-10-29): interjection, stop summary, fast ack, progress, metrics line.
Fakes only: no device, no server, no model API."""
import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from hidagent.live_voice import ToolCall, VoiceBridge

SID = 'x' * 32


def fixture(app_tools=None):
    state = {'device': True, 'screen': True, 'calibrated': True, 'target': {'device': 'ipad'},
             'models': {'planner': 'p'}, 'agent': {'running': False, 'task': '', 'steps': []}}
    starts, stops = [], []

    async def start(goal):
        starts.append(goal)
        state['agent'].update(task=goal)

    async def stop():
        stops.append(time.monotonic())
        if state['agent']['running']:
            state['agent']['steps'].append({'action': 'stopped', 't': time.time()})
        state['agent']['running'] = False
    b = VoiceBridge(lambda: state, start, stop)
    b.app_tools = app_tools
    b.sessions[SID] = {'expires': time.monotonic() + 60, 'calls': {}, 'paused': False, 'cancelled_ids': set(),
                       'device_family': 'ipad'}
    return b, state, starts, stops


class NoteSink:
    """Stands in for the running Agent's owner-note inbox."""
    def __init__(self, state):
        self.state, self.owner_notes = state, []

    def add_owner_note(self, text):
        if not self.state['agent']['running']:
            return None
        self.owner_notes.append({'text': text, 'delivered': False})
        return {'accepted': True}


def add(b, text, utterance, cid='note-1'):
    return b.call(ToolCall(session=SID, id=cid, name='add_to_current_task', args={'text': text}, utterance=utterance))


# ---------------------------------------------------------------- 1 interjection

def test_interjection_reaches_running_agent_prompt_before_its_next_step(monkeypatch):
    """Real Agent loop: the owner speaks while step 1 is being decided; step 2's prompt carries it."""
    from hidagent import agent as agent_module
    from hidagent.agent import Agent
    from hidagent.debug import Trace
    monkeypatch.setattr(agent_module.vision, 'changed', lambda *a, **k: True)

    async def run():
        first_decision = asyncio.Event()
        release = asyncio.Event()

        class Screen:
            live, frame, aspect, updated = True, b'jpeg', .65, 0

            async def settled(self):
                pass

        class Model:
            model = 'fake'

            def __init__(self):
                self.actor_prompts = []

            async def chat(self, system, prompt, image=None, temperature=0, **kw):
                if system.startswith('You plan tasks'):
                    return '1. Open Notes'
                self.actor_prompts.append(prompt)
                if len(self.actor_prompts) == 1:
                    first_decision.set()
                    await release.wait()
                    return 'THINK: Notes is opening\nDO: wait'
                return 'THINK: added\nDO: done ok'

        model = Model()
        a = Agent(None, Screen(), model, model, Trace(None), max_steps=4)
        b, state, _, _ = fixture()
        b.agent = a
        state['agent'] = {'running': True, 'task': 'Open Notes and write a list', 'steps': a.steps}
        a.start('Open Notes and write a list')
        await first_decision.wait()
        receipt = await add(b, 'also add milk', '还有，加上牛奶')
        assert receipt['status'] == 'note_added', receipt
        assert receipt['applies'] == 'before_next_step' and receipt['note_added'] is True
        assert 'next step' in receipt['instruction']
        assert 'Owner just said' not in model.actor_prompts[0]
        release.set()
        await asyncio.wait_for(a._job, 2)
        assert len(model.actor_prompts) == 2
        assert 'Owner just said' in model.actor_prompts[1]
        assert '还有，加上牛奶' in model.actor_prompts[1]
        assert 'untrusted' in model.actor_prompts[1]
        assert a.owner_notes[0]['delivered'] is True
    asyncio.run(run())


def test_agent_prompt_is_unchanged_without_owner_notes():
    from hidagent.agent import Agent
    from hidagent.debug import Trace

    class Model:
        model = 'fake'
        prompts = []

        async def chat(self, system, prompt, image=None, **kw):
            self.prompts.append(prompt)
            return 'THINK: x\nDO: wait'
    model = Model()
    a = Agent(None, SimpleNamespace(live=True, frame=b'jpeg', aspect=.65), model, model, Trace(None))
    a.task, a.plan = 'Open Notes', '1. Open Notes'
    asyncio.run(a._next_actions())
    assert model.prompts[-1] == 'Goal: Open Notes\n\nPlan:\n1. Open Notes\n\nRecent actions:\n(none)\n\nNext action?'
    assert a.add_owner_note('ignored while idle') is None and a.owner_notes == []


def test_add_to_current_task_refused_when_idle_then_normal_dispatch():
    async def run():
        b, state, starts, _ = fixture()
        b.agent = NoteSink(state)
        r = await add(b, 'open Notes', '打开备忘录')
        assert r['status'] == 'no_running_task' and r['note_added'] is False and r['terminal'] is True
        assert 'phone_command' in r['instruction']
        assert starts == [] and b.agent.owner_notes == []
        r = await b.call(ToolCall(session=SID, id='go', name='phone_command', args={'goal': 'Open Notes'}))
        assert starts == ['Open Notes']
    asyncio.run(run())


def test_add_to_current_task_accepts_late_owner_transcription():
    async def run():
        b, state, starts, stops = fixture()
        b.agent = NoteSink(state)
        state['agent'].update(running=True, task='Open Notes')
        r = await add(b, 'also add milk', '')
        assert r['status'] == 'note_added'
        assert b.agent.owner_notes == [{'text':'also add milk','delivered':False}] and not stops and not starts
    asyncio.run(run())


def test_add_to_current_task_stop_utterance_is_a_stop_not_a_note():
    async def run():
        b, state, _, stops = fixture()
        b.agent = NoteSink(state)
        state['agent'].update(running=True, task='Scroll down', steps=[{'action': 'swipe', 't': 1.0}])
        r = await add(b, 'stop', '停止')
        assert r['status'] == 'stopped' and stops and b.agent.owner_notes == []
    asyncio.run(run())


def test_add_to_current_task_same_call_id_adds_once():
    async def run():
        b, state, _, _ = fixture()
        b.agent = NoteSink(state)
        state['agent'].update(running=True, task='Open Notes')
        first, again = await asyncio.gather(add(b, 'milk', 'add milk'), add(b, 'milk', 'add milk'))
        assert first == again and len(b.agent.owner_notes) == 1
    asyncio.run(run())


class FakeAppTools:
    server_instance = 's' * 16

    def __init__(self, receipt):
        self.busy, self.current_id, self.receipt, self.calls = True, 'voice_running', receipt, []

    def catalog_name_list(self):
        from hidagent.app_tools import TOOLS
        return list(TOOLS)

    def catalog(self):
        from hidagent.app_tools import TOOLS
        return {'tools': [{'name': n, 'app': t['app']} for n, t in TOOLS.items()]}

    def status(self, request_id=None):
        return dict(self.receipt)

    def owns_voice(self, sid, ids=None):
        return False

    async def call(self, *a, **kw):
        self.calls.append(a)
        return {'status': 'busy', 'terminal': True}

    async def stop(self):
        self.busy = False


RUNNING_TOOL = {'request_id': 'voice_running', 'tool_name': 'grok.open', 'app': 'Grok Bot', 'status': 'in_progress',
                'running': True, 'terminal': False, 'poll_again': True, 'elapsed_s': 1.0}
DONE_TOOL = {'request_id': 'voice_running', 'tool_name': 'grok.open', 'app': 'Grok Bot', 'status': 'completed',
             'running': False, 'terminal': True, 'poll_again': False, 'elapsed_s': 6.2,
             'metrics': {'total_s': 6.2, 'hid_actions': 3,
                         'summary': '6.2 s, 2 model calls (3.1 s), first action 0.9 s, 3 inputs'},
             'result': {'receipt': {'input_was_sent': False}}, 'instruction': 'Final receipt.'}


def test_deterministic_app_tool_records_note_and_surfaces_it_once_after_the_tool():
    async def run():
        tools = FakeAppTools(RUNNING_TOOL)
        b, state, starts, _ = fixture(tools)
        b.agent = NoteSink(state)
        b.owner_app_tool = 'voice_running'
        state['agent'].update(running=True, task='Open Grok Bot')  # its internal device step
        r = await add(b, 'then search for news', '然后搜一下新闻')
        assert r['status'] == 'note_recorded' and r['applies_after_tool'] is True
        assert 'after' in r['instruction'] and 'Never restart' in r['instruction']
        assert b.agent.owner_notes == [], 'a fixed tool sequence is never rewritten mid-way'
        tools.busy, tools.receipt = False, DONE_TOOL
        state['agent']['running'] = False
        final = await b.call(ToolCall(session=SID, id='poll', name='phone_status', args={}))
        assert final['owner_notes_after_task'] == ['然后搜一下新闻']
        assert 'ask' in final['instruction'].lower() and 'never start it automatically' in final['instruction']
        again = await b.call(ToolCall(session=SID, id='poll2', name='phone_status', args={}))
        assert 'owner_notes_after_task' not in again
        assert starts == [] and tools.calls == [], 'no automatic restart or replay'
    asyncio.run(run())


def test_app_goal_note_reaches_mission_planner_context():
    from hidagent.app_mission import AppMission

    async def run():
        seen = []

        class Writer:
            model = 'mission-model'

            async def chat(self, system, context, **kw):
                seen.append(json.loads(context))
                return json.dumps({'kind': 'finish', 'tool_name': '', 'args': {}, 'reason': 'done'})
        tools = SimpleNamespace(server_instance='s' * 16, busy=False, calls={})
        m = AppMission(tools, Writer(), SimpleNamespace(snapshot=lambda: {'preferences': []}), lambda: {}, None)
        m._active = lambda r: None
        worker = asyncio.get_running_loop().create_future()
        r = {'id': 'goal', 'goal': 'Find one public idea', 'steps': [], 'findings': [], 'drafts': [], 'max_steps': 4,
             'worker': worker, 'supervisor': None, 'status': 'running', 'started': time.monotonic()}
        m.current = r
        await m._plan(r)
        assert 'owner_just_said' not in seen[0], 'normal goal context unchanged'
        b, state, _, _ = fixture()
        b.mission = m
        receipt = await add(b, 'focus on education', '重点看教育类的')
        assert receipt['status'] == 'note_added' and receipt['applies'] == 'before_next_goal_step'
        await m._plan(r)
        note = seen[1]['owner_just_said']
        assert note['notes'] == ['重点看教育类的'] and 'never authorizes' in note['meaning']
        assert r['owner_notes'][0]['delivered'] is True
        worker.cancel()
    asyncio.run(run())


# ---------------------------------------------------------------- 2 stop summary

def test_stop_summary_reports_steps_input_unfinished_and_time():
    async def run():
        b, state, _, _ = fixture()
        now = time.time()
        state['agent'].update(running=True, task='Open Notes and type "milk"', elapsed_s=12.4, steps=[
            {'action': 'plan', 'plan': '1. Open Notes', 't': now - 12},
            {'action': 'key', 'combo': 'cmd+space', 't': now - 10, 'thought': 'Open search'},
            {'action': 'type', 'text': 'Notes', 't': now - 8},
            {'action': 'tap', 'x': 1, 'y': 2, 't': now - 6},
            {'action': 'type', 'text': 'milk', 't': now - 2}])
        r = await b.call(ToolCall(session=SID, id='stop', name='stop_phone', args={}))
        s = r['stop_summary']
        assert r['status'] == 'stopped' and s['was_running'] is True and s['task_kind'] == 'phone_command'
        assert s['steps_done'] == 4 and s['last_actions'] == ['type', 'tap', 'type']
        assert s['text_input'] is True and s['unfinished'] == 'Open Notes and type "milk"'
        assert s['elapsed_s'] == 12.4
        assert '4 steps' in s['line'] and 'typed' in s['line'] and 'not finished' in s['line'].lower()
        assert 'one or two sentences' in r['instruction'] and 'stop_summary' in r['instruction']
    asyncio.run(run())


def test_stop_summary_when_nothing_was_running():
    async def run():
        b, _, _, _ = fixture()
        r = await b.call(ToolCall(session=SID, id='stop', name='stop_phone', args={}))
        assert r['stop_summary']['was_running'] is False and r['stop_summary']['steps_done'] == 0
    asyncio.run(run())


def test_stop_summary_for_app_tool_uses_metrics_and_delivery_flags():
    async def run():
        running = {**RUNNING_TOOL, 'metrics': {'hid_actions': 2, 'summary': '3.0 s, 1 model calls (1.0 s), first action 0.5 s, 2 inputs'}}
        tools = FakeAppTools(running)
        b, state, _, _ = fixture(tools)
        b.owner_app_tool = 'voice_running'
        tools.receipt = {**running, 'status': 'stopped', 'running': False, 'terminal': True,
                         'result': {'reason': 'Action stopped.', 'input_was_sent': None, 'never_retry_typing': True,
                                    'delivery_uncertain': True}}
        r = await b.call(ToolCall(session=SID, id='stop', name='stop_phone', args={}))
        s = r['stop_summary']
        assert s['task_kind'] == 'app_tool' and s['tool_name'] == 'grok.open'
        assert s['steps_done'] == 2 and s['delivery_uncertain'] is True
        assert s['metrics_line'].endswith('2 inputs') and 'uncertain' in s['line']
    asyncio.run(run())


# ---------------------------------------------------------------- 3 fast ack, progress, no duplicate

def test_phone_command_returns_started_receipt_immediately():
    async def run():
        b, state, starts, _ = fixture()

        async def start(goal):
            starts.append(goal)
            state['agent'].update(running=True, task=goal, steps=[])
        b.start = start
        began = time.monotonic()
        r = await asyncio.wait_for(b.call(ToolCall(session=SID, id='go', name='phone_command',
                                                   args={'goal': 'Open Notes'})), 2)
        assert time.monotonic() - began < 1
        assert r['status'] == 'started' and r['running'] is True and r['terminal'] is False
        assert "Say one short sentence that you're on it" in r['instruction'] and 'Do NOT call phone_status' in r['instruction']
        assert starts == ['Open Notes']
    asyncio.run(run())


def test_app_tool_internal_dispatch_still_waits_for_its_device_task():
    async def run():
        b, state, starts, _ = fixture()

        async def start(goal):
            starts.append(goal)
            state['agent'].update(running=True, task=goal, steps=[])
            asyncio.get_running_loop().call_later(.3, lambda: state['agent'].update(running=False, steps=[{'action': 'done', 't': 1}]))
        b.start = start
        r = await b.execute('phone_command', {'goal': 'Open Notes'}, _app_tool=True)
        assert r['status'] == 'model_reported_done'
    asyncio.run(run())


def test_running_receipt_has_progress_line_and_status_returns_on_new_step():
    async def run():
        b, state, _, _ = fixture()
        state['agent'].update(running=True, task='Open Notes', steps=[
            {'action': 'plan', 'plan': '1. Open', 't': 1.0},
            {'action': 'key', 'combo': 'cmd+space', 'thought': 'I open system search to find Notes.', 't': 2.0}])
        snap = b.snapshot()
        assert snap['progress']['line'] == 'I open system search to find Notes.'
        assert snap['progress']['steps_done'] == 1 and snap['progress']['last_action'] == 'key'

        def step():
            state['agent']['steps'].append({'action': 'tap', 'thought': 'Tapping the Notes result.', 't': 3.0})
        asyncio.get_running_loop().call_later(.4, step)
        began = time.monotonic()
        r = await b.call(ToolCall(session=SID, id='poll', name='phone_status', args={}))
        assert time.monotonic() - began < 2.5, 'a new step ends the long poll early'
        assert r['progress']['line'] == 'Tapping the Notes result.' and r['running'] is True
    asyncio.run(run())


def test_no_duplicate_dispatch_when_busy_points_to_add_to_current_task():
    async def run():
        b, state, starts, _ = fixture()
        state['agent'].update(running=True, task='Open Notes', steps=[])
        r = await b.call(ToolCall(session=SID, id='dup', name='phone_command', args={'goal': 'Open Notes and add milk'}))
        assert r['status'] == 'busy' and starts == []
        assert 'add_to_current_task' in r['instruction']
    asyncio.run(run())


# ---------------------------------------------------------------- 4 metrics line

def test_final_app_tool_receipt_carries_metrics_line_for_the_spoken_result():
    async def run():
        tools = FakeAppTools(DONE_TOOL)
        tools.busy = False
        b, _, _, _ = fixture(tools)
        b.owner_app_tool = 'voice_running'
        r = await b.call(ToolCall(session=SID, id='poll', name='phone_status', args={}))
        assert r['metrics_line'] == DONE_TOOL['metrics']['summary']
        assert 'metrics_line' in r['instruction']
    asyncio.run(run())


# ---------------------------------------------------------------- setup gating

def _mint(monkeypatch, b):
    monkeypatch.setenv('GEMINI_LIVE_API_KEY', 'k')
    for name in ('SOURCE_NOTE_WORKFLOWS', 'DEVICE_MODE', 'AIRPLAY_NAME', 'CAMERA_CONVERSATION'):
        monkeypatch.delenv(name, raising=False)

    async def post(self, url, **kwargs):
        return httpx.Response(200, json={'name': 'auth_tokens/x'})
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    r = asyncio.run(b.mint())
    return r['setup']['systemInstruction']['parts'][0]['text'], [d['name'] for d in r['setup']['tools'][0]['functionDeclarations']]


def test_add_to_current_task_is_declared_with_app_tools_only(monkeypatch):
    b, _, _, _ = fixture()
    text, names = _mint(monkeypatch, b)
    assert 'add_to_current_task' not in names and 'add_to_current_task' not in text
    b, _, _, _ = fixture(FakeAppTools(RUNNING_TOOL))
    text, names = _mint(monkeypatch, b)
    assert 'add_to_current_task' in names
    rule = text[text.index('call add_to_current_task'):]
    assert 'instead of phone_command' in rule and '[TASK FINISHED]' in text and 'Do NOT call phone_status' in text


def test_note_heard_while_phone_command_is_starting_reaches_that_task_only():
    async def run():
        b, state, starts, _ = fixture()
        b.agent = NoteSink(state)
        gate = asyncio.Event()

        async def start(goal):
            await gate.wait()
            starts.append(goal)
            state['agent'].update(running=True, task=goal, steps=[])
        b.start = start
        job = asyncio.create_task(b.call(ToolCall(session=SID, id='go', name='phone_command', args={'goal': 'Open Notes'})))
        await asyncio.sleep(.05)
        r = await add(b, 'the shopping note', '是购物清单那条')
        assert r['status'] == 'note_added' and r['task_kind'] == 'starting'
        gate.set()
        assert (await job)['status'] == 'started'
        assert [n['text'] for n in b.agent.owner_notes] == ['是购物清单那条']
        assert b.pending_agent_notes == []
    asyncio.run(run())


def test_stop_reports_notes_recorded_for_a_fixed_tool_as_not_done():
    async def run():
        tools = FakeAppTools(RUNNING_TOOL)
        b, state, _, _ = fixture(tools)
        b.owner_app_tool = 'voice_running'
        await add(b, 'then search news', '然后搜新闻')
        tools.receipt = {**RUNNING_TOOL, 'status': 'stopped', 'running': False, 'terminal': True, 'result': {}}
        r = await b.call(ToolCall(session=SID, id='stop', name='stop_phone', args={}))
        assert r['stop_summary']['owner_notes_not_done'] == ['然后搜新闻']
        assert '然后搜新闻' in r['stop_summary']['line'] and b.after_task_notes == []
    asyncio.run(run())


def test_running_status_receipts_instruct_silence_not_progress_speech():
    async def run():
        for tools in (None, FakeAppTools(RUNNING_TOOL)):
            b,state,_,_=fixture(tools)
            if tools is None:
                state['agent'].update(running=True,task='Search rednote')
            r=await b.call(ToolCall(session=SID,id='poll',name='phone_status',args={}))
            assert 'Stay silent; you will be told when it finishes.' in r['instruction']
            assert 'tell the owner' not in r['instruction'].lower()
            assert r['poll_again'] is False
    asyncio.run(run())


def test_browser_task_status_is_immediate_session_checked_and_terminal():
    from fastapi import HTTPException
    async def run():
        b,state,starts,_=fixture()
        state['agent'].update(running=True,task='Search rednote')
        r=await asyncio.wait_for(b.task_status(SID),.2)
        assert r['running'] and 'Stay silent' in r['instruction']
        state['agent'].update(running=False,steps=[{'action':'done','answer':'A fun result'}])
        r=await b.task_status(SID)
        assert r['terminal'] and r['last_action']['answer']=='A fun result'
        assert not starts
        b.sessions[SID]['expires']=0
        with pytest.raises(HTTPException): await b.task_status(SID)
    asyncio.run(run())


def test_browser_receipts_are_bound_to_tasks_even_when_next_task_starts_before_poll():
    async def run():
        b,state,starts,_=fixture()
        async def start(goal):
            starts.append(goal)
            state['agent'].update(running=True,task=goal,steps=[])
        b.start=start
        first=await b.call(ToolCall(session=SID,id='first',name='phone_command',args={'goal':'Open Notes'}))
        state['agent'].update(running=False,steps=[{'action':'done','answer':'Notes opened'}])
        second=await b.call(ToolCall(session=SID,id='second',name='phone_command',args={'goal':'Open Calculator'}))
        old=await b.task_status(SID,first['task_watch_id'])
        assert old['terminal'] and old['last_action']['answer']=='Notes opened'
        current=await b.task_status(SID,second['task_watch_id'])
        assert current['running'] and current['goal']=='Open Calculator'
        assert starts==['Open Notes','Open Calculator']
    asyncio.run(run())


def test_browser_status_route_is_local_session_bound_and_uncached():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hidagent.live_voice import router_for
    b,state,starts,_=fixture()
    app=FastAPI();app.include_router(router_for(b))
    with TestClient(app,base_url='http://127.0.0.1:8894') as client:
        response=client.get('/api/voice/task_status',params={'session':SID})
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        assert response.json()['terminal'] and not starts
        assert client.get('/api/voice/task_status',params={'session':'unknown'}).status_code==401
        assert client.get('/api/voice/task_status',params={'session':SID,'task':'other'}).status_code==404
        assert client.get('/api/voice/task_status',params={'session':SID},headers={'origin':'https://evil.example'}).status_code==403


def test_device_tools_accept_empty_transcription_but_keep_supplied_arguments():
    from unittest.mock import AsyncMock
    async def run():
        for name,args in [('use_app_tool',{'tool_name':'keyboard.type_focused','args':{'text':'Exact text'}}),
                          ('start_app_goal',{'goal':'Find something fun'})]:
            b,_,_,_=fixture()
            b.execute=AsyncMock(return_value={'status':'completed','terminal':True})
            r=await b.call(ToolCall(session=SID,id='late',name=name,args=args,utterance=''))
            assert r['status']=='completed'
            assert b.execute.call_args.args[1]==args
    asyncio.run(run())
