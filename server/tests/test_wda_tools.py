"""Build/run planning tests never execute Xcode, USB, sockets or a server."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from hidagent.wda_driver import WDAError, validate_binding
from test_wda_driver import MINI, inventory
from hidagent.wda_driver import validate_device

PATH = Path(__file__).resolve().parents[2] / 'legacy' / 'tools' / 'wda' / 'manage.py'
spec = importlib.util.spec_from_file_location('wda_manage', PATH)
manage = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = manage
spec.loader.exec_module(manage)


def test_generic_build_plan_is_offline_and_device_independent(tmp_path, monkeypatch):
    monkeypatch.setenv('APPLE_DEVELOPMENT_TEAM', 'TESTTEAM01')
    command = manage.build_command(tmp_path)
    assert command[:2] == ['xcodebuild', 'build-for-testing']
    assert command[command.index('-destination') + 1] == 'generic/platform=iOS'
    assert 'DEVELOPMENT_TEAM=TESTTEAM01' in command
    assert 'PRODUCT_BUNDLE_IDENTIFIER=com.example.handsy.wda.WebDriverAgentRunner' in command
    assert '-allowProvisioningUpdates' not in command
    assert '-skipPackageUpdates' in command
    assert manage.xcode_env()['DEVELOPER_DIR'] == '/Applications/Xcode.app/Contents/Developer'


def test_run_plan_only_accepts_verified_mini(tmp_path):
    device = validate_device(MINI, inventory())
    command = manage.run_command(tmp_path, device, tmp_path / 'mini.xctestrun')
    assert command[:2] == ['xcodebuild', 'test-without-building']
    assert command[command.index('-destination') + 1] == f'id={MINI}'
    assert '00000000-0000000000000002' not in ' '.join(command)
    with pytest.raises(WDAError):
        manage.run_command(tmp_path, type(device)('00000000-0000000000000002', 'iPad17,1'), tmp_path / 'x')


def test_missing_vendor_stops_before_xcode_or_device_enumeration(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('No subprocess allowed when vendor source is absent')
    monkeypatch.setattr(manage.subprocess, 'run', forbidden)
    with pytest.raises(WDAError, match='Vendor'):
        manage.build(tmp_path)


def test_vendor_rejects_unpinned_revision_before_copy(tmp_path):
    with pytest.raises(WDAError):
        manage.vendor(tmp_path, tmp_path / 'source', 'main')
    assert not (tmp_path / 'work' / 'WebDriverAgent').exists()


def test_binding_requires_matching_udid_and_exact_usb_forwarder(tmp_path, monkeypatch):
    identity = validate_device(MINI, inventory())
    path = tmp_path / 'binding.json'
    path.write_text(json.dumps({'udid': MINI, 'url': 'http://127.0.0.1:8100', 'pid': 123}))
    result = type('Result', (), {'stdout': f'/opt/homebrew/bin/iproxy -u {MINI} -l -s 127.0.0.1 8100:8100'})()
    monkeypatch.setattr('hidagent.wda_driver.subprocess.run', lambda *a, **k: result)
    validate_binding(identity, path)
    result.stdout = '/opt/homebrew/bin/iproxy 8100:8100'
    with pytest.raises(WDAError):
        validate_binding(identity, path)


def test_vendor_preserves_license_pins_commit_and_detects_source_mutation(tmp_path, monkeypatch):
    import subprocess
    source = tmp_path / 'appium-source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    license_text = 'BSD-3-Clause fixture. Redistribution permitted. Neither the name may endorse.'
    (source / 'LICENSE').write_text(license_text)
    project = source / 'WebDriverAgent.xcodeproj'
    project.mkdir()
    (project / 'project.pbxproj').write_text('test project')
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Offline Test', '-c',
                    'user.email=offline@example.invalid', 'commit', '-qm', 'Local fixture'], check=True)
    commit = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'], check=True,
                            capture_output=True, text=True).stdout.strip()
    root = tmp_path / 'worktree'
    root.mkdir()
    real_run = subprocess.run
    def local_git(command, **kwargs):
        if command[3:6] == ['remote', 'get-url', 'origin']:
            return subprocess.CompletedProcess(command, 0, stdout=manage.UPSTREAM + '\n')
        return real_run(command, **kwargs)
    monkeypatch.setattr(subprocess, 'run', local_git)
    manage.vendor(root, source, commit)
    assert (root / 'work/WebDriverAgent/LICENSE').read_text() == license_text
    assert manage.require_vendor(root)['commit'] == commit
    (root / 'work/WebDriverAgent/WebDriverAgent.xcodeproj/project.pbxproj').write_text('changed')
    with pytest.raises(WDAError, match='source'):
        manage.require_vendor(root)
