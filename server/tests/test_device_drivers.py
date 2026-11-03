"""Offline driver contract tests. Every transport is an injected fake."""
import asyncio
from types import SimpleNamespace

import pytest

from hidagent import drivers


def run(coro):
    return asyncio.run(coro)


class FakeAndroid:
    def __init__(self):
        self.pkg = 'com.google.android.youtube'
        self.calls = []
        self.value = ''
        self.focused = False
        self.keyboard = False
        self.nodes = None

    def shell(self, command):
        if command == 'dumpsys window windows':
            return SimpleNamespace(output=f'mCurrentFocus=Window{{abc u0 {self.pkg}/.Main}}')
        return SimpleNamespace(output=f'mInputShown={str(self.keyboard).lower()}')

    def dump_hierarchy(self, **kwargs):
        nodes = self.nodes or [
            ('', 'Search', 'android.widget.ImageView', False, True, '[0,0][60,60]', self.pkg),
            (self.value, '', 'android.widget.EditText', self.focused, True, '[0,80][300,140]', self.pkg),
            (self.value if not self.focused else '', '', 'android.widget.TextView', False, False, '[0,160][300,210]', self.pkg),
        ]
        from xml.sax.saxutils import quoteattr
        return '<hierarchy>' + ''.join('<node ' + ' '.join(f'{k}={quoteattr(str(v).lower() if isinstance(v, bool) else v)}' for k, v in zip(
            ['text', 'content-desc', 'class', 'focused', 'clickable', 'bounds', 'package'], node)) + '/>' for node in nodes) + '</hierarchy>'

    def app_start(self, pkg):
        self.calls.append(('open', pkg)); self.pkg = pkg

    def click(self, x, y):
        self.calls.append(('click', x, y)); self.focused = True; self.keyboard = True

    def __call__(self, **selector):
        assert selector == {'focused': True, 'packageName': self.pkg}
        return SimpleNamespace(set_text=self.set_text)

    def set_text(self, text):
        self.calls.append(('type', text)); self.value = text

    def press(self, key):
        self.calls.append(('key', key))
        if key == 'enter':
            self.focused = False; self.keyboard = False


def android(fake=None):
    return drivers.AndroidDriver('offline-serial', connect=lambda serial: fake or FakeAndroid(), poll_interval=0)


def test_default_driver_preserves_legacy_goal_and_never_connects(monkeypatch):
    monkeypatch.delenv('DEVICE_DRIVER', raising=False)
    calls = []
    async def start(goal): calls.append(goal)
    d = drivers.select_driver(lambda: {'status': 'idle'}, start)
    assert isinstance(d, drivers.DeviceDriver)
    run(d.command('Open Notes and type "ESP32QA"'))
    assert calls == ['Open Notes and type "ESP32QA"']


def test_protocol_esp32_semantic_methods_delegate_once():
    calls = []
    async def start(goal): calls.append(goal); return {'status': 'started'}
    d = drivers.ESP32Driver(lambda: {'status': 'idle'}, start)
    async def scenario():
        await d.open_app('Notes'); await d.search_in_app('YouTube', 'test')
        await d.tap_text('Hello'); await d.type_text('a"b\n中'); await d.screen_text(); await d.home()
    run(scenario())
    assert len(calls) == 6 and calls[0] == 'Open Notes'
    assert 'a\\"b\\n中' in calls[3]
    assert run(d.status()) == {'status': 'idle'}


@pytest.mark.parametrize('name', ['typo', 'unregistered'])
def test_unknown_or_unregistered_driver_never_falls_back(name):
    d = drivers.select_driver(lambda: {}, None, name=name)
    result = run(d.open_app('YouTube'))
    assert result['status'] == 'not_ready' and result['terminal']


def test_wda_seam_accepts_injected_protocol_implementation():
    fake = android()
    assert drivers.select_driver(lambda: {}, None, name='wda', factories={'wda': lambda: fake}) is fake


def test_android_requires_explicit_serial_and_connects_lazily():
    calls = []
    d = drivers.AndroidDriver('', connect=lambda serial: calls.append(serial))
    assert not calls
    assert run(d.open_app('YouTube'))['status'] == 'not_ready'
    assert not calls
    fake = FakeAndroid()
    d = drivers.AndroidDriver('chosen', connect=lambda serial: calls.append(serial) or fake)
    assert calls == []
    assert run(d.open_app('YouTube'))['status'] == 'verified'
    assert calls == ['chosen']


def test_android_open_and_search_verify_tree_without_model_or_screenshot():
    fake = FakeAndroid(); d = android(fake)
    result = run(d.search_in_app('youtube', 'ESP32QA 中文'))
    assert result['status'] == 'verified' and result['verification_passed']
    assert fake.calls.count(('type', 'ESP32QA 中文')) == 1
    assert ('key', 'enter') in fake.calls
    assert result['recipe']['submit'] == 'enter'
    assert run(d.screen_text())['texts']


def test_android_never_taps_launcher_or_ambiguous_matches():
    fake = FakeAndroid(); d = android(fake)
    fake.nodes = [('Search', '', 'Button', False, True, '[0,0][10,10]', 'launcher')]
    assert run(d.tap_text('Search'))['status'] == 'not_found'
    fake.nodes = [('Search', '', 'Button', False, True, '[0,0][10,10]', fake.pkg)] * 2
    assert run(d.tap_text('Search'))['status'] == 'ambiguous'
    assert fake.calls == []


def test_android_typing_uncertainty_never_retries_or_submits():
    fake = FakeAndroid()
    def broken(text): fake.calls.append(('type', text))
    fake.set_text = broken
    result = run(android(fake).search_in_app('youtube', 'ESP32QA'))
    assert result['status'] == 'uncertain' and result['never_retry_typing']
    assert fake.calls.count(('type', 'ESP32QA')) == 1
    assert ('key', 'enter') not in fake.calls


def test_android_result_needs_two_nonfocused_observations():
    fake = FakeAndroid()
    def no_enter(key): fake.calls.append(('key', key))
    fake.press = no_enter
    result = run(android(fake).search_in_app('youtube', 'ESP32QA'))
    assert result['status'] == 'uncertain'
    assert result['verification_passed'] is False


def test_android_rejects_arbitrary_goal_and_preserves_literal_dictation():
    fake = FakeAndroid(); fake.focused = True
    d = android(fake)
    assert run(d.command('Publish something'))['status'] == 'unsupported'
    result = run(d.command('Type "ESP32QA 中 \\"quoted\\""'))
    assert result['status'] == 'verified'
    assert ('type', 'ESP32QA 中 "quoted"') in fake.calls


def test_wda_environment_loads_only_explicit_adapter_factory(monkeypatch):
    import sys
    fake = android(); fake.name = 'wda'
    monkeypatch.setenv('DEVICE_DRIVER', 'wda')
    monkeypatch.setitem(sys.modules, 'hidagent.wda_driver', SimpleNamespace(create_driver=lambda: fake))
    d = drivers.select_driver(lambda: {}, None)
    assert d is fake


def test_android_waits_for_delayed_foreground_without_relaunch_or_query_resend():
    fake = FakeAndroid()
    original = fake.shell
    polls = []
    def delayed(command):
        if command == 'dumpsys window windows':
            polls.append(command)
            if len(polls) <= 3:
                return SimpleNamespace(output='mCurrentFocus=Window{abc u0 launcher/.Home}')
        return original(command)
    fake.shell = delayed
    assert run(android(fake).open_app('youtube'))['status'] == 'verified'
    assert len(fake.calls) == 1
