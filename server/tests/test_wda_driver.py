"""Offline WDA protocol server via MockTransport, never bind a socket."""
import asyncio
import json

import httpx
import pytest

from hidagent.wda_driver import WDAClient, WDAError, validate_device, trim_tree

MINI = '00000000-0000000000000001'
PRO = '00000000-0000000000000002'

@pytest.fixture(autouse=True)
def synthetic_denied_device(monkeypatch):
    monkeypatch.setattr('hidagent.wda_driver.DENIED_UDIDS', {PRO})


def inventory(udid=MINI, model='iPad14,1'):
    return {'result': {'devices': [{'hardwareProperties': {'udid': udid, 'productType': model},
                                  'connectionProperties': {'pairingState': 'paired', 'tunnelState': 'connected'},
                                  'deviceProperties': {'name': 'iPad mini (2)', 'developerModeStatus': 'enabled'}}]}}


def client(handler, **kw):
    identity = validate_device(MINI, inventory())
    return WDAClient(identity, transport=httpx.MockTransport(handler), **kw)


def test_identity_requires_explicit_udid_and_correct_model():
    assert validate_device(MINI, inventory()).udid == MINI
    for udid, data in [('', inventory()), (PRO, inventory(PRO)),
                       (MINI, inventory(model='iPad17,1')), ('unknown', inventory()),
                       (MINI, {'result': {'devices': []}})]:
        with pytest.raises(WDAError):
            validate_device(udid, data)
    data = inventory()
    data['result']['devices'][0]['connectionProperties']['pairingState'] = 'unpaired'
    with pytest.raises(WDAError):
        validate_device(MINI, data)


def test_protocol_actions_tree_and_content_free_timing_log():
    async def run():
        calls, logs = [], []
        def handle(req):
            body = json.loads(req.content) if req.content else None
            calls.append((req.method, req.url.path, body))
            if req.url.path == '/status':
                return httpx.Response(200, json={'value': {'ready': True}})
            if req.url.path == '/session':
                return httpx.Response(200, json={'value': {'sessionId': 'fake-session'}})
            if req.url.path.endswith('/elements'):
                return httpx.Response(200, json={'value': [{'element-6066-11e4-a52e-4f735466cecf': 'field'}]})
            if req.url.path.endswith('/source'):
                return httpx.Response(200, json={'value': '<App><Field label="Search" focused="true"/></App>'})
            if req.url.path.endswith('/screenshot'):
                return httpx.Response(200, json={'value': 'aW1hZ2U='})
            return httpx.Response(200, json={'value': None})
        async with client(handle, log=logs.append) as wda:
            assert (await wda.status())['ready']
            await wda.start_session()
            assert await wda.find(label='Search') == ['field']
            assert await wda.find(predicate='type == "XCUIElementTypeSearchField"') == ['field']
            await wda.tap('field')
            await wda.type_focused('private test text')
            await wda.home()
            await wda.activate('com.google.ios.youtube')
            await wda.open_url('https://example.org/search?q=public')
            assert 'Search' in json.dumps(await wda.source())
            assert await wda.screenshot() == b'image'
        assert ('POST', '/session/fake-session/wda/keys', {'value': list('private test text')}) in calls
        assert ('POST', '/session/fake-session/url', {'url': 'https://example.org/search?q=public'}) in calls
        assert ('POST', '/session/fake-session/wda/apps/activate', {'bundleId': 'com.google.ios.youtube'}) in calls
        assert logs and all(row['elapsed_s'] >= 0 and row['outcome'] == 'ok' for row in logs)
        assert 'private test text' not in json.dumps(logs)
    asyncio.run(run())


def test_uncertain_typing_is_never_retried_or_followed_by_more_input():
    async def run():
        count = 0
        def handle(req):
            nonlocal count
            if req.url.path == '/session':
                return httpx.Response(200, json={'value': {'sessionId': 'fake-session'}})
            count += 1
            raise httpx.ReadTimeout('lost receipt', request=req)
        async with client(handle) as wda:
            await wda.start_session()
            with pytest.raises(WDAError) as caught:
                await wda.type_focused('once')
            assert caught.value.delivery_uncertain
            for action in (lambda: wda.type_focused('once'), lambda: wda.tap('submit'), wda.home):
                with pytest.raises(WDAError):
                    await action()
            assert count == 1
    asyncio.run(run())


def test_wda_protocol_error_and_missing_session_fail_closed():
    async def run():
        async with client(lambda req: httpx.Response(200, json={'value': {'error': 'invalid session id'}})) as wda:
            with pytest.raises(WDAError):
                await wda.status()
            with pytest.raises(WDAError):
                await wda.tap('field')
    asyncio.run(run())


def test_tree_trim_bounds_json_and_xml_and_rejects_unsafe_xml():
    tree = {'type': 'App', 'label': 'x' * 2000, 'children': [{'type': 'Text', 'label': 'item'}] * 1000}
    result = trim_tree(tree, max_nodes=8, max_text=50)
    assert result['truncated'] and len(result['nodes']) == 8
    assert len(result['nodes'][0]['label']) == 50
    assert trim_tree('<App><Text label="Results"/></App>')['nodes'][1]['label'] == 'Results'
    with pytest.raises(WDAError):
        trim_tree('<!DOCTYPE x [<!ENTITY x "private">]><App>&x;</App>')


@pytest.mark.parametrize('url', ['http://127.0.0.1:8890', 'http://127.0.0.1:8894',
                                  'https://api.example.com', 'http://user:pass@127.0.0.1:8100'])
def test_client_rejects_foreign_or_production_endpoints(url):
    with pytest.raises(WDAError):
        client(lambda req: httpx.Response(200), base_url=url)


def test_disconnected_paired_mini_is_not_ready():
    data = inventory()
    data['result']['devices'][0]['connectionProperties']['tunnelState'] = 'unavailable'
    with pytest.raises(WDAError):
        validate_device(MINI, data)


def test_bad_udid_is_rejected_before_enumeration(monkeypatch):
    from hidagent.wda_driver import discover_device
    def forbidden(*args, **kw):
        pytest.fail('Invalid UDID must not enumerate devices')
    monkeypatch.setattr('hidagent.wda_driver.subprocess.run', forbidden)
    with pytest.raises(WDAError):
        discover_device('any-device')


def test_active_element_can_prove_ios_focus_without_tvos_focused_attribute():
    async def run():
        def handle(req):
            value = {'sessionId': 's'} if req.url.path == '/session' else {'ELEMENT': 'field'}
            return httpx.Response(200, json={'value': value})
        async with client(handle) as wda:
            await wda.start_session()
            assert await wda.active_element() == 'field'
    asyncio.run(run())


def test_source_supports_explicit_xml_and_json_flags():
    async def run():
        formats = []
        def handle(req):
            if req.url.path == '/session':
                return httpx.Response(200, json={'value': {'sessionId': 's'}})
            formats.append(req.url.params['format'])
            return httpx.Response(200, json={'value': {'type': 'App', 'children': []}})
        async with client(handle) as wda:
            await wda.start_session()
            await wda.source(format='xml')
            await wda.source(format='json')
        assert formats == ['xml', 'json']
    asyncio.run(run())
