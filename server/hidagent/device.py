"""TCP link to the HID bridge board (see firmware/hid_bridge)."""

import asyncio
import logging
import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
import os
import time
from collections import deque
from .hid_lease import check_hid

log = logging.getLogger(__name__)


_timeline = ContextVar('input_timeline', default=None)
_source = ContextVar('input_command_source', default='foreground')
_probe_gate = ContextVar('input_probe_gate', default=None)


class InputTimelines:
    """Bounded metadata-only diagnostics in the existing trace root.

    Each file is at most max_bytes; one rotated file and last completed rows
    are retained. Disk failures never change input delivery or trigger retries.
    """
    def __init__(self, root, max_bytes=16 << 20, last=20, max_events=32768):
        self.root = root
        self.max_bytes = max_bytes
        self.max_events = max_events
        self.recent = deque(maxlen=last)
        self.write_errors = 0

    @contextmanager
    def capture(self, path):
        identity = uuid.uuid4().hex
        row = {'request_id': identity, 'action_id': identity, 'path': path,
               'events': [], 'dropped_events': 0, 'event_limit': self.max_events}
        token = _timeline.set(row)
        try:
            yield row
        finally:
            _timeline.reset(token)

    def finish(self, row, status):
        timeline_event('finish', status=status)
        self.recent.append(row)
        try:
            encoded = (json.dumps(row, separators=(',', ':')) + '\n').encode()
            if len(encoded) > self.max_bytes:
                # Retain a bounded prefix and the final outcome, mark omissions.
                events = row['events']
                compact = dict(row, events=list(events))
                while len(encoded) > self.max_bytes and len(compact['events']) > 1:
                    compact['events'].pop(-2)
                    compact['dropped_events'] += 1
                    encoded = (json.dumps(compact, separators=(',', ':')) + '\n').encode()
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / 'input-timelines.jsonl'
            if path.exists() and path.stat().st_size + len(encoded) > self.max_bytes:
                path.replace(self.root / 'input-timelines.previous.jsonl')
            with path.open('ab') as stream:
                stream.write(encoded)
        except OSError:
            self.write_errors += 1


def timeline_event(event, **fields):
    row = _timeline.get()
    if row is not None:
        item = {'event': event, 'mono_s': time.monotonic(), **fields}
        if len(row['events']) < row['event_limit']:
            row['events'].append(item)
        elif event == 'finish':
            row['events'][-1] = item
            row['dropped_events'] += 1
        else:
            row['dropped_events'] += 1


@contextmanager
def command_source(source):
    token = _source.set(source)
    try:
        yield
    finally:
        _source.reset(token)


@contextmanager
def probe_recheck(check):
    token = _probe_gate.set(check)
    try:
        yield
    finally:
        _probe_gate.reset(token)


class DeviceError(RuntimeError):
    def __init__(self, message, *, delivery="unknown", command_name=None):
        super().__init__(message)
        self.delivery, self.command_name = delivery, command_name



class _LoopLagProbe:
    """Host scheduling diagnostic, not a measurement of network RTT or board work."""
    interval = .1

    def __init__(self):
        self.loop=asyncio.get_running_loop()
        self.max_lag=0.0
        self.due=self.loop.time()+self.interval
        self.handle=self.loop.call_later(self.interval,self._tick)

    def _tick(self):
        now=self.loop.time()
        self.max_lag=max(self.max_lag,max(0,now-self.due))
        self.due=now+self.interval
        self.handle=self.loop.call_later(self.interval,self._tick)

    def finish(self):
        # Also count an overdue callback not yet dispatched when ACK completes.
        self.max_lag=max(self.max_lag,max(0,self.loop.time()-self.due))
        self.handle.cancel()
        return round(self.max_lag,4)


class Device:
    """Accepts one board connection and sends it commands, one at a time."""

    def __init__(self):
        self._reader = self._writer = None
        self._lock = asyncio.Lock()
        self.foreground_pending = 0
        self.diagnostics = None
        self._lock_holder = self._previous_holder = None
        self._enqueue_sequence = 0
        self._last = self._beat = 0.0
        self._command_sequence = 0
        self.timings = deque(maxlen=100)  # command names/timing only, never typed content
        self._connected_at = 0.0
        self.links = 0  # bumped on every (re)connect, so gestures know to release and re-home

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    def readiness(self, now=None):
        """Passive current-link ACK evidence, never a guarantee of future Wi-Fi delivery.
        HID ACKs include deliberate BLE pacing, so only diagnostic ping/stats use the 2s limit."""
        now=time.time() if now is None else now
        samples=[x for x in self.timings if x.get('link')==self.links and now-x['finished_at']<15][-2:]
        age=max(0,now-self._connected_at)
        stable=bool(self.connected and age>=4 and len(samples)==2 and all(x['outcome']=='ok' and (x['command'] not in ('ping','stats') or x['wire_s']<=2) for x in samples))
        reason='disconnected' if not self.connected else 'ready' if stable else 'waiting_for_stable_acknowledgements'
        return {'connected':self.connected,'stable':stable,'reason':reason,'link_generation':self.links,
                'recent_ack_count':len(samples),'last_ack_s':samples[-1]['wire_s'] if samples else None}

    async def serve(self, host: str, port: int):
        return await asyncio.start_server(self._accept, host, port)

    async def _accept(self, reader, writer):
        # newer firmware greets with "hello <last reset reason> <uptime ms>": read it before any command
        try:
            hello = (await asyncio.wait_for(reader.readline(), float(os.environ.get("DEVICE_HELLO_TIMEOUT", "1")))).decode().strip()
        except (OSError, asyncio.TimeoutError):
            hello = ""
        if os.environ.get("DEVICE_REQUIRE_HELLO") == "1" and not hello.startswith("hello "):
            log.warning("controller greeting missing; waiting for a clean reconnect")
            writer.close()
            await writer.wait_closed()
            return
        if self.connected:
            self._writer.close()
        self._reader, self._writer = reader, writer
        self.links += 1
        self._connected_at = time.time()
        self._last = self._beat = time.time()  # fresh link: nothing to keep awake yet
        log.info("board connected from %s %s", writer.get_extra_info("peername"), hello)

    async def wait_connected(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while not self.connected and time.time() < deadline:
            await asyncio.sleep(0.2)
        return self.connected

    @staticmethod
    def _timeout(command: str) -> float:
        """Seconds the board may take: Wi-Fi replies need up to ~2 s at p99, moves are paced
        (configured report interval, normally 40ms), typing is per character.
        wire_s also includes host scheduling delays; it is not pure network RTT."""
        name, _, arg = command.partition(" ")
        if name in ("move", "home"):
            # A 40-100ms paced report cannot finish within the old 12ms/report
            # budget. Wait for this command's ACK, never reconnect/replay a
            # legitimate long move. Cancellation still closes the link.
            try:
                report_s = max(40, min(100, int(os.environ.get("HID_POINTER_MS", "40")))) / 1000
            except ValueError:
                report_s = .1
            reports = 50 if name == "home" else (max(abs(int(n)) for n in arg.split()) + 9) // 10
            return 5 + reports * (report_s + .02)
        if name == "type":
            return 5 + 0.24 * len(arg)  # slowest supported typing + bounded BLE retry allowance
        if name == "ping":
            return 8  # an idle check: tolerate a Wi-Fi hiccup rather than drop a healthy board
        return 5

    def background_yielding(self):
        return os.environ.get('HEARTBEAT_YIELD', '0') == '1' and self.foreground_pending > 0

    async def send(self, command: str) -> str | None:
        # No awaits between arbitration and enqueue. Already queued/sent work
        # retains the original lock/ACK lifecycle and is never cancelled here.
        if command.partition(' ')[0] in ('ping', 'stats') and _source.get() in ('heartbeat', 'health') and self.background_yielding():
            timeline_event('command_yielded', source=_source.get(), outcome='not_enqueued')
            return None
        # Background traffic keeps its in-memory transport/lock history, but
        # must not evict foreground rows or append a JSONL record every beat.
        if self.diagnostics is not None and _timeline.get() is None and _source.get() not in ('heartbeat', 'health'):
            with self.diagnostics.capture('device/' + _source.get()) as row:
                status = 'failed'
                try:
                    result = await self._diagnosed_send(command)
                    status = 'ok'
                    return result
                finally:
                    self.diagnostics.finish(row, status)
        return await self._diagnosed_send(command)

    async def _diagnosed_send(self, command):
        self._enqueue_sequence += 1
        command_id = self._enqueue_sequence
        name = command.partition(' ')[0]
        # Raw debug commands can contain arbitrary text even in their first word.
        safe_name = name if name in {'move','home','type','key','typing','tune','ping','stats','press','release','click','scroll'} else 'other'
        meta = {'command_id': command_id, 'command': safe_name, 'source': _source.get(),
                'link_generation': self.links,
                'prior_lock_holder': self._lock_holder or self._previous_holder}
        timeline_event('command_enqueue', **meta)
        outcome = 'failed'
        try:
            result = await self._send_command(command, meta)
            outcome = meta.pop('_outcome', 'ok')
            return result
        except DeviceError as error:
            meta['delivery'] = error.delivery
            raise
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        finally:
            timeline_event('command_finish', **meta, outcome=outcome)

    async def _send_command(self, command: str, meta) -> str | None:
        name = command.partition(" ")[0]
        if not self.connected:
            raise DeviceError("board not connected", delivery="not_sent", command_name=name)
        timeout = self._timeout(command) * float(os.environ.get("DEVICE_TIMEOUT_SCALE", "1"))
        queued = time.monotonic()
        async with self._lock:
            meta['link_generation'] = self.links
            meta['prior_lock_holder'] = self._previous_holder
            timeline_event('command_lock_acquired', **meta)
            queue_s = time.monotonic() - queued
            if not self.connected:
                raise DeviceError("board not connected before command was sent", delivery="not_sent", command_name=name)
            # A command can wait behind another ACK after Phone checked its
            # lease. Recheck at the final queue boundary before any wire write.
            # Rejection is definitely not_sent and must not close a new link or
            # create a transport sample for a packet that never existed.
            try:
                check_hid(command)
            except (Exception, asyncio.CancelledError):
                raise DeviceError("input lease expired before command was sent",
                                  delivery="not_sent", command_name=name) from None
            gate = _probe_gate.get()
            if name == 'move' and gate is not None and gate():
                meta['_outcome'] = 'probe_skipped'
                return None
            self._command_sequence += 1
            sequence = self._command_sequence
            reader, writer = self._reader, self._writer
            link = self.links
            started, written, outcome = time.monotonic(), False, "failed"
            drain_finished = None
            holder = {'command_id': meta['command_id'], 'command': meta['command'],
                      'source': meta['source'], 'link_generation': link}
            self._lock_holder = holder
            scheduler=_LoopLagProbe()
            try:
                writer.write(command.encode() + b"\n")
                written = True
                timeline_event('command_write_done', **meta)
                # One budget covers asynchronous write backpressure and ACK.
                # A successful write() is already potentially delivered, even
                # when drain times out; never classify it as safe to resend.
                await asyncio.wait_for(writer.drain(), timeout)
                drain_finished = time.monotonic()
                timeline_event('command_drain_done', **meta)
                remaining = timeout - (drain_finished - started)
                if remaining <= 0:
                    raise asyncio.TimeoutError
                reply = (await asyncio.wait_for(reader.readline(), remaining)).decode().strip()
                timeline_event('command_ack', **meta, accepted=reply == "ok" or (name == "stats" and reply.startswith("ok ")))
                if reply.startswith("hello "):
                    writer.close()
                    raise DeviceError("controller handshake arrived late; reconnecting", delivery="unknown", command_name=name)
                if not reply:
                    writer.close()  # EOF is a dead link, not a healthy controller
                if not (reply == "ok" or (name == "stats" and reply.startswith("ok "))):
                    raise DeviceError(reply or "board disconnected", delivery="unknown", command_name=name)
                outcome = "ok"
            except asyncio.CancelledError:
                writer.close()  # pending untagged ACK must never satisfy a later command
                outcome = "cancelled"
                raise
            except (OSError, asyncio.TimeoutError) as error:
                writer.close()
                raise DeviceError(f"board link lost during {name}: {error!r}",
                                  delivery="unknown" if written else "not_sent", command_name=name) from error
            finally:
                self._previous_holder = holder
                self._lock_holder = None
                sample = {"sequence":sequence, "link":link, "finished_at":time.time(), "command": name, "queue_s": round(queue_s, 4),
                                     "wire_s": round(time.monotonic()-started, 4),
                                     "write_drain_s": round((drain_finished or time.monotonic())-started, 4),
                                     "ack_wait_s": round(time.monotonic()-drain_finished, 4) if drain_finished is not None else 0.0,
                                     "host_loop_lag_s":scheduler.finish(), "outcome": outcome}
                self.timings.append(sample)
                timeline_event('command_transport', **meta,
                               **{k: sample[k] for k in ('queue_s', 'wire_s', 'write_drain_s', 'ack_wait_s', 'host_loop_lag_s', 'outcome')})
        self._beat = time.time()
        if name not in ("ping", "stats"):
            self._last = self._beat
        if queue_s > .5 or time.monotonic()-started > 1:
            log.info("board timing %s queue=%.3fs wire=%.3fs outcome=%s", name, queue_s, time.monotonic()-started, outcome)
        return reply if name == "stats" else None

    async def keep_awake(self, idle: float = 25, heartbeat: float = 2, screen=None, busy=None, nudge=True) -> None:
        """While idle: ping every few seconds, so a board that was unplugged or hung shows as gone within
        seconds (not at the next command), and nudge the pointer now and then so the phone does not auto-lock."""
        while True:
            await asyncio.sleep(heartbeat)
            # Heartbeats/idle nudges must not queue ahead of real input or tear
            # down a session while a model is planning its next physical action.
            if self.background_yielding() or (busy is not None and busy()) or self._lock.locked():
                continue
            if not self.connected or time.time() - self._beat < heartbeat:
                continue
            try:
                # nudge only a phone that is awake and mirroring: moving a sleeping phone's pointer can stall the
                # board on its USB link, and there is nothing to keep awake anyway
                if nudge and time.time() - self._last > idle and (screen is None or screen.live):
                    with command_source("heartbeat"):
                        await self.send("move 1 0")
                        await self.send("move -1 0")
                else:
                    with command_source("heartbeat"):
                        await self.send("ping")
            except DeviceError as e:
                log.info("board gone: %s", e)
