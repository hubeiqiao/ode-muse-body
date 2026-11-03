"""Offline queue-boundary regressions. No sockets, receivers or model calls."""
import asyncio
import importlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from hidagent.device import Device
from hidagent.phone import Phone


class Writer:
    def __init__(self):
        self.writes = []
        self.closed = False
    def is_closing(self): return self.closed
    def write(self, data): self.writes.append(data)
    async def drain(self): pass
    def close(self): self.closed = True


class Reader:
    async def readline(self): return b'ok\n'


def board():
    d = Device()
    d._writer, d._reader = Writer(), Reader()
    return d


@pytest.mark.parametrize('enabled,force,expected', [('1', False, []), ('0', False, [b'move 12 0\n', b'move -12 0\n']), ('1', True, [b'move 12 0\n', b'move -12 0\n'])])
def test_probe_rechecks_frame_only_at_device_lock_boundary(tmp_path, monkeypatch, enabled, force, expected):
    monkeypatch.setenv('INPUT_PROBE_RECHECK', enabled)
    async def run():
        d = board()
        screen = SimpleNamespace(updated=time.time()-10, fresh=AsyncMock(return_value=b'frame'))
        p = Phone(d, tmp_path/'map.json', screen=screen)
        await d._lock.acquire()
        task = asyncio.create_task(p.ensure_live(force_probe=force))
        await asyncio.sleep(0)
        assert not task.done() and not d._writer.writes
        screen.updated = time.time()
        d._lock.release()
        await task
        assert d._writer.writes == expected
        assert screen.fresh.await_count == bool(expected)
    asyncio.run(run())


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('HIDAGENT_DATA', str(tmp_path))
    m = importlib.import_module('hidagent.app')
    d = board()
    monkeypatch.setattr(m, 'device', d)
    monkeypatch.setattr(m, 'manual_lock', asyncio.Lock())
    monkeypatch.setattr(m, 'agent', SimpleNamespace(running=False))
    monkeypatch.setattr(m, 'exploration', SimpleNamespace(busy=False))
    monkeypatch.setattr(m, 'app_tools_busy', lambda: False)
    monkeypatch.setattr(m, 'screen', SimpleNamespace(updated=time.time()))
    monkeypatch.setattr(m, 'phone', SimpleNamespace())
    # Firmware stats ACK is distinct from action ACKs.
    class StatsReader:
        async def readline(self): return b'ok mouse_sub=1\n'
    d._reader = StatsReader()
    return m, d


@pytest.mark.parametrize('enabled,expected', [('1', []), ('0', [b'stats\n'])])
def test_health_does_not_enqueue_while_foreground_holds_manual_lock(api, monkeypatch, enabled, expected):
    monkeypatch.setenv('HEARTBEAT_YIELD', enabled)
    m, d = api
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def action():
            entered.set()
            await release.wait()
        task = asyncio.create_task(m.act(action))
        await entered.wait()
        try:
            await m.device_health()
            assert d._writer.writes == expected
        finally:
            release.set()
            await task
    asyncio.run(run())


def test_health_does_not_enqueue_while_foreground_waits_for_manual_lock(api, monkeypatch):
    monkeypatch.setenv('HEARTBEAT_YIELD', '1')
    m, d = api
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingLock:
            def locked(self): return False
            async def __aenter__(self):
                entered.set()
                await release.wait()
            async def __aexit__(self, *args): pass
        monkeypatch.setattr(m, 'manual_lock', WaitingLock())
        task = asyncio.create_task(m.act(AsyncMock()))
        await entered.wait()
        try:
            await m.device_health()
            assert not d._writer.writes
        finally:
            release.set()
            await task
    asyncio.run(run())


def test_http_timeline_persists_transport_and_typing_without_payload(api, tmp_path, monkeypatch):
    from hidagent.device import InputTimelines
    m, d = api
    store = InputTimelines(tmp_path/'traces', max_bytes=4096, last=3)
    monkeypatch.setattr(m, 'input_timelines', store)
    d.diagnostics = store
    d._reader = Reader()
    p = Phone(d, tmp_path/'map.json', screen=SimpleNamespace(updated=time.time()))
    monkeypatch.setattr(m, 'phone', p)
    monkeypatch.setattr(d, 'readiness', lambda: {'connected': True, 'stable': True})
    p.screen.mirror_health = lambda: {'state': 'session_connected'}
    monkeypatch.setenv('HID_KEY_MS', '40')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url='http://127.0.0.1') as c:
            r = await c.post('/api/type', json={'text':'PRIVATE_PAYLOAD'})
            assert r.status_code == 200
            timelines = (await c.get('/api/device-health?timelines=true')).json()['timelines']
        row = timelines[-1]
        assert row['request_id'] == row['action_id']
        events = row['events']
        assert [e['mono_s'] for e in events] == sorted(e['mono_s'] for e in events)
        names = [e['event'] for e in events]
        for name in ('received', 'act_entered', 'manual_lock_acquired', 'ensure_live', 'command_enqueue', 'command_lock_acquired', 'command_write_done', 'command_ack', 'command_finish', 'typing', 'finish'):
            assert name in names
        command = next(e for e in events if e['event']=='command_lock_acquired')
        assert {'source','link_generation','prior_lock_holder','command_id'} <= command.keys()
        typing = next(e for e in events if e['event']=='typing')
        assert typing['chars']==15 and typing['chunks']==1 and typing['keyboard_ms']==40
        assert typing['initial_gap']==0 and typing['config_resend']
        disk = ''.join(x.read_text() for x in (tmp_path/'traces').glob('input-timelines*.jsonl'))
        assert 'PRIVATE_PAYLOAD' not in disk+repr(timelines)
        assert 'finish' in disk
    asyncio.run(run())


def test_sent_ping_ack_is_consumed_before_foreground_and_new_health_yields(api, monkeypatch):
    from hidagent.device import command_source
    monkeypatch.setenv('HEARTBEAT_YIELD', '1')
    m, d = api
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        consumed = []
        class OrderedReader:
            async def readline(self):
                index = len(consumed)
                if index == 0:
                    entered.set()
                    await release.wait()
                consumed.append(index)
                return b'ok\n' if index == 0 else b'ok action_ack=1\n'
        d._reader = OrderedReader()
        async def heartbeat():
            with command_source('heartbeat'):
                await d.send('ping')
        ping = asyncio.create_task(heartbeat())
        await entered.wait()
        action = asyncio.create_task(m.act(lambda: d.send('stats')))
        await asyncio.sleep(0)
        try:
            await m.device_health()
            assert d._writer.writes == [b'ping\n']
            release.set()
            await ping
            await action
            assert consumed == [0,1]
            assert d._writer.writes == [b'ping\n', b'stats\n']
            assert [x['outcome'] for x in d.timings] == ['ok','ok']
            assert not d._writer.closed
        finally:
            release.set()
            await asyncio.gather(ping, action, return_exceptions=True)
    asyncio.run(run())


def test_yield_never_drops_an_idle_nudge_return_move(monkeypatch):
    from hidagent.device import command_source
    monkeypatch.setenv('HEARTBEAT_YIELD', '1')
    async def run():
        d = board()
        with command_source('heartbeat'):
            await d.send('move 1 0')
            d.foreground_pending = 1
            await d.send('move -1 0')
        assert d._writer.writes == [b'move 1 0\n', b'move -1 0\n']
    asyncio.run(run())


def test_persisted_transport_includes_existing_segments(tmp_path):
    from hidagent.device import InputTimelines
    async def run():
        d = board()
        d.diagnostics = InputTimelines(tmp_path)
        await d.send('type SECRET')
        row = d.diagnostics.recent[-1]
        segment = next(e for e in row['events'] if e['event']=='command_transport')
        assert {'queue_s','write_drain_s','ack_wait_s','host_loop_lag_s','outcome'} <= segment.keys()
        assert 'SECRET' not in repr(row)
    asyncio.run(run())


@pytest.mark.parametrize('source,command', [
    ('heartbeat', 'ping'), ('heartbeat', 'stats'),
    ('heartbeat', 'move 1 0'), ('heartbeat', 'move -1 0'),
    ('health', 'stats'),
])
def test_background_sends_do_not_flood_foreground_timelines(tmp_path, source, command):
    import json
    from hidagent.device import InputTimelines, command_source

    async def run():
        d = board()
        store = d.diagnostics = InputTimelines(tmp_path/'traces')
        with command_source(source):
            for _ in range(50):
                await d.send(command)
        assert not store.recent
        assert not store.root.exists()
        assert store.write_errors == 0
        with store.capture('/api/key') as row:
            await d.send('key enter')
            store.finish(row, 200)

        assert list(store.recent) == [row]
        lines = (store.root/'input-timelines.jsonl').read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])['path'] == '/api/key'
        assert len(d._writer.writes) == 51
        assert d.timings[-2]['command'] == command.partition(' ')[0]
        assert d.timings[-2]['outcome'] == 'ok'
        lock = next(e for e in row['events'] if e['event']=='command_lock_acquired')
        assert lock['prior_lock_holder']['source'] == source
        assert lock['prior_lock_holder']['command_id'] == 50
        transport = next(e for e in row['events'] if e['event']=='command_transport')
        assert transport['queue_s'] >= 0

    asyncio.run(run())


def test_still_stale_probe_records_fresh_frame_and_source_state(tmp_path, monkeypatch):
    from hidagent.device import InputTimelines
    monkeypatch.setenv('INPUT_PROBE_RECHECK', '1')
    async def run():
        d = board()
        store = InputTimelines(tmp_path/'traces')
        screen = SimpleNamespace(updated=time.time()-10, _sock=object(),
                                 _receiver_health={'source_epoch':'epoch-2', 'peer_connected':True},
                                 _receiver_health_at=time.monotonic())
        async def fresh(after, timeout):
            screen.updated = time.time()
            return b'frame'
        screen.fresh = fresh
        p = Phone(d, tmp_path/'map.json', screen=screen)
        with store.capture('/api/key') as row:
            await p.ensure_live()
            store.finish(row, 200)
        events = row['events']
        decision = next(e for e in events if e['event']=='ensure_live')
        assert decision['probed'] and decision['reason']=='stale'
        assert decision['source_epoch']=='epoch-2' and decision['source_session_connected'] is True
        end = next(e for e in events if e['event']=='probe_fresh_end')
        assert end['accepted'] and end['accepted_frame_time']==screen.updated
        assert d._writer.writes==[b'move 12 0\n', b'move -12 0\n']
    asyncio.run(run())


def test_probe_recheck_after_optional_tune_but_before_first_move(tmp_path, monkeypatch):
    monkeypatch.setenv('INPUT_PROBE_RECHECK', '1')
    monkeypatch.setenv('HID_POINTER_MS', '40')
    async def run():
        d = board()
        screen = SimpleNamespace(updated=time.time()-10, fresh=AsyncMock())
        class TuneReader:
            async def readline(self):
                screen.updated = time.time()
                return b'ok\n'
        d._reader = TuneReader()
        p = Phone(d, tmp_path/'map.json', screen=screen)
        await p.ensure_live()
        assert d._writer.writes==[b'tune 10 40\n']
        screen.fresh.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('enabled,has_ping', [('1',False), ('0',True)])
def test_heartbeat_waiting_foreground_gate_and_source(tmp_path, monkeypatch, enabled, has_ping):
    from hidagent.device import InputTimelines
    monkeypatch.setenv('HEARTBEAT_YIELD', enabled)
    async def run():
        d = board()
        d.foreground_pending = 1
        d.diagnostics = InputTimelines(tmp_path)
        task = asyncio.create_task(d.keep_awake(heartbeat=.001, nudge=False))
        try:
            await asyncio.sleep(.01)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        assert bool(d._writer.writes) is has_ping
        if has_ping:
            assert all(w==b'ping\n' for w in d._writer.writes)
            assert d._previous_holder['source'] == 'heartbeat'
            assert d.timings[-1]['command'] == 'ping'
        assert not d.diagnostics.recent
        assert not (tmp_path/'input-timelines.jsonl').exists()
    asyncio.run(run())


def test_default_flags_keep_legacy_probe_and_health(api, tmp_path, monkeypatch):
    monkeypatch.delenv('INPUT_PROBE_RECHECK', raising=False)
    monkeypatch.delenv('HEARTBEAT_YIELD', raising=False)
    m, d = api
    async def run():
        d._reader = Reader()
        p = Phone(d, tmp_path/'map.json', screen=SimpleNamespace(updated=time.time()-10, fresh=AsyncMock(return_value=b'frame')))
        await d._lock.acquire()
        task = asyncio.create_task(p.ensure_live())
        await asyncio.sleep(0)
        p.screen.updated = time.time()
        d._lock.release()
        await task
        d.foreground_pending = 1
        await m.device_health()
        assert d._writer.writes==[b'move 12 0\n', b'move -12 0\n', b'stats\n']
    asyncio.run(run())


def test_timeline_bounds_rotation_and_disk_failure_never_change_delivery(tmp_path):
    import json
    from hidagent.device import InputTimelines, timeline_event
    store = InputTimelines(tmp_path/'traces', max_bytes=2048, last=2, max_events=512)
    for i in range(5):
        with store.capture('/api/key') as row:
            for _ in range(600): timeline_event('test', source='foreground')
            store.finish(row, 200)
        assert len(row['events'])==512 and row['events'][-1]['event']=='finish'
        assert row['dropped_events']>0
    assert len(store.recent)==2
    files = list(store.root.glob('*.jsonl'))
    assert len(files)==2 and all(p.stat().st_size<=2048 for p in files)
    assert all(json.loads(line)['events'][-1]['status']==200 for p in files for line in p.read_text().splitlines())
    blocked = tmp_path/'file'
    blocked.write_text('not a directory')
    async def run():
        d = board()
        d.diagnostics = InputTimelines(blocked)
        await d.send('key enter')
        assert d._writer.writes==[b'key enter\n'] and d.diagnostics.write_errors==1
    asyncio.run(run())


def test_http_rejection_and_disconnected_controller_are_metadata_only(api, tmp_path, monkeypatch):
    from hidagent.device import InputTimelines
    m, d = api
    store = InputTimelines(tmp_path/'traces')
    monkeypatch.setattr(m, 'input_timelines', store)
    p = Phone(d, tmp_path/'map.json', screen=SimpleNamespace(updated=time.time()))
    monkeypatch.setattr(m, 'phone', p)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url='http://127.0.0.1') as c:
            # Invalid request is received/finished but never enters act.
            r = await c.post('/api/key', json={'text':'PRIVATE\nTEXT'})
            assert r.status_code==422
            d._writer.close()
            r = await c.post('/api/type', json={'text':'PRIVATE_TEXT'})
            assert r.status_code==409
            assert d.foreground_pending==0
            writes = list(d._writer.writes)
            await c.get('/api/device-health?timelines=true')
            assert d._writer.writes==writes
        rows = list(store.recent)
        assert len({r['request_id'] for r in rows})==2
        assert [r['events'][-1]['status'] for r in rows]==[422,409]
        assert not any(e['event']=='command_finish' for e in rows[-1]['events'])
        check = next(e for e in rows[-1]['events'] if e['event']=='ensure_live')
        assert check['controller_stable'] is False and check['pointer_probe_sent'] is False
        assert 'PRIVATE' not in repr(rows)
    asyncio.run(run())
