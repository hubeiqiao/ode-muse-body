"""Optional, read-only text observation through a persistent local Vision worker.

OCR rows are text+boxes, not accessibility nodes, roles, or proof of focus.
"""
import asyncio
import base64
import copy
import hashlib
import json
import math
import time


class LocalTextError(RuntimeError):
    pass


class LocalTextReader:
    def __init__(self, command, timeout=3.0):
        self.command = list(command)
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._process = None
        self._cached_hash = None
        self._cached = None

    async def _close_process(self):
        process, self._process = self._process, None
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()

    async def close(self):
        async with self._lock:
            await self._close_process()

    @staticmethod
    def _validate(data):
        if not isinstance(data, dict):
            raise ValueError('invalid observation')
        if data.get('source') != 'local_vision_ocr' or data.get('is_accessibility_tree') is not False:
            raise ValueError('unexpected observation source')
        rows = data.get('rows')
        if not isinstance(rows, list) or len(rows) > 2000:
            raise ValueError('invalid rows')
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('box'), dict):
                raise ValueError('invalid row')
            if not isinstance(row.get('text'), str):
                raise ValueError('invalid text')
            values = [row.get('confidence'), *[row.get('box', {}).get(k) for k in ('left', 'top', 'right', 'bottom')]]
            if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
                raise ValueError('invalid confidence/box')
            b = row['box']
            if b['left'] >= b['right'] or b['top'] >= b['bottom']:
                raise ValueError('empty box')
        return data

    async def read(self, frame, *, mode="fast"):
        if mode not in ("fast","accurate"):raise LocalTextError("Unsupported local OCR mode")
        if not isinstance(frame, bytes) or not frame or len(frame) > 8*1024*1024:
            raise LocalTextError('No supported frame to read')
        digest = hashlib.sha256(frame).hexdigest()
        started = time.perf_counter()
        async with self._lock:
            if (digest,mode) == self._cached_hash:
                return {**copy.deepcopy(self._cached), 'cache_hit': True, 'read_s': round(time.perf_counter()-started, 4)}
            try:
                if self._process is None or self._process.returncode is not None:
                    self._process = await asyncio.create_subprocess_exec(*self.command,
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL, limit=8*1024*1024)
                async def exchange():
                    self._process.stdin.write(json.dumps({'image_base64': base64.b64encode(frame).decode(),'mode':mode}).encode()+b'\n')
                    await self._process.stdin.drain()
                    return await self._process.stdout.readline()
                line = await asyncio.wait_for(exchange(), self.timeout)
                data = self._validate(json.loads(line))
                if mode=="accurate" and data.get("mode")!="accurate":raise ValueError("Worker does not support accurate mode")
            except asyncio.CancelledError:
                await self._close_process()  # discard a late reply; never associate it with the next frame
                raise
            except (OSError, TimeoutError, ValueError, TypeError, KeyError) as exc:
                await self._close_process()
                raise LocalTextError('Local text observation unavailable; use the screenshot fallback') from exc
            data.update(frame_sha256=digest, recognition_languages=['en-US'],
                        limitations=['OCR only: no element roles or verified keyboard focus. Text may be incomplete or wrong.'])
            self._cached_hash, self._cached = (digest,mode), copy.deepcopy(data)
            return {**data, 'cache_hit': False, 'read_s': round(time.perf_counter()-started, 4)}
