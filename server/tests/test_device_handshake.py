import asyncio

import pytest

from hidagent.device import Device, DeviceError


class Reader:
    def __init__(self, line, delay=0):
        self.line, self.delay, self.completed = line, delay, False

    async def readline(self):
        await asyncio.sleep(self.delay)
        self.completed = True
        return self.line


class Writer:
    closed = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass

    def is_closing(self):
        return self.closed

    def get_extra_info(self, key):
        return ('board', 7300)

    def write(self, data):
        pass

    async def drain(self):
        pass


def test_slow_greeting_is_consumed_before_accepting_commands(monkeypatch):
    monkeypatch.setenv('DEVICE_HELLO_TIMEOUT', '2')
    monkeypatch.setenv('DEVICE_REQUIRE_HELLO', '1')
    device, reader, writer = Device(), Reader(b'hello power-on 123\n', 1.1), Writer()
    asyncio.run(device._accept(reader, writer))
    assert reader.completed and device.connected


def test_required_greeting_timeout_closes_the_connection(monkeypatch):
    monkeypatch.setenv('DEVICE_HELLO_TIMEOUT', '.01')
    monkeypatch.setenv('DEVICE_REQUIRE_HELLO', '1')
    device, reader, writer = Device(), Reader(b'hello power-on 123\n', 1), Writer()
    asyncio.run(device._accept(reader, writer))
    assert writer.closed and not device.connected


def test_late_greeting_closes_out_of_sync_connection():
    device, writer = Device(), Writer()
    device._reader, device._writer = Reader(b'hello power-on 123\n'), writer
    with pytest.raises(DeviceError, match='handshake arrived late'):
        asyncio.run(device.send('ping'))
    assert writer.closed


def test_extended_stats_reply_is_read_only_and_does_not_weaken_action_acknowledgement():
    device,writer=Device(),Writer()
    device._reader,device._writer=Reader(b'ok mouse_sub=1 keyboard_sub=1\n'),writer
    assert asyncio.run(device.send('stats'))=='ok mouse_sub=1 keyboard_sub=1'
    device._reader=Reader(b'ok mouse_sub=1 keyboard_sub=1\n')
    with pytest.raises(DeviceError):asyncio.run(device.send('click'))


def test_disconnection_while_queued_is_definitely_not_sent():
    async def run():
        d,w=Device(),Writer();d._reader,d._writer=Reader(b'ok\n'),w
        await d._lock.acquire()
        task=asyncio.create_task(d.send('type private text'))
        await asyncio.sleep(0);w.close();d._lock.release()
        with pytest.raises(DeviceError) as caught:await task
        assert caught.value.delivery=='not_sent' and caught.value.command_name=='type'
        assert 'private text' not in str(caught.value)
    asyncio.run(run())


def test_busy_agent_suppresses_background_heartbeat():
    async def run():
        d,w=Device(),Writer();d._reader,d._writer=Reader(b'ok\n'),w;d._beat=0
        sent=[]
        async def send(command):sent.append(command)
        d.send=send
        job=asyncio.create_task(d.keep_awake(heartbeat=.001,busy=lambda:True))
        await asyncio.sleep(.01);job.cancel()
        with pytest.raises(asyncio.CancelledError):await job
        assert not sent
    asyncio.run(run())


def test_lost_ack_after_payload_is_ambiguous_and_never_retried(monkeypatch):
    async def run():
        d,w=Device(),Writer();d._reader,d._writer=Reader(b'ok\n',1),w
        d._timeout=lambda _: .001
        with pytest.raises(DeviceError) as caught:await d.send('type SECRET')
        assert caught.value.delivery=='unknown' and caught.value.command_name=='type'
        assert w.closed and 'SECRET' not in str(caught.value)
        assert len(d.timings)==1 and d.timings[-1]['outcome']=='failed'
    asyncio.run(run())


def test_eof_immediately_marks_controller_disconnected():
    async def run():
        d,w=Device(),Writer();d._reader,d._writer=Reader(b''),w
        with pytest.raises(DeviceError):await d.send('ping')
        assert not d.connected
    asyncio.run(run())


def test_timing_sequence_distinguishes_fresh_input_without_leaking_payload():
    import time
    async def run():
        d,w=Device(),Writer();d._reader,d._writer=Reader(b'ok\n'),w
        started=time.time()
        await d.send('type PRIVATE-PAYLOAD')
        await d.send('key enter')
        assert [r['sequence'] for r in d.timings]==[1,2]
        assert all(r['finished_at']>=started for r in d.timings)
        assert 'PRIVATE-PAYLOAD' not in repr(d.timings)
        assert [r['command'] for r in d.timings]==['type','key']
    asyncio.run(run())


def test_idle_nudge_disabled_uses_ping_only():
    from unittest.mock import AsyncMock
    import time
    async def run():
        d=Device();d._writer=type('Writer',(),{'is_closing':lambda self:False})()
        d._last=time.time()-100;d.send=AsyncMock()
        job=asyncio.create_task(d.keep_awake(heartbeat=.001,nudge=False))
        await asyncio.sleep(.006);job.cancel()
        try:await job
        except asyncio.CancelledError:pass
        assert d.send.await_count>0
        assert all(c.args==('ping',) for c in d.send.await_args_list)
    asyncio.run(run())
