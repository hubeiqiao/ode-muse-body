"""Durable benchmark reservations and uncertainty, no device activity."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from hidagent.wda_driver import WDAError

PATH = Path(__file__).resolve().parents[2] / 'legacy/tools/wda/benchmark.py'
spec = importlib.util.spec_from_file_location('wda_benchmark', PATH)
bench = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bench
spec.loader.exec_module(bench)


def test_saved_reservation_prevents_replay(tmp_path, monkeypatch):
    async def run():
        factory = AsyncMock(return_value=SimpleNamespace(http=SimpleNamespace(aclose=AsyncMock())))
        operation = AsyncMock(return_value={'verified': True, 'model_calls': 0, 'driver': 'wda'})
        monkeypatch.setattr(bench, 'search', operation)
        record = await bench.run_case(tmp_path, 'youtube', 'fresh public query', factory=factory)
        assert record['status'] == 'verified' and record['total_s'] >= 0
        assert json.loads((tmp_path / 'youtube/result.json').read_text())['status'] == 'verified'
        assert (tmp_path / 'youtube/intent.json').is_file()
        with pytest.raises(FileExistsError):
            await bench.run_case(tmp_path, 'youtube', 'fresh public query', factory=factory)
        operation.assert_awaited_once()
    asyncio.run(run())


def test_uncertain_receipt_saved_and_batch_stops(tmp_path, monkeypatch):
    async def run():
        fake = SimpleNamespace(http=SimpleNamespace(aclose=AsyncMock()))
        monkeypatch.setattr(bench, 'search', AsyncMock(side_effect=WDAError('Lost input receipt', delivery_uncertain=True)))
        with pytest.raises(WDAError):
            await bench.run_case(tmp_path, 'x', 'fresh public query', factory=AsyncMock(return_value=fake))
        record = json.loads((tmp_path / 'x/result.json').read_text())
        assert record['status'] == 'input_uncertain' and record['never_retry_typing']
        fake.http.aclose.assert_awaited_once()
    asyncio.run(run())


def test_report_never_fills_unmeasured_rows_with_reference_timings(tmp_path):
    bench.write_report(tmp_path)
    report = (tmp_path / 'README.zh-CN.md').read_text()
    assert report.count('未测') == 10
    assert 'rednote excluded: security restriction, account abnormal' in report
    assert '~60' in report and '~8.7' in report


def test_tracking_id_is_not_appended_to_public_search_queries(tmp_path, monkeypatch):
    async def run():
        out = tmp_path / 'new-run'
        operation = AsyncMock()
        monkeypatch.setattr(bench, 'run_case', operation)
        await bench.run_batch(out, ['google_maps', 'app_store'], 'deadbeef')
        assert [c.args[2] for c in operation.await_args_list] == [
            'Ottawa public library', 'IELTS speaking']
        assert json.loads((out / 'batch.json').read_text())['run_id'] == 'deadbeef'
    asyncio.run(run())
