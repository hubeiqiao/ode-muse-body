import asyncio

import pytest
from unittest.mock import AsyncMock

from hidagent import clipboard
from hidagent import phone as phone_module
from hidagent.phone import Phone


class Device:
    links = 0

    def __init__(self, mac):
        self.sent, self.mac, self.pasted = [], mac, []

    async def send(self, line):
        self.sent.append(line)
        self.pasted.append(self.mac.get("text"))  # what the phone would receive at the moment of the keystroke
        return "ok"


@pytest.fixture(autouse=True)
def keyboard_readiness(monkeypatch):
    # These tests isolate encoding/pacing; passive readiness is covered at the API seam.
    monkeypatch.setattr(Phone, 'ensure_keyboard_live', AsyncMock())


@pytest.fixture
def mac(monkeypatch):
    """An in-memory Mac clipboard; no waiting for Universal Clipboard."""
    board = {"text": "the user's own copy"}
    monkeypatch.setattr(clipboard, "supported", lambda: True)
    monkeypatch.setattr(clipboard, "write", lambda text: board.update(text=text))
    monkeypatch.setattr(phone_module, "HANDOFF_WAIT", 0)
    return board


def test_ascii_is_typed_key_by_key(tmp_path, mac):
    device = Device(mac)
    asyncio.run(Phone(device, tmp_path / "c.json").type("Calendar"))
    assert device.sent == ["type Calendar"]
    assert mac["text"] == "the user's own copy"


def test_chinese_is_pasted_and_stays_on_the_clipboard(tmp_path, mac):
    device = Device(mac)
    asyncio.run(Phone(device, tmp_path / "c.json").type("打开设置 ⚙️"))
    assert device.sent == ["key cmd+v"] and device.pasted == ["打开设置 ⚙️"]
    assert mac["text"] == "打开设置 ⚙️"  # never swapped back while an "Allow Paste" prompt may be open


def test_non_ascii_needs_a_mac(tmp_path, mac, monkeypatch):
    monkeypatch.setattr(clipboard, "supported", lambda: False)
    device = Device(mac)
    with pytest.raises(RuntimeError, match="Mac"):
        asyncio.run(Phone(device, tmp_path / "c.json").type("你好"))
    assert device.sent == []


@pytest.mark.parametrize('combo,expected', [('Tab', 'tab'), ('Command+Tab', 'cmd+tab'),
                                             ('Return', 'enter'), ('Shift+A', 'shift+A')])
def test_named_keys_are_case_insensitive_without_losing_letter_case(tmp_path, mac, combo, expected):
    device = Device(mac)
    asyncio.run(Phone(device, tmp_path / 'c.json').key(combo))
    assert device.sent == ['key ' + expected]


def test_fast_typing_tunes_once_per_link_and_retunes_after_reconnect(tmp_path, mac, monkeypatch):
    monkeypatch.setenv("HID_KEY_MS", "20")
    device = Device(mac)
    phone = Phone(device, tmp_path / "c.json")
    async def run():
        await phone.type("ABC")
        await phone.type("def")
        device.links += 1
        await phone.type("123")
    asyncio.run(run())
    assert device.sent == ["typing 20", "type ABC", "type def", "typing 20", "type 123"]


@pytest.mark.parametrize("value", ["0", "19", "101", "nan"])
def test_invalid_typing_delay_never_sends_text(tmp_path, mac, monkeypatch, value):
    monkeypatch.setenv("HID_KEY_MS", value)
    device = Device(mac)
    with pytest.raises(ValueError):
        asyncio.run(Phone(device, tmp_path / "c.json").type("test"))
    assert device.sent == []


def test_typing_preflight_failure_is_not_payload_delivery(tmp_path,monkeypatch):
    from hidagent.device import DeviceError
    monkeypatch.setenv('HID_KEY_MS','20')
    class Failing:
        links=1
        async def send(self,command):
            raise DeviceError('configuration ACK lost',delivery='unknown',command_name='typing')
    async def run():
        with pytest.raises(DeviceError) as caught:await Phone(Failing(),tmp_path/'c').type('hello')
        assert caught.value.typing_may_have_happened is False
    asyncio.run(run())
