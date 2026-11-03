"""Offline voice-to-runner contracts. All transports and model calls are fakes."""
import asyncio
import base64
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from hidagent import drivers, wda_driver
from hidagent.live_voice import VoiceBridge
from hidagent.companion import CompanionMemory
from test_wda_driver import client, MINI, inventory
from test_wda_search import SearchServer


class Runner(SearchServer):
    def __init__(self, **kwargs):
        super().__init__('com.google.ios.youtube', **kwargs)
        self.empty = self.lost = self.secure = False

    def __call__(self, req):
        if req.url.path.endswith('/wda/keys') and self.lost:
            self.calls.append((req.url.path, json.loads(req.content)))
            raise httpx.ReadTimeout('Lost receipt', request=req)
        if req.url.path.endswith('/clear'): self.query = ''
        if req.url.path.endswith('/wda/apps/activate'): self.bundle = json.loads(req.content)['bundleId']
        data = super().__call__(req).json()
        if req.url.path.endswith('/elements') and 'Missing' in json.loads(req.content).get('value', ''):
            data['value'] = []
        if req.url.path.endswith('/element/active'): data['value'] = {'ELEMENT': 'field'}
        if req.url.path.endswith('/source'):
            if self.empty: data['value'] = {'type': 'XCUIElementTypeApplication', 'children': []}
            elif self.secure:
                data['value']['children'][0].update(type='XCUIElementTypeSecureTextField', value='PRIVATE PASSWORD')
        if req.url.path.endswith('/screenshot'):
            png = io.BytesIO()
            Image.new('RGB', (2, 2), 'white').save(png, format='PNG')
            data['value'] = base64.b64encode(png.getvalue()).decode()
        return httpx.Response(200, json=data)


def adapter(runner=None, **kwargs):
    runner = runner or Runner()
    return drivers.WDADriver(client=client(runner), **kwargs), runner


def test_wda_factory_is_lazy_and_native(monkeypatch):
    factory = AsyncMock(side_effect=AssertionError('Must remain lazy'))
    monkeypatch.setattr(wda_driver.WDAClient, 'from_env', factory)
    monkeypatch.setenv('DEVICE_DRIVER', 'wda')
    d = drivers.select_driver(lambda: {}, None)
    assert isinstance(d, drivers.WDADriver) and isinstance(d, drivers.DeviceDriver)
    factory.assert_not_called()


def test_semantic_contract_and_reused_search_session():
    async def scenario():
        d, runner = adapter()
        try:
            assert (await d.status())['device_ready']
            assert (await d.open_app('com.apple.mobilenotes'))['verification_passed']
            assert (await d.tap_text('Search'))['status'] == 'input_sent'
            assert (await d.type_text('ESP32QA 中 "literal"'))['verification_passed']
            assert 'ESP32QA 中 "literal"' in (await d.screen_text())['texts']
            for query in ('first query', 'second query'):
                result = await d.search_in_app('YouTube', query)
                assert result['status'] == 'verified' and result['app'] == 'youtube'
            assert (await d.home())['status'] == 'input_sent'
            assert sum(p == '/session' for p, _ in runner.calls) == 1
            assert not any(p.endswith('/screenshot') for p, _ in runner.calls)
        finally: await d.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('options,status', [({'duplicate': True}, 'ambiguous'), ({'focused': False}, 'not_ready')])
def test_ambiguous_tap_and_unfocused_typing(options, status):
    async def scenario():
        d, runner = adapter(Runner(**options))
        try:
            result = await (d.tap_text('Search') if options.get('duplicate') else d.type_text('ESP32QA'))
            assert result['status'] == status
            assert not any(p.endswith(('/click', '/wda/keys')) for p, _ in runner.calls)
        finally: await d.aclose()
    asyncio.run(scenario())


def test_lost_receipt_blocks_later_input():
    async def scenario():
        d, runner = adapter(); runner.lost = True
        try:
            result = await d.type_text('ESP32QA once')
            assert result['status'] == 'uncertain' and result['never_retry_typing']
            await d.type_text('ESP32QA once'); await d.home()
            assert sum(p.endswith('/wda/keys') for p, _ in runner.calls) == 1
            assert not any(p == '/wda/homescreen' for p, _ in runner.calls)
        finally: await d.aclose()
    asyncio.run(scenario())


def test_secure_fields_are_never_read_or_typed():
    async def scenario():
        d, runner = adapter(); runner.secure = True
        try:
            assert 'PRIVATE PASSWORD' not in json.dumps(await d.screen_text())
            assert (await d.type_text('ESP32QA'))['status'] == 'not_ready'
            assert not any(p.endswith('/wda/keys') for p, _ in runner.calls)
        finally: await d.aclose()
    asyncio.run(scenario())


def test_generic_voice_uses_existing_model_tree_and_memory(tmp_path):
    async def scenario():
        model = type('Model', (), {'chat': AsyncMock(side_effect=[
            '{"action":"tap","label":"Search"}', '{"action":"type","text":"ESP32QA 中"}', '{"action":"done"}'])})()
        d, runner = adapter()
        b = VoiceBridge(lambda: {}, AsyncMock(), AsyncMock(), memory=CompanionMemory(tmp_path), driver=d, executor_model=model)
        b.task_memory.checkpoint('plan', 'ESP32QA practice', 'Read screen', 'wda')
        assert (await b.execute('phone_command', {'goal': 'Select Search then enter ESP32QA 中'}))['status'] == 'started'
        await b.driver_executor.task
        assert (await b.execute('phone_status', {}))['status'] == 'model_reported_done'
        assert any(t['status'] == 'model_reported_done' for t in b.task_memory.recall('wda')['tasks'])
        assert all(c.args[2] is None for c in model.chat.await_args_list)
        assert 'Search' in model.chat.await_args_list[0].args[1]
        assert 'ESP32QA practice' in model.chat.await_args_list[0].args[1]
        assert 'ESP32QA 中' not in (tmp_path / 'MEMORY.md').read_text()
        assert not any(p.endswith('/screenshot') for p, _ in runner.calls)
        assert sum(p.endswith('/wda/keys') for p, _ in runner.calls) == 1
        await d.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('label', ['Missing', 'Search'])
def test_generic_never_guesses_or_repeats(label):
    async def scenario():
        model = type('Model', (), {'chat': AsyncMock(return_value=json.dumps({'action': 'tap', 'label': label}))})()
        d, runner = adapter(model=model, max_steps=3)
        try:
            result = await d.command('Select the search control')
            assert result['status'] in ('not_found', 'uncertain')
            assert sum(p.endswith('/click') for p, _ in runner.calls) == (0 if label == 'Missing' else 1)
        finally: await d.aclose()
    asyncio.run(scenario())


def test_only_empty_tree_allows_screenshot_fallback():
    async def scenario():
        model = type('Model', (), {'chat': AsyncMock(return_value='{"action":"tap","label":"Missing"}')})()
        d, runner = adapter(model=model); runner.empty = True
        try:
            assert (await d.command('Select the visible search control'))['status'] == 'not_found'
            assert model.chat.await_args.args[2].startswith(b'\xff\xd8')
            assert Image.open(io.BytesIO(model.chat.await_args.args[2])).format == 'JPEG'
            assert sum(p.endswith('/screenshot') for p, _ in runner.calls) == 1
            assert not any(p.endswith('/click') for p, _ in runner.calls)
        finally: await d.aclose()
    asyncio.run(scenario())


def test_stop_during_model_call_prevents_input():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def choose(*a, **k):
            entered.set(); await release.wait(); return '{"action":"tap","label":"Search"}'
        d, runner = adapter(model=type('Model', (), {'chat': AsyncMock(side_effect=choose)})())
        b = VoiceBridge(lambda: {}, AsyncMock(), AsyncMock(), driver=d)
        await b.execute('phone_command', {'goal': 'Select the visible search control'})
        await entered.wait()
        assert (await b.execute('stop_phone', {}))['running']
        release.set(); await b.driver_executor.task
        assert b.driver_executor.snapshot()['status'] == 'stopped'
        assert not any(p.endswith('/click') for p, _ in runner.calls)
        await d.aclose()
    asyncio.run(scenario())


def test_wda_only_app_memory(tmp_path):
    async def scenario():
        d, _ = adapter()
        b = VoiceBridge(lambda: {}, AsyncMock(), AsyncMock(), driver=d, memory=CompanionMemory(tmp_path))
        await b.execute('phone_command', {'goal': 'Search app_store for ESP32QA private query'})
        await b.driver_executor.task
        recalled = b.task_memory.recall('wda')
        assert recalled['facts'][0]['app'] == recalled['tasks'][0]['app'] == 'app_store'
        assert 'private query' not in (tmp_path / 'MEMORY.md').read_text()
        await d.aclose()
    asyncio.run(scenario())


def test_explicit_lan_url_skips_forwarder_binding(monkeypatch):
    async def scenario():
        monkeypatch.setenv('WDA_URL', 'http://192.0.2.10:8100')
        monkeypatch.setattr(wda_driver, 'discover_device', lambda: wda_driver.validate_device(MINI, inventory()))
        monkeypatch.setattr(wda_driver, 'validate_binding', lambda *a: pytest.fail('LAN must not require iproxy'))
        monkeypatch.delenv('WDA_BINDING_FILE', raising=False)
        c = await wda_driver.WDAClient.from_env()
        assert str(c.http.base_url) == 'http://192.0.2.10:8100'
        await c.http.aclose()
    asyncio.run(scenario())


def load_tool(name, path):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[2] / path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_rednote_excluded_before_dispatch(tmp_path, monkeypatch, capsys):
    bench = load_tool('wire_bench', 'legacy/tools/wda/benchmark.py')
    monkeypatch.delenv('ENABLE_REDNOTE_BENCHMARK', raising=False)
    operation = AsyncMock(); monkeypatch.setattr(bench, 'run_case', operation)
    asyncio.run(bench.run_batch(tmp_path / 'batch', ['xiaohongshu'], 'offline'))
    operation.assert_not_called()
    assert 'security restriction' in capsys.readouterr().out
    assert 'security restriction' in (tmp_path / 'batch/README.zh-CN.md').read_text()


def test_rednote_env_opt_in(tmp_path, monkeypatch):
    bench = load_tool('wire_bench_enabled', 'legacy/tools/wda/benchmark.py')
    monkeypatch.setenv('ENABLE_REDNOTE_BENCHMARK', '1')
    operation = AsyncMock(); monkeypatch.setattr(bench, 'run_case', operation)
    asyncio.run(bench.run_batch(tmp_path / 'batch', ['xiaohongshu'], 'offline'))
    operation.assert_awaited_once()


def test_basic_benchmark_excludes_before_http(tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / 'tools'))
    bench = load_tool('wire_basic_bench', 'tools/basic_ops_benchmark.py')
    monkeypatch.delenv('ENABLE_REDNOTE_BENCHMARK', raising=False)
    class Forbidden:
        def request(self, *a, **k): pytest.fail('Disabled app must not contact server')
    result = bench.run_case(Forbidden(), bench.CASES[2], tmp_path, tmp_path, expected_keyboard=False)
    assert result['status'] == 'excluded'
    assert 'security restriction' in capsys.readouterr().out


@pytest.mark.parametrize('paired,developer,ready,next_step', [
    (True, True, True, 'approve Enable UI Automation with Touch ID on the iPad'),
    (False, True, True, 'Pair the selected iPad with Xcode'),
    (True, False, True, 'Enable Developer Mode'),
    (True, True, False, 'Start the installed HandsyRunner'),
])
def test_doctor_read_only(paired, developer, ready, next_step):
    doctor = load_tool('wire_doctor', 'legacy/tools/handsy-runner/doctor.py')
    data = inventory(); row = data['result']['devices'][0]
    row['connectionProperties']['pairingState'] = 'paired' if paired else 'unpaired'
    row['deviceProperties']['developerModeStatus'] = 'enabled' if developer else 'disabled'
    calls = []
    def fake(req):
        calls.append((req.method, req.url.path)); return httpx.Response(200, json={'value': {'ready': ready}})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
            result = await doctor.check(MINI, 'http://192.0.2.10:8100', inventory=data, http=http)
        assert result['pairing'] == ('paired' if paired else 'unpaired')
        assert result['developer_mode'] == ('enabled' if developer else 'disabled')
        assert result['runner_reachable'] and result['runner_ready'] is ready
        assert next_step in result['next_step']
        assert result['ui_automation'] == 'not_proven_by_status'
        assert calls == [('GET', '/status')]
    asyncio.run(scenario())


def test_generic_uncertain_completion_blocks_future_mutation():
    async def scenario():
        model = type('Model', (), {'chat': AsyncMock(return_value='{"action":"tap","label":"Search"}')})()
        d, runner = adapter(model=model, max_steps=2)
        try:
            assert (await d.command('Select the search control'))['never_retry_typing']
            await d.home()
            assert not any(p == '/wda/homescreen' for p, _ in runner.calls)
        finally: await d.aclose()
    asyncio.run(scenario())


def test_empty_tree_cannot_be_model_reported_done():
    async def scenario():
        model = type('Model', (), {'chat': AsyncMock(return_value='{"action":"done"}')})()
        d, runner = adapter(model=model); runner.empty = True
        try:
            assert (await d.command('Check the current result'))['status'] == 'not_ready'
        finally: await d.aclose()
    asyncio.run(scenario())


def test_unlabelled_focused_textview_can_accept_dictation():
    class Unlabelled(Runner):
        def __call__(self, req):
            data = super().__call__(req).json()
            if req.url.path.endswith('/source'):
                data['value']['children'][0].update(type='XCUIElementTypeTextView', label='', name='')
            return httpx.Response(200, json=data)
    async def scenario():
        d, runner = adapter(Unlabelled())
        try:
            assert (await d.type_text('ESP32QA exact'))['status'] == 'verified'
            assert sum(p.endswith('/wda/keys') for p, _ in runner.calls) == 1
        finally: await d.aclose()
    asyncio.run(scenario())


def test_doctor_unreachable_runner_has_concrete_next_step():
    doctor = load_tool('wire_doctor_unreachable', 'legacy/tools/handsy-runner/doctor.py')
    def unreachable(req): raise httpx.ConnectError('Offline', request=req)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as http:
            result = await doctor.check(MINI, 'http://192.0.2.10:8100', inventory=inventory(), http=http)
        assert not result['runner_reachable'] and not result['runner_ready']
        assert 'Start the installed HandsyRunner' in result['next_step']
    asyncio.run(scenario())


@pytest.mark.parametrize('url', ['http://8.8.8.8:8100', 'http://0.0.0.0:8100',
    'http://192.0.2.10:8100/path', 'http://192.0.2.10:8100?key=x',
    'http://user:secret@192.0.2.10:8100', 'https://192.0.2.10:8100'])
def test_invalid_runner_url_never_enumerates_devices(url, monkeypatch):
    monkeypatch.setenv('WDA_URL', url)
    monkeypatch.setattr(wda_driver, 'discover_device', lambda: pytest.fail('Reject URL before inventory'))
    with pytest.raises(wda_driver.WDAError): asyncio.run(wda_driver.WDAClient.from_env())


def test_default_client_still_requires_binding(monkeypatch):
    monkeypatch.delenv('WDA_URL', raising=False)
    monkeypatch.delenv('WDA_BINDING_FILE', raising=False)
    monkeypatch.setattr(wda_driver, 'discover_device', lambda: wda_driver.validate_device(MINI, inventory()))
    with pytest.raises(wda_driver.WDAError, match='WDA_BINDING_FILE'):
        asyncio.run(wda_driver.WDAClient.from_env())


def test_direct_rednote_benchmark_case_is_excluded(tmp_path, monkeypatch):
    bench = load_tool('wire_bench_direct', 'legacy/tools/wda/benchmark.py')
    monkeypatch.delenv('ENABLE_REDNOTE_BENCHMARK', raising=False)
    factory = AsyncMock(side_effect=AssertionError('Must not contact runner'))
    result = asyncio.run(bench.run_case(tmp_path, 'xiaohongshu', 'unused', factory=factory))
    assert result['status'] == 'excluded'
    assert not (tmp_path / 'xiaohongshu').exists()
    factory.assert_not_called()


def test_secure_tree_never_turns_into_screenshot_fallback():
    async def scenario():
        model = type('Model', (), {'chat': AsyncMock(return_value='{"action":"done"}')})()
        d, runner = adapter(model=model); runner.secure = True
        try:
            assert (await d.command('Inspect the current screen'))['status'] == 'not_ready'
            model.chat.assert_not_called()
            assert not any(p.endswith('/screenshot') for p, _ in runner.calls)
        finally: await d.aclose()
    asyncio.run(scenario())


def test_empty_tree_can_resolve_label_via_runner_without_coordinate_guess():
    class EmptySource(Runner):
        def __call__(self, req):
            data = super().__call__(req).json()
            if req.url.path.endswith('/source'):
                data['value'] = {'type': 'XCUIElementTypeApplication', 'children': []}
            return httpx.Response(200, json=data)
    async def scenario():
        model = type('Model', (), {'chat': AsyncMock(side_effect=[
            '{"action":"tap","label":"Search"}', '{"action":"blocked"}'])})()
        d, runner = adapter(EmptySource(), model=model)
        try:
            await d.command('Select the visible Search control')
            assert sum(p.endswith('/click') for p, _ in runner.calls) == 1
        finally: await d.aclose()
    asyncio.run(scenario())


def test_typing_requires_known_foreground_bundle():
    class UnknownForeground(Runner):
        def __call__(self, req):
            data = super().__call__(req).json()
            if req.url.path.endswith('/wda/activeAppInfo'): data['value'] = {}
            return httpx.Response(200, json=data)
    async def scenario():
        d, runner = adapter(UnknownForeground())
        try:
            assert (await d.type_text('ESP32QA'))['status'] == 'not_ready'
            assert not any(p.endswith('/wda/keys') for p, _ in runner.calls)
        finally: await d.aclose()
    asyncio.run(scenario())
