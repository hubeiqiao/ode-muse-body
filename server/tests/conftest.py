"""Shared test isolation: the owner-chosen AI name never leaks in from ~/.hidagent or the shell."""
import os
import tempfile
import socket
import pytest
from synthetic_assets import ASSETS

# Isolate all public tests from persistent operator state before app imports.
_DATA = tempfile.TemporaryDirectory(prefix="ipad-agent-test-data-")
os.environ["HIDAGENT_DATA"] = _DATA.name
os.environ["APP_CONTROLS_DIR"] = str(ASSETS / "app-controls")
os.environ.pop("SCENARIO_KNOWLEDGE_DIR", None)


@pytest.fixture(autouse=True)
def no_live_device_ports(monkeypatch):
    original = socket.socket.connect
    def guarded(sock, address):
        if isinstance(address, tuple) and address[1] in (8890, 8891, 7300, 8100):
            raise AssertionError("Offline tests must not contact live device services")
        return original(sock, address)
    monkeypatch.setattr(socket.socket, "connect", guarded)



@pytest.fixture(autouse=True)
def isolated_ai_name(tmp_path, monkeypatch):
    from hidagent import identity
    monkeypatch.setattr(identity, '_settings_path', tmp_path / 'ai-name-settings' / 'settings.json')
    for name in ('AI_NAME', 'COMPANION_NAME'):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HIDAGENT_DATA", str(tmp_path / "runtime"))
