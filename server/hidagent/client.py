"""HTTP client for the legacy server, shared by the CLI and optional MCP bridge."""

import os
import time
import math
from .command_input import standalone_dictation

import httpx


class IPhoneClient:
    def __init__(self, url: str | None = None, *, transport=None):
        url = url or os.environ.get("IPHONE_AGENT_URL", "http://127.0.0.1:8890")
        parsed = httpx.URL(url)
        if parsed.scheme not in ("http", "https") or not parsed.host or parsed.query or parsed.fragment:
            raise ValueError("server URL must be an HTTP(S) URL without a query or fragment")
        self.http = httpx.Client(base_url=url.rstrip("/"), timeout=60, transport=transport, trust_env=False)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.close()

    def request(self, method: str, path: str, body: dict | None = None, *, timeout: float | None = None):
        response = self.http.request(method, path, json=body, **({'timeout':timeout} if timeout is not None else {}))
        response.raise_for_status()
        return response

    def status(self) -> dict:
        return self.request("GET", "/api/status").json()

    def screenshot(self) -> bytes:
        return self.request("GET", "/api/frame.jpg").content

    def action(self, name: str, **arguments) -> dict:
        if name not in ("tap", "swipe", "scroll", "type", "key", "home", "back", "probe", "motion-timing"):
            raise ValueError("unknown control action")
        return self.request("POST", f"/api/{name}", arguments or None).json()

    def calibrate(self, x: float | None = None, y: float | None = None) -> dict:
        if x is None and y is None:
            return self.request("POST", "/api/calibrate/start", timeout=180).json()
        if x is None or y is None:
            raise ValueError("provide both pointer coordinates")
        return self.request("POST", "/api/calibrate/finish", {"x": x, "y": y}).json()

    def delegate(self, goal: str) -> dict:
        return self.request("POST", "/api/agent", {"text": goal}).json()

    def command(self, goal: str, wait_seconds: float = 20) -> dict:
        """One natural-language command with a bounded wait; never retry a mutation."""
        if not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 30:
            raise ValueError('wait_seconds must be between 0 and 30')
        started = time.monotonic()
        self.delegate(goal)
        while True:
            state = self.status()
            job = state['agent']
            if not job['running'] or time.monotonic() - started >= wait_seconds:
                break
            time.sleep(.1)
        final = job['steps'][-1] if job['steps'] else {}
        outcome = 'in_progress' if job['running'] else 'completed' if final.get('action') == 'done' else 'failed'
        if outcome == 'failed' and standalone_dictation(goal) is not None and any(step.get('action') in ('type','type_uncertain') for step in job['steps']):
            outcome = 'executed_unverified'  # input happened; never invite automatic duplicate typing
        return {'status': outcome, 'engine': state.get('engine'), 'elapsed_s': round(time.monotonic()-started,3),
                'result': final, 'task': job, 'verification': 'Model completion; inspect the returned actual screen.'}

    def task_status(self) -> dict:
        return self.status()["agent"]

    def stop(self) -> dict:
        return self.request("POST", "/api/agent/stop").json()
