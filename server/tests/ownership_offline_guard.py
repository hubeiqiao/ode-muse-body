"""Temporary verification-only guard: no listeners or actual network connections."""
import socket
import pytest

SERVER_MODULES = {'test_mcp.py', 'test_mirror_health.py', 'test_screen_stream.py'}

def pytest_collection_modifyitems(items):
    for item in items:
        if item.path.name in SERVER_MODULES or item.nodeid == 'tests/test_device.py::test_round_trip_with_a_fake_board':
            item.add_marker(pytest.mark.skip(reason='Owner offline rule: server/socket or fake-board transport integration'))

def pytest_sessionstart(session):
    def denied(*args, **kwargs):
        raise RuntimeError('Offline verification forbids actual socket bind/connect')
    socket.socket.bind = denied
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
