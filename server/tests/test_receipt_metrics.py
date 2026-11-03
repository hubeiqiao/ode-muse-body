"""Offline receipt instrumentation: no device, model or network transport."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hidagent.app_tools import AppTools
from hidagent.receipt_journal import ReceiptJournal
from test_app_mission import setup as mission_setup, finish, wait


def test_metrics_capture_actual_calls_inputs_waits_without_private_content(tmp_path):
    async def run():
        from hidagent.receipt_journal import ReceiptMetrics
        clock = [100.0]
        async def chat(*args, **kwargs):
            clock[0] += 1
            return 'private reply'
        async def evaluate(*args, **kwargs):
            clock[0] += .5
            return {'seconds': .5, 'model': 'jev-test'}
        async def send(command):
            clock[0] += .1
        trace = SimpleNamespace(write=lambda *a, **kw: None)
        muse = SimpleNamespace(model='muse-test', provider='meta', chat=chat)
        jev = SimpleNamespace(model='jev-test', evaluate=evaluate)
        phone = SimpleNamespace(_send=send)
        agent = SimpleNamespace(trace=trace, phone=phone, client=jev, planner=muse, actor=muse)
        metrics = ReceiptMetrics(100, clock=lambda: clock[0])
        original = (muse.chat, jev.evaluate, phone._send, trace.write)
        metrics.observe(agent, muse)
        try:
            await muse.chat('private prompt', 'secret')
            await jev.evaluate({'policy': 'private'}, {}, b'private frame')
            await phone._send('key private-text')
            await phone._send('click')
            trace.write(b'private screenshot', kind='step', action='wait', text='private')
            trace.write(kind='step', action='done', launch_method='spotlight_first')
            # Duplicate rich trace data must not double count the leaf call.
            trace.write(kind='jev', model='jev-test', seconds=.5)
        finally:
            metrics.close()
        result = metrics.snapshot(104)
        assert result['total_s'] == 4
        assert result['model_calls'] == {'count': 2, 'total_s': 1.5,
            'jev': {'count': 1, 'total_s': .5}, 'muse': {'count': 1, 'total_s': 1.0},
            'other': {'count': 0, 'total_s': 0.0}}
        assert result['first_feedback_s'] == 1.5
        assert result['hid_actions'] == 2 and result['waits'] == 1
        assert result['launch_method'] == 'spotlight_first'
        assert '2 model calls (1.5 s)' in result['summary']
        assert 'private' not in json.dumps(result) and 'secret' not in json.dumps(result)
        assert original == (muse.chat, jev.evaluate, phone._send, trace.write)
    asyncio.run(run())


def test_every_tool_receipt_and_saved_history_have_metrics(tmp_path):
    async def run():
        state = {'device': True, 'screen': True, 'calibrated': True, 'agent': {'running': False}}
        bridge = SimpleNamespace(exploration=None, execute=AsyncMock(return_value={
            'status': 'model_reported_done', 'running': False}), snapshot=lambda **kw: {})
        writer = SimpleNamespace(model='test', chat=AsyncMock(return_value='{"text":"Private local body."}'))
        journal = ReceiptJournal(tmp_path / 'receipts.sqlite3')
        tools = AppTools(bridge, writer, SimpleNamespace(snapshot=lambda: {'preferences': [], 'discoveries': []}),
                         lambda: state, AsyncMock(), journal=journal)
        try:
            r = await tools.call('metrics_draft_one', 'x.prepare_draft', {'goal': 'Private brief'})
            assert r['metrics']['model_calls']['count'] == 1
            assert r['metrics']['first_feedback_s'] is None
            assert r['metrics']['hid_actions'] == r['metrics']['waits'] == 0
            saved = journal.get('metrics_draft_one')['metrics']
            assert saved == r['metrics'] == tools.history()['items'][0]['metrics']
            tools.calls.clear()
            assert tools.status('metrics_draft_one')['metrics'] == saved
            # Old rows remain readable with explicit unknown instrumentation.
            journal.reserve('legacy_row', tools.server_instance, 'sig', 'x.open', 'device')
            journal.finish('legacy_row', tools.server_instance, 'completed', {}, 2)
            assert tools.status('legacy_row')['metrics']['first_feedback_s'] is None
            state['device'] = False
            assert (await tools.call('not_ready_metrics', 'x.open', {}))['metrics']['model_calls']['count'] == 0
        finally:
            journal.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['error', 'cancel'])
def test_failed_and_cancelled_model_calls_count_and_restore(failure):
    async def run():
        from hidagent.receipt_journal import ReceiptMetrics
        async def chat(*args, **kw):
            if failure == 'cancel':
                raise asyncio.CancelledError()
            raise RuntimeError('private provider body')
        writer = SimpleNamespace(model='haiku-test', chat=chat)
        metrics = ReceiptMetrics(0, clock=lambda: 1)
        metrics.observe(None, writer)
        try:
            with pytest.raises((RuntimeError, asyncio.CancelledError)):
                await writer.chat('private')
        finally:
            metrics.close()
        assert writer.chat is chat
        assert metrics.snapshot(2)['model_calls']['other']['count'] == 1
    asyncio.run(run())


def test_goal_receipt_sums_planner_and_child_metrics_without_double_counting(tmp_path):
    async def run():
        from test_app_mission import plan
        m, tools, bridge, journal, state, body = mission_setup(tmp_path, [
            plan('youtube.search', {'query': 'speaking'}), finish()])
        try:
            await m.start(body)
            receipt = await wait(m)
            assert receipt['metrics']['model_calls']['count'] == 2
            assert receipt['metrics'] == journal.get(body.request_id)['metrics']
            assert receipt['steps'][0]['metrics']['model_calls']['count'] == 0
            assert 'goal' not in receipt['metrics']
        finally:
            journal.close()
    asyncio.run(run())


def test_stop_snapshot_includes_inflight_call_and_never_changes_after_late_return():
    async def run():
        from hidagent.receipt_journal import ReceiptMetrics
        clock = [10.0]
        entered, release = asyncio.Event(), asyncio.Event()
        async def chat(*args):
            entered.set()
            await release.wait()
        writer = SimpleNamespace(model='jev-test', chat=chat)
        metrics = ReceiptMetrics(10, clock=lambda: clock[0])
        metrics.observe(writer=writer)
        job = asyncio.create_task(writer.chat('private'))
        try:
            await entered.wait()
            clock[0] = 12
            stopped = metrics.snapshot(12)
            assert stopped['model_calls']['count'] == 1
            assert stopped['model_calls']['total_s'] == 2
            clock[0] = 20
            release.set()
            await job
            assert metrics.snapshot(12) == stopped
        finally:
            metrics.close()
    asyncio.run(run())


def test_migration_reads_original_schema_without_rewriting_private_result(tmp_path):
    import sqlite3
    path = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE receipts(request_id TEXT PRIMARY KEY, server_instance TEXT NOT NULL, '
            'signature TEXT NOT NULL, tool_name TEXT NOT NULL, effect TEXT NOT NULL, state TEXT NOT NULL, '
            'created_at REAL NOT NULL, updated_at REAL NOT NULL, elapsed_s REAL, result TEXT NOT NULL)')
        connection.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('old', 'old_boot', 'sig', 'x.open', 'device', 'completed', 1, 2, 1, '{"reason":"Old result"}'))
    journal = ReceiptJournal(path)
    try:
        row = journal.get('old')
        assert row['metrics']['instrumentation'] == 'unavailable'
        assert row['metrics']['total_s'] == 1
        assert row['result'] == {'reason': 'Old result'}
    finally:
        journal.close()


def test_goal_counts_child_judge_and_action_offset_once(tmp_path):
    async def run():
        from test_app_mission import plan
        m, tools, bridge, journal, state, body = mission_setup(tmp_path, [
            plan('youtube.search', {'query': 'speaking'}), finish()])
        judge = SimpleNamespace(model='jev-test', evaluate=AsyncMock(return_value={}))
        phone = SimpleNamespace(_send=AsyncMock())
        tools.result_reader.agent = SimpleNamespace(client=judge, phone=phone)
        async def execute(*args, **kwargs):
            await judge.evaluate({'policy': 'public'}, {})
            await phone._send('click')
            return {'status': 'model_reported_done', 'running': False}
        bridge.execute.side_effect = execute
        try:
            await m.start(body)
            receipt = await wait(m)
            metrics = receipt['metrics']
            assert metrics['model_calls']['count'] == 3
            assert metrics['model_calls']['jev']['count'] == 1
            assert metrics['hid_actions'] == 1
            assert 0 <= metrics['first_feedback_s'] <= metrics['total_s']
            assert journal.get(body.request_id)['metrics'] == metrics
        finally:
            journal.close()
    asyncio.run(run())
