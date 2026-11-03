"""Execute our Swift protocol with a fake backend, never bind a socket/device."""
import asyncio
import json
import os
import selectors
import sys
from pathlib import Path
import shutil
import subprocess

import httpx
import pytest

from hidagent.wda_driver import WDAClient, WDAError, DeviceIdentity

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'legacy/tools/handsy-runner'


@pytest.fixture(scope='module')
def swift_runner(tmp_path_factory):
    if sys.platform != 'darwin' or shutil.which('xcrun') is None:
        pytest.skip('Xcode Swift compiler required for owned runner protocol checks')
    binary = tmp_path_factory.mktemp('handsy-swift') / 'protocol-tests'
    subprocess.run(['xcrun', '--sdk', 'macosx', 'swiftc', '-module-cache-path', str(binary.parent / 'modules'),
                    str(RUNNER / 'HandsyRunnerUITests/Protocol.swift'),
                    str(RUNNER / 'Tests/main.swift'), '-o', str(binary)],
                   check=True, capture_output=True, text=True,
                   env={**os.environ, 'DEVELOPER_DIR': '/Applications/Xcode.app/Contents/Developer'}, timeout=60)
    return binary


def test_http_parser_and_protocol_contract(swift_runner):
    result = subprocess.run([str(swift_runner), '--self-test'], check=True,
                            capture_output=True, text=True, timeout=10)
    assert result.stdout.strip() == 'HTTP and protocol checks passed'


def test_snapshot_automation_and_request_budget_without_devices(swift_runner):
    # Compile the real adapter with offline XCTest doubles. No HTTPServer,
    # XCTest framework, socket, device discovery, installation or test run.
    result = subprocess.run(['bash', str(RUNNER / 'Tests/run.sh')], check=True,
                            capture_output=True, text=True, timeout=60)
    assert result.stdout.splitlines() == [
        'HTTP and protocol checks passed',
        'Snapshot automation checks passed',
        'Snapshot find checks passed',
        'Request budget and response log checks passed',
    ]


def test_wda_client_against_owned_swift_router(swift_runner):
    async def run():
        with subprocess.Popen([str(swift_runner)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True) as fake:
            def handle(request):
                fake.stdin.write(json.dumps({'method': request.method,
                                             'target': request.url.raw_path.decode(),
                                             'body': json.loads(request.content) if request.content else {}}) + '\n')
                fake.stdin.flush()
                with selectors.DefaultSelector() as ready:
                    ready.register(fake.stdout, selectors.EVENT_READ)
                    assert ready.select(timeout=5), 'Swift fake did not return a receipt'
                response = json.loads(fake.stdout.readline())
                return httpx.Response(response['status'], json=response['body'])
            try:
                identity = DeviceIdentity('00000000-0000000000000001', 'iPad14,1')
                async with WDAClient(identity, transport=httpx.MockTransport(handle)) as driver:
                    assert (await driver.status())['ready']
                    await driver.start_session()
                    await driver.activate('com.example.editor')
                    assert (await driver.active_app())['bundleId'] == 'com.example.editor'
                    assert (await driver.source(format='json'))['nodes'][1]['label'] == 'Search'
                    assert (await driver.source(format='xml'))['nodes'][1]['label'] == 'Search'
                    assert await driver.find(label='Search') == ['field']
                    assert await driver.find(predicate='label == "Search"') == ['field']
                    assert await driver.active_element() == 'field'
                    await driver.tap('field')
                    await driver.clear('field')
                    await driver.type_focused('English 中文 👨‍👩‍👧')
                    assert await driver.screenshot() == b'\x89PNG\r\n\x1a\n'
                    await driver.open_url('https://example.org')
                    assert (await driver.active_app())['bundleId'] == 'com.apple.mobilesafari'
                    await driver.home()
                    assert (await driver.active_app())['bundleId'] == 'com.apple.springboard'
                    with pytest.raises(WDAError, match='no automatic retry'):
                        await driver.tap('stale')
            finally:
                fake.stdin.close()
                fake.wait(timeout=5)
    asyncio.run(run())


def test_scripts_reject_missing_udid_without_invoking_xcode():
    env = {**os.environ, 'WDA_DEVICE_UDID': '', 'PATH': '/usr/bin:/bin'}
    for name in ('build.sh', 'run.sh'):
        result = subprocess.run(['bash', str(RUNNER / name)], env=env,
                                capture_output=True, text=True, timeout=5)
        assert result.returncode != 0
        assert 'WDA_DEVICE_UDID' in result.stderr


def test_owned_build_and_run_commands_and_model_guard(monkeypatch):
    monkeypatch.setenv('APPLE_DEVELOPMENT_TEAM', 'TESTTEAM01')
    import runpy
    manager = runpy.run_path(str(RUNNER / 'manage.py'))
    udid = '00000000-0000000000000001'
    root = Path('/tmp/owned-worktree')
    build = manager['build_command'](root, udid)
    run = manager['run_command'](root, udid)
    assert 'build-for-testing' in build and 'test-without-building' in run
    assert build[build.index('-destination') + 1] == 'id=' + udid
    assert run[run.index('-destination') + 1] == 'id=' + udid
    assert 'DEVELOPMENT_TEAM=TESTTEAM01' in build
    assert 'CODE_SIGN_STYLE=Automatic' in build
    assert '-allowProvisioningUpdates' in build
    assert str(root / 'work/handsy-runner-build') in build
    assert 'WebDriverAgent' not in ' '.join(build + run)
    inventory = {'result': {'devices': [{'hardwareProperties': {'udid': udid, 'productType': 'iPad14,1'}}]}}
    manager['validate_inventory'](udid, inventory)
    inventory['result']['devices'][0]['hardwareProperties']['productType'] = 'iPad17,1'
    with pytest.raises(ValueError, match='iPad14,1'):
        manager['validate_inventory'](udid, inventory)
    with pytest.raises(ValueError):
        manager['validate_inventory']('', inventory)
    with pytest.raises(ValueError):
        manager['validate_inventory'](udid, {'result': {'devices': []}})


def test_live_entry_points_reject_wrong_model_before_xcode(monkeypatch):
    import runpy
    import sys
    manager = runpy.run_path(str(RUNNER / 'manage.py'))
    calls = []
    def fake_inventory(command, **kwargs):
        calls.append(command)
        assert command[:4] == ['xcrun', 'devicectl', 'list', 'devices']
        Path(command[-1]).write_text(json.dumps({'result': {'devices': [
            {'hardwareProperties': {'udid': '00000000-0000000000000001', 'productType': 'iPhone17,1'}}]}}))
    monkeypatch.setenv('WDA_DEVICE_UDID', '00000000-0000000000000001')
    monkeypatch.setattr(subprocess, 'run', fake_inventory)
    monkeypatch.setattr(os, 'execvp', lambda *a: pytest.fail('Wrong model must never reach xcodebuild'))
    for mode in ('build', 'run'):
        monkeypatch.setattr(sys, 'argv', ['manage.py', mode])
        with pytest.raises(ValueError, match='iPad14,1'):
            manager['main']()
    assert len(calls) == 2
