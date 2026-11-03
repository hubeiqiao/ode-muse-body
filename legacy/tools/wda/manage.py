#!/usr/bin/env python3
"""Offline source/build preparation and explicitly invoked mini-only live run.

No auto-download, provisioning updates, account changes or default device.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'server'))
from hidagent.wda_driver import (BASE_URL, DEVELOPER_DIR, DENIED_UDIDS, WDAClient,
                                 WDAError, discover_device)

UPSTREAM = 'https://github.com/appium/WebDriverAgent'
# Signing identities must be supplied explicitly for optional native experiments.
def signing_team():
    team = os.environ.get('APPLE_DEVELOPMENT_TEAM', '')
    if not re.fullmatch(r'[A-Z0-9]{10}', team):
        raise WDAError('Set APPLE_DEVELOPMENT_TEAM to your own ten-character team ID')
    return team
BUNDLE = 'com.example.handsy.wda.WebDriverAgentRunner'


def xcode_env():
    return {**os.environ, 'DEVELOPER_DIR': DEVELOPER_DIR}


def local_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise WDAError('WDA artifact path escapes this worktree')
    return path


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def source_hashes(source):
    return {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(source.rglob('*')) if path.is_file()}


def vendor(root, source, commit):
    """Copy a pinned clean local Appium checkout, preserving its BSD LICENSE."""
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise WDAError('Vendor requires an explicit full upstream commit, not a branch')
    source = Path(source).resolve()
    target = local_path(root, 'work/WebDriverAgent')
    pin = local_path(root, 'legacy/tools/wda/upstream.json')
    if target.exists() or pin.exists():
        raise WDAError('Vendor destination already exists; never overwrite local source')
    try:
        origin = subprocess.run(['git', '-C', str(source), 'remote', 'get-url', 'origin'],
                                capture_output=True, text=True, check=True).stdout.strip()
        if origin.rstrip('/').removesuffix('.git') not in (UPSTREAM, 'git@github.com:appium/WebDriverAgent'):
            raise WDAError('Vendor source origin must be Appium WebDriverAgent')
        head = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(['git', '-C', str(source), 'status', '--porcelain'],
                               capture_output=True, text=True, check=True).stdout.strip()
        if head != commit or dirty:
            raise WDAError('Vendor source must be clean at the exact requested commit')
        archive = subprocess.run(['git', '-C', str(source), 'archive', '--format=tar', commit],
                                 capture_output=True, check=True).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise WDAError('Vendor requires an existing local Appium checkout; offline mode does not clone') from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wda-vendor-', dir=target.parent) as directory:
        stage = Path(directory)
        tar = stage / 'source.tar'
        tar.write_bytes(archive)
        unpack = stage / 'unpack'
        unpack.mkdir()
        with tarfile.open(tar) as stream:
            for member in stream.getmembers():
                if (not (unpack / member.name).resolve().is_relative_to(unpack)
                        or member.issym() or member.islnk() or not (member.isfile() or member.isdir())):
                    raise WDAError('Unsafe vendor archive member')
            stream.extractall(unpack, filter='data')
        license_path = unpack / 'LICENSE'
        if not license_path.is_file() or not (unpack / 'WebDriverAgent.xcodeproj' / 'project.pbxproj').is_file():
            raise WDAError('Vendor source missing LICENSE or Xcode project')
        license_text = license_path.read_text()
        if 'Redistribution' not in license_text or 'Neither the name' not in license_text:
            raise WDAError('Expected BSD-3 upstream license was not found; review offline source')
        shutil.move(str(unpack), target)
    write_json(pin, {'upstream': UPSTREAM, 'commit': commit, 'archive_sha256': hashlib.sha256(archive).hexdigest(),
                     'license': 'BSD-3-Clause', 'license_sha256': hashlib.sha256(license_path_bytes(target)).hexdigest(),
                     'source_sha256': source_hashes(target)})
    print(f'Vendored {UPSTREAM} commit {commit}; LICENSE preserved at {target / "LICENSE"}')


def license_path_bytes(target):
    return (target / 'LICENSE').read_bytes()


def require_vendor(root):
    source = local_path(root, 'work/WebDriverAgent')
    pin_path = local_path(root, 'legacy/tools/wda/upstream.json')
    if not pin_path.is_file() or not (source / 'WebDriverAgent.xcodeproj' / 'project.pbxproj').is_file():
        raise WDAError('Vendor source unavailable. Supply a pinned local Appium checkout using vendor.sh first')
    pin = json.loads(pin_path.read_text())
    if (pin.get('upstream') != UPSTREAM or not re.fullmatch(r'[0-9a-f]{40}', pin.get('commit', ''))
            or pin.get('license_sha256') != hashlib.sha256(license_path_bytes(source)).hexdigest()):
        raise WDAError('Vendor pin or LICENSE mismatch')
    if pin.get('source_sha256') != source_hashes(source):
        raise WDAError('Vendor source differs from the pinned archive; refuse stale build')
    return pin


def build_command(root):
    return ['xcodebuild', 'build-for-testing', '-project', str(local_path(root, 'work/WebDriverAgent/WebDriverAgent.xcodeproj')),
            '-scheme', 'WebDriverAgentRunner', '-destination', 'generic/platform=iOS',
            '-derivedDataPath', str(local_path(root, 'work/wda-build/DerivedData')),
            '-disableAutomaticPackageResolution', '-skipPackageUpdates',
            f'DEVELOPMENT_TEAM={signing_team()}', f'PRODUCT_BUNDLE_IDENTIFIER={BUNDLE}',
            'CODE_SIGN_IDENTITY=' + os.environ.get('APPLE_CODE_SIGN_IDENTITY', 'Apple Development'), 'CODE_SIGN_STYLE=Automatic',
            'COMPILER_INDEX_STORE_ENABLE=NO']


def build(root):
    pin = require_vendor(root)
    receipt = local_path(root, 'work/wda-build/build.json')
    derived = local_path(root, 'work/wda-build/DerivedData')
    if receipt.exists() or derived.exists():
        raise WDAError('Build output already exists; preserve evidence and choose a fresh worktree')
    command = build_command(root)
    print('DEVELOPER_DIR=' + DEVELOPER_DIR, flush=True)
    print(json.dumps(command), flush=True)
    subprocess.run(command, env=xcode_env(), cwd=root, check=True)
    products = sorted(derived.glob('Build/Products/*.xctestrun'))
    if len(products) != 1:
        raise WDAError('Expected exactly one fresh xctestrun product; no device run selected')
    write_json(receipt, {'commit': pin['commit'], 'team': signing_team(), 'bundle_id': BUNDLE,
                         'xctestrun': str(products[0]), 'command': command})
    print('Build ready. Pair only the iPad mini, enable Developer Mode, then explicitly set WDA_DEVICE_UDID.')


def run_command(root, identity, xctestrun):
    if identity.model != 'iPad14,1' or identity.udid in DENIED_UDIDS:
        raise WDAError('Refusing a device other than the verified iPad mini')
    return ['xcodebuild', 'test-without-building', '-xctestrun', str(xctestrun),
            '-destination', 'id=' + identity.udid, '-destination-timeout', '30',
            '-resultBundlePath', str(local_path(root, 'work/wda-live/run.xcresult'))]


async def wait_ready(identity, forwarder, runner, *, seconds=45):
    deadline = time.monotonic() + seconds
    async with WDAClient(identity) as client:
        while time.monotonic() < deadline:
            if forwarder.poll() is not None or runner.poll() is not None:
                raise WDAError('Runner or forwarder exited before readiness')
            try:
                status = await client.status()
                if isinstance(status, dict) and status.get('ready') is True:
                    return
            except WDAError:
                pass  # Only status is polled. No device input is retried.
            await asyncio.sleep(.5)
    raise WDAError('WDA readiness timed out; no app input sent')


def run(root):
    pin = require_vendor(root)
    receipt_path = local_path(root, 'work/wda-build/build.json')
    if not receipt_path.exists():
        raise WDAError('Build first; no device enumeration was started')
    build_info = json.loads(receipt_path.read_text())
    if build_info.get('commit') != pin['commit'] or build_info.get('bundle_id') != BUNDLE:
        raise WDAError('Build identity does not match pinned source')
    product = Path(build_info['xctestrun']).resolve()
    if not product.is_relative_to(local_path(root, 'work/wda-build/DerivedData')) or not product.is_file():
        raise WDAError('Build product missing or outside the isolated worktree')
    identity = discover_device()  # Explicit UDID, paired mini product type and Developer Mode.
    iproxy = shutil.which('iproxy')
    if not iproxy:
        raise WDAError('iproxy missing. Install libimobiledevice/libusbmuxd separately; this script never runs brew')
    live = local_path(root, 'work/wda-live')
    if live.exists():
        raise WDAError('Live run directory exists; preserve it, never replay a run automatically')
    # Ensure another service cannot be mistaken for our forwarder.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 8100))
    live.mkdir(parents=True)
    runner = forwarder = None
    binding = live / 'binding.json'
    try:
        with (live / 'xcode.log').open('wb') as runner_log, (live / 'iproxy.log').open('wb') as proxy_log:
            runner = subprocess.Popen(run_command(root, identity, product), env=xcode_env(), cwd=root,
                                      stdout=runner_log, stderr=subprocess.STDOUT, start_new_session=True)
            forwarder = subprocess.Popen([iproxy, '-u', identity.udid, '-l', '-s', '127.0.0.1', '8100:8100'],
                                         stdout=proxy_log, stderr=subprocess.STDOUT, start_new_session=True)
            write_json(binding, {'udid': identity.udid, 'url': BASE_URL, 'pid': forwarder.pid})
            asyncio.run(wait_ready(identity, forwarder, runner))
            print(f'WDA ready on {BASE_URL}, mini {identity.udid}', flush=True)
            print(f'export DEVICE_DRIVER=wda WDA_DEVICE_UDID={identity.udid} WDA_BINDING_FILE={binding}', flush=True)
            print('Keep this process open. Ctrl-C stops only these two owned processes.', flush=True)
            while runner.poll() is None and forwarder.poll() is None:
                time.sleep(.5)
            raise WDAError('Runner or forwarder exited; inspect logs before another task')
    finally:
        binding.unlink(missing_ok=True)
        for process in (forwarder, runner):
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='operation', required=True)
    source = subs.add_parser('vendor')
    source.add_argument('--source', required=True, type=Path)
    source.add_argument('--commit', required=True)
    subs.add_parser('build')
    subs.add_parser('run')
    args = parser.parse_args()
    try:
        if args.operation == 'vendor':
            vendor(ROOT, args.source, args.commit)
        elif args.operation == 'build':
            build(ROOT)
        else:
            run(ROOT)
    except (WDAError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'WDA stopped: {exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
