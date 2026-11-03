#!/usr/bin/env python3
"""Read-only iPad runner diagnosis. Never builds, launches, pairs or sends input."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import httpx

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'server'))
from hidagent.wda_driver import DEVELOPER_DIR, DENIED_UDIDS, WDAError, runner_url
import re


def read_inventory():
    with tempfile.TemporaryDirectory(prefix='handsy-doctor-') as directory:
        path = Path(directory) / 'devices.json'
        subprocess.run(['xcrun', 'devicectl', 'list', 'devices', '--json-output', str(path)],
                       env={**os.environ, 'DEVELOPER_DIR': DEVELOPER_DIR},
                       capture_output=True, check=True, timeout=30)
        return json.loads(path.read_text())


async def check(udid, url, *, inventory=None, http=None):
    url = runner_url(url)
    if not re.fullmatch(r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}', udid or '') or udid.upper() in DENIED_UDIDS:
        raise WDAError('Set WDA_DEVICE_UDID to the explicitly selected iPad mini UDID')
    inventory = await asyncio.to_thread(read_inventory) if inventory is None else inventory
    matches = [d for d in inventory.get('result', {}).get('devices', [])
               if d.get('hardwareProperties', {}).get('udid', '').upper() == udid.upper()]
    result = dict(pairing='unknown', developer_mode='unknown', runner_reachable=False,
                  runner_ready=False, ui_automation='not_proven_by_status')
    if len(matches) != 1:
        return {**result, 'next_step': 'Connect the explicitly selected iPad mini and pair it with Xcode'}
    device = matches[0]
    if device.get('hardwareProperties', {}).get('productType') != 'iPad14,1':
        raise WDAError('Only the explicitly selected iPad14,1 is permitted')
    result['pairing'] = device.get('connectionProperties', {}).get('pairingState', 'unknown')
    result['developer_mode'] = device.get('deviceProperties', {}).get('developerModeStatus', 'unknown')
    owned = http is None
    http = http or httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False)
    try:
        response = await http.get(url + '/status')
        response.raise_for_status()
        data = response.json()
        result['runner_reachable'] = True
        result['runner_ready'] = (isinstance(data, dict) and data.get('status', 0) == 0
                                  and isinstance(data.get('value'), dict)
                                  and data['value'].get('ready') is True and not data['value'].get('error'))
    except (httpx.HTTPError, ValueError):
        pass
    finally:
        if owned:
            await http.aclose()
    if result['pairing'] != 'paired':
        step = 'Pair the selected iPad with Xcode and approve Trust on the iPad'
    elif result['developer_mode'] != 'enabled':
        step = 'Enable Developer Mode in Settings > Privacy & Security, restart, and confirm on the iPad'
    elif not result['runner_ready']:
        step = 'Start the installed HandsyRunner with legacy/tools/handsy-runner/run.sh; check WDA_URL and iPad Local Network permission'
    else:
        step = ('If prompted, approve Enable UI Automation with Touch ID on the iPad. '
                '/status does not prove UI Automation approval. Then open the isolated voice console and start voice.')
    return {**result, 'next_step': step}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--udid', default=os.environ.get('WDA_DEVICE_UDID', ''))
    parser.add_argument('--url', default=os.environ.get('WDA_URL', 'http://127.0.0.1:8100'))
    args = parser.parse_args()
    try:
        result = asyncio.run(check(args.udid, args.url))
    except (WDAError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'not_ready', 'next_step': str(exc)}, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['pairing'] == 'paired' and result['developer_mode'] == 'enabled' and result['runner_ready'] else 2


if __name__ == '__main__':
    sys.exit(main())
