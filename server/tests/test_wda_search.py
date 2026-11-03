"""All search fixtures are synthetic public screens, not app/device evidence."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from hidagent.wda_search import APPS, search
from hidagent.wda_driver import WDAError
from test_wda_driver import client
from test_app_tools import setup_tools


class SearchServer:
    def __init__(self, bundle, *, results=True, duplicate=False, focused=True):
        self.bundle, self.results, self.duplicate, self.focused = bundle, results, duplicate, focused
        self.query, self.submitted, self.calls = '', False, []

    def __call__(self, req):
        body = json.loads(req.content) if req.content else {}
        self.calls.append((req.url.path, body))
        if req.url.path == '/status':
            value = {'ready': True}
        elif req.url.path == '/session':
            value = {'sessionId': 'search-session'}
        elif req.url.path.endswith('/wda/activeAppInfo'):
            value = {'bundleId': self.bundle}
        elif req.url.path.endswith('/source'):
            nodes = [{'type': 'XCUIElementTypeSearchField', 'label': 'Search', 'value': self.query,
                      'focused': self.focused, 'enabled': True, 'visible': True}]
            if self.submitted and self.results:
                nodes += [{'type': 'XCUIElementTypeStaticText', 'label': f'Results for {self.query}'},
                          {'type': 'XCUIElementTypeCell', 'label': 'Public speaking practice tips'}]
            value = {'type': 'XCUIElementTypeApplication', 'children': nodes}
        elif req.url.path.endswith('/elements'):
            value = ([{'ELEMENT': 'field'}, {'ELEMENT': 'other'}] if self.duplicate else [{'ELEMENT': 'field'}])
        elif req.url.path.endswith('/wda/keys'):
            text = ''.join(body['value'])
            if text == '\n':
                self.submitted = True
            else:
                self.query += text
            value = None
        else:
            value = None
        return httpx.Response(200, json={'value': value})


@pytest.mark.parametrize('app', list(range(11)))
def test_all_eleven_searches_use_tree_without_models(app):
    async def run():
        key = list(APPS)[app]
        server = SearchServer(APPS[key]['bundle_id'])
        async with client(server) as wda:
            result = await search(wda, key, 'ielts public practice', wait_s=.01)
        assert result['verified'] and result['driver'] == 'wda' and result['model_calls'] == 0
        assert result['query'] == 'ielts public practice' and result['verification'] == 'accessibility_tree'
        assert [b for p, b in server.calls if p.endswith('/wda/keys')] == [
            {'value': list('ielts public practice')}, {'value': ['\n']}]
    asyncio.run(run())


@pytest.mark.parametrize('options', [{'results': False}, {'duplicate': True}, {'focused': False}])
def test_no_success_on_query_echo_ambiguous_field_or_unfocused_editor(options):
    async def run():
        server = SearchServer(APPS['youtube']['bundle_id'], **options)
        async with client(server) as wda:
            with pytest.raises(WDAError):
                await search(wda, 'youtube', 'unique public query', wait_s=.01)
        calls = [b for p, b in server.calls if p.endswith('/wda/keys')]
        assert len(calls) == (2 if options.get('results') is False else 0)
    asyncio.run(run())


def test_foreground_mismatch_never_types():
    async def run():
        server = SearchServer('com.apple.Preferences')
        async with client(server) as wda:
            with pytest.raises(WDAError):
                await search(wda, 'youtube', 'public query', wait_s=.01)
        assert not any(p.endswith('/wda/keys') for p, _ in server.calls)
    asyncio.run(run())


def test_wda_tool_choice_ignores_hid_readiness_and_deduplicates(monkeypatch):
    monkeypatch.setenv('DEVICE_DRIVER', 'wda')
    async def run():
        tools, bridge, writer, state = setup_tools(stable=False)
        state.update(device=False, screen=False, calibrated=False)
        server = SearchServer(APPS['youtube']['bundle_id'])
        fake = client(server)
        monkeypatch.setattr('hidagent.wda_driver.WDAClient.from_env', AsyncMock(return_value=fake))
        first = await tools.call('wda_request_1', 'youtube.search', {'query': 'public query'})
        second = await tools.call('wda_request_1', 'youtube.search', {'query': 'public query'})
        assert first['status'] == second['status'] == 'completed'
        assert first['result']['receipt']['driver'] == 'wda'
        assert tools.catalog()['device_driver'] == 'wda'
        assert len([p for p, _ in server.calls if p.endswith('/wda/keys')]) == 2
        bridge.execute.assert_not_called()
        writer.chat.assert_not_called()
    asyncio.run(run())


def test_default_tool_choice_still_requires_esp32(monkeypatch):
    monkeypatch.delenv('DEVICE_DRIVER', raising=False)
    async def run():
        tools, bridge, _, _ = setup_tools(stable=False)
        assert tools.catalog()['device_driver'] == 'esp32'
        result = await tools.call('default_request', 'youtube.search', {'query': 'public query'})
        assert result['status'] == 'not_ready'
        bridge.execute.assert_not_called()
    asyncio.run(run())


def test_cancel_wda_does_not_stop_or_call_hid(monkeypatch):
    monkeypatch.setenv('DEVICE_DRIVER', 'wda')
    async def run():
        tools, bridge, _, _ = setup_tools()
        entered = asyncio.Event()
        async def hang(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        fake = SimpleNamespace(uncertain=False, http=SimpleNamespace(aclose=AsyncMock()))
        monkeypatch.setattr('hidagent.wda_driver.WDAClient.from_env', AsyncMock(return_value=fake))
        monkeypatch.setattr('hidagent.wda_search.search', hang)
        await tools.call('cancel_wda_1', 'youtube.search', {'query': 'public query'}, wait_seconds=0)
        await asyncio.wait_for(entered.wait(), timeout=1)
        await tools.stop()
        await tools.calls['cancel_wda_1']['job']
        result = tools.status('cancel_wda_1')
        assert result['status'] == 'stopped' and result['result']['never_retry_typing']
        bridge.execute.assert_not_called()
        tools.stop_phone.assert_not_called()
        fake.http.aclose.assert_awaited_once()
    asyncio.run(run())


def test_ios_active_element_proves_focus_when_tree_omits_focused():
    class IOSServer(SearchServer):
        def __call__(self, req):
            if req.url.path.endswith('/element/active'):
                return httpx.Response(200, json={'value': {'ELEMENT': 'field'}})
            response = super().__call__(req)
            if req.url.path.endswith('/source'):
                body = response.json()
                for node in body['value']['children']:
                    node.pop('focused', None)
                return httpx.Response(200, json=body)
            return response
    async def run():
        server = IOSServer(APPS['youtube']['bundle_id'])
        async with client(server) as wda:
            result = await search(wda, 'youtube', 'public query', wait_s=.01)
        assert result['verified']
    asyncio.run(run())


@pytest.mark.parametrize('heading', ['Direct messages', 'Inbox', 'Sign in', 'Log in'])
def test_private_or_login_search_context_never_receives_query(heading):
    class PrivateServer(SearchServer):
        def __call__(self, req):
            response = super().__call__(req)
            if req.url.path.endswith('/source'):
                body = response.json()
                body['value']['children'].append({'type': 'XCUIElementTypeStaticText', 'label': heading})
                return httpx.Response(200, json=body)
            return response
    async def run():
        server = PrivateServer(APPS['instagram']['bundle_id'])
        async with client(server) as wda:
            with pytest.raises(WDAError, match='Private or login'):
                await search(wda, 'instagram', 'public query', wait_s=.01)
        assert not any(p.endswith('/wda/keys') for p, _ in server.calls)
    asyncio.run(run())
