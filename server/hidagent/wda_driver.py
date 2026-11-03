"""Bounded async WebDriverAgent client. No HID/model dependencies or retries.

The default factory requires a verified local iproxy binding. Explicit WDA_URL
also permits a private LAN runner. Tests use an in-process HTTP MockTransport.
"""
import asyncio
import base64
import binascii
import json
import ipaddress
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit
import xml.etree.ElementTree as ET

import httpx

LOG = logging.getLogger(__name__)
DEVELOPER_DIR = '/Applications/Xcode.app/Contents/Developer'
DENIED_UDIDS = {value.strip().upper() for value in os.environ.get('WDA_DENIED_UDIDS', '').split(',') if value.strip()}
BASE_URL = 'http://127.0.0.1:8100'


def runner_url(value):
    """Explicit HTTP runner on port 8100, no credentials, paths or DNS lookup."""
    try:
        parts = urlsplit(value)
        host = ipaddress.ip_address(parts.hostname or '')
        if (parts.scheme != 'http' or parts.port != 8100 or parts.username or parts.password
                or parts.path not in ('', '/') or parts.query or parts.fragment
                or host.version != 4 or not (host.is_private or host.is_loopback)
                or host.is_unspecified or host.is_multicast or host.is_link_local):
            raise ValueError('Invalid runner address')
    except (ValueError, TypeError) as exc:
        raise WDAError('WDA_URL must be an explicit private IPv4 HTTP runner on port 8100') from exc
    return value.rstrip('/')


class WDAError(RuntimeError):
    def __init__(self, message, *, delivery_uncertain=False):
        super().__init__(message)
        self.delivery_uncertain = delivery_uncertain


@dataclass(frozen=True)
class DeviceIdentity:
    udid: str
    model: str


def validate_device(udid, inventory):
    """Explicit UDID and hardware product type, never device name or first row."""
    if not isinstance(udid, str) or not re.fullmatch(r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}', udid):
        raise WDAError('Set WDA_DEVICE_UDID to the explicitly selected iPad mini UDID')
    if udid.upper() in DENIED_UDIDS:
        raise WDAError('Selected device is denied by WDA_DENIED_UDIDS')
    devices = inventory.get('result', {}).get('devices', [])
    matches = [d for d in devices if d.get('hardwareProperties', {}).get('udid', '').upper() == udid.upper()]
    if len(matches) != 1:
        raise WDAError('Selected iPad mini must appear exactly once in devicectl inventory')
    device = matches[0]
    hardware, props = device.get('hardwareProperties', {}), device.get('deviceProperties', {})
    if hardware.get('productType') != 'iPad14,1':
        raise WDAError('Only hardware productType iPad14,1 is permitted')
    if device.get('connectionProperties', {}).get('pairingState') != 'paired':
        raise WDAError('Pair the iPad mini with Xcode first')
    if device.get('connectionProperties', {}).get('tunnelState') != 'connected':
        raise WDAError('Selected iPad mini is not currently connected')
    if props.get('developerModeStatus') != 'enabled':
        raise WDAError('Enable Developer Mode on the iPad mini first')
    return DeviceIdentity(udid.upper(), 'iPad14,1')


def discover_device(udid=None):
    """Called only by explicitly executed live tools, never at module import."""
    selected = os.environ.get('WDA_DEVICE_UDID', '') if udid is None else udid
    # Reject absent/prohibited identifiers before even enumerating devices.
    if not re.fullmatch(r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}', selected) or selected.upper() in DENIED_UDIDS:
        raise WDAError('Explicit permitted WDA_DEVICE_UDID is required')
    with tempfile.TemporaryDirectory(prefix='wda-inventory-') as directory:
        path = Path(directory) / 'devices.json'
        try:
            subprocess.run(['xcrun', 'devicectl', 'list', 'devices', '--json-output', str(path)],
                           env={**os.environ, 'DEVELOPER_DIR': DEVELOPER_DIR},
                           check=True, capture_output=True, timeout=30)
            return validate_device(selected, json.loads(path.read_text()))
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            raise WDAError('Unable to verify the selected iPad mini inventory') from exc


def validate_binding(identity, path):
    """Do not trust an arbitrary server listening on localhost:8100."""
    try:
        binding = json.loads(Path(path).read_text())
        if binding['udid'] != identity.udid or binding['url'] != BASE_URL:
            raise ValueError('Binding target mismatch')
        # Exact managed iproxy process, with an explicit USB target, no auto-pick.
        args = subprocess.run(['ps', '-p', str(int(binding['pid'])), '-o', 'command='],
                              check=True, capture_output=True, text=True, timeout=3).stdout.strip().split()
        if len(args) != 7 or Path(args[0]).name != 'iproxy' or args[1:] != ['-u', identity.udid, '-l', '-s', '127.0.0.1', '8100:8100']:
            raise ValueError('Forwarder command mismatch')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        raise WDAError('No verified mini-only iproxy binding; run legacy/tools/wda/run.sh') from exc


def trim_tree(value, *, max_nodes=600, max_text=240):
    """Return bounded, parent-indexed nodes for both WDA JSON and XML sources."""
    if not 1 <= max_nodes <= 10000 or not 1 <= max_text <= 2000:
        raise ValueError('Invalid source bounds')
    if isinstance(value, str):
        if len(value) > 4_000_000 or '<!DOCTYPE' in value.upper() or '<!ENTITY' in value.upper():
            raise WDAError('Unsafe or oversized source XML')
        try:
            value = ET.fromstring(value)
        except ET.ParseError as exc:
            raise WDAError('Invalid source XML') from exc
    if not isinstance(value, (dict, ET.Element)):
        raise WDAError('Unexpected WDA source tree')
    nodes, stack, truncated = [], [(value, None)], False
    while stack and len(nodes) < max_nodes:
        item, parent = stack.pop()
        if isinstance(item, ET.Element):
            attrs, children = {'type': item.tag, **item.attrib}, list(item)
        elif isinstance(item, dict):
            attrs, children = item, item.get('children', [])
        else:
            raise WDAError('Invalid source node')
        row = {'parent': parent}
        for key in ('type', 'name', 'label', 'value', 'enabled', 'visible', 'focused'):
            if key in attrs:
                raw = attrs[key]
                row[key] = raw[:max_text] if isinstance(raw, str) else raw
                truncated |= isinstance(raw, str) and len(raw) > max_text
        if not isinstance(children, list):
            raise WDAError('Invalid source children')
        index = len(nodes)
        nodes.append(row)
        # A bounded stack also protects huge JSON sibling arrays.
        available = max_nodes - len(nodes)
        truncated |= len(children) > available
        stack.extend((child, index) for child in reversed(children[:available]))
    return {'nodes': nodes, 'truncated': bool(truncated or stack)}


class WDAClient:
    def __init__(self, identity, *, base_url=BASE_URL, timeout=8, transport=None, log=None):
        if identity.model != 'iPad14,1' or identity.udid.upper() in DENIED_UDIDS:
            raise WDAError('Only verified iPad mini is permitted')
        base_url = runner_url(base_url)
        if timeout <= 0:
            raise WDAError('WDA requires a positive timeout')
        self.identity, self.session_id, self.uncertain = identity, None, False
        self.before_input = None
        self.log = log or (lambda row: LOG.info('wda_action %s', json.dumps(row, sort_keys=True)))
        self.http = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout),
                                     transport=transport, trust_env=False, follow_redirects=False)

    @classmethod
    async def from_env(cls, *, log=None):
        explicit_url = os.environ.get('WDA_URL')
        url = runner_url(explicit_url) if explicit_url else BASE_URL
        identity = await asyncio.to_thread(discover_device)
        if explicit_url:
            return cls(identity, base_url=url, log=log)
        path = os.environ.get('WDA_BINDING_FILE', '')
        if not path:
            raise WDAError('WDA_BINDING_FILE is required')
        await asyncio.to_thread(validate_binding, identity, path)
        return cls(identity, log=log)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        # No DELETE /session or hidden device action during cleanup.
        await self.http.aclose()

    async def _request(self, method, path, payload=None, *, action, mutates=False):
        if mutates and self.uncertain:
            raise WDAError('Previous input is uncertain; inspect before a new task', delivery_uncertain=True)
        if mutates and self.before_input:
            self.before_input()
        started, outcome = time.perf_counter(), 'error'
        try:
            response = await self.http.request(method, path, json=payload)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or 'value' not in data:
                raise WDAError('Malformed WDA receipt')
            value = data['value']
            if (isinstance(value, dict) and value.get('error')) or data.get('status', 0) != 0:
                raise WDAError('WDA rejected the action')
            outcome = 'ok'
            return value
        except asyncio.CancelledError:
            if mutates:
                self.uncertain = True
            outcome = 'cancelled'
            raise
        except (httpx.HTTPError, ValueError, WDAError) as exc:
            # Even a server error after a POST may follow actual input. Never retry.
            if mutates:
                self.uncertain = True
            raise WDAError(f'WDA {action} failed; no automatic retry', delivery_uncertain=mutates) from exc
        finally:
            self.log({'action': action, 'elapsed_s': round(time.perf_counter() - started, 6),
                      'outcome': outcome, 'delivery_uncertain': self.uncertain})

    def _session(self, tail):
        if not self.session_id:
            raise WDAError('Start a WDA session first')
        return '/session/' + quote(self.session_id, safe='') + tail

    async def status(self):
        return await self._request('GET', '/status', action='status')

    async def start_session(self):
        if self.session_id:
            raise WDAError('Session already started')
        value = await self._request('POST', '/session', {'capabilities': {'alwaysMatch': {}}},
                                    action='session', mutates=True)
        if not isinstance(value, dict) or not isinstance(value.get('sessionId'), str) or not value['sessionId']:
            self.uncertain = True
            raise WDAError('WDA session receipt missing sessionId', delivery_uncertain=True)
        self.session_id = value['sessionId']

    async def source(self, *, format='xml'):
        if format not in ('xml', 'json'):
            raise ValueError('Source format must be xml or json')
        return trim_tree(await self._request('GET', self._session('/source?format=' + format), action='source'))

    async def active_element(self):
        value = await self._request('GET', self._session('/element/active'), action='active_element')
        element = value.get('element-6066-11e4-a52e-4f735466cecf', value.get('ELEMENT')) if isinstance(value, dict) else None
        if not isinstance(element, str) or not element:
            raise WDAError('Focused element is unavailable')
        return element

    async def find(self, *, label=None, predicate=None):
        if (label is None) == (predicate is None):
            raise ValueError('Supply exactly one label or predicate')
        using, value = ('accessibility id', label) if label is not None else ('predicate string', predicate)
        found = await self._request('POST', self._session('/elements'), {'using': using, 'value': value}, action='find')
        if not isinstance(found, list):
            raise WDAError('Malformed WDA element receipt')
        ids = [v.get('element-6066-11e4-a52e-4f735466cecf', v.get('ELEMENT')) for v in found if isinstance(v, dict)]
        if len(ids) != len(found) or any(not isinstance(i, str) or not i for i in ids):
            raise WDAError('Malformed WDA element ids')
        return ids

    async def tap(self, element):
        await self._request('POST', self._session('/element/' + quote(element, safe='') + '/click'), {},
                            action='tap', mutates=True)

    async def clear(self, element):
        await self._request('POST', self._session('/element/' + quote(element, safe='') + '/clear'), {},
                            action='clear', mutates=True)

    async def type_focused(self, text):
        await self._request('POST', self._session('/wda/keys'), {'value': list(text)}, action='type', mutates=True)

    async def home(self):
        await self._request('POST', '/wda/homescreen', {}, action='home', mutates=True)

    async def activate(self, bundle_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+', bundle_id):
            raise ValueError('Invalid bundle id')
        await self._request('POST', self._session('/wda/apps/activate'), {'bundleId': bundle_id},
                            action='activate', mutates=True)

    async def active_app(self):
        return await self._request('GET', self._session('/wda/activeAppInfo'), action='active_app')

    async def open_url(self, url):
        parts = urlsplit(url)
        if not parts.scheme or parts.username or parts.password or any(ord(c) < 32 for c in url):
            raise ValueError('Invalid deep link')
        await self._request('POST', self._session('/url'), {'url': url}, action='open_url', mutates=True)

    async def screenshot(self):
        value = await self._request('GET', self._session('/screenshot'), action='screenshot')
        try:
            return base64.b64decode(value, validate=True)
        except (binascii.Error, TypeError, ValueError) as exc:
            raise WDAError('Invalid screenshot receipt') from exc
