"""Benchmark adapter tests use fake receipts and local trace files only."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def benchmark(monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'tools/basic_ops_benchmark.py'
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location('basic_ops_benchmark', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_route_report_preserves_fallback_and_unknown_probe(benchmark):
    steps = [dict(kind='app-search-route', route_used='keyboard_shortcut', phase='focus', pointer_probe_sent=False),
             dict(kind='app-search-route', route_used='pointer', phase='focus', pointer_probe_sent=None)]
    assert benchmark.route_report(steps, []) == dict(
        route_used='pointer', routes_attempted=['keyboard_shortcut', 'pointer'], pointer_probe_sent=None)
    timeline = [dict(events=[dict(event='pointer_probe', pointer_probe_sent=True, delivery='unknown')])]
    assert benchmark.route_report(steps, timeline)['pointer_probe_sent'] is True
    assert benchmark.route_report(steps[:1], [])['pointer_probe_sent'] is False
    assert benchmark.route_report([], [])['pointer_probe_sent'] is None


class FakeHTTP:
    def __init__(self, data, *, lost=False, enabled=True, pending=False, status='completed'):
        self.calls, self.data, self.lost, self.enabled, self.pending = [], data, lost, enabled, pending
        self.status = status

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == '/api/app-tools/catalog':
            return dict(server_instance='a' * 32, app_search_keyboard_routes=dict(enabled=self.enabled, apps=['X', 'TikTok']))
        if path == '/api/status':
            return dict(calibrated=True, controller=dict(stable=True), agent=dict(running=False))
        if method == 'POST':
            if self.lost: raise TimeoutError('Uncertain POST receipt')
            trace = self.data / 'traces/test-run'
            trace.mkdir(parents=True)
            (trace / 'steps.jsonl').write_text(json.dumps(dict(kind='step', phase='focus', route_used='keyboard_shortcut', pointer_probe_sent=False)) + '\n')
            return dict(status='accepted')
        return dict(status='in_progress' if self.pending else self.status, terminal=not self.pending)


def test_benchmark_dispatches_registered_program_once_and_prints_route(benchmark, tmp_path, capsys):
    http = FakeHTTP(tmp_path / 'data')
    out = tmp_path / 'out'
    result = benchmark.run_case(http, benchmark.CASES[3], tmp_path / 'data', out, expected_keyboard=True, sleep=lambda _: None)
    assert result['route_used'] == 'keyboard_shortcut'
    assert 'route_used=keyboard_shortcut' in capsys.readouterr().out
    posts = [(path, body) for method, path, body in http.calls if method == 'POST']
    assert len(posts) == 1 and posts[0][0] == '/api/app-tools/call'
    assert posts[0][1]['tool_name'] == 'x.search'
    assert json.loads((out / 'raw/b04-x.json').read_text())['route_used'] == 'keyboard_shortcut'


def test_benchmark_flag_mismatch_stops_before_dispatch(benchmark, tmp_path):
    http = FakeHTTP(tmp_path / 'data', enabled=False)
    with pytest.raises(RuntimeError, match='server'):
        benchmark.run_case(http, benchmark.CASES[3], tmp_path / 'data', tmp_path / 'out', expected_keyboard=True)
    assert not any(method == 'POST' for method, _, _ in http.calls)


def test_benchmark_uncertain_dispatch_never_resends(benchmark, tmp_path):
    http = FakeHTTP(tmp_path / 'data', lost=True)
    with pytest.raises(TimeoutError):
        benchmark.run_case(http, benchmark.CASES[3], tmp_path / 'data', tmp_path / 'out', expected_keyboard=True)
    assert sum(method == 'POST' for method, _, _ in http.calls) == 1


@pytest.mark.parametrize('status', ['failed', 'not_ready'])
def test_benchmark_records_known_failure_without_aborting_app_matrix(benchmark, tmp_path, status):
    http = FakeHTTP(tmp_path / 'data', status=status)
    result = benchmark.run_case(http, benchmark.CASES[3], tmp_path / 'data', tmp_path / 'out',
                                expected_keyboard=True, sleep=lambda _: None)
    assert result['status']['status'] == status
    assert sum(method == 'POST' for method, _, _ in http.calls) == 1


def test_benchmark_timeout_stops_batch_without_resending(benchmark, tmp_path):
    http = FakeHTTP(tmp_path / 'data', pending=True)
    ticks = iter([0, 0, 2])
    with pytest.raises(TimeoutError):
        benchmark.run_case(http, benchmark.CASES[3], tmp_path / 'data', tmp_path / 'out',
                           expected_keyboard=True, timeout=1, monotonic=lambda: next(ticks), sleep=lambda _: None)
    assert sum(method == 'POST' for method, _, _ in http.calls) == 1


def test_catalog_reports_server_flag_and_narrowed_supported_apps(monkeypatch):
    from hidagent.app_tools import AppTools
    tools = AppTools(None, None, None, None, None)
    monkeypatch.setenv('APP_SEARCH_KEYBOARD_ROUTES', '1')
    monkeypatch.setenv('APP_SEARCH_KEYBOARD_APPS', 'X,Reddit')
    assert tools.catalog()['app_search_keyboard_routes'] == dict(enabled=True, apps=['X'])
    monkeypatch.delenv('APP_SEARCH_KEYBOARD_ROUTES')
    assert tools.catalog()['app_search_keyboard_routes'] == dict(enabled=False, apps=[])
