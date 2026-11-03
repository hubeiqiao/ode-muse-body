"""Local, single-owner task receipts. Storage is not permission to replay HID."""
import json
import os
import sqlite3
import time
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

_metrics_scope = ContextVar('receipt_metrics', default=None)


class ReceiptResult(dict):
    """Carry separate metrics through the journal's existing five-argument hook."""
    def __init__(self, value, metrics):
        super().__init__(value)
        self.metrics = metrics


class ReceiptMetrics:
    """Content-free observation of model attempts and acknowledged HID packets.

    The executor's single-owner lease already serializes AppTools. Wrappers only
    observe calls in this task's inherited context and are removed at completion.
    No prompts, replies, frames, commands, model names or policy strings are kept.
    """
    def __init__(self, started, *, clock=time.monotonic):
        self.started, self.clock = started, clock
        self.calls = {key: {'count': 0, 'total_s': 0.0} for key in ('jev', 'muse', 'other')}
        self.first = None
        self.hid_actions = self.waits = 0
        self.launch_method = None
        self._restore = []
        self._token = None
        self._inflight = {}
        self._frozen = None

    def model_call(self, bucket, seconds):
        self.calls[bucket]['count'] += 1
        self.calls[bucket]['total_s'] += max(0, seconds)

    @staticmethod
    def bucket(client):
        if getattr(client, 'provider', None) == 'meta':
            return 'muse'
        return 'jev' if str(getattr(client, 'model', '')).lower().startswith('jev') else 'other'

    def _replace(self, obj, name, wrapper):
        original = getattr(obj, name)
        owned = name in vars(obj)
        setattr(obj, name, wrapper(original))
        self._restore.append((obj, name, original, owned))

    def observe(self, agent=None, writer=None):
        self._token = _metrics_scope.set(self)
        seen = set()

        def client(obj):
            if obj is None or id(obj) in seen:
                return
            seen.add(id(obj))
            # Count leaf calls: fallback evaluate and vision evaluate may each
            # make multiple provider requests, including repairs and hedges.
            children = [getattr(obj, key, None) for key in ('primary', 'backup', 'llm')]
            if any(item is not None for item in children):
                for item in children:
                    client(item)
                return
            name = 'chat' if callable(getattr(obj, 'chat', None)) else 'evaluate'
            if not callable(getattr(obj, name, None)):
                return
            bucket = self.bucket(obj)
            def wrap(original):
                @wraps(original)
                async def measured(*args, **kwargs):
                    if _metrics_scope.get() is not self:
                        return await original(*args, **kwargs)
                    at = self.clock()
                    key = object()
                    self.calls[bucket]['count'] += 1
                    self._inflight[key] = (bucket, at)
                    try:
                        return await original(*args, **kwargs)
                    finally:
                        self.calls[bucket]['total_s'] += max(0, self.clock() - at)
                        self._inflight.pop(key, None)
                return measured
            self._replace(obj, name, wrap)

        client(writer)
        if agent is None:
            return
        for name in ('client', 'planner', 'actor', 'source_writer', 'inventory_writer', 'fallback'):
            client(getattr(agent, name, None))
        phone = getattr(agent, 'phone', None)
        if phone is not None and callable(getattr(phone, '_send', None)):
            def wrap_send(original):
                @wraps(original)
                async def send(command, *args, **kwargs):
                    at = self.clock()
                    result = await original(command, *args, **kwargs)
                    if (_metrics_scope.get() is self and isinstance(command, str)
                            and command.partition(' ')[0] in ('move', 'click', 'press', 'scroll', 'key', 'type', 'home')):
                        self.hid_actions += 1
                        if self.first is None:
                            self.first = max(0, at - self.started)
                    return result
                return send
            self._replace(phone, '_send', wrap_send)
        trace = getattr(agent, 'trace', None)
        if trace is not None and callable(getattr(trace, 'write', None)):
            def wrap_trace(original):
                @wraps(original)
                def write(*args, **record):
                    if _metrics_scope.get() is self:
                        if record.get('kind') == 'step' and record.get('action') == 'wait':
                            self.waits += 1
                        method = record.get('launch_method')
                        if method in ('spotlight_first', 'home_icon', 'shortcut', 'foreground_reuse'):
                            self.launch_method = method
                    return original(*args, **record)
                return write
            self._replace(trace, 'write', wrap_trace)

    def close(self):
        for obj, name, original, owned in reversed(self._restore):
            if owned:
                setattr(obj, name, original)
            else:
                delattr(obj, name)
        self._restore.clear()
        if self._token is not None:
            _metrics_scope.reset(self._token)
            self._token = None

    def snapshot(self, finished=None):
        if self._frozen is not None:
            return copy.deepcopy(self._frozen)
        end = self.clock() if finished is None else finished
        calls = copy.deepcopy(self.calls)
        for bucket, at in self._inflight.values():
            calls[bucket]['total_s'] += max(0, end - at)
        value = self.combine(max(0, end - self.started), calls,
                             self.first, self.hid_actions, self.waits, self.launch_method)
        if finished is not None:
            self._frozen = value
        return copy.deepcopy(value)

    @staticmethod
    def combine(total, calls=None, first=None, inputs=0, waits=0, launch=None):
        split = {k: {'count': (calls or {}).get(k, {}).get('count', 0),
                     'total_s': round((calls or {}).get(k, {}).get('total_s', 0), 3)}
                 for k in ('jev', 'muse', 'other')}
        count = sum(v['count'] for v in split.values())
        seconds = round(sum(v['total_s'] for v in split.values()), 3)
        feedback = 'first action unavailable' if first is None else f'first action {first:.1f} s'
        return {'total_s': round(total, 3), 'model_calls': {'count': count, 'total_s': seconds, **split},
                'first_feedback_s': None if first is None else round(first, 3),
                'hid_actions': inputs, 'waits': waits, 'launch_method': launch,
                'summary': f'{total:.1f} s, {count} model calls ({seconds:.1f} s), {feedback}, {inputs} inputs'}


TERMINAL=frozenset(('completed','needs_review','failed','stopped','interrupted','input_uncertain'))
class ReceiptJournalError(RuntimeError):pass
class ReceiptConflict(ReceiptJournalError):pass

class ReceiptJournal:
    def __init__(self,path,*,capacity=4096):
        self.path=Path(path);self.capacity=capacity;self._lease=None
        self.path.parent.mkdir(parents=True,exist_ok=True)
        try:
            import fcntl
            self._lease=os.open(str(self.path)+'.lock',os.O_CREAT|os.O_RDWR,0o600)
            fcntl.flock(self._lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            fd=os.open(self.path,os.O_CREAT|os.O_RDWR,0o600);os.close(fd)
            with self._connection() as c:
                c.execute('PRAGMA journal_mode=WAL')
                c.execute("""CREATE TABLE IF NOT EXISTS receipts(
                  request_id TEXT PRIMARY KEY, server_instance TEXT NOT NULL,
                  signature TEXT NOT NULL, tool_name TEXT NOT NULL, effect TEXT NOT NULL,
                  state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                  elapsed_s REAL, result TEXT NOT NULL DEFAULT '{}')""")
                c.execute('CREATE INDEX IF NOT EXISTS receipt_order ON receipts(created_at DESC,request_id)')
                if 'metrics' not in {row['name'] for row in c.execute('PRAGMA table_info(receipts)')}:
                    c.execute("ALTER TABLE receipts ADD COLUMN metrics TEXT NOT NULL DEFAULT '{}'")
        except Exception:
            self.close();raise ReceiptJournalError('Task receipt storage is unavailable or already owned') from None
    @contextmanager
    def _connection(self):
        c=sqlite3.connect(self.path,timeout=1.5)
        c.row_factory=sqlite3.Row
        try:
            c.execute('PRAGMA synchronous=FULL')
            with c:yield c
        finally:c.close()
    def close(self):
        if self._lease is not None:
            os.close(self._lease);self._lease=None
    def _ready(self):
        if self._lease is None:raise ReceiptJournalError('Task receipt storage is closed')
    def recover(self,current_instance):
        self._ready()
        try:
            with self._connection() as c:
                rows=c.execute("SELECT * FROM receipts WHERE state IN ('accepted','started') AND server_instance!=?",(current_instance,)).fetchall()
                for row in rows:
                    uncertain=row['state']=='started' and row['effect'] in ('device','sequence')
                    result={'reason':'Server restarted before a final receipt. Inspect current device before a new instruction; no automatic replay.',
                            'delivery_uncertain':uncertain,'never_retry_typing':uncertain,
                            'input_delivery':'unknown' if uncertain else 'not_dispatched' if row['state']=='accepted' else 'no_device_input'}
                    c.execute("UPDATE receipts SET state='interrupted',updated_at=?,result=? WHERE request_id=?",(time.time(),json.dumps(result),row['request_id']))
        except sqlite3.Error:raise ReceiptJournalError('Could not recover task receipts safely') from None
    def get(self,request_id):
        self._ready()
        try:
            with self._connection() as c:row=c.execute('SELECT * FROM receipts WHERE request_id=?',(request_id,)).fetchone()
            return self._decode(row) if row else None
        except (sqlite3.Error,ValueError):raise ReceiptJournalError('Could not read task receipt') from None
    @staticmethod
    def _decode(row):
        value=dict(row);value['result']=json.loads(value['result'])
        value['metrics']=json.loads(value['metrics']) or {**ReceiptMetrics.combine(value['elapsed_s'] or 0), 'instrumentation':'unavailable'}
        return value
    def reserve(self,request_id,instance,signature,tool_name,effect):
        self._ready()
        try:
            with self._connection() as c:
                c.execute('BEGIN IMMEDIATE')
                old=c.execute('SELECT * FROM receipts WHERE request_id=?',(request_id,)).fetchone()
                if old:
                    if old['signature']!=signature or old['tool_name']!=tool_name:raise ReceiptConflict('Request id changed; no operation was replayed')
                    return self._decode(old)
                if c.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]>=self.capacity:raise ReceiptJournalError('Task receipt history is full; no operation was started')
                now=time.time()
                c.execute("INSERT INTO receipts(request_id,server_instance,signature,tool_name,effect,state,created_at,updated_at) VALUES(?,?,?,?,?,'accepted',?,?)",
                          (request_id,instance,signature,tool_name,effect,now,now))
            return None
        except sqlite3.Error:raise ReceiptJournalError('Could not reserve receipt; no operation was started') from None
    def started(self,request_id,instance):
        self._ready()
        try:
            with self._connection() as c:
                changed=c.execute("UPDATE receipts SET state='started',updated_at=? WHERE request_id=? AND server_instance=? AND state='accepted'",(time.time(),request_id,instance)).rowcount
                if changed!=1:raise ReceiptConflict('Task is no longer eligible to start')
        except sqlite3.Error:raise ReceiptJournalError('Could not mark dispatch; no operation was started') from None
    def finish(self,request_id,instance,state,result,elapsed_s,*,metrics=None):
        self._ready()
        if state not in TERMINAL or not isinstance(result,dict):raise ReceiptJournalError('Invalid terminal task receipt')
        data=json.dumps(result,ensure_ascii=False,allow_nan=False)
        metric_data=json.dumps(metrics or getattr(result,'metrics',{}),allow_nan=False)
        if len(data.encode())>131072:raise ReceiptJournalError('Task receipt exceeded local storage limit')
        try:
            with self._connection() as c:
                allowed="state!='stopped'" if state=='stopped' else "state IN ('accepted','started')"
                changed=c.execute("UPDATE receipts SET state=?,updated_at=?,elapsed_s=?,result=?,metrics=? WHERE request_id=? AND server_instance=? AND "+allowed,
                                  (state,time.time(),elapsed_s,data,metric_data,request_id,instance)).rowcount
                if changed:return True
                old=c.execute('SELECT state,server_instance FROM receipts WHERE request_id=?',(request_id,)).fetchone()
                return bool(old and old['state']==state and old['server_instance']==instance)
        except sqlite3.Error:raise ReceiptJournalError('Final receipt could not be saved; never repeat the operation') from None
    def history(self,limit=20,offset=0):
        self._ready()
        if type(limit)is not int or not 1<=limit<=100 or type(offset)is not int or not 0<=offset<=self.capacity:raise ReceiptJournalError('Invalid history page')
        try:
            with self._connection() as c:
                rows=c.execute('SELECT * FROM receipts ORDER BY created_at DESC,request_id LIMIT ? OFFSET ?',(limit,offset)).fetchall()
                count=c.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
            return [self._decode(row) for row in rows],count
        except (sqlite3.Error,ValueError):raise ReceiptJournalError('Could not read task history') from None
