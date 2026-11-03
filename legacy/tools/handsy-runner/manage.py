"""Owned HandsyRunner build/run entry points. No downloads or device auto-pick."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

DEVELOPER_DIR = '/Applications/Xcode.app/Contents/Developer'


def validate_udid(udid):
    if not re.fullmatch(r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}', udid):
        raise ValueError('Set WDA_DEVICE_UDID to the explicitly selected iPad mini UDID')


def validate_inventory(udid, inventory):
    validate_udid(udid)
    matches = [d for d in inventory.get('result', {}).get('devices', [])
               if d.get('hardwareProperties', {}).get('udid', '').upper() == udid.upper()]
    if len(matches) != 1 or matches[0].get('hardwareProperties', {}).get('productType') != 'iPad14,1':
        raise ValueError('Only exactly one explicitly selected iPad14,1 is permitted')


def base_command(root, udid):
    validate_udid(udid)
    return ['xcodebuild', '-project', str(root / 'legacy/tools/handsy-runner/HandsyRunner.xcodeproj'),
            '-scheme', 'HandsyRunner', '-destination', 'id=' + udid, '-destination-timeout', '30',
            '-derivedDataPath', str(root / 'work/handsy-runner-build'), '-parallel-testing-enabled', 'NO']


def build_command(root, udid):
    team = os.environ.get('APPLE_DEVELOPMENT_TEAM', '')
    if not re.fullmatch(r'[A-Z0-9]{10}', team):
        raise ValueError('Set APPLE_DEVELOPMENT_TEAM to your own ten-character team ID')
    return base_command(root, udid) + ['build-for-testing', 'DEVELOPMENT_TEAM=' + team,
                                     'CODE_SIGN_STYLE=Automatic', '-allowProvisioningUpdates',
                                     '-allowProvisioningDeviceRegistration']


def run_command(root, udid):
    return base_command(root, udid) + ['test-without-building', '-test-timeouts-enabled', 'NO',
                                     '-only-testing:HandsyRunnerUITests/HandsyRunnerTests/testServe']


def main():
    udid = os.environ.get('WDA_DEVICE_UDID', '')
    validate_udid(udid)  # Reject before any subprocess, including device enumeration.
    root = Path(__file__).resolve().parents[3]
    os.environ['DEVELOPER_DIR'] = DEVELOPER_DIR
    with tempfile.TemporaryDirectory(prefix='handsy-inventory-') as directory:
        path = Path(directory) / 'devices.json'
        subprocess.run(['xcrun', 'devicectl', 'list', 'devices', '--json-output', str(path)],
                       check=True, capture_output=True, timeout=30)
        validate_inventory(udid, json.loads(path.read_text()))
    mode = sys.argv[1]
    if mode == 'build':
        command = build_command(root, udid)
    elif mode == 'run':
        if not list((root / 'work/handsy-runner-build/Build/Products').glob('*.xctestrun')):
            raise ValueError('Run legacy/tools/handsy-runner/build.sh first')
        print('HandsyRunner LAN URL: http://<IPAD_MINI_WIFI_IP>:8100 (same trusted LAN).', flush=True)
        print('The existing Python client requires its verified localhost:8100 forwarding binding.', flush=True)
        command = run_command(root, udid)
    else:
        raise ValueError('Expected build or run')
    os.execvp(command[0], command)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        sys.exit(str(exc))
